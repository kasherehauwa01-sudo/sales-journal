import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from sqlalchemy.dialects import postgresql

from app.routers import product_analytics


class RowsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows

    def first(self):
        return self.rows[0] if self.rows else None


class DetailsDb:
    def __init__(self, rows):
        self.rows = rows
        self.queries = []

    async def execute(self, query):
        self.queries.append(query)
        return RowsResult(self.rows)


class ScalarsResult:
    def __init__(self, rows):
        self.rows = rows

    def all(self):
        return self.rows


class ManagerDb:
    def __init__(self, clients):
        self.clients = clients
        self.query = None

    async def scalars(self, query):
        self.query = query
        return ScalarsResult(self.clients)


def request(**values):
    defaults = {
        "date_from": date(2026, 9, 1),
        "date_to": date(2026, 9, 30),
        "departments": [],
        "managers": [],
        "article_key": "code:sku-1",
    }
    return product_analytics.DetailRequest(**{**defaults, **values})


def test_details_queries_only_selected_product_and_keeps_sql_filters(monkeypatch):
    db = DetailsDb([
        SimpleNamespace(
            period=datetime(2026, 9, 1), revenue=120, units=3, checks=2,
            article="A-1", code="SKU-1", name="Бокал", first_sale=date(2026, 9, 2),
            last_sale=date(2026, 9, 5),
        )
    ])

    async def catalog(rows):
        assert len(rows) == 1
        return {}

    async def full_dataset_must_not_run(*_args, **_kwargs):
        raise AssertionError("details не должен пересчитывать весь отчёт")

    monkeypatch.setattr(product_analytics, "_catalog", catalog)
    monkeypatch.setattr(product_analytics, "_dataset", full_dataset_must_not_run)
    result = asyncio.run(product_analytics.details(
        request(departments=["ЦУМ"], article="SKU", search="Бокал"),
        group_by="week", db=db,
    ))

    compiled = db.queries[0].compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "lower(trim(sale_items.code))" in sql
    assert "sales.department IN" in sql
    assert "sale_items.article ILIKE" in sql
    assert "sale_items.name ILIKE" in sql
    assert "date_trunc" in sql
    assert "week" in compiled.params.values()
    assert result["group_by"] == "week"
    assert result["points"] == [{"period": date(2026, 9, 1), "revenue": 120.0, "units": 3.0, "checks": 2}]


def test_details_returns_empty_points_instead_of_404(monkeypatch):
    db = DetailsDb([])
    calls = []

    async def catalog(rows):
        calls.extend(rows)
        return {}

    monkeypatch.setattr(product_analytics, "_catalog", catalog)
    result = asyncio.run(product_analytics.details(request(), group_by="day", db=db))

    assert result["points"] == []
    assert result["group_by"] == "day"
    assert result["product"]["key"] == "code:sku-1"
    assert result["product"]["code"] == "sku-1"
    assert len(calls) == 1
    assert len(db.queries) == 2  # динамика + легкий lookup карточки одного SKU


def test_details_supports_day_week_and_month_grouping(monkeypatch):
    async def catalog(_rows):
        return {}

    monkeypatch.setattr(product_analytics, "_catalog", catalog)
    for grouping in ("day", "week", "month"):
        row = SimpleNamespace(
            period=datetime(2026, 9, 1), revenue=10, units=1, checks=1,
            article="A-1", code="SKU-1", name="Бокал",
            first_sale=date(2026, 9, 1), last_sale=date(2026, 9, 1),
        )
        db = DetailsDb([row])
        result = asyncio.run(product_analytics.details(request(), group_by=grouping, db=db))
        compiled = db.queries[0].compile(dialect=postgresql.dialect())

        assert grouping in compiled.params.values()
        assert result["group_by"] == grouping


def test_details_manager_lookup_is_limited_by_product_and_report_filters(monkeypatch):
    db = ManagerDb(["клиент а", "клиент б"])

    async def managers():
        return {"клиент а": "Иванов", "клиент б": "Петров"}

    monkeypatch.setattr(product_analytics, "get_client_managers", managers)
    data = request(managers=["Иванов"], departments=["ЦУМ"], search="Бокал")
    condition, _, _ = product_analytics._detail_product(data)
    clients = asyncio.run(product_analytics._detail_manager_clients(data, db, condition))

    sql = str(db.query.compile(dialect=postgresql.dialect()))
    assert "JOIN sale_items" in sql
    assert "lower(trim(sale_items.code))" in sql
    assert "sales.department IN" in sql
    assert "sale_items.name ILIKE" in sql
    assert clients == ["клиент а"]
