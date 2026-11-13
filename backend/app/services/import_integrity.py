"""Shared import integrity hooks. Legacy config/rules are read only for reimport safety."""

import asyncio, hashlib, logging, smtplib, socket, uuid
from contextlib import asynccontextmanager, AsyncExitStack
from datetime import timedelta
from sqlalchemy import select, text, update, or_, func
from sqlalchemy.orm import noload
from app.database import engine, SessionLocal
from app.models import ImportBatch, FtpConfig
from app.models.import_control import ImportCycle, ImportReceipt
from app.services.import_control import (
    FTP_LOCK,
    MAIL_LOCK,
    config,
    now,
    canonical_filename,
    record_receipt,
)
from app.services.import_deadline import deadline, cycle_sales_day, due_sales_day

log = logging.getLogger(__name__)


@asynccontextmanager
async def owned_lock(selected_engine, key):
    # Reuse the existing shielded cleanup: repeated cancellation must not leave
    # a session lock on a connection returned to the pool.
    from app.services.seasonality_worker import advisory, SeasonalityBusy

    async with AsyncExitStack() as stack:
        try:
            await stack.enter_async_context(advisory(selected_engine, key=key))
        except SeasonalityBusy:
            yield False
            return
        yield True


async def begin_cycle(sales_day=None):
    async with SessionLocal() as db:
        c = await config(db)
        # Previous owner cannot still be running: caller holds the FTP session lock.
        await db.execute(
            update(ImportCycle)
            .where(ImportCycle.status == "running")
            .values(
                status="interrupted", finished_at=now(), error_code="worker_interrupted"
            )
        )
        cycle = ImportCycle(
            id=str(uuid.uuid4()),
            sales_date=sales_day or cycle_sales_day(now()),
            status="running",
            source_confirmed=False,
            config_revision=c.revision,
        )
        db.add(cycle)
        await db.commit()
        return cycle.id, cycle.sales_date, None


async def finish_cycle(identity, marker_seen, error=None):
    async with SessionLocal() as db:
        cycle = await db.get(ImportCycle, identity)
        if not cycle:
            return
        cycle.status = "failed" if error else "finished"
        cycle.finished_at = now()
        cycle.error_code = error
        cycle.source_confirmed = bool(
            not error
            and cycle.sales_date
            and cycle.started_at >= deadline(cycle.sales_date)
        )
        day = cycle.sales_date
        await db.commit()


async def receipt_before(batch_id, filename=None, cycle_id=None):
    from pathlib import Path

    async with SessionLocal() as db:
        batch = await db.get(
            ImportBatch, batch_id, options=[noload(ImportBatch.errors)]
        )
        if not batch:
            return
        existing = await db.scalar(
            select(ImportReceipt).where(ImportReceipt.import_id == batch_id)
        )
        if existing and existing.sha256:
            return

        source_path = batch.stored_path

    def digest():
        result = hashlib.sha256()
        with Path(source_path).open("rb") as source:
            for chunk in iter(lambda: source.read(65536), b""):
                result.update(chunk)
        return result.hexdigest()

    checksum = await asyncio.to_thread(digest)
    async with SessionLocal() as db:
        batch = await db.get(
            ImportBatch, batch_id, options=[noload(ImportBatch.errors)]
        )
        if not batch:
            return
        await record_receipt(db, batch, cycle_id, filename, checksum)
        await db.commit()


async def receipt_after(batch_id, departments):
    async with SessionLocal() as db:
        batch = await db.get(
            ImportBatch, batch_id, options=[noload(ImportBatch.errors)]
        )
        if batch:
            await record_receipt(db, batch, departments=departments)
            await db.commit()


async def safe_hook(function, *args):
    try:
        return await function(*args)
    except asyncio.CancelledError:
        raise
    except Exception:
        # No source rows, exception text, credentials or recipients in logs.
        log.warning("import_control metadata_hook_failed hook=%s", function.__name__)
        return None


def mail_failure(error):
    # SMTP acknowledgement can be lost. Never blind-retry a possibly accepted email.
    if isinstance(error, smtplib.SMTPRecipientsRefused):
        codes = [value[0] for value in error.recipients.values()]
        return (
            ("failed", "smtp_temporary", True)
            if codes and all(400 <= x < 500 for x in codes)
            else ("failed", "smtp_rejected", False)
        )
    if isinstance(error, smtplib.SMTPResponseException):
        return (
            ("failed", "smtp_temporary", True)
            if 400 <= error.smtp_code < 500
            else ("failed", "smtp_rejected", False)
        )
    if isinstance(error, socket.gaierror):
        return "failed", "smtp_dns", True
    return "uncertain", "smtp_delivery_unknown", False


async def check_deadline_cycle():
    async with SessionLocal() as db:
        c = await config(db)
        if not c.enabled or not c.automatic or not c.start_date:
            return
        ftp = await db.get(FtpConfig, 1)
        if not ftp or not ftp.enabled:
            return
        day = due_sales_day(now())
        if day < c.start_date:
            return
        done = await db.scalar(
            select(ImportCycle.id)
            .where(
                ImportCycle.sales_date == day,
                ImportCycle.started_at >= deadline(day),
                ImportCycle.status.in_(("finished", "failed")),
            )
            .limit(1)
        )
        if done:
            return
    # Normal FTP import, with its existing retry policy and cross-process lock.
    from app.services.ftp_autoload import run_autoload

    await run_autoload(day)


async def revision_guard(batch_id):
    from app.models.import_control import ImportRule
    import re

    async with SessionLocal() as db:
        c = await config(db)
        if not c.enabled:
            return None
        batch = await db.get(
            ImportBatch, batch_id, options=[noload(ImportBatch.errors)]
        )
        receipt = await db.scalar(
            select(ImportReceipt).where(ImportReceipt.import_id == batch_id)
        )
        if not receipt or not receipt.sha256:
            return "source_identity_unavailable"
        rules = (
            await db.scalars(
                select(ImportRule)
                .where(ImportRule.active.is_(True), ImportRule.controlled.is_(True))
                .limit(1001)
            )
        ).all()
        if len(rules) > 1000:
            return "source_identity_unavailable"
        matching = []
        for rule in rules:
            pattern = re.escape(rule.filename_template).replace(
                re.escape("{date}"),
                (
                    r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}"
                    if rule.date_format == "dmy"
                    else r"[0-9]{4}-[0-9]{2}-[0-9]{2}"
                ),
            )
            if re.fullmatch(pattern, receipt.filename.removeprefix("EROOR_")):
                matching.append(rule)
        if not matching:
            return None
        legacy = (
            select(ImportBatch.id)
            .outerjoin(ImportReceipt, ImportReceipt.import_id == ImportBatch.id)
            .where(
                ImportBatch.filename == batch.filename,
                ImportBatch.id != batch.id,
                ImportBatch.added_rows > 0,
                ImportReceipt.id.is_(None),
            )
        )
        if batch.declared_period_start and batch.declared_period_end:
            start = func.coalesce(
                ImportBatch.declared_period_start, ImportBatch.period_start
            )
            end = func.coalesce(ImportBatch.declared_period_end, ImportBatch.period_end)
            legacy = legacy.where(
                or_(
                    start.is_(None),
                    end.is_(None),
                    (start <= batch.declared_period_end)
                    & (end >= batch.declared_period_start),
                )
            )
        if await db.scalar(legacy.limit(1)):
            return "legacy_source_requires_review"
        query = (
            select(ImportReceipt.id)
            .join(ImportBatch, ImportBatch.id == ImportReceipt.import_id)
            .where(
                ImportReceipt.filename.in_(
                    (receipt.filename, receipt.filename.removeprefix("EROOR_"))
                ),
                or_(ImportReceipt.status == "success", ImportBatch.added_rows > 0),
                or_(
                    ImportReceipt.sha256.is_(None),
                    ImportReceipt.sha256 != receipt.sha256,
                ),
            )
        )
        if batch.declared_period_start and batch.declared_period_end:
            query = query.where(
                or_(
                    ImportReceipt.period_start.is_(None),
                    ImportReceipt.period_end.is_(None),
                    (ImportReceipt.period_start <= batch.declared_period_end)
                    & (ImportReceipt.period_end >= batch.declared_period_start),
                ),
            )
        elif not all("{date}" in rule.filename_template for rule in matching):
            # A changed constant XLS filename with no verified source period is
            # not proof of a new day. Quarantine rather than double-count sales.
            if await db.scalar(query.limit(1)):
                return "source_period_requires_review"
            return None
        if await db.scalar(query.where(ImportReceipt.sha256.is_(None)).limit(1)):
            return "legacy_source_requires_review"
        if await db.scalar(query.limit(1)):
            return "corrected_source_requires_review"
        return None


async def record_download_error(batch_id, filename, cycle_id):
    async with SessionLocal() as db:
        batch = await db.get(
            ImportBatch, batch_id, options=[noload(ImportBatch.errors)]
        )
        if batch:
            await record_receipt(db, batch, cycle_id, filename)
            await db.commit()


@asynccontextmanager
async def source_owner(batch_id):
    """Serialize monitored source identities across manual uploads and FTP workers."""
    async with SessionLocal() as db:
        c = await config(db)
        batch = await db.get(
            ImportBatch, batch_id, options=[noload(ImportBatch.errors)]
        )
        receipt = await db.scalar(
            select(ImportReceipt).where(ImportReceipt.import_id == batch_id)
        )
        name = canonical_filename(
            (receipt.filename if receipt else batch.filename).removeprefix("EROOR_")
        )
        enabled = c.enabled
    if not enabled:
        yield True
        return
    key = int.from_bytes(
        hashlib.sha256(("import-file:" + name).encode()).digest()[:8],
        "big",
        signed=True,
    )
    async with owned_lock(engine, key) as acquired:
        yield acquired
