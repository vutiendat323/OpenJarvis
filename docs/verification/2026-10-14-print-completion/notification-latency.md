# Notification latency

User reported more than five seconds between physical printing and hearing
the invoice-ready notification. The event turn used the main Agent model;
the configured fast voice responder did not apply to event turns.

## Change

Event turns now bind the same NativeAgentRuntime and Agent to the configured
`voice.responder.model` for that invocation only. The local configuration uses
`deepseek-flash`. Its existing reasoning-off options apply from the first
notification round. No responder acknowledgement is generated: the notification
itself remains the Agent's reply through BackgroundReporter and the normal
Pipecat event-turn pipeline.

Customer turns retain their original model binding. Runtime ownership,
interruption/cancellation, payment checks, original CUPS job verification,
checkout and printing behavior are preserved. Disabling the voice responder
keeps event turns on the original binding.

## Real API benchmark, 2026-10-14

Six sequential calls used CloudEngine, OrchestratorAgent and NativeAgentRuntime
with the current ordering prompt and a synthetic verified PRINT_COMPLETED.
The baseline used the main model; the changed path used the fast model with
thinking disabled. Both used the same Agent. Tool definitions were omitted
(`tools=[]`), history was empty, and no merchant or physical printer was called.
These inputs isolate model response latency, not deployed checkout latency.

| Paired sample | Main model first text | Fast model first text |
| --- | ---: | ---: |
| 1 | 7.384 s | 0.999 s |
| 2 | 2.593 s | 0.937 s |
| 3 | 5.031 s | 0.625 s |
| Median | 5.031 s | 0.937 s |

Full reply times for the fast model were 1.280, 1.228 and 0.970 seconds.
All three replies included invoice readiness, approximately 15 minutes and
counter pickup, and the menu/new-order question. Replies were model-generated.
The small sample does not establish production percentiles.

Raw synthetic results and the standalone benchmark script are in
`/tmp/jarvis-notification-latency-20261014.json` and
`/tmp/jarvis-notification-latency-20261014.py` respectively; those temporary
files may be removed by normal cleanup.

## Regression verification

- Failing tests first reproduced a blocked main-model notification and absence
  of the scoped binding/reasoning-off behavior; all passed after the change.
- Final relevant regression run across 20 suites: **449 passed, 1 skipped**.
- Independent review: **73 tests passed**, no concrete blocking findings.
- Ruff and `git diff --check`: passed.
- Integration now verifies PRINT_COMPLETED reaches the fast-model binding
  through the real runtime, and the customer binding remains unchanged. It
  retains active-customer deferral, duplicate delivery protection, model speech
  generation, paid receipt status, and single-job print assertions.

No service restart, commit, or push was performed. Local HEAD remains
`5a75ba5c`. The active kiosk has not been reloaded by this task.

## Practical limits

The numbers measure first Agent text, not first audible audio. CUPS polling,
an active customer turn, TTS rendering, and browser buffering remain additional
costs. Merchant polling and CUPS verification were not weakened to reduce them.
Live acceptance should measure verified PRINT_COMPLETED to first audible audio
after the normal deployment loads this change.

## Self-evaluation

| Axis | Score | Evidence / improvement |
| --- | --- | --- |
| Accuracy | 4/5 | Real paired API measurements; three samples cannot establish production percentiles. |
| Completeness | 4/5 | Scoped model path and regression coverage delivered; deployed audible latency is unmeasured. |
| Clarity | 4/5 | First text and audible latency distinguished; the active kiosk still needs normal deployment. |
| Actionability | 4/5 | Uses existing configuration, runtime and event handling; no new service or dependency. |
| Conciseness | 4/5 | Three core files changed for scoped binding/options; extra layers were avoided. |

Overall: **4.0/5**. Highest-impact improvement is measuring first audible
audio on the actual kiosk after deployment. Self-check: this report supports
the measured reduction without promising a sub-second physical-print-to-speech
result that was not tested.
