from datetime import date
from app.services.seasonality import calculate_history
from test_seasonality import observations


def history(year, pattern, growth=1):
    return [
        row
        for row in observations(pattern, first_year=year, growth=growth)
        if row["year"] == year
    ]


def test_old_years_all_contribute_with_normalized_growth():
    data = []
    for year in range(2019, 2026):
        rows = history(year, [10] * 9 + [70, 90, 70])
        for row in rows:
            row["units"] *= year - 2018
        data += rows
    result = calculate_history(data, date(2026, 10, 13))
    assert result["pattern"] == "confirmed"
    assert result["years"] == list(range(2019, 2026))
    assert result["compared_pairs"] == 21
    assert result["start_month"] == 10 and result["peak_month"] == 11


def test_partial_years_compare_common_months_without_two_full_years():
    data = history(2025, [10] * 5 + [70, 90, 70] + [10] * 4) + history(
        2026, [10] * 5 + [70, 90, 70] + [10] * 4
    )
    result = calculate_history(data, date(2026, 10, 13))
    assert result["pattern"] == "confirmed"
    assert result["start_month"] == 6 and result["end_month"] == 8
    assert result["confidence"] <= 75
    assert result["indices"][10] is None


def test_one_year_and_short_overlap_are_insufficient():
    assert (
        calculate_history(history(2023, [20] * 12), date(2026, 10, 13))["pattern"]
        == "insufficient"
    )
    rows = history(2025, [10] * 9 + [70, 90, 70]) + history(
        2026, [10] * 9 + [70, 90, 70]
    )
    assert calculate_history(rows, date(2026, 5, 1))["pattern"] == "insufficient"


def test_flat_partial_years_and_single_burst_not_confirmed():
    data = history(2025, [20] * 12) + history(2026, [20] * 12)
    assert calculate_history(data, date(2026, 10, 13))["pattern"] == "none"
    data[10]["units"] = 1000
    assert calculate_history(data, date(2026, 10, 13))["pattern"] != "confirmed"


def test_cross_year_season_and_current_month_exclusion():
    rows = history(2023, [80, 80] + [10] * 9 + [80]) + history(
        2024, [80, 80] + [10] * 9 + [80]
    )
    result = calculate_history(rows, date(2026, 10, 13))
    assert result["pattern"] == "confirmed"
    assert result["start_month"] == 12 and result["end_month"] == 2
    current = history(2026, [20] * 12)
    assert max(calculate_history(current, date(2026, 1, 1))["years"], default=0) == 0


def test_recent_season_shift_has_more_weight_without_dropping_old_years():
    old = [70, 90, 70] + [10] * 9
    new = [10] * 5 + [70, 90, 70] + [10] * 4
    rows = sum([history(year, old) for year in range(2010, 2020)], [])
    rows += sum([history(year, new) for year in range(2020, 2026)], [])
    result = calculate_history(rows, date(2026, 10, 13))
    assert result["years"] == list(range(2010, 2026))
    assert result["pattern"] == "confirmed"
    assert result["start_month"] == 6
    annual = result["annual"]
    assert 0 < annual[0]["weight"] < annual[-1]["weight"]
    assert result["historical_indices"] != result["indices"]
    assert result["compared_pairs"] == 120


def test_two_independent_seasons_are_kept():
    pattern = [10, 80, 80, 10, 10, 10, 10, 10, 80, 80, 10, 10]
    result = calculate_history(
        history(2024, pattern) + history(2025, pattern), date(2026, 10, 13)
    )
    assert result["pattern"] == "confirmed"
    assert [window["months"] for window in result["windows"]] == [[2, 3], [9, 10]]


def test_recent_flat_demand_does_not_inherit_old_season_forever():
    rows = sum(
        [history(year, [70, 90, 70] + [10] * 9) for year in range(2010, 2024)], []
    )
    rows += history(2024, [20] * 12) + history(2025, [20] * 12)
    result = calculate_history(rows, date(2026, 10, 13))
    assert result["pattern"] == "none"
    assert result["historical_windows"]


def test_new_product_never_gets_zeros_before_appearance():
    rows = history(2025, [0] * 6 + [30] * 6) + history(2026, [30] * 12)
    result = calculate_history(rows, date(2026, 10, 13))
    assert result["pattern"] == "insufficient"
    assert result["annual"][0]["observed_months"] == list(range(7, 13))
    assert result["indices"][:6] == [None] * 6


def test_twenty_year_growth_is_normalized_and_trend_is_reported():
    rows = []
    for year in range(2006, 2026):
        part = history(year, [10] * 9 + [70, 90, 70])
        for item in part:
            item["units"] *= 1.1 ** (year - 2006)
        rows += part
    result = calculate_history(rows, date(2026, 10, 13))
    assert result["pattern"] == "confirmed"
    assert len(result["years"]) == 20
    assert abs(result["trend_percent"] - 10) < 1e-8


def test_unfinished_year_does_not_reduce_forecast_for_unobserved_peak():
    pattern = [10] * 9 + [70, 90, 70]
    rows = history(2024, pattern) + history(2025, pattern) + history(2026, pattern)
    result = calculate_history(rows, date(2026, 10, 13))
    assert result["pattern"] == "confirmed"
    assert abs(result["expected_units"][10] - 90) < 1e-8
    assert abs(result["expected_units"][0] - 10) < 1e-8
