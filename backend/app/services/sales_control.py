"""Actual sales only. Bounded date grid + indexed existence probes, no file rules."""

import hashlib, html, json, smtplib
from datetime import date, datetime, time, timedelta, timezone
from email.message import EmailMessage
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import select, text, update
from sqlalchemy.dialects.postgresql import insert
from app.config import settings
from app.database import engine, SessionLocal
from app.models import SmtpConfig
from app.models.sales_control import (
    SalesControlSettings,
    SalesControlExclusion,
    SalesControlAudit,
    SalesControlDelivery,
)
from app.services.email_recipients import parse_recipient_emails
from app.services.import_integrity import owned_lock, mail_failure
from app.services.import_control import MAIL_LOCK, CONTROL_LOCK
from app.services.async_resources import run_blocking

MOSCOW = ZoneInfo("Europe/Moscow")
INDEX = "ix_sales_department_date_control"
MAX_DAYS = 366
MAX_DEPARTMENTS = 1000

# Loose index scan: O(number of department values), not DISTINCT over every sale.
DEPARTMENTS = """WITH RECURSIVE names(name) AS (
 SELECT min(department) FROM sales
 UNION ALL
 SELECT (SELECT min(department) FROM sales WHERE department>names.name)
 FROM names WHERE name IS NOT NULL
), departments AS MATERIALIZED (
 SELECT name AS department,(SELECT sale_date FROM sales WHERE department=names.name ORDER BY sale_date LIMIT 1) AS first_date
 FROM names WHERE name IS NOT NULL AND btrim(name)<>'' LIMIT 1001
) """
GRID = (
    DEPARTMENTS
    + """, missing AS (
 SELECT d.department,g.day::date AS sales_date
 FROM departments d CROSS JOIN LATERAL generate_series(greatest(d.first_date,CAST(:start AS date)),CAST(:end AS date),INTERVAL '1 day') g(day)
 LEFT JOIN LATERAL (SELECT 1 AS present FROM sales s WHERE s.department=d.department AND s.sale_date=g.day::date LIMIT 1) hit ON true
 WHERE hit.present IS NULL AND (:department='' OR d.department=:department)
 AND EXISTS(SELECT 1 FROM sales_control_exclusions e WHERE e.department=d.department AND e.sales_date=g.day::date)=:hidden
 AND (:search='' OR position(lower(:search) IN lower(d.department))>0
      OR position(:search IN to_char(g.day,'DD.MM.YYYY'))>0 OR position(:search IN to_char(g.day,'YYYY-MM-DD'))>0)
) """
)


def now():
    return datetime.now(timezone.utc)


def today():
    return now().astimezone(MOSCOW).date()


async def limits(db):
    # This bounded existence query gains nothing from expensive JIT compilation.
    # Session-local only: no PostgreSQL configuration or calculation changes.
    await db.execute(text("SET LOCAL jit=off"))
    await db.execute(text("SET LOCAL statement_timeout='5s'"))
    await db.execute(text("SET LOCAL lock_timeout='1s'"))


async def index_ready(db):
    ok = await db.scalar(
        text(
            """SELECT EXISTS(SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid=i.indexrelid
      WHERE c.relname=:name AND i.indrelid='sales'::regclass AND i.indisvalid AND i.indisready
      AND NOT i.indisunique AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=2
      AND pg_get_indexdef(i.indexrelid,1,true)='department' AND pg_get_indexdef(i.indexrelid,2,true)='sale_date')"""
        ),
        {"name": INDEX},
    )
    if not ok:
        raise HTTPException(
            503, "Индекс контроля продаж не подготовлен. Обратитесь к администратору."
        )


async def departments(db):
    await limits(db)
    await index_ready(db)
    rows = (
        (
            await db.execute(
                text(
                    DEPARTMENTS
                    + "SELECT department,first_date FROM departments ORDER BY department"
                )
            )
        )
        .mappings()
        .all()
    )
    if len(rows) > MAX_DEPARTMENTS:
        raise HTTPException(
            503,
            "Превышено безопасное количество подразделений. Требуется проверка справочника продаж.",
        )
    return [dict(r) for r in rows]


async def missing(
    db,
    start=None,
    end=None,
    department="",
    search="",
    hidden=False,
    page=1,
    sort="date",
    direction="desc",
    all_rows=False,
):
    end = end or today() - timedelta(days=1)
    start = start or end - timedelta(days=29)
    if start > end or end >= today() or (end - start).days >= MAX_DAYS:
        raise HTTPException(422, "Выберите завершённый период не более 366 дней.")
    deps = await departments(db)
    if department and department not in {r["department"] for r in deps}:
        raise HTTPException(422, "Подразделение не найдено в фактических продажах.")
    params = dict(
        start=start,
        end=end,
        department=department,
        search=search.strip(),
        hidden=hidden,
        offset=(page - 1) * 50,
    )
    total = await db.scalar(text(GRID + "SELECT count(*) FROM missing"), params)
    order = ("sales_date" if sort == "date" else "department") + (
        " DESC" if direction == "desc" else " ASC"
    )
    order += ", department ASC" if sort == "date" else ", sales_date DESC"
    rows = (
        (
            await db.execute(
                text(
                    GRID
                    + "SELECT department,sales_date FROM missing ORDER BY "
                    + order
                    + ("" if all_rows else " LIMIT 50 OFFSET :offset")
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return dict(
        items=[dict(r, status="Продажи отсутствуют") for r in rows],
        total=total,
        page=page,
        date_from=start,
        date_to=end,
    )


async def settings_row(db):
    result = await db.get(SalesControlSettings, 1)
    if not result:
        raise HTTPException(503, "Настройки контроля продаж не установлены миграцией.")
    return result


def settings_out(row):
    return dict(
        enabled=row.enabled,
        send_time=row.send_time,
        recipients=row.recipients,
        revision=row.revision,
    )


async def mutation_lock(db):
    await limits(db)
    for key in (MAIL_LOCK, CONTROL_LOCK):
        if not await db.scalar(
            text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}
        ):
            raise HTTPException(
                409, "Идёт отправка или изменение настроек. Повторите действие позже."
            )


def audit(db, actor, action, details):
    db.add(
        SalesControlAudit(created_at=now(), actor=actor, action=action, details=details)
    )


async def save_settings(db, payload, actor):
    await mutation_lock(db)
    row = await settings_row(db)
    if payload.revision != row.revision:
        raise HTTPException(
            409, "Настройки изменены другим администратором. Обновите страницу."
        )
    try:
        recipients = (
            parse_recipient_emails("\n".join(payload.recipients))
            if payload.recipients
            else []
        )
    except ValueError:
        raise HTTPException(422, "Проверьте адреса получателей.")
    if len(recipients) > 100 or (payload.enabled and not recipients):
        raise HTTPException(
            422, "Для включения нужны корректные получатели (не более 100)."
        )
    old = settings_out(row)
    row.enabled = payload.enabled
    row.send_time = payload.send_time
    row.recipients = recipients
    row.revision += 1
    local = now().astimezone(MOSCOW)
    hour, minute = map(int, row.send_time.split(":"))
    next_run = datetime.combine(local.date(), time(hour, minute), MOSCOW)
    if next_run <= local:
        next_run += timedelta(days=1)
    row.not_before = next_run
    await db.execute(
        update(SalesControlDelivery)
        .where(SalesControlDelivery.manual_retry.is_(True))
        .values(manual_retry=False, error_code="settings_changed")
    )
    audit(db, actor, "settings", {"before": old, "after": settings_out(row)})
    await db.commit()
    return settings_out(row)


async def exclusions(db, items, hide, actor):
    await mutation_lock(db)
    known = {r["department"]: r["first_date"] for r in await departments(db)}
    for department, day in set(items):
        if department not in known or not known[department] <= day < today():
            raise HTTPException(422, "Некорректная пара даты и подразделения.")
        if hide:
            await db.execute(
                insert(SalesControlExclusion)
                .values(
                    department=department, sales_date=day, hidden_at=now(), actor=actor
                )
                .on_conflict_do_nothing()
            )
        else:
            from sqlalchemy import delete

            await db.execute(
                delete(SalesControlExclusion).where(
                    SalesControlExclusion.department == department,
                    SalesControlExclusion.sales_date == day,
                )
            )
    audit(
        db,
        actor,
        "hide" if hide else "restore",
        {
            "items": [
                {"department": d, "sales_date": day.isoformat()}
                for d, day in sorted(set(items))
            ]
        },
    )
    await db.commit()
    return {"changed": len(set(items))}


def recipient_hash(recipients):
    return hashlib.sha256(
        json.dumps(sorted(x.lower() for x in recipients)).encode()
    ).hexdigest()


def send_message(smtp, recipients, day, departments):
    message = EmailMessage()
    message["Subject"] = f"Контроль продаж. Отсутствуют продажи за {day:%d.%m.%Y}"
    message["From"] = f"{smtp.sender_name} <{smtp.sender_email}>"
    message["To"] = ", ".join(recipients)
    link = (
        settings.public_url.rstrip("/")
        + "/imports?view=control&date_from="
        + day.isoformat()
        + "&date_to="
        + day.isoformat()
    )
    message.set_content(
        "Здравствуйте!\n\nЗа "
        + day.strftime("%d.%m.%Y")
        + " отсутствуют продажи:\n"
        + "\n".join(departments)
        + "\n\nПожалуйста, проверьте поступление данных о продажах.\n"
        + link
        + "\n\nЖурнал продаж"
    )
    rows = "".join(
        '<tr><td style="padding:10px;border-bottom:1px solid #ddd">'
        + html.escape(d)
        + '</td><td style="padding:10px">Продажи отсутствуют</td></tr>'
        for d in departments
    )
    message.add_alternative(
        '<html lang="ru"><body style="font-family:Arial,sans-serif;color:#243047"><main style="max-width:640px;margin:auto"><h2>Контроль продаж</h2><p>Здравствуйте!</p><p>За '
        + day.strftime("%d.%m.%Y")
        + ' отсутствуют продажи по следующим подразделениям:</p><table style="width:100%;border-collapse:collapse"><tr><th align="left">Подразделение</th><th align="left">Статус</th></tr>'
        + rows
        + '</table><p>Пожалуйста, проверьте поступление данных о продажах.</p><p><a href="'
        + html.escape(link, quote=True)
        + '">Открыть контроль продаж</a></p><p>Журнал продаж</p></main></body></html>',
        subtype="html",
    )
    client = (
        smtplib.SMTP_SSL(smtp.host, smtp.port, timeout=30)
        if smtp.security == "SSL"
        else smtplib.SMTP(smtp.host, smtp.port, timeout=30)
    )
    try:
        if smtp.security == "STARTTLS":
            client.starttls()
        if smtp.username:
            client.login(smtp.username, smtp.password)
        return client.send_message(message)
    finally:
        client.quit()


async def notification_tick():
    # Same MAIL/CONTROL locks as the updater, held in committed sessions. No DB
    # transaction/snapshot remains open during SMTP; admin mutations try these locks.
    async with owned_lock(engine, MAIL_LOCK) as acquired:
        if not acquired:
            return
        async with owned_lock(engine, CONTROL_LOCK) as controlled:
            if not controlled:
                return
            async with SessionLocal() as db:
                await db.execute(
                    text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                )
                await limits(db)
                cfg = await settings_row(db)
                await db.execute(
                    update(SalesControlDelivery)
                    .where(SalesControlDelivery.status == "sending")
                    .values(status="uncertain", error_code="sender_interrupted")
                )
                if not cfg.enabled:
                    await db.commit()
                    return
                local = now().astimezone(MOSCOW)
                h, m = map(int, cfg.send_time.split(":"))
                scheduled = datetime.combine(local.date(), time(h, m), MOSCOW)
                if scheduled > local:
                    scheduled -= timedelta(days=1)
                manual = await db.scalar(
                    select(SalesControlDelivery)
                    .where(SalesControlDelivery.manual_retry.is_(True))
                    .order_by(SalesControlDelivery.id)
                    .limit(1)
                )
                if not manual and (
                    cfg.not_before is None or scheduled < cfg.not_before
                ):
                    await db.commit()
                    return
                day = (
                    manual.sales_date
                    if manual
                    else scheduled.date() - timedelta(days=1)
                )
                group = recipient_hash(cfg.recipients)
                if manual and manual.recipients_hash != group:
                    manual.manual_retry = False
                    manual.error_code = "recipients_changed"
                    await db.commit()
                    return
                row = manual or await db.scalar(
                    select(SalesControlDelivery).where(
                        SalesControlDelivery.sales_date == day,
                        SalesControlDelivery.recipients_hash == group,
                    )
                )
                if row and (
                    row.recipients_hash != group
                    or (
                        not row.manual_retry
                        and (
                            row.status in ("sent", "not_required", "uncertain")
                            or row.attempts >= 3
                            or (row.next_attempt and row.next_attempt > now())
                        )
                    )
                ):
                    await db.commit()
                    return
                report = await missing(db, day, day, all_rows=True)
                if not row:
                    row = SalesControlDelivery(
                        settings_id=1,
                        sales_date=day,
                        recipients_hash=group,
                        checked_at=now(),
                        status="failed",
                        attempts=0,
                        missing_count=0,
                        manual_retry=False,
                    )
                    db.add(row)
                row.checked_at = now()
                row.missing_count = report["total"]
                row.manual_retry = False
                if not report["total"]:
                    row.status = "not_required"
                    row.error_code = None
                    await db.commit()
                    return
                smtp = await db.get(SmtpConfig, 1)
                row.attempts += 1
                row.attempted_at = now()
                row.next_attempt = now() + timedelta(minutes=5)
                if not smtp:
                    row.status = "failed"
                    row.error_code = "smtp_not_configured"
                    await db.commit()
                    return
                row.status = "sending"
                await db.commit()
                identity = row.id
                recipients = list(cfg.recipients)
                names = [r["department"] for r in report["items"]]
            try:
                rejected = await run_blocking(
                    send_message, smtp, recipients, day, names
                )
                status, error, retry = (
                    ("uncertain", "partial_delivery", False)
                    if rejected
                    else ("sent", None, False)
                )
            except Exception as exc:
                status, error, retry = mail_failure(exc)
            async with SessionLocal() as db:
                row = await db.get(SalesControlDelivery, identity)
                row.status = status
                row.error_code = error
                if status == "sent":
                    row.sent_at = now()
                elif not retry:
                    row.attempts = max(row.attempts, 3)
                await db.commit()
