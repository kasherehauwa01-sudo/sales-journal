"""All control endpoints use the existing protected settings session."""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
import hmac
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, func, or_, cast, String
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import noload
from app.database import get_db
from app.config import settings
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
from app.services.settings_auth import require_settings_admin
from app.services.email_recipients import parse_recipient_emails
from app.services.import_control import (
    config,
    lock,
    audit,
    save_rule,
    check_date,
    now,
    validate_template,
    OPEN_STATES,
)

router = APIRouter(
    prefix="/import-control",
    tags=["Контроль импорта"],
    dependencies=[Depends(require_settings_admin)],
)


def actor(request):
    if (
        settings.mtls_proxy_secret
        and hmac.compare_digest(
            request.headers.get("x-sales-proxy-secret", ""), settings.mtls_proxy_secret
        )
        and request.headers.get("x-sales-mtls-verify") == "SUCCESS"
    ):
        return (
            "settings-admin:certificate:"
            + request.headers.get("x-sales-mtls-serial", "unknown")[:128]
        )
    return "settings-admin:shared-session"


def output(row):
    return {column.name: getattr(row, column.name) for column in row.__table__.columns}


class SettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool = False
    automatic: bool = False
    email_enabled: bool = False
    start_date: date | None = None
    day_offset: int | None = Field(None, ge=0, le=7)
    marker_template: str | None = Field(None, max_length=512)
    recipients: str = Field("", max_length=10000)
    approve: bool = False


@router.get("/settings")
async def get_settings(db: AsyncSession = Depends(get_db)):
    c = await config(db)
    await db.commit()
    return {
        k: getattr(c, k)
        for k in (
            "enabled",
            "automatic",
            "email_enabled",
            "start_date",
            "day_offset",
            "marker_template",
            "recipients",
            "revision",
        )
    }


@router.put("/settings")
async def settings_put(
    data: SettingsIn, request: Request, db: AsyncSession = Depends(get_db)
):
    await lock(db)
    c = await config(db)
    if data.enabled and (not data.approve or not data.start_date):
        raise HTTPException(
            422,
            "Для включения нужны утверждение и дата начала контроля",
        )
    if data.enabled and not await db.scalar(
        select(func.count()).select_from(ImportRule)
    ):
        raise HTTPException(422, "Сначала утвердите справочник подразделений")
    if (
        c.start_date
        and data.start_date != c.start_date
        and await db.scalar(select(ImportIssue.id).limit(1))
    ):
        raise HTTPException(
            422, "Дата начала контроля зафиксирована историей; её нельзя переносить"
        )
    if data.automatic and not data.enabled:
        raise HTTPException(422, "Сначала включите контроль")
    try:
        marker = None  # Deprecated column retained for schema/API compatibility.
        recipients = (
            "\n".join(parse_recipient_emails(data.recipients))
            if data.recipients.strip()
            else ""
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    if data.email_enabled and (
        not data.enabled or not data.automatic or not recipients
    ):
        raise HTTPException(422, "Для email нужны автоматический контроль и получатели")
    before = output(c)
    for key, value in data.model_dump(
        exclude={"approve", "marker_template", "recipients"}
    ).items():
        setattr(c, key, value)
    c.marker_template = marker
    c.recipients = recipients
    c.revision += 1
    await audit(
        db,
        "settings",
        1,
        actor(request),
        {"before": before, "after": output(c)},
        data.start_date,
    )
    await db.commit()
    await db.refresh(c)
    return output(c)


class RuleIn(BaseModel):
    start_date: date
    end_date: date | None = None
    filename_template: str = Field(min_length=1, max_length=512)
    date_format: str = "dmy"
    name: str = Field(min_length=1, max_length=255)
    active: bool = True
    controlled: bool = True
    model_config = ConfigDict(extra="forbid")


class DepartmentIn(RuleIn):
    code: str = Field(min_length=1, max_length=128)


@router.get("/departments")
async def departments(db: AsyncSession = Depends(get_db)):
    rows = (
        await db.scalars(
            select(ImportDepartment).order_by(ImportDepartment.id).limit(1001)
        )
    ).all()
    rules = (
        await db.scalars(
            select(ImportRule)
            .order_by(ImportRule.department_id, ImportRule.start_date)
            .limit(10001)
        )
    ).all()
    if len(rows) > 1000 or len(rules) > 10000:
        raise HTTPException(503, "Превышен безопасный размер справочника")
    return [
        {
            **output(row),
            "rules": [output(r) for r in rules if r.department_id == row.id],
        }
        for row in rows
    ]


@router.post("/departments", status_code=201)
async def add_department(
    data: DepartmentIn, request: Request, db: AsyncSession = Depends(get_db)
):
    await lock(db)
    if not data.code.strip() or not data.name.strip():
        raise HTTPException(422, "Код и название не могут быть пустыми")
    if await db.scalar(
        select(ImportDepartment.id).where(ImportDepartment.code == data.code.strip())
    ):
        raise HTTPException(409, "Код подразделения уже существует")
    row = ImportDepartment(code=data.code.strip(), name=data.name)
    db.add(row)
    await db.flush()
    try:
        await save_rule(
            db,
            row,
            data.start_date,
            data.end_date,
            data.filename_template,
            data.date_format,
            data.name,
            data.active,
            data.controlled,
            actor(request),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    await audit(
        db,
        "department",
        row.id,
        actor(request),
        {"created": {"code": row.code, "name": row.name}},
        data.start_date,
    )
    await db.commit()
    return output(row)


@router.post("/departments/{identity}/rules", status_code=201)
async def edit_department(
    identity: int, data: RuleIn, request: Request, db: AsyncSession = Depends(get_db)
):
    await lock(db)
    row = await db.get(ImportDepartment, identity)
    if not row:
        raise HTTPException(404, "Подразделение не найдено")
    if not data.name.strip():
        raise HTTPException(422, "Название не может быть пустым")
    before = row.name
    try:
        rule = await save_rule(
            db,
            row,
            data.start_date,
            data.end_date,
            data.filename_template,
            data.date_format,
            data.name,
            data.active,
            data.controlled,
            actor(request),
        )
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    row.name = data.name
    await audit(
        db,
        "department",
        row.id,
        actor(request),
        {"name": {"before": before, "after": row.name}},
        data.start_date,
    )
    await db.commit()
    await db.refresh(rule)
    return output(rule)


@router.get("/audit")
async def history(
    entity: str | None = None,
    entity_id: str | None = None,
    department_id: int | None = None,
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if department_id:
        conditions.append(
            or_(
                (ImportControlAudit.entity == "department")
                & (ImportControlAudit.entity_id == str(department_id)),
                (ImportControlAudit.entity == "rule")
                & (
                    ImportControlAudit.entity_id.in_(
                        select(cast(ImportRule.id, String)).where(
                            ImportRule.department_id == department_id
                        )
                    )
                ),
            )
        )
    if entity:
        conditions.append(ImportControlAudit.entity == entity)
    if entity_id:
        conditions.append(ImportControlAudit.entity_id == entity_id)
    total = await db.scalar(
        select(func.count()).select_from(ImportControlAudit).where(*conditions)
    )
    rows = (
        await db.scalars(
            select(ImportControlAudit)
            .where(*conditions)
            .order_by(ImportControlAudit.id.desc())
            .offset((page - 1) * 50)
            .limit(50)
        )
    ).all()
    return {"items": [output(row) for row in rows], "total": total, "page": page}


@router.get("/candidates")
async def candidates(db: AsyncSession = Depends(get_db)):
    # Bounded metadata proposal, no Sales scan and no inference of real stores/dates.
    rows = (
        await db.scalars(
            select(ImportBatch)
            .options(noload(ImportBatch.errors))
            .order_by(ImportBatch.id.desc())
            .limit(200)
        )
    ).all()
    return {
        "approved": False,
        "items": [
            {
                "filename": r.filename,
                "period_start": r.period_start,
                "period_end": r.period_end,
                "status": r.status,
                "import_id": r.id,
            }
            for r in rows
        ],
    }


@router.post("/check-open")
async def check_open(request: Request, db: AsyncSession = Depends(get_db)):
    dates = (
        await db.scalars(
            select(ImportIssue.sales_date)
            .where(ImportIssue.status.in_(OPEN_STATES))
            .group_by(ImportIssue.sales_date)
            .order_by(func.min(ImportIssue.checked_at))
            .limit(31)
        )
    ).all()
    results = []
    for day in dates:
        results.append(await check_date(db, day, actor=actor(request)))
        await db.commit()
    return {"checked_dates": len(results), "items": results}


class CheckIn(BaseModel):
    date_from: date
    date_to: date


@router.post("/check")
async def check(data: CheckIn, request: Request, db: AsyncSession = Depends(get_db)):
    if data.date_to < data.date_from or (data.date_to - data.date_from).days >= 31:
        raise HTTPException(422, "Проверка ограничена 31 днём за запрос")
    result = []
    for delta in range((data.date_to - data.date_from).days + 1):
        result.append(
            await check_date(
                db, data.date_from + timedelta(days=delta), actor=actor(request)
            )
        )
        await db.commit()
    return result


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    reason: str = Field(min_length=5, max_length=1000)


@router.post("/cycles/{day}/confirm")
async def confirm(
    day: date, data: Confirmation, request: Request, db: AsyncSession = Depends(get_db)
):
    import uuid

    await lock(db)
    c = await config(db)
    if (
        not c.enabled
        or not c.start_date
        or day < c.start_date
        or day > datetime.now(ZoneInfo(settings.autoload_timezone)).date()
    ):
        raise HTTPException(
            422, "Подтвердить можно только наступившую дату включённого контроля"
        )
    if await db.scalar(
        select(ImportCycle.id).where(
            ImportCycle.sales_date == day, ImportCycle.status == "running"
        )
    ):
        raise HTTPException(409, "Цикл ещё работает")
    cycle = ImportCycle(
        id=str(uuid.uuid4()),
        sales_date=day,
        status="finished",
        source_confirmed=True,
        config_revision=c.revision,
        finished_at=now(),
    )
    db.add(cycle)
    await db.flush()
    await audit(
        db,
        "cycle",
        cycle.id,
        actor(request),
        {"confirmed": True, "reason": data.reason},
        day,
    )
    result = await check_date(db, day, confirmed=True, actor=actor(request))
    await db.commit()
    return result


@router.get("/status")
async def status(db: AsyncSession = Depends(get_db)):
    c = await config(db)
    latest = await db.scalar(
        select(ImportDayCheck)
        .where(
            ImportDayCheck.sales_date
            <= datetime.now(ZoneInfo(settings.autoload_timezone)).date(),
            ImportDayCheck.sales_date >= (c.start_date or date.min),
        )
        .order_by(ImportDayCheck.sales_date.desc())
        .limit(1)
    )
    counts = (
        await db.execute(
            select(ImportIssue.status, func.count()).group_by(ImportIssue.status)
        )
    ).all()
    cycle = await db.scalar(
        select(ImportCycle).order_by(ImportCycle.started_at.desc()).limit(1)
    )
    result = output(latest) if latest else None
    if (
        c.enabled
        and cycle
        and cycle.sales_date
        and c.start_date
        and cycle.sales_date >= c.start_date
    ):
        if not latest or (
            cycle.sales_date >= latest.sales_date
            and latest.checked_at < (cycle.finished_at or cycle.started_at)
        ):
            result = {
                "sales_date": cycle.sales_date,
                "status": (
                    "unavailable"
                    if cycle.status in ("failed", "interrupted")
                    else "waiting"
                ),
                "expected": None,
                "successful": None,
                "missing": None,
                "errors": None,
                "checked_at": None,
                "error_code": cycle.error_code or "cycle_not_checked",
            }
    return {
        "enabled": c.enabled,
        "latest": result,
        "open_counts": dict(counts),
        "last_cycle": output(cycle) if cycle else None,
    }


@router.get("/issues")
async def issues(
    date_from: date | None = None,
    date_to: date | None = None,
    department_id: int | None = None,
    status: str = "open",
    direction: str = "desc",
    page: int = Query(1, ge=1),
    db: AsyncSession = Depends(get_db),
):
    conditions = []
    if date_from:
        conditions.append(ImportIssue.sales_date >= date_from)
    if date_to:
        conditions.append(ImportIssue.sales_date <= date_to)
    if department_id:
        conditions.append(ImportIssue.department_id == department_id)
    if status == "open":
        conditions.append(ImportIssue.status.in_(OPEN_STATES))
    elif status != "all":
        conditions.append(ImportIssue.status == status)
    total = await db.scalar(
        select(func.count()).select_from(ImportIssue).where(*conditions)
    )
    order = (
        ImportIssue.sales_date.asc()
        if direction == "asc"
        else ImportIssue.sales_date.desc()
    )
    rows = (
        await db.scalars(
            select(ImportIssue)
            .where(*conditions)
            .order_by(order, ImportIssue.id)
            .offset((page - 1) * 50)
            .limit(50)
        )
    ).all()
    return {
        "items": [output(row) for row in rows],
        "total": total,
        "page": page,
        "last_checked_at": max((r.checked_at for r in rows), default=None),
    }


@router.post("/issues/{identity}/close")
async def close(
    identity: int,
    data: Confirmation,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await lock(db)
    row = await db.get(ImportIssue, identity)
    if not row:
        raise HTTPException(404, "Пропуск не найден")
    before = row.status
    row.status = "closed"
    row.closure_reason = data.reason
    row.closure_method = "administrator"
    row.resolved_at = now()
    await audit(
        db,
        "issue",
        identity,
        actor(request),
        {"before": before, "after": "closed", "reason": data.reason},
        row.sales_date,
    )
    await db.commit()
    return output(row)


@router.get("/issues/{identity}/details")
async def issue_details(identity: int, db: AsyncSession = Depends(get_db)):
    row = await db.get(ImportIssue, identity)
    if not row:
        raise HTTPException(404, "Пропуск не найден")
    rows = (
        await db.execute(
            select(ImportReceipt, ImportBatch.error_text, ImportBatch.error_rows)
            .join(ImportBatch, ImportBatch.id == ImportReceipt.import_id)
            .where(
                ImportReceipt.filename.in_(
                    (row.expected_filename, "EROOR_" + row.expected_filename)
                )
            )
            .order_by(ImportReceipt.id.desc())
            .limit(20)
        )
    ).all()
    return {
        "issue": output(row),
        "receipts": [
            {**output(r), "error_text": message, "error_rows": errors}
            for r, message, errors in rows
        ],
    }


@router.get("/notices")
async def notices(page: int = Query(1, ge=1), db: AsyncSession = Depends(get_db)):
    rows = (
        await db.scalars(
            select(ImportNotice)
            .order_by(ImportNotice.id.desc())
            .offset((page - 1) * 50)
            .limit(50)
        )
    ).all()
    return {
        "items": [
            {key: value for key, value in output(row).items() if key != "body"}
            for row in rows
        ],
        "page": page,
        "total": await db.scalar(select(func.count()).select_from(ImportNotice)),
    }


@router.post("/notices/{identity}/retry")
async def retry(
    identity: int,
    data: Confirmation,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    await lock(db)
    row = await db.get(ImportNotice, identity)
    if not row or row.status not in ("failed", "uncertain"):
        raise HTTPException(409, "Уведомление нельзя повторить")
    before = row.status
    row.status = "pending"
    row.attempts = 0
    row.next_attempt = now()
    await audit(
        db,
        "notice",
        identity,
        actor(request),
        {"before": before, "after": "pending", "reason": data.reason},
        row.sales_date,
    )
    await db.commit()
    return {"queued": True}
