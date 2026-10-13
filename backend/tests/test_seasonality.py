from datetime import date
import pytest
from app.services.seasonality import calculate, describe, recommend


def observations(pattern, *, growth=1, first_year=2024):
    return [
        {
            "year": year,
            "month": month,
            "units": units * (growth if year == first_year + 1 else 1),
            "positive_lines": 5,
            "first_sale": date(year, month, 1),
            "last_sale": date(year, month, 28),
        }
        for year in (first_year, first_year + 1)
        for month, units in enumerate(pattern, 1)
    ]


def seasonal():
    return calculate(observations([10] * 9 + [70, 90, 70]), 2024)


def test_repeated_pattern_and_growth_normalization():
    profile = seasonal()
    assert profile["pattern"] == "confirmed"
    assert (profile["start_month"], profile["peak_month"], profile["end_month"]) == (
        10,
        11,
        12,
    )
    assert 70 <= profile["confidence"] <= 85
    grown = calculate(observations([10] * 9 + [70, 90, 70], growth=5), 2024)
    assert grown["indices"] == profile["indices"]
    assert grown["expected_units"][10] == pytest.approx(
        profile["expected_units"][10] * 5
    )


@pytest.mark.parametrize("pattern", [[20] * 12, [10] * 11 + [500], [0] * 12])
def test_uniform_single_peak_and_zero(pattern):
    result = calculate(observations(pattern), 2024)
    assert result["pattern"] != "confirmed"
    assert (
        result["pattern"] == "insufficient"
        if not sum(pattern)
        else result["pattern"] == "none"
    )


def test_one_year_burst_and_weak_patterns_rejected():
    months = observations([20] * 12)
    months[10]["units"] = 500
    assert calculate(months, 2024)["pattern"] == "none"
    assert (
        calculate(observations([20, 21, 18, 19, 23, 18, 20, 21, 22, 19, 18, 20]), 2024)[
            "pattern"
        ]
        == "none"
    )


def test_december_january_wrap():
    result = calculate(
        observations([80, 10, 10, 10, 10, 10, 10, 10, 10, 10, 60, 90]), 2024
    )
    assert result["pattern"] == "confirmed"
    assert result["season_months"] == [11, 12, 1]
    assert describe(result, date(2026, 1, 15))["status"] == "Сезон сейчас"
    assert describe(result, date(2026, 10, 1))["status"] == "Скоро сезон"
    assert describe(result, date(2026, 6, 1))["status"] == "Вне сезона"


def test_partial_missing_and_irregular_history():
    assert (
        calculate(observations([10] * 9 + [70, 90, 70])[:12], 2024)["pattern"]
        == "insufficient"
    )
    assert (
        calculate(observations([10] * 9 + [70, 90, 70]), 2024, complete_history=False)[
            "pattern"
        ]
        == "insufficient"
    )
    assert (
        calculate(observations([0] * 9 + [70, 90, 70]), 2024)["pattern"]
        == "insufficient"
    )


def test_statuses_horizon_and_none():
    profile = seasonal()
    assert describe(profile, date(2026, 9, 1))["status"] == "Скоро сезон"
    assert describe(profile, date(2026, 8, 1))["status"] == "Вне сезона"
    assert describe(profile, date(2026, 10, 12))["status"] == "Сезон сейчас"
    assert (
        describe(calculate(observations([20] * 12), 2024), date(2026, 1, 1))["status"]
        == "Сезонность не выявлена"
    )
    assert describe(None, date(2026, 1, 1))["status"] == "Недостаточно данных"


def test_priority_no_automatic_discount_scope_and_stock():
    profile = seasonal()
    criteria = {"min_stock": 1}
    row = {
        "stock": 100,
        "reasons": ["Продажи падают"],
        "status": "Кандидат",
        "units_30": 1,
        "stop_factors": [],
    }
    result = recommend(row, profile, criteria, date(2026, 10, 12))
    assert result["priority"] == 95
    assert (
        recommend(row, profile, criteria, date(2026, 10, 12), comparable_scope=False)[
            "priority"
        ]
        == 80
    )
    assert (
        recommend({**row, "stock": 0}, profile, criteria, date(2026, 10, 12))[
            "priority"
        ]
        == 0
    )
    assert (
        recommend({**row, "stock": None}, profile, criteria, date(2026, 10, 12))[
            "priority"
        ]
        == 0
    )
    assert (
        recommend(
            {**row, "reasons": [], "status": "Нет сигналов"},
            profile,
            criteria,
            date(2026, 10, 12),
        )["priority"]
        == 40
    )
    assert (
        "без обязательной скидки"
        in recommend(
            {**row, "reasons": [], "status": "Нет сигналов"},
            profile,
            criteria,
            date(2026, 10, 12),
        )["action"]
    )
    assert (
        recommend(
            {**row, "reasons": ["Избыточный запас"]},
            profile,
            criteria,
            date(2026, 12, 12),
        )["priority"]
        == 100
    )
    assert (
        recommend(row, profile, criteria, date(2026, 9, 1))["priority"] == 80
    )  # pre-season volume is not underperformance


def test_two_seasons_and_repeated_single_month():
    assert calculate(observations([10] * 11 + [60]), 2024)["pattern"] == "none"
    profile = calculate(
        observations([60, 60, 10, 10, 60, 60, 10, 10, 10, 10, 10, 10]), 2024
    )
    assert profile["pattern"] == "confirmed" and len(profile["windows"]) == 2
    assert describe(profile, date(2026, 5, 15))["start_month"] == 5
    assert describe(profile, date(2026, 4, 1))["status"] == "Скоро сезон"


def test_negative_month_and_late_assortment_are_insufficient():
    pattern = [10] * 9 + [70, 90, 70]
    pattern[0] = -1
    assert calculate(observations(pattern), 2024)["pattern"] == "insufficient"
    rows = observations([10] * 9 + [70, 90, 70])[4:]
    assert calculate(rows, 2024)["pattern"] == "insufficient"


def test_failed_import_history_is_not_confirmed():
    rows = observations([10] * 9 + [70, 90, 70])
    rows[0]["incomplete_source"] = True
    assert calculate(rows, 2024)["pattern"] == "insufficient"


def test_goods_arriving_after_january_do_not_have_full_two_year_history():
    months = [
        row
        for row in observations([10] * 9 + [70, 90, 70])
        if not (row["year"] == 2024 and row["month"] == 1)
    ]
    assert calculate(months, 2024)["pattern"] == "insufficient"


def test_growth_stop_recommends_promotion_but_not_discount():
    result = recommend(
        {
            "stock": 100,
            "reasons": [],
            "status": "Не рекомендуется сейчас",
            "units_30": 100,
            "stop_factors": ["Продажи растут без скидки"],
        },
        seasonal(),
        {"min_stock": 1},
        date(2026, 10, 12),
    )
    assert result["priority"] == 40 and "без обязательной скидки" in result["action"]
