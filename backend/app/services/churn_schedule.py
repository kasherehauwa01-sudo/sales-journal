"""Durable permanent scheduling. No scheduler is started by the web application.

Operator approval is separate from the existing analytical admission policy.
Only the host CLI may ingest receiver receipts; HTTP never acknowledges delivery.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
import re

from pydantic import BaseModel, ConfigDict, Field, StrictInt
from sqlalchemy import select
from app.models import (ChurnSchedule, ChurnJob, ChurnReceipt,
                        AnalyticsAutomationDecision, AnalyticsAutomationEvent)
from app.services.analytics_automation import digest
from app.services.churn_pilot import ALGORITHM_POLICY

SCHEDULE_LOCK = 734190225
POLICY = 'churn-permanent/1'
MOSCOW = ZoneInfo('Europe/Moscow')
TERMINAL = {'SUCCEEDED', 'PARTIAL', 'DIAGNOSED', 'EXPIRED'}


class ScheduleSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    time: str = Field(pattern=r'^(?:[01]\d|2[0-3]):[0-5]\d$')
    daily_limit: StrictInt = Field(ge=1, le=100)
    cooldown_days: StrictInt = Field(ge=1, le=3650)
    algorithm_policy: str
    receiver_policy_revision: StrictInt = Field(ge=1)
    producer_policy_revision: StrictInt = Field(ge=1)


def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def next_run(config, now):
    local = now.astimezone(MOSCOW)
    hour, minute = map(int, config['time'].split(':'))
    candidate = local.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if candidate <= local:
        candidate += timedelta(days=1)
    return candidate


async def control(db):
    row = await db.get(ChurnSchedule, 1, with_for_update=True)
    if row is None:
        row = ChurnSchedule(id=1, revision=0, enabled=False, approved=False, settings_json={})
        db.add(row)
        await db.flush()
    return row


async def change(db, action, payload, *, revision, actor):
    if not isinstance(actor, str) or not re.fullmatch(r'[a-zA-Z0-9_.@-]{1,100}', actor):
        raise ValueError('AUDITED_ACTOR_REQUIRED')
    row = await control(db)
    if type(revision) is not int or revision != row.revision:
        raise ValueError('POLICY_REVISION_CONFLICT')
    if action == 'draft':
        config = ScheduleSettings.model_validate(payload).model_dump()
        if config['algorithm_policy'] != ALGORITHM_POLICY:
            raise ValueError('ALGORITHM_POLICY_MISMATCH')
        row.settings_json = config
        row.approved = row.enabled = False
    elif action == 'approve':
        ScheduleSettings.model_validate(row.settings_json)
        if row.settings_json['algorithm_policy'] != ALGORITHM_POLICY:
            raise ValueError('ALGORITHM_POLICY_MISMATCH')
        if payload != {'confirm_business_approval': True} or type(payload.get('confirm_business_approval')) is not bool:
            raise ValueError('EXPLICIT_APPROVAL_REQUIRED')
        row.approved = True
        row.enabled = False  # approval never starts transmission
    elif action == 'enable':
        if not row.approved:
            raise ValueError('POLICY_NOT_APPROVED')
        row.enabled = True
    elif action == 'disable':
        row.enabled = False
    else:
        raise ValueError('INVALID_CONTROL_ACTION')
    # Enable/disable is audited but does not change the approved policy identity.
    if action in {'draft', 'approve'}:
        row.revision += 1
    db.add(AnalyticsAutomationDecision(id=str(uuid4()), source_run_id='PERMANENT_POLICY',
        created_at=datetime.now(timezone.utc), data=dict(action=action, actor=actor,
            policy_revision=row.revision, settings=row.settings_json)))
    await db.flush()
    return dict(revision=row.revision, approved=row.approved, enabled=row.enabled, settings=row.settings_json)


async def enqueue(db, *, now, diagnostic=False):
    row = await control(db)
    if not diagnostic and (not row.approved or not row.enabled):
        return None
    config = row.settings_json
    if not diagnostic:
        ScheduleSettings.model_validate(config)
        if config['algorithm_policy'] != ALGORITHM_POLICY:
            raise ValueError('ALGORITHM_POLICY_MISMATCH')
        local = now.astimezone(MOSCOW)
        if local.strftime('%H:%M') < config['time']:
            return None
    day = now.astimezone(MOSCOW).date()
    kind = 'diagnostic' if diagnostic else 'daily'
    # Caller holds the schedule row lock. Unique constraint also fences other writers.
    job = await db.scalar(select(ChurnJob).where(ChurnJob.business_date == day, ChurnJob.kind == kind))
    if job is None:
        job = ChurnJob(id=str(uuid4()), business_date=day, kind=kind, policy_revision=row.revision,
            status='QUEUED', created_at=now, updated_at=now, counters={})
        db.add(job)
        await db.flush()
    return job


def guard_job(job, row, now):
    if job.kind != 'diagnostic' and (not row or not row.enabled or not row.approved
            or row.revision != job.policy_revision):
        raise ValueError('POLICY_REVOKED_OR_CHANGED')
    if job.business_date != now.astimezone(MOSCOW).date():
        raise ValueError('JOB_EXPIRED')


async def page(db, job, *, now, size=25):
    guard_job(job, await db.get(ChurnSchedule, 1), now)
    if job.kind != 'daily' or job.status not in {'RELEASED', 'DELIVERING'} or not job.release_id:
        return []
    from app.services.analytics_release_guard import current_release
    await current_release(db, job.release_id)
    done = select(ChurnReceipt.event_id).where(ChurnReceipt.job_id == job.id)
    return list(await db.scalars(select(AnalyticsAutomationEvent).where(
        AnalyticsAutomationEvent.release_id == job.release_id,
        AnalyticsAutomationEvent.id.not_in(done)).order_by(AnalyticsAutomationEvent.id).limit(size)))


DECISIONS = {'TASK_CREATED', 'TASK_EXISTS', 'BLOCKED', 'REVIEW'}


async def acknowledge(db, job, receipts, *, now):
    """Called only by private CLI after the local Calltrack command returns.

    Lost acknowledgements are replayed to Calltrack with the same immutable event.
    Only an atomic receiver TASK_CREATED/TASK_EXISTS decision confirms acceptance.
    """
    if not isinstance(receipts, list) or not 1 <= len(receipts) <= 25:
        raise ValueError('INVALID_RECEIPTS')
    ids = [r.get('event_id') for r in receipts if isinstance(r, dict)]
    if len(ids) != len(receipts) or any(type(v) is not int for v in ids) or len(set(ids)) != len(ids):
        raise ValueError('INVALID_RECEIPTS')
    events = {e.id: e for e in await db.scalars(select(AnalyticsAutomationEvent).where(
        AnalyticsAutomationEvent.release_id == job.release_id, AnalyticsAutomationEvent.id.in_(ids)))}
    for receipt in receipts:
        event = events.get(receipt['event_id'])
        decision = receipt.get('decision')
        task = receipt.get('task_id')
        created = receipt.get('created_for_release', decision == 'TASK_CREATED')
        reasons = receipt.get('reasons', [])
        if (not event or receipt.get('event_hash') != digest(event.payload) or decision not in DECISIONS or type(created) is not bool
                or not isinstance(reasons, list) or len(reasons) > 30
                or any(not isinstance(r, str) or not re.fullmatch('[A-Z0-9_]{1,64}', r) for r in reasons)
                or (task is not None and (not isinstance(task, str) or not re.fullmatch('[a-zA-Z0-9-]{1,64}', task)))
                or (decision in {'TASK_CREATED', 'TASK_EXISTS'} and not task)):
            raise ValueError('INVALID_RECEIVER_ACKNOWLEDGEMENT')
        old = await db.scalar(select(ChurnReceipt).where(ChurnReceipt.job_id == job.id, ChurnReceipt.event_id == event.id))
        if old:
            if (old.event_hash != receipt['event_hash'] or old.task_id != task
                    or old.created_for_release != created
                    or (old.decision != decision and {old.decision, decision} != {'TASK_CREATED', 'TASK_EXISTS'})):
                raise ValueError('RECEIPT_CONFLICT')
            continue
        db.add(ChurnReceipt(id=str(uuid4()), job_id=job.id, event_id=event.id, event_hash=receipt['event_hash'],
            decision=decision, created_for_release=created, task_id=task, reasons=reasons, observed_at=now))
    await db.flush()
    records = list(await db.scalars(select(ChurnReceipt).where(ChurnReceipt.job_id == job.id)))
    accepted = sum(r.decision in {'TASK_CREATED', 'TASK_EXISTS'} for r in records)
    counts = dict(job.counters, sent=len(records), accepted=accepted,
        tasks_created=len({r.task_id for r in records if r.created_for_release and r.task_id}),
        tasks_linked=len({r.task_id for r in records if r.task_id}), rejected=len(records)-accepted)
    job.counters = counts
    job.status = 'DELIVERING'
    total = int(counts.get('allowed', 0))
    if len(records) == total:
        job.status = 'SUCCEEDED' if accepted == total else 'PARTIAL'
    job.updated_at = now


async def overview(db, now=None):
    now = now or datetime.now(timezone.utc)
    row = await db.get(ChurnSchedule, 1)
    jobs = list(await db.scalars(select(ChurnJob).order_by(ChurnJob.created_at.desc()).limit(30)))
    return dict(policy=dict(revision=row.revision, enabled=row.enabled, approved=row.approved,
        settings=row.settings_json) if row else dict(revision=0, enabled=False, approved=False, settings={}),
        next_run=next_run(row.settings_json, now).isoformat() if row and row.enabled and row.approved else None,
        schedule_timezone='Europe/Moscow', scheduler='HOST_SYSTEMD_OPT_IN',
        history=[dict(id=j.id, business_date=j.business_date.isoformat(), kind=j.kind, status=j.status,
            run_id=j.run_id, release_id=j.release_id, updated_at=utc(j.updated_at).isoformat(),
            counters=j.counters, error_code=j.error_code) for j in jobs])
