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
    # New dictionary rows are invisible to autovacuum until publication. The
    # parent-table statistics also determine the cost of real RI/FK probes.
    set_stage("clients_statistics")
    ensure_headroom()
    await db.execute(text("ANALYZE category_season_clients"))
    set_stage("facts_input")
    ensure_headroom()
    await db.execute(input_statement(source_statement, name), dict(sid=sid, end=end))
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
