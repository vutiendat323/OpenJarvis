# Automatic selection of an unspecified dine-in table

Implemented in the working tree. No commit, runtime restart, live merchant write,
or paid model evaluation. Existing unrelated changes remain.

## Behavior

Confirmed at-table dining with no saved table and an omitted/unclear/any-table
choice uses `skill_trendcoffee-auto-table`. It reads the merchant's current list
once and sets the first row whose status is exactly `available`, preserving
API order rather than sorting table numbers. No fixed table number or ID.

The Agent decides whether dining is confirmed, whether a table is already saved,
and whether the customer gave a clear specific choice. A clear choice retains
the existing selection path; unknown dining or an explicitly unavailable table
requires clarification. No free table, invalid data, wrong source or HTTP failure
stops before cart selection/checkout, preserving the draft.

Checkout requested: auto-table with `finish_turn=false`, then guarded checkout
with the updated draft revision/selected slug and `table_status="available"`.
The canonical CHECKOUT_NOW intent stays unchanged during preparation. Checkout
rechecks that this automatically selected table is still free before order POST.
Explicit confirmed sharing retains the existing default behavior (`any`).

## Scope and latency

- New merchant skill performs GET and local selection in one call, with no LLM
  step inside it. Enabled in the preset and described in the table/checkout skills.
- Prompt removes the earlier ban on assignment and missing-table question rules.
  Agent still reasons about intent and exceptions.
- Core changes are generic: `$first` over a nonempty array, derived default
  inputs exposed as optional checkout arguments, and cart revision metadata
  preserved through prepared display skills. No table/dining/language routing
  was added to core, no new dependency, no new checkout execution path.
- Deterministic configured Agent test runs auto-table then checkout in **two
  inference rounds**, with no separate model call to select/edit the table.
  Checkout's menu/table reads still use WorkflowEngine and existing guards.
  Two table GETs remain (selection and pre-write recheck); their freshness is
  intentional. No live wall-clock/p50/p90 speedup is claimed.

## Verification

- Initial regression: 11 failures for the missing first operator/procedure.
  Revision propagation and a table becoming reserved before checkout also
  failed before their corresponding fixes.
- Real cart, HttpRequestTool, SkillExecutor, WorkflowEngine, bill/QR tools and
  preset visibility/intent normalization are exercised. Only external HTTP
  transport and model output are fixtures; no actual order/payment was made.
- **761 passed, 1 skipped, 5 deselected**, exit 0: skills, relevant display/HTTP/
  evidence tools, system wiring, intent and checkout Agent suites.
- Ruff check/format, merchant TOML parsing and `git diff --check` passed.
- Independent focused reviewer: no concrete findings; 139 auto-table/bundled
  tests passed and final 12 auto-table tests passed.

Limits: scripted model outputs verify wiring/continuation, not spontaneous
understanding of noisy speech. The optional availability constraint is selected
by Agent instructions, as requested; core does not infer merchant policy from
speech or table provenance. A merchant table can change after the final GET;
the merchant order API remains responsible for accepting the order. Updated
backend/config must be loaded before the running kiosk uses these changes.

Self-evaluation: accuracy 4 (no live model latency evidence), completeness 4
(requested fallback and write guard covered; runtime loading pending), clarity 4
(policy vs generic core distinguished), actionability 4 (reviewable changes and
checks; live follow-up remains), conciseness 4 (reused cart/checkout, small core
changes). Overall 4.0. Next useful verification: actual noisy table-number
utterances after loading the updated stack. Assessment matches the delivered
scope and avoids claiming live routing/speedup.
