"""Receipt metadata/reimport safety only; no expected-file completeness checks."""

import json, hashlib, unicodedata
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from app.models.import_control import ImportControlConfig, ImportReceipt, ImportCycle

CONTROL_LOCK = 734190270
FTP_LOCK = 734190271
MAIL_LOCK = 734190272


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
