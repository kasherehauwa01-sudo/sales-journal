"""Cross-repository contract + actual ASGI HTTP/SQL integration, no production access.

CALLTRACK_ROOT can point to a separate checkout in CI. Both repositories are required.
Only the directory source, integration token and DB connection are replaced by fixtures;
the production router, HTTP auth, request validation and aggregation execute unchanged.
"""
import asyncio
import os
import re
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import create_engine, event, text

from app.config import settings
from app.database import get_db
import app.main as main
from app.services import calltrack_dashboard as service


@pytest.fixture
def contract():
    root = Path(os.environ.get("CALLTRACK_ROOT", Path(__file__).resolve().parents[3] / "Calltrack"))
    assert (root / "api/dashboard_sales.php").exists(), "Set CALLTRACK_ROOT to the Calltrack checkout"
    source = (root / "api/dashboard_sales.php").read_text()
    match = re.search(r"salesJournalRequest\('([^']+)','([^']+)',\[([^]]+)\]\)", source)
    assert match, "Calltrack dashboard upstream call changed: review contract"
    method, url, fields = match.groups()
    assert set(re.findall(r"'([^']+)'\s*=>", fields)) == {"year", "managers"}
    transport = (root / "api/sales_journal.php").read_text()
    assert "'Authorization: Bearer '.$token" in transport
    return method, url


def test_http_contract_actual_router_and_sql(monkeypatch, contract):
    method, url = contract
    monkeypatch.setattr(settings, "calltrack_integration_token", "fixture-only-token")
    monkeypatch.setattr(main, "record_mtls_usage", AsyncMock())
    monkeypatch.setattr(service, "get_client_managers", AsyncMock(return_value={"one": "Alice", "two": "Bob"}))
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def functions(conn, _):
        conn.create_function("regexp_replace", 4, lambda v, p, r, f: re.sub(p, r, v or ""))

    async def run():
        with engine.begin() as conn:
            conn.execute(text("CREATE TABLE sales (id INTEGER, sale_date DATE, client TEXT, total_amount NUMERIC)"))
            conn.execute(text("CREATE TABLE sale_items (sale_id INTEGER, quantity NUMERIC)"))
            conn.execute(text("""INSERT INTO sales VALUES
                (1,'2026-01-01','One',100.25),(2,'2026-01-31','One',50),
                (3,'2026-12-31','Two',75),(4,'2025-12-31','One',900),
                (5,'2027-01-01','One',900),(6,'2026-02-01','One',-10)"""))
            conn.execute(text("INSERT INTO sale_items VALUES (1,2),(1,3),(2,1),(3,4),(6,-1)"))

            class DB:
                async def execute(self, query):
                    return conn.execute(query)

            async def db_override():
                yield DB()

            main.app.dependency_overrides[get_db] = db_override
            try:
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url="http://fixture") as client:
                    body = {"year": 2026, "managers": ["Alice"]}
                    headers = {"Authorization": "Bearer fixture-only-token"}
                    assert (await client.request(method, url, json=body)).status_code == 401
                    assert (await client.request(method, url, json=body, headers={"Authorization": "Bearer wrong"})).status_code == 401
                    result = await client.request(method, url, json=body, headers=headers)
                    assert result.status_code == 200, result.text
                    payload = result.json()
                    assert set(payload) == {"items"}
                    assert len(payload["items"]) == 12
                    assert payload["items"][0] == {"month": 1, "revenue": 150.25, "checks": 2, "units": 6}
                    assert payload["items"][1] == {"month": 2, "revenue": -10, "checks": 1, "units": -1}
                    assert payload["items"][11]["checks"] == 0
                    assert all(set(row) == {"month", "revenue", "checks", "units"} for row in payload["items"])

                    body["managers"] = ["Alice", "Bob"]
                    result = await client.request(method, url, json=body, headers=headers)
                    assert result.status_code == 200
                    assert result.json()["items"][11]["revenue"] == 75
                    body["managers"] = []
                    result = await client.request(method, url, json=body, headers=headers)
                    assert all(row["checks"] == 0 for row in result.json()["items"])
                    assert (await client.get(url, headers=headers)).status_code == 405
                    for invalid in [{"year": 1999, "managers": []}, {"year": 2026}, {"year": 2026, "managers": "Alice"}]:
                        assert (await client.request(method, url, json=invalid, headers=headers)).status_code == 422
                    # A missing client-manager directory is an error, never unscoped sales.
                    from app.services.clients_vr import ClientsVrError
                    monkeypatch.setattr(service, "get_client_managers", AsyncMock(side_effect=ClientsVrError("fixture unavailable")))
                    result = await client.request(method, url, json={"year": 2026, "managers": ["Alice"]}, headers=headers)
                    assert result.status_code == 503
            finally:
                main.app.dependency_overrides.pop(get_db, None)
        engine.dispose()

    asyncio.run(run())
