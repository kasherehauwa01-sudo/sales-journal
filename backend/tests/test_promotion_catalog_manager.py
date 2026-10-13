from io import BytesIO
import pytest
from openpyxl import load_workbook
from app.routers.promotion_candidates_report import _promotion_projection
from app.services import promotion_selected_export as export


@pytest.mark.parametrize(
    "info,expected",
    [
        ({"Менеджер": "Иван"}, "Иван"),
        ({"properties": {"Менеджер": "Мария", "Менеджер ОЗ": "Другой"}}, "Мария"),
        ({"attributes": [{"name": "Менеджер", "value": "Анна"}]}, "Анна"),
        ({"Менеджер ОЗ": "Другой", "manager": "Клиентский"}, None),
        ({}, None),
    ],
)
def test_manager_comes_only_from_catalog_characteristic(info, expected):
    assert _promotion_projection(info)["catalog_manager"] == expected


def test_selected_excel_has_catalog_manager(monkeypatch):
    monkeypatch.setattr(export, "ensure_headroom", lambda: None)
    data = export.SelectedExport(
        rows=[{"key": "code:001", "name": "Товар", "catalog_manager": "Иван"}]
    )
    book = load_workbook(BytesIO(export.excel_selected(data).body), read_only=True)
    try:
        values = list(book["Выбранные товары"].values)
        assert values[0][-1] == "Менеджер" and values[1][-1] == "Иван"
    finally:
        book.close()
