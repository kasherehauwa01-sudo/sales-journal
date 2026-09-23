from datetime import date

from app.services.sales_dynamics_report import calculated_metrics, default_grouping, metric_comparison, previous_period


def test_custom_previous_period_has_same_duration():
    assert previous_period(date(2026,9,1),date(2026,9,30),"custom")== (date(2026,8,2),date(2026,8,31))


def test_calendar_week_uses_previous_full_week():
    assert previous_period(date(2026,9,21),date(2026,9,23),"current_week")== (date(2026,9,14),date(2026,9,20))


def test_calendar_month_uses_previous_full_month():
    assert previous_period(date(2026,9,1),date(2026,9,23),"current_month")== (date(2026,8,1),date(2026,8,31))


def test_metric_change_and_zero_previous_value():
    assert metric_comparison(120,100)=={"current":120,"previous":100,"difference":20,"change_percent":20}
    assert metric_comparison(10,0)["change_percent"] is None


def test_average_metrics_and_empty_period():
    assert calculated_metrics(1000,4,10)=={"revenue":1000,"sales_count":4,"items_count":10,"average_check":250,"average_items_per_sale":2.5}
    assert calculated_metrics(0,0,0)=={"revenue":0,"sales_count":0,"items_count":0,"average_check":0,"average_items_per_sale":0}


def test_default_grouping_for_days_weeks_and_months():
    assert default_grouping(date(2026,9,1),date(2026,9,30))=="day"
    assert default_grouping(date(2026,1,1),date(2026,4,30))=="week"
    assert default_grouping(date(2025,1,1),date(2026,1,1))=="month"
