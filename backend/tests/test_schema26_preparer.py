"""Integrity checks for offline generation, never an installation."""

import hashlib, os, runpy, subprocess, sys
from pathlib import Path
import pytest

TOOL = Path(__file__).parents[2] / "tools/prepare_schema26_updater.py"
MODULE = runpy.run_path(str(TOOL))


def test_unreviewed_source_is_rejected():
    with pytest.raises(ValueError, match="SHA256"):
        MODULE["adapt"](b"#!/bin/bash\necho unreviewed\n")


def test_original_path_cannot_be_overwritten(tmp_path):
    path = tmp_path / "update_sales.sh"
    original = b"operator-owned bytes"
    path.write_bytes(original)
    result = subprocess.run(
        [sys.executable, str(TOOL), str(path), str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0 and path.read_bytes() == original


@pytest.mark.skipif(
    not os.environ.get("SALES_UPDATE_TEST_SOURCE"),
    reason="Audited external source required",
)
def test_audited_generation_is_deterministic_and_never_changes_input(tmp_path):
    source = Path(os.environ["SALES_UPDATE_TEST_SOURCE"])
    original = source.read_bytes()
    assert hashlib.sha256(original).hexdigest() == MODULE["INPUT_SHA256"]
    destination = tmp_path / "update_sales.schema26.sh"
    command = [sys.executable, str(TOOL), str(source), str(destination)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert source.read_bytes() == original and destination.read_bytes() == MODULE[
        "adapt"
    ](original)
    if os.environ.get("SALES_UPDATE_TEST_SCRIPT"):
        assert (
            destination.read_bytes()
            == Path(os.environ["SALES_UPDATE_TEST_SCRIPT"]).read_bytes()
        )
    assert (
        destination.with_name(destination.name + ".sha256")
        .read_text()
        .startswith(hashlib.sha256(destination.read_bytes()).hexdigest() + "  ")
    )
    preserved = destination.read_bytes()
    again = subprocess.run(command, capture_output=True, text=True)
    assert (
        again.returncode != 0
        and destination.read_bytes() == preserved
        and source.read_bytes() == original
    )
