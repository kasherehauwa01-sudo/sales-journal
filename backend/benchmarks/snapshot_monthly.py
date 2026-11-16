"""Lab orchestration sharing the candidate SQL; never a production entry point."""

from datetime import date
from app.services.category_snapshot_facts import (
    month_ranges,
    input_statement,
    batch_statement,
    empty_input_statement,
    chunk_input_statement,
    input_boundary_statement,
    ordered_input_statement,
)
import time
import sqlalchemy as sa
from app.services.category_seasonality_snapshot import facts_statement, sqlstate


def monthly_statement(pairs, factory=facts_statement):
    source = str(factory(pairs))
    old = "WHERE s.sale_date<:end"
    if source.count(old) != 1:
        raise ValueError("Unreviewed facts date predicate")
    return sa.text(
        source.replace(old, "WHERE s.sale_date>=:start AND s.sale_date<:end")
    )


def physical_target(db, pairs, end):
    """Logged targets use real model PK/FK/check/index definitions in an owned lab schema.
    Outer transaction is rolled back at the end: never keep synthetic generations.
    """
    from uuid import uuid4
    from datetime import datetime, timezone, timedelta
    from app.models import (
        CategorySeasonSnapshot,
        CategorySeasonCategory,
        CategorySeasonClient,
        CategorySeasonFact,
    )

    schema = "cs_lab_" + uuid4().hex
    db.execute(sa.text("CREATE SCHEMA " + schema))
    db.execute(sa.text("SET LOCAL search_path TO pg_temp," + schema))
    metadata = sa.MetaData()
    tables = [
        m.__table__.to_metadata(metadata, schema=schema)
        for m in (
            CategorySeasonSnapshot,
            CategorySeasonCategory,
            CategorySeasonClient,
            CategorySeasonFact,
        )
    ]
    metadata.create_all(db)
    db.execute(
        tables[0]
        .insert()
        .values(
            id="synthetic",
            status="building",
            started_at=datetime.now(timezone.utc),
            through=end - timedelta(days=1),
            options={},
            fact_rows=0,
        )
    )
    categories = db.scalars(sa.text("SELECT DISTINCT category_id FROM " + pairs)).all()
    if categories:
        db.execute(
            tables[1].insert(),
            [
                dict(
                    snapshot_id="synthetic",
                    id=i,
                    key=str(i),
                    parent_key="synthetic",
                    parent="synthetic",
                    name="synthetic",
                )
                for i in categories
            ],
        )
    return schema


def sizes(db):
    result = {}
    for name in (
        "category_season_facts",
        "baseline_facts",
        "category_season_clients",
        "cs_monthly_input",
        "cs_input_" + "c" * 32,
    ):
        if db.scalar(sa.text("SELECT to_regclass(:t)"), {"t": name}) is None:
            continue
        result[name] = int(
            db.scalar(
                sa.text("SELECT pg_total_relation_size(CAST(:t AS regclass))"),
                {"t": name},
            )
        )
    return result


def execute_facts(
    db,
    pairs,
    sid,
    end,
    record,
    monthly,
    plan_nodes,
    chosen_joins,
    checkpoint,
    statement_factory=None,
    materialized=False,
    row_counts=None,
    input_ordered=True,
    chunked=False,
):
    """All statements run on the caller's connection and repeatable-read transaction."""
    began = time.perf_counter()
    factory = statement_factory or facts_statement
    record["batches"] = []
    if materialized:
        stage_input(
            db,
            pairs,
            sid,
            end,
            record,
            plan_nodes,
            checkpoint,
            ordered=input_ordered,
            chunked=chunked,
        )
    if monthly:
        first = db.scalar(
            sa.text("SELECT min(sale_date) FROM sales WHERE sale_date<:end"),
            {"end": end},
        )
        intervals = month_ranges(first, end)
    else:
        intervals = [(None, end)]
    record["batches"] = []
    for start, stop in intervals:
        packet = dict(
            start=start,
            end=stop,
            status="running",
            estimated_plan=None,
            actual_plan=None,
        )
        record["batches"].append(packet)
        record["phase"] = "estimated_plan"
        statement = (
            materialized_statement()
            if materialized
            else monthly_statement(pairs, factory) if monthly else factory(pairs)
        )
        args = dict(sid=sid, end=stop)
        if monthly:
            args["start"] = start
        estimated = db.scalar(
            sa.text("EXPLAIN (FORMAT JSON, SETTINGS) " + str(statement)), args
        )
        packet.update(
            estimated_plan=estimated, estimated_nodes=plan_nodes(estimated[0]["Plan"])
        )
        record.update(
            estimated_plan=estimated, estimated_nodes=packet["estimated_nodes"]
        )
        record["estimated_joins"] = chosen_joins(packet["estimated_nodes"])
        record["phase"] = "facts"
        checkpoint("running")
        started = time.perf_counter()
        try:
            actual = db.scalar(
                sa.text(
                    "EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) "
                    + str(statement)
                ),
                args,
            )
        except sa.exc.DBAPIError as exc:
            packet["sqlstate"] = sqlstate(exc)
            packet.update(
                status="failed", elapsed_seconds=time.perf_counter() - started
            )
            record["failed_batch"] = len(record["batches"]) - 1
            raise
        nodes = plan_nodes(actual[0]["Plan"])
        packet.update(
            status="complete",
            actual_plan=actual,
            actual_nodes=nodes,
            elapsed_seconds=time.perf_counter() - started,
            temp_read_blocks=actual[0]["Plan"].get("Temp Read Blocks", 0),
            temp_written_blocks=actual[0]["Plan"].get("Temp Written Blocks", 0),
            executor_wal_bytes=actual[0]["Plan"].get("WAL Bytes", 0),
            storage_after=sizes(db),
        )
        record.update(
            actual_plan=actual if not monthly else None,
            actual_nodes=[
                n for b in record["batches"] for n in b.get("actual_nodes", [])
            ],
        )
        checkpoint("running")
    record.update(
        facts_status="complete",
        elapsed_seconds=time.perf_counter() - began,
        facts_stage_seconds=time.perf_counter() - began,
        longest_batch_seconds=max(
            (b["elapsed_seconds"] for b in record["batches"]), default=0
        ),
        sql_seconds=sum(b["elapsed_seconds"] for b in record["batches"]),
        temp_read_blocks=sum(b["temp_read_blocks"] for b in record["batches"]),
        temp_written_blocks=sum(b["temp_written_blocks"] for b in record["batches"]),
        executor_wal_bytes=sum(b["executor_wal_bytes"] for b in record["batches"]),
    )
    record["longest_staging_sql_seconds"] = max(
        (a["elapsed_seconds"] for a in record.get("staging", [])), default=0
    )
    record["longest_measured_sql_seconds"] = max(
        record["longest_batch_seconds"], record["longest_staging_sql_seconds"]
    )
    record["io_measurement_complete"] = True
    record["chosen_joins"] = chosen_joins(record.get("actual_nodes") or [])
    record["spill_nodes"] = record.get("actual_nodes") or []
    # The return count of a filtered Seq Scan is not the number visited.
    repeated = []
    for relation in (
        ("sales", "sale_items", "cs_monthly_input")
        if materialized
        else ("sales", "sale_items")
    ):
        full = (row_counts or {}).get(relation)
        if full is None:
            full = db.scalar(sa.text("SELECT count(*) FROM " + relation))
        batches = 0
        visits = 0
        for b in record["batches"]:
            scanned = sum(
                (n.get("actual_rows", 0) + (n.get("rows_removed_by_filter") or 0))
                * (n.get("actual_loops") or 0)
                for n in b.get("actual_nodes", [])
                if n.get("relation") == relation and n["node_type"] == "Seq Scan"
            )
            visits += scanned
            if full and scanned >= full * 0.99:
                batches += 1
        record[relation + "_sequential_rows_visited"] = visits
        if batches > 1:
            repeated.append(relation)
    record["repeated_full_scan_relations"] = repeated


def reset_physical_clients(db, schema):
    """A fresh parent relation for each first-fill trial, avoiding ANALYZE leakage."""
    from app.models import (
        CategorySeasonSnapshot,
        CategorySeasonCategory,
        CategorySeasonClient,
        CategorySeasonFact,
    )

    metadata = sa.MetaData()
    tables = [
        m.__table__.to_metadata(metadata, schema=schema)
        for m in (
            CategorySeasonSnapshot,
            CategorySeasonCategory,
            CategorySeasonClient,
            CategorySeasonFact,
        )
    ]
    db.execute(sa.text("TRUNCATE category_season_facts RESTART IDENTITY"))
    constraints = db.scalars(
        sa.text(
            "SELECT conname FROM pg_constraint WHERE conrelid=CAST(:fact AS regclass) AND confrelid=CAST(:client AS regclass) AND contype='f'"
        ),
        {
            "fact": schema + ".category_season_facts",
            "client": schema + ".category_season_clients",
        },
    ).all()
    quote = db.dialect.identifier_preparer.quote
    for constraint in constraints:
        db.execute(
            sa.text(
                "ALTER TABLE "
                + schema
                + ".category_season_facts DROP CONSTRAINT "
                + quote(constraint)
            )
        )
    tables[2].drop(db)
    tables[2].create(db)
    constraint = next(
        c
        for c in tables[3].foreign_key_constraints
        if list(c.column_keys) == ["snapshot_id", "client_id"]
    )
    db.execute(sa.schema.AddConstraint(constraint))


INPUT = "cs_monthly_input"


def materialized_statement():
    return batch_statement(INPUT)


def stage_input(
    db, pairs, sid, end, record, plan_nodes, checkpoint, ordered=True, chunked=False
):
    if chunked:
        return stage_chunked_input(db, pairs, sid, end, record, plan_nodes, checkpoint)
    statement = str(input_statement(facts_statement(pairs), INPUT, ordered=ordered))
    record["staging"] = []
    stage = dict(kind="joined_input", status="running", actual_plan=None)
    record["staging"].append(stage)
    record["phase"] = "staging_input"
    stage["estimated_plan"] = db.scalar(
        sa.text("EXPLAIN (FORMAT JSON,SETTINGS) " + statement), dict(sid=sid, end=end)
    )
    checkpoint("running")
    started = time.perf_counter()
    try:
        actual = db.scalar(
            sa.text("EXPLAIN (ANALYZE,BUFFERS,WAL,SETTINGS,FORMAT JSON) " + statement),
            dict(sid=sid, end=end),
        )
        stage.update(
            status="complete",
            actual_plan=actual,
            actual_nodes=plan_nodes(actual[0]["Plan"]),
            elapsed_seconds=time.perf_counter() - started,
        )
    except sa.exc.DBAPIError as exc:
        stage.update(
            status="failed",
            sqlstate=sqlstate(exc),
            elapsed_seconds=time.perf_counter() - started,
        )
        raise
    for kind, sql in [
        ("input_date_index", "CREATE INDEX ON cs_monthly_input(sale_date)"),
        ("input_statistics", "ANALYZE cs_monthly_input"),
    ]:
        record["phase"] = kind
        stage = dict(kind=kind, status="running")
        record["staging"].append(stage)
        checkpoint("running")
        started = time.perf_counter()
        try:
            db.execute(sa.text(sql))
        except sa.exc.DBAPIError as exc:
            stage.update(
                status="failed",
                sqlstate=sqlstate(exc),
                elapsed_seconds=time.perf_counter() - started,
            )
            raise
        stage.update(
            status="complete",
            elapsed_seconds=time.perf_counter() - started,
            storage_after=sizes(db),
        )


RAW_INPUT = "cs_input_" + "c" * 32


def stage_chunked_input(db, pairs, sid, end, record, plan_nodes, checkpoint):
    record["staging"] = []
    source = facts_statement(pairs)

    def execute(kind, statement, args=None, explain=False):
        stage = dict(kind=kind, status="running", actual_plan=None)
        record["staging"].append(stage)
        record["phase"] = kind
        if explain:
            stage["estimated_plan"] = db.scalar(
                sa.text("EXPLAIN (FORMAT JSON, SETTINGS) " + str(statement)), args or {}
            )
        checkpoint("running")
        started = time.perf_counter()
        try:
            if explain:
                actual = db.scalar(
                    sa.text(
                        "EXPLAIN (ANALYZE, BUFFERS, WAL, SETTINGS, FORMAT JSON) "
                        + str(statement)
                    ),
                    args or {},
                )
                stage.update(
                    actual_plan=actual, actual_nodes=plan_nodes(actual[0]["Plan"])
                )
            else:
                db.execute(statement, args or {})
        except sa.exc.DBAPIError as exc:
            stage.update(
                status="failed",
                sqlstate=sqlstate(exc),
                elapsed_seconds=time.perf_counter() - started,
            )
            raise
        stage.update(
            status="complete",
            elapsed_seconds=time.perf_counter() - started,
            storage_after=sizes(db),
        )
        checkpoint("running")

    execute(
        "input_empty", empty_input_statement(source, RAW_INPUT), dict(sid=sid, end=end)
    )
    start = db.scalar(sa.text("SELECT min(id) FROM sales"))
    while start is not None:
        last = db.scalar(input_boundary_statement(), {"start": start})
        if last is None:
            break
        execute(
            "input_chunk",
            chunk_input_statement(source, RAW_INPUT),
            dict(sid=sid, end=end, id_start=start, id_end=last + 1),
            explain=True,
        )
        start = last + 1
    execute("input_sort", ordered_input_statement(RAW_INPUT, INPUT), explain=True)
    execute("input_raw_drop", sa.text("DROP TABLE pg_temp." + RAW_INPUT))
    execute("input_date_index", sa.text("CREATE INDEX ON cs_monthly_input(sale_date)"))
    execute("input_statistics", sa.text("ANALYZE cs_monthly_input"))
