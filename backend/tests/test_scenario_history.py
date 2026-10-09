from datetime import date, datetime, timezone
from types import SimpleNamespace


def test_scenario_history_exposes_email_status_and_timestamp():
    from app.services.scenario_history import scenario_run_out

    created = datetime(2026, 10, 9, 8, 30, tzinfo=timezone.utc)
    run = SimpleNamespace(id=7, scenario_id=2, run_date=date(2026, 10, 9),
        created_at=created, run_type="scheduled", period_start=date(2026, 9, 25),
        period_end=date(2026, 10, 8), recipients="user@example.test",
        status="completed", message="Отчет отправлен")
    result = scenario_run_out(run, "HoReCa")
    assert result["scenario_name"] == "HoReCa"
    assert result["created_at"] == created
    assert result["email_status"] == "completed"
