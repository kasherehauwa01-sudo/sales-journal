from datetime import date

from app.services.rfm import SegmentSettings, enrich, segment_client, tied_quintiles


def row(**overrides):
    value = {"r_score": 3, "f_score": 3, "m_score": 3, "recency_days": 20,
             "frequency": 3, "first_purchase_days": 100, "cycle_overdue": 1}
    value.update(overrides)
    return value


def test_quintiles_keep_equal_values_together_and_are_deterministic():
    values = [1, 1, 1, 2, 3, 4, 5]
    first, bounds = tied_quintiles(values)
    second, _ = tied_quintiles(values)
    assert first == second
    assert len(set(first[:3])) == 1
    assert bounds


def test_small_sample_uses_valid_scores():
    scores, _ = tied_quintiles([10, 20])
    assert all(1 <= score <= 5 for score in scores)
    assert scores[1] > scores[0]


def test_recency_direction_is_reversed():
    scores, _ = tied_quintiles([1, 100], high_is_good=False)
    assert scores[0] > scores[1]


def test_new_requires_recent_first_purchase_not_only_one_check():
    settings = SegmentSettings()
    assert segment_client(row(frequency=1, first_purchase_days=10, r_score=5), settings)[0] == "Новые"
    assert segment_client(row(frequency=1, first_purchase_days=300, f_score=1, m_score=2), settings)[0] == "Редко покупающие"


def test_risk_segments_have_priority_and_keep_value_tags():
    settings = SegmentSettings()
    segment, tags = segment_client(row(recency_days=60, cycle_overdue=2, f_score=5, m_score=5), settings)
    assert segment == "Засыпающие"
    assert tags == ["Крупный клиент", "Высокая частота покупок"]
    segment, tags = segment_client(row(recency_days=200, cycle_overdue=4, f_score=5, m_score=5), settings)
    assert segment == "Потерянные" and "Крупный клиент" in tags


def test_core_marketing_segments():
    settings = SegmentSettings()
    assert segment_client(row(r_score=5, f_score=5, m_score=4), settings)[0] == "Постоянные активные"
    assert segment_client(row(r_score=5, f_score=3, frequency=2), settings)[0] == "Перспективные"
    assert segment_client(row(r_score=3, f_score=3, m_score=5), settings)[0] == "Крупные клиенты"
    assert segment_client(row(r_score=3, f_score=1, m_score=2, frequency=1), settings)[0] == "Редко покупающие"


def test_enrich_calculates_codes_and_days_from_analysis_end():
    rows = [{"recency_days": 10, "frequency": 2, "monetary": 100.0,
             "first_purchase": date(2026, 9, 1), "cycle_overdue": None}]
    result, boundaries = enrich(rows, date(2026, 9, 30), SegmentSettings())
    assert result[0]["first_purchase_days"] == 29
    assert len(result[0]["rfm_code"]) == 3
    assert set(boundaries) == {"r", "f", "m"}
