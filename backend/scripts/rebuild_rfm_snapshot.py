"""Explicit initial build or read-only status: python -m scripts.rebuild_rfm_snapshot."""
import argparse
import asyncio
import json
import logging
from datetime import date

from app.database import engine, SessionLocal
from app.services.rfm_snapshot import active_snapshot, metadata, rebuild_snapshot


async def run(args):
    try:
        if args.status:
            async with SessionLocal() as db:
                snapshot = await active_snapshot(db)
                if snapshot is None:
                    raise SystemExit("RFM snapshot is not built")
                result = metadata(snapshot)
        else:
            result = await rebuild_snapshot(engine, as_of=args.as_of)
        print(json.dumps(result, default=str, ensure_ascii=False))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--as-of", type=date.fromisoformat)
    group.add_argument("--status", action="store_true")
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
