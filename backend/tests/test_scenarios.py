from datetime import date,datetime,timedelta,timezone
from types import SimpleNamespace
from app.services.scenario_periods import manual_test_period,months_before,report_period
from app.services.scenario_retry import scheduled_run_plan

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

def test_failed_scenario_is_retried_after_one_hour_with_original_period():
 now=datetime(2026,9,29,1,5,tzinfo=timezone.utc)
 latest=SimpleNamespace(status="failed",created_at=now-timedelta(hours=1),run_date=date(2026,9,28),period_start=date(2026,9,14),period_end=date(2026,9,27))
 assert scheduled_run_plan(now,latest)==(date(2026,9,28),(date(2026,9,14),date(2026,9,27)))

def test_failed_scenario_is_not_retried_before_one_hour():
 now=datetime(2026,9,28,0,35,tzinfo=timezone.utc)
 latest=SimpleNamespace(status="failed",created_at=now-timedelta(minutes=30),run_date=date(2026,9,28),period_start=date(2026,9,14),period_end=date(2026,9,27))
 assert scheduled_run_plan(now,latest) is None

def test_retry_interval_uses_last_attempt_instead_of_creation_time():
 now=datetime(2026,9,28,2,5,tzinfo=timezone.utc)
 latest=SimpleNamespace(status="failed",created_at=now-timedelta(days=1),updated_at=now-timedelta(minutes=30),run_date=date(2026,9,28),period_start=date(2026,9,14),period_end=date(2026,9,27))
 assert scheduled_run_plan(now,latest) is None

def test_failed_scenario_is_retried_one_hour_after_last_attempt():
 now=datetime(2026,9,28,2,5,tzinfo=timezone.utc)
 latest=SimpleNamespace(status="failed",created_at=now-timedelta(days=1),last_attempt_at=now-timedelta(hours=1),run_date=date(2026,9,28),period_start=date(2026,9,14),period_end=date(2026,9,27))
 assert scheduled_run_plan(now,latest)==(date(2026,9,28),(date(2026,9,14),date(2026,9,27)))

def test_running_scenario_is_not_started_concurrently_before_retry_interval():
 now=datetime(2026,9,28,1,5,tzinfo=timezone.utc)
 latest=SimpleNamespace(status="running",created_at=now-timedelta(hours=3),updated_at=now-timedelta(minutes=5),run_date=date(2026,9,28),period_start=date(2026,9,14),period_end=date(2026,9,27))
 assert scheduled_run_plan(now,latest) is None

def test_successful_scenario_is_not_repeated_for_same_schedule_date():
 now=datetime(2026,9,28,5,5,tzinfo=timezone.utc)
 latest=SimpleNamespace(status="completed",created_at=now-timedelta(hours=4),run_date=date(2026,9,28),period_start=date(2026,9,14),period_end=date(2026,9,27))
 assert scheduled_run_plan(now,latest) is None
