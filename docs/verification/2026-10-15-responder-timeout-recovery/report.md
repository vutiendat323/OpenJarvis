# Responder timeout recovery

User chose to fix the existing DeepSeek API flow. Keep the model, prompt, tool
contract and two-second decision deadline. No canned acknowledgement text.

## Root cause and behavior

The previous flow canceled the entire Responder stream at two seconds. Its buffered
acknowledgement disappeared even if a valid delegation arrived shortly afterward,
while the Agent was still working. The Agent started only after that deadline.

The decision deadline still starts the Agent at two seconds. The same Responder
request can now finish its delegation while the Agent works. A complete filler may
be queued before the first Agent text. Once the Agent starts producing text, late
filler is suppressed, including when the speech projector is buffering an incomplete
fragment. A late standalone answer never replaces or interrupts the running Agent.

Provider streaming and the pending filler are owned by the turn and canceled on
delegation, Agent-first speech, interruption, completion or error. Filler and Agent
speech frames are serialized. `PREAMBLE_SPOKEN` changes only when filler is queued;
the Agent receives the existing acknowledgement note before inference begins.

Production change: `src/openjarvis/server/voice/llm.py` only. No model, provider,
prompt, timeout or merchant workflow change. New `voice_preamble_late` log lines
distinguish recovered filler from Agent-first/empty/stale outcomes; the original
`voice_preamble outcome=timeout` still means the routing deadline was exceeded,
not necessarily that no Responder speech followed.

## Verification

The initial regression used the real VoiceResponder and speech pipeline with a
controlled model stream: filler text arrived, delegation waited for the Agent to
start after fallback, and the old code lost the filler. Observed the expected
failure before editing production code. A second red-first regression caught late
filler following an Agent fragment held by the speech projector.

Coverage also checks late standalone answers, provider errors, a never-returning
stream, interruption after fallback, stream cleanup, early delegation, direct hear
checks, carried requests, event turns and progress suppression.

- Focused voice/Responder/background/progress suites: **98 passed**.
- Broader voice, lifecycle, authority, carry-over and progress/tool-start suites:
  **248 passed, 1 skipped**. The opt-in remote checkout acceptance case was disabled.
- Ruff check/format and `git diff --check`: pass.
- Independent read-only review: no concrete remaining findings in scope.

## Two real DeepSeek smoke calls

`provider_smoke.py` makes exactly two paid synthetic Responder calls. The Agent is
a local fixture that waits three seconds and has no merchant tools. It uses the
unchanged configured prompt and thinking-disabled `deepseek-flash`.

| Case | First provider text | Delegation | Agent starts | Filler queued | Fixture result queued |
|---|---:|---:|---:|---:|---:|
| Vietnamese menu | 2.8395 s | 3.0256 s | 2.0019 s | 3.0298 s | 5.0037 s |
| English menu | 2.8379 s | 2.9566 s | 2.0010 s | 2.9569 s | 5.0025 s |

Both crossed the unchanged decision deadline and both recovered their model-written
acknowledgement before the fixture Agent's result. These are service speech-frame
times, not microphone-to-audio playback measurements. Two calls are a smoke check,
not a provider latency percentile or a guarantee that every timeout is recoverable.
The old branch would discard these late acknowledgements and leave only the fixture
result. DeepSeek time to first token itself is unchanged by this patch.

Exact measurements and synthetic text: `provider-smoke.json`. No credential is
included. Running the reproducer again incurs two additional model calls.

No real merchant order/payment requests, production stack restart, deployment or
commit. The running microphone/WebRTC/TTS stack has not loaded this patch.

## Self-evaluation

| Criterion | Score | Evidence / next improvement |
|---|---:|---|
| Accuracy | 4/5 | Two real API recoveries plus deterministic tests; audio playback not measured. |
| Completeness | 4/5 | Ownership, cancellation and speech ordering covered; live stack replay remains. |
| Clarity | 5/5 | Distinguishes routing deadline, recovered filler and upstream model latency. |
| Actionability | 5/5 | Patch, regression tests and bounded smoke reproducer are available. |
| Conciseness | 4/5 | Behavior confined to the voice LLM service and verification artifacts. |
