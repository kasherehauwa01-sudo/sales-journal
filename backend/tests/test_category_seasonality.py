from collections import defaultdict
from datetime import datetime, date, timezone
from decimal import Decimal as D
from app.services.category_seasonality import branch, coverage_method, seasonal_profile
from app.services.category_seasonality_snapshot import previous_slot, next_slot, cutoff


def test_store_month_regularities_not_shop_names():
    assert coverage_method(14, 30) == "excluded"
    assert coverage_method(18, 31) == "excluded"
    assert coverage_method(1, 30) == "excluded"
    assert coverage_method(27, 30) == "estimated"
    assert coverage_method(28, 31) == "estimated"
    assert coverage_method(0, 31, True) == "confirmed"


def test_partial_year_current_completed_months_and_equal_calendar_weights():
    cov = {
        ("A", y, m): "confirmed"
        for y, months in [
            (2024, range(9, 13)),
            (2025, range(1, 13)),
            (2026, range(1, 11)),
        ]
        for m in months
    }
    facts = {k: (D(50) if k[2] == 4 else D(10), 1) for k in cov}
    p = seasonal_profile(facts, cov)
    assert p["peaks"] == [4] and 2026 in p["cells"][3]["years"]
    assert p["cells"][8]["index"] == -25 and p["cells"][3]["index"] == 275
    assert p["cells"][10]["years"] == [2024, 2025]
    assert cutoff(datetime(2026, 11, 1, 21, tzinfo=timezone.utc)) == date(2026, 11, 1)


def test_unknown_vs_confirmed_zero_and_one_day_not_trough():
    cov = {("A", 2025, m): "confirmed" for m in [1, 2, 3]}
    cov[("A", 2025, 4)] = "excluded"
    facts = {
        ("A", 2025, 1): (D(10), 1),
        ("A", 2025, 2): (D(20), 1),
        ("A", 2025, 4): (D(1), 1),
    }
    p = seasonal_profile(facts, cov)
    assert p["troughs"] == [3] and p["cells"][3]["index"] is None
    cov[("A", 2025, 3)] = "estimated"
    p = seasonal_profile(facts, cov)
    assert (
        p["cells"][2]["index"] is None
        and p["cells"][2]["reason"] == "Нет подтверждённого нуля"
    )


def test_store_normalization_prevents_new_store_turnover_peak():
    cov = {("A", 2024, m): "confirmed" for m in range(1, 13)} | {
        ("B", 2025, m): "estimated" for m in [3, 4]
    }
    f = {k: (D(1000 if k[0] == "B" else 10), 1) for k in cov}
    p = seasonal_profile(f, cov)
    assert all(c["index"] == 0 for c in p["cells"]) and p["estimated"]
    assert p["cells"][0]["stores"] == ["A"] and p["cells"][2]["stores"] == ["A", "B"]
    assert not p["peaks"] and not p["troughs"]


def test_one_month_nonpositive_returns_and_equal_extremes():
    cov = {("A", 2025, m): "confirmed" for m in range(1, 5)}
    f = {("A", 2025, m): (D(v), 1) for m, v in enumerate([30, 30, 10, 10], 1)}
    p = seasonal_profile(f, cov)
    assert p["peaks"] == [1, 2] and p["troughs"] == [3, 4]
    assert "Данные за один год" in p["cells"][0]["notes"]
    assert "База по доступным месяцам" in p["cells"][0]["notes"]
    assert (
        seasonal_profile({("A", 2025, 1): (D(10), 1)}, {("A", 2025, 1): "confirmed"})[
            "cells"
        ][0]["index"]
        is None
    )
    for v in [0, -1]:
        p = seasonal_profile({k: (D(v), 1) for k in cov}, cov)
        assert all(c["index"] is None for c in p["cells"])
    p = seasonal_profile(
        {("A", 2025, 1): (D(-2), 1), ("A", 2025, 2): (D(10), 1)},
        {k: "confirmed" for k in [("A", 2025, 1), ("A", 2025, 2)]},
    )
    assert p["cells"][0]["index"] == -150


def test_schedule_moscow_saturday_19_and_month_boundary():
    before = datetime(2026, 11, 14, 15, 59, tzinfo=timezone.utc)
    due = datetime(2026, 11, 14, 16, tzinfo=timezone.utc)
    assert next_slot(before).astimezone(timezone.utc) == due
    assert previous_slot(due).astimezone(timezone.utc) == due
    assert next_slot(due).date() == date(2026, 11, 21)
    assert cutoff(datetime(2026, 10, 31, 21, tzinfo=timezone.utc)) == date(2026, 11, 1)


def test_hierarchy_keys_and_no_legacy_algorithms():
    assert branch(1, "A", "X", None)["key"] != branch(2, "B", "X", None)["key"]
    assert branch(None, "A", "X", None)["parent"] == "Без данных об иерархии"
    import app.main

    assert "/api/reports/seasonality" not in {r.path for r in app.main.app.routes}


def test_two_children_have_distinct_peaks_and_troughs_and_before_rounding_extrema():
    coverage = {("A", 2025, m): "confirmed" for m in range(1, 13)}

    def facts(peak, trough):
        return {
            k: (D(50) if k[2] == peak else D(1) if k[2] == trough else D(10), 1)
            for k in coverage
        }

    april = seasonal_profile(facts(4, 2), coverage)
    july = seasonal_profile(facts(7, 3), coverage)
    assert april["peaks"] == [4] and april["troughs"] == [2]
    assert july["peaks"] == [7] and july["troughs"] == [3]
    # Values that round to the same displayed percentage still have one maximum.
    f = {k: (D(10), 1) for k in coverage}
    f[("A", 2025, 4)] = (D("10.00001"), 1)
    assert seasonal_profile(f, coverage)["peaks"] == [4]


def test_observed_estimated_zero_with_lines_is_not_an_unknown_month():
    c = {("A", 2025, 1): "estimated", ("A", 2025, 2): "confirmed"}
    p = seasonal_profile({("A", 2025, 1): (D(0), 1), ("A", 2025, 2): (D(10), 1)}, c)
    assert p["cells"][0]["index"] == -100 and p["cells"][0]["estimated"]
