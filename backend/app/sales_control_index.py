"""Explicit, standalone CONCURRENTLY index preparation; never run by startup/migration."""

import argparse
import sqlalchemy as sa
from app.config import settings
from start_backend import sync_engine, StartupBlocked
from app.services.product_analytics_runtime import ensure_headroom


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--create", action="store_true")
    args = parser.parse_args()
    engine = sync_engine(settings)
    try:
        with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
            acquired = []
            try:
                # Same session and order as the production updater. Nonblocking:
                # no index build may overlap import/seasonality/catalog/mail work.
                keys = (
                    (
                        734190221,
                        734190220,
                        734190117,
                        734190271,
                        734190272,
                        734190270,
                        734190249,
                    )
                    if args.create
                    else (734190249,)
                )
                for key in keys:
                    if not db.scalar(
                        sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
                    ):
                        raise StartupBlocked(
                            "Background/schema work is active; retry in a maintenance window"
                        )
                    acquired.append(key)
                rev = list(
                    db.scalars(sa.text("SELECT version_num FROM alembic_version"))
                )
                if rev not in (["20261111_27"], ["20261112_28"]):
                    raise StartupBlocked("Only schema27/28 supported")
                existing = db.scalar(
                    sa.text("SELECT to_regclass('ix_sales_department_date_control')")
                )
                if not existing and args.create:
                    ensure_headroom()
                    db.execute(sa.text("SET maintenance_work_mem='16MB'"))
                    db.execute(sa.text("SET max_parallel_maintenance_workers=0"))
                    db.execute(sa.text("SET lock_timeout='2s'"))
                    db.execute(sa.text("SET statement_timeout='30min'"))
                    db.execute(
                        sa.text(
                            "CREATE INDEX CONCURRENTLY ix_sales_department_date_control ON sales (department,sale_date)"
                        )
                    )
                valid = db.scalar(
                    sa.text(
                        "SELECT EXISTS(SELECT 1 FROM pg_index i WHERE i.indexrelid=to_regclass('ix_sales_department_date_control') AND i.indrelid='sales'::regclass AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=2 AND pg_get_indexdef(i.indexrelid,1,true)='department' AND pg_get_indexdef(i.indexrelid,2,true)='sale_date')"
                    )
                )
                if not valid:
                    raise StartupBlocked(
                        "Index missing/invalid; no automatic DROP/rebuild. Audit required"
                    )
                print("SALES_CONTROL_INDEX_OK")
            finally:
                for key in reversed(acquired):
                    db.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
