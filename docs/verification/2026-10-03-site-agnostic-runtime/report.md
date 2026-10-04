# Site-agnostic runtime correction — 2026-10-03

## Scope and correction

The previous implementation introduced merchant-specific behavior into shared runtime. That exceeded prompt optimization scope and contradicted the requirement to support different websites through the same agent.

Removed the three newly created source modules:

- `src/openjarvis/kiosk/ordering_tools.py`
- `src/openjarvis/kiosk/voice_response.py`
- `src/openjarvis/kiosk/menu_search.py`

Removed their registrations, merchant-name detection, fixed Vietnamese acknowledgements, output clipping, menu-term correction, table-response generation and table cache. SkillExecutor and SystemBuilder now match HEAD; no new merchant callbacks or automatic merchant composition remain.

## Remaining generic behavior

- A configured prompt takes precedence over the default prompt builder, preserving persona configuration.
- Tool-enabled streaming holds text until the model decision resolves, executes tools before speaking, and discards narration from intermediate tool rounds.
- Successful terminal results are recognized by display capability, `completed_display`, or the site-neutral `complete_turn` metadata contract. `continue_agent` and failures prevent early completion.
- When a terminal tool provides `customer_message`, the agent uses that response once, including an explicitly empty response. It makes no additional model call. Without that metadata, existing model text is retained after successful completion.
- Neither model responses nor site-provided responses are translated, shortened or replaced by merchant templates in the orchestrator.
- Skill input defaults come from skill configuration; caller arguments override them.
- Existing standalone cart completion and explicit compound continuation remain. Checkout/payment recipes and guards are retained.

TREND Coffee names, endpoints and ordering policy remain in the existing site-specific prompt and skill configurations. That prompt is for this merchant only; other sites need their own configured instructions/tools. This correction does not establish automatic support for arbitrary live websites.

The 15-word voice policy remains in the ordering prompt, not a global hardcoded limiter. Consequently it is a model instruction, not a guaranteed runtime enforcement across arbitrary sites.

## Verification

- Related backend tests: **377 passed, 2 skipped, 8 dependency warnings**. Full output: [verification.log](verification.log).
- Generic regression cases cover arbitrary clinic, school and library tool names, different languages, terminal silence, response preservation beyond 15 words, failed/nonterminal tools, continuation, and one model call for successful terminal tools.
- Existing checkout procedure, payment evidence, voice payment acceptance, conversation scope, display, skill security and adapter tests included.
- Focused orchestrator/site tests: **89 passed**.
- Ruff on changed task source/tests/benchmark script: passed. `git diff --check`: passed.

Tests use scripted engines and fixtures; they do not validate live medical or educational websites, model instruction compliance, or production speech quality.

## Latency evidence limitations

The earlier [ordering benchmark](../2026-10-02-ordering-voice-latency/report.md) describes the rejected implementation and is marked superseded. Its one-model-call and fixed speech-length results must not be attributed to this source.

No new live-model latency benchmark was run for this correction. Removing the composed add/checkout wrapper can require multiple model calls for compound operations. Tool-enabled no-tool answers now wait until generation completes before speech; tool-free agents retain immediate streaming. These are explicit tradeoffs of tool-first speech and removing merchant-specific shortcuts.

No changes were committed, pushed or deployed in this correction. Unrelated existing UI, STT, browser and launcher changes were preserved.
