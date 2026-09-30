# Voice Speaker Embedding + Audio-Visual Speaker Fusion

Status: design approved in conversation 2026-09-30; awaiting written-spec review.
Scope: OpenJarvis backend `src/openjarvis/server/voice/` and the sibling `vision/`
service (separate repository). Kiosk ordering preset only.

## 1. Goal

Lock one target customer per kiosk session and let only that person's speech
reach STT → LLM → TTS or interrupt the bot.

1. Map each audio speaker to a visual identity (Sortformer slot ↔ face track).
2. Lock one target customer for the kiosk session; nobody else can take over.
3. Only the target's audio enters STT and creates transcript words.
4. Other speakers, music, TV/video speech and bot echo never open turns or
   interrupt the bot.
5. Only confirmed target speech may barge in.
6. Realtime first: cached identity, a lookup-only fast path, bounded async
   work, stale results dropped.

Tools: Sortformer diarization (existing), Light-ASD (existing), a new voice
speaker embedding, voice/face fusion, and TSE (existing) only during overlap.

### Non-goals

- Body presence / YOLO (`body_m`) — out of scope; Prod runs with YOLO off.
- Labeled A/V replay datasets. Acceptance is unit tests + live trial (§9).
- Changing the LLM authority rule for UNCERTAIN turns (§5.5).
- Handing the lock to a second customer inside one kiosk session.
- Running vision and backend on different hosts. Both use `time.time()` on
  one host as the shared clock.

## 2. Current state (what this changes)

Read on 2026-09-30 from `feat/live-voice-mode` (last commit `ddae21b7`).

- `AudioOnlyGate.frame` (`speaker.py`) decides per 80 ms row. With fresh
  Vision faces it ignores slot identity: the verdict is "some voice is active
  and the nearest face within 1.5 m moves its mouth (ASD ≥ 0.7, else MAR ≥
  0.5)". Slots are used only for echo detection and an audio-only fallback
  target (`FLOOR_FRAMES`, marked `ponytail … PR-5 replaces it`).
- The anchor is re-chosen every frame as the nearest face; a closer person
  takes over immediately.
- Vision runs Light-ASD on its own nearest-face choice (`track_faces.py`), which
  can differ from the backend's anchor. Results reach the backend only through
  the 10 Hz `faces` event, and the ASD worker sleeps 80 ms between windows.
- STT mask (`SpeakerAudioProcessor._silenced`) silences REJECT and no-voice
  rows, but lets rows with no verdict through (a dropped diarizer chunk leaks
  unmasked audio) and never masks UNCERTAIN (overlap).
- `TargetSpeakerTurnStartStrategy` opens a turn on any VAD onset while the bot
  is silent and no reply is pending; barge-in needs 3 consecutive ACCEPT rows.

## 3. Decisions

| Question | Decision |
|---|---|
| Architecture | Option A: pure identity layer in the backend + two vision protocol additions. Rejected: fusion inside vision (adds a network hop to the fast path), embedding-only clustering replacing Sortformer (≥1 s latency, no overlap handling). |
| Lock lifetime | Kiosk FSM: lock during `active`, release when the FSM leaves `active`. A closer person never takes the lock. |
| Voice-only evidence | A strict voiceprint match on a target slot is a full ACCEPT (STT, turn open, barge-in) even with no face evidence. |
| Acceptance | Unit tests + live trial on Prod with baseline comparison. |
| Embedding model | NeMo TitaNet-small (192-d, 16 kHz). NeMo is already installed through the `voice-speaker` extra. |
| Pre-lock behaviour | Unchanged current gate, plus bot-voiceprint echo detection. |
| Post-lock behaviour | Fail-closed: UNCERTAIN and verdict-less rows are masked from STT; turns open only on ACCEPT. |

## 4. Architecture

Identity principle: the target is a voiceprint **and** a face track. Losing
one re-acquires it from the other (ByteTrack ID change → voiceprint; slot
permutation → voiceprint or ASD; face turned away → voiceprint).

### 4.1 Units

| Unit | Responsibility | Depends on |
|---|---|---|
| `server/voice/speaker_identity.py` (new, pure) | `TargetLock` state machine, `SlotTrackBinder`, `IdentityTable`, voiceprint maths (normalized mean, cosine) | numpy |
| `server/voice/speaker_embedding.py` (new) | `SpeakerEmbedder` protocol, `TitaNetEmbedder`, `EmbeddingWorker` | NeMo, torch (lazy import) |
| `server/voice/speaker.py` | `SpeakerSettings` gains fusion keys; gate gains `identity="fusion"` mode reading the `IdentityTable`; `FaceTrackBuffer.add_asd` + separate ASD deque | `speaker_identity` |
| `server/voice/speaker_audio.py` | Segment collector, embedding submission, fail-closed STT mask after lock, TSE enrollment from voiceprint audio | above |
| `server/voice/speaker_vision.py` | v2 handshake with v1 fallback, `asd_track` pin (re-sent on reconnect), `asd` event intake | vision WS |
| `server/voice/turn_detection.py` | Post-lock turn start rule | `SpeakerTracker` |
| `server/voice/tts.py` / routes wiring | `on_bot_audio` callback for the bot voiceprint; process-wide embedder loading like `_diarizer()` | |
| `vision/presentation/ws_server.py` | `asd_track` command, `asd` push loop, protocol v2 | |
| `vision/domain/active_speaker.py` | `AVBuffer.pin_track`, condition signalling, publish callback | |
| `vision/application/track_faces.py`, `active_speaker.py` | Pinned-track crop selection; condition wait instead of `sleep(0.08)` | |

`TargetLock` reads the kiosk FSM state from `openjarvis.kiosk.runtime` (same
process). Unchanged: Sortformer, Light-ASD model, TSE model, RNNoise, the
Pipecat pipeline order.

### 4.2 Data flow per 240 ms diarizer chunk

```
mic ─► VisionAudioBridge ─► vision ASD (pinned track) ─push on publish─► FaceTrackBuffer.asd
  └─► Sortformer ─► rows[slot probs]
        ├─ FAST PATH (every 80 ms row, table lookups only)
        │    IdentityTable + target_track ASD/MAR ─► verdict ─► turn/barge-in, STT mask
        │    SlotTrackBinder.update(slot probs × per-track ASD/MAR)
        └─ SLOW PATH (async, bounded, stale-dropping)
             ≥1 s solo, non-bot segment per slot ─► TitaNet ─► embedding
             ─► IdentityTable[slot].voice_sim / bot_sim
             ─► enroll into voiceprint iff segment is target_confirmed
```

All `IdentityTable`, binder and voiceprint mutations run on the pipeline event
loop (executor results are awaited there), so they need no locks.

## 5. Target lock and verdicts

### 5.1 `TargetLock`

```
FSM ≠ active ──► NONE      (current gate behaviour)
FSM = active ──► PRE_LOCK ──(ASD evidence)──► LOCKED ──(FSM leaves active)──► NONE, epoch += 1
```

- **PRE_LOCK**: verdicts come from the current gate (nearest face ≤ 1.5 m,
  ASD/MAR), except that slots matching the bot voiceprint are echo. The binder
  accumulates evidence.
- **Lock condition**: one (track T, slot S) pair collects ≥ `lock_asd_frames`
  rows within 3 s where the row verdict is ACCEPT with `source=asd` for T,
  S is the only active slot, and S is not BOT. MAR alone never locks (live
  2026-09-29: MAR-only ACCEPTs during a playing video). If Vision ASD is
  `disabled`/`unavailable`, the lock never forms and the log says why.
- **On lock**: `target_track = T`, `target_slots = {S}`, send
  `asd_track(T)`, enroll the locking rows' audio. The voiceprint is **ready**
  once ≥ `voiceprint_min_secs` of audio is enrolled.
- **While LOCKED**:
  - Add slot S′ to `target_slots` when its embedding matches the voiceprint
    (cos ≥ `voice_match`), or when it co-occurs with `target_track` ASD ≥
    `asd_accept_prob` for `lock_asd_frames` rows.
  - When `target_track` has been absent from `faces` for more than 0.5 s,
    send `asd_track(null)` so Vision's ASD falls back to the nearest face.
    Replace `target_track` with T′ once T′ has ASD-confirmed speech
    (`lock_asd_frames` rows) co-occurring with a TARGET slot while the
    voiceprint is ready; then send `asd_track(T′)`. If the old track returns
    first, re-pin it.
  - Release only when the FSM leaves `active`: reset table, binder, customer
    voiceprint, send `asd_track(null)`, `lock_epoch += 1`. The bot voiceprint
    survives.

### 5.2 Slot labels (`IdentityTable`)

| Label | Rule |
|---|---|
| `TARGET` | Slot in `target_slots` |
| `BOT` | Embedding cos to bot voiceprint ≥ `bot_match`, or the existing echo rule (`ECHO_MIN_FRAMES`, `ECHO_BOT_FRACTION`) |
| `OTHER` | Embedding cos to voiceprint < `voice_reject`, or the binder maps the slot to a track other than `target_track` |
| `UNKNOWN` | No evidence yet |

`voice_sim[slot]` is the cosine of the slot's newest embedding against the
voiceprint.

Binder mapping: on each row, for every active slot s and every track x in the
nearest `faces` event, `evidence[s][x] += p(s) × a(x)`, where `a(x)` is the
track's ASD probability when one covers the row, else its MAR (Vision reads MAR
for the three largest faces), else 0. Evidence decays with a 5 s half-life. A
slot maps to track x when `evidence[s][x]` ≥ 1.0 (≈ 1 s of full evidence) and
≥ 2 × its second-best track. These two numbers are module constants, not
config.

### 5.3 Verdict table while LOCKED (per 80 ms row)

"Face visible" means `target_track` appears in a Vision `faces` event within
0.3 s of the row, the buffer is fresh (≤ 1 s), and the row has an ASD
probability or a MAR value for it; a track present without either counts as
not visible. "Only TARGET" and the other "only" rows consider non-BOT active
slots: a BOT slot active alongside never changes the row's verdict.

| Situation | Verdict |
|---|---|
| No active slot | `None` (masked from STT) |
| Only BOT slots active | REJECT |
| Only TARGET, face visible, ASD ≥ `asd_accept_prob` or MAR ≥ `mouth_active` | ACCEPT |
| Only TARGET, face visible, mouth still, voiceprint ready and `voice_sim ≥ voice_match` | ACCEPT (MAR miss: hand or cup over the mouth) |
| Only TARGET, face visible, mouth still, otherwise | REJECT |
| Only TARGET, face not visible, voiceprint ready and `voice_sim ≥ voice_match` | ACCEPT |
| Only TARGET, face not visible, otherwise | UNCERTAIN (masked) |
| Only OTHER, or overlap without a TARGET slot | REJECT |
| Only UNKNOWN, target ASD ≥ `asd_accept_prob` | ACCEPT; binder learns the slot |
| Only UNKNOWN, otherwise | UNCERTAIN (masked until its embedding arrives) |
| TARGET overlapping another voice | UNCERTAIN with `target_overlap`: STT gets TSE output enrolled from voiceprint audio; without TSE, the mix passes only if the target is visibly speaking (ASD or MAR), else masked. Never counts toward barge-in |

Overlap uses the existing `OverlapDetector` hysteresis over non-BOT active
slots.

### 5.4 Turns and barge-in while LOCKED

- A VAD onset alone no longer opens a turn.
- Bot silent and no reply pending: ≥ 1 ACCEPT row opens the turn.
- Bot speaking or reply pending: `bargein_accept_frames` consecutive ACCEPT
  rows (default 3) open the turn and interrupt.
- Turn close rules are unchanged.

### 5.5 LLM authority

Unchanged (`llm.py`): REJECT turns get the unconfirmed-speaker note and
`uncertain_allowed_tools`; ACCEPT and UNCERTAIN turns act normally. After lock,
UNCERTAIN audio is masked, so UNCERTAIN turns become rare.

### 5.6 Accepted trade-offs

- The customer cannot barge in while speaking over a TV/video.
- Until the voiceprint is ready (~2 s of confirmed speech), speech with the
  face turned away is masked.

## 6. Vision protocol v2

Changes apply only to the ASD owner socket (the `VisionAudioBridge`
connection). Presence events and the `faces` stream are unchanged.

### 6.1 Handshake

The bridge sends `asd_audio_start` with `"version": 2`. A v2 vision enables
§6.2–6.3. A v1 vision answers `invalid`; the bridge retries once with
`"version": 1` and runs without pinning or push (ASD on the nearest face,
results via `faces` at 10 Hz), logging `asd_protocol=v1`. Vision accepts both
versions.

### 6.2 `asd_track`

```json
→ {"cmd":"asd_track","stream_id":"…","track_id":17}
← {"event":"asd_track_status","stream_id":"…","track_id":17,"status":"pinned"}
```

- `track_id: null` clears the pin (`"status":"cleared"`). A non-owner socket,
  a wrong `stream_id` or a non-integer id gets `"status":"invalid"`.
- `AVBuffer.pin_track(id | None)`. While pinned, `track_faces` pushes crops only
  for the pinned track and only on frames where a detection confirms it; a PnP
  distance is not required. A hidden pinned track never falls back to another
  face: crops pause and the 3 s history prunes itself (the backend unpins
  after 0.5 s of absence, §5.1). Unpinned keeps today's nearest-face choice.
- The pin belongs to the stream: stop or reset clears it. The bridge stores
  its desired pin and re-sends it after each `ready`.

### 6.3 Push on publish

```json
← {"event":"asd","stream_id":"…","track_id":17,"t0":1790000000.12,"frame_secs":0.04,"probabilities":[25 floats]}
```

- A successful `AVBuffer.publish()` calls a callback injected by `main.py`,
  which sets an `asyncio.Event` on the WS loop via `call_soon_threadsafe`.
  `_asd_push_loop` sends `latest_result` to the owner only.
- Coalescing, not queueing: several publishes during one send produce one
  event with the newest result; an already-sent `t0` is never re-sent.
- The backend bridge passes `asd` events to `FaceTrackBuffer.add_asd()`, a
  separate deque bounded to 3 s that `active_speaker()` reads under v2. The
  `asd` field inside `faces` stays for v1 and is ignored under v2.

### 6.4 Worker wake-up

`_asd_loop` waits on a `threading.Condition` signalled by `push_audio` and
`push_crop`, with an 80 ms timeout, instead of `time.sleep(0.08)`. Still one
window in flight; the existing `_issued_t0` token still drops stale results.

### 6.5 Latency budget (estimates, to be measured)

| Stage | Now | After |
|---|---|---|
| Worker wait | 0–80 ms | ≈ 0 |
| Light-ASD inference (RTX 3060) | a few ms | same |
| `faces` tick wait | 0–100 ms | 0 |
| Local WS | < 1 ms | < 1 ms |

The remaining limit is camera/track-loop lag: the last bin needs a crop within
80 ms of its centre. `asd_age_ms` (publish time − window end) is logged to
measure it.

## 7. Slow path, resources, failures

### 7.1 Segment collection (`SpeakerAudioProcessor._diarize`)

- Per slot, append a row's PCM only when that slot is the only active slot,
  there is no overlap, and the bot is not audible (including the 0.3 s tail).
- Per-slot buffer ≤ 3 s. When it reaches `embed_segment_secs`, submit and
  clear.
- `target_confirmed` = ≥ 80% of the segment's rows were ACCEPT with
  `source=asd` on `target_track`. Only such segments enroll; all segments
  update `voice_sim`/`bot_sim`.

### 7.2 `EmbeddingWorker`

- Own single-thread executor (not the diarizer's).
- At most 1 job running and 1 pending job per slot (≤ 5 total); a newer job
  for a slot replaces its pending one; the worker takes the longest-waiting
  slot first.
- Each job carries `(lock_epoch, slot, segment_end)`. A result applies only if
  the epoch is unchanged and `segment_end` is newer than the slot's stored one.

### 7.3 Model and voiceprints

- `TitaNetEmbedder`: loaded once per process like `_diarizer()` (failure
  remembered, warm-up call). VRAM and latency are measured at implementation;
  the plan must confirm the pretrained model name in the installed NeMo.
- Customer voiceprint: L2-normalised embeddings; normalised mean of the last
  ≤ 8 enrolled embeddings.
- Bot voiceprint: `VieNeuTTSService` calls `on_bot_audio` with rendered PCM;
  the first ~3 s is resampled 48 kHz → 16 kHz and embedded once per process,
  cached by voice id, never reset by the FSM.

### 7.4 Bounds

| Buffer | Bound |
|---|---|
| Diarizer queue / bridge packets | 2 chunks / 8 packets (existing) |
| Per-slot segment buffer | ≤ 3 s × 4 slots |
| Embedding jobs | 1 running + ≤ 4 pending |
| Voiceprint | ≤ 8 embeddings |
| `FaceTrackBuffer` ASD deque | 3 s |
| Binder | 4 slots × visible tracks; evidence half-life 5 s; tracks absent > 5 s pruned |
| `IdentityTable` | ≤ 4 rows |

### 7.5 Fail-closed STT (LOCKED only)

A row that still has no verdict when its audio is released to STT
(`stt_mask_delay_secs` after arrival) is silenced and counted in
`stt_masked_no_verdict`. Pre-lock behaviour is unchanged.

### 7.6 Degradation

| Failure | Behaviour |
|---|---|
| Embedder load or inference fails | Voice disabled for the process; fusion runs on ASD + binder; rules needing `voice_match` count as no match (masked/REJECT). Logged once. |
| Vision ASD `disabled`/`unavailable` | No lock; current behaviour |
| Vision disconnects while LOCKED | `faces` goes stale → "face not visible" rows → voiceprint path; pin re-sent on reconnect |
| No diarizer | Existing fallback (gate without diarizer) |
| Kiosk FSM not running | Never PRE_LOCK; current behaviour |
| FSM reset | `lock_epoch += 1`; results of older epochs dropped; `asd_track(null)` |

## 8. Configuration (`[voice.speaker]`)

| Key | Default | Notes |
|---|---|---|
| `identity` | `"none"` | `"fusion"` requires `enabled`, `diarizer="sortformer"`, `vision_faces`, `vision_asd`; otherwise load fails |
| `lock_asd_frames` | 6 | 0.48 s of ASD-confirmed solo speech |
| `voiceprint_min_secs` | 2.0 | |
| `voice_match` | 0.65 | starting value, tuned from live-trial logs |
| `voice_reject` | 0.45 | must be < `voice_match` |
| `bot_match` | 0.70 | |
| `embed_segment_secs` | 1.0 | |
| `embedder_model` | `"titanet_small"` | confirmed during planning |

`identity="none"` must behave exactly as today. The ordering-kiosk preset sets
`identity="fusion"`.

## 9. Testing and acceptance

### 9.1 Tests (TDD; pure logic needs no GPU)

- `tests/server/test_speaker_identity.py` (new): lock only in FSM `active`;
  6 ASD rows for one (T,S) within 3 s lock; MAR alone never locks; release +
  epoch on FSM exit; slot rebind by voice match and by ASD co-occurrence; track
  absence > 0.5 s unpins, T′ is adopted only with ASD + a TARGET slot + ready
  voiceprint, the old track returning is re-pinned; binder mapping threshold
  and dominance; a closer person never
  steals; label classification; every row of §5.3 (parametrised); stale
  epoch and older `segment_end` dropped.
- `tests/server/test_speaker_audio.py` (extend, fake diarizer/embedder):
  segment collector takes solo non-bot rows only; enrollment only from
  `target_confirmed`; worker bounds, replacement and oldest-first; fail-closed
  mask after lock, unchanged before; TSE enrollment from voiceprint audio.
- Turn detection: post-lock VAD onset alone opens nothing; 1 ACCEPT opens with
  the bot silent; barge-in needs 3; overlap never barges in.
- Pipeline scenarios with fakes: TV speech while the bot speaks → no
  interruption and every `SttAudioFrame` silent; target turned away with a
  ready voiceprint → ACCEPT; closer bystander → target unchanged; echo →
  REJECT via bot voiceprint.
- `tests/server/test_speaker_vision.py`: v2 handshake, v1 fallback on
  `invalid`, pin re-sent after reconnect, `asd` → `add_asd`, stale stream
  ignored.
- Vision: `asd_track` owner/stream validation, `null` clears, pin survives
  reconnect; pinned `AVBuffer` never switches track and needs no PnP; push
  goes to the owner only, coalesces, never repeats a `t0`; v1 clients
  unchanged; `tests/test_architecture.py` still passes (the publish callback is
  injected by `main.py`).
- GPU test (skipped without CUDA/NeMo): TitaNet loads, 192-d output, 1 s
  latency measured.
- All existing tests pass; with `identity="none"` they are the regression
  guard.

### 9.2 Structured logs (INFO, one `key=value` line each)

1. `speaker_lock event=lock|rebind_slot|rebind_track|release epoch= track= slots= asd_frames= after_s= reason=`
2. `speaker_identity slot= label= voice_sim= bot_sim= seg_s= embed_ms= pending=`
3. Per turn (extends the `close_turn` line): `verdict= frames={A,R,U} sources={asd,mar,voice} masked_s= overlap_s= tse= target_track= target_slots=`
4. `bargein allowed=true accept_run=` / `bargein blocked reason=overlap|not_target|uncertain`
5. Session summary at `_stop`: `lock_after_s`, rebind counts, ASD
   `effective_use`/`late`, `asd_age_ms` and `embed_ms` p50/p95,
   `stt_masked_no_verdict`, `echo_rejects`, `bargein_blocked`.
6. The existing `transcript '…'` line is kept: it is what Gemini heard.

### 9.3 Live trial (operator-run on Prod)

Each scenario runs twice: `identity="none"` (baseline), then `"fusion"`.
Claims about voice quality come from the operator, not from health checks.

| # | Scenario | Pass |
|---|---|---|
| S1 | Customer alone orders | Lock on the first utterance; every turn answered |
| S2 | Bystander talks nearby, also standing closer | 0 bystander words in `transcript`; 0 barge-ins; target unchanged |
| S3 | Video with speech plays while the bot speaks and while the customer speaks | No interruption; transcript holds only the customer; TSE visible in logs |
| S4 | Customer looks down at the menu / turns away and speaks | ACCEPT after the voiceprint is ready; missed turns ≤ baseline |
| S5 | Background music | No turn opened |
| S6 | Loud speaker echo | Bot never interrupts itself |
| S7 | Customer A leaves, customer B arrives | `release` then a new `lock` for B; A's voiceprint gone |

ASD `effective_use` rises and `late` falls versus baseline.

## 10. Rollout

1. Vision v2 (accepts v1 and v2) deployed on Dev and Prod first.
2. Backend with `identity="none"` default; existing tests green.
3. Ordering-kiosk preset switches to `identity="fusion"` for the live trial.
