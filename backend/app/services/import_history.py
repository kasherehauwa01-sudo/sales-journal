"""Metadata reconciliation only; never fetch files, sales or enqueue historical mail."""

import asyncio
import hashlib
import json
import logging
import re
import time
from datetime import date, timedelta

from fastapi import HTTPException
from sqlalchemy import select, func, text, or_

from app.models.import_control import (
    ImportDepartment,
    ImportRule,
    ImportHistoryJob,
    ImportHistoryResult,
    ImportIssue,
)
from app.services import import_control as control
from app.services.import_deadline import MOSCOW
from app.services.product_analytics_runtime import ensure_headroom

BATCH_SIZE = 25
BATCH_SECONDS = 3
log = logging.getLogger(__name__)


async def rules_snapshot(db):
    rows = (
        await db.scalars(select(ImportRule).order_by(ImportRule.id).limit(10001))
    ).all()
    if len(rows) > 10000:
        raise HTTPException(503, "Превышен безопасный размер справочника")
    fields = (
        "id",
        "department_id",
        "start_date",
        "end_date",
        "filename_template",
        "date_format",
        "display_name",
        "active",
        "controlled",
    )
    payload = [{k: getattr(r, k) for k in fields} for r in rows]
    digest = hashlib.sha256(
        json.dumps(payload, default=str, sort_keys=True).encode()
    ).hexdigest()
    return rows, digest


def make_plan(rows, end, department_id=None, start=None):
    """Per-department first rule, never the daily configuration's activation date."""
    first = {}
    for r in rows:
        if department_id is None or r.department_id == department_id:
            first[r.department_id] = min(
                first.get(r.department_id, r.start_date), r.start_date
            )
    return [
        {
            "department_id": dep,
            "first_rule_date": first_day.isoformat(),
            "date_from": max(first_day, start or first_day).isoformat(),
            "date_to": end.isoformat(),
        }
        for dep, first_day in sorted(first.items())
        if max(first_day, start or first_day) <= end
    ]


async def start(db, *, actor, department_id=None, date_from=None, date_to=None):
    await control.lock(db)
    old = await db.get(ImportHistoryJob, 1)
    if old and old.status == "running":
        raise HTTPException(409, "Историческая сверка уже выполняется")
    end = date_to or control.now().astimezone(MOSCOW).date()
    if end > control.now().astimezone(MOSCOW).date() or (date_from and date_from > end):
        raise HTTPException(422, "Некорректный период сверки")
    rows, digest = await rules_snapshot(db)
    plan = make_plan(rows, end, department_id, date_from)
    if not plan or len(plan) > 1000:
        raise HTTPException(
            422, "Нет правил в выбранном периоде или слишком много подразделений"
        )
    # Revisit obsolete previously checked days on an explicit full recalculation.
    # No expectations are created before a first rule; obsolete results are retained as not_expected.
    if date_from is None:
        for part in plan:
            previous = await db.scalar(
                select(func.min(ImportHistoryResult.sales_date)).where(
                    ImportHistoryResult.department_id == part["department_id"]
                )
            )
            if previous and previous < date.fromisoformat(part["date_from"]):
                part["date_from"] = previous.isoformat()
    row = old or ImportHistoryJob(id=1)
    if not old:
        db.add(row)
    row.plan, row.rules_hash = plan, digest
    row.position, row.checked = 0, 0
    row.total = sum(
        (date.fromisoformat(p["date_to"]) - date.fromisoformat(p["date_from"])).days + 1
        for p in plan
    )
    row.next_date = date.fromisoformat(plan[0]["date_from"])
    row.status, row.error_code = "running", None
    row.started_at = row.updated_at = control.now()
    await control.audit(db, "history", 1, actor, {"start": plan, "rules_hash": digest})
    await db.flush()
    return row


async def command(db, action, actor):
    await control.lock(db)
    row = await db.get(ImportHistoryJob, 1)
    if not row:
        raise HTTPException(404, "Историческая сверка ещё не запускалась")
    if action == "resume":
        _, digest = await rules_snapshot(db)
        if digest != row.rules_hash:
            raise HTTPException(
                409, "Правила изменились. Запустите новую сверку с начала правил"
            )
        if row.status == "completed":
            raise HTTPException(409, "Сверка завершена. Для пересчёта начните новую")
        row.status, row.error_code = "running", None
    else:
        row.status = "paused" if row.status != "completed" else "completed"
    row.updated_at = control.now()
    await control.audit(db, "history", 1, actor, {"action": action})
    return row


async def step(db):
    # Zero lock wait: the primary import/settings/day checker always take precedence.
    acquired = await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": control.CONTROL_LOCK}
    )
    if not acquired:
        return False
    await db.execute(text("SET LOCAL statement_timeout='2s'"))
    await db.execute(text("SET LOCAL lock_timeout='100ms'"))
    row = await db.get(ImportHistoryJob, 1)
    if not row or row.status != "running":
        return False
    _, digest = await rules_snapshot(db)
    if digest != row.rules_hash:
        row.status, row.error_code = "paused", "rules_changed"
        row.updated_at = control.now()
        return False
    ensure_headroom()
    part = row.plan[row.position]
    day, dep = row.next_date, part["department_id"]
    result = await control.check_date(
        db, day, historical=True, department_id=dep, actor="history-worker"
    )
    # Fail-closed bounds must not silently skip a date and claim completion.
    if result.get("error_code") in ("legacy_evidence_limit", "evidence_limit"):
        row.status, row.error_code = "error", result["error_code"]
        return False
    active = await db.scalar(
        select(ImportRule.id).where(
            ImportRule.department_id == dep,
            ImportRule.start_date <= day,
            or_(ImportRule.end_date.is_(None), ImportRule.end_date >= day),
            ImportRule.active.is_(True),
            ImportRule.controlled.is_(True),
        )
    )
    if not active:
        previous = await db.get(ImportHistoryResult, (day, dep))
        if previous:
            previous.status, previous.expected_filename, previous.rule_id = (
                "not_expected",
                None,
                None,
            )
            previous.checked_at, previous.error_code = (
                control.now(),
                "rule_no_longer_applies",
            )
        issue = await db.scalar(
            select(ImportIssue).where(
                ImportIssue.department_id == dep,
                ImportIssue.sales_date == day,
                ImportIssue.status.in_(control.OPEN_STATES),
            )
        )
        if issue:
            issue.status, issue.closure_method = "resolved", "rule_no_longer_applies"
            issue.resolved_at = issue.checked_at = control.now()
            await control.audit(
                db,
                "issue",
                issue.id,
                "history-worker",
                {"resolved": "rule_no_longer_applies"},
                day,
            )
    row.checked += 1
    row.next_date = day + timedelta(days=1)
    if row.next_date > date.fromisoformat(part["date_to"]):
        row.position += 1
        if row.position == len(row.plan):
            row.status, row.next_date = "completed", None
        else:
            row.next_date = date.fromisoformat(row.plan[row.position]["date_from"])
    row.updated_at = control.now()
    return True


async def tick(factory, *, batch_size=BATCH_SIZE, seconds=BATCH_SECONDS, stop=None):
    if not 1 <= batch_size <= 100 or not 0 < seconds <= 10:
        raise ValueError("Unsafe history batch limits")
    started, done = time.perf_counter(), 0
    while done < batch_size and time.perf_counter() - started < seconds:
        if stop and stop.is_set():
            break
        async with asyncio.timeout(
            max(0.01, seconds - (time.perf_counter() - started))
        ):
            async with factory() as db:
                advanced = await step(db)
                await db.commit()  # checkpoint and result/issue are one atomic transaction
        if not advanced:
            break
        done += 1
        await asyncio.sleep(0)  # cancellation between committed days is safe
    log.info(
        "import_history batch checked=%d elapsed_ms=%.3f",
        done,
        (time.perf_counter() - started) * 1000,
    )
    return done


# A dated literal may be intentional. Only propose, never silently generalize it.
DATED_FILE = re.compile(
    r"(?P<prefix>.+_)(?P<date>\d{2}\.\d{2}\.\d{4}|\d{4}-\d{2}-\d{2})(?P<suffix>\.[A-Za-z0-9]+)$"
)


def proposed_template(rule):
    match = DATED_FILE.fullmatch(rule.filename_template)
    if not match:
        return None
    from datetime import datetime

    try:
        datetime.strptime(match["date"], control.FORMATS[rule.date_format])
    except ValueError:
        return None
    return match["prefix"] + "{date}" + match["suffix"]


async def repair_preview(db):
    rows, digest = await rules_snapshot(db)
    departments = dict(
        (await db.execute(select(ImportDepartment.id, ImportDepartment.code))).all()
    )
    return {
        "token": digest,
        "items": [
            {
                "rule_id": r.id,
                "department_id": r.department_id,
                "department_code": departments.get(r.department_id),
                "start_date": r.start_date,
                "end_date": r.end_date,
                "before": r.filename_template,
                "after": proposed_template(r),
            }
            for r in rows
            if proposed_template(r)
        ],
    }


async def repair_apply(db, *, token, ids, actor, reason):
    await control.lock(db)
    job = await db.get(ImportHistoryJob, 1)
    if job and job.status == "running":
        raise HTTPException(409, "Сначала приостановите историческую сверку")
    proposal = await repair_preview(db)
    if proposal["token"] != token:
        raise HTTPException(
            409, "Правила изменились. Получите новый предварительный отчёт"
        )
    selected = [p for p in proposal["items"] if p["rule_id"] in ids]
    if len(selected) != len(set(ids)) or not selected:
        raise HTTPException(422, "Выберите правила из предварительного отчёта")
    for item in selected:
        rule = await db.get(ImportRule, item["rule_id"])
        rule.filename_template = control.validate_template(
            item["after"], rule.date_format
        )
        # Existing PostgreSQL rule guard rejects cross-department collisions.
        await db.flush()
        await control.audit(
            db,
            "rule",
            rule.id,
            actor,
            {
                "before": {"filename_template": item["before"]},
                "after": {"filename_template": item["after"]},
                "reason": reason,
                "historical_correction": True,
            },
            rule.start_date,
        )
    c = await control.config(db)
    c.revision += 1
    return {"updated": len(selected)}


async def safe_tick(factory):
    """Pause on a technical/resource failure; no infinite minute-by-minute error loop."""
    from sqlalchemy.exc import SQLAlchemyError

    try:
        from app.config import settings

        return await tick(
            factory,
            batch_size=settings.import_history_batch_size,
            seconds=settings.import_history_batch_seconds,
        )
    except (HTTPException, SQLAlchemyError, TimeoutError):
        # Do not log query parameters, filenames or raw exceptions.
        async with factory() as db:
            if await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": control.CONTROL_LOCK},
            ):
                row = await db.get(ImportHistoryJob, 1)
                if row and row.status == "running":
                    row.status, row.error_code, row.updated_at = (
                        "paused",
                        "resources_or_database",
                        control.now(),
                    )
                    await db.commit()
        return 0


async def recheck_receipt(factory, batch_id):
    """Only metadata rows for this exact source; no historical file/sales scan."""
    from app.models.import_control import ImportReceipt

    async with factory() as db:
        receipt = await db.scalar(
            select(ImportReceipt).where(ImportReceipt.import_id == batch_id)
        )
        if not receipt:
            return
        pairs = (
            await db.execute(
                select(
                    ImportHistoryResult.sales_date, ImportHistoryResult.department_id
                )
                .where(ImportHistoryResult.expected_filename == receipt.filename)
                .order_by(ImportHistoryResult.checked_at)
                .limit(31)
            )
        ).all()
    for day, dep in pairs:
        async with factory() as db:
            if not await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": control.CONTROL_LOCK},
            ):
                return  # periodic open-issue maintenance will retry
            await control.check_date(db, day, historical=True, department_id=dep)
            await db.commit()


async def recheck_waiting(factory):
    """Waiting is not a missing issue; finalize approved historical days after 00:20."""
    from app.services.import_deadline import due_sales_day

    async with factory() as db:
        pairs = (
            await db.execute(
                select(
                    ImportHistoryResult.sales_date, ImportHistoryResult.department_id
                )
                .where(
                    ImportHistoryResult.status == "waiting",
                    ImportHistoryResult.sales_date <= due_sales_day(control.now()),
                )
                .order_by(ImportHistoryResult.checked_at)
                .limit(5)
            )
        ).all()
    for day, dep in pairs:
        async with factory() as db:
            if not await db.scalar(
                text("SELECT pg_try_advisory_xact_lock(:key)"),
                {"key": control.CONTROL_LOCK},
            ):
                return
            await control.check_date(db, day, historical=True, department_id=dep)
            await db.commit()
