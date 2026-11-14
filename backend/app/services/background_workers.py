"""Independent bounded lanes in the existing FastAPI lifespan, no new service/cron."""

import asyncio
import logging
import time
from datetime import datetime, timezone

from app.config import settings

log = logging.getLogger(__name__)
# Technical per-process liveness, not a cross-process promise of DB freshness.
observations = {}


async def periodic(name, operation, *, interval=60, timeout=None, wake=None):
    state = observations.setdefault(name, {"state": "idle"})
    failures = 0
    while True:
        started = time.perf_counter()
        state.update(
            state="running",
            started_at=datetime.now(timezone.utc).isoformat(),
            error_code=None,
        )
        try:
            if timeout is None:
                result = await operation()
            else:
                async with asyncio.timeout(timeout):
                    result = await operation()
            failures = 0
            state.update(
                state="idle", last_success=datetime.now(timezone.utc).isoformat()
            )
            if isinstance(result, int):
                state["processed"] = result
            if isinstance(result, dict) and "status" in result:
                state["result"] = result["status"]
        except asyncio.CancelledError:
            state["state"] = "stopped"
            raise
        except Exception as error:
            failures += 1
            state.update(
                state="error",
                error_code=(
                    "timeout"
                    if isinstance(error, TimeoutError)
                    else type(error).__name__
                ),
            )
            log.warning("background lane=%s reason=%s", name, state["error_code"])
        finally:
            state["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
        # No overlapping invocations, no catch-up burst. Failure waits the same
        # bounded interval; an FTP thread is not unsafely abandoned on timeout.
        delay = (
            max(interval, min(300, interval * 2 ** min(failures, 6)))
            if failures
            else interval
        )
        if wake is None or failures:
            await asyncio.sleep(delay)
            if wake is not None:
                wake.clear()
        else:
            try:
                await asyncio.wait_for(wake.wait(), delay)
            except TimeoutError:
                pass
            wake.clear()


async def run():
    from app.database import SessionLocal, engine
    from app.services import ftp_autoload, import_integrity, rfm_snapshot, sales_control
    from app.services.scenarios import scheduler as scenarios

    async def rfm():
        return await rfm_snapshot.refresh_tick(engine)

    from app.services.category_seasonality_snapshot import tick as category_tick
    async def category_seasonality():
        return await category_tick(engine)

    rfm_snapshot.refresh_requested = asyncio.Event()
    # Existing daily FTP23:55 and hourly scenarios retain their own schedule.
    # Non-network metadata jobs can be safely rolled back on cancellation.
    async with asyncio.TaskGroup() as group:
        group.create_task(periodic("category_seasonality", category_seasonality, interval=60))
        group.create_task(periodic("ftp_daily", ftp_autoload.scheduler, interval=60))
        group.create_task(periodic("scenarios", scenarios, interval=60))
        group.create_task(
            periodic("ftp_deadline", import_integrity.check_deadline_cycle)
        )
        group.create_task(
            periodic("sales_notifications", sales_control.notification_tick)
        )
        group.create_task(
            periodic(
                "rfm",
                rfm,
                timeout=settings.rfm_refresh_timeout_seconds + 30,
                interval=settings.rfm_refresh_poll_seconds,
                wake=rfm_snapshot.refresh_requested,
            )
        )
