"""Owner-operated read-only metadata/EXPLAIN; never executes a sales calculation.

Default: database settings and cumulative temp counters only. --manager requests
an estimated plan for the installed optimized query. Customer literals are removed
from the output. Run in the backend environment with its existing credentials.
"""
import argparse
import asyncio
import json

import sqlalchemy as sa
from sqlalchemy.pool import NullPool

from app.config import settings


class OutdatedDashboardQuery(RuntimeError):
    pass


def plan_structure(node):
    # Do not print Filter, Index Cond, SQL, parameters or arbitrary plan metadata:
    # those can contain client names and other private literals.
    allowed = {"Node Type", "Strategy", "Join Type", "Partial Mode", "Parallel Aware",
               "Plan Rows", "Plan Width", "Startup Cost", "Total Cost", "Relation Name",
               "Alias", "CTE Name", "Subplan Name", "Group Key", "Sort Key"}
    result = {key: value for key, value in node.items() if key in allowed}
    if node.get("Plans"):
        result["Plans"] = [plan_structure(child) for child in node["Plans"]]
    return result


def explain_query(connection, query):
    if not query.is_select:
        raise ValueError("Only SELECT plans are permitted")
    compiled = query.compile(dialect=connection.dialect, compile_kwargs={"render_postcompile": True})
    result = connection.exec_driver_sql(
        "EXPLAIN (ANALYZE false, FORMAT JSON, SETTINGS) " + str(compiled), compiled.params
    ).scalar_one()
    if isinstance(result, str):
        result = json.loads(result)
    return {"bind_parameters": len(compiled.params), "plan": plan_structure(result[0]["Plan"])}


async def selected_names(managers):
    from app.services.calltrack_buyers import manager_key
    from app.services.clients_vr import get_client_managers
    owners = {manager_key(value) for value in managers}
    return [name for name, owner in (await get_client_managers()).items() if manager_key(owner) in owners]


def inspect(year=None, managers=()):
    query = None
    names = []
    if managers:
        from app.services.calltrack_dashboard import monthly_sales_query, business_today
        from sqlalchemy.dialects import postgresql
        # Refuse to explain an old, expanded-IN implementation or silently claim
        # that an old image contains this fix. No live directory/database yet.
        probe = str(monthly_sales_query(2026, ["synthetic-probe"]).compile(dialect=postgresql.dialect()))
        if "dashboard_scope" not in probe or "ANY (" not in probe:
            raise OutdatedDashboardQuery()
        today = business_today()
        year = year if year is not None else today.year
        if not 2000 <= year <= 2100 or len(managers) > 500:
            raise ValueError("Unsupported dashboard scope")
        names = asyncio.run(selected_names(managers))
        if names:
            query = monthly_sales_query(year, names, compare=True, as_of=today)
    url = sa.engine.make_url(settings.database_url)
    if url.drivername not in {"postgresql+asyncpg", "postgresql+psycopg"}:
        raise ValueError("PostgreSQL required")
    engine = sa.create_engine(url.set(drivername="postgresql+psycopg"), poolclass=NullPool,
                              hide_parameters=True, connect_args={"connect_timeout": 5})
    output = {}
    try:
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                connection.execute(sa.text("SET TRANSACTION READ ONLY"))
                connection.execute(sa.text("SET LOCAL statement_timeout = '5s'"))
                connection.execute(sa.text("SET LOCAL lock_timeout = '1s'"))
                if connection.scalar(sa.text("SHOW transaction_read_only")) != "on":
                    raise RuntimeError("Read-only transaction required")
                output["settings"] = [dict(row) for row in connection.execute(sa.text("""
                    SELECT name, setting, unit FROM pg_settings
                    WHERE name IN ('server_version','dynamic_shared_memory_type','max_parallel_workers_per_gather',
                      'work_mem','hash_mem_multiplier','temp_file_limit','plan_cache_mode',
                      'data_directory','temp_tablespaces')
                    ORDER BY name
                """)).mappings()]
                output["temporary_files"] = dict(connection.execute(sa.text("""
                    SELECT datname, temp_files, temp_bytes, stats_reset
                    FROM pg_stat_database WHERE datname=current_database()
                """)).mappings().one())
                output["indexes"] = [dict(row) for row in connection.execute(sa.text("""
                    SELECT tablename,indexname FROM pg_indexes
                    WHERE schemaname='public' AND tablename IN ('sales','sale_items')
                    ORDER BY tablename,indexname
                """)).mappings()]
                if query is not None:
                    from app.services.report_execution import REPORT_SETTINGS
                    for statement in REPORT_SETTINGS:
                        connection.execute(sa.text(statement))
                    # Planning budget, not the production report's execution timeout.
                    connection.execute(sa.text("SET LOCAL statement_timeout = '5s'"))
                    output["selected_clients_count"] = len(names)
                    output["estimated_plan"] = explain_query(connection, query)
                    output["plan_limits"] = {"parallel_workers_per_gather": 0, "work_mem": "4MB", "hash_mem_multiplier": 1}
                elif managers:
                    output["selected_clients_count"] = 0
                    output["estimated_plan"] = None
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--year", type=int)
    parser.add_argument("--manager", action="append", default=[])
    args = parser.parse_args()
    try:
        print(json.dumps(inspect(args.year, args.manager), ensure_ascii=False, indent=2, default=str))
    except Exception as exc:
        # No full exceptions: driver errors can embed URLs, SQL and parameters.
        original = getattr(exc, "orig", None)
        print(json.dumps({"error": type(exc).__name__, "sqlstate": getattr(original, "sqlstate", None)}))
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
