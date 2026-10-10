"""Opt-in standalone worker. Never imported/started by FastAPI lifespan."""

import asyncio
import logging
import signal
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.config import settings
from app.services.catalog_sync_worker import serve


async def execute():
    # Two dedicated advisory-lock sessions plus batch + heartbeat; no overflow.
    engine = create_async_engine(
        settings.database_url,
        pool_size=4,
        max_overflow=0,
        pool_pre_ping=True,
        pool_timeout=settings.catalog_sync_db_timeout_seconds,
        connect_args={
            "timeout": settings.catalog_sync_db_timeout_seconds,
            "server_settings": {
                "statement_timeout": str(
                    int(settings.catalog_sync_db_timeout_seconds * 1000)
                ),
                "lock_timeout": str(
                    int(settings.catalog_sync_db_lock_timeout_seconds * 1000)
                ),
            },
        },
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    shutdown = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(signum, shutdown.set)
    try:
        await serve(engine, factory, shutdown)
        return 0
    except Exception:
        logging.error("catalog_sync worker exited code=worker_unavailable")
        return 1
    finally:
        await engine.dispose()


def main():
    logging.basicConfig(level=settings.log_level)
    raise SystemExit(asyncio.run(execute()))


if __name__ == "__main__":
    main()
