"""The Marimo notebook is plain Python, so it can be executed as a smoke test."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

APP = Path(__file__).resolve().parents[1] / "app.py"


def _run_app(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(APP), *args],
        capture_output=True,
        text=True,
        timeout=300,
        cwd=APP.parent,
    )


def test_app_runs_without_a_file():
    result = _run_app()
    assert result.returncode == 0, result.stderr


def test_app_runs_against_a_geopackage(sample_gpkg, tmp_path):
    result = _run_app(
        "--gpkg", str(sample_gpkg), "--workspace", str(tmp_path / "workspace")
    )
    assert result.returncode == 0, result.stderr
    assert (tmp_path / "workspace" / "waterways.working.gpkg").exists()
