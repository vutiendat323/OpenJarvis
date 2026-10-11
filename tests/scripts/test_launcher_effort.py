"""The launcher edits model effort through arguments, not environment variables."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parents[2] / "scripts" / "launcher.sh"


def _run(tmp_path: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {
        "PATH": os.environ["PATH"],
        "HOME": str(tmp_path),
        "OPENJARVIS_LOG_DIR": str(tmp_path),
    }
    return subprocess.run(
        ["bash", str(LAUNCHER), *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


def _file(tmp_path: Path) -> dict:
    return json.loads((tmp_path / "effort.json").read_text())


def test_effort_command_writes_the_requested_values(tmp_path):
    result = _run(
        tmp_path, "effort", "--deepseek-effort", "off", "--luna-effort", "medium"
    )

    assert result.returncode == 0, result.stderr
    assert _file(tmp_path) == {"deepseek": "off", "luna": "medium"}
    assert "deepseek=off" in result.stdout and "luna=medium" in result.stdout


def test_effort_command_changes_one_model_and_keeps_the_other(tmp_path):
    _run(tmp_path, "effort", "--deepseek-effort", "high", "--luna-effort", "high")

    result = _run(tmp_path, "effort", "--luna-effort=low")

    assert result.returncode == 0, result.stderr
    assert _file(tmp_path) == {"deepseek": "high", "luna": "low"}


def test_effort_without_arguments_shows_the_defaults_and_writes_nothing(tmp_path):
    result = _run(tmp_path, "effort")

    assert result.returncode == 0, result.stderr
    assert "deepseek=low" in result.stdout and "luna=low" in result.stdout
    assert not (tmp_path / "effort.json").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("effort", "--deepseek-effort", "medium"),
        ("effort", "--luna-effort", "off"),
        ("effort", "--luna-effort", "warp"),
        ("effort", "--deepseek-effort"),
    ],
)
def test_invalid_effort_is_rejected_and_nothing_is_written(tmp_path, args):
    result = _run(tmp_path, *args)

    assert result.returncode == 2
    assert not (tmp_path / "effort.json").exists()


def test_help_documents_the_effort_options(tmp_path):
    result = _run(tmp_path, "help")

    assert "--deepseek-effort" in result.stdout
    assert "--luna-effort" in result.stdout
    assert "effort" in result.stdout
