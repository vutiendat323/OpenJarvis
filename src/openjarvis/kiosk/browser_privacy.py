"""Bounded text filtering at the browser-to-Agent observation boundary."""

from __future__ import annotations

import re
from typing import Any, Callable

_SECRET = re.compile(
    r"""(?ix)(\b(?:password|passwd|access_token|refresh_token|token|api[_-]?key|authorization|cookie|set-cookie|cvv|cvc|card_number)\b["']?\s*[:=]\s*["']?)([^\s"'&,;<>]+)"""
)
_BEARER = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_CARD = re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)")
_SECRET_KEY = re.compile(
    r"(?i)^(?:password|passwd|.*token|api[_-]?key|authorization|cookie|set-cookie|cvv|cvc|card_number)$"
)
_PRIVATE_FIELD = re.compile(
    r"""(?im)^(\s*-\s*(?:textbox|spinbutton)[^\n]*(?:password|card|cvv|cvc|token)[^\n]*?:)\s*[^\n]+$"""
)


def redact_browser_text(text: str) -> str:
    text = _BEARER.sub("Bearer [REDACTED]", text)
    text = _SECRET.sub(r"\1[REDACTED]", text)
    text = _JWT.sub("[REDACTED]", text)
    text = _PRIVATE_FIELD.sub(r"\1 [REDACTED]", text)
    return _CARD.sub("[REDACTED]", text)


def redact_browser_value(
    value: Any, redact: Callable[[str], str] = redact_browser_text
) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {
            key: "[REDACTED]"
            if _SECRET_KEY.match(str(key))
            else redact_browser_value(item, redact)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_browser_value(item, redact) for item in value]
    return value


def browser_log_arguments(name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Keep useful selectors; never put typed values or executable code in logs."""
    if name in {
        "browser_type",
        "browser_fill_form",
        "browser_evaluate",
        "browser_run_code",
    }:
        return {
            key: "[REDACTED]"
            if key in {"text", "fields", "function", "code"}
            else redact_browser_value(value)
            for key, value in params.items()
        }
    return redact_browser_value(params)
