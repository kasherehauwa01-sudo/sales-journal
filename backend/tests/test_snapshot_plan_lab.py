"""Lab-only regression tests; native cases require an isolated localhost DB."""

import json
import os
import stat
from unittest.mock import Mock
import pytest
import sqlalchemy as sa
from benchmarks import benchmark_snapshot_facts as lab


@pytest.fixture
def engine():
    raw = os.environ.get("SNAPSHOT_PLAN_TEST_URL")
    if not raw:
        pytest.skip("Dedicated SNAPSHOT_PLAN_TEST_URL required")
    url = sa.engine.make_url(raw)
    assert (
        url.host in ("127.0.0.1", "localhost")
        and url.database == "category_snapshot_lab"
    )
    engine = sa.create_engine(url, poolclass=sa.pool.NullPool)
    yield engine
    engine.dispose()


def test_writer_private_checkpoints_and_no_overwrite(tmp_path):
    path = tmp_path / "report.json"
    writer = lab.ReportWriter(path)
    writer({"status": "running", "runs": [{"estimated_plan": [{}]}]})
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    with pytest.raises(FileExistsError):
        lab.ReportWriter(path)({"status": "complete"})
    writer({"status": "partial"})
    assert json.loads(path.read_text()) == {"status": "partial"}


def test_estimated_nodes_do_not_invent_actual_rows():
    nodes = lab.plan_nodes(
        {
            "Node Type": "Hash Join",
            "Plan Rows": 137,
            "Plans": [{"Node Type": "Seq Scan", "Plan Rows": 4600000}],
        }
    )
    assert all(n["actual_rows"] is None and n["actual_loops"] is None for n in nodes)
    assert lab.chosen_joins(nodes) == ["Hash Join"]


def test_success_both_directions_and_local_settings(engine):
    report = lab.run(engine, 50, 200, 20, 10, 2)
    assert report["status"] == "complete"
    assert [r["variant"] for r in report["runs"]] == [
        "baseline",
        "no_nested_loop",
        "no_nested_loop",
        "baseline",
    ]
    for row in report["runs"]:
        assert row["status"] == "complete" and row["equivalent"] is True
        assert row["comparison"]["reference_minus_current"] == 0
        assert row["comparison"]["current_minus_reference"] == 0
        assert row["totals"]["lines"] == 200
        assert row["effective_settings"]["enable_nestloop"] == (
            "off" if row["variant"] == "no_nested_loop" else "on"
        )
        assert row["effective_settings"]["statement_timeout"] == "1min"
        assert row["effective_settings"]["work_mem"] == "4MB"
        assert row["effective_settings"]["temp_file_limit"] == "1GB"
        assert all(
            n["actual_rows"] is not None and n["actual_loops"] is not None
            for n in row["actual_nodes"]
        )
    with engine.connect() as db:
        assert db.scalar(sa.text("SHOW enable_nestloop")) == "on"
        assert db.scalar(sa.text("SELECT to_regclass('pg_temp.sales')")) is None


def delayed_statement(original, always=False):
    condition = (
        "2"
        if always
        else "CASE WHEN current_setting('enable_nestloop')='on' THEN 2 ELSE 0 END"
    )
    sql = str(original(lab.PAIRS)).replace(
        "FROM sale_items i", "FROM sale_items i CROSS JOIN lab_delay"
    )
    return sa.text(
        "WITH lab_delay AS MATERIALIZED (SELECT pg_sleep(" + condition + ")) " + sql
    )


def test_timeout_continues_and_never_compares_incomplete_baseline(engine, monkeypatch):
    original = lab.facts_statement
    monkeypatch.setattr(
        lab, "facts_statement", lambda pairs: delayed_statement(original)
    )
    checkpoints = []
    result = lab.run(
        engine,
        50,
        200,
        20,
        10,
        1,
        sql_timeout=1,
        progress=lambda r: checkpoints.append(json.loads(json.dumps(r, default=str))),
    )
    assert result["status"] == "partial"
    baseline, candidate = result["runs"]
    assert (
        baseline["status"] == "failed"
        and baseline["phase"] == "facts"
        and baseline["sqlstate"] == "57014"
    )
    assert (
        baseline["estimated_plan"]
        and baseline["actual_plan"] is None
        and baseline["actual_nodes"] is None
    )
    assert (
        candidate["status"] == "complete"
        and candidate["actual_plan"]
        and candidate["totals"]["lines"] == 200
    )
    assert baseline["equivalent"] is None and candidate["equivalent"] is None
    assert candidate["comparison_status"] == "not_available"
    assert any(
        r["runs"][0]["estimated_plan"] and r["runs"][0]["actual_plan"] is None
        for r in checkpoints
    )


def test_both_timeouts_keep_two_plans(engine, monkeypatch):
    original = lab.facts_statement
    monkeypatch.setattr(
        lab, "facts_statement", lambda pairs: delayed_statement(original, always=True)
    )
    result = lab.run(engine, 50, 200, 20, 10, 1, sql_timeout=1)
    assert result["status"] == "failed" and len(result["runs"]) == 2
    assert all(
        r["sqlstate"] == "57014"
        and r["estimated_plan"]
        and r["actual_plan"] is None
        and r["equivalent"] is None
        for r in result["runs"]
    )


def test_later_completed_pair_only_compares_its_own_results(engine, monkeypatch):
    original = lab.facts_statement
    calls = 0

    def statement(pairs):
        nonlocal calls
        calls += 1
        return (
            delayed_statement(original, always=True) if calls == 1 else original(pairs)
        )

    monkeypatch.setattr(lab, "facts_statement", statement)
    result = lab.run(engine, 50, 200, 20, 10, 2, sql_timeout=1)
    assert result["status"] == "partial" and result["runs"][0]["status"] == "failed"
    assert (
        result["runs"][1]["status"] == "complete"
        and result["runs"][1]["equivalent"] is None
    )
    assert all(r["equivalent"] is True for r in result["runs"][2:])


def test_comparison_detects_numeric_change(engine, monkeypatch):
    original = lab.facts_statement
    monkeypatch.setattr(
        lab,
        "facts_statement",
        lambda pairs: sa.text(
            str(original(pairs)).replace(
                "sum(i.quantity),",
                "sum(i.quantity)+CASE WHEN current_setting('enable_nestloop')='off' THEN 1 ELSE 0 END,",
            )
        ),
    )
    result = lab.run(engine, 50, 200, 20, 10, 1)
    assert result["status"] == "partial"
    for row in result["runs"]:
        assert row["status"] == "complete" and row["equivalent"] is False
        assert (
            row["comparison"]["reference_minus_current"] > 0
            and row["comparison"]["current_minus_reference"] > 0
        )


def test_main_partial_report_nonzero_after_both_attempts(tmp_path, monkeypatch):
    path = tmp_path / "partial.json"
    monkeypatch.setenv(
        "LAB_DATABASE_URL", "postgresql+psycopg://lab@127.0.0.1/category_snapshot_lab"
    )
    monkeypatch.setattr(
        "sys.argv", ["lab", "--confirm-synthetic-lab", "--output", str(path)]
    )
    monkeypatch.setattr(lab.sa, "create_engine", lambda *a, **k: Mock())

    def run(*args, **kwargs):
        assert args[-1] == 60 and kwargs["comparison"] == "no_nested_loop"
        return dict(
            status="partial",
            runs=[
                dict(variant="baseline", status="failed", sqlstate="57014"),
                dict(variant="no_nested_loop", status="complete"),
            ],
        )

    monkeypatch.setattr(lab, "run", run)
    with pytest.raises(SystemExit, match="SYNTHETIC_LAB_INCOMPLETE"):
        lab.main()
    assert len(json.loads(path.read_text())["runs"]) == 2


def test_comparison_timeout_retains_successful_sql_plan_but_not_equivalence(
    engine, monkeypatch
):
    original = lab.sa.text

    def statement(sql):
        if sql.startswith("WITH forward AS"):
            sql = (
                sql.replace(
                    "WITH forward AS",
                    "WITH comparison_delay AS MATERIALIZED (SELECT pg_sleep(2)), forward AS",
                    1,
                )
                + " FROM comparison_delay"
            )
        return original(sql)

    monkeypatch.setattr(lab.sa, "text", statement)
    result = lab.run(engine, 50, 200, 20, 10, 1, sql_timeout=1)
    baseline, candidate = result["runs"]
    assert result["status"] == "partial" and baseline["equivalent"] is None
    assert candidate["status"] == "failed" and candidate["phase"] == "comparison"
    assert candidate["facts_status"] == "complete" and candidate["actual_plan"]
    assert candidate["sqlstate"] == "57014" and candidate["equivalent"] is None


def test_monthly_materialized_physical_contract_and_exact_results(engine):
    report = lab.run(
        engine,
        50,
        200,
        20,
        10,
        1,
        sql_timeout=60,
        comparison="monthly_materialized",
        baseline_timeout=180,
        physical=True,
        fresh_client_statistics=True,
    )
    assert report["status"] == "complete" and report["physical_target"]
    assert report["source_fk_validated"] and report["input_ordered_by_date"]
    baseline, candidate = report["runs"]
    assert baseline["effective_settings"]["statement_timeout"] == "3min"
    assert candidate["effective_settings"]["statement_timeout"] == "1min"
    assert all(
        r["effective_settings"]["enable_nestloop"] == "on" for r in report["runs"]
    )
    assert all(
        r["effective_settings"]["transaction_isolation"] == "repeatable read"
        for r in report["runs"]
    )
    assert candidate["equivalent"] is True and candidate["totals"]["lines"] == 200
    assert len(candidate["batches"]) == 36 and len(candidate["staging"]) == 3
    assert candidate["storage_final"]["cs_monthly_input"] > 0
    assert all(b["status"] == "complete" for b in candidate["batches"])
    with engine.connect() as db:
        assert (
            db.scalar(
                sa.text(
                    "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=:name)"
                ),
                {"name": report["target_schema"]},
            )
            is False
        )
