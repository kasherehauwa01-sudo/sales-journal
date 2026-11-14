"""Explicit administrator-only local command; no fill on startup or browser access."""

import argparse, asyncio, json
from app.database import engine
from app.services.category_seasonality_snapshot import rebuild


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["fill"])
    p.add_argument("--confirm", action="store_true", required=True)
    p.parse_args()

    async def run():
        try:
            try:
                print(json.dumps(await rebuild(engine, manual=True)))
            except Exception as exc:
                raise SystemExit(
                    "CATEGORY_SNAPSHOT_FAILED: " + type(exc).__name__
                ) from None
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
