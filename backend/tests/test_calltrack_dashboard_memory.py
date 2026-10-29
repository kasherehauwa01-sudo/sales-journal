"""Dashboard SQL equivalence and resource bounds; no live PostgreSQL or production."""
import asyncio
from datetime import date
import re
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, event, func, select, text
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.exc import DBAPIError
from fastapi import HTTPException

from app.models import Sale, SaleItem
from app.schemas.calltrack import CalltrackDashboardSalesRequest
from app.services import calltrack_dashboard as service, product_analytics_runtime as runtime
from app.services.report_execution import REPORT_SETTINGS


def legacy_query(year, names, *, compare=False, as_of=None):
    """Previous SQL shape retained as the numerical reference, small fixtures only."""
    from datetime import timedelta
    start = date(year - 1 if compare else year, 1, 1)
    end = min(date(year + 1, 1, 1), as_of + timedelta(days=1)) if as_of else date(year + 1, 1, 1)
    identity = func.lower(func.trim(func.regexp_replace(Sale.client, r"\s+", " ", "g")))
    units = select(SaleItem.sale_id, func.sum(SaleItem.quantity).label("units")).join(
        Sale, Sale.id == SaleItem.sale_id
    ).where(Sale.sale_date >= start, Sale.sale_date < end, identity.in_(names)).group_by(SaleItem.sale_id).subquery()
    year_col, month = func.extract("year", Sale.sale_date), func.extract("month", Sale.sale_date)
    return select(year_col.label("year"), month.label("month"), func.sum(Sale.total_amount).label("revenue"), func.count(Sale.id).label("checks"), func.coalesce(func.sum(units.c.units), 0).label("units")).outerjoin(
        units, units.c.sale_id == Sale.id
    ).where(Sale.sale_date >= start, Sale.sale_date < end, identity.in_(names)).group_by(year_col, month).order_by(year_col, month)


@pytest.fixture
def ledger(monkeypatch):
    # SQLite-only membership adapter. The CTE, monthly groups, joins, decimal
    # results and date conditions execute unchanged; native PG ANY has own tests.
    monkeypatch.setattr(service, "dashboard_client_condition", lambda names: func.lower(func.trim(func.regexp_replace(Sale.client, r"\s+", " ", "g"))).in_(names))
    engine = create_engine("sqlite://")
    @event.listens_for(engine, "connect")
    def functions(connection, record):
        connection.create_function("regexp_replace", 4, lambda v, p, r, f: None if v is None else re.sub(p, r, v))
    with engine.begin() as db:
        db.execute(text("CREATE TABLE sales (id INTEGER PRIMARY KEY, sale_date DATE, client TEXT, total_amount NUMERIC)"))
        db.execute(text("CREATE TABLE sale_items (sale_id INTEGER, quantity NUMERIC)"))
        db.execute(text("""INSERT INTO sales VALUES
          (1,'2026-01-01',' One ',100.25),(2,'2026-01-31','ONE',50),
          (3,'2026-04-01','One',111),(4,'2026-02-01','One',-10),
          (5,'2026-02-02','One',0),(6,'2026-03-01','Two',75),
          (7,'2026-11-01','One',999),(8,'2026-10-19','One',30),
          (9,'2026-10-20','One',123),(10,'2025-12-31','One',900),
          (11,'2025-01-01','One',40),(12,'2024-12-31','One',888),
          (13,'2027-01-01','One',777),(14,'2026-05-01',NULL,42),
          (15,'2026-05-01','',43),(16,'2026-06-01','A   B',13),
          (17,'2026-06-02',' A B ',14)"""))
        db.execute(text("""INSERT INTO sale_items VALUES
          (1,2),(1,3),(2,1),(4,-1),(5,0),(6,4),(7,999),(8,NULL),
          (9,123),(10,99),(11,2),(12,88),(13,77),(14,1),(15,1),(16,1.5),(17,2.5)"""))
        yield db
    engine.dispose()


@pytest.mark.parametrize("names,compare,as_of", [
    (["one"], False, None), (["one"], True, None),
    (["one", "a b"], True, date(2026, 10, 19)),
    (["one", "two"], True, date(2026, 10, 19)),
    ([], True, date(2026, 10, 19)), (["missing"], True, None),
    (["one"], True, date(2025, 6, 1)), (["one"], False, date(2024, 12, 1)),
    (["one"], True, date(2026, 1, 1)),
])
def test_monthly_preaggregation_matches_previous_sql(ledger, names, compare, as_of):
    before = ledger.execute(legacy_query(2026, names, compare=compare, as_of=as_of)).mappings().all()
    after = ledger.execute(service.monthly_sales_query(2026, names, compare=compare, as_of=as_of)).mappings().all()
    assert after == before
    assert len(after) <= (24 if compare else 12)
    if names == ["one"] and as_of is None:
        april = next(row for row in after if row["year"] == 2026 and row["month"] == 4)
        assert april["checks"] == 1 and april["revenue"] == 111 and april["units"] == 0


def test_100003_clients_have_one_array_and_one_normalization_expression():
    names = [f"client-{i}" for i in range(100_003)] + ["client-0"]
    sql = service.monthly_sales_query(2026, names, compare=True, as_of=date(2026, 10, 19)).compile(
        dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True}
    )
    arrays = [value for value in sql.params.values() if isinstance(value, list)]
    assert len(arrays) == 1 and len(arrays[0]) == 100_003
    assert len(sql.params) < 12
    assert len(str(sql)) < 2500
    assert str(sql).count("regexp_replace(") == 1
    assert str(sql).count("ANY (") == 1
    assert "GROUP BY sale_items.sale_id" not in str(sql)
    assert "original_products_text" not in str(sql) and "sales.phone" not in str(sql)
    assert "IN (__[POSTCOMPILE" not in str(sql)


class Session:
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    def __init__(self, fail=False):
        self.settings = []
        self.queries = 0
        self.rollbacks = 0
        self.fail = fail

    async def execute(self, query):
        if str(query).startswith("SET LOCAL"):
            self.settings.append(str(query))
            return None
        assert self.settings == list(REPORT_SETTINGS)
        self.queries += 1
        if self.fail:
            class DiskFull(Exception):
                sqlstate = "53100"
            raise DBAPIError("private SQL", {"client": "private-name"}, DiskFull())
        return SimpleNamespace(mappings=lambda: [dict(year=2026, month=1, revenue=150, checks=2, units=6)])

    async def rollback(self):
        self.rollbacks += 1


@pytest.mark.parametrize("fail", [False, True])
def test_dashboard_limits_apply_before_sql_and_always_rollback(monkeypatch, tmp_path, fail):
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "dashboard.lock"))
    monkeypatch.setattr(service, "get_client_managers", AsyncMock(return_value={"one": "Alice"}))
    monkeypatch.setattr(service, "business_today", lambda: date(2026, 10, 19))
    db = Session(fail)
    payload = CalltrackDashboardSalesRequest(year=2026, managers=["Alice"])
    if fail:
        with pytest.raises(HTTPException) as caught:
            asyncio.run(service.dashboard_sales(db, payload))
        assert caught.value.status_code == 503
    else:
        response = asyncio.run(service.dashboard_sales(db, payload))
        assert response["items"][0]["checks"] == 2
    assert db.queries == 1 and db.rollbacks == 1
    assert "SET LOCAL max_parallel_workers_per_gather = 0" in db.settings


def test_unmatched_manager_does_not_open_report_transaction(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "dashboard.lock"))
    monkeypatch.setattr(service, "get_client_managers", AsyncMock(return_value={"one": "Alice"}))
    db = Session()
    result = asyncio.run(service.dashboard_sales(db, CalltrackDashboardSalesRequest(year=2026, managers=["Other"])))
    assert all(row["checks"] == 0 for row in result["items"])
    assert db.queries == 0 and db.settings == [] and db.rollbacks == 0


def test_dashboard_queries_serialize_with_other_reports(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "dashboard.lock"))
    monkeypatch.setattr(service, "get_client_managers", AsyncMock(return_value={"one": "Alice"}))
    active = 0
    peak = 0

    class SlowSession(Session):
        async def execute(self, query):
            nonlocal active, peak
            if not str(query).startswith("SET LOCAL"):
                active += 1
                peak = max(peak, active)
                await asyncio.sleep(0.01)
                result = await super().execute(query)
                active -= 1
                return result
            return await super().execute(query)

    async def run():
        payload = CalltrackDashboardSalesRequest(year=2026, managers=["Alice"])
        sessions = [SlowSession() for _ in range(3)]
        await asyncio.gather(*(service.dashboard_sales(session, payload) for session in sessions))
        assert all(session.queries == 1 and session.rollbacks == 1 for session in sessions)

    asyncio.run(run())
    assert peak == 1


def test_plan_inspector_does_not_analyze_or_output_customer_literals():
    from scripts.inspect_calltrack_dashboard import explain_query
    from sqlalchemy.dialects.postgresql import psycopg
    import json

    class Connection:
        dialect = psycopg.dialect()
        def exec_driver_sql(self, statement, parameters):
            assert statement.startswith("EXPLAIN (ANALYZE false, FORMAT JSON, SETTINGS) ")
            assert "PRIVATE-CLIENT" not in statement
            assert ["PRIVATE-CLIENT"] in parameters.values()
            return SimpleNamespace(scalar_one=lambda: [{"Plan": {
                "Node Type": "Aggregate", "Plan Rows": 24,
                "Filter": "customer PRIVATE-CLIENT", "Output": ["PRIVATE-CLIENT"],
                "Plans": [{"Node Type": "CTE Scan", "CTE Name": "dashboard_scope", "Index Cond": "PRIVATE-CLIENT"}],
            }}])

    result = explain_query(Connection(), service.monthly_sales_query(2026, ["PRIVATE-CLIENT"], compare=True))
    assert "PRIVATE-CLIENT" not in json.dumps(result)
    assert result["plan"]["Plan Rows"] == 24
    assert result["plan"]["Plans"][0] == {"Node Type": "CTE Scan", "CTE Name": "dashboard_scope"}


def test_metadata_inspector_is_readonly_and_does_not_load_clients(monkeypatch):
    from scripts import inspect_calltrack_dashboard as inspector
    statements = []
    flags = dict(rollback=False, disposed=False)
    directory = AsyncMock(side_effect=AssertionError("Metadata must not load ClientsVR"))
    monkeypatch.setattr(inspector, "selected_names", directory)
    monkeypatch.setattr(inspector.settings, "database_url", "postgresql+psycopg://fixture.invalid/fixture")

    class Result:
        def __init__(self, rows): self.rows = rows
        def mappings(self): return self
        def __iter__(self): return iter(self.rows)
        def one(self): return self.rows[0]

    class Connection:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def begin(self): return self
        def rollback(self): flags["rollback"] = True
        def scalar(self, query):
            statements.append(str(query))
            return "on"
        def execute(self, query):
            sql = str(query)
            statements.append(sql)
            if "pg_stat_database" in sql:
                return Result([dict(datname="fixture", temp_files=0, temp_bytes=0, stats_reset=None)])
            return Result([])

    class Engine:
        def connect(self): return Connection()
        def dispose(self): flags["disposed"] = True

    monkeypatch.setattr(inspector.sa, "create_engine", lambda *args, **kwargs: Engine())
    result = inspector.inspect()
    assert result["temporary_files"]["temp_bytes"] == 0
    assert all(sql.lstrip().startswith(("SET TRANSACTION READ ONLY", "SET LOCAL", "SHOW", "SELECT")) for sql in statements)
    assert not any("EXPLAIN" in sql for sql in statements)
    assert flags == dict(rollback=True, disposed=True)
    directory.assert_not_awaited()
