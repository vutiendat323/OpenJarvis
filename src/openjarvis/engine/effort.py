"""Operator-set reasoning effort, editable while the backend is serving.

``scripts/launcher.sh effort ...`` writes a small JSON file and the backend
reads it on every model call (re-parsed only when the file changes), so a new
value applies to the next request without a restart. The per-provider
environment variables remain the fallback for runs that have no file.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any

EFFORT_FILE_ENV = "OPENJARVIS_EFFORT_FILE"

_lock = threading.Lock()
_cache: dict[str, Any] = {"path": None, "stamp": None, "values": {}}


def _read(path: str) -> dict[str, str]:
    try:
        stat = os.stat(path)
    except FileNotFoundError:
        return {}
    stamp = (stat.st_mtime_ns, stat.st_size)
    with _lock:
        if _cache["path"] == path and _cache["stamp"] == stamp:
            return _cache["values"]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError) as exc:
        raise ValueError(f"effort file {path} is unreadable: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"effort file {path} must hold a JSON object")
    values = {
        str(key): str(value).strip().lower()
        for key, value in data.items()
        if isinstance(value, str) and value.strip()
    }
    with _lock:
        _cache.update(path=path, stamp=stamp, values=values)
    return values


def effort_setting(key: str, env_name: str) -> str:
    """The effort for ``key``: the file's value, else ``env_name``, else ``""``."""
    path = os.environ.get(EFFORT_FILE_ENV, "").strip()
    if path:
        value = _read(path).get(key, "")
        if value:
            return value
    return os.environ.get(env_name, "").strip().lower()
