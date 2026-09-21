from datetime import date
from app.services.scenario_periods import manual_test_period,months_before,report_period

def test_report_period_for_fourteenth():
 assert report_period(date(2026,9,14))==(date(2026,8,27),date(2026,9,13))

def test_report_period_for_twenty_eighth():
 assert report_period(date(2026,9,28))==(date(2026,9,14),date(2026,9,27))

def test_report_is_not_scheduled_on_other_days():
 assert report_period(date(2026,9,15)) is None

def test_three_month_period_uses_calendar_months():
 assert months_before(date(2026,3,31),3)==date(2025,12,31)

def test_manual_test_period_is_exactly_fourteen_calendar_days():
 assert manual_test_period(date(2026,9,21))==(date(2026,9,8),date(2026,9,21))
