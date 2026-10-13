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
