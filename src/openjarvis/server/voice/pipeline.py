"""Compose one Pipecat pipeline for a Voice session."""

from __future__ import annotations

from typing import Any

# What VieNeu renders. The pipeline is told the same rate, and the output
# transport resamples from there if the peer negotiated something else.
VIENEU_SAMPLE_RATE_HZ = 48_000

# Keep transport endpointing and semantic turn detection separate. Silero's
# short stop promptly sends Gemini ``audio_stream_end`` so the final transcript
# starts arriving. Smart Turn then decides whether the pause completes the
# thought; an incomplete thought stays open until speech resumes or the longer
# fallback expires.
VAD_STOP_SECS = 0.2
MIN_TURN_SILENCE_SECS = 1.5
SMART_TURN_STOP_SECS = 3.0


class VoiceLeaseBusy(RuntimeError):
    """Raised when a Voice session is already running."""

    def __init__(self, status: str, fallback: str | None = None) -> None:
        super().__init__(status)
        self.status = status
        self.fallback = fallback


def claim_voice_lease(
    sessions: Any, *, chat_thread_id: str, execution_binding: Any
) -> Any:
    """Take the single Voice lease, or refuse so the caller falls back to text."""
    result = sessions.start_session(
        chat_thread_id=chat_thread_id, execution_binding=execution_binding
    )
    if result.session is None:
        raise VoiceLeaseBusy(result.status, getattr(result, "fallback", None))
    return result.session


async def return_to_listening_if_unheard(aggregator: Any) -> None:
    """Close a turn the stop timeout ended without a transcript.

    The kiosk shows "Processing" from the moment the customer stops talking,
    and only a reply clears it. With no words there is no reply, so tell the
    kiosk it is listening again. A turn that has words is answered as usual.
    """
    if aggregator.aggregation_string().strip():
        return
    from loguru import logger

    from openjarvis.server.voice.llm import voice_activity_frame

    logger.warning("voice: user turn ended with no transcript; listening again")
    await aggregator.push_frame(voice_activity_frame("listening"))


def build_voice_pipeline(
    *,
    connection: Any,
    binding: Any,
    renderer: Any,
    stt: Any,
    recall: tuple[Any, Any] | None = None,
    speaker: Any | None = None,
    diarizer: Any | None = None,
    faces: Any | None = None,
    separator: Any | None = None,
    vision_audio: Any | None = None,
    embedder: Any | None = None,
    bot_voiceprint: Any | None = None,
) -> Any:
    """Wire transport, VAD, STT, Agent, and voice into one runnable worker.

    ``speaker`` is the ``[voice.speaker]`` settings; None loads the preset's.
    ``diarizer`` is a loaded speaker diarizer, used only while the gate is on.
    ``faces`` is Vision's face-track buffer, used when ``vision_faces`` is on.
    ``separator`` is a loaded target-speaker extractor, used with ``stt_mask``.
    ``embedder`` is the speaker embedder for ``identity = "fusion"``;
    ``bot_voiceprint`` is the process's ``BotVoiceprint``.
    Returns the worker and the shared context, which the caller reads on
    teardown to write the conversation to the Chat thread.
    """
    from pipecat.audio.turn.smart_turn.base_smart_turn import SmartTurnParams
    from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import (
        LocalSmartTurnAnalyzerV3,
    )
    from pipecat.audio.vad.silero import SileroVADAnalyzer
    from pipecat.audio.vad.vad_analyzer import VADParams
    from pipecat.pipeline.pipeline import Pipeline
    from pipecat.pipeline.worker import PipelineParams, PipelineWorker
    from pipecat.processors.aggregators.llm_context import LLMContext
    from pipecat.processors.aggregators.llm_response_universal import (
        LLMContextAggregatorPair,
        LLMUserAggregatorParams,
        UserTurnStrategies,
    )
    from pipecat.transports.base_transport import TransportParams
    from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport

    from openjarvis.kiosk.runtime import customer_present
    from openjarvis.server.voice.lifecycle import VoiceSessionLifecycle
    from openjarvis.server.voice.llm import (
        OpenJarvisLLMService,
        VoiceTurnState,
    )
    from openjarvis.server.voice.speaker import SpeakerTracker, load_speaker_settings
    from openjarvis.server.voice.speaker_enhancement import (
        AudioFrameMetadataProcessor,
        build_audio_enhancer,
    )
    from openjarvis.server.voice.tts import VieNeuTTSService
    from openjarvis.server.voice.turn_detection import (
        ConfirmedTurnAnalyzerUserTurnStopStrategy,
        TargetSpeakerTurnStartStrategy,
    )

    if speaker is None:
        speaker = load_speaker_settings()
    enhancer = build_audio_enhancer(speaker)
    transport = SmallWebRTCTransport(
        webrtc_connection=connection,
        params=TransportParams(
            audio_in_enabled=True,
            audio_out_enabled=True,
            audio_in_filter=enhancer,
        ),
    )
    tracker = (
        SpeakerTracker(speaker, diarized=diarizer is not None)
        if speaker.enabled
        else None
    )
    # Disabled: Pipecat's default start strategies, exactly as before.
    # Enabled: one gate decides turn starts and barge-in; the stop strategy
    # closes turns through it and the LLM service reads its verdict.
    speaker_gate = (
        TargetSpeakerTurnStartStrategy(
            tracker=tracker,
            bargein_accept_frames=speaker.bargein_accept_frames,
        )
        if tracker is not None
        else None
    )
    speaker_audio = None
    if tracker is not None and diarizer is not None:
        from openjarvis.server.voice.speaker import AudioOnlyGate
        from openjarvis.server.voice.speaker_audio import SpeakerAudioProcessor

        # Gemini hears only a delayed, speaker-gated copy of the mic, so a
        # phone video or a bystander never lands in the customer's words.
        stt_delay = 0.0
        if speaker.stt_mask and hasattr(stt, "enable_masked_feed"):
            stt_delay = speaker.stt_mask_delay_secs
        fusion = speaker.identity == "fusion" and faces is not None
        if speaker.identity == "fusion" and faces is None:
            from loguru import logger

            logger.warning(
                "voice speaker identity=fusion without Vision faces; "
                "using the audio-only gate"
            )
        if fusion:
            from openjarvis.kiosk.runtime import current_state
            from openjarvis.server.voice.speaker_identity import FusionGate

            gate = FusionGate(
                speaker,
                faces,
                fsm_state=current_state,
                bot_voiceprint=(
                    (lambda: bot_voiceprint.embedding)
                    if bot_voiceprint is not None
                    else (lambda: None)
                ),
            )
        else:
            gate = AudioOnlyGate(speaker, faces=faces if speaker.vision_faces else None)
        speaker_audio = SpeakerAudioProcessor(
            diarizer=diarizer,
            stt_delay_secs=stt_delay,
            gate=gate,
            separator=separator,
            embedder=embedder if fusion else None,
            tracker=tracker,
            speaker_settings=speaker,
            audio_enhancer=enhancer,
            target_audio_monitor=getattr(stt, "target_audio_monitor", None),
            **({"vision_audio": vision_audio} if vision_audio is not None else {}),
        )
        if stt_delay:
            stt.enable_masked_feed(
                stt_delay,
                drain=speaker_audio.drain if separator is not None else None,
            )
    turn_state = VoiceTurnState()
    llm = OpenJarvisLLMService(
        binding,
        recall=recall,
        turn_state=turn_state,
        speaker_tracker=tracker,
        uncertain_allowed_tools=speaker.uncertain_allowed_tools,
    )
    context = LLMContext()
    # The VAD analyser belongs to the user aggregator, not the transport, and
    # interruption is always on — there is no flag to enable. Running it here,
    # locally, is what keeps barge-in independent of the round trip to the
    # transcription service. It also drives the transcript: the aggregator
    # broadcasts VADUserStoppedSpeakingFrame *upstream*, where GeminiSTTService
    # takes it as the signal to finalize the utterance. That is why the STT
    # goes before the aggregator in the pipeline below — behind it, the frame
    # would never reach the service and every transcript would wait out the
    # model's own silence window instead.
    #
    # realtime_service_mode stays pinned off. Left at None the pair infers the
    # mode from what the services announce, and realtime mode drives a
    # speech-to-speech turn with event-only signals, never pushing
    # LLMContextFrame — which is the only thing the OpenJarvis Agent runs on.
    # Inferring correctly today is not worth a silent no-answer tomorrow.
    # Smart Turn can finish a complete utterance at a VAD stop while keeping an
    # incomplete clause open. Its Vietnamese COMPLETE verdicts can still be
    # over-eager, so require sustained silence before accepting one; speech
    # resuming inside that window cancels the pending stop. Its stop_secs is the
    # maximum-silence fallback. wait_for_transcript stays on so a turn never
    # ends before its user message exists.
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=VAD_STOP_SECS)),
            user_turn_strategies=UserTurnStrategies(
                start=[speaker_gate] if speaker_gate is not None else None,
                stop=[
                    ConfirmedTurnAnalyzerUserTurnStopStrategy(
                        turn_analyzer=LocalSmartTurnAnalyzerV3(
                            params=SmartTurnParams(stop_secs=SMART_TURN_STOP_SECS)
                        ),
                        wait_for_transcript=True,
                        minimum_silence_secs=MIN_TURN_SILENCE_SECS,
                        speaker_gate=speaker_gate,
                    )
                ],
            ),
        ),
        realtime_service_mode=False,
    )
    user_aggregator.event_handler("on_user_turn_stop_timeout")(
        return_to_listening_if_unheard
    )
    on_bot_audio = None
    if (
        speaker.identity == "fusion"
        and embedder is not None
        and bot_voiceprint is not None
        and bot_voiceprint.embedding is None
    ):
        from openjarvis.server.voice.speaker_embedding import bot_audio_sink

        on_bot_audio = bot_audio_sink(embedder, bot_voiceprint)
    # Greeting, idle nudge, time limit and absence goodbye: fixed event -> TTS,
    # never through the Agent. The session starts once the client connects,
    # which only happens after the kiosk FSM accepted a customer.
    lifecycle = VoiceSessionLifecycle(presence=customer_present)

    @transport.event_handler("on_client_connected")
    async def _start_lifecycle(_transport: Any, _client: Any) -> None:
        await lifecycle.start_session()

    pipeline = Pipeline(
        [
            transport.input(),
            *([AudioFrameMetadataProcessor()] if enhancer is not None else []),
            *([speaker_audio] if speaker_audio is not None else []),
            stt,
            user_aggregator,
            llm,
            lifecycle,
            VieNeuTTSService(
                renderer, sample_rate=VIENEU_SAMPLE_RATE_HZ, on_audio=on_bot_audio
            ),
            transport.output(),
            assistant_aggregator,
        ]
    )
    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            audio_out_sample_rate=VIENEU_SAMPLE_RATE_HZ,
        ),
    )
    return worker, context
