"""Explicit local synthetic lab only. Never imports sales or uses a working dump."""

import argparse
import json
import os
import time
from datetime import date
from pathlib import Path
import sqlalchemy as sa
from app.services.category_seasonality_snapshot import facts_statement, sqlstate
from app.services.report_execution import REPORT_SETTINGS

PAIRS = "cs_pairs_" + "a" * 32


class LabFailure(Exception):
    def __init__(self, report):
        self.report = report


def run(engine, sales, items, products, clients, repetitions, sql_timeout=60):
    results = []
    with engine.connect() as db, db.begin():
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
        factddl = """(id bigserial PRIMARY KEY,snapshot_id text,category_id int,client_id int,store text,brand text,manufacturer text,
          year int,month int,units numeric,revenue numeric,lines bigint) ON COMMIT DROP"""
        for table in ("category_season_facts", "baseline_facts"):
            db.execute(sa.text("CREATE TEMP TABLE " + table + factddl))
        db.execute(
            sa.text(
                "CREATE INDEX ON category_season_facts(snapshot_id,store,year,month)"
            )
        )
        db.execute(
            sa.text("CREATE INDEX ON category_season_facts(snapshot_id,category_id)")
        )
        input_counts = dict(
            sales=sales,
            sale_items=items,
            pairs=db.scalar(sa.text(f"SELECT count(*) FROM {PAIRS}")),
            clients=clients,
        )
        for repeat in range(repetitions):
            for variant in (
                ("baseline", "analyze_clients")
                if repeat % 2 == 0
                else ("analyze_clients", "baseline")
            ):
                db.execute(sa.text("DROP TABLE IF EXISTS category_season_clients"))
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
                if variant == "analyze_clients":
                    db.execute(sa.text("ANALYZE category_season_clients"))
                db.execute(sa.text("TRUNCATE category_season_facts RESTART IDENTITY"))
                statement = str(facts_statement(PAIRS))
                args = dict(sid="synthetic", end=date(2026, 1, 1))
                estimated = db.scalar(
                    sa.text("EXPLAIN (FORMAT JSON, SETTINGS) " + statement), args
                )
                started = time.perf_counter()
                try:
                    actual = db.scalar(
                        sa.text(
                            "EXPLAIN (ANALYZE, BUFFERS, SETTINGS, FORMAT JSON) "
                            + statement
                        ),
                        args,
                    )
                except sa.exc.DBAPIError as exc:
                    raise LabFailure(
                        dict(
                            status="failed",
                            phase="facts",
                            sqlstate=sqlstate(exc),
                            variant=variant,
                            repeat=repeat,
                            input=input_counts,
                            elapsed_seconds=time.perf_counter() - started,
                            estimated_plan=estimated,
                            runs=results,
                            production_scale_verified=False,
                        )
                    ) from None
                elapsed = time.perf_counter() - started
                totals = dict(
                    db.execute(
                        sa.text(
                            "SELECT count(*) AS groups,sum(lines) AS lines,sum(units) AS units,sum(revenue) AS revenue FROM category_season_facts"
                        )
                    )
                    .mappings()
                    .one()
                )
                # Compare every dimension and every numeric value in SQL, not in Python RAM.
                columns = "snapshot_id,category_id,client_id,store,brand,manufacturer,year,month,units,revenue,lines"
                if not results:
                    db.execute(
                        sa.text(
                            "INSERT INTO baseline_facts("
                            + columns
                            + ") SELECT "
                            + columns
                            + " FROM category_season_facts"
                        )
                    )
                difference = db.scalar(
                    sa.text(
                        "SELECT EXISTS((SELECT "
                        + columns
                        + " FROM category_season_facts EXCEPT ALL SELECT "
                        + columns
                        + " FROM baseline_facts) UNION ALL (SELECT "
                        + columns
                        + " FROM baseline_facts EXCEPT ALL SELECT "
                        + columns
                        + " FROM category_season_facts))"
                    )
                )
                assert not difference, "Synthetic results differ"

                def spill_nodes(node):
                    return [
                        dict(
                            node_type=node["Node Type"],
                            temp_read_blocks=node.get("Temp Read Blocks", 0),
                            temp_written_blocks=node.get("Temp Written Blocks", 0),
                            sort_space_kb=node.get("Sort Space Used"),
                            sort_space_type=node.get("Sort Space Type"),
                            disk_usage_kb=node.get("Disk Usage"),
                        )
                    ] + [
                        x for child in node.get("Plans", []) for x in spill_nodes(child)
                    ]

                results.append(
                    dict(
                        variant=variant,
                        repeat=repeat,
                        elapsed_seconds=elapsed,
                        totals=totals,
                        estimated_plan=estimated,
                        actual_plan=actual,
                        spill_nodes=spill_nodes(actual[0]["Plan"]),
                        equivalent=True,
                    )
                )
        return dict(
            status="complete",
            input=input_counts,
            source_heap_bytes={
                table: db.scalar(
                    sa.text("SELECT pg_relation_size(CAST(:table AS regclass))"),
                    {"table": table},
                )
                for table in ("sales", "sale_items", PAIRS)
            },
            statement_timeout_seconds=sql_timeout,
            block_size_bytes=int(db.scalar(sa.text("SHOW block_size"))),
            settings=list(REPORT_SETTINGS),
            temp_file_limit="1GB per backend",
            production_scale_verified=False,
            limitations=[
                "synthetic distribution; source rows narrower than production",
                "single client generation in a temp table, not production multi-generation statistics",
                "no target foreign-key triggers",
                "no other VPS services",
                "warm caches; order alternated",
            ],
            runs=results,
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--confirm-synthetic-lab", action="store_true", required=True)
    parser.add_argument("--sales", type=int, default=25000)
    parser.add_argument("--items", type=int, default=100000)
    parser.add_argument("--products", type=int, default=10000)
    parser.add_argument("--clients", type=int, default=2000)
    parser.add_argument("--repetitions", type=int, default=2)
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
    try:
        result = run(
            engine,
            args.sales,
            args.items,
            args.products,
            args.clients,
            args.repetitions,
            args.sql_timeout_seconds,
        )
        with args.output.open("x") as file:
            json.dump(result, file, default=str, indent=2)
    except LabFailure as exc:
        with args.output.open("x") as file:
            json.dump(exc.report, file, default=str, indent=2)
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
