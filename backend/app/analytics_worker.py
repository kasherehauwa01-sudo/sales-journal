"""Manual/standalone SHADOW worker. Disabled by environment AND durable control."""
import argparse
import asyncio
from datetime import date
import json
import logging
import signal
from sqlalchemy import select
from app.config import settings
from app.models import AnalyticsSignalRun
from app.services.analytics_signal_worker import (WORKER_LOCK, last_closed_day, record_failure, run_batch, state)
from app.services.seasonality_worker import advisory, verify, SeasonalityBusy


async def execute(options):
    from app.database import engine, SessionLocal
    shutdown = asyncio.Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(signum, shutdown.set)
    try:
        if options.command == "status":
            async with SessionLocal() as db:
                from app.models import AnalyticsSignalState
                control = await db.get(AnalyticsSignalState, 1)
                print(json.dumps(dict(mode="SHADOW", automation_allowed=False,
                    configuration_enabled=settings.analytics_signals_enabled,
                    enabled=bool(control and control.enabled),
                    active_run_id=control.active_run_id if control else None,
                    published_run_id=control.published_run_id if control else None)))
            return 0
        if options.command in {"enable", "pause"}:
            async with SessionLocal() as db:
                control = await state(db)
                control.enabled = options.command == "enable"
                if options.command == 'pause' and control.active_run_id:
                    active = await db.get(AnalyticsSignalRun, control.active_run_id)
                    if active and active.status == 'RUNNING':active.status = 'PAUSED'
                await db.commit()
            print(json.dumps({"enabled": options.command == "enable", "mode": "SHADOW", "automation_allowed": False}))
            return 0
        if not settings.analytics_signals_enabled:
            print(json.dumps({"status": "DISABLED", "mode": "SHADOW", "automation_allowed": False}))
            return 0
        async with advisory(engine, key=WORKER_LOCK) as leadership:
            while not shutdown.is_set():
                async with SessionLocal() as db:
                    control = await state(db)
                    active = await db.get(AnalyticsSignalRun, control.active_run_id) if control.active_run_id else None
                    target = options.as_of or (active.as_of if active else last_closed_day())
                    await db.commit()
                try:
                    result = await run_batch(SessionLocal, engine, as_of=target,
                        scan_size=options.scan_size, client_size=options.client_size, leadership=leadership)
                    print(json.dumps(result, default=str), flush=True)
                except SeasonalityBusy:
                    result = {"status": "BUSY"}
                except Exception as exc:
                    await record_failure(SessionLocal, type(exc).__name__)
                    logging.exception("Shadow analytics batch failed")
                    return 1
                if result["status"] == "DISABLED":return 0
                if options.command == "run" and result["status"] in {"SUCCEEDED", "SUPERSEDED"}:return 0 if result["status"] == "SUCCEEDED" else 1
                await verify(leadership, WORKER_LOCK)
                delay = options.poll if result["status"] == "SUCCEEDED" else options.pause
                try:await asyncio.wait_for(shutdown.wait(), delay)
                except TimeoutError:pass
        return 0
    except SeasonalityBusy:
        logging.error("Another shadow worker holds leadership")
        return 1
    finally:
        if shutdown.is_set():
            async with SessionLocal() as db:
                control = await state(db)
                if control.active_run_id:
                    active = await db.get(AnalyticsSignalRun, control.active_run_id)
                    if active and active.status == 'RUNNING':active.status = 'PAUSED'
                await db.commit()
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["status", "enable", "pause", "run", "worker"])
    parser.add_argument("--as-of", type=date.fromisoformat)
    parser.add_argument("--scan-size", type=int, default=2000)
    parser.add_argument("--client-size", type=int, default=250)
    parser.add_argument("--pause", type=float, default=.25)
    parser.add_argument("--poll", type=float, default=60)
    options = parser.parse_args()
    if not 1 <= options.scan_size <= 5000 or not 1 <= options.client_size <= 1000 or not .1 <= options.pause <= 60 or not 10 <= options.poll <= 3600:
        parser.error("Invalid worker limits")
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(execute(options)))


if __name__ == "__main__":main()
