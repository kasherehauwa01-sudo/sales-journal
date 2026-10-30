"""Private, bounded CLI for the opt-in host scheduler. Never started by FastAPI."""
import argparse
import asyncio
from datetime import datetime, timezone, timedelta
import json
import sys
import time
from sqlalchemy import select

from app.config import settings
from app.models import (ChurnJob, ChurnSchedule, AnalyticsSignalRun, AnalyticsAutomationPolicy,
                        AnalyticsAutomationEvent, AnalyticsAutomationRelease)
from app.services import churn_schedule as schedule
from app.services.analytics_automation import release_churn, digest, canonical
from app.services.churn_receiver_evidence import attach
from app.services.analytics_signal_worker import run_batch, WORKER_LOCK
from app.services.seasonality_worker import advisory, verify


def now():
    return datetime.now(timezone.utc)


async def calculate(factory, engine, job_id, *, deadline):
    # Reuse the existing worker and its source locks; do not change durable enablement.
    async with advisory(engine, key=WORKER_LOCK) as leadership:
        while time.monotonic() < deadline:
            async with factory() as db:
                job = await db.get(ChurnJob, job_id)
                schedule.guard_job(job, await db.get(ChurnSchedule, 1), now())
                target = job.business_date - timedelta(days=1)
            # Existing checkpoints can belong to yesterday's interrupted invocation.
            # Let the worker finish its frozen requested day, then build today's run.
            result = await run_batch(factory, engine, as_of=None, scan_size=1000,
                client_size=100, leadership=leadership)
            async with factory.begin() as db:
                job = await db.get(ChurnJob, job_id, with_for_update=True)
                job.run_id = result.get('run_id')
                job.updated_at = now()
                job.status = 'CALCULATING'
                if result['status'] == 'SUCCEEDED':
                    run = await db.get(AnalyticsSignalRun, job.run_id)
                    if run.as_of == target:
                        job.counters = dict(calculated=run.counters.get('CHURN_RISK', 0))
                        job.status = 'DIAGNOSED' if job.kind == 'diagnostic' else 'AWAITING_EVIDENCE'
                elif result['status'] in {'DISABLED', 'BLOCKED', 'SUPERSEDED'}:
                    job.status, job.error_code = 'BLOCKED', 'CALCULATION_' + result['status']
                done = job.status != 'CALCULATING'
            await verify(leadership, WORKER_LOCK)
            if done:
                return
            await asyncio.sleep(.25)


async def authorize(db, job, data):
    row = await db.get(ChurnSchedule, 1, with_for_update=True)
    schedule.guard_job(job, row, now())
    if job.kind != 'daily' or job.status not in {'AWAITING_EVIDENCE', 'BLOCKED'} or not job.run_id:
        raise ValueError('JOB_NOT_READY_FOR_RELEASE')
    policy = await db.get(AnalyticsAutomationPolicy, 1)
    if not policy or policy.revision != row.settings_json['producer_policy_revision']:
        raise ValueError('PRODUCER_POLICY_REVISION_CHANGED')
    if set(data) != {'manifest', 'receiver_evidence'}:
        raise ValueError('INVALID_EVIDENCE_BUNDLE')
    envelope, bundle = data['manifest'], data['receiver_evidence']
    if envelope.get('payload', {}).get('receiver_evidence_hash') != digest(bundle):
        raise ValueError('RECEIVER_EVIDENCE_NOT_BOUND')
    result = await release_churn(db, job.run_id, envelope)
    job.counters = dict(job.counters, prepared=result['candidates'], allowed=result['ready'])
    if result['status'] not in {'RELEASED', 'EXISTING'}:
        job.status, job.error_code = 'BLOCKED', 'RELEASE_NOT_APPROVED'
        job.updated_at = now()
        return
    job.release_id = result['release_id']
    # The transaction has not committed: receiver evidence is frozen before exposure.
    events = await db.stream_scalars(select(AnalyticsAutomationEvent).where(
        AnalyticsAutomationEvent.release_id == job.release_id).execution_options(yield_per=25))
    async for event in events:
        if 'permanent_policy' in event.payload:
            if event.payload['permanent_policy']['producer_revision'] != job.policy_revision:
                raise ValueError('IMMUTABLE_RELEASE_CONFLICT')
            continue
        if result['status'] == 'EXISTING':
            raise ValueError('IMMUTABLE_RELEASE_CONFLICT')
        event.payload = attach(event.payload, bundle, expected_hash=envelope['payload']['receiver_evidence_hash'],
            policy_revision=job.policy_revision, config=row.settings_json, producer_proof=envelope['payload'])
    await events.close()
    if result['status'] == 'RELEASED':
        release = await db.get(AnalyticsAutomationRelease, job.release_id)
        first = await db.scalar(select(AnalyticsAutomationEvent).where(
            AnalyticsAutomationEvent.release_id == job.release_id).order_by(AnalyticsAutomationEvent.id).limit(1))
        release.metadata_json = dict(release.metadata_json, run=first.payload['run'],
            permanent_policy=dict(version=schedule.POLICY, revision=job.policy_revision))
    job.status, job.error_code, job.updated_at = 'RELEASED', None, now()


async def execute(args, data):
    from app.database import SessionLocal, engine
    try:
        async with advisory(engine, key=schedule.SCHEDULE_LOCK) as leadership:
            if args.command == 'tick':
                async with SessionLocal.begin() as db:
                    job = await schedule.enqueue(db, now=now())
                    jid = job.id if job else None
                    calculate_needed = job and job.status in {'QUEUED', 'CALCULATING'}
                # Manually queued diagnostics are allowed even with transmission disabled.
                if not jid:
                    async with SessionLocal() as db:
                        job = await db.scalar(select(ChurnJob).where(ChurnJob.kind == 'diagnostic',
                            ChurnJob.business_date == now().astimezone(schedule.MOSCOW).date(),
                            ChurnJob.status.in_(['QUEUED', 'CALCULATING'])))
                        jid = job.id if job else None
                        calculate_needed = bool(job)
                if calculate_needed:
                    await calculate(SessionLocal, engine, jid, deadline=time.monotonic()+args.seconds)
                async with SessionLocal() as db:
                    return await schedule.overview(db)
            async with SessionLocal.begin() as db:
                if args.command in {'draft', 'approve', 'enable', 'disable'}:
                    return await schedule.change(db, args.command, data, revision=args.revision, actor=args.actor)
                if args.command == 'status':
                    return await schedule.overview(db)
                job = await db.get(ChurnJob, args.job_id, with_for_update=True)
                if not job:
                    raise ValueError('JOB_NOT_FOUND')
                if args.command == 'authorize':
                    await authorize(db, job, data)
                elif args.command == 'page':
                    rows = await schedule.page(db, job, now=now())
                    return dict(job_id=job.id, release_id=job.release_id, events=[dict(event_id=e.id,
                        event_hash=digest(e.payload), event_json=canonical(e.payload)) for e in rows])
                elif args.command == 'ack':
                    if set(data) != {'job_id', 'release_id', 'receipts'} or data['job_id'] != job.id or data['release_id'] != job.release_id:
                        raise ValueError('ACK_CONTEXT_MISMATCH')
                    await schedule.acknowledge(db, job, data['receipts'], now=now())
                elif args.command == 'failure':
                    if job.status not in schedule.TERMINAL:
                        attempts = int(job.counters.get('failed_attempts', 0))+1
                        job.counters = dict(job.counters, failed_attempts=attempts,
                            retry_after=(now()+timedelta(seconds=60*2**min(attempts, 5))).isoformat())
                        job.error_code = 'HOST_STAGE_FAILED'
                        job.updated_at = now()
                        if attempts >= 3:
                            job.status = 'FAILED'
                elif args.command == 'retry':
                    schedule.guard_job(job, await db.get(ChurnSchedule, 1), now())
                    if job.status not in {'FAILED', 'BLOCKED'}:
                        raise ValueError('JOB_NOT_RETRYABLE')
                    from app.models import AnalyticsAutomationDecision
                    from uuid import uuid4
                    db.add(AnalyticsAutomationDecision(id=str(uuid4()), source_run_id='PERMANENT_RETRY',
                        created_at=now(), data=dict(actor=args.actor, job_id=job.id, previous_error=job.error_code)))
                    job.counters = {k:v for k,v in job.counters.items() if k not in {'failed_attempts', 'retry_after'}}
                    job.status = 'RELEASED' if job.release_id else 'QUEUED'
                    job.error_code = None
                    job.updated_at = now()
                await verify(leadership, schedule.SCHEDULE_LOCK)
                return dict(job_id=job.id, status=job.status, counters=job.counters, error_code=job.error_code)
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['tick', 'status', 'draft', 'approve', 'enable', 'disable', 'authorize', 'page', 'ack', 'failure', 'retry'])
    parser.add_argument('--job-id')
    parser.add_argument('--revision', type=int)
    parser.add_argument('--actor', default='host-scheduler')
    parser.add_argument('--seconds', type=int, default=45)
    args = parser.parse_args()
    if not 1 <= args.seconds <= 600:
        parser.error('Invalid bounded runtime')
    try:
        raw = sys.stdin.buffer.read(4*1024*1024+1) if args.command in {'draft', 'approve', 'authorize', 'ack'} else b'{}'
        if len(raw) > 4*1024*1024:
            raise ValueError('INPUT_TOO_LARGE')
        data = json.loads(raw)
        with asyncio.Runner() as runner:
            result = runner.run(asyncio.wait_for(execute(args, data), args.seconds+40))
        print(json.dumps(result, default=str))
    except Exception:
        # Never expose a private manifest, SQL, token, customer name or exception text.
        print(json.dumps({'status': 'ERROR', 'code': 'CHURN_COMMAND_FAILED'}))
        raise SystemExit(1)


if __name__ == '__main__':
    main()
