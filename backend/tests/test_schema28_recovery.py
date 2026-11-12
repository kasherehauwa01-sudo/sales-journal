"""Exercise the shared durable recovery transaction against schema28 artifacts."""

import re
import pytest
import test_schema27_recovery as shared
from test_updater_schema28 import (
    gen,
    recovery,
    source26,
    release28,
    artifact28,
    updater,
)


@pytest.fixture
def recovery28(release28, tmp_path, monkeypatch):
    original_fake = shared.fake27

    def fake():
        code = original_fake()
        mapping = {
            "20261109_26": gen.SOURCE,
            "20261111_27": gen.TARGET,
            "sales-schema27-migrate-": "sales-schema28-migrate-",
        }
        return re.sub(
            "|".join(re.escape(k) for k in mapping), lambda m: mapping[m[0]], code
        )

    # Shared fixture adds protocol handlers using literals from the reviewed 27
    # transaction. Map those too, after it has composed the fake child process.
    class PathSource:
        def read_bytes(self):
            return source26()

    monkeypatch.setattr(shared, "gen", gen)
    monkeypatch.setattr(shared, "recovery", recovery)
    monkeypatch.setattr(shared, "source26", lambda: PathSource())
    monkeypatch.setattr(shared, "fake27", fake)
    value = shared.recovery27.__wrapped__(release28, tmp_path)
    for file in (value[0] / "bin").iterdir():
        code = file.read_text().replace(
            "read()['revision']=='20261111_27'", "read()['revision']=='20261112_28'"
        )
        file.write_text(code)
    return value


def test_recovery28_success_no_ddl(recovery28):
    root, project, cp, marker, before, original, run = recovery28
    result, state, events = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["containers"] == {
        "backend": shared.IDS["new-backend"],
        "frontend": shared.IDS["new-frontend"],
    }
    assert not marker.exists()


@pytest.mark.parametrize("failure", ["backend-health", "backend-up", "normal-process"])
def test_recovery28_backend_failure(recovery28, failure):
    shared.test_backend_failure_restores_original_bridge_and_marker(recovery28, failure)


@pytest.mark.parametrize("failure", ["frontend-up", "frontend-health"])
def test_recovery28_frontend_resume(recovery28, failure):
    shared.test_frontend_failure_preserves_backend_and_resume(recovery28, failure)
