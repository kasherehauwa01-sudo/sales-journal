"""Small durable control record shared by the web app and the separate worker."""

from datetime import timezone
from sqlalchemy import select, text
from fastapi import HTTPException
from app.models import CatalogSyncControl, CatalogSyncState
from app.services.catalog_sync import SYNC_LOCK_ID, now

HEARTBEAT_SECONDS = 5
STALE_SECONDS = 20


def worker_ready(control):
    if control is None or control.heartbeat_at is None:
        return False
    stamp = control.heartbeat_at
    if stamp.tzinfo is None:  # SQLite fixtures; PostgreSQL returns aware values.
        stamp = stamp.replace(tzinfo=timezone.utc)
    return (now() - stamp).total_seconds() < STALE_SECONDS


async def control_row(db, *, lock=False):
    query = select(CatalogSyncControl).where(CatalogSyncControl.id == 1)
    if lock:
        query = query.with_for_update()
    control = await db.scalar(query)
    if control is None:
        raise HTTPException(503, "Таблица управления синхронизацией не подготовлена")
    return control


async def start(db, mode="discovery"):
    control = await control_row(db, lock=True)
    if not worker_ready(control):
        raise HTTPException(
            503,
            "Фоновый worker выключен или недоступен. Обратитесь к администратору сервера.",
        )
    if control.requested or control.status in {
        "queued",
        "running",
        "waiting",
        "stopping",
    }:
        raise HTTPException(409, "Синхронизация уже запущена или останавливается")
    # Transaction-scoped probe cannot leave a session lock in the web pool.
    if not await db.scalar(
        text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": SYNC_LOCK_ID}
    ):
        raise HTTPException(409, "Уже выполняется ручная или фоновая синхронизация")
    state = await db.get(CatalogSyncState, 1)
    if mode == "refresh" and (state is None or not state.completed):
        raise HTTPException(409, "Сначала завершите первоначальный проход по продажам")
    if mode != control.mode and not control.refresh_completed:
        raise HTTPException(
            409, "Сначала завершите приостановленную сверку характеристик"
        )
    control.mode = mode
    control.requested = True
    control.status = "queued"
    control.error_code = None
    control.run_started_at = now()
    control.scanned = control.processed = 0
    await db.commit()
    return {"accepted": True}


async def pause(db):
    control = await control_row(db, lock=True)
    control.requested = False
    if control.status in {"queued", "running", "waiting", "stopping"}:
        control.status = "stopping" if worker_ready(control) else "paused"
    await db.commit()
    return {"accepted": True}


async def status(db):
    control = await control_row(db)
    state = await db.get(CatalogSyncState, 1)
    ready = worker_ready(control)
    display_status = control.status
    if not ready and display_status in {"queued", "running", "waiting", "stopping"}:
        display_status = "paused"
    elapsed = None
    if control.run_started_at is not None:
        stamp = control.run_started_at
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        elapsed = max((now() - stamp).total_seconds(), 0)
    return {
        "worker_ready": ready,
        "status": display_status,
        "requested": control.requested,
        "mode": control.mode,
        "error_code": control.error_code,
        "cycle_started_at": (
            control.cycle_started_at
            if state and control.tracked_cycle == state.cycle
            else None
        ),
        "refresh_completed": control.refresh_completed,
        "scanned_this_run": control.scanned,
        "processed_this_run": control.processed,
        "scan_rows_per_second": (
            round(control.scanned / elapsed, 2)
            if display_status == "running" and elapsed and control.scanned
            else None
        ),
        "eta_seconds": None,  # Gaps in IDs: no count or misleading percentage.
        "state": (
            {
                name: getattr(state, name)
                for name in (
                    "cycle",
                    "cursor",
                    "upper_id",
                    "processed",
                    "matched",
                    "not_found",
                    "ambiguous",
                    "invalid",
                    "errors",
                    "completed",
                    "last_success_at",
                    "last_full_success_at",
                    "started_at",
                    "status",
                    "error_code",
                )
            }
            if state
            else None
        ),
    }
