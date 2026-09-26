from datetime import date

from sqlalchemy.dialects import postgresql

from app.routers.store_analytics_report import _sales


def test_sales_query_uses_one_grouped_row_per_check():
    query = _sales(date(2026, 9, 1), date(2026, 9, 30), [], None)
    sql = str(query.select().compile(dialect=postgresql.dialect()))

    assert "LEFT OUTER JOIN sale_items" in sql
    assert "GROUP BY sales.id" in sql
    assert "filtered_store_sales" not in sql


def test_sales_query_keeps_store_and_client_filters():
    query = _sales(
        date(2026, 9, 1),
        date(2026, 9, 30),
        ["Авиаторов"],
        ["Розничный клиент"],
    )
    compiled = query.select().compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "sales.department IN" in sql
    assert "lower(trim(sales.client)) IN" in sql
    assert date(2026, 9, 1) in compiled.params.values()
    assert date(2026, 9, 30) in compiled.params.values()
