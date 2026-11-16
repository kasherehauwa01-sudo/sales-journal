import logging
from unittest.mock import AsyncMock
import pytest
import sqlalchemy as sa
from app.services import category_seasonality_snapshot as service
from app import category_snapshot_diagnostics as diagnostics


def test_sqlstate_never_stringifies_sql_parameters():
    class PrivateError(Exception):
        sqlstate = "57014"

        def __str__(self):
            raise AssertionError("Business data must not be stringified")

    error = sa.exc.DBAPIError(
        "PRIVATE SQL", {"client": "PRIVATE CLIENT"}, PrivateError(), False
    )
    assert service.sqlstate(error) == "57014"
    PrivateError.sqlstate = "private data"
    assert service.sqlstate(error) is None


def test_stage_logs_only_stage_and_time(caplog):
    token = service._BUILD_STAGE.set(("settings", None))
    try:
        with caplog.at_level(logging.INFO):
            service.set_build_stage("facts")
            service.set_build_stage("coverage")
        assert "stage=facts" in caplog.text and "duration_ms=" in caplog.text
    finally:
        service._BUILD_STAGE.reset(token)


def test_facts_query_preserves_original_sql_and_rejects_identifiers():
    import ast, subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    original = subprocess.check_output(
        [
            "git",
            "show",
            "6c2de638d47736262c50fde0f1c12b69e06ed765:backend/app/services/category_seasonality_snapshot.py",
        ],
        cwd=root,
        text=True,
    )
    tree = ast.parse(original)
    query = next(
        n
        for n in ast.walk(tree)
        if isinstance(n, ast.JoinedStr)
        and any(
            isinstance(v, ast.Constant)
            and isinstance(v.value, str)
            and "INSERT INTO category_season_facts" in v.value
            for v in n.values
        )
    )
    pairs = "cs_pairs_" + "a" * 32
    expected = "".join(
        v.value if isinstance(v, ast.Constant) else pairs for v in query.values
    )
    assert str(service.facts_statement(pairs)) == expected
    for bad in ("sales", "cs_pairs_x;DROP TABLE sales", "cs_pairs_" + "g" * 32):
        with pytest.raises(ValueError):
            service.facts_statement(bad)


def test_metadata_only_readonly_and_no_business_rows():
    import asyncio

    class Context:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def begin(self):
            return self

        async def execute(self, statement, params=None):
            sql = str(statement)
            statements.append(sql)
            assert not any(
                x in sql.upper()
                for x in (
                    "EXPLAIN",
                    "ANALYZE ",
                    "INSERT ",
                    "CREATE ",
                    "SUM(",
                    "MOST_COMMON_VALS",
                    "HISTOGRAM_BOUNDS",
                )
            )
            return self

        def mappings(self):
            return self

        def all(self):
            return []

        def one(self):
            return {"temp_files": 0, "temp_bytes": 0, "stats_reset": None}

    statements = []

    class Engine:
        def connect(self):
            return Context()

    result = asyncio.run(diagnostics.collect(Engine()))
    assert statements[0] == "SET TRANSACTION READ ONLY"
    assert result["mode"] == "metadata_only"


def test_cli_failure_contains_sqlstate_without_sql_or_client(monkeypatch):
    from app import category_seasonality as cli

    class DriverError(Exception):
        sqlstate = "57014"

    error = sa.exc.DBAPIError(
        "PRIVATE SQL",
        {"client": "PRIVATE CLIENT"},
        DriverError("PRIVATE MESSAGE"),
        False,
    )
    monkeypatch.setattr(cli, "engine", type("Engine", (), {"dispose": AsyncMock()})())
    monkeypatch.setattr(cli, "rebuild", AsyncMock(side_effect=error))
    monkeypatch.setattr("sys.argv", ["category_seasonality", "fill", "--confirm"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert str(exc.value) == "CATEGORY_SNAPSHOT_FAILED: DBAPIError sqlstate=57014"


def test_build_stage_is_task_local():
    import asyncio

    async def run():
        async def worker(name):
            token = service._BUILD_STAGE.set((name, None))
            try:
                await asyncio.sleep(0)
                assert service._BUILD_STAGE.get()[0] == name
            finally:
                service._BUILD_STAGE.reset(token)

        await asyncio.gather(worker("facts"), worker("clients"))

    asyncio.run(run())


def test_lab_timeout_writes_failed_report_not_success(tmp_path, monkeypatch):
    import json
    from benchmarks import benchmark_snapshot_facts as lab

    destination = tmp_path / "failed.json"
    monkeypatch.setenv(
        "LAB_DATABASE_URL",
        "postgresql+psycopg://synthetic@127.0.0.1/category_snapshot_lab",
    )
    monkeypatch.setattr(
        "sys.argv", ["lab", "--confirm-synthetic-lab", "--output", str(destination)]
    )

    class Engine:
        def dispose(self):
            pass

    monkeypatch.setattr(lab.sa, "create_engine", lambda *args, **kwargs: Engine())

    def failed(*args, **kwargs):
        raise lab.LabFailure(dict(status="failed", phase="facts", sqlstate="57014"))

    monkeypatch.setattr(lab, "run", failed)
    with pytest.raises(SystemExit, match="phase=facts sqlstate=57014"):
        lab.main()
    assert json.loads(destination.read_text())["status"] == "failed"
