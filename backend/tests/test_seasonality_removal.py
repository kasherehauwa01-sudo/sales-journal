"""Removal contracts: no report/calculation; shared source freshness survives."""

import asyncio
import os
from pathlib import Path
import subprocess
import sys
import pytest
from pydantic import ValidationError
from app.models import Base, SalesSourceState
from app.services import sales_revision, source_control
from app.routers import promotion_candidates_report as promotion, report_pdf
from test_catalog_directory import database


def test_removed_routes_and_pdf_report():
    import httpx
    import app.main

    assert not any("/reports/seasonality" in r.path for r in app.main.app.routes)
    assert "seasonality" not in report_pdf.REPORTS

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app.main.app.router),
            base_url="http://test",
        ) as client:
            for path in (
                "/api/reports/seasonality",
                "/api/reports/seasonality/any-product",
            ):
                assert (await client.get(path)).status_code == 404

    asyncio.run(run())


@pytest.mark.parametrize(
    "key,value",
    [
        ("use_seasonality", True),
        ("use_seasonality", False),
        ("seasonality_status", "Сезон сейчас"),
    ],
)
def test_legacy_seasonal_requests_are_rejected_instead_of_misleading_results(
    key, value
):
    with pytest.raises(ValidationError):
        promotion.Request.model_validate(
            {"date_from": "2026-09-01", "date_to": "2026-09-30", key: value}
        )


@pytest.mark.parametrize("command", ["run", "scheduled", "worker", "enable", "rebuild"])
def test_retired_cli_fails_without_connecting_to_database(command):
    root = Path(__file__).parents[1]
    result = subprocess.run(
        [sys.executable, "-m", "app.seasonality", command],
        cwd=root,
        env=dict(os.environ, PYTHONPATH=str(root), DATABASE_URL="must-not-connect"),
        capture_output=True,
        text=True,
        timeout=5,
    )
    assert result.returncode != 0 and "удалена" in result.stderr
    assert "Traceback" not in result.stderr


def test_source_revision_increment_preserves_shared_state_and_does_not_schedule_calculation():
    assert not {
        "seasonality_months",
        "seasonality_store_months",
        "seasonality_profiles",
    } & set(Base.metadata.tables)
    assert SalesSourceState.__tablename__ == "seasonality_state"

    async def run():
        async with database([]) as (_, factory):
            async with factory() as db:
                db.add(
                    SalesSourceState(
                        id=1,
                        source_revision=41,
                        enabled=False,
                        rebuild_required=False,
                        generation=9,
                    )
                )
                await db.commit()
                await sales_revision.changed(db)
                await db.commit()
                row = await db.get(SalesSourceState, 1)
                assert row.source_revision == 42
                assert (
                    row.enabled is False
                    and row.rebuild_required is False
                    and row.generation == 9
                )
                await sales_revision.changed(db)
                await db.rollback()
                row = await db.get(SalesSourceState, 1)
                assert row.source_revision == 42

    asyncio.run(run())


def test_reviewed_updater_imports_preserve_exact_shared_lock_ids():
    import app.seasonality
    from app.services import seasonality_worker as shim

    assert (shim.SOURCE_LOCK, shim.WORKER_LOCK, shim.CATALOG_LOCK) == (
        734190220,
        734190221,
        734190117,
    )
    assert (
        shim.advisory is source_control.advisory
        and shim.verify is source_control.verify
    )
    assert not hasattr(shim, "run_batch") and not hasattr(app.seasonality, "worker")
