"""FSM boundaries must finish voice output before revoking the microphone."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjarvis.core.events import EventBus, EventType
from openjarvis.kiosk.effects import KioskDependencies, SideEffect, run_side_effects
from openjarvis.server.voice.pipeline import speak_kiosk_boundary


@pytest.mark.asyncio
async def test_cleanup_waits_for_audio_before_microphone_off():
    playback_done = asyncio.Event()
    ending = asyncio.Event()
    order = []
    bus = EventBus()
    bus.subscribe(EventType.KIOSK_STATE_CHANGED, lambda _: order.append("mic-off"))

    async def playback():
        await playback_done.wait()
        order.append("audio-finished")

    async def end_session():
        order.append("goodbye-queued")
        ending.set()

    task = asyncio.create_task(playback())
    state = SimpleNamespace(
        pipecat_voice_lifecycle=SimpleNamespace(end_session=end_session),
        pipecat_voice_task=task,
    )

    async def boundary(kind):
        await speak_kiosk_boundary(state, kind)

    effects = asyncio.create_task(
        run_side_effects(
            [
                SideEffect("tts_goodbye"),
                SideEffect("publish_state", {"state": "cleanup", "mic_enabled": False}),
            ],
            KioskDependencies(bus=bus, voice_boundary=boundary),
        )
    )
    try:
        await asyncio.wait_for(ending.wait(), timeout=1)
        assert order == ["goodbye-queued"]
        assert not effects.done()
        playback_done.set()
        await asyncio.wait_for(effects, timeout=1)
        assert order == ["goodbye-queued", "audio-finished", "mic-off"]
    finally:
        playback_done.set()
        await asyncio.gather(task, effects, return_exceptions=True)


@pytest.mark.asyncio
async def test_warning_routes_to_current_voice_and_greeting_is_not_duplicated():
    stop = asyncio.Event()
    task = asyncio.create_task(stop.wait())
    warning = AsyncMock()
    state = SimpleNamespace(
        pipecat_voice_task=task,
        pipecat_voice_lifecycle=SimpleNamespace(request_warning=warning),
    )
    try:
        await speak_kiosk_boundary(state, "tts_greeting")
        warning.assert_not_awaited()
        await speak_kiosk_boundary(state, "tts_warning")
        warning.assert_awaited_once()
    finally:
        stop.set()
        await task


@pytest.mark.asyncio
async def test_peer_departure_does_not_cancel_the_fsm():
    task = asyncio.create_task(asyncio.Event().wait())

    async def end_session():
        task.cancel()

    state = SimpleNamespace(
        pipecat_voice_task=task,
        pipecat_voice_lifecycle=SimpleNamespace(end_session=end_session),
    )
    await speak_kiosk_boundary(state, "tts_goodbye")
    assert task.cancelled()
    assert asyncio.current_task().cancelling() == 0


@pytest.mark.asyncio
async def test_hung_voice_has_bounded_shutdown_grace():
    task = asyncio.create_task(asyncio.Event().wait())
    state = SimpleNamespace(
        pipecat_voice_task=task,
        pipecat_voice_lifecycle=SimpleNamespace(end_session=AsyncMock()),
    )
    await speak_kiosk_boundary(state, "tts_goodbye", grace_secs=0.01)
    assert task.cancelled()


@pytest.mark.asyncio
async def test_real_tts_pipeline_drains_boundary_audio_before_end():
    pytest.importorskip("pipecat")
    from pipecat.frames.frames import EndFrame, StartFrame, TTSAudioRawFrame
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineWorker
    from pipecat.processors.frame_processor import FrameProcessor
    from pipecat.workers.runner import WorkerRunner

    from openjarvis.server.voice import lifecycle as cues
    from openjarvis.server.voice.lifecycle import VoiceSessionLifecycle
    from openjarvis.server.voice.tts import VieNeuTTSService

    rendered = []
    output = []
    started = asyncio.Event()

    class Renderer:
        async def stream_tts(self, text):
            await asyncio.sleep(0.02)
            rendered.append(text)
            yield SimpleNamespace(
                audio=b"\0\0" * 480, sample_rate_hz=48_000, channels=1
            )

    class Sink(FrameProcessor):
        async def process_frame(self, frame, direction):
            await super().process_frame(frame, direction)
            if isinstance(frame, StartFrame):
                started.set()
            elif isinstance(frame, TTSAudioRawFrame):
                await asyncio.sleep(0.01)
                output.append("audio")
            elif isinstance(frame, EndFrame):
                output.append("end")
            await self.push_frame(frame, direction)

    lifecycle = VoiceSessionLifecycle(warning_after_secs=1, limit_secs=2)
    worker = PipelineWorker(
        Pipeline([lifecycle, VieNeuTTSService(Renderer(), sample_rate=48_000), Sink()]),
        cancel_on_idle_timeout=False,
        enable_rtvi=False,
        enable_turn_tracking=False,
    )
    runner = WorkerRunner(handle_sigint=False, handle_sigterm=False)
    await runner.add_workers(worker)

    async def drive():
        await started.wait()
        await lifecycle.start_session()
        await lifecycle.request_warning()
        await lifecycle.end_session()

    driver = asyncio.create_task(drive())
    try:
        await asyncio.wait_for(runner.run(), timeout=3)
        await driver
        assert rendered == [cues.GREETING, cues.WARNING, cues.GOODBYE]
        assert output == ["audio", "audio", "audio", "end"]
    finally:
        driver.cancel()
        await asyncio.gather(driver, return_exceptions=True)
