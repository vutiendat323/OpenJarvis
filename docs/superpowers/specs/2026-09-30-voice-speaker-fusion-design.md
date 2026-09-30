# Voice Speaker Fusion Design

**Date:** 2026-09-30  
**Status:** Design approved, implementation not started  
**Repositories:** `vutiendat323/OpenJarvis` (`feat/live-voice-mode`) and `vutiendat323/vision` (`feat/mouth-activity`)

## 1. Goal

Build a low-latency **Target Speaker Authority** layer that binds microphone speaker identities to visual identities, selects one kiosk customer as the target, and ensures that only that target can:

- enter STT and create transcript text,
- open a user turn,
- barge in / interrupt bot TTS,
- provide TSE enrollment and target-only overlap audio.

Required behavior after lock:

```text
speaker_0 <-> person A
speaker_1 <-> person B
...

selected target = person A
speaker_0 -> STT -> LLM -> TTS + barge-in authority
speaker_1/music/TV/video/bot echo -> no STT authority, no barge-in
```

The initial implementation must guarantee robust **target binding**. Full mapping of every visible person to every diarizer slot is desirable but is not a prerequisite if Vision only exposes ASD for one selected track. Multi-face ASD is a future extension for complete all-speaker/all-face association.

## 2. Design Principles

1. **Precision over recall.** False target acceptance and false barge-in are worse than temporarily masking a real customer utterance.
2. **Realtime first.** No embedding or GPU identity inference may block the live diarizer/VAD path.
3. **Fail closed after lock.** If authority is unresolved before the STT release deadline, mask the frame.
4. **Identity is multimodal.** A target identity is not a ByteTrack ID or a Sortformer slot alone.
5. **Slot IDs and track IDs are ephemeral.** Voice/face identity must survive Sortformer slot permutation and ByteTrack reacquisition.
6. **VAD is not authority.** VAD only says that speech exists; it must not independently open a locked turn or interrupt the bot.
7. **Waveform enrollment and voice identity are separate.** Speaker embeddings identify a voice; TSE still requires clean target PCM enrollment.
8. **No unbounded queues.** Slow model results are stale-dropped rather than allowed to accumulate latency.

## 3. Current Baseline Kept Intact

The design keeps the existing model stack and processor ordering unless explicitly stated:

- WebRTC input
- optional RNNoise enhancement
- NeMo Streaming Sortformer diarization
- overlap detection
- existing Vision face buffer / ASD bridge
- speaker-aware turn detection
- delayed/masked Gemini STT feed
- REAL-TSE / WeSep BSRNN target extraction
- Gemini STT -> OpenJarvis Agent -> VieNeu TTS

The new identity layer sits around the existing speaker gate rather than replacing Sortformer, Light-ASD, TSE, RNNoise, Silero VAD, Gemini STT, or VieNeu TTS.

`identity = "none"` remains the default and must preserve current behavior exactly. The kiosk preset enables `identity = "fusion"`.

---

# Part 1 — Architecture and Data Flow

## 4. Identity Model

The target identity has three related but distinct layers:

```text
PersonIdentity
  face_embedding
  voice_embedding / voiceprint
  current_track_id

AudioIdentity
  Sortformer speaker slot(s)

Binding
  Sortformer slot <-> PersonIdentity
```

Why:

- If ByteTrack changes `track_17 -> track_31`, face ReID reconnects the visual identity; voice evidence can confirm it once speech resumes.
- If Sortformer changes or splits slots, voiceprint and ASD reconnect the correct slot to the target.
- If the customer turns away from camera, the voiceprint can still authorize the target voice.

The implementation must not claim that a voiceprint alone can recover a new visual track before that track speaks. Face ReID is the visual continuity mechanism.

## 5. Components

### OpenJarvis

#### `server/voice/speaker_identity.py` — new, pure logic

No model dependency beyond NumPy.

Responsibilities:

- `TargetLock` state machine
- `SlotTrackBinder` with decaying evidence
- `IdentityTable`
- target slot confidence / TTL
- voiceprint centroid math and cosine similarity
- customer-session reset and `lock_epoch`

#### `server/voice/speaker_embedding.py` — new

Responsibilities:

- `SpeakerEmbedder` protocol
- NeMo TitaNet embedder implementation
- bounded `EmbeddingWorker`
- process-global model loading / warmup pattern matching existing diarizer resources

#### `server/voice/speaker.py`

Add `identity = "none" | "fusion"` and identity-aware gate logic.

The fast path must only read cached identity state; it must not run embeddings.

#### `server/voice/speaker_audio.py`

Responsibilities added:

- collect clean per-slot audio segments
- enqueue embedding jobs asynchronously
- update STT masking from cached verdicts
- keep clean target PCM separately for TSE enrollment
- never block Sortformer on embedding inference

#### `server/voice/speaker_vision.py`

Responsibilities added:

- send `asd_track(track_id | null)` to Vision
- re-send pinned track after reconnect while locked
- accept immediate ASD result events
- ignore stale stream / generation results

### Vision

Existing WS / tracking / ASD modules gain protocol behavior for:

- setting / clearing the pinned ASD track
- publishing ASD results immediately when available
- preserving stream identity and rejecting stale requests/results

The first implementation may keep one pinned ASD track after target lock. Full multi-face ASD is not required for the first target-authority implementation.

## 6. Data Flow

For each Sortformer update (~80 ms rows, current diarizer chunking unchanged):

```text
mic
  +-> VisionAudioBridge -> Vision ASD for selected/pinned track
  |
  +-> Sortformer -> speaker probability rows
                     |
                     +-> FAST PATH
                     |     IdentityTable lookup
                     |     target ASD/MAR cached evidence
                     |     TargetLock / SlotTrackBinder cached state
                     |     frame verdict + STT mask decision
                     |
                     +-> SLOW PATH (async, bounded)
                           clean single-slot segment >= embed_segment_secs
                           -> EmbeddingWorker
                           -> speaker embedding
                           -> IdentityTable / binder / voiceprint update
```

No embedding inference is awaited inside `_diarize`.

Completion flow:

```text
_diarize -> enqueue embedding job -> continue immediately
                                    |
                              executor thread
                                    |
                              completion callback
                                    |
                              event loop update
```

All IdentityTable, binder, and voiceprint mutation happens on the pipeline event loop.

## 7. Session Lifetime

When kiosk FSM leaves active state, clear all customer identity state:

```text
clear:
  target lock
  customer voiceprint
  face identity / current customer track
  slot<->track binder
  customer IdentityTable rows
  TSE target enrollment

keep process-wide:
  model caches
  bot voiceprint cache keyed by voice identity/config
```

Increment `lock_epoch` on reset. Async results from prior epochs are discarded.

---

# Part 2 — TargetLock, Slot Labels, Verdict, and Barge-in

## 8. TargetLock State Machine

```text
FSM != active -> NONE
FSM = active  -> PRE_LOCK
PRE_LOCK --sufficient ASD evidence--> LOCKED
LOCKED --FSM leaves active--> NONE + reset
```

### NONE

Fusion authority is inactive. Gate behavior is the current implementation.

### PRE_LOCK

Use current nearest-face gate behavior as the bootstrap:

- nearest eligible face within configured range
- ASD as strong evidence
- MAR as fallback evidence only
- Sortformer slot evidence
- bot echo evidence

`SlotTrackBinder` accumulates evidence during PRE_LOCK.

Only ASD can establish the target lock. MAR must not lock a target because previous live trials showed that visual mouth-motion evidence can accept video / non-target speech.

### LOCK condition

Lock a pair `(track T, slot S)` only when:

- ASD source confirms the same pair,
- only slot `S` is speaking,
- `S` is not classified BOT,
- the required evidence is temporally clustered, not merely scattered.

Starting configuration:

```text
lock_asd_frames = 6
```

The implementation should require these frames within a short contiguous cluster (target: <= 1.0 s, with at least a short consecutive run), even though the rolling evidence window may remain 3 s. Six sparse false positives across 3 s must not lock a customer.

If ASD is unavailable/disabled, fusion never transitions to LOCKED. PRE_LOCK keeps the existing gate behavior and logs the reason.

### On lock

Set:

```text
target_track = T
target_slots = {S with confidence + last_verified_at}
```

Then:

- send `asd_track(T)` to Vision,
- begin target voiceprint enrollment from clean target-confirmed audio,
- separately retain clean target PCM for TSE enrollment.

Voiceprint is considered ready only after at least `voiceprint_min_secs` of unique clean target speech.

## 9. Target Slot Membership

`target_slots` is not a permanent raw set. Each member stores at least:

```text
slot
target_confidence
last_verified_at
```

A slot loses TARGET status when its confidence/TTL expires without fresh ASD or voiceprint confirmation.

A new slot `S'` may join the target identity when either:

- its speaker embedding matches the ready target voiceprint at `voice_match`, or
- its speech is temporally aligned with strong target-track ASD for sufficient frames.

This handles Sortformer slot split / permutation.

## 10. Track Rebinding

If `target_track` disappears for more than the configured grace period (starting point 0.5 s), a new visual track `T'` may become the current target track only when:

1. Vision evidence says `T'` is actively speaking, and
2. the simultaneously active audio slot matches the ready target voiceprint.

Face ReID should be used where available to reconnect visual identity even before speech, but voice + ASD is the required cross-modal confirmation when assigning audio authority to the reacquired track.

A closer bystander must never steal the target after LOCKED.

## 11. Slot Classification

`IdentityTable` caches each current Sortformer slot as one of:

| Label | Rule |
|---|---|
| `TARGET` | Valid member of `target_slots` with non-expired evidence |
| `BOT` | bot embedding match at `bot_match`, or existing echo heuristic indicates bot echo |
| `OTHER` | embedding rejects target at `voice_reject`, or binder confidently associates slot to another visual identity |
| `UNKNOWN` | insufficient evidence |

Bot voiceprint is secondary evidence only. It does not replace true AEC or the existing echo heuristic because loudspeaker-room-microphone propagation alters the acoustic signal.

## 12. LOCKED Verdict Table

Per ~80 ms speaker row:

| Situation | Verdict / action |
|---|---|
| No active slot | no speech verdict; STT silence |
| BOT only | `REJECT` |
| TARGET only + target visible + ASD >= 0.7 or MAR >= 0.5 | `ACCEPT` |
| TARGET only + target visible + mouth evidence weak + cached voice match valid | `ACCEPT` |
| TARGET only + target visible + cached voice rejects target | `REJECT` |
| TARGET only + target not visible + ready cached voice match | `ACCEPT` |
| TARGET only + target not visible + voiceprint not ready | `UNCERTAIN`, mask |
| OTHER only | `REJECT` |
| overlap without TARGET | `REJECT` |
| UNKNOWN only + target ASD >= 0.7 | `ACCEPT` and binder learns candidate slot |
| UNKNOWN only otherwise | `UNCERTAIN`, mask until identity resolves |
| TARGET + another slot overlap | `UNCERTAIN`, mark `target_overlap`; use TSE target-only output if successful; otherwise mask; never barge in |

Important: mixed overlap audio must never be sent directly to STT as a fallback. If TSE is unavailable or fails, mask the overlap segment.

## 13. Turn Opening and Barge-in

After LOCKED:

- VAD alone does not open a turn.
- Bot idle: at least one `ACCEPT` frame may open the user turn.
- Bot speaking / reply pending: require `bargein_accept_frames` consecutive accepted target frames (starting value 3).
- `OTHER`, `BOT`, and `UNKNOWN` cannot barge in.
- overlap never barge-ins, even if the target is present.

Turn close and LLM authorization otherwise remain as current behavior.

---

# Part 3 — Vision Association Contract

## 14. Visual Identity and ASD

Vision remains responsible for:

- face detection and tracking,
- current track ID,
- face ReID if added/available,
- mouth activity,
- Light-ASD speaking probability,
- timestamped ASD evidence.

OpenJarvis owns target authority and cross-modal speaker binding.

## 15. `asd_track` Control

OpenJarvis sends the selected target track to Vision after LOCKED:

```json
{"cmd":"asd_track","track_id":17}
```

Clear on session reset:

```json
{"cmd":"asd_track","track_id":null}
```

On Vision reconnect while LOCKED, OpenJarvis re-sends the current pinned target track.

Protocol changes must preserve backward compatibility with the existing v1 behavior where possible. If a protocol version bump is used, both repositories must share matching fixtures and stale stream IDs must be ignored.

## 16. Multi-face Association Scope

Pinned target ASD is sufficient for the first implementation of target authority.

It does **not** prove complete global mapping such as:

```text
speaker_0 <-> person A
speaker_1 <-> person B
speaker_2 <-> person C
```

for all simultaneously visible people.

Complete all-person mapping requires multi-face ASD or another equivalent active-speaker association source. This is explicitly outside the minimum acceptance scope unless added during implementation planning.

---

# Part 4 — Speaker Embeddings and Resource Limits

## 17. Audio Segment Collection

Collection occurs from Sortformer rows, but never blocks them.

Per slot, collect only speech that is:

- exactly one active slot,
- not overlap,
- bot not currently speaking,
- outside the existing bot tail window (~0.3 s).

Each slot keeps a bounded buffer (max ~3 s).

A candidate embedding segment starts at `embed_segment_secs` (1.0 s).

Do not concatenate arbitrary disjoint speech into one fake continuous segment. Reset/cut the segment when there is a material gap, overlap, slot change, bot speech, or bot-tail contamination.

A segment is `target_confirmed` only when at least 80% of its constituent rows are target `ACCEPT` with ASD source tied to the locked target track.

- target-confirmed segments may enroll/update customer voiceprint,
- other clean segments may only be compared against known identities.

## 18. EmbeddingWorker

Use an executor separate from the diarizer executor.

Bounded scheduling:

```text
1 embedding inference running
+ latest-only pending work per slot
```

A newly queued segment for the same pending slot replaces the older pending segment.

Scheduling priority:

```text
TARGET / candidate-target
> UNKNOWN
> OTHER / BOT
```

Within the same priority, prefer the oldest pending slot so one continuously talking source cannot monopolize the embedder.

Every job carries:

```text
lock_epoch
slot
segment_end
```

A result is applied only when:

- `lock_epoch` still matches current session state, and
- `segment_end` is newer than the last applied result for that slot.

Do not attempt to cancel/kill an in-progress GPU inference. Let it complete and stale-drop its result if necessary.

## 19. Speaker Embedding Model

Initial model choice: NeMo TitaNet small-class speaker embedding model, 16 kHz input.

The exact model identifier and output dimensionality must be verified against the installed NeMo version during implementation planning. The design must not hard-code an unverified model name merely from memory.

Load once per process using the same warm-resource pattern as the current diarizer:

- model load attempted once,
- failure remembered,
- one warmup inference,
- runtime latency and VRAM measured rather than assumed.

## 20. Customer Voiceprint

For each accepted enrollment embedding:

```text
L2 normalize embedding
reject clear outlier if applicable
retain <= 8 recent valid enrollment embeddings
mean embeddings
L2 normalize centroid again
```

Voiceprint readiness is based on accumulated **unique clean speech duration**, starting at:

```text
voiceprint_min_secs = 2.0
```

`voice_sim[slot]` is cosine similarity between the latest cached slot embedding and the normalized target voiceprint centroid.

## 21. TSE Enrollment

Keep separate data:

```text
target_voice_embedding   # identity / matching
target_enrollment_pcm    # waveform enrollment for TSE
```

TSE must never be described as receiving an embedding vector as enrollment unless the model is explicitly changed to support that interface.

Clean target-confirmed PCM is the enrollment source for the current REAL-TSE path.

## 22. Bot Voiceprint

Add a callback/hook from VieNeu TTS output to collect a clean direct TTS sample (e.g. first ~3 s of suitable PCM), resample 48 kHz -> 16 kHz, and compute a bot speaker embedding.

Cache per process keyed by effective bot voice identity/configuration.

Bot embedding is supporting evidence only. Keep existing echo heuristics and future/true AEC as primary defenses against self-interruption.

## 23. Bounded Resources

| Resource | Limit / policy |
|---|---|
| diarizer queue | existing bound |
| Vision audio bridge queue | existing bound (currently 8 packets) |
| audio buffer per slot | <= 3 s |
| embedding inference | 1 running |
| embedding pending | latest-only <= 1 per slot |
| customer voiceprint | <= 8 enrollment embeddings |
| ASD evidence deque | ~3 s |
| binder | active slots x currently relevant tracks; evidence decays, starting half-life 5 s |
| stale tracks | remove after bounded inactivity, starting value 5 s |
| IdentityTable | <= Sortformer slot count (currently 4) |

The fast path performs only bounded table lookups and simple numeric operations.

## 24. Fail-closed STT

Only after LOCKED:

If the delayed STT release deadline arrives and the frame/chunk has no authoritative verdict, release silence instead of raw audio.

Starting delay remains the current kiosk value (0.7 s). Do not dynamically increase it in production to hide slow inference.

Log at least:

```text
stt_masked_no_verdict
verdict_latency_ms
diarizer_late
embed_late
```

Before LOCKED, preserve current behavior.

## 25. Degradation Behavior

| Failure | Behavior |
|---|---|
| embedder load/runtime failure | disable embedding for process/session as appropriate; continue ASD/binder fusion; rules requiring voice match fail closed; log once plus counters |
| ASD unavailable before lock | never LOCK; stay PRE_LOCK/current gate behavior |
| Vision disconnect while LOCKED | stale face evidence expires; use ready cached voiceprint for target authorization; no stale ASD reuse |
| Vision reconnect while LOCKED | re-send pinned `asd_track(target_track)` |
| diarizer unavailable after lock | mask STT authority and disable barge-in; never fall back to VAD-only authority |
| kiosk FSM disabled | never enter PRE_LOCK; preserve existing non-fusion behavior |
| FSM reset | increment `lock_epoch`, clear customer identity/TSE state, stale-drop old async results, send `asd_track(null)` |
| TSE fail/unavailable during overlap | mask overlap audio; no barge-in |

---

# Part 5 — Test Plan

## 26. TDD and Regression

Implementation follows TDD:

1. write failing test,
2. verify failure,
3. implement minimal behavior,
4. verify pass,
5. run regression suite.

`identity = "none"` must preserve every current speaker-gate behavior. Existing tests are regression tests for this requirement.

Fusion config validation requires:

```text
vision_asd = true
vision_faces = true
diarizer = "sortformer"
```

## 27. Backend Pure Logic Tests

New `test_speaker_identity.py` covers:

- NONE -> PRE_LOCK only while kiosk FSM is active,
- lock requires clustered ASD evidence for one `(track, slot)` pair,
- MAR alone never locks,
- bot slot cannot lock,
- leaving active resets and increments epoch,
- target slot rebind by voice match,
- target slot rebind by ASD temporal evidence,
- slot TTL/confidence expiration,
- target track reacquisition requires target voice + ASD when voiceprint ready,
- closer bystander does not steal locked target,
- TARGET/BOT/OTHER/UNKNOWN classification,
- every row of the LOCKED verdict table as parameterized tests,
- old epoch results dropped,
- older `segment_end` result dropped.

## 28. Speaker Audio Tests

Extend `test_speaker_audio.py` with fake diarizer and fake embedder:

- collect only clean single-slot rows,
- segment cut/reset on gap, overlap, bot speech, and bot tail,
- only target-confirmed segments enroll customer voiceprint,
- non-target clean segments are match-only,
- embedding job enqueue does not block `_diarize`,
- at most one inference running,
- latest-only pending work per slot,
- priority TARGET/candidate > UNKNOWN > OTHER/BOT,
- stale epoch result discarded,
- stale segment result discarded,
- after LOCKED, no verdict by STT deadline -> silence,
- before LOCKED behavior unchanged,
- TSE enrollment comes from clean target PCM, not from the embedding vector,
- TSE fail during overlap -> mask.

## 29. Turn Detection Tests

After LOCKED:

- VAD alone cannot open a turn,
- accepted target frame may open turn while bot idle,
- bot speaking/reply-pending requires 3 consecutive accepted target frames,
- OTHER/BOT/UNKNOWN never barge in,
- overlap never barge-ins.

## 30. Pipeline Scenario Tests

Synthetic scenarios:

- TV/video speech while bot speaks -> no interruption, STT frames silent,
- customer turns away after voiceprint ready -> target speech accepted,
- bystander becomes physically closer -> target unchanged,
- Sortformer target slot changes -> target rebinds,
- ByteTrack target ID changes -> face/voice continuity recovers target,
- Vision reconnect -> pinned ASD track resent,
- diarizer late/drop after lock -> fail-closed mask,
- bot echo -> no self-interrupt.

## 31. Vision Protocol Tests

Extend `test_speaker_vision.py` / Vision tests for:

- protocol handshake/version behavior,
- `asd_track(track)` set and clear,
- pinned track resent after reconnect,
- immediate ASD result forwarded to `FaceTrackBuffer`,
- stale stream result ignored,
- old generation result ignored.

If a protocol v2 is introduced, compatibility/fallback behavior must be explicit in tests rather than silently assumed.

## 32. Optional GPU Tests

Auto-skip when CUDA/NeMo/model artifact is unavailable.

Measure / assert shape-contract only, not production performance thresholds in CI:

- load speaker embedder,
- one 16 kHz segment returns a valid normalized speaker embedding,
- record latency for a 1 s segment,
- verify process-global reuse.

Exact embedding dimensionality is asserted only after the chosen installed model is verified.

---

# Part 6 — Structured Logs and Live Trial Acceptance

## 33. Structured Logging

INFO logs use stable `key=value` fields.

### Lock lifecycle

```text
speaker_lock event=pre_lock|lock|slot_rebind|track_rebind|reset track=... slots=... asd_frames=... after_s=... reason=...
```

### Embedding result

```text
speaker_identity slot=... label=TARGET|BOT|OTHER|UNKNOWN voice_sim=... bot_sim=... seg_s=... embed_ms=... pending=... stale=false
```

### Turn summary

Extend current turn-close logging with:

```text
verdict=...
evidence=asd|mar|voice|mixed
masked_s=...
overlap_s=...
tse=success|fail|unused
target_track=...
target_slots=...
```

### Barge-in

```text
bargein allowed=true accept_run=3
bargein allowed=false reason=bot|other|unknown|overlap|not_target|insufficient_run
```

### Session summary

At voice stop, report at least:

```text
lock_after_s
slot_rebind_count
track_rebind_count
asd_effective_use
asd_late
asd_age_ms_p50
asd_age_ms_p95
embed_ms_p50
embed_ms_p95
verdict_latency_ms_p50
verdict_latency_ms_p95
stt_masked_no_verdict
echo_rejects
false_bargein_count
bargein_blocked_count
```

Preserve the existing transcript log because it represents what Gemini actually received/heard.

## 34. Primary Acceptance Metrics

Compare baseline (`identity="none"`) and fusion (`identity="fusion"`) with focus on:

```text
false_target_accept_rate
false_bargein_count
lock_latency_ms
target_stt_miss_rate
```

Secondary operational metrics include ASD effective use/late, embedding latency, verdict latency, mask rate, TSE success/failure, and rebind counts.

The primary optimization order is:

```text
1. false target acceptance / false barge-in
2. target transcript contamination
3. lock latency / realtime responsiveness
4. target recall
```

## 35. Live Trial Matrix

Each scenario is run with baseline first, then fusion.

| ID | Scenario | Pass condition |
|---|---|---|
| S1 | customer alone, normal ordering | locks on first suitable utterance; normal target turns reach transcript and bot responds |
| S2 | bystander speaks beside customer, including standing closer | zero bystander transcript contamination; zero false barge-in; locked target unchanged |
| S3a | TV/video speech while customer is silent | zero transcript contamination; zero barge-in |
| S3b | TV/video speech overlaps customer | TSE produces target-only STT audio; if TSE cannot isolate target, overlap is masked; no overlap barge-in |
| S4 | customer looks down/turns away after voiceprint ready | target voice remains accepted by voice identity; no target switch |
| S5 | background music | music does not open turns, does not create transcript, zero false barge-in |
| S6 | loud bot speaker / strong room echo | zero bot self-interrupt; bot output absent from STT transcript; logs show which echo defense fired |
| S7 | customer A leaves, customer B arrives | FSM reset clears A; B receives a new lock and new customer voiceprint/TSE enrollment |

## 36. Accepted Trade-offs

The first implementation intentionally accepts:

- During the initial voiceprint warmup, a target who immediately turns away may have speech masked.
- Customer + competing speech overlap cannot barge in.
- If TSE cannot isolate target overlap, audio is masked rather than risking transcript contamination.
- If ASD is unavailable before target lock, Fusion does not guess a locked identity.
- Full mapping for every visible bystander is not required until multi-face ASD / equivalent association is implemented.

---

# Configuration — Starting Values

Starting values for live calibration only; they are not validated production thresholds:

```toml
[voice.speaker]
identity = "fusion"
lock_asd_frames = 6
voiceprint_min_secs = 2.0
voice_match = 0.65
voice_reject = 0.45
bot_match = 0.70
embed_segment_secs = 1.0
```

Existing settings such as `bargein_accept_frames`, ASD thresholds, STT mask delay, TSE, and diarizer latency remain authoritative unless the implementation plan explicitly changes them.

Every new threshold must be logged during live trials so it can be calibrated from observed distributions rather than assumed values.

# Non-goals

This design does not attempt to:

- replace the existing Sortformer diarizer,
- replace Light-ASD,
- replace TSE,
- solve general multi-party meeting diarization,
- identify customers across separate kiosk sessions,
- preserve customer voiceprints after the active kiosk session ends,
- use mouth movement alone as speaker identity,
- allow VAD-only barge-in after target lock,
- dynamically stretch STT delay to hide slow inference.

# Implementation Gate

This document is the approved design baseline only. Product code changes require a separate implementation plan derived from this spec before execution.
