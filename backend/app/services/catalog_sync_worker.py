"""One opt-in worker, bounded batches, durable commands and explicit restart resume."""

import asyncio
import logging
import time
from sqlalchemy import func, select, update, text
from pydantic import ValidationError
from app.config import settings
from app.models import CatalogLookup, CatalogSyncState, SaleItem
from app.services.catalog_sync import (
    SYNC_LOCK_ID,
    SyncBusy,
    _state,
    now,
    save_results,
    sync_lock,
    synchronize,
)
from app.services.catalog_sync_control import control_row, HEARTBEAT_SECONDS
from app.services.catalog_lookup_client import LookupError, LookupInput, lookup_products
from app.services.product_analytics_runtime import available_memory
from app.services.product_analytics_cache import invalidate_product_analytics_cache

log = logging.getLogger(__name__)
WORKER_LOCK_ID = SYNC_LOCK_ID + 1


def check_memory():
    available = available_memory()
    if available is None:
        raise LookupError("memory_metrics_unavailable")
    if available < settings.catalog_sync_min_available_mb * 1024 * 1024:
        raise LookupError("memory_headroom", retryable=True)


async def boot(factory):
    # Called ONLY after obtaining worker leadership. Old run commands are never
    # executed automatically after deployment/crash. Checkpoints are untouched.
    async with factory() as db:
        control = await control_row(db, lock=True)
        control.requested = False
        if control.status not in {"completed", "failed"}:
            control.status = "paused"
        control.heartbeat_at = now()
        await db.commit()


async def verify_lock(connection, key):
    held = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' "
            "AND pid=pg_backend_pid() AND classid=0 AND objid=:key "
            "AND objsubid=1 AND granted)"
        ),
        {"key": key},
    )
    await connection.commit()
    if not held:
        raise LookupError("lock_lost")


async def heartbeat(factory, shutdown, leader):
    while not shutdown.is_set():
        await verify_lock(leader, WORKER_LOCK_ID)
        async with factory() as db:
            control = await control_row(db)
            control.heartbeat_at = now()
            await db.commit()
        try:
            await asyncio.wait_for(shutdown.wait(), HEARTBEAT_SECONDS)
        except TimeoutError:
            pass


async def requested(factory, shutdown):
    if shutdown.is_set():
        return False
    async with factory() as db:
        return (await control_row(db)).requested


async def delay(factory, shutdown, seconds):
    deadline = time.monotonic() + seconds
    while await requested(factory, shutdown):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return True
        try:
            await asyncio.wait_for(
                shutdown.wait(), min(remaining, settings.catalog_sync_poll_seconds)
            )
        except TimeoutError:
            pass
    return False


async def finish(factory, status, error_code=None):
    async with factory() as db:
        control = await control_row(db, lock=True)
        control.requested = False
        control.status = status
        control.error_code = error_code
        await db.commit()


async def prepare(factory, mode):
    async with factory() as db:
        state = await _state(db)
        control = await control_row(db)
        if mode == "discovery":
            if state.cycle == 0:
                control.cycle_started_at = now()
                control.tracked_cycle = 1
                await db.commit()
                return True
            if state.completed:
                upper = int(await db.scalar(select(func.max(SaleItem.id))) or 0)
                if upper <= state.upper_id:
                    return False
                # Tail only, preserve cycle and deduplication of known identities.
                state.cursor = max(state.cursor, state.upper_id)
                state.upper_id = upper
                state.completed = False
                await db.commit()
            return True
        if not state.completed:
            raise LookupError("initial_scan_incomplete")
        if control.refresh_completed:
            control.refresh_cursor = None
            control.refresh_upper = await db.scalar(
                select(func.max(CatalogLookup.identity))
            )
            control.refresh_completed = False
            await db.commit()
        return True


async def refresh_batch(factory, batch_size, client, lock_guard=None):
    """Full reconciliation of known identities by bounded PK pages, not updated_at."""
    async with factory() as db:
        control = await control_row(db)
        state = await _state(db)
        query = select(CatalogLookup).where(
            CatalogLookup.identity <= (control.refresh_upper or "")
        )
        if control.refresh_cursor is not None:
            query = query.where(CatalogLookup.identity > control.refresh_cursor)
        rows = (
            await db.scalars(query.order_by(CatalogLookup.identity).limit(batch_size))
        ).all()
        if not rows:
            control.refresh_completed = True
            state.last_full_success_at = now()
            await db.commit()
            return {"scanned": 0, "processed": 0, "completed": True}
        pending, inputs, invalid = [], [], []
        for lookup in rows:
            if lookup.status == "invalid":
                continue  # Immutable invalid sales IDs: no repeated futile HTTP.
            # Keep sales-identity code-first matching, even for previously matched
            # entries: changed codes must not silently change matching semantics.
            raw = {
                name: value
                for name, value in (("code", lookup.code), ("article", lookup.article))
                if value
            }
            try:
                request = LookupInput.model_validate(raw).model_dump(exclude_none=True)
            except ValidationError:
                invalid.append(lookup)
            else:
                pending.append(lookup)
                inputs.append(request)
        await db.commit()  # No idle transaction during HTTP.
        results = await client(inputs) if inputs else []
        if lock_guard is not None:
            await lock_guard()
        await save_results(db, pending, results, state.cycle)
        for lookup in invalid:
            lookup.status, lookup.matched_by, lookup.checked_at = "invalid", None, now()
        for name in ("matched", "not_found", "ambiguous"):
            setattr(
                state,
                name,
                getattr(state, name) + sum(result.status == name for result in results),
            )
        state.processed += len(results) + len(invalid)
        state.invalid += len(invalid)
        if results or invalid:
            await db.execute(
                update(CatalogSyncState)
                .where(CatalogSyncState.id == 1)
                .values(revision=CatalogSyncState.revision + 1)
                .execution_options(synchronize_session=False)
            )
            state.last_success_at = now()
        control.refresh_cursor = rows[-1].identity
        control.refresh_completed = control.refresh_cursor >= (
            control.refresh_upper or ""
        )
        if control.refresh_completed:
            state.last_full_success_at = now()
        await db.commit()  # Characteristics + refresh checkpoint atomically.
        if results or invalid:
            invalidate_product_analytics_cache()
        return {
            "scanned": len(rows),
            "processed": len(results) + len(invalid),
            "completed": control.refresh_completed,
        }


async def run_request(engine, factory, shutdown, client=lookup_products):
    try:
        async with sync_lock(engine) as connection:  # Same exclusion as legacy CLI.

            async def guard():
                await verify_lock(connection, SYNC_LOCK_ID)

            async with factory() as db:
                mode = (await control_row(db)).mode
            if not await requested(factory, shutdown):
                await finish(factory, "paused")
                return
            if not await prepare(factory, mode):
                await finish(factory, "completed")
                return
            failures = 0
            while await requested(factory, shutdown):
                async with factory() as db:
                    control = await control_row(db)
                    if not control.requested:
                        break
                    control.status = "running"
                    control.error_code = None
                    await db.commit()
                entered_sync = False
                try:
                    await guard()
                    check_memory()
                    if mode == "discovery":
                        entered_sync = True
                        result = await synchronize(
                            factory,
                            max_products=settings.catalog_sync_batch_size,
                            max_scan_rows=settings.catalog_sync_batch_size,
                            batch_size=settings.catalog_sync_batch_size,
                            stop_event=shutdown,
                            client=client,
                            lock_guard=guard,
                        )
                        async with factory() as db:
                            result["completed"] = (await _state(db)).completed
                    else:
                        result = await refresh_batch(
                            factory,
                            settings.catalog_sync_batch_size,
                            client,
                            lock_guard=guard,
                        )
                    failures = 0
                except Exception as exc:
                    retryable = isinstance(exc, LookupError) and exc.retryable
                    code = (
                        exc.code
                        if isinstance(exc, LookupError)
                        else (
                            "memory_headroom"
                            if getattr(exc, "status_code", None) == 503
                            else "sync_error"
                        )
                    )
                    retryable = retryable or code == "memory_headroom"
                    failures += 1
                    log.warning(
                        "catalog_sync worker error code=%s attempt=%s", code, failures
                    )
                    # Discovery records API failures itself; refresh/check_memory
                    # failures need an explicit durable technical-error counter.
                    if mode == "refresh" or not entered_sync:
                        async with factory() as db:
                            state = await _state(db)
                            state.errors += 1
                            await db.commit()
                    if not retryable or failures > settings.catalog_sync_retry_limit:
                        await finish(
                            factory,
                            "paused" if code == "memory_headroom" else "failed",
                            code,
                        )
                        return
                    async with factory() as db:
                        control = await control_row(db)
                        control.status = "waiting" if control.requested else "stopping"
                        control.error_code = code
                        await db.commit()
                    if not await delay(
                        factory,
                        shutdown,
                        min(
                            3600,
                            settings.catalog_sync_retry_seconds * 2 ** (failures - 1),
                        ),
                    ):
                        break
                    continue
                async with factory() as db:
                    control = await control_row(db)
                    control.scanned += result["scanned"]
                    control.processed += result["processed"]
                    await db.commit()
                log.info(
                    "catalog_sync worker batch scanned=%s processed=%s completed=%s",
                    result["scanned"],
                    result["processed"],
                    result["completed"],
                )
                if result["completed"]:
                    await finish(factory, "completed")
                    return
                if not await delay(
                    factory, shutdown, settings.catalog_sync_pause_seconds
                ):
                    break
            await finish(factory, "paused")
    except SyncBusy:
        await finish(factory, "paused", "catalog_sync_already_running")
    except asyncio.CancelledError:
        await asyncio.shield(finish(factory, "paused", "cancelled"))
        raise
    except Exception:
        log.error("catalog_sync worker stopped code=sync_error")
        await finish(factory, "failed", "sync_error")


async def serve(engine, factory, shutdown, client=lookup_products):
    async with sync_lock(engine, WORKER_LOCK_ID) as leader:
        await boot(factory)
        pulse = asyncio.create_task(heartbeat(factory, shutdown, leader))
        # A heartbeat failure disables processing, rather than running unmanaged.
        pulse.add_done_callback(
            lambda task: shutdown.set() if not task.cancelled() else None
        )
        try:
            while not shutdown.is_set():
                if await requested(factory, shutdown):
                    await run_request(engine, factory, shutdown, client=client)
                else:
                    async with factory() as db:
                        control = await control_row(db)
                        if control.status == "stopping":
                            control.status = "paused"
                            await db.commit()
                    try:
                        await asyncio.wait_for(
                            shutdown.wait(), settings.catalog_sync_poll_seconds
                        )
                    except TimeoutError:
                        pass
        finally:
            shutdown.set()
            try:
                await pulse
            finally:
                async with factory() as db:
                    control = await control_row(db)
                    control.requested = False
                    control.heartbeat_at = None
                    if control.status not in {"completed", "failed"}:
                        control.status = "paused"
                    await db.commit()
