"""Operator-only CLI. Importing this module never starts synchronization."""

import argparse
import asyncio
import json
import logging
import signal


async def execute(args):
    from app.database import SessionLocal, engine
    from app.services.catalog_attributes import sync_status
    from app.services.catalog_sync import sync_lock, synchronize, SyncBusy
    from app.services.catalog_lookup_client import LookupError

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        if args.command == "status":
            async with SessionLocal() as db:
                print(
                    json.dumps(await sync_status(db), default=str, ensure_ascii=False)
                )
        else:
            async with sync_lock(engine):
                print(
                    json.dumps(
                        await synchronize(
                            SessionLocal,
                            max_products=args.max_products,
                            max_scan_rows=args.max_scan_rows,
                            batch_size=args.batch_size,
                            stop_event=stop,
                        )
                    )
                )
        return 0
    except (SyncBusy, LookupError) as exc:
        logging.error("catalog_sync command failed code=%s", str(exc))
        return 1
    except Exception:
        logging.error("catalog_sync command failed code=sync_error")
        return 1
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(
        description="Manual bounded CatalogVR reconciliation; no automatic schedule."
    )
    parser.add_argument("command", choices=("run", "status"))
    parser.add_argument("--max-products", type=int, default=1000)
    parser.add_argument("--max-scan-rows", type=int, default=10000)
    parser.add_argument("--batch-size", type=int, default=100)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(asyncio.run(execute(args)))


if __name__ == "__main__":
    main()
