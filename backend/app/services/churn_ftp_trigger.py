"""Eligibility to schedule from a locally observed FTP import, not completeness."""

from datetime import date

# This intentionally ignores source_confirmed, marker files and external attestations.
# A matching successful receipt is sufficient once its FTP cycle has finished.
FTP_CYCLE_READY = """
SELECT EXISTS (
    SELECT 1
    FROM import_control_cycles AS cycle
    JOIN import_control_receipts AS receipt ON receipt.cycle_id = cycle.id
    JOIN import_batches AS batch ON batch.id = receipt.import_id
    WHERE cycle.sales_date = :sales_day
      AND cycle.status = 'finished'
      AND cycle.finished_at IS NOT NULL
      AND receipt.period_end = :sales_day
      AND receipt.status = 'success'
      AND batch.status = 'completed'
      AND batch.error_rows = 0
) AND NOT EXISTS (
    SELECT 1
    FROM import_control_cycles AS active_cycle
    WHERE active_cycle.sales_date = :sales_day
      AND active_cycle.status = 'running'
)
"""


async def ftp_cycle_ready(db, sales_day: date) -> bool:
    """Check the requested sales day, not the most recent FTP poll."""
    from sqlalchemy import text

    return bool(await db.scalar(text(FTP_CYCLE_READY), {"sales_day": sales_day}))
