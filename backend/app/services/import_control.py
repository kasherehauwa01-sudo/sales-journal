"""Metadata-only completeness checks; never downloads or reimports sales."""

import hashlib, json, re, unicodedata, uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import PurePosixPath
from sqlalchemy import select, text, or_, func
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import noload
from app.services.import_deadline import deadline
from fastapi import HTTPException
from app.models import ImportBatch
from app.models.import_control import (
    ImportControlConfig,
    ImportDepartment,
    ImportRule,
    ImportCycle,
    ImportReceipt,
    ImportDayCheck,
    ImportIssue,
    ImportControlAudit,
    ImportNotice,
)

CONTROL_LOCK = 734190270
FTP_LOCK = 734190271
MAIL_LOCK = 734190272
OPEN_STATES = ("missing", "error")
FORMATS = {"dmy": "%d.%m.%Y", "iso": "%Y-%m-%d"}


def now():
    return datetime.now(timezone.utc)


def canonical_filename(value):
    value = unicodedata.normalize("NFC", value)
    if (
        not value
        or len(value) > 512
        or any(ord(c) < 32 or c in "/\\#" for c in value)
        or value in (".", "..")
    ):
        raise ValueError("Некорректное имя файла")
    return value


def validate_template(value, fmt="dmy"):
    canonical_filename(value)
    if (
        fmt not in FORMATS
        or value.count("{date}") > 1
        or "{" in value.replace("{date}", "")
        or "}" in value.replace("{date}", "")
    ):
        raise ValueError("Разрешено точное имя или один маркер {date}; формат dmy/iso")
    return unicodedata.normalize("NFC", value)


def expected_filename(rule, day):
    return rule.filename_template.replace(
        "{date}", day.strftime(FORMATS[rule.date_format])
    )


def patterns_overlap(a, af, b, bf):
    # Conservative language intersection: reject possible ambiguity even if a
    # digit combination is not a real calendar date. No unbounded regex/globs.
    a = a.replace("{date}", "##.##.####" if af == "dmy" else "####-##-##")
    b = b.replace("{date}", "##.##.####" if bf == "dmy" else "####-##-##")
    return len(a) == len(b) and all(
        x == y
        or (x == "#" and y.isascii() and y.isdigit())
        or (y == "#" and x.isascii() and x.isdigit())
        for x, y in zip(a, b)
    )


def applies(rule, day):
    return rule.start_date <= day and (rule.end_date is None or day <= rule.end_date)


def missing_label(count):
    if count % 10 == 1 and count % 100 != 11:
        return f"Не загружен {count} файл"
    word = "файла" if 2 <= count % 10 <= 4 and not 12 <= count % 100 <= 14 else "файлов"
    return f"Не загружено {count} {word}"


def receipt_error_label(code):
    return {
        "empty_file": "Нулевой размер файла",
        "validation_failed": "Файл отклонён действующим валидатором строк",
        "ftp_download_failed": "Ошибка получения файла FTP",
        "corrected_source_requires_review": "Исправленная версия требует проверки переимпорта",
        "legacy_source_requires_review": "Нет контрольной суммы прежней версии; нужна проверка",
        "source_period_requires_review": "Период изменённого источника не подтверждён",
        "source_identity_unavailable": "Не удалось подтвердить идентичность источника",
        "source_import_in_progress": "Этот источник уже обрабатывается",
        "import_interrupted": "Обработка прервана",
    }.get(code, "Ошибка обработки; подробности в журнале импорта")


async def lock(db):
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": CONTROL_LOCK})


async def audit(db, entity, identity, actor, changes, effective=None):
    db.add(
        ImportControlAudit(
            entity=entity,
            entity_id=str(identity),
            actor=actor,
            effective_date=effective,
            changes=json.loads(json.dumps(changes, default=str)),
        )
    )


async def config(db):
    value = await db.get(ImportControlConfig, 1)
    if not value:
        await db.execute(
            insert(ImportControlConfig)
            .values(id=1)
            .on_conflict_do_nothing(index_elements=["id"])
        )
        value = await db.get(ImportControlConfig, 1)
    return value


async def save_rule(
    db, department, start, end, template, fmt, display_name, active, controlled, actor
):
    await lock(db)
    template = validate_template(template, fmt)
    if end and end < start:
        raise ValueError("Дата окончания раньше начала")
    # Effective edit splits the old interval, never overwrites its history.
    rows = (
        await db.scalars(
            select(ImportRule).where(
                or_(ImportRule.end_date.is_(None), ImportRule.end_date >= start)
            )
        )
    ).all()
    replace_rule = None
    for row in rows:
        if end and row.start_date > end:
            continue
        if row.department_id == department.id:
            if row.start_date == start:
                verified = await db.scalar(
                    select(ImportDayCheck.sales_date)
                    .where(ImportDayCheck.sales_date >= start)
                    .limit(1)
                )
                if verified:
                    raise ValueError(
                        "Историческое правило уже проверялось; выберите новую дату вступления"
                    )
                replace_rule = row
                continue
            if row.start_date > start:
                raise ValueError(
                    "На эту дату уже есть правило; выберите новую дату вступления"
                )
            before = {"end_date": row.end_date}
            row.end_date = start - timedelta(days=1)
            await audit(
                db,
                "rule",
                row.id,
                actor,
                {"before": before, "after": {"end_date": row.end_date}},
                start,
            )
        elif (
            row.active
            and row.controlled
            and active
            and controlled
            and patterns_overlap(row.filename_template, row.date_format, template, fmt)
        ):
            raise ValueError(
                "Шаблон может совпасть с файлом другого подразделения на ту же дату"
            )
    if replace_rule:
        fields = {
            "end_date": end,
            "filename_template": template,
            "date_format": fmt,
            "display_name": display_name,
            "active": active,
            "controlled": controlled,
        }
        before = {key: getattr(replace_rule, key) for key in fields}
        for key, value in fields.items():
            setattr(replace_rule, key, value)
        await db.flush()
        await audit(
            db,
            "rule",
            replace_rule.id,
            actor,
            {"before": before, "after": fields},
            start,
        )
        c = await config(db)
        c.revision += 1
        return replace_rule
    rule = ImportRule(
        department_id=department.id,
        start_date=start,
        end_date=end,
        filename_template=template,
        date_format=fmt,
        display_name=display_name,
        active=active,
        controlled=controlled,
    )
    db.add(rule)
    await db.flush()
    await audit(
        db,
        "rule",
        rule.id,
        actor,
        {
            "created": {
                "department_id": department.id,
                "start_date": start,
                "end_date": end,
                "filename_template": template,
                "date_format": fmt,
                "display_name": display_name,
                "active": active,
                "controlled": controlled,
            }
        },
        start,
    )
    c = await config(db)
    c.revision += 1
    return rule


async def record_receipt(
    db, batch, cycle_id=None, filename=None, digest=None, departments=None
):
    receipt = await db.scalar(
        select(ImportReceipt).where(ImportReceipt.import_id == batch.id)
    )
    if not receipt:
        receipt = ImportReceipt(
            import_id=batch.id,
            cycle_id=cycle_id,
            filename=canonical_filename(
                (filename or batch.filename).removeprefix("EROOR_")
            ),
            status="processing",
            departments=[],
        )
        db.add(receipt)
    if digest:
        receipt.sha256 = digest
    if departments is not None:
        receipt.departments = sorted(departments)[:64]
    receipt.period_start = batch.declared_period_start or batch.period_start
    receipt.period_end = batch.declared_period_end or batch.period_end
    receipt.period_method = batch.source_period_method or (
        "OBSERVED_SALES" if batch.period_start else None
    )
    receipt.status = (
        "success"
        if batch.status == "completed" and batch.error_rows == 0 and batch.file_size > 0
        else ("error" if batch.status in ("completed", "failed") else batch.status)
    )
    known_errors = {
        "corrected_source_requires_review",
        "source_period_requires_review",
        "legacy_source_requires_review",
        "source_identity_unavailable",
        "source_import_in_progress",
        "import_interrupted",
        "ftp_download_failed",
    }
    receipt.error_code = (
        batch.error_text
        if batch.error_text in known_errors
        else (
            "empty_file"
            if batch.file_size == 0
            else (
                "import_failed"
                if batch.status == "failed"
                else "validation_failed" if batch.error_rows else None
            )
        )
    )
    if receipt.status == "error" and not receipt.period_start and receipt.cycle_id:
        cycle = await db.get(ImportCycle, receipt.cycle_id)
        if cycle and cycle.sales_date:
            receipt.period_start = receipt.period_end = cycle.sales_date
            receipt.period_method = "APPROVED_CYCLE_ERROR"
    receipt.finished_at = batch.finished_at
    if receipt.status == "success" and receipt.sha256:
        receipt.duplicate_of = await db.scalar(
            select(ImportReceipt.id)
            .where(
                ImportReceipt.filename == receipt.filename,
                ImportReceipt.sha256 == receipt.sha256,
                ImportReceipt.status == "success",
                ImportReceipt.import_id != batch.id,
            )
            .order_by(ImportReceipt.id)
            .limit(1)
        )
    return receipt


def receipt_state(rule, day, receipts):
    name = expected_filename(rule, day)
    matched = [
        r for r in receipts if r.filename == name or r.filename == "EROOR_" + name
    ]
    # Only existing source/observed period metadata gives successful coverage.
    covered = [
        r
        for r in matched
        if r.period_start
        and r.period_end
        and r.period_start <= day <= r.period_end
        and (
            r.period_start == r.period_end
            or getattr(r, "period_method", None) != "OBSERVED_SALES"
        )
    ]
    errors = [
        r
        for r in matched
        if r.status == "error" and (r in covered or "{date}" in rule.filename_template)
    ]
    successes = [r for r in covered if r.status == "success"]
    last_success = max((r.import_id for r in successes), default=0)
    errors = [r for r in errors if r.import_id > last_success]
    if errors:
        latest = max(errors, key=lambda r: r.import_id)
        return "error", latest.error_code, latest.import_id
    if successes:
        return "success", None, None
    if any(r.status in ("processing", "queued") for r in matched):
        return "waiting", None, None
    if any(r.status == "success" for r in matched) and not covered:
        return "unavailable", "unverified_period", None
    return "missing", None, None


async def check_date(db, day, *, confirmed=False, actor="system"):
    await lock(db)
    c = await config(db)
    if not c.enabled or not c.start_date or day < c.start_date:
        return {
            "sales_date": day,
            "status": "unavailable",
            "error_code": "control_not_enabled",
            "expected": 0,
            "successful": 0,
            "missing": 0,
            "errors": 0,
        }
    rules = (
        await db.scalars(
            select(ImportRule)
            .where(
                ImportRule.start_date <= day,
                or_(ImportRule.end_date.is_(None), ImportRule.end_date >= day),
                ImportRule.active.is_(True),
                ImportRule.controlled.is_(True),
            )
            .limit(1001)
        )
    ).all()
    if len(rules) > 1000:
        raise HTTPException(503, "Слишком много правил для безопасной проверки")
    names = [expected_filename(r, day) for r in rules]
    # Reuse existing successful journal evidence, without rereading old files.
    observed_start = func.coalesce(
        ImportBatch.declared_period_start, ImportBatch.period_start
    )
    observed_end = func.coalesce(
        ImportBatch.declared_period_end, ImportBatch.period_end
    )
    legacy = (
        (
            await db.scalars(
                select(ImportBatch)
                .options(noload(ImportBatch.errors))
                .outerjoin(ImportReceipt, ImportReceipt.import_id == ImportBatch.id)
                .where(
                    ImportReceipt.id.is_(None),
                    ImportBatch.status.in_(("completed", "failed")),
                    ImportBatch.filename.in_(names + ["EROOR_" + n for n in names]),
                    or_(
                        observed_start.is_(None),
                        (observed_start <= day) & (observed_end >= day),
                    ),
                )
                .order_by(ImportBatch.id)
                .limit(1001)
            )
        ).all()
        if names
        else []
    )
    if len(legacy) > 1000:
        return {
            "sales_date": day,
            "status": "unavailable",
            "error_code": "legacy_evidence_limit",
            "expected": len(rules),
            "successful": 0,
            "missing": 0,
            "errors": 0,
        }
    for batch in legacy:
        await record_receipt(db, batch)
    await db.flush()
    receipts = (
        (
            await db.scalars(
                select(ImportReceipt)
                .where(
                    ImportReceipt.filename.in_(names + ["EROOR_" + n for n in names]),
                    or_(
                        ImportReceipt.period_start.is_(None),
                        (ImportReceipt.period_start <= day)
                        & (ImportReceipt.period_end >= day),
                    ),
                )
                .order_by(ImportReceipt.id)
                .limit(5001)
            )
        ).all()
        if names
        else []
    )
    if len(receipts) > 5000:
        return {
            "sales_date": day,
            "status": "unavailable",
            "error_code": "evidence_limit",
            "expected": len(rules),
            "successful": 0,
            "missing": 0,
            "errors": 0,
        }
    pending = (
        (
            await db.execute(
                select(ImportBatch.id, ImportBatch.filename)
                .where(
                    ImportBatch.status.in_(("queued", "processing")),
                    ImportBatch.filename.in_(names + ["EROOR_" + n for n in names]),
                )
                .limit(1001)
            )
        ).all()
        if names
        else []
    )
    cycles = (
        await db.scalars(
            select(ImportCycle)
            .where(ImportCycle.sales_date == day)
            .order_by(ImportCycle.started_at.desc())
            .limit(20)
        )
    ).all()
    # Even manual confirmation cannot finalize a day before the approved deadline.
    final = now() >= deadline(day) and (
        confirmed
        or bool(
            cycles
            and cycles[0].status == "finished"
            and cycles[0].source_confirmed
            and cycles[0].started_at >= deadline(day)
        )
    )
    states = [(rule, *receipt_state(rule, day, receipts)) for rule in rules]
    completed_sources = {
        expected_filename(r, day): max(
            (
                x.import_id
                for x in receipts
                if x.status == "success" and x.filename == expected_filename(r, day)
            ),
            default=0,
        )
        for r, state, _, _ in states
    }
    pending = [
        (identity, name)
        for identity, name in pending
        if identity > completed_sources.get(name.removeprefix("EROOR_"), 0)
    ]
    if pending or any(x.status == "running" for x in cycles):
        final = False
    counts = {
        key: sum(item[1] == key for item in states)
        for key in ("success", "missing", "error", "unavailable", "waiting")
    }
    status = (
        "unavailable"
        if not rules
        or counts["unavailable"]
        or (cycles and cycles[0].status in ("failed", "interrupted"))
        else (
            "waiting"
            if not final or counts["waiting"]
            else (
                "error"
                if counts["error"]
                else "incomplete" if counts["missing"] else "complete"
            )
        )
    )
    at = now()
    closed = set()
    for rule, state, error, import_id in states:
        issue = await db.scalar(
            select(ImportIssue).where(
                ImportIssue.sales_date == day,
                ImportIssue.department_id == rule.department_id,
            )
        )
        if state == "success" and issue and issue.status in OPEN_STATES:
            before = issue.status
            issue.status = "resolved"
            issue.resolved_at = at
            issue.closure_method = "late_import"
            await audit(
                db,
                "issue",
                issue.id,
                actor,
                {"before": before, "after": "resolved"},
                day,
            )
        elif final and state in OPEN_STATES:
            if not issue:
                issue = ImportIssue(
                    sales_date=day,
                    department_id=rule.department_id,
                    rule_id=rule.id,
                    department_name=rule.display_name,
                    expected_filename=expected_filename(rule, day),
                    status=state,
                    error_code=error,
                    import_id=import_id,
                    checked_at=at,
                )
                db.add(issue)
                await db.flush()
                await audit(db, "issue", issue.id, actor, {"created": state}, day)
            elif issue.status != "closed":
                if issue.status != state:
                    await audit(
                        db,
                        "issue",
                        issue.id,
                        actor,
                        {"before": issue.status, "after": state},
                        day,
                    )
                issue.status = state
                issue.resolved_at = None
                issue.closure_method = None
                issue.error_code = error
                issue.import_id = import_id
        if issue:
            issue.checked_at = at
            if issue.status == "closed":
                closed.add(rule.department_id)
    check = await db.get(ImportDayCheck, day)
    if not check:
        check = ImportDayCheck(sales_date=day)
        db.add(check)
    for key, value in {
        "status": status,
        "expected": len(rules),
        "successful": counts["success"],
        "missing": counts["missing"],
        "errors": counts["error"],
        "checked_at": at,
        "config_revision": c.revision,
        "error_code": (
            "unverified_period"
            if counts["unavailable"]
            else "no_rules" if not rules else None
        ),
    }.items():
        setattr(check, key, value)
    fingerprint = None
    if final and status in ("incomplete", "error"):
        problems = [
            {
                "department": r.display_name,
                "department_id": r.department_id,
                "filename": expected_filename(r, day),
                "status": s,
                "error_code": e,
            }
            for r, s, e, _ in states
            if s in OPEN_STATES and r.department_id not in closed
        ]
        fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "date": day.isoformat(),
                    "problems": sorted(problems, key=lambda x: x["department_id"]),
                },
                sort_keys=True,
                ensure_ascii=False,
            ).encode()
        ).hexdigest()
        exists = await db.scalar(
            select(ImportNotice.id).where(ImportNotice.dedupe_key == fingerprint)
        )
        if problems and not exists and c.email_enabled and c.recipients:
            body = (
                f'Обнаружен неполный импорт продаж.\nДата продаж: {day:%d.%m.%Y}\nОжидается файлов: {len(rules)}\nУспешно импортировано: {counts["success"]}\nОтсутствует: {counts["missing"]}\nС ошибками: {counts["error"]}\n\n'
                + "\n".join(
                    f'{i+1}. {p["department"]}: {p["filename"]} — '
                    + (
                        "Не получен"
                        if p["status"] == "missing"
                        else "Файл получен, но не импортирован: "
                        + receipt_error_label(p["error_code"])
                    )
                    for i, p in enumerate(problems)
                )
                + "\n\nПожалуйста, проверьте выгрузку из 1С.\nЭто автоматическое уведомление системы «Журнал продаж»."
            )
            db.add(
                ImportNotice(
                    sales_date=day,
                    dedupe_key=fingerprint,
                    status="pending",
                    attempts=0,
                    recipients=c.recipients,
                    subject=f"Журнал продаж | Неполный импорт за {day:%d.%m.%Y}",
                    body=body,
                    next_attempt=at,
                )
            )
    await db.flush()
    return {
        **{
            key: getattr(check, key)
            for key in (
                "sales_date",
                "status",
                "expected",
                "successful",
                "missing",
                "errors",
                "checked_at",
                "error_code",
            )
        },
        "problem_key": fingerprint,
    }
