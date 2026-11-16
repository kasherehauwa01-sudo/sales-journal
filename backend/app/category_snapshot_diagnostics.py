"""Read-only bounded metadata diagnostics; never prepares data or runs aggregation."""

import asyncio
import json
from sqlalchemy import text

TABLES = (
    "sales",
    "sale_items",
    "catalog_lookups",
    "catalog_products",
    "category_season_clients",
    "category_season_facts",
    "category_season_snapshots",
)
SETTINGS = (
    "server_version",
    "statement_timeout",
    "lock_timeout",
    "work_mem",
    "hash_mem_multiplier",
    "max_parallel_workers_per_gather",
    "temp_file_limit",
    "default_statistics_target",
    "track_io_timing",
)


async def collect(engine):
    async with engine.connect() as db:
        async with db.begin():
            await db.execute(text("SET TRANSACTION READ ONLY"))
            await db.execute(text("SET LOCAL statement_timeout='3s'"))
            await db.execute(text("SET LOCAL lock_timeout='500ms'"))
            settings = (
                (
                    await db.execute(
                        text(
                            "SELECT name,setting,unit,context FROM pg_settings WHERE name=ANY(:names) ORDER BY name"
                        ),
                        {"names": list(SETTINGS)},
                    )
                )
                .mappings()
                .all()
            )
            tables = (
                (
                    await db.execute(
                        text(
                            """SELECT relname,n_live_tup,n_dead_tup,n_mod_since_analyze,last_analyze,last_autoanalyze,
                pg_relation_size(relid) AS heap_bytes,pg_indexes_size(relid) AS index_bytes
                FROM pg_stat_user_tables WHERE schemaname=current_schema() AND relname=ANY(:names) ORDER BY relname"""
                        ),
                        {"names": list(TABLES)},
                    )
                )
                .mappings()
                .all()
            )
            indexes = (
                (
                    await db.execute(
                        text(
                            """SELECT t.relname AS table_name,i.relname AS index_name,x.indisvalid,x.indisunique,
                pg_get_indexdef(i.oid) AS definition FROM pg_index x
                JOIN pg_class t ON t.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid
                JOIN pg_namespace n ON n.oid=t.relnamespace
                WHERE n.nspname=current_schema() AND t.relname=ANY(:names) ORDER BY t.relname,i.relname LIMIT 128"""
                        ),
                        {"names": list(TABLES)},
                    )
                )
                .mappings()
                .all()
            )
            stats = (
                (
                    await db.execute(
                        text(
                            """SELECT tablename,attname,null_frac,n_distinct,avg_width,correlation
                FROM pg_stats WHERE schemaname=current_schema() AND tablename=ANY(:names)
                ORDER BY tablename,attname LIMIT 512"""
                        ),
                        {"names": list(TABLES)},
                    )
                )
                .mappings()
                .all()
            )
            temp = (
                (
                    await db.execute(
                        text(
                            "SELECT temp_files,temp_bytes,stats_reset FROM pg_stat_database WHERE datname=current_database()"
                        )
                    )
                )
                .mappings()
                .one()
            )
            return {
                "database_temp_cumulative": dict(temp),
                "mode": "metadata_only",
                "settings_scope": "diagnostic_session_not_failed_attempt",
                "settings": [dict(x) for x in settings],
                "tables": [dict(x) for x in tables],
                "indexes": [dict(x) for x in indexes],
                "statistics": [dict(x) for x in stats],
            }


def main():
    import argparse

    argparse.ArgumentParser(description=__doc__).parse_args()
    try:
        from app.database import engine
    except Exception:
        raise SystemExit("CATEGORY_METADATA_DIAGNOSTICS_INIT_FAILED") from None

    async def run():
        try:
            print(json.dumps(await collect(engine), default=str, ensure_ascii=False))
        except Exception:
            raise SystemExit("CATEGORY_METADATA_DIAGNOSTICS_FAILED") from None
        finally:
            await engine.dispose()

    asyncio.run(run())


if __name__ == "__main__":
    main()
