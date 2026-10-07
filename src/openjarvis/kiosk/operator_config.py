"""Resolve the operator's saved proximity threshold with existing env priority."""

from __future__ import annotations

import math
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from openjarvis.core.paths import get_config_dir
from openjarvis.kiosk.config import KioskConfig

try:
    import tomllib
except ImportError:
    import tomli as tomllib


TRANSITION_BOUNDS = {
    "approach_threshold_m": (0.1, 6.0),
    "approach_entry_debounce": (0.1, 10.0),
    "approach_sustain_seconds": (0.1, 30.0),
    "leave_sustain_seconds_prompting": (0.1, 60.0),
    "leave_sustain_seconds_active": (0.1, 120.0),
    "session_max_seconds": (120.0, 3600.0),
    "popup_timeout": (1.0, 120.0),
    "decline_cooldown_seconds": (0.0, 120.0),
}


def _settings_path() -> Path:
    selected = os.environ.get("OPENJARVIS_CONFIG", "").strip()
    return (
        Path(selected).expanduser()
        if selected
        else get_config_dir() / "kiosk-settings.toml"
    )


def _saved_settings() -> dict:
    path = _settings_path()
    if not path.is_file():
        return {}
    with path.open("rb") as source:
        return tomllib.load(source).get("kiosk", {})


def transition_values(config: KioskConfig) -> dict[str, float]:
    return {key: float(getattr(config, key)) for key in TRANSITION_BOUNDS}


def validate_transition_config(config: KioskConfig) -> None:
    for key, (minimum, maximum) in TRANSITION_BOUNDS.items():
        value = getattr(config, key)
        if (
            type(value) not in (int, float)
            or not math.isfinite(value)
            or not minimum <= value <= maximum
        ):
            raise ValueError(f"{key} must be between {minimum} and {maximum}")
    if config.approach_entry_debounce > config.approach_sustain_seconds:
        raise ValueError(
            "approach_entry_debounce must not exceed approach_sustain_seconds"
        )


def load_kiosk_config() -> KioskConfig:
    values = asdict(KioskConfig())
    saved = _saved_settings()
    for key in TRANSITION_BOUNDS:
        if key in saved:
            values[key] = saved[key]
    overrides = {
        "approach_threshold_m": "KIOSK_APPROACH_THRESHOLD_M",
        "approach_entry_debounce": "KIOSK_APPROACH_ENTRY_DEBOUNCE",
        "approach_sustain_seconds": "KIOSK_APPROACH_SUSTAIN_SECONDS",
        "leave_sustain_seconds_prompting": "KIOSK_LEAVE_SUSTAIN_PROMPTING",
        "leave_sustain_seconds_active": "KIOSK_LEAVE_SUSTAIN_ACTIVE",
        "popup_timeout": "KIOSK_POPUP_TIMEOUT",
        "decline_cooldown_seconds": "KIOSK_DECLINE_COOLDOWN_SECONDS",
    }
    for key, environment in overrides.items():
        if environment in os.environ:
            values[key] = float(os.environ[environment])
    if "KIOSK_SESSION_MINUTES" in os.environ:
        values["session_max_seconds"] = float(os.environ["KIOSK_SESSION_MINUTES"]) * 60
    values["session_warning_seconds"] = values["session_max_seconds"] - 60
    config = KioskConfig(**values)
    validate_transition_config(config)
    return config


def save_kiosk_config(config: KioskConfig) -> None:
    """Atomically save only transition settings, preserving the rest of the preset."""
    import tomlkit

    validate_transition_config(config)
    path = _settings_path()
    if os.environ.get("OPENJARVIS_CONFIG", "").strip() and not path.is_file():
        raise ValueError("Active OpenJarvis preset does not exist")
    path.parent.mkdir(parents=True, exist_ok=True)
    document = tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
    section = document.setdefault("kiosk", tomlkit.table())
    section.update(transition_values(config))
    descriptor, temporary = tempfile.mkstemp(prefix=".kiosk-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as output:
            output.write(tomlkit.dumps(document))
            output.flush()
            os.fsync(output.fileno())
        if path.exists():
            os.chmod(temporary, path.stat().st_mode & 0o777)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def load_approach_threshold() -> float:
    override = os.environ.get("KIOSK_APPROACH_THRESHOLD_M")
    if override is not None:
        value = float(override)
    else:
        value = _saved_settings().get("approach_threshold_m", 1.0)
    if (
        type(value) not in (float, int)
        or not math.isfinite(value)
        or not 0.1 <= value <= 6
    ):
        raise ValueError("Kiosk approach threshold must be between 0.1 and 6 metres")
    return float(value)
