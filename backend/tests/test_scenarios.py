from datetime import date
from app.services.scenario_periods import report_period

def test_report_period_for_fourteenth():
 assert report_period(date(2026,9,14))==(date(2026,8,27),date(2026,9,13))

def test_report_period_for_twenty_eighth():
 assert report_period(date(2026,9,28))==(date(2026,9,14),date(2026,9,27))

def test_report_is_not_scheduled_on_other_days():
 assert report_period(date(2026,9,15)) is None
