"""Explicit local synthetic lab only. Never imports sales or uses a working dump."""

import argparse
import json
import os
import time
import tempfile
from datetime import date
from pathlib import Path
import sqlalchemy as sa
from app.services.category_seasonality_snapshot import facts_statement, sqlstate
from app.services.report_execution import REPORT_SETTINGS
from benchmarks.snapshot_monthly import (
    execute_facts,
    physical_target,
    sizes,
    reset_physical_clients,
)

PAIRS = "cs_pairs_" + "a" * 32


class LabFailure(Exception):
    def __init__(self, report):
        self.report = report


def plan_nodes(node, path="0"):
    return [
        dict(
            path=path,
            node_type=node["Node Type"],
            join_type=node.get("Join Type"),
            plan_rows=node.get("Plan Rows"),
            actual_rows=node.get("Actual Rows"),
            actual_loops=node.get("Actual Loops"),
            rows_removed_by_filter=node.get("Rows Removed by Filter"),
            exact_heap_blocks=node.get("Exact Heap Blocks"),
            lossy_heap_blocks=node.get("Lossy Heap Blocks"),
            index_cond=node.get("Index Cond"),
            hashagg_batches=node.get("HashAgg Batches"),
            shared_read_blocks=node.get("Shared Read Blocks", 0),
            shared_hit_blocks=node.get("Shared Hit Blocks", 0),
            local_read_blocks=node.get("Local Read Blocks", 0),
            local_hit_blocks=node.get("Local Hit Blocks", 0),
            relation=node.get("Relation Name"),
            index=node.get("Index Name"),
            temp_read_blocks=node.get("Temp Read Blocks", 0),
            temp_written_blocks=node.get("Temp Written Blocks", 0),
            sort_method=node.get("Sort Method"),
            sort_space_kb=node.get("Sort Space Used"),
            sort_space_type=node.get("Sort Space Type"),
            disk_usage_kb=node.get("Disk Usage"),
            hash_batches=node.get("Hash Batches"),
            peak_memory_kb=node.get("Peak Memory Usage"),
        )
    ] + [
        x
        for i, child in enumerate(node.get("Plans", []))
        for x in plan_nodes(child, path + "." + str(i))
    ]


def chosen_joins(nodes):
    return [
        n["node_type"]
        for n in nodes
        if n["node_type"] in {"Nested Loop", "Hash Join", "Merge Join"}
    ]


class ReportWriter:
    """Keep the latest private checkpoint, including plans before timed execution."""

    def __init__(self, path):
        self.path = path
        self.created = False

    def __call__(self, report):
        if not self.created:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
            self.created = True
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", dir=self.path.parent, delete=False
            ) as file:
                temporary = file.name
                json.dump(report, file, default=str, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary and os.path.exists(temporary):
                os.unlink(temporary)


def run(
    engine,
    sales,
    items,
    products,
    clients,
    repetitions,
    sql_timeout=60,
    comparison="no_nested_loop",
    progress=None,
    baseline_timeout=None,
    physical=False,
    fresh_client_statistics=False,
):
    results = []
    preparation_started = time.perf_counter()
    with engine.connect() as db, db.begin():
        db.execute(sa.text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        db.execute(sa.text("SET LOCAL search_path TO pg_temp"))
        for setting in REPORT_SETTINGS:
            db.execute(sa.text(setting))
        db.execute(
            sa.text("SELECT set_config('statement_timeout',:value,true)"),
            {"value": str(sql_timeout) + "s"},
        )
        # Stand-only disk protection. No automatic fallback if the role cannot set it.
        db.execute(sa.text("SET LOCAL temp_file_limit='1GB'"))
        db.execute(
            sa.text(
                "CREATE TEMP TABLE sales(id bigint PRIMARY KEY,sale_date date,client text,department text) ON COMMIT DROP"
            )
        )
        db.execute(
            sa.text(
                """INSERT INTO sales SELECT n, date '2023-01-01'+((n*17)%1095)::int,
          ' client_'||(n%:clients)::text||' ', 'store_'||(n%12)::text FROM generate_series(1,:n) n"""
            ),
            dict(n=sales, clients=clients),
        )
        db.execute(
            sa.text(
                "CREATE TEMP TABLE sale_items(sale_id bigint,code text,article text,quantity numeric,actual_price numeric) ON COMMIT DROP"
            )
        )
        db.execute(
            sa.text(
                """INSERT INTO sale_items SELECT 1+(n-1)%:sales,
          CASE WHEN n%7=0 THEN NULL ELSE lpad((n%:products)::text,8,'0') END,
          'article_'||(n%:products)::text, (n%5)-1, ((n%997)+1)::numeric/100
          FROM generate_series(1,:n) n"""
            ),
            dict(sales=sales, products=products, n=items),
        )
        db.execute(sa.text("CREATE INDEX ON sale_items(sale_id)"))
        db.execute(sa.text("CREATE INDEX ON sale_items(code)"))
        db.execute(sa.text("CREATE INDEX ON sale_items(article)"))
        db.execute(sa.text("CREATE INDEX ON sales(sale_date)"))
        db.execute(sa.text("CREATE INDEX ON sales(lower(trim(client)),sale_date)"))
        db.execute(
            sa.text(
                f"""CREATE TEMP TABLE {PAIRS} AS SELECT DISTINCT coalesce(code,'') AS code,coalesce(article,'') AS article,
          1+length(coalesce(code,''))%7 AS category_id,'brand'::text AS brand,'maker'::text AS manufacturer FROM sale_items"""
            )
        )
        for table in ("sales", "sale_items", PAIRS):
            db.execute(sa.text("ANALYZE " + table))
        if physical:
            # Add after bulk generation, then validate before measuring facts.
            # This mirrors the real sale_items→sales FK for join estimation.
            db.execute(
                sa.text(
                    "ALTER TABLE sale_items ADD CONSTRAINT lab_items_sale_fk FOREIGN KEY(sale_id) REFERENCES sales(id) NOT VALID"
                )
            )
            db.execute(
                sa.text("ALTER TABLE sale_items VALIDATE CONSTRAINT lab_items_sale_fk")
            )
        factddl = """(id bigserial PRIMARY KEY,snapshot_id text,category_id int,client_id int,store text,brand text,manufacturer text,
          year int,month int,units numeric,revenue numeric,lines bigint) ON COMMIT DROP"""
        if physical:
            target_schema = physical_target(db, PAIRS, date(2026, 1, 1))
        else:
            target_schema = None
            db.execute(sa.text("CREATE TEMP TABLE category_season_facts" + factddl))
            db.execute(
                sa.text(
                    "CREATE INDEX ON category_season_facts(snapshot_id,store,year,month)"
                )
            )
            db.execute(
                sa.text(
                    "CREATE INDEX ON category_season_facts(snapshot_id,category_id)"
                )
            )
        db.execute(sa.text("CREATE TEMP TABLE baseline_facts" + factddl))
        input_counts = dict(
            sales=sales,
            sale_items=items,
            pairs=db.scalar(sa.text(f"SELECT count(*) FROM {PAIRS}")),
            clients=clients,
        )
        columns = "snapshot_id,category_id,client_id,store,brand,manufacturer,year,month,units,revenue,lines"
        header = dict(
            input=input_counts,
            statement_timeout_seconds=sql_timeout,
            baseline_timeout_seconds=baseline_timeout or sql_timeout,
            physical_target=physical,
            source_fk_validated=physical,
            fresh_client_statistics=fresh_client_statistics,
            target_schema=target_schema,
            preparation_seconds=time.perf_counter() - preparation_started,
            isolation="repeatable read",
            whole_snapshot_build_measured=False,
            block_size_bytes=int(db.scalar(sa.text("SHOW block_size"))),
            server_version=db.scalar(sa.text("SHOW server_version")),
            settings=list(REPORT_SETTINGS),
            temp_file_limit="1GB per backend",
            comparison_variant=comparison,
            input_ordered_by_date=comparison == "monthly_materialized",
            production_scale_verified=False,
            source_heap_bytes={
                table: db.scalar(
                    sa.text("SELECT pg_relation_size(CAST(:t AS regclass))"),
                    {"t": table},
                )
                for table in ("sales", "sale_items", PAIRS)
            },
            limitations=[
                "synthetic distribution; source rows narrower than production",
                "single client generation in a temp table, not production multi-generation statistics",
                (
                    "logged actual-model FK/index targets"
                    if physical
                    else "no target foreign-key triggers"
                ),
                "no other VPS services",
                "warm caches; order alternated",
            ],
        )

        def checkpoint(status):
            report = dict(header, status=status, runs=results)
            if progress:
                progress(report)
            return report

        for repeat in range(repetitions):
            # Only reference a completed result from this repetition. If just
            # one variant succeeds, its output is NEVER declared equivalent.
            reference = None
            db.execute(sa.text("TRUNCATE baseline_facts RESTART IDENTITY"))
            order = (
                ("baseline", comparison)
                if repeat % 2 == 0
                else (comparison, "baseline")
            )
            for variant in order:
                record = dict(
                    variant=variant,
                    repeat=repeat,
                    status="running",
                    phase="setup",
                    sqlstate=None,
                    estimated_plan=None,
                    actual_plan=None,
                    actual_nodes=None,
                    equivalent=None,
                    comparison_status="not_available",
                )
                results.append(record)
                transaction = db.begin_nested()
                attempt_started = time.perf_counter()
                phase_started = attempt_started
                facts_started = None
                try:
                    # Explicitly restore baseline ON; SET LOCAL is scoped to
                    # this savepoint/transaction and never changes server defaults.
                    db.execute(
                        sa.text(
                            "SET LOCAL enable_nestloop = "
                            + ("off" if variant == "no_nested_loop" else "on")
                        )
                    )
                    db.execute(
                        sa.text("SELECT set_config('statement_timeout',:v,true)"),
                        {
                            "v": str(
                                (baseline_timeout or sql_timeout)
                                if variant == "baseline"
                                else sql_timeout
                            )
                            + "s"
                        },
                    )
                    record["wal_start_lsn"] = str(
                        db.scalar(sa.text("SELECT pg_current_wal_insert_lsn()"))
                    )
                    db.execute(sa.text("DROP TABLE IF EXISTS pg_temp.cs_monthly_input"))
                    if physical:
                        reset_physical_clients(db, target_schema)
                    else:
                        db.execute(
                            sa.text("DROP TABLE IF EXISTS category_season_clients")
                        )
                    if not physical:
                        db.execute(
                            sa.text(
                                "CREATE TEMP TABLE category_season_clients(snapshot_id text,id int,name text,PRIMARY KEY(snapshot_id,id),UNIQUE(snapshot_id,name)) ON COMMIT DROP"
                            )
                        )
                    db.execute(
                        sa.text(
                            "INSERT INTO category_season_clients SELECT 'synthetic',n,'client_'||n::text FROM generate_series(0,:n-1) n"
                        ),
                        dict(n=clients),
                    )
                    if fresh_client_statistics or variant == "analyze_clients":
                        db.execute(sa.text("ANALYZE category_season_clients"))
                    db.execute(
                        sa.text("TRUNCATE category_season_facts RESTART IDENTITY")
                    )
                    record["effective_settings"] = dict(
                        db.execute(
                            sa.text(
                                "SELECT current_setting('transaction_isolation') AS transaction_isolation,current_setting('enable_nestloop') AS enable_nestloop,current_setting('work_mem') AS work_mem,current_setting('temp_file_limit') AS temp_file_limit,current_setting('statement_timeout') AS statement_timeout,current_setting('max_parallel_workers_per_gather') AS max_parallel_workers_per_gather,current_setting('hash_mem_multiplier') AS hash_mem_multiplier"
                            )
                        )
                        .mappings()
                        .one()
                    )
                    record["phase"] = "facts"
                    phase_started = facts_started = time.perf_counter()
                    execute_facts(
                        db,
                        PAIRS,
                        "synthetic",
                        date(2026, 1, 1),
                        record,
                        variant.startswith("monthly"),
                        plan_nodes,
                        chosen_joins,
                        checkpoint,
                        statement_factory=facts_statement,
                        materialized=variant.startswith("monthly_materialized"),
                        input_ordered=variant != "monthly_materialized_unordered",
                        row_counts={"sales": sales, "sale_items": items},
                    )
                    record["phase"] = "totals"
                    phase_started = time.perf_counter()
                    record["totals"] = dict(
                        db.execute(
                            sa.text(
                                "SELECT count(*) AS groups,sum(lines) AS lines,sum(units) AS units,sum(revenue) AS revenue FROM category_season_facts"
                            )
                        )
                        .mappings()
                        .one()
                    )
                    record["phase"] = "comparison"
                    phase_started = time.perf_counter()
                    if reference is None:
                        db.execute(
                            sa.text(
                                "INSERT INTO baseline_facts("
                                + columns
                                + ") SELECT "
                                + columns
                                + " FROM category_season_facts"
                            )
                        )
                    else:
                        differences = dict(
                            db.execute(
                                sa.text(
                                    "WITH forward AS (SELECT "
                                    + columns
                                    + " FROM baseline_facts EXCEPT ALL SELECT "
                                    + columns
                                    + " FROM category_season_facts),reverse AS (SELECT "
                                    + columns
                                    + " FROM category_season_facts EXCEPT ALL SELECT "
                                    + columns
                                    + " FROM baseline_facts) SELECT (SELECT count(*) FROM forward) AS reference_minus_current,(SELECT count(*) FROM reverse) AS current_minus_reference"
                                )
                            )
                            .mappings()
                            .one()
                        )
                        equal = all(value == 0 for value in differences.values())
                        detail = dict(
                            reference_variant=reference["variant"],
                            current_variant=variant,
                            **differences,
                        )
                        record.update(
                            equivalent=equal,
                            comparison_status="equal" if equal else "different",
                            comparison=detail,
                        )
                    transaction.commit()
                    record.update(status="complete", phase="complete", sqlstate=None)
                    if reference is None:
                        reference = record
                    else:
                        reference.update(
                            equivalent=record["equivalent"],
                            comparison_status=record["comparison_status"],
                            comparison=record["comparison"],
                        )
                except sa.exc.DBAPIError as exc:
                    # 57014 aborts only this savepoint, not preparation or the
                    # following variant. Never reuse partial facts or actual rows.
                    transaction.rollback()
                    record["io_measurement_complete"] = False
                    batches = record.get("batches", [])
                    record["longest_observed_batch_seconds"] = max(
                        (b.get("elapsed_seconds", 0) for b in batches), default=0
                    )
                    if batches and batches[-1]["status"] == "running":
                        batches[-1].update(status="failed", sqlstate=sqlstate(exc))
                    record.update(
                        status="failed",
                        sqlstate=sqlstate(exc),
                        phase_elapsed_seconds=time.perf_counter() - phase_started,
                    )
                    if (
                        facts_started is not None
                        and record.get("facts_status") != "complete"
                    ):
                        record["elapsed_seconds"] = time.perf_counter() - facts_started
                        record["facts_status"] = "failed"
                finally:
                    record["storage_final"] = sizes(db)
                    if record.get("wal_start_lsn"):
                        record["cluster_wal_insert_bytes"] = int(
                            db.scalar(
                                sa.text(
                                    "SELECT pg_wal_lsn_diff(pg_current_wal_insert_lsn(),CAST(:start AS pg_lsn))"
                                ),
                                {"start": record["wal_start_lsn"]},
                            )
                        )
                    record["attempt_elapsed_seconds"] = (
                        time.perf_counter() - attempt_started
                    )
                checkpoint("running")
        status = (
            "complete"
            if all(
                r["status"] == "complete" and r["equivalent"] is True for r in results
            )
            else (
                "partial"
                if any(r["status"] == "complete" for r in results)
                else "failed"
            )
        )
        report = checkpoint(status)
        # The physical schema belongs exclusively to this synthetic run. Its
        # rollback preserves measured WAL/IO but never leaves lab generations.
        if physical:
            db.rollback()
        return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-synthetic-lab", action="store_true", required=True)
    parser.add_argument("--sales", type=int, default=25000)
    parser.add_argument("--items", type=int, default=100000)
    parser.add_argument("--products", type=int, default=10000)
    parser.add_argument("--clients", type=int, default=2000)
    parser.add_argument("--repetitions", type=int, default=2)
    parser.add_argument(
        "--comparison",
        choices=(
            "no_nested_loop",
            "analyze_clients",
            "monthly",
            "monthly_materialized",
            "monthly_materialized_unordered",
        ),
        default="no_nested_loop",
    )
    parser.add_argument("--fresh-client-statistics", action="store_true")
    parser.add_argument("--physical-target", action="store_true")
    parser.add_argument("--baseline-timeout-seconds", type=int)
    parser.add_argument("--sql-timeout-seconds", type=int, default=60)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if (
        not all(
            v > 0
            for v in (
                args.sales,
                args.items,
                args.products,
                args.clients,
                args.repetitions,
            )
        )
        or (
            args.baseline_timeout_seconds is not None
            and not 1 <= args.baseline_timeout_seconds <= 300
        )
        or not 1 <= args.sql_timeout_seconds <= 300
        or args.output.exists()
        or args.output.is_symlink()
        or not args.output.parent.is_dir()
    ):
        parser.error("Positive sizes and a new output file required")
    try:
        url = sa.engine.make_url(os.environ["LAB_DATABASE_URL"])
    except Exception:
        parser.error("Valid private LAB_DATABASE_URL required")
    if (
        url.host not in ("localhost", "127.0.0.1")
        or url.database != "category_snapshot_lab"
    ):
        parser.error("Dedicated localhost category_snapshot_lab database required")
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    writer = ReportWriter(args.output)
    try:
        result = run(
            engine,
            args.sales,
            args.items,
            args.products,
            args.clients,
            args.repetitions,
            args.sql_timeout_seconds,
            comparison=args.comparison,
            progress=writer,
            baseline_timeout=args.baseline_timeout_seconds,
            physical=args.physical_target,
            fresh_client_statistics=args.fresh_client_statistics,
        )
        writer(result)
        if result["status"] != "complete":
            raise SystemExit(
                "SYNTHETIC_LAB_INCOMPLETE: see each attempt phase/sqlstate; no automatic retry"
            )
    except LabFailure as exc:
        writer(exc.report)
        raise SystemExit(
            "SYNTHETIC_LAB_FAILED phase=facts sqlstate="
            + (exc.report["sqlstate"] or "unknown")
        ) from None
    except Exception as exc:
        raise SystemExit(
            "SYNTHETIC_LAB_FAILED phase=preparation_or_comparison sqlstate="
            + (sqlstate(exc) or "unknown")
        ) from None
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
