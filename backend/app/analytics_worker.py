"""Manual/standalone SHADOW worker. Disabled by environment AND durable control."""
import argparse
import asyncio
from datetime import date, datetime
import json
import logging
import signal
from sqlalchemy import select
from app.config import settings
from app.models import AnalyticsSignalRun, AnalyticsSignalState
from app.services.analytics_signal_worker import (WORKER_LOCK, last_closed_day, record_failure, run_batch, state)
from app.services.seasonality_worker import advisory, verify, SeasonalityBusy


async def execute(options):
    from app.database import engine, SessionLocal
    shutdown = asyncio.Event()
    checkpoint_id = None
    for signum in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(signum, shutdown.set)
    try:
        if options.command == "status":
            async with SessionLocal() as db:
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
                    target = options.as_of or last_closed_day()
                    if not options.as_of and active and active.status in {"RUNNING", "PAUSED", "FAILED"}:
                        original = active.metadata_json.get("source_decision", {}).get("requested_as_of")
                        target = date.fromisoformat(original) if original else active.as_of
                    checkpoint_id = active.id if active else None
                    checkpoint_heartbeat = active.heartbeat_at if active else None
                    await db.commit()
                try:
                    result = await run_batch(SessionLocal, engine, as_of=target,
                        scan_size=options.scan_size, client_size=options.client_size, leadership=leadership)
                    checkpoint_id = result.get("run_id")
                    checkpoint_heartbeat = datetime.fromisoformat(result["heartbeat_at"]) if result.get("heartbeat_at") else None
                    print(json.dumps(result, default=str), flush=True)
                except SeasonalityBusy:
                    result = {"status": "BUSY"}
                except Exception as exc:
                    await record_failure(SessionLocal, type(exc).__name__,
                        run_id=checkpoint_id, heartbeat_at=checkpoint_heartbeat)
                    logging.exception("Shadow analytics batch failed")
                    return 1
                if result["status"] == "DISABLED":return 0
                if options.command == "run" and result["status"] in {"SUCCEEDED", "SUPERSEDED", "BLOCKED"}:return 0 if result["status"] == "SUCCEEDED" else 1
                await verify(leadership, WORKER_LOCK)
                delay = options.poll if result["status"] in {"SUCCEEDED", "BLOCKED"} else options.pause
                try:await asyncio.wait_for(shutdown.wait(), delay)
                except TimeoutError:pass
        return 0
    except SeasonalityBusy:
        logging.error("Another shadow worker holds leadership")
        return 1
    finally:
        if shutdown.is_set() and checkpoint_id:
            async with SessionLocal() as db:
                control = await db.get(AnalyticsSignalState, 1, with_for_update=True)
                if control and control.active_run_id == checkpoint_id:
                    active = await db.get(AnalyticsSignalRun, checkpoint_id)
                    if (active and active.status == 'RUNNING' and checkpoint_heartbeat is not None
                            and active.heartbeat_at == checkpoint_heartbeat):
                        active.status = 'PAUSED'
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
