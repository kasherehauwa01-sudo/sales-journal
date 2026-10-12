"""Explicit operator CLI; no task starts in FastAPI lifespan or on import."""

import argparse
import asyncio
from datetime import datetime
import json
import logging
import signal
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from app.config import settings
from app.services.seasonality_storage import state
from app.services.seasonality_worker import (
    advisory,
    run_batch,
    SeasonalityBusy,
    WORKER_LOCK,
    verify,
)

log = logging.getLogger(__name__)


def today():
    return datetime.now(ZoneInfo(settings.autoload_timezone)).date()


async def status(factory):
    async with factory() as db:
        current = await state(db)
        await db.commit()
        return {
            column.name: getattr(current, column.name)
            for column in current.__table__.columns
        }


async def run(
    factory,
    engine,
    *,
    max_batches=20,
    scan_size=1000,
    profile_size=100,
    pause=1,
    stop=None
):
    stop = stop or asyncio.Event()
    for _ in range(max_batches):
        if stop.is_set() or (await status(factory))["stop_requested"]:
            break
        try:
            result = await run_batch(
                factory, engine, today(), scan_size=scan_size, profile_size=profile_size
            )
        except asyncio.CancelledError:
            raise
        except (SeasonalityBusy, HTTPException) as exc:
            async with factory() as db:
                current = await state(db)
                current.error_code = (
                    "resource_busy"
                    if isinstance(exc, SeasonalityBusy)
                    else "memory_or_queue"
                )
                if not isinstance(exc, SeasonalityBusy):
                    current.failure_count += 1
                    if current.failure_count >= 3:
                        current.enabled = False
                await db.commit()
            break
        except Exception:
            async with factory() as db:
                current = await state(db)
                current.error_code = "calculation_error"
                current.failure_count += 1
                if current.failure_count >= 3:
                    current.enabled = False
                await db.commit()
            log.error("seasonality failure code=calculation_error")
            break
        if result["phase"] == "idle":
            break
        try:
            await asyncio.wait_for(stop.wait(), timeout=pause)
        except TimeoutError:
            pass
    return await status(factory)


async def worker(factory, engine, *, stop, options):
    # One worker holds leadership. Each batch uses independent resource/source
    # locks, releasing them during pauses so reports and imports can proceed.
    async with advisory(engine, key=WORKER_LOCK) as leadership:
        while not stop.is_set():
            await verify(leadership, WORKER_LOCK)
            current = await status(factory)
            if current["enabled"] and (
                current["phase"] != "idle"
                or current["last_checked"] != today()
                or current["source_revision"] != current["job_revision"]
                or current["rebuild_required"]
            ):
                for _ in range(options.max_batches):
                    if stop.is_set():
                        break
                    control = await status(factory)
                    if not control["enabled"]:
                        break
                    result = await run(
                        factory,
                        engine,
                        max_batches=1,
                        scan_size=options.scan_size,
                        profile_size=options.profile_size,
                        pause=0,
                        stop=stop,
                    )
                    if result["error_code"] or result["phase"] == "idle":
                        break
                    try:
                        await asyncio.wait_for(stop.wait(), timeout=options.pause)
                    except TimeoutError:
                        pass
            try:
                await asyncio.wait_for(stop.wait(), timeout=options.poll)
            except TimeoutError:
                pass


async def execute(options):
    from app.database import SessionLocal, engine

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        if options.command in ("enable", "pause", "rebuild"):
            async with SessionLocal() as db:
                current = await state(db, lock=True)
                if options.command == "rebuild":
                    current.rebuild_required = True
                    current.source_revision += 1
                else:
                    current.enabled = options.command == "enable"
                    current.stop_requested = options.command == "pause"
                    current.failure_count = 0
                await db.commit()
        elif options.command in ("run", "scheduled"):
            async with advisory(engine, key=WORKER_LOCK):
                async with SessionLocal() as db:
                    current = await state(db, lock=True)
                    if options.command == "run":
                        current.stop_requested = False
                    elif not current.enabled or current.stop_requested:
                        await db.commit()
                        print(
                            json.dumps(
                                await status(SessionLocal),
                                default=str,
                                ensure_ascii=False,
                            )
                        )
                        return 0
                    await db.commit()
                result = await run(
                    SessionLocal,
                    engine,
                    max_batches=options.max_batches,
                    scan_size=options.scan_size,
                    profile_size=options.profile_size,
                    pause=options.pause,
                    stop=stop,
                )
                if result["error_code"]:
                    print(json.dumps(result, default=str, ensure_ascii=False))
                    return 1
        elif options.command == "worker":
            await worker(SessionLocal, engine, stop=stop, options=options)
        print(json.dumps(await status(SessionLocal), default=str, ensure_ascii=False))
        return 0
    except SeasonalityBusy:
        log.error("seasonality failure code=already_running")
        return 1
    except Exception:
        log.error("seasonality failure code=command_error")
        return 1
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=["run", "scheduled", "status", "enable", "pause", "rebuild", "worker"],
    )
    parser.add_argument("--max-batches", type=int, default=20)
    parser.add_argument("--scan-size", type=int, default=1000)
    parser.add_argument("--profile-size", type=int, default=100)
    parser.add_argument("--pause", type=float, default=1)
    parser.add_argument("--poll", type=float, default=60)
    options = parser.parse_args()
    if (
        not 1 <= options.max_batches <= 1000
        or not 1 <= options.scan_size <= 2000
        or not 1 <= options.profile_size <= 100
        or not 0.1 <= options.pause <= 3600
        or not 10 <= options.poll <= 3600
    ):
        parser.error("invalid limits")
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(execute(options)))


if __name__ == "__main__":
    main()
