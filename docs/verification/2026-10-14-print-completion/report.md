# Verified invoice completion and Agent notification

Implemented locally on `feat/live-voice-mode`; local HEAD remains `5a75ba5c`.
No commit, remote push, service restart, live merchant mutation, or physical
print was performed for this feature.

## Behavior and evidence

- `InvoiceAutoPrinter` retains its merchant payment/invoice identity checks,
  original merchant PDF, held CUPS job, and release of that same job. Submission
  remains distinct from completion; observing status never submits another job.
- `CupsPrinter.job_state` queries the original job through the existing local
  IPP primitive, checking job ID, name, and queue. Completion requires explicit
  successful completion reasons. `queued-in-device`, missing success reasons,
  errors, cancellation, and query failures cannot produce `PRINT_COMPLETED`.
- Worker state and owning conversation/display IDs are journaled in SQLite.
  Existing databases acquire the new columns automatically. Submitted jobs
  resume observation after worker restart; ambiguous submit/release states keep
  the existing manual-review behavior.
- `PRINT_COMPLETED` travels through EventBus and
  `NativeAgentRuntime.record_background_event` into the existing ActionLedger.
  BackgroundReporter delivers it through the existing voice event turn when
  idle. Customer turns retain priority; no new speech callback or scripted
  customer message was introduced.
- Completion delivery retries after subscriber failure. The stable merchant,
  order, and CUPS job event ID deduplicates runtime outcomes. Bus delivery is
  at least once, not a distributed exactly-once speech guarantee. A process
  restart creates a new voice session; old events retain their old owner and
  do not announce to the next customer.
- Printing status updates inline within Order Status. Payment status, invoice
  contents, selected screen, and navigation are preserved. Status is retained
  for known orders across receipt redisplay and menu navigation, is scoped to
  the display session, and is cleared by its existing reset. Completed status
  cannot regress to submitted. Raw ownership IDs are excluded from operator
  job diagnostics and customer display patches.

## Verification

- Initial broader backend regression run: **540 passed, 2 skipped**.
- After lifecycle fixes and ownership filtering, final relevant backend run:
  **499 passed, 1 skipped** across 23 suites covering runtime, streaming/tool
  behavior, checkout, presentation, CUPS, printing, voice priority, background
  reporting, and voice routes.
- Frontend full suite: **510 passed** in 49 files. TypeScript/Vite production
  build passed in an isolated source export; live static assets were untouched.
- Ruff checks on changed Python code and `git diff --check`: passed.
- Independent review reran four printing/integration suites: **124 passed**;
  no remaining important findings after redisplay and delivery retry fixes.

The integrated test in `tests/server/test_print_completion.py` runs real
worker/SQLite, EventBus, presentation, runtime, ActionLedger, BackgroundReporter,
and Pipecat event-turn primitives. Merchant, CUPS, browser, and model are mocked.
It verifies pending payment does not print; paid payment submits one job;
submission does not create a completion notice; verified completion reaches
the owning Agent; an active customer turn defers speech; model-generated
Vietnamese/English replies arrive after idle; a later failing subscriber and
durable retry produce one Agent outcome and one response; the paid invoice
shows completed status without a second print.

## Limits

This verifies the application's integration, not a live merchant transaction,
physical receipt, real model wording, or audible playback. The deployed kiosk
has not been restarted to load the changes. CUPS success is the print server's
verified result; it is not a sensor proving paper was physically collected.
Presentation and ActionLedger remain session-local existing primitives.

## Self-evaluation

| Axis | Score | Evidence and improvement |
| --- | --- | --- |
| Accuracy | 4/5 | Identity checks, failure cases and model event delivery pass; real device output is untested. |
| Completeness | 4/5 | Requested primitives, inline status and voice priority are covered; live hardware acceptance remains. |
| Clarity | 4/5 | Submitted and verified completed states are distinct; device-specific reporting needs operator confirmation. |
| Actionability | 4/5 | Implementation, regression tests and review are present; loading it requires the normal deployment process. |
| Conciseness | 4/5 | No new dependency or speech mechanism; durable worker and regression coverage span several existing modules. |

Overall: **4.0/5**. Highest-impact follow-up is a controlled live acceptance
run using the intended CUPS queue and existing checkout once deployed.
Self-check: this assessment separates tested application behavior from
unverified physical output, matching the requested completion standard.
