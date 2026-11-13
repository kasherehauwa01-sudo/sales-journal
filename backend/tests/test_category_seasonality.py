from datetime import date
from decimal import Decimal as D
from app.services.category_seasonality import branch, monthly_coverage, profile
from app.routers.category_seasonality_report import Request
import pytest


def values(peak=None, scale=1):
    return [D((50 if m == peak else 10) * scale) for m in range(12)]


def test_two_subcategories_and_equal_names_in_different_parents_are_independent():
    a, b, c = (
        branch(1, "Посуда", "Чашки", "legacy"),
        branch(1, "Посуда", "Тарелки", None),
        branch(2, "Декор", "Чашки", None),
    )
    assert len({a["key"], b["key"], c["key"]}) == 3
    assert a["parent_key"] == b["parent_key"] != c["parent_key"]
    assert (
        branch(None, "Выдуманный родитель", "Чашки", None)["parent"]
        == "Без данных об иерархии"
    )
    assert (
        branch(1, "Посуда", None, "Плоский раздел")["parent"]
        == "Без данных об иерархии"
    )


def test_recurring_peaks_scale_invariance_and_different_children():
    coverage = {y: [True] * 12 for y in [2023, 2024, 2025]}
    april = profile(
        {y: values(3, scale) for y, scale in zip(coverage, [1, 2, 8])}, coverage
    )
    july = profile({y: values(6) for y in coverage}, coverage)
    assert april["peaks"] == [dict(month=4, count=3, total=3, repeating=True)]
    assert july["peaks"][0]["month"] == 7
    assert april["indices"][3] == pytest.approx(275)


def test_spike_flat_negative_zero_incomplete_and_single_year():
    cov = {y: [True] * 12 for y in [2023, 2024, 2025]}
    assert not profile({2023: values(3), 2024: values(), 2025: values()}, cov)["peaks"]
    assert (
        profile({2023: values()}, cov)["message"]
        == "Недостаточно данных для оценки повторяемости"
    )
    assert profile({2023: [D(0)] * 12}, cov)["indices"] is None
    assert profile({2023: [D(-1)] * 12}, cov)["indices"] is None
    cov[2023][11] = False
    p = profile({2023: values(3)}, cov)
    assert p["indices"] is None and not p["annual"][0]["included"]
    assert p["annual"][0]["values"][3] == 50


def test_equal_peaks_and_returns_and_two_thirds_boundary():
    nums = [D(10)] * 12
    nums[0] = nums[11] = D(40)
    p = profile({2023: nums, 2024: nums}, {2023: [True] * 12, 2024: [True] * 12})
    assert [v["month"] for v in p["peaks"]] == [1, 12]
    nums[4] = D(-2)
    p = profile({2023: nums}, {2023: [True] * 12})
    assert p["annual"][0]["values"][4] == -2
    assert p["annual"][0]["indices"][4] < -100
    vs = {2022: values(3), 2023: values(3), 2024: values()}
    assert profile(vs, {y: [True] * 12 for y in vs})["peaks"][0]["repeating"]


def test_confirmed_zero_requires_whole_store_month_coverage_and_leap_day():
    years = [2024]
    cov = monthly_coverage(
        years,
        ["A", "B"],
        [
            (date(2024, 1, 1), date(2024, 12, 31), ["A"]),
            (date(2024, 1, 1), date(2024, 2, 28), ["B"]),
            (date(2024, 3, 1), date(2024, 12, 31), ["B"]),
        ],
    )
    assert cov[2024][0] and not cov[2024][1] and cov[2024][2]
    assert not any(monthly_coverage(years, [], [])[2024])
    assert not any(monthly_coverage(years, ["new"], [])[2024])
    p = profile({2024: [D(0)] + [D(10)] * 11}, {2024: [True] * 12})
    assert p["indices"][0] == -100


def test_old_product_seasonality_not_reintroduced():
    import app.main
    from app.routers.report_pdf import REPORTS

    assert "/api/reports/category-seasonality" in {r.path for r in app.main.app.routes}
    assert "/api/reports/seasonality" not in {r.path for r in app.main.app.routes}
    assert "category-seasonality" not in REPORTS
    assert Request(years=[2023]).stores == []


@pytest.mark.parametrize("years", [[2026], [2025, 2027], [1899]])
def test_current_incomplete_and_future_years_are_rejected_before_sql(years):
    import asyncio
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.services.category_seasonality import build

    with pytest.raises(HTTPException) as exc:
        asyncio.run(build(AsyncMock(), Request(years=years), date(2026, 11, 13)))
    assert exc.value.status_code == 422


def test_client_directory_failure_is_an_error_not_empty_report(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from fastapi import HTTPException
    from app.services.clients_vr import ClientsVrError
    from app.services import category_seasonality as service

    monkeypatch.setattr(
        service,
        "get_sales_filter_clients",
        AsyncMock(side_effect=ClientsVrError("private")),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            service.build(
                AsyncMock(), Request(years=[2025], manager=["M"]), date(2026, 11, 13)
            )
        )
    assert exc.value.status_code == 502 and "private" not in exc.value.detail


def test_threshold_is_inclusive_and_two_of_four_is_not_repeating():
    nums = [D(12), D(8)] + [D(10)] * 10
    p = profile({2023: nums, 2024: nums}, {2023: [True] * 12, 2024: [True] * 12})
    assert p["indices"][0] == 20 and p["peaks"][0]["repeating"]
    v = {2022: values(3), 2023: values(3), 2024: values(), 2025: values()}
    p = profile(v, {y: [True] * 12 for y in v})
    assert (
        p["peaks"][0]["count"] == 2
        and p["peaks"][0]["total"] == 4
        and not p["peaks"][0]["repeating"]
    )
