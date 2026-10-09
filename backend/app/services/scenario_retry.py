from datetime import datetime, timedelta

from app.services.scenario_periods import report_period

SCENARIO_RETRY_INTERVAL = timedelta(hours=1)


def scheduled_run_plan(now: datetime, latest):
    """Определяет первичный запуск либо почасовой повтор последней ошибки."""
    if latest and latest.status in {"failed", "running"}:
        created = latest.created_at
        if created:
            if created.tzinfo is None:
                created = created.replace(tzinfo=now.tzinfo)
            if now - created < SCENARIO_RETRY_INTERVAL:
                return None
        if latest.period_start and latest.period_end:
            return latest.run_date, (latest.period_start, latest.period_end)
    period = report_period(now.date())
    if period and (not latest or latest.run_date != now.date()):
        return now.date(), period
    return None
