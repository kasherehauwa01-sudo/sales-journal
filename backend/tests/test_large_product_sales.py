"""Large selection compilation and actual aggregation in disposable SQLite memory.
No working PostgreSQL, external catalogue, Docker or production data is used.
"""
import asyncio
from contextlib import asynccontextmanager
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, func, distinct, select, text
from sqlalchemy.dialects.postgresql import asyncpg

from app.models import Sale, SaleItem
from app.routers import analytics, product_reports, promotion_candidates_report
from app.services.report_filters import client_names_condition
from app.services import report_execution, product_analytics_runtime as runtime


@pytest.mark.parametrize("field", ["code", "article", "name"])
def test_more_than_100000_product_identifiers_use_one_bind(field):
    products = [SimpleNamespace(code=None, article=None, name="Item") for _ in range(100_003)]
    for index, product in enumerate(products):
        setattr(product, field, f"  SKU-{index}  ")
    # Repeated/whitespace variants do not increase SQL size or bind count.
    products.append(SimpleNamespace(**{"code": None, "article": None, "name": "Item", field: "SKU-0"}))
    query = select(SaleItem.id).where(product_reports._product_condition(products))
    sql = query.compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
    assert len(sql.params) == 1
    assert len(str(sql)) < 300
    assert "ANY (" in str(sql)
    values = next(iter(sql.params.values()))
    assert len(values) == 100_003
    assert "sku-0" in values and "sku-100002" in values


def test_mixed_product_categories_keep_code_article_name_rules():
    products = [
        SimpleNamespace(code=" C1 ", article="Ignored", name="Ignored"),
        SimpleNamespace(code=None, article=" A1 ", name="Ignored"),
        SimpleNamespace(code=None, article=None, name=" Name1 "),
    ]
    condition = product_reports._product_condition(products)
    sql = select(SaleItem.id).where(condition).compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
    assert len(sql.params) == 3
    assert sorted(sql.params.values()) == [["a1"], ["c1"], ["name1"]]
    assert str(sql).count(" OR ") == 2
    assert product_reports._product_condition([]) is None


def test_large_clients_and_products_remain_bounded_in_summary_and_promotions():
    count = 100_003
    products = [SimpleNamespace(code=f"code-{i}", article=None, name="Item") for i in range(count)]
    clients = [f"client-{i}" for i in range(count)]
    conditions = [product_reports._product_condition(products), client_names_condition(Sale.client, clients)]
    query = product_reports._summary_query(conditions)
    compiled = query.compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
    assert len(compiled.params) < 10
    assert len(str(compiled)) < 2000
    assert str(compiled).count("ANY (") == 2
    request = promotion_candidates_report.Request(date_from="2026-09-01", date_to="2026-09-30")
    query = select(Sale.id).where(*promotion_candidates_report._conditions(request, clients))
    sql = query.compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
    assert len(sql.params) < 10
    assert "ANY (" in str(sql)


@pytest.fixture
def ledger():
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        db.execute(text("CREATE TABLE sales(id INTEGER PRIMARY KEY, client TEXT, department TEXT, sale_date DATE, total_amount NUMERIC, base_amount NUMERIC, discount_percent NUMERIC)"))
        db.execute(text("CREATE TABLE sale_items(id INTEGER PRIMARY KEY, sale_id INTEGER, code TEXT, article TEXT, name TEXT, quantity NUMERIC, actual_price NUMERIC, base_price NUMERIC)"))
        # Repeated clients, mixed checks, null client/prices, zero/negative units,
        # a receipt without items, and excluded department/date history.
        db.execute(text("INSERT INTO sales VALUES (1,'Alpha','A','2026-09-02',300,360,10),(2,'Alpha','A','2026-09-03',-50,60,0),(3,NULL,'A','2026-09-04',0,NULL,NULL),(4,'Beta','B','2026-09-04',900,1000,5),(5,'Old','A','2025-01-01',500,600,0),(6,'','A','2026-09-05',20,30,0),(7,'No items','A','2026-09-06',70,80,0)"))
        db.execute(text("INSERT INTO sale_items VALUES (1,1,'x','a','Item X',2,100,120),(2,1,'y','b','Item Y',1,100,120),(3,2,'x','a','Item X',-1,50,60),(4,3,'x','a','Item X',0,NULL,NULL),(5,4,'x','a','Item X',9,100,110),(6,5,'x','a','Item X',5,100,120),(7,6,'x','a','Item X',1,20,NULL)"))
        yield db
    engine.dispose()


@pytest.mark.parametrize("selection", ["x", "y", "all", "none"])
def test_receipt_preaggregation_preserves_all_summary_inputs(ledger, selection):
    conditions = [Sale.sale_date.between(date(2026, 9, 1), date(2026, 9, 30)), Sale.department == "A"]
    if selection != "all":
        conditions.append(SaleItem.code == selection)
    reference = select(
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.actual_price), 0),
        func.coalesce(func.sum(SaleItem.quantity), 0),
        func.count(distinct(Sale.id)), func.count(distinct(Sale.client)),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.base_price), 0),
    ).select_from(SaleItem).join(Sale).where(*conditions)
    before = ledger.execute(reference).one()
    after = ledger.execute(product_reports._summary_query(conditions)).one()
    assert tuple(after) == tuple(before)
    sql = str(product_reports._summary_query(conditions))
    assert "count(DISTINCT sales.id)" not in sql
    assert "GROUP BY sales.id, sales.client" in sql


def test_filter_first_check_and_department_aggregations_are_equivalent(ledger):
    filtered = analytics.filters(select(Sale.id, Sale.department, Sale.total_amount, Sale.base_amount, Sale.discount_percent), date(2026, 9, 1), date(2026, 9, 30), "A")
    sales = filtered.subquery()
    old_units = select(SaleItem.sale_id, func.sum(SaleItem.quantity).label("units")).group_by(SaleItem.sale_id).subquery()
    old_checks = select(sales.c.total_amount, func.coalesce(old_units.c.units, 0).label("units")).select_from(sales).outerjoin(old_units, old_units.c.sale_id == sales.c.id)
    assert sorted(ledger.execute(old_checks).all()) == sorted(ledger.execute(select(analytics._checks_rows(filtered))).all())
    old_departments = select(sales.c.department, func.sum(sales.c.total_amount), func.count(sales.c.id), func.avg(sales.c.total_amount), func.coalesce(func.sum(old_units.c.units), 0), func.avg(sales.c.discount_percent)).select_from(sales).outerjoin(old_units, old_units.c.sale_id == sales.c.id).group_by(sales.c.department)
    assert ledger.execute(old_departments).one() == ledger.execute(analytics._department_query(filtered)).one()
    reference_units = select(func.coalesce(func.sum(SaleItem.quantity), 0)).select_from(SaleItem).join(sales, SaleItem.sale_id == sales.c.id).scalar_subquery()
    from sqlalchemy import case
    reference_metrics = select(
        func.coalesce(func.sum(sales.c.total_amount), 0), func.count(sales.c.id),
        func.coalesce(func.avg(sales.c.total_amount), 0), func.coalesce(func.sum(sales.c.base_amount), 0),
        func.coalesce(func.avg(sales.c.discount_percent), 0),
        func.coalesce(func.sum(sales.c.base_amount - sales.c.total_amount), 0), reference_units,
        func.coalesce(func.avg(case((sales.c.discount_percent > 0, 1), else_=0)) * 100, 0),
    )
    metrics = ledger.execute(analytics._metrics_query(filtered)).one()
    assert tuple(metrics) == tuple(ledger.execute(reference_metrics).one())
    assert float(metrics[0]) == 340  # receipt totals, including receipt without items
    assert metrics[1] == 5
    assert float(metrics[6]) == 3  # selected receipt item units, including returns
    for builder in (analytics._checks_rows, analytics._department_query):
        sql = str(select(builder(filtered)) if builder is analytics._checks_rows else builder(filtered))
        assert "JOIN check_sales" in sql or "JOIN department_sales" in sql
        assert "original_products_text" not in sql


def test_reused_large_client_array_stays_one_bind_in_filtered_analytics():
    filtered = analytics.filters(select(Sale), clients=[f"client-{i}" for i in range(100_003)])
    for query in (analytics._metrics_query(filtered), analytics._department_query(filtered), select(analytics._checks_rows(filtered))):
        compiled = query.compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
        arrays = [value for value in compiled.params.values() if isinstance(value, list)]
        assert len(arrays) == 1 and len(arrays[0]) == 100_003
        assert len(compiled.params) < 20
        assert len(str(compiled)) < 3000
        assert "original_products_text" not in str(compiled)


class PgSession:
    bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))
    def __init__(self):
        self.statements = []
        self.rolled_back = False

    async def execute(self, statement):
        self.statements.append(str(statement))

    async def rollback(self):
        await asyncio.sleep(0.001)
        self.rolled_back = True


@pytest.mark.parametrize("fail", [False, True])
def test_report_limits_are_transaction_local_and_rollback_on_failure(monkeypatch, tmp_path, fail):
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "reports.lock"))
    session = PgSession()

    async def run():
        try:
            async with asynccontextmanager(report_execution.get_report_db)(session) as db:
                assert db is session
                assert session.statements == list(report_execution.REPORT_SETTINGS)
                assert all(value.startswith("SET LOCAL ") for value in session.statements)
                if fail:
                    raise RuntimeError("query failed")
        except RuntimeError:
            assert fail
        assert session.rolled_back

    asyncio.run(run())
    assert "SET LOCAL max_parallel_workers_per_gather = 0" in session.statements
    assert "SET LOCAL work_mem = '4MB'" in session.statements


def test_concurrent_reports_wait_through_transaction_cleanup(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "reports.lock"))
    active = 0
    peak = 0

    class CountingSession(PgSession):
        async def rollback(self):
            nonlocal active
            await super().rollback()
            active -= 1

    async def report():
        nonlocal active, peak
        session = CountingSession()
        async with asynccontextmanager(report_execution.get_report_db)(session):
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
        assert session.rolled_back

    asyncio.run(_parallel(report))
    assert peak == 1


async def _parallel(report):
    await asyncio.gather(report(), report(), report())


def test_cancelled_report_rolls_back_before_next_admission(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "reports.lock"))

    async def run():
        entered = asyncio.Event()
        session = PgSession()

        async def report():
            async with asynccontextmanager(report_execution.get_report_db)(session):
                entered.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(report())
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.rolled_back
        next_session = PgSession()
        async with asynccontextmanager(report_execution.get_report_db)(next_session):
            pass
        assert next_session.rolled_back

    asyncio.run(run())


def test_report_admission_precedes_large_request_body_parsing(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.routing import APIRouter
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "reports.lock"))
    app = FastAPI()
    router = APIRouter(route_class=report_execution.ReportRoute)

    @router.post("/summary")
    async def summary(payload: dict):
        return {"ok": True}

    app.include_router(router)

    async def run():
        reads = 0
        messages = []

        async def receive():
            nonlocal reads
            reads += 1
            return {"type": "http.request", "body": b'{"products": []}', "more_body": False}

        async def send(message):
            messages.append(message)

        scope = dict(type="http", method="POST", path="/summary", raw_path=b"/summary", root_path="", query_string=b"", headers=[(b"content-type", b"application/json")], scheme="http", http_version="1.1", server=("test", 80), client=("test", 1))
        async with runtime.heavy_operation():
            task = asyncio.create_task(app(scope, receive, send))
            await asyncio.sleep(0.02)
            assert reads == 0
        await task
        assert reads == 1
        assert messages[0]["status"] == 200

    asyncio.run(run())


def test_product_saved_set_writes_keep_their_original_database_dependency():
    from app.database import get_db
    for route in product_reports.router.routes:
        dependencies = {dependency.call for dependency in route.dependant.dependencies}
        if route.path.endswith("/summary") or route.path.endswith("/export"):
            assert report_execution.get_report_db in dependencies
        if "/sets" in route.path:
            assert get_db in dependencies
            assert report_execution.get_report_db not in dependencies


@pytest.mark.parametrize("sqlstate", ["53100", "53200", "53300", "57014"])
def test_resource_failure_returns_503_without_logging_parameters(monkeypatch, tmp_path, caplog, sqlstate):
    from fastapi import HTTPException
    from sqlalchemy.exc import DBAPIError
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "reports.lock"))
    session = PgSession()

    class DriverFailure(Exception):
        pass

    driver = DriverFailure("private SQL and private-client")
    driver.sqlstate = sqlstate

    async def run():
        with pytest.raises(HTTPException) as caught:
            async with asynccontextmanager(report_execution.get_report_db)(session):
                raise DBAPIError("private SQL", {"client": "private-client"}, driver)
        assert caught.value.status_code == 503
        assert session.rolled_back

    asyncio.run(run())
    assert sqlstate in caplog.text
    assert "private SQL" not in caplog.text and "private-client" not in caplog.text


def test_busy_report_is_503_before_body_or_database_access(monkeypatch, tmp_path):
    from fastapi import FastAPI, APIRouter
    import httpx
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "reports.lock"))
    monkeypatch.setattr(runtime, "WAIT_SECONDS", 0.01)
    app = FastAPI()
    router = APIRouter(route_class=report_execution.ReportRoute)

    @router.post("/summary")
    async def summary(payload: dict):
        raise AssertionError("A busy request must not enter report calculation")

    app.include_router(router)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            async with runtime.heavy_operation():
                response = await asyncio.create_task(client.post("/summary", json={"products": []}))
            assert response.status_code == 503
            assert response.headers["retry-after"] == "10"

    asyncio.run(run())


def test_summary_http_accepts_more_than_100000_products_and_large_retail_scope(monkeypatch, tmp_path):
    from fastapi import FastAPI
    import httpx
    from app.database import get_db
    monkeypatch.setattr(runtime, "LOCK_PATH", str(tmp_path / "reports.lock"))
    count = 100_003

    async def buyers(value):
        assert value == "Розница"
        return [f"client-{i}" for i in range(count)]

    class Db:
        async def execute(self, query):
            compiled = query.compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
            assert len(compiled.params) < 10
            arrays = [v for v in compiled.params.values() if isinstance(v, list)]
            assert sorted(len(v) for v in arrays) == [count, count]
            return SimpleNamespace(one=lambda: (0, 0, 0, 0, 0))

    async def database():
        return Db()

    monkeypatch.setattr(product_reports, "get_buyer_type_clients", buyers)
    app = FastAPI()
    app.include_router(product_reports.router)
    app.dependency_overrides[get_db] = database

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.post("/reports/product-sales/summary", json=dict(
                date_from="2026-09-01", date_to="2026-09-30", buyer_type="Розница",
                products=[{"code": f"code-{i}", "name": "Item"} for i in range(count)],
            ))
            assert response.status_code == 200, response.text
            assert response.json()["current"]["checks"] == 0

    asyncio.run(run())
