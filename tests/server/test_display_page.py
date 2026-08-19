"""The page escapes merchant text and never builds markup from data."""

from __future__ import annotations

from pathlib import Path

import pytest

PAGE = (
    Path(__file__).resolve().parents[2]
    / "frontend/public/display.html"
)


def test_the_page_exists():
    assert PAGE.is_file()


def test_the_page_never_assigns_innerhtml():
    """textContent only. innerHTML with merchant-supplied text is an XSS hole
    on a screen a customer is standing in front of."""
    source = PAGE.read_text(encoding="utf-8")
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "document.write" not in source


def test_the_page_connects_to_the_existing_event_socket():
    source = PAGE.read_text(encoding="utf-8")
    assert "/v1/agents/events" in source


def test_the_page_loads_nothing_from_a_third_party():
    source = PAGE.read_text(encoding="utf-8")
    assert "http://" not in source.replace("http://localhost", "")
    assert "https://" not in source
