"""Regression for I4 — a broken ``system_prompt_path`` must not degrade
``jarvis serve`` to a toolless chatbot.

``resolve_agent_system_prompt`` raises ``RuntimeError`` on a configured but
unreadable path, deliberately: the Agent's instructions must never be
silently dropped. Before this fix, ``serve.py`` called it *inside* a
``try: ... except Exception`` that also wraps ``construct_registered_agent``,
so a typo in the path just logged a warning and left ``system.agent = None``
-- and the server would still come up, routing every request to the raw
engine with no tools and no guidance.

Reuses the ``jarvis serve`` harness from ``test_serve_single_build`` (heavy
I/O and ``uvicorn.run`` stubbed out) rather than re-building it.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tests.cli.test_serve_single_build import _counting_build, _run_serve


def test_broken_system_prompt_path_is_fatal(tmp_path, monkeypatch):
    def _configure(config):
        config.agent.system_prompt_path = str(tmp_path / "does-not-exist.md")

    calls, build_spy = _counting_build()

    with pytest.raises(RuntimeError, match="system_prompt_path is not readable"):
        _run_serve(
            tmp_path,
            monkeypatch,
            build_spy=build_spy,
            set_system_spy=MagicMock(),
            configure=_configure,
        )
