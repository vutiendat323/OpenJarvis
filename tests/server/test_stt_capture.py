"""Opt-in local recording of what the kiosk STT hears, for accuracy review."""

from __future__ import annotations

import json
import wave

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.frames.frames import InputAudioRawFrame, TranscriptionFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.stt_service import STTService

from openjarvis.server.voice.routes import _transcriber
from openjarvis.server.voice.stt_capture import CAPTURE_DIR_ENV, SttCapture
from openjarvis.server.voice.transcription import SttAudioFrame


def _read(path):
    with wave.open(str(path)) as source:
        return source.getframerate(), source.getnchannels(), source.readframes(10**9)


def _session(directory):
    [session] = [p for p in directory.iterdir() if p.is_dir()]
    return session


def test_capture_writes_readable_wavs_and_timestamped_transcripts(tmp_path):
    capture = SttCapture(tmp_path)
    capture.raw(b"\x01\x00" * 16_000, 16_000)
    capture.heard(b"\x02\x00" * 8_000, 16_000)
    capture.transcript("về menu giúp mình")
    capture.close()

    session = _session(tmp_path)
    rate, channels, raw = _read(session / "raw.wav")
    assert (rate, channels, raw) == (16_000, 1, b"\x01\x00" * 16_000)
    assert _read(session / "heard.wav")[2] == b"\x02\x00" * 8_000
    [line] = (session / "transcripts.jsonl").read_text().splitlines()
    entry = json.loads(line)
    assert entry["text"] == "về menu giúp mình"
    assert entry["raw_s"] == pytest.approx(1.0)
    assert entry["heard_s"] == pytest.approx(0.5)


def test_capture_records_why_each_stretch_of_audio_was_kept_or_silenced(tmp_path):
    """verdicts.jsonl turns 'a word went missing' into 'this row, this verdict'."""
    capture = SttCapture(tmp_path)
    second = b"\x01\x00" * 16_000
    capture.heard(second, 16_000, verdict="ACCEPT", reason="kept")
    capture.heard(second, 16_000, verdict="ACCEPT", reason="kept")
    capture.heard(second, 16_000, verdict="None", reason="silenced_no_voice")
    capture.heard(second, 16_000)  # a caller that knows no verdict adds no line
    capture.close()

    lines = (_session(tmp_path) / "verdicts.jsonl").read_text().splitlines()
    assert [json.loads(line) for line in lines] == [
        {"heard_s": 0.0, "verdict": "ACCEPT", "reason": "kept"},
        {"heard_s": 2.0, "verdict": "None", "reason": "silenced_no_voice"},
    ]


def test_capture_wav_is_readable_before_close(tmp_path):
    """A killed process (launcher restart) must not lose the recording."""
    capture = SttCapture(tmp_path)
    capture.raw(b"\x01\x00" * 32_000, 16_000)

    assert len(_read(_session(tmp_path) / "raw.wav")[2]) == 64_000
    capture.close()


def test_capture_files_are_private(tmp_path):
    capture = SttCapture(tmp_path)
    capture.raw(b"\x00\x00" * 160, 16_000)
    capture.close()

    session = _session(tmp_path)
    assert oct(session.stat().st_mode & 0o777) == "0o700"
    assert oct((session / "raw.wav").stat().st_mode & 0o777) == "0o600"


def test_capture_is_off_unless_the_directory_is_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv(CAPTURE_DIR_ENV, raising=False)

    assert _transcriber()._capture is None
    assert list(tmp_path.iterdir()) == []


@pytest.mark.anyio
async def test_service_records_what_the_mic_heard_and_what_gemini_heard(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv(CAPTURE_DIR_ENV, str(tmp_path))
    stt = _transcriber()
    stt.enable_masked_feed(0.5)
    sent = []

    async def feed(frame, direction):
        sent.append(frame)

    async def parent_push(self, frame, direction=FrameDirection.DOWNSTREAM):
        pass

    monkeypatch.setattr(stt, "process_audio_frame", feed)
    monkeypatch.setattr(STTService, "push_frame", parent_push)
    live = InputAudioRawFrame(
        audio=b"\x01\x00" * 160, sample_rate=16000, num_channels=1
    )
    gated = SttAudioFrame(audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1)

    await stt.process_frame(live, FrameDirection.DOWNSTREAM)
    await stt.process_frame(gated, FrameDirection.DOWNSTREAM)
    await stt.push_frame(
        TranscriptionFrame(text="Wallet menu.", user_id="", timestamp="")
    )
    await stt.cleanup()

    session = _session(tmp_path)
    assert _read(session / "raw.wav")[2] == live.audio
    assert _read(session / "heard.wav")[2] == gated.audio
    entry = json.loads((session / "transcripts.jsonl").read_text())
    assert entry["text"] == "Wallet menu."


@pytest.mark.anyio
async def test_service_records_the_gate_decision_carried_by_the_audio(
    monkeypatch, tmp_path
):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv(CAPTURE_DIR_ENV, str(tmp_path))
    stt = _transcriber()
    stt.enable_masked_feed(0.5)

    async def feed(frame, direction):
        pass

    monkeypatch.setattr(stt, "process_audio_frame", feed)
    gated = SttAudioFrame(
        audio=b"\x00\x00" * 160,
        sample_rate=16000,
        num_channels=1,
        verdict="None",
        reason="silenced_no_voice",
    )

    await stt.process_frame(gated, FrameDirection.DOWNSTREAM)
    await stt.cleanup()

    [line] = (_session(tmp_path) / "verdicts.jsonl").read_text().splitlines()
    assert json.loads(line) == {
        "heard_s": 0.0,
        "verdict": "None",
        "reason": "silenced_no_voice",
    }


@pytest.mark.anyio
async def test_unmasked_service_records_the_same_audio_for_both(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv(CAPTURE_DIR_ENV, str(tmp_path))
    stt = _transcriber()

    async def parent_process(self, frame, direction):
        pass

    monkeypatch.setattr(STTService, "process_frame", parent_process)
    live = InputAudioRawFrame(
        audio=b"\x03\x00" * 160, sample_rate=16000, num_channels=1
    )

    await stt.process_frame(live, FrameDirection.DOWNSTREAM)
    await stt.cleanup()

    session = _session(tmp_path)
    assert _read(session / "raw.wav")[2] == live.audio
    assert _read(session / "heard.wav")[2] == live.audio


@pytest.mark.anyio
async def test_a_capture_failure_never_breaks_the_pipeline(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv(CAPTURE_DIR_ENV, str(tmp_path))
    stt = _transcriber()
    pushed = []

    def broken(*_args):
        raise OSError("disk full")

    async def feed(frame, direction):
        pushed.append(frame)

    monkeypatch.setattr(stt._capture, "raw", broken)
    monkeypatch.setattr(stt, "push_frame", feed)
    stt.enable_masked_feed(0.5)
    live = InputAudioRawFrame(
        audio=b"\x01\x00" * 160, sample_rate=16000, num_channels=1
    )

    await stt.process_frame(live, FrameDirection.DOWNSTREAM)
    await stt.process_frame(live, FrameDirection.DOWNSTREAM)

    assert pushed == [live, live]
    assert stt._capture is None
