"""Operator observations follow the gate; live tuning preserves its identity."""

import json
from types import SimpleNamespace

import numpy as np
import pytest

from openjarvis.server.voice.speaker import FaceTrackBuffer, SpeakerSettings, Verdict
from openjarvis.server.voice.speaker_console import (
    SpeakerConsole,
    audio_levels,
    save_speaker_values,
)
from openjarvis.server.voice.speaker_identity import FusionGate
from openjarvis.server.voice.speaker_vision import VisionAudioBridge


def _console():
    settings = SpeakerSettings(identity="fusion", vision_asd=True)
    gate = FusionGate(settings, FaceTrackBuffer(), fsm_state=lambda: "active")
    gate.lock.observe_fsm("active", 1.0)
    for i in range(settings.lock_asd_frames):
        gate.lock.observe_row(1.0 + i * 0.08, [0], anchor=7, anchor_asd_accept=True)
    bridge = VisionAudioBridge("ws://localhost:9876", "session", FaceTrackBuffer())
    processor = SimpleNamespace(
        _gate=gate, _fusion=gate, _vision_audio=bridge, _bot_audible_until=0,
        _separator=None, _enrolled=0, _tracker=SimpleNamespace(bargein_blocked=2),
        stt_masked_no_verdict=3,
    )
    console = SpeakerConsole(processor, settings)
    bridge.console = console
    return console


def test_audio_observations_are_bounded_and_do_not_overflow_int16():
    assert audio_levels(b"pcm")["rms_dbfs"] is None
    silence = audio_levels(np.zeros(320, np.int16).tobytes())
    assert silence["rms_dbfs"] == -96 and silence["peak"] == 0
    loud = audio_levels(np.full(4000, -32768, np.int16).tobytes())
    assert loud["rms_dbfs"] == 0 and loud["peak"] == 1
    assert len(loud["waveform"]) <= 64


def test_live_thresholds_reach_the_gate_and_lock_without_resetting_identity():
    console = _console()
    gate = console.processor._gate
    assert gate.lock.locked
    before = (gate.lock.epoch, gate.lock.target_track, set(gate.lock.target_slots))
    console.tune("asd_accept_prob", 0.8)
    console.tune("voice_match", 0.75)
    console.tune("mouth_active", 0.6)
    assert gate._asd_accept == 0.8 and gate._mouth_active == 0.6
    assert gate.lock._s.voice_match == 0.75
    assert before == (gate.lock.epoch, gate.lock.target_track, gate.lock.target_slots)


@pytest.mark.parametrize("key,value", [
    ("voice_match", 0.4), ("voice_reject", 0.8), ("asd_accept_prob", 0.2),
    ("asd_reject_prob", 0.9), ("mouth_active", float("nan")),
    ("mouth_active", True), ("identity", "none"),
])
def test_invalid_live_changes_leave_the_gate_settings_intact(key, value):
    console = _console()
    before = console.settings
    with pytest.raises(ValueError):
        console.tune(key, value)
    assert console.settings is before
    assert console.processor._gate.settings is before


def test_snapshot_uses_actual_verdict_and_counters():
    console = _console()
    console.verdict(Verdict.REJECT, 12.0)
    snapshot = console.snapshot()
    assert snapshot["verdict"] == Verdict.REJECT.value
    assert snapshot["verdict_ts"] == 12
    assert snapshot["target_track"] == 7 and snapshot["locked"]
    assert snapshot["stt_masked_no_verdict_frames"] == 3
    assert snapshot["bargein_blocked"] == 2
    assert snapshot["audio"]["ts"] is None
    json.dumps(snapshot, allow_nan=False)


def test_save_updates_only_speaker_values_and_preserves_preset_comments(tmp_path):
    path = tmp_path / "preset.toml"
    path.write_text('# merchant configuration\n[agent]\nmax_turns = 8\n'
                    '[voice.speaker]\nvoice_match = 0.65 # tuned\n')
    save_speaker_values({"voice_match": 0.8}, str(path))
    source = path.read_text()
    assert "max_turns = 8" in source and "# merchant configuration" in source
    assert "voice_match = 0.8 # tuned" in source
    assert not list(tmp_path.glob(".console-*"))


def test_live_kiosk_distance_changes_engagement_and_survives_reload(
    tmp_path, monkeypatch,
):
    from openjarvis.kiosk.evaluate import evaluate_state, get_config, set_config
    from openjarvis.kiosk.events import EventHistory, VisionEvent
    from openjarvis.kiosk.operator_config import load_approach_threshold

    before = get_config()
    history = EventHistory()
    for timestamp in (1.0, 1.5):
        history.push(VisionEvent(kind="person_near", ts=timestamp, nearest_m=1.2,
                                 track_id=7, body_m=-1.0, facing=True))
    try:
        console = _console()
        assert evaluate_state(history, 1.5, "idle", None, None, None)[0] == "idle"
        console.tune("approach_threshold_m", 1.4)
        result = evaluate_state(history, 1.5, "idle", None, None, None)
        assert result[0] == "approaching"
        preset = tmp_path / "preset.toml"
        preset.write_text("[voice.speaker]\nvoice_match = 0.65\n")
        save_speaker_values(console.values(), str(preset))
        monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
        monkeypatch.delenv("KIOSK_APPROACH_THRESHOLD_M", raising=False)
        assert load_approach_threshold() == 1.4
        monkeypatch.setenv("KIOSK_APPROACH_THRESHOLD_M", "1.2")
        assert load_approach_threshold() == 1.2
    finally:
        set_config(before)


@pytest.mark.asyncio
async def test_console_commands_return_gate_confirmation():
    console = _console()
    bridge = console.processor._vision_audio
    bridge._stream_id = "stream"
    sent = []

    async def send(payload):
        sent.append(json.loads(payload))

    await bridge._console_command(SimpleNamespace(send=send), {
        "request_id": "request", "action": "set", "key": "voice_match", "value": 0.8,
    }, "stream")
    assert sent == [{"cmd": "voice_command_result", "stream_id": "stream",
                     "request_id": "request", "ok": True,
                     "key": "voice_match", "value": 0.8}]
    assert console.processor._gate.lock._s.voice_match == 0.8


@pytest.mark.asyncio
async def test_monitor_toggle_round_trip_is_boolean_and_never_saved(tmp_path):
    from openjarvis.server.voice.target_audio_monitor import LocalTargetAudioMonitor

    console = _console()
    console.target_audio_monitor = LocalTargetAudioMonitor()
    bridge = console.processor._vision_audio
    bridge._stream_id = "stream"
    sent = []

    async def send(payload):
        sent.append(json.loads(payload))

    before = console.settings
    try:
        for enabled in (True, False):
            await bridge._console_command(SimpleNamespace(send=send), {
                "request_id": "toggle", "action": "set",
                "key": "target_audio_monitor", "value": enabled,
            }, "stream")
            assert sent[-1]["ok"] and sent[-1]["value"] is enabled
            assert console.snapshot()["target_audio_monitor"]["enabled"] is enabled
        assert console.settings is before
        preset = tmp_path / "preset.toml"
        preset.write_text("[voice.speaker]\nvoice_match = 0.65\n")
        save_speaker_values(console.values(), str(preset))
        assert "target_audio_monitor" not in preset.read_text()
        for invalid in (0, 1, "true", None):
            with pytest.raises(ValueError):
                console.tune("target_audio_monitor", invalid)
    finally:
        console.target_audio_monitor.close()


@pytest.mark.asyncio
async def test_slow_monitor_control_does_not_block_voice_event_loop(monkeypatch):
    import asyncio
    import time

    from openjarvis.server.voice.target_audio_monitor import LocalTargetAudioMonitor

    console = _console()
    local = LocalTargetAudioMonitor()
    console.target_audio_monitor = local
    bridge = console.processor._vision_audio
    bridge._stream_id = "stream"
    original = local.set_enabled

    def slow_control(enabled):
        time.sleep(0.2)
        original(enabled)

    monkeypatch.setattr(local, "set_enabled", slow_control)

    async def send(payload):
        pass

    task = asyncio.create_task(bridge._console_command(SimpleNamespace(send=send), {
        "action": "set", "key": "target_audio_monitor", "value": True,
    }, "stream"))
    try:
        await asyncio.sleep(0.03)
        assert not task.done(), "Device control blocked the Voice event loop"
        await task
    finally:
        local.close()
