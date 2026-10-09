def scenario_run_out(run, name: str) -> dict:
    """Формирует публичную запись истории без SMTP-паролей и других секретов."""
    return {"id": run.id, "scenario_id": run.scenario_id, "scenario_name": name,
            "run_date": run.run_date, "created_at": run.created_at, "run_type": run.run_type,
            "period_start": run.period_start, "period_end": run.period_end,
            "recipients": run.recipients, "email_status": run.status, "message": run.message}
