"""Local playback must never hold up Gemini's audio feed."""

import asyncio
import subprocess
import sys
import threading
import time
from types import SimpleNamespace

import pytest
from pipecat.frames.frames import InputAudioRawFrame
from pipecat.processors.frame_processor import FrameDirection

from openjarvis.server.voice.routes import _transcriber
from openjarvis.server.voice.transcription import SttAudioFrame

PCM = b"\x34\x12" * 320


def wait_for(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, (
            "local playback did not reach expected state"
        )
        time.sleep(0.01)


@pytest.fixture
def monitor(monkeypatch, tmp_path):
    from openjarvis.server.voice import target_audio_monitor as module

    output = tmp_path / "heard.pcm"
    processes = []
    rates = []

    def player(rate):
        rates.append(rate)
        process = subprocess.Popen(
            [
                sys.executable,
                "-u",
                "-c",
                (
                    "import os,sys\n"
                    "with open(sys.argv[1], 'ab', buffering=0) as out:\n"
                    " while chunk := os.read(0, 640): out.write(chunk)\n"
                ),
                str(output),
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(module, "_open_player", player)
    local = module.LocalTargetAudioMonitor()
    yield local, output, processes, rates
    local.close()
    for process in processes:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=2)


def test_off_never_opens_output_or_retains_pcm(monitor):
    local, output, processes, _ = monitor
    for _ in range(100):
        local.offer(PCM, 16000)
    assert not processes and not output.exists()
    assert local.snapshot()["queued_ms"] == 0
    assert local.snapshot()["enabled"] is False


def test_on_plays_the_exact_pcm_and_off_stops_the_player(monitor):
    local, output, processes, rates = monitor
    local.set_enabled(True)
    local.offer(PCM, 16000)
    local.offer(bytes(len(PCM)), 16000)
    wait_for(lambda: output.exists() and output.stat().st_size == len(PCM) * 2)
    assert output.read_bytes() == PCM + bytes(len(PCM))
    assert rates == [16000]
    local.set_enabled(False)
    assert processes[0].poll() is not None
    local.offer(PCM, 16000)
    assert output.read_bytes() == PCM + bytes(len(PCM))
    local.set_enabled(True)
    local.offer(b"\x78\x56" * 320, 16000)
    wait_for(lambda: output.stat().st_size == len(PCM) * 3)
    assert output.read_bytes()[len(PCM) * 2 :] == b"\x78\x56" * 320


def test_missing_device_disables_monitor_and_can_be_retried(monitor, monkeypatch):
    from openjarvis.server.voice import target_audio_monitor as module

    local, _, _, _ = monitor
    player = module._open_player

    def missing(rate):
        raise OSError("no local audio device")

    monkeypatch.setattr(module, "_open_player", missing)
    local.set_enabled(True)
    local.offer(PCM, 16000)
    wait_for(lambda: local.snapshot()["status"] == "error")
    assert local.snapshot()["enabled"] is False
    assert "no local audio device" in local.snapshot()["error"]
    monkeypatch.setattr(module, "_open_player", player)
    local.set_enabled(True)
    local.offer(PCM, 16000)
    wait_for(lambda: local.snapshot()["status"] == "playing")


def test_stalled_player_has_bounded_memory_and_teardown(monitor, monkeypatch):
    from openjarvis.server.voice import target_audio_monitor as module

    local, _, processes, _ = monitor

    def stalled(rate):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(module, "_open_player", stalled)
    local.set_enabled(True)
    local.offer(PCM, 16000)
    wait_for(lambda: bool(processes))
    for _ in range(1000):
        local.offer(PCM, 16000)
        assert local.snapshot()["queued_ms"] <= 200
    assert local.snapshot()["dropped_frames"] > 0
    local.set_enabled(False)
    assert processes[0].poll() is not None
    assert local.snapshot()["queued_ms"] == 0


def test_monitor_never_waits_for_its_queue_lock(monitor):
    local, _, _, _ = monitor
    local.set_enabled(True)
    run = local._run
    done = threading.Event()
    run.lock.acquire()

    def offer():
        local.offer(PCM, 16000)
        done.set()

    thread = threading.Thread(target=offer)
    try:
        thread.start()
        assert done.wait(0.5), "PCM producer waited for the monitor worker"
    finally:
        run.lock.release()
        thread.join(timeout=2)
    assert local.snapshot()["dropped_frames"] == 1


def test_slow_start_does_not_play_stale_audio(monitor, monkeypatch):
    from openjarvis.server.voice import target_audio_monitor as module

    local, output, _, _ = monitor
    player = module._open_player

    def slow_start(rate):
        time.sleep(0.3)
        return player(rate)

    monkeypatch.setattr(module, "_open_player", slow_start)
    local.set_enabled(True)
    local.offer(PCM, 16000)
    wait_for(lambda: local.snapshot()["dropped_frames"] > 0)
    local.offer(b"\x78\x56" * 320, 16000)
    wait_for(lambda: output.exists() and output.stat().st_size == len(PCM))
    assert output.read_bytes() == b"\x78\x56" * 320


def test_player_uses_local_socket_and_the_stt_pcm_format(monkeypatch):
    from openjarvis.server.voice import target_audio_monitor as module

    commands = []
    monkeypatch.setenv("PULSE_SERVER", "tcp:remote.example")
    monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        module.subprocess, "check_output", lambda *args, **kwargs: "alsa_output.test\n"
    )

    def start(command, **kwargs):
        commands.append((command, kwargs))

    monkeypatch.setattr(module.subprocess, "Popen", start)
    module._open_player(24000)
    command, options = commands[0]
    assert "--server=unix:/run/user/1000/pulse/native" in command
    assert "--format=s16le" in command and "--channels=1" in command
    assert "--rate=24000" in command and "--raw" in command
    assert "--device=alsa_output.test" in command
    assert "shell" not in options


def test_null_default_sink_does_not_claim_hardware_playback(monkeypatch):
    from openjarvis.server.voice import target_audio_monitor as module

    monkeypatch.setattr(module.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(
        module.subprocess, "check_output", lambda *args, **kwargs: "auto_null\n"
    )
    with pytest.raises(OSError, match="No hardware audio output"):
        module._open_player(16000)


def test_stalled_pipe_eventually_disables_playback(monitor, monkeypatch):
    from openjarvis.server.voice import target_audio_monitor as module

    local, _, processes, _ = monitor

    def stalled(rate):
        process = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        processes.append(process)
        return process

    monkeypatch.setattr(module, "_open_player", stalled)
    local.set_enabled(True)
    deadline = time.monotonic() + 3
    while local.enabled and time.monotonic() < deadline:
        local.offer(PCM, 16000)
        time.sleep(0.01)
    assert local.snapshot()["status"] == "error"
    assert "stalled" in local.snapshot()["error"]
    wait_for(lambda: processes[0].poll() is not None)


@pytest.fixture
def stt(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("OPENJARVIS_CONFIG", raising=False)
    monkeypatch.delenv("OPENJARVIS_STT_CAPTURE_DIR", raising=False)
    service = _transcriber()
    service._sample_rate = 16000
    return service


@pytest.mark.asyncio
async def test_stt_exposes_a_disabled_session_monitor(stt):
    assert getattr(stt, "target_audio_monitor", None) is not None
    assert stt.target_audio_monitor.snapshot()["enabled"] is False
    await stt.cleanup()


@pytest.mark.asyncio
async def test_only_masked_stt_audio_reaches_local_output(stt, monitor):
    local, output, _, _ = monitor
    stt.target_audio_monitor = local
    local.set_enabled(True)
    stt.enable_masked_feed(0.5)
    sent, live = [], []

    async def send_realtime_input(**kwargs):
        sent.append(kwargs["audio"].data)

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        live.append(frame)

    stt._session = SimpleNamespace(send_realtime_input=send_realtime_input)
    stt.push_frame = push
    raw = InputAudioRawFrame(audio=PCM, sample_rate=16000, num_channels=1)
    silence = bytes(len(PCM))
    await stt.process_frame(raw, FrameDirection.DOWNSTREAM)
    await stt.process_frame(SttAudioFrame(audio=silence), FrameDirection.DOWNSTREAM)
    await asyncio.to_thread(
        wait_for, lambda: output.exists() and output.stat().st_size == len(PCM)
    )
    assert live == [raw]
    assert sent == [silence]
    assert output.read_bytes() == silence


@pytest.mark.asyncio
async def test_monitor_enqueue_failure_does_not_change_gemini_audio(
    stt, monitor, monkeypatch
):
    local, _, _, _ = monitor
    stt.target_audio_monitor = local
    local.set_enabled(True)

    def broken(*args):
        raise RuntimeError("broken monitor")

    monkeypatch.setattr(local, "offer", broken)
    sent = []

    async def send_realtime_input(**kwargs):
        sent.append(kwargs["audio"].data)

    stt._session = SimpleNamespace(send_realtime_input=send_realtime_input)
    async for _ in stt.run_stt(PCM):
        pass
    assert sent == [PCM]
    assert local.snapshot()["enabled"] is False


@pytest.mark.asyncio
async def test_stt_cleanup_stops_local_output(stt, monitor):
    local, _, processes, _ = monitor
    stt.target_audio_monitor = local
    local.set_enabled(True)
    local.offer(PCM, 16000)
    await asyncio.to_thread(wait_for, lambda: bool(processes))
    await stt.cleanup()
    assert processes[0].poll() is not None
    assert local.snapshot()["enabled"] is False


@pytest.mark.asyncio
async def test_cleanup_cancels_finalization_before_waiting_for_monitor(
    stt, monkeypatch
):
    finalized = []

    async def pending_finalize():
        await asyncio.sleep(0.02)
        finalized.append(True)

    def slow_close():
        time.sleep(0.1)

    monkeypatch.setattr(stt.target_audio_monitor, "close", slow_close)
    stt._finalize_task = asyncio.create_task(pending_finalize())
    await stt.cleanup()
    assert finalized == [], "Monitor teardown delayed cancellation of STT finalization"


@pytest.mark.asyncio
async def test_pipeline_console_controls_the_stt_monitor(stt):
    from unittest.mock import MagicMock

    from openjarvis.server.voice.pipeline import build_voice_pipeline
    from openjarvis.server.voice.speaker import FaceTrackBuffer, SpeakerSettings
    from openjarvis.server.voice.speaker_vision import VisionAudioBridge

    bridge = VisionAudioBridge("ws://localhost:9876", "session", FaceTrackBuffer())
    build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
        speaker=SpeakerSettings(enabled=True, diarizer="sortformer", stt_mask=True),
        diarizer=SimpleNamespace(chunk_samples=320, frame_secs=0.08),
        vision_audio=bridge,
    )
    try:
        bridge.console.tune("target_audio_monitor", True)
        assert stt.target_audio_monitor.enabled
        bridge.console.tune("target_audio_monitor", False)
        assert not stt.target_audio_monitor.enabled
    finally:
        await stt.cleanup()


@pytest.mark.asyncio
async def test_monitor_hears_post_tse_output_and_rejected_overlap_silence(stt, monitor):
    import numpy as np

    from openjarvis.server.voice.speaker import AudioOnlyGate, SpeakerSettings, Verdict
    from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor

    local, output, _, _ = monitor
    stt.target_audio_monitor = local
    local.set_enabled(True)
    stt.enable_masked_feed(0.01)
    sent = []

    async def send_realtime_input(**kwargs):
        sent.append(kwargs["audio"].data)

    stt._session = SimpleNamespace(send_realtime_input=send_realtime_input)

    class Separator:
        window_samples = 16000
        enroll_samples = 320

        def separate(self, mix, enroll):
            return np.full(len(mix), 0.5, np.float32)

    processor = SpeakerAudioProcessor(
        diarizer=SimpleNamespace(frame_secs=0.08),
        gate=AudioOnlyGate(SpeakerSettings(enabled=True)),
        stt_delay_secs=0.01,
        separator=Separator(),
    )

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        await stt.process_frame(frame, direction)

    processor.push_frame = push
    processor._add_enrollment(np.ones(320, np.int16) * 300)
    for i, verdict in enumerate((Verdict.UNCERTAIN, Verdict.REJECT)):
        start = 100 + i * 0.08
        processor._remember_verdict(start, verdict, overlap=True)
        processor._stt_line.append(
            (
                start + 0.02,
                InputAudioRawFrame(
                    audio=PCM,
                    sample_rate=16000,
                    num_channels=1,
                ),
            )
        )
    await processor._release_stt(200)
    await processor._flush_held()
    expected = b"\xff\x3f" * 320 + bytes(len(PCM))
    await asyncio.to_thread(
        wait_for, lambda: output.exists() and output.stat().st_size == len(expected)
    )
    assert b"".join(sent) == expected
    assert output.read_bytes() == expected
