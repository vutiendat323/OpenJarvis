# Voice Speaker Embedding + Audio-Visual Speaker Fusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Lock one target customer per kiosk session and let only that person's speech reach STT, open turns, or barge in, using Sortformer slots, Light-ASD, MAR and a TitaNet voiceprint.

**Architecture:** A pure identity layer (`speaker_identity.py`: voiceprint, slot↔track binder, `TargetLock`, `FusionGate`) sits behind the existing `AudioOnlyGate` interface; a bounded async `EmbeddingWorker` feeds it cached embeddings, so the per-80 ms path only looks up tables. Vision gains protocol v2 (`asd_track` pin, ASD pushed on publish, worker woken by input) and keeps v1 for old clients.

**Tech Stack:** Python 3.12, Pipecat 1.8.1, NeMo 3.0.0 (Sortformer, TitaNet-small), ONNX Runtime (Light-ASD), `websockets`, numpy, scipy, pytest + anyio.

**Spec:** `docs/superpowers/specs/2026-09-30-voice-speaker-fusion-design.md`

## Repositories, branches, commands

| | Vision (Part A) | Backend (Part B) |
|---|---|---|
| Path | `~/Projects/jarvis/vision` (own git repo) | `~/Projects/jarvis/OpenJarvis` |
| Branch | `feat/asd-protocol-v2`, created from the current `feat/mouth-activity` | `feat/voice-speaker-fusion`, created from `feat/live-voice-mode` (holds the spec) |
| Run tests | `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests/<path> -q` (the vision venv has no pytest; tests import `vision.*` from the parent dir) | `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/<path> -q` |
| Leave alone | uncommitted `presentation/console/js/console.js` | uncommitted `frontend/vite.config.ts` |

Every commit ends with the trailer `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>` (second `-m`). Stage only the files a step names. Do not push.

Part A ships first (spec §10): a v2 Vision still serves v1 backends, and the Part B bridge falls back to v1 against an old Vision.

## Global Constraints

- `identity = "none"` (the default) must behave exactly as today; every existing test must keep passing unchanged except the two protocol-version assertions this plan updates (Task A3, Task B6).
- `identity = "fusion"` requires `enabled`, `diarizer = "sortformer"`, `vision_faces`, `vision_asd`; otherwise settings load fails.
- Defaults: `lock_asd_frames = 6`, `voiceprint_min_secs = 2.0`, `voice_match = 0.65`, `voice_reject = 0.45` (must be < `voice_match`), `bot_match = 0.70`, `embed_segment_secs = 1.0`, `embedder_model = "titanet_small"`.
- Lock window 3 s; target absent after 0.5 s; binder half-life 5 s, map threshold 1.0, dominance 2×, track forget 5 s; voiceprint ≤ 8 embeddings; segment buffer ≤ 3 s per slot; `target_confirmed` ≥ 80 % ASD rows.
- Embedding jobs: 1 running + ≤ 1 pending per slot; newer job for a slot replaces its pending one; longest-waiting slot first; results tagged `(lock_epoch, slot, segment_end)`, stale ones dropped.
- Everything that mutates `IdentityTable`/binder/voiceprint runs on the pipeline event loop; no locks in `speaker_identity.py`.
- Only the target's audio reaches STT after the lock: UNCERTAIN (except target overlap with TSE or visible target speech) and verdict-less rows are silenced.
- No new dependency: NeMo, torch, scipy are already in `.venv`. `titanet_small` downloads from NGC on first load (network needed once).
- Vision layer rule (`vision/tests/test_architecture.py`) must pass: domain imports stdlib/numpy only; presentation may import domain.
- Backend and Vision share one host clock (`time.time()`).

## Review Focus

1. A customer chewing or smiling (mouth moving) while a TV voice on another slot talks → that speech is REJECTed, never ACCEPTed on MAR. Test: Task B5 `test_other_slot_is_rejected_even_while_the_target_mouth_moves`.
2. The kiosk FSM flaps `active → cleanup → active` (customer steps back and returns) → the old lock is released, old embeddings are dropped, and a new lock forms only on fresh ASD evidence. Test: Task B3 `test_fsm_flap_releases_then_relocks_on_fresh_evidence`.
3. Vision restarts mid-session → the bridge reconnects and re-sends the pin without waiting for the next target change. Test: Task B6 `test_pin_is_sent_on_v2_and_resent_after_reconnect`.
4. The bot has not spoken yet (no bot voiceprint) → the legacy playback-overlap echo rule still labels the bot's slot BOT. Test: Task B3 `test_labels` (`bot_echo=True` case).
5. The embedder failed to load → fusion keeps running on ASD/MAR: a turned-away target is UNCERTAIN (masked), nothing crashes. Tests: Task B5 case `target_hidden_no_voice`, Task B8 `test_without_an_embedder_rows_still_flow_and_nothing_is_submitted`.

---

## Part A — Vision (repo `~/Projects/jarvis/vision`)

### Task A1: AVBuffer pin, input signal, publish listener

**Files:**
- Modify: `vision/domain/active_speaker.py` (class `AVBuffer`)
- Test: `vision/tests/domain/test_active_speaker.py` (append)

**Interfaces:**
- Produces: `AVBuffer.pin_track(track_id: int | None) -> None`, `AVBuffer.pinned_track -> int | None` (property), `AVBuffer.wait_for_input(timeout: float) -> None`, `AVBuffer.set_publish_listener(listener: Callable[[AsdResult], None] | None) -> None`. `publish()` calls the listener after a successful publish, outside the lock. `start_stream`/`stop_stream` clear the pin.

- [ ] **Step 1: Create the branch**

```bash
cd ~/Projects/jarvis/vision && git switch -c feat/asd-protocol-v2
```

- [ ] **Step 2: Write the failing tests** (append to `vision/tests/domain/test_active_speaker.py`)

```python
# ---------------------------------------------------------------------------
# Protocol v2: pinned track, input signal, publish listener.
# ---------------------------------------------------------------------------

import threading
import time


def _issued(buffer, stream_id="s1", track_id=7):
    buffer.start_stream(stream_id)
    feed_window(buffer, stream_id, track_id, 100.0, 0.0)
    return buffer.next_window(offset_secs=0.0, now=101.0)


class TestPinnedTrack:
    def test_pin_selects_the_track_and_is_reported(self):
        buffer = AVBuffer()
        buffer.start_stream("s1")
        buffer.pin_track(5)
        assert buffer.pinned_track == 5
        assert buffer._track_id == 5

    def test_unpin_keeps_the_current_selection(self):
        buffer = AVBuffer()
        buffer.start_stream("s1")
        buffer.pin_track(5)
        buffer.pin_track(None)
        assert buffer.pinned_track is None
        assert buffer._track_id == 5

    def test_the_pin_belongs_to_the_stream(self):
        buffer = AVBuffer()
        buffer.start_stream("s1")
        buffer.pin_track(5)
        buffer.start_stream("s2")
        assert buffer.pinned_track is None
        buffer.pin_track(6)
        buffer.stop_stream("s2")
        assert buffer.pinned_track is None


class TestPublishListener:
    def test_listener_gets_each_published_result(self):
        buffer = AVBuffer()
        window = _issued(buffer)
        seen = []
        buffer.set_publish_listener(seen.append)
        result = AsdResult("s1", 7, window.audio_t0, np.full(25, 0.9, np.float32))
        assert buffer.publish(result)
        assert seen == [result]

    def test_a_refused_result_never_reaches_the_listener(self):
        buffer = AVBuffer()
        window = _issued(buffer)
        seen = []
        buffer.set_publish_listener(seen.append)
        stale = AsdResult("s1", 7, window.audio_t0 - 0.08, np.full(25, 0.9, np.float32))
        assert not buffer.publish(stale)
        assert seen == []

    def test_a_failing_listener_never_breaks_publish(self):
        buffer = AVBuffer()
        window = _issued(buffer)

        def boom(_result):
            raise RuntimeError("socket gone")

        buffer.set_publish_listener(boom)
        result = AsdResult("s1", 7, window.audio_t0, np.full(25, 0.9, np.float32))
        assert buffer.publish(result)
        assert buffer.latest_result(101.0) is result


class TestInputSignal:
    def test_wait_returns_as_soon_as_audio_arrives(self):
        buffer = AVBuffer()
        buffer.start_stream("s1")
        done = threading.Event()

        def waiter():
            buffer.wait_for_input(2.0)
            done.set()

        thread = threading.Thread(target=waiter)
        thread.start()
        time.sleep(0.05)
        started = time.monotonic()
        buffer.push_audio("s1", 0, 10.0, np.zeros(PACKET_SAMPLES, np.int16))
        assert done.wait(1.0)
        assert time.monotonic() - started < 0.5
        thread.join()

    def test_wait_times_out_without_input(self):
        buffer = AVBuffer()
        started = time.monotonic()
        buffer.wait_for_input(0.05)
        assert 0.04 <= time.monotonic() - started < 0.5
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests/domain/test_active_speaker.py -q`
Expected: FAIL with `AttributeError: 'AVBuffer' object has no attribute 'pin_track'` (and the other new names).

- [ ] **Step 4: Implement**

In `vision/domain/active_speaker.py`, add `import logging` below `import threading`, and `from collections.abc import Callable` below `from dataclasses import dataclass`; add after the constants:

```python
_log = logging.getLogger(__name__)
```

In `AVBuffer.__init__`, after `self._issued_t0: float | None = None` add:

```python
        # Protocol v2: the backend's locked customer. While set, the track
        # loop crops only this track, even when another face is nearer.
        self._pinned: int | None = None
        # Wakes the ASD worker when audio or a crop arrives, instead of a
        # fixed 80 ms hop. Shares the buffer lock.
        self._input = threading.Condition(self._lock)
        self._publish_listener: Callable[[AsdResult], None] | None = None
```

In `_reset_locked`, add as its last line:

```python
        self._pinned = None
```

Add these methods after `select_track`:

```python
    @property
    def pinned_track(self) -> int | None:
        with self._lock:
            return self._pinned

    def pin_track(self, track_id: int | None) -> None:
        """Hold ASD on one track (the backend's locked customer); None releases it."""
        with self._lock:
            self._pinned = track_id
        if track_id is not None:
            self.select_track(track_id)

    def wait_for_input(self, timeout: float) -> None:
        """Block until audio or a crop arrives, or *timeout* seconds pass."""
        with self._input:
            self._input.wait(timeout)

    def set_publish_listener(self, listener: Callable[[AsdResult], None] | None) -> None:
        """Called with each published result, on the publishing thread."""
        self._publish_listener = listener
```

In `push_audio`, directly after `self._prune_audio_locked()` add `self._input.notify_all()`. In `push_crop`, directly after `self._crops.append((ts, np.array(crop, dtype=np.uint8, copy=True)))` add `self._input.notify_all()`.

Replace `publish` with:

```python
    def publish(self, result: AsdResult) -> bool:
        with self._lock:
            if (result.stream_id != self._stream_id or result.track_id != self._track_id
                    or result.audio_t0 != self._issued_t0):
                return False
            self._result = result
            listener = self._publish_listener
        if listener is not None:
            try:
                listener(result)
            except Exception:  # a listener must never stop the ASD worker
                _log.exception("ASD publish listener failed")
        return True
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests/domain/test_active_speaker.py vision/tests/test_architecture.py vision/tests/test_asd_capture.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/jarvis/vision
git add domain/active_speaker.py tests/domain/test_active_speaker.py
git commit -m "feat(asd): pin a track, signal new input, notify on publish" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task A2: Track loop honours the pin; ASD worker wakes on input

**Files:**
- Modify: `vision/application/track_faces.py:78-95`
- Modify: `vision/application/active_speaker.py` (`_asd_loop`)
- Test: `vision/tests/application/test_pipeline.py`, `vision/tests/application/test_active_speaker.py` (append)

**Interfaces:**
- Consumes: `AVBuffer.pinned_track`, `AVBuffer.pin_track`, `AVBuffer.wait_for_input` (Task A1).
- Produces: nothing new; behaviour only.

- [ ] **Step 1: Write the failing tests**

Append to `vision/tests/application/test_pipeline.py`:

```python
def test_pinned_track_is_kept_even_when_another_face_is_nearer(two_faces_pipeline):
    state = two_faces_pipeline
    snap = _wait_for(state, lambda s: len([p for p in s.people if p.raw is not None]) >= 2)
    farther_id = max((p for p in snap.people if p.raw is not None), key=lambda p: p.raw).id

    state.asd.start_stream("s1")
    state.asd.pin_track(farther_id)
    # ASD selection runs once per new camera frame: feed a few.
    for _ in range(5):
        state.put_frame(np.zeros((480, 640, 3), dtype=np.uint8))
        time.sleep(0.03)
    _wait_until(lambda: len(state.asd._crops) >= 2)
    assert state.asd._track_id == farther_id


def test_hidden_pinned_track_is_never_replaced(toggleable_face_pipeline):
    state, detector = toggleable_face_pipeline
    state.asd.start_stream("s1")
    _wait_until(lambda: len(state.asd._crops) >= 1)
    pinned = state.asd._track_id
    state.asd.pin_track(pinned)
    crops_before = len(state.asd._crops)

    detector.miss = True
    for _ in range(10):
        state.put_frame(np.zeros((480, 640, 3), dtype=np.uint8))
        time.sleep(0.02)
    time.sleep(0.2)
    assert state.asd._track_id == pinned
    assert len(state.asd._crops) <= crops_before + 1
```

Append to `vision/tests/application/test_active_speaker.py`:

```python
class TestAsdLoopWakeUp:
    def test_the_worker_waits_on_input_not_a_fixed_sleep(self, monkeypatch):
        state = _fresh_state()
        waits = []
        real_wait = state.asd.wait_for_input

        def spy(timeout):
            waits.append(timeout)
            real_wait(timeout)

        monkeypatch.setattr(state.asd, "wait_for_input", spy)
        detector = _Detector()
        thread = _run(state, detector)
        try:
            assert _wait(lambda: state.asd.latest_result(time.time()) is not None)
        finally:
            _stop(state, thread)
        assert waits and all(w == pytest.approx(0.08) for w in waits)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests/application/test_pipeline.py vision/tests/application/test_active_speaker.py -q`
Expected: FAIL: the pinned test sees the nearer track selected; the wake-up test finds `waits` empty (the loop still sleeps).

- [ ] **Step 3: Implement**

In `vision/application/track_faces.py`, replace the block from `if state.asd.active and frame_seq != last_asd_seq:` through `state.asd.push_crop(nearest_id, frame_ts, crop)` with:

```python
        if state.asd.active and frame_seq != last_asd_seq:
            last_asd_seq = frame_seq
            pinned = state.asd.pinned_track
            if pinned is not None:
                # The backend locked this customer: stay on them whether or
                # not they are nearest or visible, and without a PnP distance
                # (a turned head still has a mouth to read).
                chosen_id = pinned
                chosen_box = next((box for _, tid, box in confirmed if tid == pinned), None)
            else:
                # Nearest confirmed track with a PnP distance this pass; None
                # clears the selection (and its crop/result history).
                chosen_id, chosen_z, chosen_box = None, None, None
                for _, tid, box in confirmed:
                    z = face_distances.get(tid)
                    if z is not None and (chosen_z is None or z < chosen_z):
                        chosen_id, chosen_z, chosen_box = tid, z, box
            state.asd.select_track(chosen_id)
            if chosen_box is not None:
                try:
                    crop = prepare_face_crop(frame, chosen_box)
                except Exception as e:
                    print(f"[track] ASD crop error: {e}")
                    crop = None
                if crop is not None:
                    state.asd.push_crop(chosen_id, frame_ts, crop)
```

In `vision/application/active_speaker.py`, in `_asd_loop`, replace `time.sleep(PACKET_SECS)` with:

```python
        # Woken by new audio or a crop; the timeout keeps a stalled input
        # from parking the worker for good.
        state.asd.wait_for_input(PACKET_SECS)
```

and update the docstring's first line to `Score the newest complete window of the selected track as soon as input arrives.`

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests/application vision/tests/test_architecture.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/vision
git add application/track_faces.py application/active_speaker.py tests/application/test_pipeline.py tests/application/test_active_speaker.py
git commit -m "feat(asd): keep ASD on the pinned track and wake the worker on input" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task A3: WebSocket protocol v2 (`asd_track`, pushed `asd` events)

**Files:**
- Modify: `vision/presentation/ws_server.py`
- Create: `vision/tests/fixtures/asd_protocol_v2.json`
- Test: `vision/tests/presentation/test_ws_server.py`, `vision/tests/test_asd_protocol.py`

**Interfaces:**
- Consumes: `AVBuffer.pin_track`, `AVBuffer.set_publish_listener`, `AVBuffer.latest_result` (Task A1).
- Produces (wire, spec §6):
  - `asd_audio_start` accepts `"version": 1 | 2`.
  - `→ {"cmd":"asd_track","stream_id":str,"track_id":int|null}` / `← {"event":"asd_track_status","stream_id","track_id","status":"pinned"|"cleared"|"invalid"}`.
  - `← {"event":"asd","stream_id","track_id","t0","frame_secs":0.04,"probabilities":[25]}` to the v2 owner only.
  - Server methods: `_asd_track(websocket, data) -> dict`, `_asd_event() -> dict | None`, `_publish_listener(loop) -> Callable`, `async _asd_push_loop()`.

- [ ] **Step 1: Write the failing tests**

In `vision/tests/presentation/test_ws_server.py`, in `TestAsdAudioStart.test_wrong_version_or_format_is_invalid`, change the first parametrize entry `{"version": 2}` to `{"version": 3}`. Then append:

```python
def _publish(state, track_id=3, stream_id="s1"):
    """One real window for *stream_id* and a published result for it."""
    asd = state.asd
    first = time.time() - 1.2
    for i in range(13):
        asd.push_audio(stream_id, i, first + i * 0.08, np.zeros(1280, dtype=np.int16))
    asd.select_track(track_id)
    audio_t0 = first + 13 * 0.08 - 1.0
    for k in range(25):
        asd.push_crop(track_id, audio_t0 + 0.02 + 0.04 * k, np.zeros((112, 112), np.uint8))
    window = asd.next_window(offset_secs=0.0, now=time.time())
    asd.publish(AsdResult(window.stream_id, window.track_id, window.audio_t0,
                          np.full(25, 0.9, np.float32)))
    return window


def _v2_owner():
    state, server = _ready_server()
    owner = _Sock()
    _send(server, owner, _start_msg(version=2))
    return state, server, owner


def _track_msg(track_id, stream_id="s1"):
    return json.dumps({"cmd": "asd_track", "stream_id": stream_id, "track_id": track_id})


class TestAsdProtocolV2Start:
    def test_version_two_is_ready(self):
        state, server = _ready_server()
        a = _Sock()
        _send(server, a, _start_msg(version=2))
        assert a.events()[-1]["status"] == "ready"
        assert state.asd.active


class TestAsdTrack:
    def test_owner_pins_and_clears(self):
        state, server, owner = _v2_owner()
        _send(server, owner, _track_msg(9))
        assert owner.events()[-1] == {"event": "asd_track_status", "stream_id": "s1",
                                      "track_id": 9, "status": "pinned"}
        assert state.asd.pinned_track == 9
        _send(server, owner, _track_msg(None))
        assert owner.events()[-1]["status"] == "cleared"
        assert state.asd.pinned_track is None

    @pytest.mark.parametrize("message", [
        _track_msg(9, stream_id="other"),
        _track_msg("9"),
        _track_msg(True),
        _track_msg(-1),
    ])
    def test_bad_pins_are_invalid(self, message):
        state, server, owner = _v2_owner()
        _send(server, owner, message)
        assert owner.events()[-1]["status"] == "invalid"
        assert state.asd.pinned_track is None

    def test_a_non_owner_cannot_pin(self):
        state, server, owner = _v2_owner()
        other = _Sock()
        _send(server, other, _track_msg(9))
        assert other.events()[-1]["status"] == "invalid"
        assert state.asd.pinned_track is None

    def test_a_v1_stream_cannot_pin(self):
        state, server = _ready_server()
        owner = _Sock()
        _send(server, owner, _start_msg(version=1), _track_msg(9))
        assert owner.events()[-1]["status"] == "invalid"

    def test_a_new_stream_drops_the_pin(self):
        state, server, owner = _v2_owner()
        _send(server, owner, _track_msg(9), _start_msg("s2", version=2))
        assert state.asd.pinned_track is None


class TestAsdPush:
    def test_event_carries_the_newest_result_once(self):
        state, server, owner = _v2_owner()
        window = _publish(state)
        event = server._asd_event()
        assert event["event"] == "asd" and event["stream_id"] == "s1"
        assert event["track_id"] == 3 and event["t0"] == window.audio_t0
        assert event["frame_secs"] == 0.04 and len(event["probabilities"]) == 25
        assert server._asd_event() is None  # never the same t0 twice

    def test_a_v1_owner_gets_no_push(self):
        state, server = _ready_server()
        owner = _Sock()
        _send(server, owner, _start_msg(version=1))
        _publish(state)
        assert server._asd_event() is None

    def test_push_goes_to_the_owner_only_and_coalesces(self):
        state, server, owner = _v2_owner()
        watcher = _Sock()
        _send(server, watcher, json.dumps({"cmd": "faces"}))

        async def scenario():
            server._asd_published = asyncio.Event()
            task = asyncio.create_task(server._asd_push_loop())
            _publish(state)
            server._asd_published.set()
            server._asd_published.set()
            await asyncio.sleep(0.05)
            task.cancel()

        asyncio.run(scenario())
        assert [e["event"] for e in owner.events()].count("asd") == 1
        assert watcher.sent == []

    def test_a_worker_thread_publish_wakes_the_push_loop(self):
        state, server, owner = _v2_owner()

        async def scenario():
            server._asd_published = asyncio.Event()
            state.asd.set_publish_listener(server._publish_listener(asyncio.get_running_loop()))
            task = asyncio.create_task(server._asd_push_loop())
            await asyncio.to_thread(_publish, state)
            await asyncio.sleep(0.05)
            task.cancel()

        asyncio.run(scenario())
        assert [e["event"] for e in owner.events()].count("asd") == 1
```

Create `vision/tests/fixtures/asd_protocol_v2.json`:

```json
{
  "start": {"cmd": "asd_audio_start", "version": 2, "session_id": "fixture",
            "stream_id": "fixture-stream", "sample_rate": 16000, "channels": 1},
  "track": {"cmd": "asd_track", "stream_id": "fixture-stream", "track_id": 1},
  "track_status": {"event": "asd_track_status", "stream_id": "fixture-stream",
                   "track_id": 1, "status": "pinned"},
  "asd": {"event": "asd", "stream_id": "fixture-stream", "track_id": 1, "t0": 10.04,
          "frame_secs": 0.04,
          "probabilities": [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9,
                            0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.9]}
}
```

Append to `vision/tests/test_asd_protocol.py`:

```python
def test_shared_asd_protocol_v2():
    data = json.loads(
        (Path(__file__).parent / "fixtures/asd_protocol_v2.json").read_text()
    )
    start, track, status, asd = data["start"], data["track"], data["track_status"], data["asd"]
    assert (start["cmd"], start["version"], start["sample_rate"], start["channels"]) == (
        "asd_audio_start", 2, 16000, 1)
    assert track == {"cmd": "asd_track", "stream_id": start["stream_id"], "track_id": 1}
    assert status["event"] == "asd_track_status" and status["status"] == "pinned"
    assert (status["stream_id"], status["track_id"]) == (start["stream_id"], 1)
    assert asd["event"] == "asd" and asd["stream_id"] == start["stream_id"]
    assert asd["frame_secs"] == 0.04 and len(asd["probabilities"]) == 25
    assert all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in asd["probabilities"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests/presentation/test_ws_server.py vision/tests/test_asd_protocol.py -q`
Expected: FAIL: version 2 answers `invalid`; `_asd_event`/`_asd_push_loop`/`_publish_listener` missing; `asd_track` gets no reply.

- [ ] **Step 3: Implement** (`vision/presentation/ws_server.py`)

Below `ASD_PACKET_BYTES = 2560` add:

```python
ASD_PROTOCOL_VERSIONS = (1, 2)  # 2 adds asd_track pins and pushed asd events
```

In `VisionWSServer.__init__`, after `self._asd_stream: str | None = None` add:

```python
        self._asd_version: int | None = None
        self._asd_sent_t0: float | None = None
        self._asd_published: asyncio.Event | None = None
```

In `_on_message`, add before the `elif cmd == "asd_audio_stop":` branch:

```python
        elif cmd == "asd_track":
            await websocket.send(json.dumps(self._asd_track(websocket, data)))
```

Replace `_asd_start` with:

```python
    def _asd_start(self, websocket, data: dict) -> dict:
        stream_id = data.get("stream_id")
        reply = lambda status: {"event": "asd_audio_status", "status": status,
                                "stream_id": stream_id if isinstance(stream_id, str) else None}
        version = data.get("version")
        exact = (("sample_rate", 16000), ("channels", 1))
        if not (type(version) is int and version in ASD_PROTOCOL_VERSIONS
                and all(type(data.get(k)) is int and data.get(k) == v for k, v in exact)
                and all(isinstance(data.get(k), str) and data.get(k)
                        for k in ("session_id", "stream_id"))):
            return reply("invalid")
        if self._state.asd_status != "ready":
            return reply(self._state.asd_status)
        if self._asd_owner is not None and self._asd_owner is not websocket:
            return reply("busy")
        self._asd_owner, self._asd_stream = websocket, stream_id
        self._asd_version, self._asd_sent_t0 = version, None
        self._state.asd.start_stream(stream_id)
        return reply("ready")
```

Add after `_asd_audio`:

```python
    def _asd_track(self, websocket, data: dict) -> dict:
        """Pin ASD to the backend's locked customer (v2 owner only); null unpins."""
        stream_id, track_id = data.get("stream_id"), data.get("track_id")
        valid_id = track_id is None or (type(track_id) is int and track_id >= 0)
        reply = lambda status: {"event": "asd_track_status",
                                "stream_id": stream_id if isinstance(stream_id, str) else None,
                                "track_id": track_id if type(track_id) is int else None,
                                "status": status}
        if (websocket is not self._asd_owner or self._asd_version != 2
                or stream_id != self._asd_stream or not valid_id):
            return reply("invalid")
        self._state.asd.pin_track(track_id)
        return reply("cleared" if track_id is None else "pinned")

    def _asd_event(self) -> dict | None:
        """The newest ASD result for the v2 owner, once per window."""
        if self._asd_owner is None or self._asd_version != 2:
            return None
        result = self._state.asd.latest_result(time.time())
        if (result is None or result.stream_id != self._asd_stream
                or result.audio_t0 == self._asd_sent_t0):
            return None
        self._asd_sent_t0 = result.audio_t0
        return {"event": "asd", "stream_id": result.stream_id, "track_id": result.track_id,
                "t0": result.audio_t0, "frame_secs": BIN_STEP_SECS,
                "probabilities": [float(v) for v in result.probabilities]}

    def _publish_listener(self, loop):
        """Runs on the ASD worker thread: wake the push loop on the WS loop."""
        def listener(_result) -> None:
            loop.call_soon_threadsafe(self._asd_published.set)
        return listener

    async def _asd_push_loop(self):
        """Send each published ASD window to the v2 owner as soon as it lands.

        Coalescing, never a queue: publishes that land during a send leave one
        wake-up, and only the newest result goes out.
        """
        while True:
            await self._asd_published.wait()
            self._asd_published.clear()
            owner, event = self._asd_owner, self._asd_event()
            if owner is None or event is None:
                continue
            try:
                await owner.send(json.dumps(event))
            except Exception as e:
                _log.debug("WS asd send error: %s", e)
```

In `_release_asd`, change the last line to:

```python
            self._asd_owner, self._asd_stream, self._asd_version = None, None, None
```

In `_serve`, after `asyncio.create_task(self._faces_loop())` add:

```python
            self._asd_published = asyncio.Event()
            self._state.asd.set_publish_listener(
                self._publish_listener(asyncio.get_running_loop()))
            asyncio.create_task(self._asd_push_loop())
```

Update the module docstring's ASD paragraph to: `One voice session may stream its audio for active-speaker detection (asd_audio_start / asd_audio / asd_audio_stop). Protocol v2 also lets that socket pin ASD to one track (asd_track) and pushes each ASD result to it as soon as it is published (event "asd").`

- [ ] **Step 4: Run the whole vision suite**

Run: `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests -q`
Expected: PASS (all previously passing tests plus the new ones).

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/vision
git add presentation/ws_server.py tests/presentation/test_ws_server.py tests/test_asd_protocol.py tests/fixtures/asd_protocol_v2.json
git commit -m "feat(ws): ASD protocol v2 with track pins and pushed results" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## Part B — Backend (repo `~/Projects/jarvis/OpenJarvis`)

### Task B1: Fusion settings and the kiosk FSM accessor

**Files:**
- Modify: `src/openjarvis/server/voice/speaker.py` (`SpeakerSettings`, `load_speaker_settings`)
- Modify: `src/openjarvis/kiosk/runtime.py` (add `current_state`)
- Test: `tests/server/test_speaker.py` (append)

**Interfaces:**
- Produces: `SpeakerSettings` fields `identity: str = "none"`, `lock_asd_frames: int = 6`, `voiceprint_min_secs: float = 2.0`, `voice_match: float = 0.65`, `voice_reject: float = 0.45`, `bot_match: float = 0.70`, `embed_segment_secs: float = 1.0`, `embedder_model: str = "titanet_small"`. `openjarvis.kiosk.runtime.current_state() -> str`.

- [ ] **Step 1: Create the branch**

```bash
cd ~/Projects/jarvis/OpenJarvis && git switch -c feat/voice-speaker-fusion
```

- [ ] **Step 2: Write the failing tests** (append to `tests/server/test_speaker.py`)

```python
FUSION_PRESET = """[voice.speaker]
enabled = true
diarizer = "sortformer"
vision_faces = true
vision_asd = true
identity = "fusion"
"""


def _load(tmp_path, monkeypatch, body):
    path = tmp_path / "preset.toml"
    path.write_text(body)
    monkeypatch.setenv("OPENJARVIS_CONFIG", str(path))
    return load_speaker_settings()


def test_fusion_defaults(tmp_path, monkeypatch):
    s = _load(tmp_path, monkeypatch, FUSION_PRESET)
    assert s.identity == "fusion"
    assert (s.lock_asd_frames, s.voiceprint_min_secs, s.embed_segment_secs) == (6, 2.0, 1.0)
    assert (s.voice_match, s.voice_reject, s.bot_match) == (0.65, 0.45, 0.70)
    assert s.embedder_model == "titanet_small"


def test_identity_defaults_to_none():
    assert SpeakerSettings().identity == "none"


@pytest.mark.parametrize("missing", ["enabled", "vision_faces", "vision_asd"])
def test_fusion_needs_the_whole_speaker_gate(tmp_path, monkeypatch, missing):
    body = FUSION_PRESET.replace(f"{missing} = true", f"{missing} = false")
    if missing != "vision_asd":
        body = body.replace("vision_asd = true", "vision_asd = false")
    with pytest.raises(ValueError, match="fusion"):
        _load(tmp_path, monkeypatch, body)


@pytest.mark.parametrize("extra, error", [
    ('identity = "clusters"', "identity"),
    ("voice_reject = 0.7", "voice_thresholds"),
    ("lock_asd_frames = 0", "lock_asd_frames"),
    ('embedder_model = ""', "embedder_model"),
])
def test_bad_fusion_values_fail_the_load(tmp_path, monkeypatch, extra, error):
    if extra.startswith("identity"):
        body = FUSION_PRESET.replace('identity = "fusion"', extra)
    else:
        body = FUSION_PRESET + extra + "\n"
    with pytest.raises(ValueError, match=error):
        _load(tmp_path, monkeypatch, body)


def test_kiosk_state_accessor_reads_the_fsm(monkeypatch):
    import openjarvis.kiosk.runtime as runtime

    monkeypatch.setattr(runtime, "_current_state", "active")
    assert runtime.current_state() == "active"
```

Note on `test_fusion_needs_the_whole_speaker_gate`: `vision_asd` itself requires `enabled` and `vision_faces` (existing check), so the test turns `vision_asd` off alongside them; the fusion check must then be the one that fails (its message contains `fusion`).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker.py -q`
Expected: FAIL: `SpeakerSettings` has no `identity`; `current_state` missing.

- [ ] **Step 4: Implement**

In `SpeakerSettings`, after `separator_model: str = ...` add:

```python
    # Audio-visual fusion (spec 2026-09-30): lock one customer per kiosk
    # session. "none" keeps the gate exactly as it was.
    identity: str = "none"
    lock_asd_frames: int = 6
    voiceprint_min_secs: float = 2.0
    voice_match: float = 0.65
    voice_reject: float = 0.45
    bot_match: float = 0.70
    embed_segment_secs: float = 1.0
    embedder_model: str = "titanet_small"
```

In `load_speaker_settings`, before `return SpeakerSettings(`, add:

```python
    identity = _choice(section, "identity", defaults.identity, ("none", "fusion"))
    if identity == "fusion" and not (
        enabled and diarizer == "sortformer" and vision_faces and vision_asd
    ):
        raise ValueError("voice_speaker_fusion_needs_enabled_diarizer_faces_and_asd")
    voice_match = _fraction(section, "voice_match", defaults.voice_match)
    voice_reject = _fraction(section, "voice_reject", defaults.voice_reject)
    if voice_reject >= voice_match:
        raise ValueError("voice_speaker_voice_thresholds_invalid")
    embedder_model = section.get("embedder_model", defaults.embedder_model)
    if not isinstance(embedder_model, str) or not embedder_model.strip():
        raise ValueError("voice_speaker_embedder_model_must_be_a_name")
```

and add to the `SpeakerSettings(...)` call:

```python
        identity=identity,
        lock_asd_frames=_positive_int(
            section, "lock_asd_frames", defaults.lock_asd_frames
        ),
        voiceprint_min_secs=_positive_float(
            section, "voiceprint_min_secs", defaults.voiceprint_min_secs
        ),
        voice_match=voice_match,
        voice_reject=voice_reject,
        bot_match=_fraction(section, "bot_match", defaults.bot_match),
        embed_segment_secs=_positive_float(
            section, "embed_segment_secs", defaults.embed_segment_secs
        ),
        embedder_model=embedder_model.strip(),
```

`_positive_int` raises `voice_speaker_lock_asd_frames_must_be_a_positive_int` for 0, which the test matches.

In `src/openjarvis/kiosk/runtime.py`, after `_set_state` add:

```python
def current_state() -> KioskState:
    """The FSM's current state; Voice's target lock follows it (same process)."""
    return _current_state
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker.py tests/kiosk -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker.py src/openjarvis/kiosk/runtime.py tests/server/test_speaker.py
git commit -m "feat(voice): fusion speaker settings and a kiosk state accessor" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B2: Voiceprint and slot↔track binder

**Files:**
- Create: `src/openjarvis/server/voice/speaker_identity.py`
- Test: `tests/server/test_speaker_identity.py`

**Interfaces:**
- Produces: `ROW_SECS = 0.08`, `unit(v) -> np.ndarray`, `cosine(a, b) -> float`, `class Voiceprint` (`enroll(embedding, seconds)`, `seconds`, `ready(min_secs) -> bool`, `similarity(embedding) -> float | None`, `clear()`), `class SlotTrackBinder` (`update(t, slot_probs: Mapping[int, float], track_scores: Mapping[int, float])`, `track_of(slot) -> int | None`, `clear()`).

- [ ] **Step 1: Write the failing tests** (`tests/server/test_speaker_identity.py`)

```python
"""Pure identity layer: voiceprint, slot↔track binder, target lock, fusion gate."""

from __future__ import annotations

import numpy as np
import pytest

from openjarvis.server.voice.speaker_identity import (
    SlotTrackBinder,
    Voiceprint,
    cosine,
    unit,
)

E0 = np.array([1.0, 0.0, 0.0, 0.0], np.float32)
E1 = np.array([0.0, 1.0, 0.0, 0.0], np.float32)


def test_unit_and_cosine():
    assert np.linalg.norm(unit([3.0, 4.0])) == pytest.approx(1.0)
    assert cosine([1, 0], [2, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 5]) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        unit([0.0, 0.0])


def test_voiceprint_readiness_and_similarity():
    vp = Voiceprint()
    assert vp.similarity(E0) is None
    vp.enroll(E0 * 7, 1.0)
    assert not vp.ready(2.0)
    vp.enroll(E0, 1.0)
    assert vp.ready(2.0)
    assert vp.similarity(E0) == pytest.approx(1.0)
    assert vp.similarity(E1) == pytest.approx(0.0)


def test_voiceprint_keeps_the_newest_eight():
    vp = Voiceprint()
    for _ in range(8):
        vp.enroll(E1, 1.0)
    for _ in range(8):
        vp.enroll(E0, 1.0)
    assert vp.seconds == 8.0
    assert vp.similarity(E0) == pytest.approx(1.0)


def _rows(binder, n, slot, scores, t0=100.0):
    for i in range(n):
        binder.update(t0 + i * 0.08, {slot: 1.0}, scores)


def test_binder_maps_after_one_second_of_evidence():
    binder = SlotTrackBinder()
    _rows(binder, 12, 0, {5: 1.0})  # 0.96 s
    assert binder.track_of(0) is None
    _rows(binder, 1, 0, {5: 1.0}, t0=100.96)
    assert binder.track_of(0) == 5


def test_binder_needs_a_dominant_track():
    binder = SlotTrackBinder()
    _rows(binder, 20, 0, {5: 1.0, 6: 0.6})
    assert binder.track_of(0) is None
    _rows(binder, 20, 1, {5: 1.0, 6: 0.4}, t0=102.0)
    assert binder.track_of(1) == 5


def test_binder_evidence_decays_and_absent_tracks_are_forgotten():
    binder = SlotTrackBinder()
    _rows(binder, 13, 0, {5: 1.0})
    assert binder.track_of(0) == 5
    binder.update(110.0, {}, {6: 1.0})  # 9 s later; track 5 unseen for > 5 s
    assert binder.track_of(0) is None
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_identity.py -q`
Expected: FAIL with `ModuleNotFoundError: openjarvis.server.voice.speaker_identity`.

- [ ] **Step 3: Implement** (`src/openjarvis/server/voice/speaker_identity.py`)

```python
"""Who the locked customer is: voiceprint, slot↔face evidence, target lock.

Pure on purpose, like speaker.py: no models, no Pipecat. Everything here runs
on the Voice pipeline's event loop, so nothing locks.
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping

import numpy as np

ROW_SECS = 0.08  # one diarizer frame
VOICEPRINT_MAX_EMBEDDINGS = 8
BINDER_HALF_LIFE_SECS = 5.0
BINDER_MIN_EVIDENCE = 1.0  # ≈ 1 s of full slot-and-face evidence
BINDER_DOMINANCE = 2.0
TRACK_FORGET_SECS = 5.0


def unit(vector) -> np.ndarray:
    array = np.asarray(vector, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(array))
    if not math.isfinite(norm) or norm == 0.0:
        raise ValueError("embedding_must_be_finite_and_nonzero")
    return array / norm


def cosine(a, b) -> float:
    return float(np.dot(unit(a), unit(b)))


class Voiceprint:
    """Normalised mean of the newest enrolled embeddings, with their audio length."""

    def __init__(self, max_embeddings: int = VOICEPRINT_MAX_EMBEDDINGS) -> None:
        self._embeddings: deque[tuple[np.ndarray, float]] = deque(maxlen=max_embeddings)

    @property
    def seconds(self) -> float:
        return sum(seconds for _, seconds in self._embeddings)

    def enroll(self, embedding, seconds: float) -> None:
        self._embeddings.append((unit(embedding), float(seconds)))

    def ready(self, min_secs: float) -> bool:
        return self.seconds >= min_secs

    def similarity(self, embedding) -> float | None:
        if not self._embeddings:
            return None
        mean = np.mean([e for e, _ in self._embeddings], axis=0)
        return cosine(mean, embedding)

    def clear(self) -> None:
        self._embeddings.clear()


class SlotTrackBinder:
    """Decaying evidence that diarizer slot s is the voice of face track x.

    Each row adds p(slot) × a(track) × 80 ms, where a is the track's ASD
    probability or MAR. A slot maps to a track holding ≥ 1.0 (≈ 1 s of full
    evidence) and ≥ 2× its runner-up.
    """

    def __init__(self, half_life: float = BINDER_HALF_LIFE_SECS) -> None:
        self._half_life = half_life
        self._evidence: dict[int, dict[int, float]] = {}
        self._seen: dict[int, float] = {}
        self._t: float | None = None

    def update(
        self,
        t: float,
        slot_probs: Mapping[int, float],
        track_scores: Mapping[int, float],
    ) -> None:
        if self._t is not None and t > self._t:
            decay = 0.5 ** ((t - self._t) / self._half_life)
            for row in self._evidence.values():
                for track in row:
                    row[track] *= decay
        if self._t is None or t > self._t:
            self._t = t
        for track in track_scores:
            self._seen[track] = t
        for track in [x for x, seen in self._seen.items() if t - seen > TRACK_FORGET_SECS]:
            del self._seen[track]
            for row in self._evidence.values():
                row.pop(track, None)
        for slot, p in slot_probs.items():
            row = self._evidence.setdefault(slot, {})
            for track, score in track_scores.items():
                row[track] = row.get(track, 0.0) + p * score * ROW_SECS

    def track_of(self, slot: int) -> int | None:
        ranked = sorted(
            self._evidence.get(slot, {}).items(), key=lambda kv: kv[1], reverse=True
        )
        if not ranked or ranked[0][1] < BINDER_MIN_EVIDENCE:
            return None
        if len(ranked) > 1 and ranked[0][1] < BINDER_DOMINANCE * ranked[1][1]:
            return None
        return ranked[0][0]

    def clear(self) -> None:
        self._evidence.clear()
        self._seen.clear()
        self._t = None
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_identity.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker_identity.py tests/server/test_speaker_identity.py
git commit -m "feat(voice): voiceprint and slot-to-face evidence binder" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B3: TargetLock

**Files:**
- Modify: `src/openjarvis/server/voice/speaker_identity.py`
- Test: `tests/server/test_speaker_identity.py` (append)

**Interfaces:**
- Consumes: `Voiceprint`, `SlotTrackBinder`, `cosine` (Task B2); `SpeakerSettings` (Task B1).
- Produces:
  - `class Label(str, Enum)`: `TARGET`, `BOT`, `OTHER`, `UNKNOWN`.
  - `class LockState(str, Enum)`: `NONE`, `PRE_LOCK`, `LOCKED`.
  - `@dataclass(frozen=True) EmbeddingJob(epoch: int, slot: int, segment_end: float, seconds: float, pcm: np.ndarray, target_confirmed: bool)`.
  - `@dataclass SlotIdentity(voice_sim: float | None = None, bot_sim: float | None = None, segment_end: float = -inf)`.
  - `@dataclass(frozen=True) LockEvent(event: str, epoch: int, track: int | None, slots: tuple[int, ...], detail: str = "")` with `fields() -> str`.
  - `class TargetLock(settings)`: attributes `state`, `epoch`, `target_track`, `target_slots: set[int]`, `voiceprint`, `binder`, `slots: dict[int, SlotIdentity]`, `bot_voiceprint: np.ndarray | None`, `lock_count: int`, `last_locked_after_s: float | None`, `rebinds: Counter[str]`; properties `locked`, `voice_ready`; methods `observe_fsm(fsm_state, t)`, `observe_row(t, voices, *, anchor, anchor_asd_accept, target_visible=False, target_asd_accept=False)`, `observe_embedding(job, embedding) -> bool`, `label(slot, *, bot_echo=False) -> Label`, `voice_ok(slot) -> bool`, `target_absent(t) -> bool`, `desired_pin(t) -> int | None`, `drain_events() -> list[LockEvent]`.

- [ ] **Step 1: Write the failing tests** (append to `tests/server/test_speaker_identity.py`)

```python
from dataclasses import replace

from openjarvis.server.voice.speaker import SpeakerSettings
from openjarvis.server.voice.speaker_identity import (
    EmbeddingJob,
    Label,
    LockState,
    SlotIdentity,
    TargetLock,
)

FUSION = SpeakerSettings(
    enabled=True, diarizer="sortformer", vision_faces=True, vision_asd=True,
    identity="fusion",
)


def _asd_rows(lock, n, *, track=7, slot=0, t0=100.0, step=0.08):
    for i in range(n):
        lock.observe_row(t0 + i * step, [slot], anchor=track, anchor_asd_accept=True)


def _locked(track=7, slot=0, t0=100.0):
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", t0 - 1.0)
    _asd_rows(lock, 6, track=track, slot=slot, t0=t0)
    assert lock.locked
    lock.drain_events()
    return lock


def _job(lock, slot=0, end=101.0, seconds=1.0, confirmed=True):
    return EmbeddingJob(
        epoch=lock.epoch, slot=slot, segment_end=end, seconds=seconds,
        pcm=np.zeros(16000, np.int16), target_confirmed=confirmed,
    )


def test_nothing_locks_outside_an_active_kiosk_session():
    lock = TargetLock(FUSION)
    _asd_rows(lock, 10)
    assert lock.state is LockState.NONE


def test_six_asd_rows_of_one_pair_within_three_seconds_lock():
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", 99.0)
    assert lock.state is LockState.PRE_LOCK
    _asd_rows(lock, 5)
    assert not lock.locked
    _asd_rows(lock, 1, t0=100.4)
    assert lock.locked and lock.target_track == 7 and lock.target_slots == {0}
    (event,) = lock.drain_events()
    assert event.event == "lock" and "asd_frames=6" in event.fields()
    assert lock.last_locked_after_s == pytest.approx(1.4)


def test_rows_spread_beyond_three_seconds_never_lock():
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", 99.0)
    _asd_rows(lock, 6, step=0.7)
    assert not lock.locked


def test_mar_rows_and_overlap_rows_never_lock():
    lock = TargetLock(FUSION)
    lock.observe_fsm("active", 99.0)
    for i in range(10):
        lock.observe_row(100 + i * 0.08, [0], anchor=7, anchor_asd_accept=False)
        lock.observe_row(100 + i * 0.08, [0, 1], anchor=7, anchor_asd_accept=True)
    assert not lock.locked


def test_leaving_active_releases_and_bumps_the_epoch():
    lock = _locked()
    epoch = lock.epoch
    lock.observe_fsm("cleanup", 101.0)
    assert lock.state is LockState.NONE and lock.epoch == epoch + 1
    assert lock.target_track is None and lock.target_slots == set()
    (event,) = lock.drain_events()
    assert event.event == "release" and "reason=fsm_cleanup" in event.fields()


def test_fsm_flap_releases_then_relocks_on_fresh_evidence():
    lock = _locked()
    job = _job(lock)
    lock.observe_fsm("cleanup", 101.0)
    lock.observe_fsm("active", 101.1)
    assert not lock.observe_embedding(job, E0)  # the old customer's audio
    _asd_rows(lock, 6, track=9, slot=2, t0=101.2)
    assert lock.target_track == 9 and lock.target_slots == {2}
    assert lock.lock_count == 2


def test_a_closer_person_never_steals_the_lock():
    lock = _locked()
    for i in range(12):
        lock.observe_row(100.5 + i * 0.08, [0], anchor=9, anchor_asd_accept=True,
                         target_visible=True)
    assert lock.target_track == 7


def test_slot_rebind_by_asd_needs_solo_rows():
    lock = _locked()
    for i in range(6):
        lock.observe_row(100.5 + i * 0.08, [0, 3], anchor=7, anchor_asd_accept=True,
                         target_visible=True, target_asd_accept=True)
    assert 3 not in lock.target_slots
    for i in range(6):
        lock.observe_row(101.0 + i * 0.08, [2], anchor=7, anchor_asd_accept=True,
                         target_visible=True, target_asd_accept=True)
    assert lock.target_slots == {0, 2}
    assert [e.event for e in lock.drain_events()] == ["rebind_slot"]


def test_absent_target_is_unpinned_and_a_returning_target_repinned():
    lock = _locked()  # last seen at 100.4
    assert lock.desired_pin(100.8) == 7
    assert lock.desired_pin(101.0) is None
    lock.observe_row(101.0, [], anchor=None, anchor_asd_accept=False, target_visible=True)
    assert lock.desired_pin(101.0) == 7


def test_track_rebind_needs_absence_asd_a_target_slot_and_a_ready_voiceprint():
    lock = _locked()
    _asd_rows(lock, 6, track=9, slot=0, t0=101.0)
    assert lock.target_track == 7  # voiceprint not ready
    lock.voiceprint.enroll(E0, 2.0)
    _asd_rows(lock, 6, track=9, slot=5, t0=102.0)
    assert lock.target_track == 7  # slot 5 is not the target's voice
    _asd_rows(lock, 6, track=9, slot=0, t0=103.0)
    assert lock.target_track == 9
    assert lock.desired_pin(103.45) == 9


def test_embeddings_from_an_older_epoch_or_segment_are_dropped():
    lock = _locked()
    job = _job(lock)
    assert lock.observe_embedding(job, E0)
    assert not lock.observe_embedding(replace(job, segment_end=100.5), E0)
    stale = replace(job, segment_end=102.0)
    lock.observe_fsm("idle", 102.0)
    lock.observe_fsm("active", 102.0)
    assert not lock.observe_embedding(stale, E0)


def test_only_target_confirmed_segments_enroll():
    lock = _locked()
    lock.observe_embedding(_job(lock, confirmed=False), E0)
    assert lock.voiceprint.seconds == 0.0
    lock.observe_embedding(_job(lock, end=102.0, confirmed=True), E0)
    assert lock.voiceprint.seconds == 1.0


def test_a_matching_voice_adds_its_slot_once_the_voiceprint_is_ready():
    lock = _locked()
    lock.observe_embedding(_job(lock, seconds=2.0), E0)
    assert lock.voice_ready
    lock.observe_embedding(_job(lock, slot=3, end=102.0, confirmed=False), E0 * 2)
    assert 3 in lock.target_slots
    assert "reason=voice" in lock.drain_events()[-1].fields()


def test_labels():
    lock = _locked()
    lock.bot_voiceprint = E1
    lock.observe_embedding(_job(lock, slot=2, confirmed=False), E1)
    assert lock.label(2) is Label.BOT
    assert lock.label(5, bot_echo=True) is Label.BOT  # no embedding yet: echo rule
    assert lock.label(0) is Label.TARGET
    lock.observe_embedding(_job(lock, slot=0, end=102.0, seconds=2.0), E0)
    lock.observe_embedding(_job(lock, slot=3, end=102.0, confirmed=False),
                           np.array([0.0, 0.0, 1.0, 0.0], np.float32))
    assert lock.label(3) is Label.OTHER
    for i in range(13):
        lock.binder.update(103.0 + i * 0.08, {4: 1.0}, {9: 1.0})
    assert lock.label(4) is Label.OTHER
    assert lock.label(1) is Label.UNKNOWN


def test_voice_ok_needs_a_ready_voiceprint_and_a_match():
    lock = _locked()
    lock.slots[0] = SlotIdentity(voice_sim=0.9)
    assert not lock.voice_ok(0)
    lock.voiceprint.enroll(E0, 2.0)
    assert lock.voice_ok(0)
    lock.slots[0] = SlotIdentity(voice_sim=0.5)
    assert not lock.voice_ok(0)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_identity.py -q`
Expected: FAIL with `ImportError: cannot import name 'EmbeddingJob'`.

- [ ] **Step 3: Implement** (append to `speaker_identity.py`; extend its imports)

Change the imports at the top to:

```python
import math
from collections import Counter, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum

import numpy as np

from openjarvis.server.voice.speaker import SpeakerSettings
```

Add below the existing constants:

```python
LOCK_WINDOW_SECS = 3.0
TRACK_ABSENT_SECS = 0.5
```

Append:

```python
class Label(str, Enum):
    TARGET = "TARGET"
    BOT = "BOT"
    OTHER = "OTHER"
    UNKNOWN = "UNKNOWN"


class LockState(str, Enum):
    NONE = "NONE"
    PRE_LOCK = "PRE_LOCK"
    LOCKED = "LOCKED"


@dataclass(frozen=True)
class EmbeddingJob:
    """One slot's solo speech, sent to the embedder with what it belongs to."""

    epoch: int
    slot: int
    segment_end: float
    seconds: float
    pcm: np.ndarray
    target_confirmed: bool


@dataclass
class SlotIdentity:
    voice_sim: float | None = None
    bot_sim: float | None = None
    segment_end: float = -math.inf


@dataclass(frozen=True)
class LockEvent:
    event: str
    epoch: int
    track: int | None
    slots: tuple[int, ...]
    detail: str = ""

    def fields(self) -> str:
        return (
            f"event={self.event} epoch={self.epoch} track={self.track} "
            f"slots={list(self.slots)} {self.detail}"
        ).rstrip()


def _within(times: deque[float], t: float) -> int:
    """Record *t* and count the records inside the lock window ending at it."""
    times.append(t)
    while times and times[0] < t - LOCK_WINDOW_SECS:
        times.popleft()
    return len(times)


class TargetLock:
    """One kiosk session's locked customer (spec §5.1).

    NONE outside the FSM's ``active`` state; PRE_LOCK until Light-ASD confirms
    one face and one diarizer slot together; LOCKED until the FSM leaves
    ``active``. A face standing nearer never takes the lock.
    """

    def __init__(self, settings: SpeakerSettings) -> None:
        self._s = settings
        self.state = LockState.NONE
        self.epoch = 0
        self.target_track: int | None = None
        self.target_slots: set[int] = set()
        self.voiceprint = Voiceprint()
        self.binder = SlotTrackBinder()
        self.slots: dict[int, SlotIdentity] = {}
        self.bot_voiceprint: np.ndarray | None = None
        self.lock_count = 0
        self.last_locked_after_s: float | None = None
        self.rebinds: Counter[str] = Counter()
        self._pre_lock_at: float | None = None
        self._track_seen_at: float | None = None
        self._pairs: dict[tuple[int, int], deque[float]] = {}
        self._slot_asd: dict[int, deque[float]] = {}
        self._candidates: dict[int, deque[float]] = {}
        self._events: list[LockEvent] = []

    @property
    def locked(self) -> bool:
        return self.state is LockState.LOCKED

    @property
    def voice_ready(self) -> bool:
        return self.voiceprint.ready(self._s.voiceprint_min_secs)

    def drain_events(self) -> list[LockEvent]:
        events, self._events = self._events, []
        return events

    def observe_fsm(self, fsm_state: str, t: float) -> None:
        if fsm_state == "active":
            if self.state is LockState.NONE:
                self.state = LockState.PRE_LOCK
                self._pre_lock_at = t
            return
        if self.state is LockState.NONE:
            return
        released = self.locked
        self._reset()
        if released:
            self._events.append(
                LockEvent("release", self.epoch, None, (), f"reason=fsm_{fsm_state}")
            )

    def _reset(self) -> None:
        self.epoch += 1
        self.state = LockState.NONE
        self.target_track = None
        self.target_slots = set()
        self.voiceprint.clear()
        self.binder.clear()
        self.slots.clear()
        self._pre_lock_at = None
        self._track_seen_at = None
        self._pairs.clear()
        self._slot_asd.clear()
        self._candidates.clear()

    def observe_row(
        self,
        t: float,
        voices: Sequence[int],
        *,
        anchor: int | None,
        anchor_asd_accept: bool,
        target_visible: bool = False,
        target_asd_accept: bool = False,
    ) -> None:
        need = self._s.lock_asd_frames
        if self.state is LockState.PRE_LOCK:
            if anchor is not None and anchor_asd_accept and len(voices) == 1:
                pair = (anchor, voices[0])
                count = _within(self._pairs.setdefault(pair, deque()), t)
                if count >= need:
                    self._lock(t, anchor, voices[0], count)
            return
        if not self.locked:
            return
        if target_visible:
            self._track_seen_at = t
        if target_asd_accept and len(voices) == 1 and voices[0] not in self.target_slots:
            slot = voices[0]
            if _within(self._slot_asd.setdefault(slot, deque()), t) >= need:
                self._add_slot(slot, "asd")
        if (
            self.target_absent(t)
            and anchor is not None
            and anchor != self.target_track
            and anchor_asd_accept
            and self.voice_ready
            and any(s in self.target_slots for s in voices)
        ):
            if _within(self._candidates.setdefault(anchor, deque()), t) >= need:
                old = self.target_track
                self.target_track = anchor
                self._track_seen_at = t
                self._candidates.clear()
                self.rebinds["track"] += 1
                self._events.append(
                    LockEvent(
                        "rebind_track", self.epoch, anchor,
                        tuple(sorted(self.target_slots)), f"from={old}",
                    )
                )

    def _lock(self, t: float, track: int, slot: int, frames: int) -> None:
        self.state = LockState.LOCKED
        self.target_track, self.target_slots = track, {slot}
        self._track_seen_at = t
        self._pairs.clear()
        self.lock_count += 1
        after = t - self._pre_lock_at if self._pre_lock_at is not None else 0.0
        self.last_locked_after_s = after
        self._events.append(
            LockEvent("lock", self.epoch, track, (slot,),
                      f"asd_frames={frames} after_s={after:.2f}")
        )

    def _add_slot(self, slot: int, reason: str) -> None:
        self.target_slots.add(slot)
        self.rebinds["slot"] += 1
        self._events.append(
            LockEvent(
                "rebind_slot", self.epoch, self.target_track,
                tuple(sorted(self.target_slots)), f"slot={slot} reason={reason}",
            )
        )

    def target_absent(self, t: float) -> bool:
        return self._track_seen_at is None or t - self._track_seen_at > TRACK_ABSENT_SECS

    def desired_pin(self, t: float) -> int | None:
        """The track Vision's ASD should stay on; None lets it pick the nearest."""
        if not self.locked or self.target_absent(t):
            return None
        return self.target_track

    def observe_embedding(self, job: EmbeddingJob, embedding) -> bool:
        """Apply one embedding result; False when it is stale."""
        if job.epoch != self.epoch:
            return False
        ident = self.slots.setdefault(job.slot, SlotIdentity())
        if job.segment_end <= ident.segment_end:
            return False
        ident.segment_end = job.segment_end
        ident.voice_sim = self.voiceprint.similarity(embedding)
        ident.bot_sim = (
            None if self.bot_voiceprint is None else cosine(self.bot_voiceprint, embedding)
        )
        if self.locked and job.target_confirmed:
            self.voiceprint.enroll(embedding, job.seconds)
        if (
            self.locked
            and job.slot not in self.target_slots
            and self.voice_ready
            and ident.voice_sim is not None
            and ident.voice_sim >= self._s.voice_match
        ):
            self._add_slot(job.slot, "voice")
        return True

    def label(self, slot: int, *, bot_echo: bool = False) -> Label:
        ident = self.slots.get(slot)
        if bot_echo or (
            ident is not None
            and ident.bot_sim is not None
            and ident.bot_sim >= self._s.bot_match
        ):
            return Label.BOT
        if slot in self.target_slots:
            return Label.TARGET
        if (
            self.voice_ready
            and ident is not None
            and ident.voice_sim is not None
            and ident.voice_sim < self._s.voice_reject
        ):
            return Label.OTHER
        track = self.binder.track_of(slot)
        if self.locked and track is not None and track != self.target_track:
            return Label.OTHER
        return Label.UNKNOWN

    def voice_ok(self, slot: int) -> bool:
        ident = self.slots.get(slot)
        return (
            self.voice_ready
            and ident is not None
            and ident.voice_sim is not None
            and ident.voice_sim >= self._s.voice_match
        )
```

Note on `test_labels`: after enrolling 2 s of `E0` for slot 0, slot 3's orthogonal embedding scores cosine 0.0 < `voice_reject` → OTHER; slot 4 maps to track 9 through the binder (13 rows ≥ 1.0) while the target is 7 → OTHER.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_identity.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker_identity.py tests/server/test_speaker_identity.py
git commit -m "feat(voice): lock one target customer per kiosk session" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B4: FaceTrackBuffer reads pushed ASD and answers presence

**Files:**
- Modify: `src/openjarvis/server/voice/speaker.py` (class `FaceTrackBuffer`)
- Test: `tests/server/test_speaker.py` (append)

**Interfaces:**
- Produces: `FaceTrackBuffer.use_pushed_asd(pushed: bool) -> None`, `FaceTrackBuffer.add_asd(window: dict) -> None`, `FaceTrackBuffer.tracks_at(t: float) -> list[dict]`, `FaceTrackBuffer.present(track_id: int, t0: float, t1: float) -> bool`. With pushed ASD on, `active_speaker()` reads pushed windows and ignores the `asd` field inside `faces` events.

- [ ] **Step 1: Write the failing tests** (append to `tests/server/test_speaker.py`)

```python
import time as _time


def _window(stream="s", track=7, t0=None, p=0.9):
    t0 = _time.time() - 0.9 if t0 is None else t0
    return {"stream_id": stream, "track_id": track, "t0": t0, "frame_secs": 0.04,
            "probabilities": [p] * 25}


def _faces_event(ts, *tracks, asd=None):
    rows = []
    for track in tracks:
        row = {"track_id": track, "distance_m": 0.8, "mouth_activity": 0.2}
        if asd is not None and track == asd["track_id"]:
            row["asd"] = asd
        rows.append(row)
    return {"event": "faces", "ts": ts, "tracks": rows}


def test_pushed_asd_is_read_and_faces_asd_ignored():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    faces.add(_faces_event(now, 7, asd=_window(p=0.1)))  # a v1 field: ignored
    assert faces.active_speaker(7, now - 0.08, now, stream_id="s", now=now) is None
    faces.add_asd(_window(p=0.9))
    assert faces.active_speaker(7, now - 0.08, now, stream_id="s", now=now) == pytest.approx(0.9)


def test_pushed_asd_for_another_stream_is_dropped():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    faces.add_asd(_window(stream="old"))
    assert faces.active_speaker(7, now - 0.08, now, stream_id="s", now=now) is None


def test_v1_mode_still_reads_asd_from_faces_events():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.add(_faces_event(now, 7, asd=_window(p=0.8)))
    assert faces.active_speaker(7, now - 0.08, now, stream_id="s", now=now) == pytest.approx(0.8)


def test_snapshot_without_asd_drops_pushed_windows():
    now = _time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    faces.add_asd(_window())
    frozen = faces.snapshot(include_asd=False)
    assert frozen.active_speaker(7, now - 0.08, now, stream_id="s", now=now) is None
    assert faces.snapshot().active_speaker(7, now - 0.08, now, stream_id="s", now=now)


def test_tracks_at_and_present():
    faces = FaceTrackBuffer()
    faces.add(_faces_event(100.0, 7, 9))
    faces.add(_faces_event(100.5, 9))
    assert [t["track_id"] for t in faces.tracks_at(100.1)] == [7, 9]
    assert faces.present(7, 99.8, 100.2)
    assert not faces.present(7, 100.3, 100.7)
    assert FaceTrackBuffer().tracks_at(1.0) == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker.py -q`
Expected: FAIL: `use_pushed_asd` / `add_asd` / `tracks_at` / `present` missing.

- [ ] **Step 3: Implement** (`FaceTrackBuffer` in `speaker.py`)

In `__init__`, add:

```python
        # Protocol v2: ASD windows pushed by Vision as their own events.
        self._asd_windows: deque[dict] = deque()
        self._asd_pushed = False
```

In `set_asd_stream`, inside the lock after `self._asd_stream = stream_id`, add `self._asd_windows.clear()`.

Add methods after `asd_stream_matches`:

```python
    def use_pushed_asd(self, pushed: bool) -> None:
        """v2: read ASD from pushed windows and ignore the faces-event field."""
        with self._lock:
            self._asd_pushed = pushed
            self._asd_windows.clear()

    def add_asd(self, window: dict) -> None:
        """One pushed ASD window for the current stream (older ones pruned)."""
        t0 = window.get("t0")
        if isinstance(t0, bool) or not isinstance(t0, (int, float)) or not math.isfinite(t0):
            return
        with self._lock:
            if window.get("stream_id") != self._asd_stream:
                return
            self._asd_windows.append(dict(window))
            while self._asd_windows and self._asd_windows[0]["t0"] < t0 - self._history:
                self._asd_windows.popleft()

    def tracks_at(self, t: float) -> list[dict]:
        """The tracks of the faces event nearest to *t*."""
        with self._lock:
            if not self._events:
                return []
            event = min(self._events, key=lambda e: abs(e["ts"] - t))
            return [dict(f) for f in event.get("tracks", ())]

    def present(self, track_id: int, t0: float, t1: float) -> bool:
        with self._lock:
            return any(
                f.get("track_id") == track_id
                for e in self._events
                if t0 <= e["ts"] <= t1
                for f in e.get("tracks", ())
            )
```

In `snapshot`, inside the lock add `snapshot._asd_windows = copy.deepcopy(self._asd_windows)` and `snapshot._asd_pushed = self._asd_pushed`; in its `if not include_asd:` branch add `snapshot._asd_windows.clear()`.

In `active_speaker`, replace the `windows = [...]` comprehension inside the lock with:

```python
            if self._asd_pushed:
                windows = [
                    dict(w) for w in self._asd_windows if w.get("track_id") == track_id
                ]
            else:
                windows = [
                    dict(f["asd"])
                    for event in self._events
                    for f in event.get("tracks", ())
                    if f.get("track_id") == track_id and isinstance(f.get("asd"), dict)
                ]
```

- [ ] **Step 4: Run the speaker suites**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker.py tests/server/test_speaker_vision.py tests/server/test_voice_asd_gate_replay.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker.py tests/server/test_speaker.py
git commit -m "feat(voice): face buffer reads pushed ASD windows and track presence" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B5: FusionGate

**Files:**
- Modify: `src/openjarvis/server/voice/speaker_identity.py`
- Test: `tests/server/test_speaker_identity.py` (append)

**Interfaces:**
- Consumes: `AudioOnlyGate`, `FaceTrackBuffer` (+ Task B4 methods), `VISION_WINDOW_SECS`, `Verdict` from `speaker.py`; `TargetLock`, `Label` (Task B3).
- Produces: `class FusionGate(AudioOnlyGate)`: `__init__(settings, faces, *, fsm_state: Callable[[], str], bot_voiceprint: Callable[[], np.ndarray | None] = lambda: None)`; attributes `settings`, `lock`, `row_voices: list[int]`, `row_asd_track: int | None`, `row_is_target: bool`, `target_overlap: bool`, `target_speaking_visibly: bool`, `last_source: str | None` (`"asd"|"mar"|"voice"|"audio"|None`), `echo_rejects: int`; property `locked`; `frame(probs, *, bot_speaking, t=None, asd_stream_id=None) -> Verdict | None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/server/test_speaker_identity.py`)

```python
import time

from openjarvis.server.voice.speaker import AudioOnlyGate, FaceTrackBuffer, Verdict
from openjarvis.server.voice.speaker_identity import FusionGate

A, R, U = Verdict.ACCEPT, Verdict.REJECT, Verdict.UNCERTAIN


def _row(*slots):
    return tuple(0.9 if s in slots else 0.0 for s in range(4))


def _faces(*, mouth=None, asd=None, tracks=(7,)):
    now = time.time()
    faces = FaceTrackBuffer()
    faces.set_asd_stream("s")
    faces.use_pushed_asd(True)
    for k in range(-2, 25):
        faces.add({"event": "faces", "ts": now + k * 0.1, "tracks": [
            {"track_id": x, "distance_m": 0.8, "mouth_activity": mouth} for x in tracks]})
    if asd is not None:
        faces.add_asd({"stream_id": "s", "track_id": tracks[0], "t0": now - 0.9,
                       "frame_secs": 0.04, "probabilities": [asd] * 25})
    return faces, now


def _locked_gate(faces, now, *, voice=False):
    gate = FusionGate(FUSION, faces, fsm_state=lambda: "active")
    gate.lock.observe_fsm("active", now - 1.0)
    for i in range(6):
        gate.lock.observe_row(now - 0.45 + i * 0.08, [0], anchor=7, anchor_asd_accept=True)
    if voice:
        gate.lock.voiceprint.enroll(E0, 2.0)
        gate.lock.slots[0] = SlotIdentity(voice_sim=0.9)
    return gate


def _frame(gate, now, *slots, times=1):
    verdict = None
    for i in range(times):
        verdict = gate.frame(_row(*slots), bot_speaking=False, t=now + i * 0.001,
                             asd_stream_id="s")
    return verdict


@pytest.mark.parametrize("case, faces_kw, voice, slots, expected, source", [
    ("silence", {"mouth": 0.0}, False, (), None, None),
    ("target_asd", {"asd": 0.9}, False, (0,), A, "asd"),
    ("target_mar", {"mouth": 0.8}, False, (0,), A, "mar"),
    ("target_still_voice", {"mouth": 0.0}, True, (0,), A, "voice"),
    ("target_still_no_voice", {"mouth": 0.0}, False, (0,), R, None),
    ("target_hidden_voice", {"mouth": 0.0, "tracks": (9,)}, True, (0,), A, "voice"),
    ("target_hidden_no_voice", {"mouth": 0.0, "tracks": (9,)}, False, (0,), U, None),
    ("unknown_with_target_asd", {"asd": 0.9}, False, (1,), A, "asd"),
    ("unknown_without_evidence", {"mouth": 0.0}, False, (1,), U, None),
])
def test_locked_verdict_table(case, faces_kw, voice, slots, expected, source):
    faces, now = _faces(**faces_kw)
    gate = _locked_gate(faces, now, voice=voice)
    assert _frame(gate, now, *slots) is expected
    assert gate.last_source == source
    if source == "asd":
        assert gate.row_asd_track == 7


def test_bot_only_rows_are_rejected_and_counted():
    faces, now = _faces(mouth=0.9)
    gate = _locked_gate(faces, now)
    gate.lock.slots[2] = SlotIdentity(bot_sim=0.9)
    assert _frame(gate, now, 2) is R
    assert gate.echo_rejects == 1


def test_other_slot_is_rejected_even_while_the_target_mouth_moves():
    faces, now = _faces(mouth=0.9)  # the customer chews; the TV talks
    gate = _locked_gate(faces, now, voice=True)
    gate.lock.slots[3] = SlotIdentity(voice_sim=0.1)
    assert _frame(gate, now, 3) is R


def test_target_overlap_is_uncertain_and_flags_visible_speech():
    faces, now = _faces(mouth=0.8)
    gate = _locked_gate(faces, now, voice=True)
    gate.lock.slots[3] = SlotIdentity(voice_sim=0.1)
    assert _frame(gate, now, 0, 3, times=3) is U
    assert gate.target_overlap and gate.target_speaking_visibly


def test_overlap_without_the_target_is_rejected():
    faces, now = _faces(mouth=0.8)
    gate = _locked_gate(faces, now, voice=True)
    gate.lock.slots[3] = SlotIdentity(voice_sim=0.1)
    assert _frame(gate, now, 1, 3, times=3) is R
    assert not gate.target_overlap


def test_before_the_lock_it_is_the_audio_only_gate_and_asd_rows_lock_it():
    faces, now = _faces(mouth=0.9, asd=0.9)
    fusion = FusionGate(FUSION, faces, fsm_state=lambda: "active")
    plain = AudioOnlyGate(FUSION, faces=faces)
    for i in range(6):
        t = now + i * 0.01
        expected = plain.frame(_row(0), bot_speaking=False, t=t, asd_stream_id="s")
        assert fusion.frame(_row(0), bot_speaking=False, t=t, asd_stream_id="s") is expected
    assert fusion.locked and fusion.lock.target_track == 7


def test_without_asd_evidence_the_lock_never_forms():
    faces, now = _faces(mouth=0.9)  # MAR only: Vision ASD unavailable
    gate = FusionGate(FUSION, faces, fsm_state=lambda: "active")
    for i in range(20):
        gate.frame(_row(0), bot_speaking=False, t=now + i * 0.01, asd_stream_id="s")
    assert not gate.locked


def test_outside_an_active_session_it_never_locks():
    faces, now = _faces(mouth=0.9, asd=0.9)
    gate = FusionGate(FUSION, faces, fsm_state=lambda: "idle")
    for i in range(10):
        gate.frame(_row(0), bot_speaking=False, t=now + i * 0.01, asd_stream_id="s")
    assert not gate.locked
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_identity.py -q`
Expected: FAIL with `ImportError: cannot import name 'FusionGate'`.

- [ ] **Step 3: Implement** (append to `speaker_identity.py`)

Add to its imports:

```python
import time
from collections.abc import Callable

from openjarvis.server.voice.speaker import (
    VISION_WINDOW_SECS,
    AudioOnlyGate,
    FaceTrackBuffer,
    Verdict,
)
```

(merge with the existing `from openjarvis.server.voice.speaker import SpeakerSettings` line.) Append:

```python
class FusionGate(AudioOnlyGate):
    """The target-speaker gate with a locked customer (spec §5).

    Before the lock it is the AudioOnlyGate, plus bot-voiceprint echo. Once
    Light-ASD has confirmed one face and one diarizer slot together, a row's
    verdict follows who each active slot is, not whose mouth moves nearest.
    """

    def __init__(
        self,
        settings: SpeakerSettings,
        faces: FaceTrackBuffer | None,
        *,
        fsm_state: Callable[[], str],
        bot_voiceprint: Callable[[], np.ndarray | None] = lambda: None,
    ) -> None:
        super().__init__(settings, faces=faces)
        self.settings = settings
        self.lock = TargetLock(settings)
        self._fsm_state = fsm_state
        self._bot_voiceprint = bot_voiceprint
        self.row_voices: list[int] = []
        self.row_asd_track: int | None = None
        self.row_is_target = False
        self.target_overlap = False
        self.target_speaking_visibly = False
        self.last_source: str | None = None
        self.echo_rejects = 0

    @property
    def locked(self) -> bool:
        return self.lock.locked

    def frame(
        self,
        probs: Sequence[float],
        *,
        bot_speaking: bool,
        t: float | None = None,
        asd_stream_id: str | None = None,
    ) -> Verdict | None:
        t = time.time() if t is None else t
        self.lock.observe_fsm(self._fsm_state(), t)
        self.lock.bot_voiceprint = self._bot_voiceprint()
        self.row_voices, self.row_asd_track, self.row_is_target = [], None, False
        self.target_overlap = self.target_speaking_visibly = False
        self.last_source = None
        if self.lock.locked:
            verdict = self._locked_frame(probs, bot_speaking, t, asd_stream_id)
        else:
            verdict = self._pre_lock_frame(probs, bot_speaking, t, asd_stream_id)
        if (
            verdict is Verdict.REJECT
            and not self.row_voices
            and any(p >= self._threshold for p in probs)
        ):
            self.echo_rejects += 1
        return verdict

    def _pre_lock_frame(self, probs, bot_speaking, t, stream_id):
        verdict = super().frame(
            probs, bot_speaking=bot_speaking, t=t, asd_stream_id=stream_id
        )
        active = [s for s, p in enumerate(probs) if p >= self._threshold]
        self.row_voices = [
            s for s in active
            if self.lock.label(s, bot_echo=self.is_echo(s)) is not Label.BOT
        ]
        if active and not self.row_voices:
            verdict = Verdict.REJECT  # the bot's own voice, known by its voiceprint
        detail = self.last_evidence_detail or {}
        anchor = detail.get("track_id")
        confirmed = verdict is Verdict.ACCEPT and detail.get("source") == "asd"
        if verdict is Verdict.ACCEPT:
            self.last_source = detail.get("source")
        if confirmed:
            self.row_asd_track = anchor
        self._update_binder(t, probs, anchor, detail.get("probability"))
        if self.lock.state is LockState.PRE_LOCK:
            self.lock.observe_row(
                t, self.row_voices, anchor=anchor, anchor_asd_accept=confirmed
            )
        return verdict

    def _locked_frame(self, probs, bot_speaking, t, stream_id):
        lock = self.lock
        active = [s for s, p in enumerate(probs) if p >= self._threshold]
        for s in active:
            self._active_frames[s] += 1
            if bot_speaking:
                self._bot_frames[s] += 1
        labels = {s: lock.label(s, bot_echo=self.is_echo(s)) for s in active}
        voices = [s for s in active if labels[s] is not Label.BOT]
        self.row_voices = voices
        overlap = self._overlap.update(len(voices))
        visible, asd, mouth = self._target_evidence(t, stream_id)
        asd_ok = asd is not None and asd >= self._asd_accept
        mouth_ok = mouth is not None and mouth >= self._mouth_active
        anchor, anchor_ok = (
            self._rebind_evidence(t, stream_id) if lock.target_absent(t) else (None, False)
        )
        lock.observe_row(
            t, voices, anchor=anchor, anchor_asd_accept=anchor_ok,
            target_visible=visible, target_asd_accept=asd_ok,
        )
        self._update_binder(t, probs, lock.target_track, asd)
        targets = [s for s in voices if labels[s] is Label.TARGET]
        self.last_evidence = (lock.target_track, mouth)
        self.last_evidence_detail = {
            "source": None, "t0": t - ROW_SECS, "t1": t, "stream_id": stream_id,
            "track_id": lock.target_track, "probability": asd, "mouth": mouth,
            "labels": {s: labels[s].value for s in active}, "fallback_reason": None,
        }
        if targets and self.vision_asd and stream_id is not None and visible:
            self._count_asd(asd)
        if not active:
            return None
        if not voices:
            return Verdict.REJECT
        if targets and overlap:
            self.target_overlap = True
            self.target_speaking_visibly = visible and (asd_ok or mouth_ok)
            return Verdict.UNCERTAIN
        if targets:
            self.row_is_target = True
            return self._target_verdict(targets[0], visible, asd_ok, mouth_ok)
        if overlap or all(labels[s] is Label.OTHER for s in voices):
            return Verdict.REJECT
        if asd_ok:
            # An UNKNOWN slot while the customer's face speaks: Sortformer gave
            # them a new slot. The binder learns it from this row.
            return self._accept("asd")
        return Verdict.UNCERTAIN

    def _target_verdict(self, slot, visible, asd_ok, mouth_ok):
        if visible:
            if asd_ok:
                return self._accept("asd")
            if mouth_ok:
                return self._accept("mar")
            if self.lock.voice_ok(slot):
                return self._accept("voice")  # MAR missed: hand or cup at the mouth
            return Verdict.REJECT
        if self.lock.voice_ok(slot):
            return self._accept("voice")
        return Verdict.UNCERTAIN

    def _accept(self, source: str) -> Verdict:
        self.last_source = source
        self.last_evidence_detail["source"] = source
        if source == "asd":
            self.row_asd_track = self.lock.target_track
        return Verdict.ACCEPT

    def _target_evidence(self, t, stream_id):
        """(visible, ASD probability, mouth) of the locked customer at row time t."""
        track, faces = self.lock.target_track, self._faces
        if (
            faces is None
            or track is None
            or not faces.fresh(t)
            or not faces.present(track, t - VISION_WINDOW_SECS, t + VISION_WINDOW_SECS)
        ):
            return False, None, None
        asd = (
            self._asd_probability(track, t, stream_id)
            if self.vision_asd and stream_id is not None
            else None
        )
        mouth = faces.mouth(track, t - VISION_WINDOW_SECS, t + VISION_WINDOW_SECS)
        return (asd is not None or mouth is not None), asd, mouth

    def _rebind_evidence(self, t, stream_id):
        """The nearest face and whether ASD hears it (Vision runs ASD on it
        while the locked customer is unpinned)."""
        faces = self._faces
        if faces is None or not faces.fresh(t):
            return None, False
        anchor = faces.anchor(t, self._anchor_max_m)
        if (
            anchor is None
            or anchor == self.lock.target_track
            or stream_id is None
            or not self.vision_asd
        ):
            return anchor, False
        probability = self._asd_probability(anchor, t, stream_id)
        return anchor, probability is not None and probability >= self._asd_accept

    def _update_binder(self, t, probs, asd_track, asd_probability) -> None:
        if self._faces is None or not self.row_voices:
            return
        scores: dict[int, float] = {}
        for track in self._faces.tracks_at(t):
            x = track.get("track_id")
            if isinstance(x, bool) or not isinstance(x, int):
                continue
            if x == asd_track and asd_probability is not None:
                scores[x] = float(asd_probability)
            else:
                mouth = self._faces.mouth(
                    x, t - VISION_WINDOW_SECS, t + VISION_WINDOW_SECS
                )
                scores[x] = 0.0 if mouth is None else float(mouth)
        self.lock.binder.update(
            t, {s: float(probs[s]) for s in self.row_voices}, scores
        )

    def _count_asd(self, asd: float | None) -> None:
        if asd is None:
            self.asd_counts[self.asd_row_reason or "gap"] += 1
        elif asd >= self._asd_accept:
            self.asd_counts["used_accept"] += 1
        elif asd <= self._asd_reject:
            self.asd_counts["used_reject"] += 1
        else:
            self.asd_counts["middle"] += 1
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_identity.py tests/server/test_speaker.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker_identity.py tests/server/test_speaker_identity.py
git commit -m "feat(voice): fusion gate judges rows by who each slot is" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B6: VisionAudioBridge v2 (fallback, pushed ASD, pin)

**Files:**
- Modify: `src/openjarvis/server/voice/speaker_vision.py`
- Create: `tests/fixtures/asd_protocol_v2.json` (byte-identical to the vision fixture from Task A3)
- Test: `tests/server/test_speaker_vision.py`, `tests/test_asd_protocol.py`

**Interfaces:**
- Consumes: `FaceTrackBuffer.use_pushed_asd`, `FaceTrackBuffer.add_asd` (Task B4); wire format of Task A3.
- Produces: `VisionAudioBridge.pin(track_id: int | None) -> None`, `VisionAudioBridge.protocol_version -> int` (property), `VisionAudioBridge.asd_age_ms: deque[float]` (≤ 512).

- [ ] **Step 1: Write the failing tests**

In `tests/server/test_speaker_vision.py`: in `test_bridge_packetizes_actual_samples_and_stops`, change the expected `"version": 1` to `"version": 2`; in `test_other_terminal_statuses_end_attempts`, change `@pytest.mark.parametrize("status", ["unavailable", "invalid"])` to `@pytest.mark.parametrize("status", ["unavailable"])` (under v2 a first `invalid` means "old Vision" and falls back; the new test below covers `invalid` as terminal). Then append:

```python
def _sequenced_connect(monkeypatch, sockets):
    import websockets

    opened = []

    @asynccontextmanager
    async def connect(*args, **kwargs):
        socket = sockets[len(opened)]
        opened.append(socket)
        try:
            yield socket
        finally:
            socket.closed = True

    monkeypatch.setattr(websockets, "connect", connect)
    monkeypatch.setattr(
        "openjarvis.server.voice.speaker_vision.RECONNECT_DELAYS", (0.01,)
    )
    return opened


@pytest.mark.anyio
async def test_old_vision_answering_invalid_to_v2_gets_v1_at_once(monkeypatch):
    sockets = [FakeSocket(), FakeSocket()]
    _sequenced_connect(monkeypatch, sockets)
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    await bridge.start()
    await eventually(lambda: bool(sockets[0].sent))
    first = sockets[0].sent[0]
    assert first["version"] == 2
    sockets[0].status(first["stream_id"], "invalid")
    await eventually(lambda: bool(sockets[1].sent))
    second = sockets[1].sent[0]
    assert second["version"] == 1
    sockets[1].status(second["stream_id"], "ready")
    await eventually(lambda: bridge.ready)
    assert bridge.protocol_version == 1
    bridge.pin(7)
    await asyncio.sleep(0.05)
    assert all(m["cmd"] != "asd_track" for m in sockets[1].sent)
    await bridge.close()


@pytest.mark.anyio
async def test_invalid_after_the_v1_fallback_ends_attempts(monkeypatch):
    sockets = [FakeSocket(), FakeSocket(), FakeSocket()]
    opened = _sequenced_connect(monkeypatch, sockets)
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    await bridge.start()
    await eventually(lambda: bool(sockets[0].sent))
    sockets[0].status(sockets[0].sent[0]["stream_id"], "invalid")
    await eventually(lambda: bool(sockets[1].sent))
    sockets[1].status(sockets[1].sent[0]["stream_id"], "invalid")
    await asyncio.sleep(0.05)
    assert len(opened) == 2
    await bridge.close()


@pytest.mark.anyio
async def test_pin_is_sent_on_v2_and_resent_after_reconnect(monkeypatch):
    sockets = [FakeSocket(), FakeSocket()]
    _sequenced_connect(monkeypatch, sockets)
    bridge = VisionAudioBridge("ws://vision", "voice-1", FaceTrackBuffer())
    bridge.pin(7)  # before ready: remembered
    await bridge.start()
    await eventually(lambda: bool(sockets[0].sent))
    sockets[0].status(sockets[0].sent[0]["stream_id"], "ready")
    await eventually(lambda: any(m["cmd"] == "asd_track" for m in sockets[0].sent))
    pin = next(m for m in sockets[0].sent if m["cmd"] == "asd_track")
    assert pin == {"cmd": "asd_track", "stream_id": sockets[0].sent[0]["stream_id"],
                   "track_id": 7}

    sockets[0].inbound.put_nowait(None)  # Vision restarts
    await eventually(lambda: bool(sockets[1].sent))
    sockets[1].status(sockets[1].sent[0]["stream_id"], "ready")
    await eventually(lambda: any(m["cmd"] == "asd_track" for m in sockets[1].sent))

    bridge.pin(None)
    await eventually(lambda: [m.get("track_id", 0) for m in sockets[1].sent
                              if m["cmd"] == "asd_track"][-1] is None)
    await bridge.close()


@pytest.mark.anyio
async def test_pushed_asd_reaches_the_face_buffer_and_is_aged(monkeypatch):
    sockets = [FakeSocket()]
    _sequenced_connect(monkeypatch, sockets)
    faces = FaceTrackBuffer()
    bridge = VisionAudioBridge("ws://vision", "voice-1", faces)
    await bridge.start()
    await eventually(lambda: bool(sockets[0].sent))
    stream_id = sockets[0].sent[0]["stream_id"]
    sockets[0].status(stream_id, "ready")
    await eventually(lambda: bridge.ready)
    now = time.time()
    window = {"event": "asd", "stream_id": stream_id, "track_id": 7, "t0": now - 1.0,
              "frame_secs": 0.04, "probabilities": [0.9] * 25}
    sockets[0].inbound.put_nowait(dict(window, stream_id="stale"))
    sockets[0].inbound.put_nowait(window)
    await eventually(lambda: faces.active_speaker(
        7, now - 0.1, now - 0.02, stream_id=stream_id, now=time.time()) is not None)
    assert len(bridge.asd_age_ms) == 1 and bridge.asd_age_ms[0] >= 0
    await bridge.close()
```

Copy the fixture: `cp ~/Projects/jarvis/vision/tests/fixtures/asd_protocol_v2.json ~/Projects/jarvis/OpenJarvis/tests/fixtures/asd_protocol_v2.json`, then append to `tests/test_asd_protocol.py` the same `test_shared_asd_protocol_v2` function as in Task A3 Step 1 (identical text; the file header says the assertions are identical in both repositories):

```python
def test_shared_asd_protocol_v2():
    data = json.loads(
        (Path(__file__).parent / "fixtures/asd_protocol_v2.json").read_text()
    )
    start, track, status, asd = data["start"], data["track"], data["track_status"], data["asd"]
    assert (start["cmd"], start["version"], start["sample_rate"], start["channels"]) == (
        "asd_audio_start", 2, 16000, 1)
    assert track == {"cmd": "asd_track", "stream_id": start["stream_id"], "track_id": 1}
    assert status["event"] == "asd_track_status" and status["status"] == "pinned"
    assert (status["stream_id"], status["track_id"]) == (start["stream_id"], 1)
    assert asd["event"] == "asd" and asd["stream_id"] == start["stream_id"]
    assert asd["frame_secs"] == 0.04 and len(asd["probabilities"]) == 25
    assert all(isinstance(p, (int, float)) and 0 <= p <= 1 for p in asd["probabilities"])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_vision.py tests/test_asd_protocol.py -q`
Expected: FAIL: start still says version 1; `pin`, `protocol_version`, `asd_age_ms` missing.

- [ ] **Step 3: Implement** (`speaker_vision.py`)

Add `import math` to the imports. Below `_MAX_PACKETS = 8` add:

```python
_PROTOCOL_VERSION = 2  # asd_track pins and pushed asd events; 1 is the fallback
_MAX_AGES = 512
```

In `__init__`, add after `self.last_wait_timed_out = False`:

```python
        self._version = _PROTOCOL_VERSION
        self._pin: int | None = None
        self._pin_dirty = False
        # Arrival age of pushed ASD windows (ms past the window's end), for
        # the session summary.
        self.asd_age_ms: deque[float] = deque(maxlen=_MAX_AGES)
```

Add after the `audio_seconds` property:

```python
    @property
    def protocol_version(self) -> int:
        return self._version

    def pin(self, track_id: int | None) -> None:
        """Hold Vision's ASD on the locked customer (v2); None lets it pick
        the nearest face. Remembered across reconnects."""
        if track_id == self._pin:
            return
        self._pin = track_id
        if self._ready and self._version == 2:
            self._pin_dirty = True
            self._queued.set()

    def _on_asd(self, event: dict, stream_id: str) -> None:
        if event.get("stream_id") != stream_id or not self._ready:
            return
        t0 = event.get("t0")
        if not isinstance(t0, bool) and isinstance(t0, (int, float)) and math.isfinite(t0):
            self.asd_age_ms.append((_now() - (t0 + 1.0)) * 1000.0)
        self._faces.add_asd(event)
```

Replace `_send` with:

```python
    async def _send(self, ws, stream_id: str) -> None:
        try:
            while self._stream_id == stream_id:
                await self._queued.wait()
                self._queued.clear()
                if self._pin_dirty:
                    self._pin_dirty = False
                    await ws.send(
                        json.dumps(
                            {"cmd": "asd_track", "stream_id": stream_id,
                             "track_id": self._pin}
                        )
                    )
                while self._queue and self._stream_id == stream_id:
                    seq, t0, pcm = self._queue.popleft()
                    await ws.send(
                        json.dumps(
                            {
                                "cmd": "asd_audio",
                                "stream_id": stream_id,
                                "seq": seq,
                                "t0": t0,
                                "pcm16_b64": base64.b64encode(pcm).decode("ascii"),
                            }
                        )
                    )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - reconnect after send failure
            logger.warning("Vision ASD audio send failed: {}", exc)
            await ws.close()
```

In `_run`: add `retry_now = False` next to `sender = None`; in the start message change `"version": 1` to `"version": self._version`; replace the `async for raw in ws:` body with:

```python
                        async for raw in ws:
                            try:
                                event = json.loads(raw)
                            except (TypeError, ValueError):
                                continue
                            if not isinstance(event, dict):
                                continue
                            if event.get("event") == "asd":
                                self._on_asd(event, stream_id)
                                continue
                            if (
                                event.get("event") != "asd_audio_status"
                                or event.get("stream_id") != stream_id
                            ):
                                continue
                            status = event.get("status")
                            if status == "ready" and not self._ready:
                                self._ready = True
                                self._faces.set_asd_stream(stream_id)
                                self._faces.use_pushed_asd(self._version == 2)
                                delay_idx = 0
                                if self._version == 2 and self._pin is not None:
                                    self._pin_dirty = True
                                    self._queued.set()
                                sender = asyncio.create_task(self._send(ws, stream_id))
                            elif status == "invalid" and self._version == 2:
                                # A Vision older than v2: no pins, ASD via faces.
                                self._version = 1
                                logger.warning(
                                    "Vision ASD asd_protocol=v1 (Vision predates v2)"
                                )
                                retry_now = True
                                break
                            elif status in ("disabled", "unavailable", "invalid"):
                                self._terminal = True
                                break
                            elif status == "busy":
                                break
```

and, after `if self._closed or self._terminal: break`, add:

```python
            if retry_now:
                continue
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_vision.py tests/test_asd_protocol.py tests/server/test_speaker_audio.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker_vision.py tests/server/test_speaker_vision.py tests/test_asd_protocol.py tests/fixtures/asd_protocol_v2.json
git commit -m "feat(voice): Vision ASD protocol v2 with pins, pushed results, v1 fallback" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B7: Speaker embedding (TitaNet, worker, bot voiceprint)

**Files:**
- Create: `src/openjarvis/server/voice/speaker_embedding.py`
- Test: `tests/server/test_speaker_embedding.py`, `tests/server/test_speaker_titanet.py` (GPU, opt-in)

**Interfaces:**
- Consumes: `EmbeddingJob`, `unit` (Tasks B2/B3).
- Produces: `SpeakerEmbedder` (Protocol: `embed(pcm: np.ndarray) -> np.ndarray`), `TitaNetEmbedder(model_name="titanet_small", *, device="cuda")`, `EMBEDDING_EXECUTOR`, `EmbeddingWorker(embedder, on_result: Callable[[EmbeddingJob, np.ndarray, float], None], *, executor=None)` with `submit(job)`, `clear()`, `pending: int`, `failed: bool`, `elapsed_ms: deque[float]`, `async close()`; `resample_to_16k(pcm: np.ndarray, sample_rate: int) -> np.ndarray`; `BotVoiceprint` with `embedding: np.ndarray | None`, `failed: bool`, `offer(audio: bytes, sample_rate: int, num_channels: int) -> np.ndarray | None`; `bot_audio_sink(embedder, bot, executor=None) -> Callable[[bytes, int, int], None]`.

- [ ] **Step 1: Write the failing tests** (`tests/server/test_speaker_embedding.py`)

```python
"""Bounded embedding worker and the bot's own voiceprint."""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pytest

from openjarvis.server.voice.speaker_embedding import (
    BotVoiceprint,
    EmbeddingWorker,
    bot_audio_sink,
    resample_to_16k,
)
from openjarvis.server.voice.speaker_identity import EmbeddingJob


class _Embedder:
    def __init__(self, gate=None, error=None):
        self.calls = []
        self.gate = gate
        self.error = error

    def embed(self, pcm):
        self.calls.append(len(pcm))
        if self.gate is not None:
            self.gate.wait(2.0)
        if self.error is not None:
            raise self.error
        return np.ones(4, np.float32)


def _job(slot, end):
    return EmbeddingJob(epoch=0, slot=slot, segment_end=end, seconds=1.0,
                        pcm=np.zeros(16000, np.int16), target_confirmed=False)


async def _eventually(check):
    for _ in range(200):
        if check():
            return
        await asyncio.sleep(0.01)
    assert check()


@pytest.mark.anyio
async def test_one_running_one_pending_per_slot_newest_wins_oldest_first():
    gate = threading.Event()
    results = []
    worker = EmbeddingWorker(_Embedder(gate), lambda job, emb, ms: results.append(job),
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await asyncio.sleep(0.02)
    worker.submit(_job(1, 1.1))
    worker.submit(_job(2, 1.2))
    worker.submit(_job(1, 1.3))  # replaces slot 1's pending job, keeps its place
    assert worker.pending == 2
    gate.set()
    await _eventually(lambda: len(results) == 3)
    assert [(j.slot, j.segment_end) for j in results] == [(0, 1.0), (1, 1.3), (2, 1.2)]
    assert len(worker.elapsed_ms) == 3
    await worker.close()


@pytest.mark.anyio
async def test_clear_drops_pending_jobs():
    gate = threading.Event()
    results = []
    worker = EmbeddingWorker(_Embedder(gate), lambda job, emb, ms: results.append(job),
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await asyncio.sleep(0.02)
    worker.submit(_job(1, 1.1))
    worker.clear()
    gate.set()
    await asyncio.sleep(0.1)
    assert [j.slot for j in results] == [0]
    await worker.close()


@pytest.mark.anyio
async def test_a_failure_disables_the_embedder_for_the_process():
    embedder = _Embedder(error=RuntimeError("cuda gone"))
    worker = EmbeddingWorker(embedder, lambda *a: None,
                             executor=ThreadPoolExecutor(max_workers=1))
    worker.submit(_job(0, 1.0))
    await _eventually(lambda: worker.failed)
    assert embedder.disabled is True
    worker.submit(_job(1, 1.1))
    await asyncio.sleep(0.05)
    assert embedder.calls == [16000]
    await worker.close()


def test_resample_48k_to_16k():
    out = resample_to_16k(np.zeros(48000, np.int16), 48000)
    assert out.dtype == np.int16 and len(out) == 16000
    same = np.arange(100, dtype=np.int16)
    assert resample_to_16k(same, 16000) is same


def _tts(samples, rate=48000, value=1000):
    return (np.ones(samples, np.int16) * value).tobytes()


def test_bot_voiceprint_takes_three_seconds_once():
    bot = BotVoiceprint()
    assert bot.offer(_tts(48000 * 2), 48000, 1) is None
    pcm = bot.offer(_tts(48000 * 2), 48000, 1)
    assert pcm is not None and len(pcm) == 48000
    assert bot.offer(_tts(48000), 48000, 1) is None
    assert BotVoiceprint().offer(_tts(4800), 48000, 2) is None


def test_bot_audio_sink_embeds_the_bot_once():
    bot = BotVoiceprint()
    embedder = _Embedder()
    executor = ThreadPoolExecutor(max_workers=1)
    sink = bot_audio_sink(embedder, bot, executor)
    for _ in range(4):
        sink(_tts(48000), 48000, 1)
    executor.shutdown(wait=True)
    assert embedder.calls == [48000]
    assert bot.embedding is not None and np.linalg.norm(bot.embedding) == pytest.approx(1.0)
```

GPU test `tests/server/test_speaker_titanet.py`:

```python
"""Real TitaNet on the local GPU (opt-in: needs NeMo + CUDA; downloads once)."""

from __future__ import annotations

import time

import numpy as np
import pytest

pytestmark = pytest.mark.nvidia

pytest.importorskip("nemo.collections.asr")
torch = pytest.importorskip("torch")
if not torch.cuda.is_available():
    pytest.skip("CUDA not available", allow_module_level=True)

from openjarvis.server.voice.speaker_embedding import TitaNetEmbedder  # noqa: E402


def test_titanet_small_embeds_one_second_quickly():
    embedder = TitaNetEmbedder("titanet_small")
    rng = np.random.default_rng(0)
    pcm = (rng.standard_normal(16000) * 3000).astype(np.int16)
    started = time.monotonic()
    embedding = embedder.embed(pcm)
    elapsed_ms = (time.monotonic() - started) * 1000
    print(f"titanet_small 1 s embed: {elapsed_ms:.1f} ms")
    assert embedding.shape == (192,) and np.isfinite(embedding).all()
    assert elapsed_ms < 100
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_embedding.py -q`
Expected: FAIL with `ModuleNotFoundError: openjarvis.server.voice.speaker_embedding`.

- [ ] **Step 3: Implement** (`src/openjarvis/server/voice/speaker_embedding.py`)

```python
"""Voice speaker embeddings: the model, a bounded worker, the bot's voiceprint.

Model packages (NeMo, torch) are imported only inside TitaNetEmbedder: the
``voice-speaker`` extra is optional, like the diarizer's.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections import deque
from collections.abc import Callable
from concurrent.futures import Executor, ThreadPoolExecutor
from typing import Protocol

import numpy as np
from loguru import logger

from openjarvis.server.voice.speaker_identity import EmbeddingJob, unit

SAMPLE_RATE = 16_000
BOT_ENROLL_SECS = 3.0
_MAX_TIMINGS = 512

# One GPU model, one thread, apart from the diarizer's so Sortformer never
# waits behind an embedding.
EMBEDDING_EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="embedder")


class SpeakerEmbedder(Protocol):
    def embed(self, pcm: np.ndarray) -> np.ndarray:
        """int16 mono 16 kHz -> one float32 speaker embedding."""
        ...


class TitaNetEmbedder:
    """NeMo TitaNet (192-d). Not thread-safe; EMBEDDING_EXECUTOR serialises it."""

    def __init__(self, model_name: str = "titanet_small", *, device: str = "cuda") -> None:
        import torch
        from nemo.collections.asr.models import EncDecSpeakerLabelModel

        self._torch = torch
        self._model = EncDecSpeakerLabelModel.from_pretrained(
            model_name, map_location=device
        ).eval()
        started = time.monotonic()
        self.embed(np.zeros(SAMPLE_RATE, np.int16))
        logger.info(
            f"speaker embedder {model_name} loaded "
            f"(warm-up {time.monotonic() - started:.2f} s)"
        )

    def embed(self, pcm: np.ndarray) -> np.ndarray:
        torch = self._torch
        device = self._model.device
        signal = torch.from_numpy(pcm.astype(np.float32) / 32768.0)[None].to(device)
        with torch.inference_mode():
            _, embedding = self._model.forward(
                input_signal=signal,
                input_signal_length=torch.tensor([signal.shape[1]], device=device),
            )
        return embedding[0].float().cpu().numpy()


class EmbeddingWorker:
    """At most one embedding running and one pending per slot.

    A newer job for a slot replaces its pending one and keeps its place; the
    slot waiting longest goes first, so a TV that never stops talking cannot
    starve the customer's slot. Results reach ``on_result`` on the event loop.
    """

    def __init__(
        self,
        embedder: SpeakerEmbedder,
        on_result: Callable[[EmbeddingJob, np.ndarray, float], None],
        *,
        executor: Executor | None = None,
    ) -> None:
        self._embedder = embedder
        self._on_result = on_result
        self._executor = executor or EMBEDDING_EXECUTOR
        self._pending: dict[int, EmbeddingJob] = {}
        self._order: deque[int] = deque()
        self._task: asyncio.Task | None = None
        self.failed = False
        self.elapsed_ms: deque[float] = deque(maxlen=_MAX_TIMINGS)

    @property
    def pending(self) -> int:
        return len(self._pending)

    def submit(self, job: EmbeddingJob) -> None:
        if self.failed or getattr(self._embedder, "disabled", False):
            return
        if job.slot not in self._pending:
            self._order.append(job.slot)
        self._pending[job.slot] = job
        self._kick()

    def clear(self) -> None:
        self._pending.clear()
        self._order.clear()

    def _kick(self) -> None:
        if self._task is not None or not self._order:
            return
        job = self._pending.pop(self._order.popleft())
        self._task = asyncio.get_running_loop().create_task(self._run(job))

    async def _run(self, job: EmbeddingJob) -> None:
        try:
            started = time.monotonic()
            embedding = await asyncio.get_running_loop().run_in_executor(
                self._executor, self._embedder.embed, job.pcm
            )
            elapsed = (time.monotonic() - started) * 1000.0
            self.elapsed_ms.append(elapsed)
            self._on_result(job, embedding, elapsed)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - fusion runs on without voiceprints
            self.failed = True
            self._embedder.disabled = True
            self.clear()
            logger.exception("speaker embedder failed; fusion runs without voiceprints")
        finally:
            self._task = None
            if not self.failed:
                self._kick()

    async def close(self) -> None:
        self.clear()
        if self._task is not None:
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
            self._task = None


def resample_to_16k(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
    if sample_rate == SAMPLE_RATE:
        return pcm
    from scipy.signal import resample_poly

    g = math.gcd(SAMPLE_RATE, sample_rate)
    out = resample_poly(pcm.astype(np.float32), SAMPLE_RATE // g, sample_rate // g)
    return np.clip(out, -32768, 32767).astype(np.int16)


class BotVoiceprint:
    """Jarvis's own voice, embedded once per process from VieNeu's clean output.

    One VieNeu voice serves both languages, so one voiceprint covers the bot.
    """

    def __init__(self) -> None:
        self.embedding: np.ndarray | None = None
        self.failed = False
        self._chunks: list[np.ndarray] = []
        self._samples = 0
        self._taken = False

    def offer(self, audio: bytes, sample_rate: int, num_channels: int) -> np.ndarray | None:
        """Collect TTS audio; return 3 s of 16 kHz mono once, then None."""
        if self._taken or self.failed or num_channels != 1 or len(audio) % 2:
            return None
        pcm = resample_to_16k(np.frombuffer(audio, np.int16), sample_rate)
        self._chunks.append(pcm)
        self._samples += len(pcm)
        need = int(BOT_ENROLL_SECS * SAMPLE_RATE)
        if self._samples < need:
            return None
        self._taken = True
        joined = np.concatenate(self._chunks)[:need]
        self._chunks = []
        return joined


def bot_audio_sink(
    embedder: SpeakerEmbedder, bot: BotVoiceprint, executor: Executor | None = None
) -> Callable[[bytes, int, int], None]:
    """A VieNeu ``on_audio`` hook that embeds the bot's first 3 s, off the loop."""
    pool = executor or EMBEDDING_EXECUTOR

    def embed(pcm: np.ndarray) -> None:
        try:
            bot.embedding = unit(embedder.embed(pcm))
        except Exception:  # noqa: BLE001 - echo falls back to playback overlap
            bot.failed = True
            logger.exception("bot voiceprint unavailable; echo uses playback overlap")

    def sink(audio: bytes, sample_rate: int, num_channels: int) -> None:
        if getattr(embedder, "disabled", False):
            return
        pcm = bot.offer(audio, sample_rate, num_channels)
        if pcm is not None:
            pool.submit(embed, pcm)

    return sink
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_embedding.py -q`
Expected: PASS
Then (GPU host, network once): `.venv/bin/python -m pytest tests/server/test_speaker_titanet.py -q -s`
Expected: PASS and a printed `titanet_small 1 s embed: … ms` line. Record the number in the task report; if it fails with a model-name error, stop and report (the spec's model choice is then wrong).

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker_embedding.py tests/server/test_speaker_embedding.py tests/server/test_speaker_titanet.py
git commit -m "feat(voice): TitaNet speaker embeddings, bounded worker, bot voiceprint" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B8: SpeakerAudioProcessor integration

**Files:**
- Modify: `src/openjarvis/server/voice/speaker_audio.py`
- Modify: `src/openjarvis/server/voice/turn_detection.py` (only `SpeakerVerdictFrame` fields)
- Test: `tests/server/test_speaker_audio.py` (append)

**Interfaces:**
- Consumes: `FusionGate` (B5), `EmbeddingJob` (B3), `EmbeddingWorker`, `SpeakerEmbedder` (B7), `VisionAudioBridge.pin`, `.asd_age_ms`, `.protocol_version` (B6).
- Produces:
  - `SpeakerVerdictFrame(verdict, locked: bool = False, overlap_target: bool = False, source: str | None = None)`.
  - `SpeakerAudioProcessor(..., embedder: SpeakerEmbedder | None = None, tracker: Any | None = None)`; attribute `stt_masked_no_verdict: int`; `_remember_verdict(start, verdict, overlap=False, mask=False)`; `_mask_row(verdict) -> bool`; `_after_fusion_row(pcm_row, t, bot_speaking) -> None`; `_on_embedding(job, embedding, elapsed_ms) -> None`.

- [ ] **Step 1: Write the failing tests** (append to `tests/server/test_speaker_audio.py`)

```python
# ---------------------------------------------------------------------------
# Fusion: locked-customer masking, segments for embeddings, ASD pin sync.
# ---------------------------------------------------------------------------

import time as _time

from openjarvis.server.voice.speaker_identity import (
    EmbeddingJob,
    FusionGate,
    SlotIdentity,
)

_FUSION = SpeakerSettings(enabled=True, diarizer="sortformer", vision_faces=True,
                          vision_asd=True, identity="fusion")
_ROW = 1280  # one 80 ms diarizer row


def _fusion_gate(locked=True, faces=None, now=100.0):
    gate = FusionGate(_FUSION, faces or FaceTrackBuffer(), fsm_state=lambda: "active")
    if locked:
        gate.lock.observe_fsm("active", now - 1.0)
        for i in range(6):
            gate.lock.observe_row(now - 0.45 + i * 0.08, [0], anchor=7,
                                  anchor_asd_accept=True)
    return gate


def _fusion_processor(gate, **kwargs):
    processor = SpeakerAudioProcessor(
        diarizer=_FakeDiarizer([]), gate=gate,
        executor=ThreadPoolExecutor(max_workers=1), stt_delay_secs=0.5, **kwargs,
    )
    pushed = []

    async def push(frame, direction=FrameDirection.DOWNSTREAM):
        if isinstance(frame, SttAudioFrame):
            pushed.append(np.frombuffer(frame.audio, np.int16))

    processor.push_frame = push
    return processor, pushed


def _unverdicted(processor, arrival, level=1000):
    processor._stt_line.append((arrival, InputAudioRawFrame(
        audio=(np.ones(FRAME, np.int16) * level).tobytes(),
        sample_rate=16_000, num_channels=1)))


@pytest.mark.anyio
async def test_after_the_lock_audio_without_a_verdict_is_silenced():
    processor, pushed = _fusion_processor(_fusion_gate())
    _line(processor, [(1000, Verdict.ACCEPT, False)])
    _unverdicted(processor, 100.5)
    await processor._release_stt(200.0)
    assert [int(p[0]) for p in pushed] == [1000, 0]
    assert processor.stt_masked_no_verdict == 1


@pytest.mark.anyio
async def test_before_the_lock_audio_without_a_verdict_still_passes():
    processor, pushed = _fusion_processor(_fusion_gate(locked=False))
    _line(processor, [(1000, Verdict.ACCEPT, False)])
    _unverdicted(processor, 100.5)
    await processor._release_stt(200.0)
    assert [int(p[0]) for p in pushed] == [1000, 1000]


@pytest.mark.anyio
async def test_masked_rows_reach_stt_as_silence():
    processor, pushed = _fusion_processor(_fusion_gate())
    processor._remember_verdict(100.0, Verdict.UNCERTAIN, False, mask=True)
    _unverdicted(processor, 100.08)
    await processor._release_stt(200.0)
    assert [int(p[0]) for p in pushed] == [0]


@pytest.mark.parametrize("locked, overlap, visible, separator, masked", [
    (True, False, False, False, True),    # unconfirmed after the lock
    (True, True, True, False, False),     # target overlap, visibly speaking: the mix
    (True, True, False, True, False),     # target overlap, TSE enrolled
    (True, True, False, False, True),     # target overlap, no TSE, face still
    (False, False, False, False, False),  # before the lock: unchanged
])
def test_mask_row_policy(locked, overlap, visible, separator, masked):
    gate = _fusion_gate(locked=locked)
    kwargs = {}
    if separator:
        kwargs = {"separator": _FakeSeparator(),
                  "separator_executor": ThreadPoolExecutor(max_workers=1)}
    processor, _ = _fusion_processor(gate, **kwargs)
    if separator:
        processor._add_enrollment(np.ones(3 * 16000, np.int16))
    gate.target_overlap, gate.target_speaking_visibly = overlap, visible
    assert processor._mask_row(Verdict.UNCERTAIN) is masked
    assert processor._mask_row(Verdict.ACCEPT) is False


def test_after_the_lock_only_asd_confirmed_target_rows_enroll_tse():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(
        gate, separator=_FakeSeparator(),
        separator_executor=ThreadPoolExecutor(max_workers=1),
    )
    gate.last_source, gate.row_is_target = "asd", True
    assert processor._confirmed_customer()
    gate.last_source = "mar"
    assert not processor._confirmed_customer()
    gate.last_source, gate.row_is_target = "asd", False
    assert not processor._confirmed_customer()


class _Recorder:
    def __init__(self):
        self.jobs = []
        self.cleared = 0

    def submit(self, job):
        self.jobs.append(job)

    def clear(self):
        self.cleared += 1


def _row_pcm():
    return np.ones(_ROW, np.int16)


def test_solo_rows_become_one_target_confirmed_segment():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    for i in range(13):
        gate.row_voices, gate.row_asd_track = [0], 7
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    (job,) = recorder.jobs
    assert job.slot == 0 and job.target_confirmed and job.seconds >= 1.0
    assert job.epoch == gate.lock.epoch and job.segment_end == pytest.approx(101.96)


def test_bot_audio_and_overlap_rows_are_never_collected():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    for i in range(20):
        gate.row_voices = [0]
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, True)
        gate.row_voices = [0, 1]
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    assert recorder.jobs == []


def test_mostly_unconfirmed_rows_do_not_enroll():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    for i in range(13):
        gate.row_voices, gate.row_asd_track = [0], (7 if i < 5 else None)
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    assert recorder.jobs[0].target_confirmed is False


def test_a_new_epoch_drops_collected_audio():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    processor._embeddings = recorder = _Recorder()
    gate.row_voices = [0]
    processor._after_fusion_row(_row_pcm(), 101.0, False)
    gate.lock.observe_fsm("cleanup", 101.1)
    processor._after_fusion_row(_row_pcm(), 101.2, False)
    assert recorder.cleared == 1
    assert processor._segments[0].seconds == pytest.approx(0.08)


def test_pin_follows_the_lock_once_per_change():
    class Bridge:
        def __init__(self):
            self.pins = []

        def pin(self, track):
            self.pins.append(track)

    gate = _fusion_gate()
    bridge = Bridge()
    processor, _ = _fusion_processor(gate, vision_audio=bridge)
    processor._after_fusion_row(_row_pcm(), 100.0, False)
    processor._after_fusion_row(_row_pcm(), 100.1, False)
    processor._after_fusion_row(_row_pcm(), 101.5, False)  # target unseen > 0.5 s
    assert bridge.pins == [7, None]


def test_embedding_results_update_the_lock_and_stale_ones_do_not():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    job = EmbeddingJob(epoch=gate.lock.epoch, slot=0, segment_end=101.0, seconds=2.0,
                       pcm=np.zeros(16000, np.int16), target_confirmed=True)
    processor._on_embedding(job, np.ones(4, np.float32), 12.0)
    assert gate.lock.voice_ready
    gate.lock.observe_fsm("cleanup", 102.0)
    processor._on_embedding(job, np.ones(4, np.float32), 12.0)
    assert gate.lock.voiceprint.seconds == 0.0


@pytest.mark.anyio
async def test_without_an_embedder_rows_still_flow_and_nothing_is_submitted():
    gate = _fusion_gate()
    processor, _ = _fusion_processor(gate)
    assert processor._embeddings is None
    gate.row_voices = [0]
    for i in range(20):
        processor._after_fusion_row(_row_pcm(), 101.0 + i * 0.08, False)
    assert processor._segments == {}


@pytest.mark.anyio
async def test_locked_customer_marks_an_unknown_voice_for_masking():
    # _run queues all frames at once, so per-frame audio levels cannot be
    # matched to rows here; the mask decision per row is what this pins.
    # The explicit-time tests above cover mask -> silent STT audio.
    now = _time.time()
    faces = FaceTrackBuffer()
    for k in range(-2, 25):
        faces.add({"event": "faces", "ts": now + k * 0.1, "tracks": [
            {"track_id": 7, "distance_m": 0.8, "mouth_activity": 0.0}]})
    gate = _fusion_gate(faces=faces, now=now)
    gate.lock.voiceprint.enroll(np.ones(4, np.float32), 2.0)
    gate.lock.slots[0] = SlotIdentity(voice_sim=0.9)
    rows = [(0.9, 0.0, 0.0, 0.0)] * 6 + [(0.0, 0.0, 0.0, 0.9)] * 6
    diarizer = _FakeDiarizer(rows)
    processor = SpeakerAudioProcessor(diarizer=diarizer, gate=gate,
                                      executor=ThreadPoolExecutor(max_workers=1),
                                      stt_delay_secs=0.05)
    collector = await _run(processor, [_loud() for _ in range(12)],
                           until=lambda: diarizer.pushed == 12)
    assert collector.verdicts == [Verdict.ACCEPT] * 6 + [Verdict.UNCERTAIN] * 6
    assert processor._masked_values == [False] * 6 + [True] * 6
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_audio.py -q`
Expected: FAIL: `_remember_verdict()` takes no `mask`; `_mask_row`, `_after_fusion_row`, `_on_embedding`, `stt_masked_no_verdict` missing.

- [ ] **Step 3: Implement**

`turn_detection.py` — replace the `SpeakerVerdictFrame` body:

```python
@dataclass
class SpeakerVerdictFrame(SystemFrame):
    """The gate's verdict for one diarizer frame (80 ms) of speech."""

    verdict: Verdict = Verdict.UNCERTAIN
    locked: bool = False  # a customer is locked (fusion)
    overlap_target: bool = False  # the customer talks over another voice
    source: str | None = None  # asd | mar | voice | audio
```

`speaker_audio.py` — imports: add `from typing import Any, Protocol` (replace the `Protocol` import), and:

```python
from openjarvis.server.voice.speaker_embedding import EmbeddingWorker, SpeakerEmbedder
from openjarvis.server.voice.speaker_identity import EmbeddingJob, FusionGate
```

Below `_MAX_QUEUED_CHUNKS = 2` add:

```python
# Fusion: a slot's solo speech is embedded in segments of embed_segment_secs,
# keeping at most this much while it accumulates.
SEGMENT_MAX_SECS = 3.0
# A segment enrolls the customer's voiceprint only when this share of its rows
# was ASD-confirmed on the locked face.
TARGET_CONFIRMED_FRACTION = 0.8


def _percentile(values, pct: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, int(len(ordered) * pct / 100))], 1)


class _Segment:
    """One slot's solo speech since its last embedding, bounded to 3 s."""

    def __init__(self) -> None:
        self.rows: deque[tuple[np.ndarray, int | None]] = deque()
        self.end = 0.0

    @property
    def seconds(self) -> float:
        return sum(len(pcm) for pcm, _ in self.rows) / SAMPLE_RATE

    def add(self, pcm: np.ndarray, asd_track: int | None, t: float) -> None:
        self.rows.append((pcm.copy(), asd_track))
        self.end = t
        while self.seconds > SEGMENT_MAX_SECS:
            self.rows.popleft()

    def job(self, slot: int, lock) -> EmbeddingJob:
        tracks = [track for _, track in self.rows]
        confirmed = (
            lock.locked
            and lock.target_track is not None
            and sum(track == lock.target_track for track in tracks)
            >= TARGET_CONFIRMED_FRACTION * len(tracks)
        )
        return EmbeddingJob(
            epoch=lock.epoch,
            slot=slot,
            segment_end=self.end,
            seconds=self.seconds,
            pcm=np.concatenate([pcm for pcm, _ in self.rows]),
            target_confirmed=confirmed,
        )
```

In `SpeakerAudioProcessor.__init__`, add the two keyword parameters before `**kwargs`:

```python
        embedder: SpeakerEmbedder | None = None,
        tracker: Any | None = None,
```

and at the end of `__init__`:

```python
        self._fusion = gate if isinstance(gate, FusionGate) else None
        self._tracker = tracker
        self._segments: dict[int, _Segment] = {}
        self._epoch = self._fusion.lock.epoch if self._fusion is not None else 0
        self._pinned: int | None = None
        self._masked_values: list[bool] = []
        self.stt_masked_no_verdict = 0
        self._embeddings = (
            EmbeddingWorker(embedder, self._on_embedding)
            if self._fusion is not None
            and embedder is not None
            and not getattr(embedder, "disabled", False)
            else None
        )
```

In `_diarize`, replace the whole `for i, row in enumerate(probs):` loop with:

```python
                for i, row in enumerate(probs):
                    target = self._gate.target
                    # Wall-clock time of this frame, to line it up with Vision.
                    t = ended - (len(probs) - 1 - i) * step
                    if self._asd_enabled:
                        verdict = self._gate.frame(
                            row,
                            bot_speaking=bot_speaking,
                            t=t,
                            asd_stream_id=stream_id,
                        )
                    else:
                        verdict = self._gate.frame(row, bot_speaking=bot_speaking, t=t)
                    if self._stt_delay:
                        self._remember_verdict(
                            t - step, verdict, self._gate.overlap,
                            mask=self._mask_row(verdict),
                        )
                    if (
                        self._separator is not None
                        and verdict is Verdict.ACCEPT
                        and not self._gate.overlap
                        and self._confirmed_customer()
                    ):
                        enrollment = pcm[i * row_samples : (i + 1) * row_samples]
                        self._add_enrollment(enrollment)
                    if self._fusion is not None:
                        self._after_fusion_row(
                            pcm[i * row_samples : (i + 1) * row_samples],
                            t,
                            bot_speaking,
                        )
                    if verdict is not None:
                        evidence = (
                            getattr(self._gate, "last_evidence_detail", None)
                            or self._gate.last_evidence
                        )
                        logger.debug(
                            f"{self}: speaker frame t={t:.2f} verdict={verdict.value} "
                            f"bot={bot_speaking} overlap={self._gate.overlap} "
                            f"evidence={evidence}"
                        )
                    if self._gate.target != target:
                        logger.info(
                            f"{self}: speaker target slot {target} -> "
                            f"{self._gate.target}"
                        )
                    if verdict is not None:
                        fusion = self._fusion
                        await self.push_frame(
                            SpeakerVerdictFrame(
                                verdict=verdict,
                                locked=fusion is not None and fusion.locked,
                                overlap_target=fusion is not None
                                and fusion.target_overlap,
                                source=fusion.last_source if fusion else None,
                            )
                        )
```

At the top of `_confirmed_customer` add:

```python
        if self._fusion is not None and self._fusion.locked:
            # Locked: only the customer's own slot, confirmed by ASD, enrolls TSE.
            return self._fusion.last_source == "asd" and self._fusion.row_is_target
```

Replace `_remember_verdict` and `_silenced`:

```python
    def _remember_verdict(
        self,
        start: float,
        verdict: Verdict | None,
        overlap: bool = False,
        mask: bool = False,
    ) -> None:
        self._verdict_starts.append(start)
        self._verdict_values.append(verdict)
        self._overlap_values.append(overlap)
        self._masked_values.append(mask)
        if len(self._verdict_starts) > 512:  # ~40 s of frames
            del self._verdict_starts[:256], self._verdict_values[:256]
            del self._overlap_values[:256], self._masked_values[:256]

    def _silenced(self, t: float) -> bool:
        """Rejected speech, sound the diarizer heard no voice in, or -- once a
        customer is locked -- anything not confirmed as them.

        The 2026-09-26 live test: a quiet phone video tripped the VAD but not
        Sortformer, and Gemini transcribed it into turns with no speaker
        evidence. Before a lock, audio the diarizer has not reached yet still
        goes through; after it, audio with no verdict by release time is a
        dropped diarizer chunk and stays out (fail closed).
        """
        i = self._frame_at(t)
        if i is None:
            if self._fusion is not None and self._fusion.locked:
                self.stt_masked_no_verdict += 1
                return True
            return False
        return self._masked_values[i] or self._verdict_values[i] in (
            Verdict.REJECT,
            None,
        )
```

Add after `_silenced`:

```python
    def _mask_row(self, verdict: Verdict | None) -> bool:
        """After the lock, UNCERTAIN speech stays out of STT, except the
        customer talking over another voice when TSE can extract them or their
        face visibly speaks."""
        gate = self._fusion
        if gate is None or not gate.locked or verdict is not Verdict.UNCERTAIN:
            return False
        if gate.target_overlap:
            can_separate = (
                self._separator is not None
                and self._enrolled >= self._separator.enroll_samples
            )
            return not (can_separate or gate.target_speaking_visibly)
        return True

    def _after_fusion_row(self, pcm_row: np.ndarray, t: float, bot_speaking: bool) -> None:
        """Per row: log lock events, keep Vision's ASD on the customer, and
        gather solo speech for embeddings."""
        gate = self._fusion
        lock = gate.lock
        if lock.epoch != self._epoch:
            self._epoch = lock.epoch
            self._segments.clear()
            if self._embeddings is not None:
                self._embeddings.clear()
        for event in lock.drain_events():
            logger.info(f"{self}: speaker_lock {event.fields()}")
        if self._vision_audio is not None and hasattr(self._vision_audio, "pin"):
            want = lock.desired_pin(t)
            if want != self._pinned:
                self._pinned = want
                self._vision_audio.pin(want)
        if self._embeddings is None:
            return
        voices = gate.row_voices
        if len(voices) != 1 or bot_speaking or gate.target_overlap:
            return
        slot = voices[0]
        segment = self._segments.setdefault(slot, _Segment())
        segment.add(pcm_row, gate.row_asd_track, t)
        if segment.seconds >= gate.settings.embed_segment_secs:
            self._embeddings.submit(segment.job(slot, lock))
            del self._segments[slot]

    def _on_embedding(
        self, job: EmbeddingJob, embedding: np.ndarray, elapsed_ms: float
    ) -> None:
        gate = self._fusion
        if gate is None or not gate.lock.observe_embedding(job, embedding):
            return
        ident = gate.lock.slots[job.slot]
        fmt = lambda v: "none" if v is None else f"{v:.2f}"  # noqa: E731
        logger.info(
            f"{self}: speaker_identity slot={job.slot} "
            f"label={gate.lock.label(job.slot).value} "
            f"voice_sim={fmt(ident.voice_sim)} bot_sim={fmt(ident.bot_sim)} "
            f"seg_s={job.seconds:.2f} embed_ms={elapsed_ms:.0f} "
            f"pending={self._embeddings.pending if self._embeddings else 0}"
        )
```

In `_stop`, after the existing ASD summary block and before `if self._vision_audio is not None:`, add:

```python
        if self._fusion is not None and not getattr(self, "_fusion_reported", False):
            self._fusion_reported = True
            lock = self._fusion.lock
            ages = list(getattr(self._vision_audio, "asd_age_ms", ()) or ())
            embeds = list(self._embeddings.elapsed_ms) if self._embeddings else []
            logger.info(
                f"{self}: speaker_fusion session locks={lock.lock_count} "
                f"lock_after_s={lock.last_locked_after_s} "
                f"rebinds={dict(lock.rebinds)} "
                f"asd_protocol=v{getattr(self._vision_audio, 'protocol_version', None)} "
                f"asd_age_ms_p50={_percentile(ages, 50)} "
                f"asd_age_ms_p95={_percentile(ages, 95)} "
                f"embed_ms_p50={_percentile(embeds, 50)} "
                f"embed_ms_p95={_percentile(embeds, 95)} "
                f"stt_masked_no_verdict_frames={self.stt_masked_no_verdict} "
                f"echo_rejects={self._fusion.echo_rejects} "
                f"bargein_blocked={getattr(self._tracker, 'bargein_blocked', 0)}"
            )
        if self._embeddings is not None:
            await self._embeddings.close()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_speaker_audio.py tests/server/test_speaker_tse.py tests/server/test_voice_turn_authority.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/speaker_audio.py src/openjarvis/server/voice/turn_detection.py tests/server/test_speaker_audio.py
git commit -m "feat(voice): fusion masking, embedding segments and ASD pin in the speaker processor" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B9: Turn start and barge-in after the lock

**Files:**
- Modify: `src/openjarvis/server/voice/turn_detection.py` (`TargetSpeakerTurnStartStrategy`)
- Modify: `src/openjarvis/server/voice/speaker.py` (`SpeakerTracker`)
- Test: `tests/server/test_voice_turn_authority.py`, `tests/server/test_speaker.py` (append)

**Interfaces:**
- Consumes: `SpeakerVerdictFrame.locked/overlap_target/source` (B8).
- Produces: `SpeakerTracker.record(verdict, source: str | None = None)`, `SpeakerTracker.source_counts() -> dict[str, int]`, `SpeakerTracker.bargein_blocked: int`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_voice_turn_authority.py`:

```python
def _locked_frame(verdict, overlap=False):
    return SpeakerVerdictFrame(verdict=verdict, locked=True, overlap_target=overlap,
                               source="asd" if verdict is Verdict.ACCEPT else None)


@pytest.mark.anyio
async def test_after_the_lock_a_vad_onset_alone_opens_nothing():
    strategy, tracker, events = await _strategy()
    await strategy.process_frame(_locked_frame(Verdict.UNCERTAIN))
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    assert events == []
    await strategy.process_frame(_locked_frame(Verdict.UNCERTAIN))
    assert events == []
    await strategy.process_frame(_locked_frame(Verdict.ACCEPT))
    assert events[-1] == "start:True"


@pytest.mark.anyio
async def test_after_the_lock_overlap_never_barges_in_and_is_counted():
    strategy, tracker, events = await _strategy(frames=3)
    await strategy.process_frame(_locked_frame(Verdict.UNCERTAIN))
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    for _ in range(5):
        await strategy.process_frame(_locked_frame(Verdict.UNCERTAIN, overlap=True))
    assert events == []
    assert tracker.bargein_blocked == 1


@pytest.mark.anyio
async def test_after_the_lock_three_accepts_barge_in():
    strategy, tracker, events = await _strategy(frames=3)
    await strategy.process_frame(_locked_frame(Verdict.UNCERTAIN))
    await strategy.process_frame(BotStartedSpeakingFrame())
    await strategy.process_frame(VADUserStartedSpeakingFrame())
    for _ in range(3):
        await strategy.process_frame(_locked_frame(Verdict.ACCEPT))
    assert events[-1] == "start:True"
    assert tracker.source_counts() == {"asd": 3}
```

Append to `tests/server/test_speaker.py`:

```python
def test_tracker_counts_sources_per_span():
    tracker = SpeakerTracker(SpeakerSettings(enabled=True))
    tracker.record(A, "asd")
    tracker.record(A, "voice")
    tracker.record(R)
    assert tracker.source_counts() == {"asd": 1, "voice": 1}
    tracker.begin_span()
    assert tracker.source_counts() == {}
    assert tracker.bargein_blocked == 0
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_voice_turn_authority.py tests/server/test_speaker.py -q`
Expected: FAIL: a VAD onset opens a locked turn; `source_counts`/`bargein_blocked` missing.

- [ ] **Step 3: Implement**

`speaker.py`, `SpeakerTracker.__init__`: add

```python
        self._sources: Counter[str] = Counter()
        self.bargein_blocked = 0
```

Replace `record` and `begin_span`, and add `source_counts`:

```python
    def record(self, verdict: Verdict, source: str | None = None) -> None:
        self.has_evidence = True
        self._counts[verdict] += 1
        if source is not None:
            self._sources[source] += 1

    def begin_span(self) -> None:
        """Speech started outside a turn: its frames start a fresh count."""
        self._counts.clear()
        self._sources.clear()

    def source_counts(self) -> dict[str, int]:
        """What confirmed this span's frames (asd, mar, voice, audio), for logs."""
        return dict(self._sources)
```

`turn_detection.py`, `TargetSpeakerTurnStartStrategy.__init__`: add

```python
        self._locked = False
        self._last_verdict: Verdict | None = None
        self._last_overlap = False
        self._blocked_logged = False
```

In `process_frame`, in the `VADUserStartedSpeakingFrame` branch, after `self._accept_run = 0` add `self._last_verdict, self._blocked_logged = None, False`. Replace the `SpeakerVerdictFrame` branch with:

```python
        elif isinstance(frame, SpeakerVerdictFrame):
            self._tracker.record(frame.verdict, frame.source)
            self._locked = frame.locked
            self._last_verdict, self._last_overlap = frame.verdict, frame.overlap_target
            self._accept_run = (
                self._accept_run + 1 if frame.verdict is Verdict.ACCEPT else 0
            )
            await self._maybe_start()
```

Replace `_maybe_start` down to (not including) its reset comment with:

```python
    async def _maybe_start(self) -> None:
        if self._turn_open or not self._user_speaking:
            return
        interrupting = self._bot_speaking or time.monotonic() < self._reply_pending_until
        if interrupting:
            if (
                self._tracker.has_evidence
                and self._accept_run < self._bargein_accept_frames
            ):
                self._note_blocked()
                return
        elif self._locked:
            # A customer is locked: only their confirmed voice opens a turn.
            if self._accept_run < 1:
                return
        elif self._tracker.span_verdict() is Verdict.REJECT:
            return
        if interrupting and self._locked:
            logger.info(f"{self}: bargein allowed=true accept_run={self._accept_run}")
```

(keep the existing comment block, `trigger_reset_aggregation`, `begin_turn`, `trigger_user_turn_started` lines that follow). Add:

```python
    def _note_blocked(self) -> None:
        """Log once per speech span why a locked session's speech did not barge in."""
        if not self._locked or self._blocked_logged or self._last_verdict is None:
            return
        if self._last_overlap:
            reason = "overlap"
        elif self._last_verdict is Verdict.REJECT:
            reason = "not_target"
        elif self._last_verdict is Verdict.UNCERTAIN:
            reason = "uncertain"
        else:
            return  # an ACCEPT run still building
        self._blocked_logged = True
        self._tracker.bargein_blocked += 1
        logger.info(f"{self}: bargein blocked reason={reason}")
```

In `close_turn`, change the log to:

```python
        logger.info(
            f"{self}: speaker turn verdict={verdict.value} "
            f"frames={self._tracker.frame_counts()} "
            f"sources={self._tracker.source_counts()} locked={self._locked}"
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_voice_turn_authority.py tests/server/test_speaker.py tests/server/test_speaker_audio.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/turn_detection.py src/openjarvis/server/voice/speaker.py tests/server/test_voice_turn_authority.py tests/server/test_speaker.py
git commit -m "feat(voice): after the lock only the customer's confirmed voice opens turns" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

### Task B10: Wiring, preset, full suite

**Files:**
- Modify: `src/openjarvis/server/voice/tts.py` (`on_audio`)
- Modify: `src/openjarvis/server/voice/pipeline.py` (`build_voice_pipeline`)
- Modify: `src/openjarvis/server/voice/routes.py` (`_embedder`, `_BOT_VOICEPRINT`, warm-up, session wiring)
- Modify: `configs/openjarvis/examples/ordering-kiosk-mcp.toml` (`[voice.speaker]`)
- Test: `tests/server/test_voice_turn_authority.py`, `tests/server/test_voice_llm.py` (append)

**Interfaces:**
- Consumes: everything above.
- Produces: `VieNeuTTSService(renderer, *, sample_rate, on_audio: Callable[[bytes, int, int], None] | None = None)`; `build_voice_pipeline(..., embedder=None, bot_voiceprint=None)`; `routes._embedder() -> Any | None`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/server/test_voice_turn_authority.py`:

```python
_FUSION_SPEAKER = SpeakerSettings(
    enabled=True, diarizer="sortformer", vision_faces=True, vision_asd=True,
    identity="fusion",
)


def _built_speaker_processor(speaker, **extra):
    from pipecat.processors.frame_processor import FrameProcessor

    from openjarvis.server.voice.pipeline import build_voice_pipeline
    from openjarvis.server.voice.speaker import FaceTrackBuffer

    stt = FrameProcessor()
    build_voice_pipeline(
        connection=MagicMock(), binding=MagicMock(), renderer=MagicMock(), stt=stt,
        speaker=speaker, diarizer=MagicMock(chunk_samples=3840, frame_secs=0.08),
        faces=FaceTrackBuffer(), **extra,
    )
    return stt._prev


def test_fusion_identity_builds_a_fusion_gate_on_the_kiosk_fsm(monkeypatch):
    import openjarvis.kiosk.runtime as runtime
    from openjarvis.server.voice.speaker_embedding import BotVoiceprint
    from openjarvis.server.voice.speaker_identity import FusionGate

    embedder = MagicMock(disabled=False)  # a bare MagicMock's .disabled is truthy
    processor = _built_speaker_processor(
        _FUSION_SPEAKER, embedder=embedder, bot_voiceprint=BotVoiceprint()
    )
    gate = processor._gate
    assert isinstance(gate, FusionGate)
    monkeypatch.setattr(runtime, "_current_state", "active")
    assert gate._fsm_state() == "active"
    assert processor._embeddings is not None
    assert processor._tracker is not None


def test_identity_none_keeps_the_audio_only_gate():
    from openjarvis.server.voice.speaker import AudioOnlyGate
    from openjarvis.server.voice.speaker_identity import FusionGate

    speaker = SpeakerSettings(enabled=True, diarizer="sortformer", vision_faces=True,
                              vision_asd=True)
    gate = _built_speaker_processor(speaker, embedder=MagicMock())._gate
    assert isinstance(gate, AudioOnlyGate) and not isinstance(gate, FusionGate)


@pytest.mark.anyio
async def test_embedder_load_failure_leaves_fusion_without_voiceprints(monkeypatch):
    import openjarvis.server.voice.routes as routes

    monkeypatch.setattr(routes, "load_speaker_settings", lambda: _FUSION_SPEAKER)
    monkeypatch.setattr(routes, "_EMBEDDER", None)
    monkeypatch.setattr(routes, "_EMBEDDER_FAILED", False)
    calls = []

    def boom(model_name):
        calls.append(model_name)
        raise RuntimeError("no NGC access")

    monkeypatch.setattr(routes, "TitaNetEmbedder", boom)
    assert await routes._embedder() is None
    assert await routes._embedder() is None
    assert calls == ["titanet_small"]
```

Append to `tests/server/test_voice_llm.py`:

```python
@pytest.mark.anyio
async def test_vieneu_hands_each_rendered_chunk_to_on_audio():
    class _Chunk:
        audio = b"\x01\x00" * 480
        sample_rate_hz = 48_000
        channels = 1

    class _Renderer:
        async def stream_tts(self, text):
            yield _Chunk()
            yield _Chunk()

    seen = []
    service = VieNeuTTSService(
        _Renderer(), sample_rate=48_000,
        on_audio=lambda audio, rate, channels: seen.append((len(audio), rate, channels)),
    )
    frames = [frame async for frame in service.run_tts("xin chào", "ctx-1")]
    assert len(frames) == 2
    assert seen == [(960, 48_000, 1), (960, 48_000, 1)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server/test_voice_turn_authority.py tests/server/test_voice_llm.py -q`
Expected: FAIL: `build_voice_pipeline()` has no `embedder`; `VieNeuTTSService` has no `on_audio`; `routes._embedder` missing.

- [ ] **Step 3: Implement**

`tts.py`: add `from collections.abc import AsyncGenerator, Callable` (replace the existing import); add the keyword parameter `on_audio: Callable[[bytes, int, int], None] | None = None,` after `sample_rate: int,` in `__init__`, store `self._on_audio = on_audio` after `self._renderer = renderer`, and in `run_tts` add before `yield TTSAudioRawFrame(`:

```python
            if self._on_audio is not None:
                # The bot's clean voice, for its voiceprint (fusion echo check).
                self._on_audio(chunk.audio, chunk.sample_rate_hz, chunk.channels)
```

`pipeline.py`: add `embedder: Any | None = None,` and `bot_voiceprint: Any | None = None,` to the `build_voice_pipeline` parameters (after `vision_audio`), document them in the docstring (`embedder is the speaker embedder for identity="fusion"; bot_voiceprint is the process's BotVoiceprint`), and replace the `speaker_audio = SpeakerAudioProcessor(...)` construction with:

```python
        fusion = speaker.identity == "fusion" and faces is not None
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
            **({"vision_audio": vision_audio} if vision_audio is not None else {}),
        )
```

and replace `VieNeuTTSService(renderer, sample_rate=VIENEU_SAMPLE_RATE_HZ),` in the `Pipeline([...])` list with `VieNeuTTSService(renderer, sample_rate=VIENEU_SAMPLE_RATE_HZ, on_audio=on_bot_audio),`, computing before the `Pipeline(`:

```python
    on_bot_audio = None
    if (
        speaker.identity == "fusion"
        and embedder is not None
        and bot_voiceprint is not None
        and bot_voiceprint.embedding is None
    ):
        from openjarvis.server.voice.speaker_embedding import bot_audio_sink

        on_bot_audio = bot_audio_sink(embedder, bot_voiceprint)
```

`routes.py`: add to the imports `from openjarvis.server.voice.speaker_embedding import BotVoiceprint, TitaNetEmbedder`. After the `_separator` function add:

```python
_EMBEDDER: Any | None = None
_EMBEDDER_FAILED = False
_EMBEDDER_LOCK = asyncio.Lock()
# The bot's own voice: one per process, filled from VieNeu's first 3 s.
_BOT_VOICEPRINT = BotVoiceprint()


async def _embedder() -> Any | None:
    """The process's speaker embedder for fusion, or None to run without
    voiceprints. Loaded once; a failure is logged once and remembered."""
    global _EMBEDDER, _EMBEDDER_FAILED
    settings = load_speaker_settings()
    if settings.identity != "fusion":
        return None
    async with _EMBEDDER_LOCK:
        if _EMBEDDER is None and not _EMBEDDER_FAILED:
            try:
                _EMBEDDER = await asyncio.to_thread(
                    TitaNetEmbedder, settings.embedder_model
                )
            except Exception:  # noqa: BLE001 - optional capability
                _EMBEDDER_FAILED = True
                logger.exception(
                    "speaker embedder unavailable; fusion runs without voiceprints"
                )
    return _EMBEDDER
```

In `warm_speaker_models`, add `await _embedder()` after `await _separator()`. In `start_pipeline`, add to the `build_voice_pipeline(` call:

```python
                embedder=await _embedder(),
                bot_voiceprint=_BOT_VOICEPRINT,
```

`configs/openjarvis/examples/ordering-kiosk-mcp.toml`, in `[voice.speaker]` after `separator = "tse"` add:

```toml
# Lock one customer per kiosk session (spec 2026-09-30): Light-ASD confirms a
# face and a diarizer slot together, a TitaNet voiceprint follows them when
# they look away, and nobody else's speech reaches STT, opens a turn or
# barges in. Thresholds are starting values, tuned from live-trial logs.
identity = "fusion"
```

- [ ] **Step 4: Run the whole voice and kiosk suites**

Run: `cd ~/Projects/jarvis/OpenJarvis && .venv/bin/python -m pytest tests/server tests/kiosk tests/test_asd_protocol.py -q`
Expected: PASS (GPU-only modules skip where CUDA/NeMo are missing).

Then confirm the preset loads: `cd ~/Projects/jarvis/OpenJarvis && OPENJARVIS_CONFIG=configs/openjarvis/examples/ordering-kiosk-mcp.toml .venv/bin/python -c "from openjarvis.server.voice.speaker import load_speaker_settings as l; s=l(); print(s.identity, s.voice_match)"`
Expected: `fusion 0.65`

- [ ] **Step 5: Run the vision suite once more (cross-repo contract)**

Run: `cd ~/Projects/jarvis && OpenJarvis/.venv/bin/python -m pytest vision/tests -q && diff vision/tests/fixtures/asd_protocol_v2.json OpenJarvis/tests/fixtures/asd_protocol_v2.json`
Expected: PASS and no diff output.

- [ ] **Step 6: Commit**

```bash
cd ~/Projects/jarvis/OpenJarvis
git add src/openjarvis/server/voice/tts.py src/openjarvis/server/voice/pipeline.py src/openjarvis/server/voice/routes.py configs/openjarvis/examples/ordering-kiosk-mcp.toml tests/server/test_voice_turn_authority.py tests/server/test_voice_llm.py
git commit -m "feat(voice): wire the fusion gate, TitaNet and bot voiceprint into the kiosk preset" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

## After the plan: live trial (operator, not part of execution)

Deploy order and scenarios are spec §9.3 and §10: Vision v2 on Dev and Prod first, then this backend; run S1–S7 with `identity = "none"` for the baseline, then `"fusion"`, and compare the `speaker_lock`, `speaker_identity`, turn, `bargein` and `speaker_fusion session` lines. Restarting the Prod stack (`scripts/launcher.sh start`) is the operator's call.
