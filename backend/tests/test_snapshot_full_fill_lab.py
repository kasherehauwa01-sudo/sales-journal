"""Offline safety and measurement regressions for the opt-in full CLI lab."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from benchmarks import benchmark_snapshot_fill as lab

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "fill_summary", ROOT / "tools/summarize_snapshot_fill_lab.py"
)
summary = importlib.util.module_from_spec(spec)
spec.loader.exec_module(summary)


def test_refuse_normal_database_remote_and_non_app_roles():
    valid = "postgresql+asyncpg://sales_fill_app:synthetic@127.0.0.1/category_fill_lab_0123456789ab"
    assert lab.validate_url(valid).database == "category_fill_lab_0123456789ab"
    for raw in [
        valid.replace("127.0.0.1", "db"),
        valid.replace("sales_fill_app", "postgres"),
        valid.replace("category_fill_lab_0123456789ab", "sales"),
        valid.replace("asyncpg", "psycopg"),
    ]:
        with pytest.raises(ValueError):
            lab.validate_url(raw)


def test_locale_uses_pg16_database_catalog_and_version_is_strict():
    db = Mock()
    db.scalar.return_value = "160016"
    db.execute.return_value.mappings.return_value.one.return_value = {
        "datctype": "C.UTF-8",
        "datcollate": "C.UTF-8",
    }
    assert lab.schema_check(db, 160016)["datctype"] == "C.UTF-8"
    assert "datctype,datcollate" in str(db.execute.call_args.args[0])
    db.scalar.return_value = "160015"
    with pytest.raises(RuntimeError):
        lab.schema_check(db, 160016)


def test_no_analyze_permission_claim_from_success_status_alone():
    db = Mock()
    db.execute.return_value.mappings.return_value.one.return_value = {
        "role": "sales_fill_app",
        "rolsuper": False,
        "rolcreatedb": False,
        "rolcreaterole": False,
    }
    db.scalar.side_effect = [False, ""]
    with pytest.raises(RuntimeError):
        lab.privileges(db)
    # Ownership, not a new GRANT or superuser, is the intended application profile.
    db.scalar.side_effect = [True, ""]
    assert lab.privileges(db)["owns_clients"]


def test_pgdata_sampling_never_accepts_missing_unreadable_or_external_storage(
    tmp_path, monkeypatch
):
    with pytest.raises(OSError):
        lab.allocated_bytes(tmp_path)
    (tmp_path / "PG_VERSION").write_text("16\n")
    (tmp_path / "base").mkdir()
    (tmp_path / "pg_wal").mkdir()
    (tmp_path / "pg_tblspc").mkdir()
    assert lab.allocated_bytes(tmp_path) > 0
    (tmp_path / "pg_tblspc" / "synthetic").symlink_to(tmp_path / "base")
    with pytest.raises(OSError):
        lab.allocated_bytes(tmp_path)
    (tmp_path / "pg_tblspc" / "synthetic").unlink()

    def denied(*a, **kw):
        raise PermissionError("synthetic")

    monkeypatch.setattr(lab.os, "walk", denied)
    with pytest.raises(PermissionError):
        lab.allocated_bytes(tmp_path)


def test_summary_separates_sampled_and_kernel_peaks_and_requires_both_fills(tmp_path):
    (tmp_path / "pg-memory.tsv").write_text(
        "1.00 100 120\n1.25 150 180\n2.00 120 180\n2.25 160 200\n"
    )
    for name, prior, sid, begin in [
        ("first-fill.json", None, "first", 1),
        ("retained-fill.json", "first", "second", 2),
    ]:
        d = dict(
            status="complete",
            result={"id": sid},
            prior={"active_id": prior},
            cli_begin_boottime=begin,
            cli_end_boottime=begin + 0.5,
            cli_wall_seconds=0.5,
            metrics={"max_sql_seconds": 0.1, "max_sql_stage": "facts_input"},
            backend_memory={"cgroup_peak_bytes": 300},
            resource_samples=[
                dict(boottime=begin + 0.25, memory={"cgroup_current_bytes": 200})
            ],
            disk_growth_sampled_bytes=500,
            analyze_executed=True,
            previous_snapshot_retained=True,
            next_due_valid=True,
            totals={"lines": 4000},
            observer_error=None,
            resource_measurement_complete=True,
        )
        (tmp_path / name).write_text(json.dumps(d))
    report = summary.summarize(tmp_path)
    assert report["status"] == "complete" and report["ready_for_production"] is False
    assert report["fills"][0]["combined_memory_sampled_bytes"] == 350
    assert report["fills"][0]["conservative_separate_lifetime_peak_sum_bytes"] == 480
    assert report["fills"][0]["exact_disk_peak_measured"] is False
    (tmp_path / "retained-fill.json").unlink()
    assert summary.summarize(tmp_path)["status"] == "incomplete"


def test_bad_memory_samples_never_count_as_complete(tmp_path):
    (tmp_path / "pg-memory.tsv").write_text("invalid\n")
    with pytest.raises(ValueError):
        summary.summarize(tmp_path)


def test_full_runner_pins_runtime_uses_no_build_or_production_settings():
    source = (ROOT / "tools/run_snapshot_fill_lab.sh").read_text()
    assert lab.CANDIDATE in source and 'git archive "$CANDIDATE" backend' in source
    assert "160016" in source and "datctype,datcollate" in source
    assert "docker build" not in source and "docker compose" not in source.lower()
    assert "--network none" in source and "--pull never" in source
    assert "docker.sock" not in source


def test_stock_cli_asyncpg_application_owner_smoke(tmp_path):
    import subprocess, sys

    raw = os.environ.get("CATEGORY_FILL_SMOKE_URL")
    if not raw:
        pytest.skip("Explicit existing synthetic fixture only")
    lab.validate_url(raw)
    results = []
    for number in range(2):
        output = tmp_path / f"smoke-{number}.json"
        env = dict(
            os.environ,
            DATABASE_URL=raw,
            PYTHONPATH=str(ROOT / "backend"),
            PYTHONDONTWRITEBYTECODE="1",
        )
        env.pop("LAB_ADMIN_URL", None)
        process = subprocess.run(
            [
                sys.executable,
                "-m",
                "benchmarks.benchmark_snapshot_fill",
                "fill",
                "--confirm-synthetic-lab",
                "--expected-version",
                "160015",
                "--smoke",
                "--output",
                str(output),
            ],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=90,
        )
        assert process.returncode == 0, process.stderr
        result = json.loads(output.read_text())
        assert result["scope"] == "smoke" and result["status"] == "complete"
        assert (
            result["analyze_executed"]
            and result["privileges_before"] == result["privileges_after"]
        )
        assert (
            not result["privileges_before"]["rolsuper"]
            and result["privileges_before"]["owns_clients"]
        )
        assert result["metrics"]["analyze_probe"]["statement_timeout"] == "1min"
        assert (
            result["next_due_valid"]
            and result["atomic_observed"]
            and result["previous_snapshot_retained"]
        )
        assert result["metrics"]["import_lock_seconds_lower"] > 0
        assert int(result["totals"]["lines"]) == 4000
        assert result["expected_totals"]["revenue"] == result["totals"]["revenue"]
        results.append(result)
    assert results[1]["prior"]["active_id"] == results[0]["result"]["id"]


@pytest.mark.parametrize("version,expected", [("160016", 0), ("160015", 1)])
def test_runner_control_flow_with_fake_docker_only(tmp_path, version, expected):
    import subprocess

    fake = tmp_path / "bin"
    fake.mkdir()
    journal = tmp_path / "calls.jsonl"
    root = tmp_path / "docker-root"
    root.mkdir()
    (fake / "df").write_text(
        '#!/bin/sh\nprintf "Filesystem 1024-blocks Used Available Capacity Mounted\\nlab 20971520 0 20971520 0%% /lab\\n"\n'
    )
    (fake / "df").chmod(0o755)
    (fake / "docker").write_text("""#!/usr/bin/env python3
import sys,os,json
from pathlib import Path
a=sys.argv[1:]
with open(os.environ['FAKE_JOURNAL'],'a') as f:f.write(json.dumps(a)+'\\n')
if a[0]=='info':print('2' if 'CgroupVersion' in a[-1] else os.environ['FAKE_ROOT'])
elif a[0]=='image':print('sha256:'+'a'*64+' linux/amd64')
elif a[0]=='exec':
 if any('server_version_num' in x for x in a):print(os.environ['FAKE_VERSION'])
 elif any('datctype' in x for x in a):print('C.UTF-8|C.UTF-8')
 elif 'sh' in a:print('1.00 100 120\\n1.25 150 180\\n2.00 120 180\\n2.25 160 200')
elif a[0]=='run':
 if '/lab/worker.py' not in a:print('owned-fake-container')
 else:
  phase=a[a.index('/lab/worker.py')+1]
  admin=any('admin.private.env' in x for x in a)
  assert admin==(phase=='bootstrap')
  if phase=='fill':
   out=Path(next(x[:-8] for x in a if x.endswith(':/out:rw')))
   name=a[a.index('--output')+1].split('/')[-1]
   first=name=='first-fill.json';begin=1 if first else 2
   d=dict(status='complete',resource_measurement_complete=True,observer_error=None,result={'id':'first' if first else 'second'},prior={'active_id':None if first else 'first'},cli_begin_boottime=begin,cli_end_boottime=begin+0.4,cli_wall_seconds=0.4,resource_samples=[dict(boottime=begin+0.25,memory={'cgroup_current_bytes':200})],backend_memory={'cgroup_peak_bytes':300},metrics={'max_sql_seconds':0.1,'max_sql_stage':'facts_input'},disk_growth_sampled_bytes=400,analyze_executed=True,previous_snapshot_retained=True,next_due_valid=True,totals={'lines':'4000'})
   (out/name).write_text(json.dumps(d))
""")
    (fake / "docker").chmod(0o755)
    base = tmp_path / "lab"
    base.mkdir()
    env = dict(
        os.environ,
        PATH=str(fake) + os.pathsep + os.environ["PATH"],
        LAB_BASE=str(base),
        FAKE_VERSION=version,
        FAKE_ROOT=str(root),
        FAKE_JOURNAL=str(journal),
    )
    result = subprocess.run(
        ["bash", "tools/run_snapshot_fill_lab.sh"],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    calls = [json.loads(line) for line in journal.read_text().splitlines()]
    jobs = [
        c[c.index("/lab/worker.py") + 1]
        for c in calls
        if c[0] == "run" and "/lab/worker.py" in c
    ]
    if expected == 0:
        assert jobs == ["bootstrap", "seed", "fill", "fill"]
    else:
        assert not jobs
    assert all(c[0] not in ("build", "pull", "rm", "volume") for c in calls)
    assert all(c[-1].startswith("category-fill-lab-") for c in calls if c[0] == "stop")
