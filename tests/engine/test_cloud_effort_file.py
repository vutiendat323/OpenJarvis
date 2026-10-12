"""Reasoning effort can be changed while serving, without a restart.

The launcher writes ``effort.json`` and points ``OPENJARVIS_EFFORT_FILE`` at it;
the engine reads it on every model call, so ``launcher.sh effort ...`` takes
effect on the next request. The old per-provider environment variables remain a
fallback for runs that have no file.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openjarvis.engine.cloud import (
    _deepseek_thinking_options,
    _openai_responses_effort,
)

FILE_ENV = "OPENJARVIS_EFFORT_FILE"
DEEPSEEK_ENV = "OPENJARVIS_DEEPSEEK_THINKING"
OPENAI_ENV = "OPENJARVIS_OPENAI_REASONING_EFFORT"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (FILE_ENV, DEEPSEEK_ENV, OPENAI_ENV):
        monkeypatch.delenv(name, raising=False)


def _write(path: Path, **values: str) -> None:
    path.write_text(json.dumps(values))
    # Same-size rewrites inside one mtime tick must still be noticed.
    stat = path.stat()
    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))


def _deepseek_request() -> dict:
    request: dict = {}
    _deepseek_thinking_options(request)
    return request


def test_file_drives_both_providers(monkeypatch, tmp_path):
    path = tmp_path / "effort.json"
    _write(path, **{
        "deepseek": "low", "gpt-6-luna": "medium", "gpt-5.6-luna": "low",
    })
    monkeypatch.setenv(FILE_ENV, str(path))

    assert _deepseek_request() == {"reasoning_effort": "low"}
    assert _openai_responses_effort("gpt-6-luna") == "medium"
    assert _openai_responses_effort("gpt-5.6-luna") == "low"
    assert _openai_responses_effort("openrouter/openai/gpt-6-luna") == "medium"
    assert _openai_responses_effort("openrouter/openai/gpt-5.6-luna") == "low"


def test_editing_the_file_applies_to_the_next_call(monkeypatch, tmp_path):
    path = tmp_path / "effort.json"
    _write(path, **{"deepseek": "low", "gpt-6-luna": "low"})
    monkeypatch.setenv(FILE_ENV, str(path))
    assert _openai_responses_effort("gpt-6-luna") == "low"

    _write(path, **{"deepseek": "off", "gpt-6-luna": "high"})

    assert _openai_responses_effort("gpt-6-luna") == "high"
    assert _deepseek_request() == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_file_wins_over_the_environment(monkeypatch, tmp_path):
    path = tmp_path / "effort.json"
    _write(path, **{"deepseek": "low", "gpt-6-luna": "low"})
    monkeypatch.setenv(FILE_ENV, str(path))
    monkeypatch.setenv(DEEPSEEK_ENV, "max")
    monkeypatch.setenv(OPENAI_ENV, "high")

    assert _deepseek_request() == {"reasoning_effort": "low"}
    assert _openai_responses_effort("gpt-6-luna") == "low"


def test_missing_key_falls_back_to_the_environment_then_the_default(
    monkeypatch, tmp_path
):
    path = tmp_path / "effort.json"
    _write(path, deepseek="low")
    monkeypatch.setenv(FILE_ENV, str(path))

    assert _openai_responses_effort("gpt-6-luna") == "high"
    monkeypatch.setenv(OPENAI_ENV, "medium")
    assert _openai_responses_effort("gpt-6-luna") == "medium"


def test_absent_file_keeps_the_environment_behaviour(monkeypatch, tmp_path):
    monkeypatch.setenv(FILE_ENV, str(tmp_path / "missing.json"))
    monkeypatch.setenv(DEEPSEEK_ENV, "off")

    assert _deepseek_request() == {"extra_body": {"thinking": {"type": "disabled"}}}


def test_invalid_value_in_the_file_is_refused(monkeypatch, tmp_path):
    path = tmp_path / "effort.json"
    _write(path, **{"gpt-6-luna": "warp"})
    monkeypatch.setenv(FILE_ENV, str(path))

    with pytest.raises(ValueError, match="luna"):
        _openai_responses_effort("gpt-6-luna")


def test_unreadable_file_is_refused_not_ignored(monkeypatch, tmp_path):
    path = tmp_path / "effort.json"
    path.write_text("{not json")
    monkeypatch.setenv(FILE_ENV, str(path))

    with pytest.raises(ValueError, match="effort"):
        _openai_responses_effort("gpt-6-luna")
