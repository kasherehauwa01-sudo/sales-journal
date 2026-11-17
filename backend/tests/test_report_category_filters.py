from sqlalchemy import select
from sqlalchemy.dialects import postgresql

from app.models import Sale, SaleItem
from app.services.report_filters import (
    report_item_condition,
    report_sale_amount,
    report_sale_conditions,
)


def test_brand_and_category_apply_to_the_same_catalog_item():
    statement = select(SaleItem.id).where(
        report_item_condition([" Бренд "], ["Кружки", "Кружки", " Тарелки "])
    )
    compiled = statement.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert sql.count("EXISTS") == 1
    assert "legacy_category" in sql
    assert "subcategory" in sql
    assert "article" in sql and "code" in sql
    assert {key.rsplit("_", 1)[0]: value for key, value in compiled.params.items()
            if key.startswith(("report_categories_", "report_brands_"))} == {
        "report_categories": ["Кружки", "Тарелки"],
        "report_brands": ["бренд"],
    }


def test_category_limits_both_the_receipts_and_their_amount():
    statement = select(report_sale_amount(categories=["Кружки"])).select_from(Sale).where(
        *report_sale_conditions(categories=["Кружки"])
    )
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert sql.count("legacy_category") == 2
    assert "quantity" in sql and "actual_price" in sql
    assert report_sale_amount() is Sale.total_amount
    assert report_sale_conditions() == []
