"""Operator changes must reach the FSM and survive a server restart."""

import os
from dataclasses import replace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.kiosk import runtime
from openjarvis.kiosk.config import KioskConfig
from openjarvis.kiosk.evaluate import evaluate_state, get_config, set_config
from openjarvis.kiosk.events import EventHistory, VisionEvent
from openjarvis.kiosk.operator_config import load_kiosk_config, transition_values
from openjarvis.kiosk.routes import router


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    preset = tmp_path / "kiosk.toml"
    preset.write_text("[voice.speaker]\nanchor_max_m = 1.5\n")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    for key in tuple(os.environ):
        if key.startswith("KIOSK_"):
            monkeypatch.delenv(key)
    monkeypatch.setattr(runtime, "_current_state", "idle")
    original = get_config()
    set_config(KioskConfig())
    app = FastAPI()
    app.include_router(router)
    try:
        with TestClient(app) as client:
            yield client, preset
    finally:
        set_config(original)


def test_saved_boundaries_reach_fsm_and_survive_reload(settings_client):
    client, preset = settings_client
    values = client.get("/api/kiosk/settings").json()
    assert values == transition_values(KioskConfig())
    values.update(session_max_seconds=180, popup_timeout=12, approach_threshold_m=1.4)
    response = client.put("/api/kiosk/settings", json=values)
    assert response.status_code == 200
    assert response.json() == values
    assert transition_values(load_kiosk_config()) == values
    assert get_config().session_warning_seconds == 120
    assert "anchor_max_m = 1.5" in preset.read_text()
    state, _ = evaluate_state(EventHistory(), 181, "active", None, 0, None)
    assert state == "cleanup"
    state, _ = evaluate_state(EventHistory(), 13, "prompting", None, None, 0)
    assert state == "idle"


@pytest.mark.parametrize(
    "key,value",
    [
        ("approach_threshold_m", 0),
        ("approach_entry_debounce", True),
        ("approach_sustain_seconds", "2"),
        ("session_max_seconds", 60),
        ("session_max_seconds", 4000),
        ("popup_timeout", -1),
        ("decline_cooldown_seconds", 121),
        ("session_warning_seconds", 100),
    ],
)
def test_invalid_settings_cannot_change_live_or_saved_config(
    settings_client, key, value
):
    client, preset = settings_client
    original = preset.read_text()
    values = transition_values(get_config())
    values[key] = value
    assert client.put("/api/kiosk/settings", json=values).status_code == 422
    assert get_config() == KioskConfig()
    assert preset.read_text() == original


def test_debounce_cannot_exceed_approach_hold(settings_client):
    client, _ = settings_client
    values = transition_values(get_config())
    values["approach_entry_debounce"] = 3
    assert client.put("/api/kiosk/settings", json=values).status_code == 422
    assert get_config() == KioskConfig()


def test_active_session_keeps_its_boundaries(settings_client, monkeypatch):
    client, preset = settings_client
    original = preset.read_text()
    monkeypatch.setattr(runtime, "_current_state", "active")
    values = transition_values(replace(get_config(), session_max_seconds=180))
    response = client.put("/api/kiosk/settings", json=values)
    assert response.status_code == 409
    assert get_config() == KioskConfig()
    assert preset.read_text() == original


def test_disk_failure_does_not_apply_unsaved_settings(settings_client, monkeypatch):
    client, _ = settings_client

    def fail(_):
        raise OSError("read-only preset")

    monkeypatch.setattr("openjarvis.kiosk.operator_config.save_kiosk_config", fail)
    values = transition_values(replace(get_config(), popup_timeout=12))
    assert client.put("/api/kiosk/settings", json=values).status_code == 500
    assert get_config() == KioskConfig()


def test_environment_overrides_saved_values(settings_client, monkeypatch):
    client, _ = settings_client
    values = transition_values(replace(get_config(), session_max_seconds=180))
    assert client.put("/api/kiosk/settings", json=values).status_code == 200
    monkeypatch.setenv("KIOSK_SESSION_MINUTES", "5")
    assert load_kiosk_config().session_max_seconds == 300
    assert load_kiosk_config().session_warning_seconds == 240


def test_without_a_preset_settings_use_the_config_directory(
    settings_client, tmp_path, monkeypatch
):
    client, _ = settings_client
    monkeypatch.delenv("OPENJARVIS_CONFIG")
    monkeypatch.setattr(
        "openjarvis.kiosk.operator_config.get_config_dir", lambda: tmp_path
    )
    values = transition_values(replace(get_config(), decline_cooldown_seconds=20))
    assert client.put("/api/kiosk/settings", json=values).status_code == 200
    assert (tmp_path / "kiosk-settings.toml").is_file()
    assert load_kiosk_config().decline_cooldown_seconds == 20


def test_absence_boundary_above_one_minute_still_ends_the_session(settings_client):
    client, _ = settings_client
    values = transition_values(replace(get_config(), leave_sustain_seconds_active=100))
    assert client.put("/api/kiosk/settings", json=values).status_code == 200
    history = EventHistory(max_age_seconds=180)
    history.push(VisionEvent(kind="person_near", ts=0, nearest_m=0.5, track_id=1))
    for now in range(1, 101):
        history.push(VisionEvent(kind="no_person", ts=now, nearest_m=0, track_id=-1))
    assert evaluate_state(history, 99, "active", None, 0, None)[0] == "active"
    assert evaluate_state(history, 100, "active", None, 0, None)[0] == "cleanup"
