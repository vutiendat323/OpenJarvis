"""The picker must change synthesis and preserve the existing kiosk preset."""

import json
from types import SimpleNamespace

import numpy as np
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from openjarvis.kiosk import operator_config
from openjarvis.kiosk.config import KioskConfig
from openjarvis.kiosk.routes import router
from openjarvis.server.voice import tts_engine


@pytest.fixture
def voice_settings(tmp_path, monkeypatch):
    preset = tmp_path / "kiosk.toml"
    preset.write_text(
        "[voice.speaker]\nanchor_max_m = 1.5\n[kiosk]\npopup_timeout = 12\n"
    )
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(preset))
    monkeypatch.setenv("OPENJARVIS_LOCAL_TTS_ARTIFACT_DIR", str(tmp_path))
    catalogue = tmp_path / "voices.json"
    catalogue.write_text(json.dumps({"presets": {"Trúc Ly": {}, "Mai Anh": {}}}))
    monkeypatch.setattr(
        tts_engine,
        "_distribution",
        lambda _: SimpleNamespace(locate_file=lambda _: catalogue),
        raising=False,
    )
    artifact = tts_engine.LocalVieNeuArtifact(
        root=tmp_path,
        model_dir=tmp_path,
        onnx_dir=tmp_path,
        codec_dir=tmp_path,
        revision="a" * 40,
        voice="Trúc Ly",
        style="tu_nhien",
        model_repo="model",
        model_revision="a" * 40,
        codec_repo="codec",
        codec_revision="b" * 40,
    )
    monkeypatch.setattr(tts_engine, "load_accepted_vieneu_artifact", lambda _: artifact)
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        yield client, preset, artifact


def test_picker_lists_installed_voices_and_saves_selection(voice_settings):
    client, preset, _ = voice_settings
    assert client.get("/api/kiosk/voice").json() == {
        "voice": "Trúc Ly",
        "voices": ["Trúc Ly", "Mai Anh"],
    }
    response = client.put("/api/kiosk/voice", json={"voice": "Mai Anh"})
    assert response.status_code == 200
    assert response.json()["voice"] == "Mai Anh"
    assert client.get("/api/kiosk/voice").json()["voice"] == "Mai Anh"
    assert operator_config.load_tts_voice("Trúc Ly") == "Mai Anh"
    operator_config.save_kiosk_config(KioskConfig())
    assert operator_config.load_tts_voice("Trúc Ly") == "Mai Anh"
    assert "anchor_max_m = 1.5" in preset.read_text()


@pytest.mark.parametrize("voice", ["unknown", "", 123, None])
def test_invalid_voice_cannot_change_the_saved_selection(voice_settings, voice):
    client, preset, _ = voice_settings
    original = preset.read_text()
    assert client.put("/api/kiosk/voice", json={"voice": voice}).status_code == 422
    assert preset.read_text() == original


def test_save_failure_keeps_the_previous_voice(voice_settings, monkeypatch):
    client, preset, _ = voice_settings
    original = preset.read_text()

    def fail(*_):
        raise OSError("read-only preset")

    monkeypatch.setattr(operator_config.os, "replace", fail)
    assert client.put("/api/kiosk/voice", json={"voice": "Mai Anh"}).status_code == 500
    assert preset.read_text() == original
    assert client.get("/api/kiosk/voice").json()["voice"] == "Trúc Ly"


def test_missing_artifact_disables_voice_selection(voice_settings, monkeypatch):
    client, _, _ = voice_settings
    monkeypatch.delenv("OPENJARVIS_LOCAL_TTS_ARTIFACT_DIR")
    assert client.get("/api/kiosk/voice").status_code == 503
    assert client.put("/api/kiosk/voice", json={"voice": "Mai Anh"}).status_code == 503


@pytest.mark.asyncio
async def test_saved_voice_reaches_warm_renderer_without_changing_current_speech(
    voice_settings,
):
    client, _, artifact = voice_settings
    spoken = []
    runtimes = []

    class Runtime:
        def infer_stream(self, text, *, voice, style):
            spoken.append((text, voice))
            yield np.zeros(48, dtype=np.float32)
            yield np.zeros(48, dtype=np.float32)

    def factory(_):
        runtimes.append(Runtime())
        return runtimes[-1]

    renderer = tts_engine.VieNeuRealtimeTts(artifact, runtime_factory=factory)
    speech = renderer.stream_tts("First sentence")
    await anext(speech)
    assert client.put("/api/kiosk/voice", json={"voice": "Mai Anh"}).status_code == 200
    assert len([chunk async for chunk in speech]) == 1
    assert len([chunk async for chunk in renderer.stream_tts("Next sentence")]) == 2
    assert spoken == [("First sentence", "Trúc Ly"), ("Next sentence", "Mai Anh")]
    assert len(runtimes) == 1
    assert artifact.voice == "Trúc Ly"
