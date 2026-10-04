"""What has to hold for the Gemini transcript to reach the Agent on time."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

pytest.importorskip("pipecat", reason="openjarvis[voice] not installed")

from pipecat.audio.turn.base_turn_analyzer import (
    BaseTurnAnalyzer,
    BaseTurnParams,
    EndOfTurnState,
)
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
    LocalSmartTurnAnalyzerV3,
)
from pipecat.clocks.system_clock import SystemClock
from pipecat.frames.frames import (
    EndFrame,
    InterimTranscriptionFrame,
    STTMetadataFrame,
    TranscriptionFrame,
    VADUserStartedSpeakingFrame,
    VADUserStoppedSpeakingFrame,
)
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregator,
    LLMUserAggregatorParams,
    UserTurnStrategies,
)
from pipecat.processors.frame_processor import (
    FrameDirection,
    FrameProcessor,
    FrameProcessorSetup,
)
from pipecat.services.google.gemini_live.stt import GeminiSTTService
from pipecat.turns.user_stop.speech_timeout_user_turn_stop_strategy import (
    SpeechTimeoutUserTurnStopStrategy,
)
from pipecat.turns.user_stop.turn_analyzer_user_turn_stop_strategy import (
    TurnAnalyzerUserTurnStopStrategy,
)
from pipecat.utils.asyncio.task_manager import TaskManager

from openjarvis.server.voice.gate import (
    DEFAULT_GEMINI_LIVE_MODEL,
    GEMINI_LIVE_MODEL_ENV,
)
from openjarvis.server.voice.pipeline import (
    MIN_TURN_SILENCE_SECS,
    SMART_TURN_STOP_SECS,
    VAD_STOP_SECS,
)
from openjarvis.server.voice.routes import _transcriber


class _SequencedTurnAnalyzer(BaseTurnAnalyzer):
    """Return deterministic semantic verdicts while Pipecat owns the turn."""

    def __init__(self, *states: EndOfTurnState):
        super().__init__(sample_rate=16_000)
        self._states = iter(states)

    @property
    def speech_triggered(self) -> bool:
        return True

    @property
    def params(self) -> BaseTurnParams:
        return BaseTurnParams()

    def append_audio(self, _buffer: bytes, _is_speech: bool) -> EndOfTurnState:
        return EndOfTurnState.INCOMPLETE

    async def analyze_end_of_turn(self):
        return next(self._states), None

    def clear(self):
        return None


def test_transcriber_defaults_to_the_transcription_model(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv(GEMINI_LIVE_MODEL_ENV, raising=False)

    service = _transcriber()

    assert isinstance(service, GeminiSTTService)
    assert service._settings.model == DEFAULT_GEMINI_LIVE_MODEL
    assert DEFAULT_GEMINI_LIVE_MODEL == "gemini-3.5-transcribe-live"


def test_transcriber_honours_the_model_override(monkeypatch):
    """The override is the rollback lever: a bad model is one env var away."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv(GEMINI_LIVE_MODEL_ENV, "some-other-model")

    assert _transcriber()._settings.model == "some-other-model"


def test_transcriber_prioritises_vietnamese_with_english_code_switching(monkeypatch):
    """The Live request should steer recognition without excluding English."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.delenv("OPENJARVIS_CONFIG", raising=False)

    service = _transcriber()
    transcription = service._build_live_config().input_audio_transcription

    assert transcription is not None
    assert transcription.language_codes == ["vi-VN", "en-US"]
    assert transcription.language_hints is None
    assert transcription.language_auto is None


def test_transcriber_loads_custom_vocabulary_from_the_active_preset(
    monkeypatch, tmp_path
):
    """Configured menu terms must reach Gemini's canonical request field."""
    config_path = tmp_path / "ordering-kiosk.toml"
    config_path.write_text(
        """
[voice.stt]
language_codes = ["vi-VN", "en-US"]
mode = "VERBATIM"
custom_vocabulary = ["bạc xỉu", "cà phê muối", "đá xay", "takeaway"]
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(config_path))

    service = _transcriber()
    transcription = service._build_live_config().input_audio_transcription

    assert transcription is not None
    assert transcription.language_codes == ["vi-VN", "en-US"]
    assert transcription.custom_vocabulary == [
        "bạc xỉu",
        "cà phê muối",
        "đá xay",
        "takeaway",
    ]
    assert transcription.mode == "VERBATIM"


def test_ordering_kiosk_preset_biases_only_loanword_vocabulary(monkeypatch):
    """Bias only English/loanword terms; Vietnamese terms over-bias the decoder.

    Common Vietnamese phrases in the list made Gemini force unclear audio
    into menu words (e.g. "bạc xỉu").
    """
    project_root = Path(__file__).parents[2]
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv(
        "OPENJARVIS_CONFIG",
        str(project_root / "configs/openjarvis/examples/ordering-kiosk-mcp.toml"),
    )

    transcription = _transcriber()._build_live_config().input_audio_transcription

    assert transcription is not None
    # en-US as a second hint made short Vietnamese phrases come back as
    # English ("Về menu" -> "Wallet menu"); the kiosk serves Vietnamese.
    assert transcription.language_codes == ["vi-VN"]
    vocabulary = set(transcription.custom_vocabulary or [])
    assert {
        "takeaway",
        "topping",
        "menu",
        "back menu",
        "Americano",
        "Espresso",
        "Croissant",
    } <= vocabulary
    assert all(term.isascii() for term in vocabulary), sorted(
        term for term in vocabulary if not term.isascii()
    )
    assert len(vocabulary) <= 100


def test_transcriber_waits_for_the_live_final_transcript(monkeypatch):
    """A late final transcript must not become a second Agent turn.

    The built-in Gemini P99 is 0.60 s. After the local VAD has already waited
    0.20 s that leaves only a 0.40 s safety wait, while this deployment observed
    service TTFB as high as 1.340 s. The service metadata drives the downstream
    turn-stop safety timer, so it must preserve a longer window than the
    user-pause timer.
    """
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")

    metadata = _transcriber().service_metadata_frame()

    assert metadata.ttfs_p99_latency == 1.6
    assert metadata.ttfs_p99_latency > 1.34


def test_stt_sits_upstream_of_the_vad_that_finalizes_it():
    """GeminiSTTService finalizes on the aggregator's VAD frame.

    That frame is broadcast *upstream*, so it only reaches the service while
    the service is ahead of the aggregator. Reorder these two and transcripts
    still arrive — just a silence window later, with nothing failing loudly.
    """
    from unittest.mock import MagicMock

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    # Only the wiring is under test, so everything the pipeline hangs off is a
    # stub; the transport registers event handlers on the connection at build,
    # and the STT stand-in has to be a real processor because Pipeline links it.
    stt = FrameProcessor()
    worker, _ = build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
    )
    # Walk downstream from the STT: finding the aggregator there is exactly
    # the property that makes its upstream broadcast reach the service.
    downstream = []
    node = getattr(stt, "_next", None)
    while node is not None:
        downstream.append(node)
        node = getattr(node, "_next", None)

    aggregator = next(
        (node for node in downstream if isinstance(node, LLMUserAggregator)), None
    )
    assert aggregator is not None, (
        "user aggregator is not downstream of the STT; its VAD broadcast "
        f"cannot reach it. Chain after the STT: {downstream}"
    )
    # Flush Gemini promptly, then let the semantic analyzer decide whether a
    # pause ends the turn. Replacing this with a fixed timeout splits compound
    # questions whenever a natural pause crosses that fixed boundary.
    strategy = aggregator._params.user_turn_strategies.stop[0]
    assert aggregator._params.vad_analyzer.params.stop_secs == VAD_STOP_SECS == 0.2
    assert isinstance(strategy, TurnAnalyzerUserTurnStopStrategy)
    assert isinstance(strategy._turn_analyzer, LocalSmartTurnAnalyzerV3)
    assert strategy._turn_analyzer.params.stop_secs == SMART_TURN_STOP_SECS == 3.0


@pytest.mark.anyio
async def test_final_transcript_fragments_dispatch_one_complete_agent_turn():
    """Provider fragments inside the pause window stay in one Agent turn.

    This exercises Pipecat's real cascade turn controller. A regression to
    realtime mode, or dispatching on the first final fragment, either emits no
    context frame or emits an incomplete/duplicate one.
    """
    from unittest.mock import MagicMock

    context = LLMContext()
    user_aggregator, _ = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            user_turn_strategies=UserTurnStrategies(
                stop=[
                    SpeechTimeoutUserTurnStopStrategy(
                        user_speech_timeout=0.7,
                        wait_for_transcript=True,
                    )
                ]
            ),
        ),
        realtime_service_mode=False,
    )

    task_manager = TaskManager()
    await user_aggregator.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=task_manager,
            pipeline_worker=MagicMock(),
        )
    )
    inference_count = 0

    @user_aggregator.event_handler("on_user_turn_inference_triggered")
    async def on_inference(_aggregator, _strategy):
        nonlocal inference_count
        inference_count += 1

    async def process(frame):
        await user_aggregator.process_frame(frame, FrameDirection.DOWNSTREAM)

    await process(STTMetadataFrame(service_name="gemini_stt", ttfs_p99_latency=1.2))
    await process(VADUserStartedSpeakingFrame())
    await process(
        InterimTranscriptionFrame(
            "Cho tôi một cà phê",
            user_id="customer",
            timestamp="2026-09-10T00:00:00Z",
        )
    )
    await process(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS))
    await process(
        TranscriptionFrame(
            "Cho tôi một cà phê,",
            user_id="customer",
            timestamp="2026-09-10T00:00:01Z",
            finalized=True,
        )
    )
    await process(
        TranscriptionFrame(
            "size large.",
            user_id="customer",
            timestamp="2026-09-10T00:00:01Z",
            finalized=True,
        )
    )
    await asyncio.sleep(0.75)

    assert inference_count == 1
    assert context.get_messages() == [
        {"role": "user", "content": "Cho tôi một cà phê, size large."}
    ]
    await process(EndFrame())


@pytest.mark.anyio
async def test_incomplete_clause_survives_a_natural_pause():
    """A semantic continuation must not dispatch at the old 0.7 s boundary.

    The analyzer verdict is deterministic here: the first clause is incomplete
    and the second completes the question. The real Pipecat aggregator still
    owns VAD, transcript aggregation, and turn dispatch.
    """
    from unittest.mock import MagicMock

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    stt = FrameProcessor()
    _, _ = build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
    )
    aggregator = getattr(stt, "_next", None)
    assert isinstance(aggregator, LLMUserAggregator)
    strategy = aggregator._params.user_turn_strategies.stop[0]
    assert isinstance(strategy, TurnAnalyzerUserTurnStopStrategy)
    strategy._minimum_silence_secs = VAD_STOP_SECS + 0.08
    strategy._turn_analyzer = _SequencedTurnAnalyzer(
        EndOfTurnState.INCOMPLETE,
        EndOfTurnState.COMPLETE,
    )

    task_manager = TaskManager()
    await strategy.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=task_manager,
            pipeline_worker=MagicMock(),
        )
    )
    inference_count = 0

    @strategy.event_handler("on_user_turn_inference_triggered")
    async def on_inference(_strategy):
        nonlocal inference_count
        inference_count += 1

    await strategy.handle_user_turn_started()
    await strategy.process_frame(
        STTMetadataFrame(service_name="gemini_stt", ttfs_p99_latency=1.6)
    )
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
    await strategy.process_frame(
        TranscriptionFrame(
            "Tôi có ba câu hỏi.",
            user_id="customer",
            timestamp="2026-09-10T00:00:00Z",
            finalized=True,
        )
    )
    await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS))
    await asyncio.sleep(0.8)
    assert inference_count == 0

    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
    await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS))
    await strategy.process_frame(
        TranscriptionFrame(
            "Câu thứ nhất là cà phê sữa là gì?",
            user_id="customer",
            timestamp="2026-09-10T00:00:02Z",
            finalized=True,
        )
    )
    await asyncio.sleep(0.09)
    assert inference_count == 1
    await strategy.cleanup()


@pytest.mark.anyio
async def test_complete_clause_is_cancelled_when_speech_resumes():
    """A false COMPLETE verdict must not split a natural compound question.

    The live failure was stricter than the semantic-INCOMPLETE case above:
    Smart Turn marked a Vietnamese clause COMPLETE and Gemini had finalized
    text available, but VAD detected more speech almost immediately afterward.
    """
    from unittest.mock import MagicMock

    from openjarvis.server.voice.pipeline import build_voice_pipeline

    stt = FrameProcessor()
    build_voice_pipeline(
        connection=MagicMock(),
        binding=MagicMock(),
        renderer=MagicMock(),
        stt=stt,
    )
    aggregator = getattr(stt, "_next", None)
    assert isinstance(aggregator, LLMUserAggregator)
    strategy = aggregator._params.user_turn_strategies.stop[0]
    assert strategy._minimum_silence_secs == MIN_TURN_SILENCE_SECS == 1.5
    # VAD reports the stop after 0.20 s has already elapsed. Compress the
    # remaining 1.30 s production confirmation window to 0.08 s in this test.
    strategy._minimum_silence_secs = VAD_STOP_SECS + 0.08
    strategy._turn_analyzer = _SequencedTurnAnalyzer(
        EndOfTurnState.COMPLETE,
        EndOfTurnState.COMPLETE,
    )

    task_manager = TaskManager()
    await strategy.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=task_manager,
            pipeline_worker=MagicMock(),
        )
    )
    inference_count = 0

    @strategy.event_handler("on_user_turn_inference_triggered")
    async def on_inference(_strategy):
        nonlocal inference_count
        inference_count += 1

    await strategy.handle_user_turn_started()
    await strategy.process_frame(
        STTMetadataFrame(service_name="gemini_stt", ttfs_p99_latency=1.6)
    )
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
    await strategy.process_frame(
        TranscriptionFrame(
            "Tôi có ba câu hỏi muốn hỏi bạn.",
            user_id="customer",
            timestamp="2026-09-10T00:00:00Z",
            finalized=True,
        )
    )
    await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS))
    await asyncio.sleep(0.04)
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
    await asyncio.sleep(0.06)
    assert inference_count == 0

    await strategy.process_frame(
        TranscriptionFrame(
            "Thứ nhất, cà phê sữa là gì?",
            user_id="customer",
            timestamp="2026-09-10T00:00:01Z",
            finalized=True,
        )
    )
    await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS))
    await asyncio.sleep(0.09)
    assert inference_count == 1
    await strategy.cleanup()


@pytest.mark.anyio
async def test_live_observed_late_final_does_not_start_a_second_turn(monkeypatch):
    """The observed 1.34 s Gemini final stays inside the safety deadline."""
    from unittest.mock import MagicMock

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    strategy = SpeechTimeoutUserTurnStopStrategy(
        user_speech_timeout=0.7,
        wait_for_transcript=True,
    )
    task_manager = TaskManager()
    await strategy.setup(
        FrameProcessorSetup(
            clock=SystemClock(),
            task_manager=task_manager,
            pipeline_worker=MagicMock(),
        )
    )
    inference_count = 0

    @strategy.event_handler("on_user_turn_inference_triggered")
    async def on_inference(_strategy):
        nonlocal inference_count
        inference_count += 1

    await strategy.handle_user_turn_started()
    await strategy.process_frame(_transcriber().service_metadata_frame())
    await strategy.process_frame(VADUserStartedSpeakingFrame(start_secs=0.2))
    await strategy.process_frame(
        TranscriptionFrame(
            "Cà phê sữa là gì?",
            user_id="customer",
            timestamp="2026-09-10T00:00:00Z",
            finalized=False,
        )
    )
    await strategy.process_frame(VADUserStoppedSpeakingFrame(stop_secs=VAD_STOP_SECS))
    await asyncio.sleep(1.14)
    assert inference_count == 0

    await strategy.process_frame(
        TranscriptionFrame(
            " Cà phê bạc xỉu là gì?",
            user_id="customer",
            timestamp="2026-09-10T00:00:01Z",
            finalized=True,
        )
    )
    assert inference_count == 1
    await strategy.cleanup()


@pytest.mark.anyio
async def test_masked_feed_keeps_realtime_audio_away_from_gemini(monkeypatch):
    """Live mic audio still reaches the VAD at once; Gemini hears only the
    delayed copy the speaker gate has silenced wherever it rejected speech."""
    from pipecat.frames.frames import InputAudioRawFrame

    from openjarvis.server.voice.transcription import SttAudioFrame

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stt = _transcriber()
    stt.enable_masked_feed(0.5)
    fed, pushed = [], []

    async def feed(frame, direction):
        fed.append(frame)

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    monkeypatch.setattr(stt, "process_audio_frame", feed)
    monkeypatch.setattr(stt, "push_frame", push)
    live = InputAudioRawFrame(
        audio=b"\x01\x00" * 160, sample_rate=16000, num_channels=1
    )
    masked = SttAudioFrame(audio=b"\x00\x00" * 160, sample_rate=16000, num_channels=1)

    await stt.process_frame(live, FrameDirection.DOWNSTREAM)
    await stt.process_frame(masked, FrameDirection.DOWNSTREAM)

    assert pushed == [live]
    assert [frame.audio for frame in fed] == [masked.audio]


def test_masked_feed_extends_the_final_transcript_wait(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stt = _transcriber()
    stt.enable_masked_feed(0.5)

    assert stt.service_metadata_frame().ttfs_p99_latency == pytest.approx(2.1)


@pytest.mark.anyio
async def test_masked_feed_finalizes_after_the_delayed_audio(monkeypatch):
    import time

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stt = _transcriber()
    stt.enable_masked_feed(0.05)
    calls = []

    async def fake_finalize(self):
        calls.append(time.monotonic())

    monkeypatch.setattr(GeminiSTTService, "_send_finalization_signal", fake_finalize)
    started = time.monotonic()
    await stt._send_finalization_signal()

    assert calls == []
    await asyncio.sleep(0.15)
    assert calls and calls[0] - started >= 0.05


@pytest.mark.anyio
async def test_final_transcripts_are_logged(monkeypatch):
    """The live check of the speaker gate reads what Gemini actually heard."""
    from loguru import logger
    from pipecat.frames.frames import TranscriptionFrame
    from pipecat.services.stt_service import STTService

    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stt = _transcriber()
    pushed, logged = [], []

    async def parent_push(self, frame, direction=FrameDirection.DOWNSTREAM):
        pushed.append(frame)

    monkeypatch.setattr(STTService, "push_frame", parent_push)
    sink = logger.add(lambda message: logged.append(message), level="INFO")
    try:
        frame = TranscriptionFrame(
            text="cho tôi một ly cà phê", user_id="", timestamp=""
        )
        await stt.push_frame(frame)
    finally:
        logger.remove(sink)

    assert pushed == [frame]
    assert any("cho tôi một ly cà phê" in m for m in logged)


@pytest.mark.anyio
async def test_finalization_waits_for_held_overlap_to_drain(monkeypatch):
    """Separated overlap must reach Gemini before the utterance is closed."""
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    stt = _transcriber()
    events = []

    async def drain():
        await asyncio.sleep(0.05)
        events.append("drained")

    async def fake_finalize(self):
        events.append("finalized")

    stt.enable_masked_feed(0.01, drain=drain)
    monkeypatch.setattr(GeminiSTTService, "_send_finalization_signal", fake_finalize)

    await stt._send_finalization_signal()
    await asyncio.sleep(0.2)

    assert events == ["drained", "finalized"]
    assert stt.service_metadata_frame().ttfs_p99_latency == pytest.approx(2.61)
