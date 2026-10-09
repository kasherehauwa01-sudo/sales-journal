from datetime import datetime, timedelta

from app.services.scenario_periods import report_period

SCENARIO_RETRY_INTERVAL = timedelta(hours=1)


def scheduled_run_plan(now: datetime, latest):
    """Определяет первичный запуск либо почасовой повтор последней ошибки."""
    if latest and latest.status in {"failed", "running"}:
        # В новых схемах используется явное время последней попытки. Fallback
        # сохраняет совместимость с текущей таблицей без новой миграции.
        attempted_at = (
            getattr(latest, "last_attempt_at", None)
            or getattr(latest, "updated_at", None)
            or latest.created_at
        )
        if attempted_at:
            if attempted_at.tzinfo is None:
                attempted_at = attempted_at.replace(tzinfo=now.tzinfo)
            if now - attempted_at < SCENARIO_RETRY_INTERVAL:
                return None
        if latest.period_start and latest.period_end:
            return latest.run_date, (latest.period_start, latest.period_end)
    period = report_period(now.date())
    if period and (not latest or latest.run_date != now.date()):
        return now.date(), period
    return None
