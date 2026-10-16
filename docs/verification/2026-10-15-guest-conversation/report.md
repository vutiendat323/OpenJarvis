# Separate chat conversations for kiosk customers

## Root cause and change

The FSM already ends voice when a customer leaves and grants voice again for the
next customer. `KioskPage.startPolicyVoice` nevertheless reused
`threadIdRef.current`, which survived `endVoice`. Consequently, later customers'
transcripts were appended to the first chat conversation. Backend persistence also
uses `voice:{chat_thread_id}`, so the reused client ID merged saved history.

Add one production line in `frontend/src/pages/KioskPage.tsx`: clear the thread ref
immediately before the FSM policy grants a new voice session. The existing start
callback then allocates a fresh conversation. A manual microphone stop/restart
while the same grant remains active retains that customer's conversation.
Previous conversations remain in the chat store. Each new customer also supplies
a distinct presentation generation. Backend voice IDs and pipeline contexts were
already created independently; no backend change is needed.

## Regression and verification

`frontend/src/pages/KioskPage.test.tsx` renders the actual page through
`prompting -> active -> cleanup -> idle -> approaching -> prompting -> active`.
It checks that voice starts and transcript writes use distinct chat IDs, that
non-active phases create no conversation, and that a rerender does not start a
duplicate conversation. A second regression keeps the same customer's ID across
manual microphone stop/restart. Voice transport/store boundaries use the existing
test doubles; this is not a live camera/WebRTC test.

- Before the fix: the new regression failed because the second voice start used
  `guest-1` instead of `guest-2`.
- The manual restart regression also failed before its fix: allocating a thread
  on every start split the same customer's conversation. Allocation now follows
  the policy grant boundary.
- Full frontend `npm test`: **529 passed** across 50 files, including both regressions.
- TypeScript: `tsc --noEmit --incremental --tsBuildInfoFile /tmp/jarvis-guest-conversation.tsbuildinfo` passed.
- Vite production/PWA build passed to `/tmp/jarvis-guest-conversation-build.3HUyZB`.
  Build warnings concerned Draco/WASM URL resolution, an empty React chunk,
  analytics import splitting, large chunks and stale Browserslist data.
- `git diff --check` passed. Existing generated build metadata and unrelated local
  artifacts were preserved.
- Independent focused review of the final diff and policy/voice/presentation call
  sites: no remaining concrete findings; review did not include live WebRTC.

No stack restart, merchant/model API calls, commit or push. Existing merged chat
history is retained; the fix separates subsequent customer sessions.

## Self-evaluation

| Criterion | Score | Evidence / limit |
| --- | --- | --- |
| Accuracy | 4/5 | Observed failing regression, then full frontend and type checks; no live replay. |
| Completeness | 4/5 | New guest IDs and transcript routing covered; previously merged history is retained. |
| Clarity | 4/5 | Distinguishes client chat identity from backend voice session; transport is mocked. |
| Actionability | 4/5 | One-line production fix and runnable regression; live acceptance remains. |
| Conciseness | 5/5 | Production change is limited to allocating the conversation ID. |

Overall: **4.2/5**. Next improvement: verify two real customer visits after the
frontend is loaded. The user should expect the automated checks above, without a
claim of verified live behavior.

## Publication verification — 2026-10-16

User authorized committing and pushing the conversation, checkout recovery and
STT vocabulary changes to `openmetek/feat/live-voice-mode` (`homeslands/OpenMetek`).
Fresh pre-publication checks: frontend **529 passed**; skills **498 passed,
5 deselected**; intent/display/trusted-hosts plus the preset STT test **197 passed**.
TypeScript no-emit, Ruff check/format and staged diff checks passed. CodeScene MCP
is not configured in this session; no structural score is claimed.

The configured STT vocabulary now contains **19 terms**: Yaourt, Lipton, standalone
Croissant and Pain suisse were removed; the three compound croissant hints retain
only Ham & Cheese, Matcha ribbon and Salted egg charcoal. TOML parsing and an
explicit vocabulary check passed. The unrelated local debounce edit, generated
build metadata and older diagnostic artifacts stay outside the commit. Publication
does not restart the running stack or verify its loaded version.
