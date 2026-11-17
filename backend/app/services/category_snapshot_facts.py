"""Bounded facts SQL under the caller's repeatable-read/publication transaction.
No commits, external calls, planner overrides or global PostgreSQL settings.
"""

import logging
import re
import time
from datetime import date
from uuid import uuid4
from sqlalchemy import text

log = logging.getLogger(__name__)


def month_ranges(first, end):
    if first is None or first >= end:
        return []
    start = first.replace(day=1)
    result = []
    while start < end:
        following = date(start.year + start.month // 12, start.month % 12 + 1, 1)
        stop = min(following, end)
        result.append((start, stop))
        start = stop
    return result


def input_identifier(name):
    if not re.fullmatch(r"cs_input_[0-9a-f]{32}|cs_monthly_input", name):
        raise ValueError("invalid_snapshot_input_identifier")
    return name


def input_statement(source_statement, name, ordered=True):
    name = input_identifier(name)
    source = str(source_statement)
    if (
        source.count("FROM sale_items i") != 1
        or source.count("GROUP BY") != 1
        or source.count("WHERE s.sale_date<:end") != 1
    ):
        raise ValueError("unreviewed_snapshot_source_statement")
    joins = source[source.index("FROM sale_items i") : source.index("GROUP BY")]
    return text(
        f"""CREATE TEMP TABLE {name} ON COMMIT DROP AS
      SELECT s.sale_date,p.category_id,c.id AS client_id,s.department AS store,p.brand,p.manufacturer,
      extract(year from s.sale_date)::int AS year,extract(month from s.sale_date)::int AS month,
      i.quantity AS units,i.quantity*i.actual_price AS revenue,1::bigint AS lines """
        + joins
        + (" ORDER BY s.sale_date" if ordered else "")
    )


INPUT_SALES_BATCH = 10000


def empty_input_statement(source_statement, name):
    # Let PostgreSQL preserve the exact expression types, including numeric scale.
    return text(
        str(input_statement(source_statement, name, ordered=False)) + " AND false"
    )


def chunk_input_statement(source_statement, name):
    name = input_identifier(name)
    original = str(input_statement(source_statement, name, ordered=False))
    prefix = f"CREATE TEMP TABLE {name} ON COMMIT DROP AS"
    if not original.startswith(prefix):
        raise ValueError("unreviewed_snapshot_input_prefix")
    return text(
        f"INSERT INTO pg_temp.{name} "
        + original[len(prefix) :]
        + " AND i.sale_id>=:id_start AND i.sale_id<:id_end"
    )


def input_boundary_statement():
    # Keyset pagination bounds actual sales rows, not the density of their IDs.
    return text(
        "SELECT max(id) FROM (SELECT id FROM sales WHERE id>=:start "
        f"ORDER BY id LIMIT {INPUT_SALES_BATCH}) limited"
    )


def ordered_input_statement(raw, ordered):
    raw, ordered = input_identifier(raw), input_identifier(ordered)
    return text(
        f"CREATE TEMP TABLE {ordered} ON COMMIT DROP AS "
        f"SELECT * FROM pg_temp.{raw} ORDER BY sale_date"
    )


def batch_statement(name):
    name = input_identifier(name)
    return text(
        f"""INSERT INTO category_season_facts(snapshot_id,category_id,client_id,store,brand,manufacturer,year,month,units,revenue,lines)
      SELECT :sid,category_id,client_id,store,brand,manufacturer,year,month,
      sum(units),coalesce(sum(revenue),0),sum(lines)
      FROM pg_temp.{name} WHERE sale_date>=:start AND sale_date<:end
      GROUP BY category_id,client_id,store,brand,manufacturer,year,month"""
    )


async def build_facts(
    db, pairs, sid, end, source_statement, ensure_headroom, set_stage
):
    name = "cs_input_" + uuid4().hex
    raw = "cs_input_" + uuid4().hex
    # New dictionary rows are invisible to autovacuum until publication. The
    # parent-table statistics also determine the cost of real RI/FK probes.
    set_stage("clients_statistics")
    ensure_headroom()
    await db.execute(text("ANALYZE category_season_clients"))
    set_stage("facts_input")
    ensure_headroom()
    await db.execute(
        empty_input_statement(source_statement, raw), dict(sid=sid, end=end)
    )
    start = await db.scalar(text("SELECT min(id) FROM sales"))
    number = 0
    while start is not None:
        ensure_headroom()
        last = await db.scalar(input_boundary_statement(), {"start": start})
        if last is None:
            break
        began = time.perf_counter()
        await db.execute(
            chunk_input_statement(source_statement, raw),
            dict(sid=sid, end=end, id_start=start, id_end=last + 1),
        )
        number += 1
        log.info(
            "category_snapshot_input batch=%d duration_ms=%.3f",
            number,
            (time.perf_counter() - began) * 1000,
        )
        start = last + 1
    set_stage("facts_sort")
    ensure_headroom()
    await db.execute(ordered_input_statement(raw, name))
    await db.execute(text(f"DROP TABLE pg_temp.{raw}"))
    set_stage("facts_index")
    ensure_headroom()
    await db.execute(text(f"CREATE INDEX ON {name}(sale_date)"))
    set_stage("facts_statistics")
    ensure_headroom()
    await db.execute(text(f"ANALYZE {name}"))
    first = await db.scalar(text(f"SELECT min(sale_date) FROM pg_temp.{name}"))
    set_stage("facts_months")
    for number, (start, stop) in enumerate(month_ranges(first, end), 1):
        ensure_headroom()
        began = time.perf_counter()
        await db.execute(batch_statement(name), dict(sid=sid, start=start, end=stop))
        log.info(
            "category_snapshot_batch batch=%d duration_ms=%.3f",
            number,
            (time.perf_counter() - began) * 1000,
        )
    ensure_headroom()
