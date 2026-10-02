"""Resolve the operator's saved proximity threshold with existing env priority."""

from __future__ import annotations

import math
import os
from pathlib import Path

try:
    import tomllib
except ImportError:
    import tomli as tomllib


def load_approach_threshold() -> float:
    override = os.environ.get("KIOSK_APPROACH_THRESHOLD_M")
    if override is not None:
        value = float(override)
    else:
        selected = os.environ.get("OPENJARVIS_CONFIG", "").strip()
        path = Path(selected).expanduser() if selected else None
        if path is not None and path.is_file():
            with path.open("rb") as source:
                document = tomllib.load(source)
            value = document.get("kiosk", {}).get("approach_threshold_m", 1.0)
        else:
            value = 1.0
    if (
        type(value) not in (float, int)
        or not math.isfinite(value)
        or not 0.1 <= value <= 6
    ):
        raise ValueError("Kiosk approach threshold must be between 0.1 and 6 metres")
    return float(value)
