from datetime import date
import pytest
from app.routers.promotion_candidates_report import (
    _aviatorov_stock,
    _promotion_projection,
    Request,
)
from app.services.promotion_candidates import classify_candidate
from test_promotion_candidates import row, SETTINGS


@pytest.mark.parametrize(
    "values,expected",
    [
        (
            [
                {"warehouse": "Авиаторов Зал+Склад", "quantity": 3},
                {"warehouse": "Другой", "quantity": 100},
            ],
            3,
        ),
        ({"Авиаторов Зал+Склад": {"quantity": 4}, "Другой": 99}, 4),
        ({" АВИАТОРОВ  ЗАЛ+СКЛАД ": 5}, 5),
        ([{"name": "Авиаторов Зал+Склад", "stock_quantity": 0}], 0),
        (
            [
                {"warehouse": "Авиаторов Зал+Склад", "quantity": 2},
                {"warehouse": "Авиаторов Зал+Склад", "value": 3},
            ],
            5,
        ),
        ([{"warehouse": "Другой", "quantity": 10}], None),
        ([{"warehouse": "Авиаторов Зал+Склад", "quantity": "nan"}], None),
        ([{"warehouse": "Авиаторов Зал+Склад", "quantity": "invalid"}], None),
    ],
)
def test_named_warehouse_only(values, expected):
    assert _aviatorov_stock({"stocks": values}) == expected


def test_minimum_uses_named_stock_and_can_be_disabled():
    classify = lambda stock, minimum: classify_candidate(
        row(stock=500, aviatorov_stock=stock),
        {**SETTINGS, "min_stock": minimum},
        date(2026, 9, 30),
    )
    assert "Маленький остаток" in classify(2, 3)["stop_factors"]
    assert "Маленький остаток" not in classify(3, 3)["stop_factors"]
    assert "Маленький остаток" not in classify(0, None)["stop_factors"]
    assert "Маленький остаток" not in classify(None, 3)["stop_factors"]
    assert (
        Request(
            date_from=date(2026, 9, 1),
            date_to=date(2026, 9, 30),
            criteria={"min_stock": None},
        ).criteria.min_stock
        is None
    )


def test_total_stock_is_preserved():
    result = _promotion_projection(
        {
            "stocks": [
                {"warehouse": "Авиаторов Зал+Склад", "quantity": 2},
                {"warehouse": "Другой", "quantity": 10},
            ]
        }
    )
    assert result["stock"] == 12 and result["aviatorov_stock"] == 2


def test_excel_contains_named_warehouse_quantity():
    from io import BytesIO
    from openpyxl import load_workbook
    from app.routers.promotion_candidates_report import _excel

    item = dict(
        status="Кандидат",
        revenue=10,
        units=1,
        category_change_percent=0,
        name="Товар",
        code="001",
        article="01",
        category="Категория",
        brand="Бренд",
        manufacturer="Завод",
        reasons=[],
        units_30=1,
        units_previous_30=2,
        units_change_percent=-50,
        stock=12,
        stock_months=1,
        days_without_sales=1,
        aviatorov_stock=2,
    )
    book = load_workbook(BytesIO(_excel([item]).body), read_only=True)
    try:
        values = list(book["Кандидаты"].values)
        assert values[0][-1] == "Остатки Авиаторов Зал+Склад"
        assert values[1][-1] == 2
    finally:
        book.close()
