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
        tmp_path, "effort", "--deepseek-effort", "off",
        "--openai-effort", "gpt-6-luna=medium",
        "--openai-effort", "gpt-5.6-luna=high",
    )

    assert result.returncode == 0, result.stderr
    assert _file(tmp_path) == {
        "deepseek": "off", "gpt-6-luna": "medium", "gpt-5.6-luna": "high",
    }
    assert "deepseek=off" in result.stdout
    assert "gpt-6-luna=medium" in result.stdout
    assert "gpt-5.6-luna=high" in result.stdout


def test_effort_command_changes_one_model_and_keeps_the_other(tmp_path):
    _run(
        tmp_path, "effort", "--deepseek-effort", "high",
        "--openai-effort", "gpt-6-luna=high",
        "--openai-effort", "gpt-5.6-luna=medium",
    )

    result = _run(tmp_path, "effort", "--openai-effort=gpt-6-luna=low")

    assert result.returncode == 0, result.stderr
    assert _file(tmp_path) == {
        "deepseek": "high", "gpt-6-luna": "low", "gpt-5.6-luna": "medium",
    }


def test_effort_without_arguments_shows_the_defaults_and_writes_nothing(tmp_path):
    result = _run(tmp_path, "effort")

    assert result.returncode == 0, result.stderr
    assert "deepseek=low" in result.stdout
    assert "gpt-6-luna=low" in result.stdout
    assert "gpt-5.6-luna=low" in result.stdout
    assert not (tmp_path / "effort.json").exists()


@pytest.mark.parametrize(
    "args",
    [
        ("effort", "--deepseek-effort", "medium"),
        ("effort", "--openai-effort", "gpt-6-luna=off"),
        ("effort", "--openai-effort", "gpt-6-luna=warp"),
        ("effort", "--openai-effort", "gpt-6-luna"),
        ("effort", "--openai-effort", "=low"),
        ("effort", "--openai-effort", "deepseek-flash=low"),
        ("effort", "--openai-effort", "gpt-6-luna="),
        ("effort", "--openai-effort"),
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
    assert "--openai-effort" in result.stdout
    assert "effort" in result.stdout


def test_effort_update_preserves_other_openai_models(tmp_path):
    result = _run(tmp_path, "effort", "--openai-effort", "gpt-5-mini=high")
    assert result.returncode == 0, result.stderr

    result = _run(tmp_path, "effort", "--openai-effort", "gpt-6-luna=medium")

    assert result.returncode == 0, result.stderr
    assert _file(tmp_path)["gpt-5-mini"] == "high"
    assert "gpt-5-mini=high" in result.stdout


def test_invalid_update_does_not_replace_existing_presets(tmp_path):
    _run(tmp_path, "effort", "--openai-effort", "gpt-6-luna=medium")
    before = (tmp_path / "effort.json").read_bytes()

    result = _run(
        tmp_path, "effort", "--openai-effort", "gpt-6-luna=high",
        "--openai-effort", "gpt-5.6-luna=warp",
    )

    assert result.returncode == 2
    assert (tmp_path / "effort.json").read_bytes() == before


def test_shared_legacy_preset_is_split_without_changing_the_other_model(tmp_path):
    (tmp_path / "effort.json").write_text(json.dumps({
        "deepseek": "off", "luna": "high",
    }))

    result = _run(tmp_path, "effort", "--openai-effort", "gpt-6-luna=low")

    assert result.returncode == 0, result.stderr
    assert _file(tmp_path) == {
        "deepseek": "off", "gpt-6-luna": "low", "gpt-5.6-luna": "high",
    }


@pytest.mark.parametrize("command", ["start", "restart"])
def test_start_keeps_other_model_presets(tmp_path, monkeypatch, command):
    # An isolated checkout passes the file preflight, then stops at the missing
    # STT key before it can stop or launch any host services.
    root = tmp_path / "checkout"
    launcher = root / "scripts/launcher.sh"
    launcher.parent.mkdir(parents=True)
    launcher.write_bytes(LAUNCHER.read_bytes())
    monkeypatch.setattr(f"{__name__}.LAUNCHER", launcher)
    executable = root / ".venv/bin/jarvis"
    executable.parent.mkdir(parents=True)
    executable.write_text("#!/bin/sh\nexit 1\n")
    executable.chmod(0o755)
    (root / ".env").write_text("")
    config = root / "configs/openjarvis/examples/ordering-kiosk-mcp.toml"
    config.parent.mkdir(parents=True)
    config.write_text("")
    (tmp_path / ".cache/openjarvis/vieneu-3.2.3-onnx").mkdir(parents=True)
    _run(tmp_path, "effort", "--openai-effort", "gpt-5.6-luna=high")

    result = _run(
        tmp_path, command, "--no-vision", "--openai-effort", "gpt-6-luna=medium",
    )

    assert result.returncode == 1
    assert "GEMINI_API_KEY missing" in result.stderr
    assert _file(tmp_path) == {
        "deepseek": "low", "gpt-6-luna": "medium", "gpt-5.6-luna": "high",
    }
