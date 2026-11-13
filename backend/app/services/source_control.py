"""Shared sales/catalog ownership. Stable IDs also protect reviewed updaters."""

import asyncio
from contextlib import asynccontextmanager
from sqlalchemy import text

SOURCE_LOCK = 734190220
WORKER_LOCK = 734190221
CATALOG_LOCK = 734190117


class ResourceBusy(RuntimeError):
    pass


@asynccontextmanager
async def advisory(engine, *, key=SOURCE_LOCK, shared=False, wait=False):
    if engine.dialect.name != "postgresql":
        yield None  # Isolated SQLite tests only.
        return
    async with engine.connect() as connection:
        function = ("pg_advisory_lock" if wait else "pg_try_advisory_lock") + (
            "_shared" if shared else ""
        )
        acquired = await connection.scalar(
            text(f"SELECT {function}(:key)"), {"key": key}
        )
        if not wait and not acquired:
            raise ResourceBusy("resource_busy")
        await connection.commit()
        try:
            yield connection
        finally:

            async def release():
                try:
                    await connection.execute(
                        text(
                            "SELECT pg_advisory_unlock"
                            + ("_shared" if shared else "")
                            + "(:key)"
                        ),
                        {"key": key},
                    )
                    await connection.commit()
                except BaseException:
                    await connection.invalidate()
                    raise

            cleanup = asyncio.create_task(release())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError


async def verify(connection, key):
    if connection is None:
        return
    owned = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND classid=0 AND objid=:key AND objsubid=1 AND granted)"
        ),
        {"key": key},
    )
    if not owned:
        raise ResourceBusy("lost_lock")
    await connection.commit()
