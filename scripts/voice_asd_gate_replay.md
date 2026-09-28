# Offline speaker gate evidence replay

Run from this checkout:

```sh
.venv/bin/python scripts/voice_asd_gate_replay.py /path/evidence.jsonl --output /path/gate-report.json
```

Provide complete session history from before the first decision, including an
initial `stream` record (`null` when disabled), every subsequent stream reset and
original face/MAR event, and every labeled speaker decision. All timestamps must
share one timeline. The tool checks required fields; it cannot detect records
silently omitted by a recorder. Capture is a separate user-operated action.

Each JSONL line has `kind`:

* `stream`: `received_at`, `stream_id` (string or null).
* `faces`: `received_at`, `event` (original `faces` event, including `ts`,
  `tracks`, `track_id`, `distance_m`, `mouth_activity`, optional `asd`).
* `speaker`: `t` (audio interval end; interval is preceding 80 ms), `decided_at`,
  `stream_id`, `probs` (unchanged diarizer row), `bot_speaking` boolean,
  `customer_speaking` boolean ground truth.

Events received after the decision deadline cannot affect that decision. Two real
AudioOnlyGates and FaceTrackBuffers run with default policy thresholds, ASD off/on.
Missing face/MAR, diarizer, labels or initial stream history makes comparison
unavailable. UNKNOWN/UNCERTAIN is not ACCEPT. Reports include decisions, evidence,
false ACCEPTs, customer ACCEPT recall and raw anchor ASD-use fraction. The latter
is not the required live post-warm-up eligible-frame metric.

## Separate opt-in trials

Prepare four copies of the operator's configuration, with enhancer/ASD:
`none/false`, `rnnoise/false`, `none/true`, `rnnoise/true`. Do not change the active
speaker-test preset or start services. For either ASD trial:

```toml
[voice.speaker]
enabled = true
diarizer = "sortformer"
vision_faces = true
vision_asd = true
asd_accept_prob = 0.7
asd_reject_prob = 0.3
asd_wait_secs = 0.12
stt_mask = true
stt_mask_delay_secs = 0.7
```

Keep the operator's other settings. This branch removed `confirm_uncertain` by
prior user decision; do not reintroduce the obsolete field. Configure a kiosk
Vision client. In the corresponding Vision config set `asd_provider: light_asd`,
`asd_model_path: /home/robber/.cache/openjarvis/light_asd/light_asd.onnx`, and
`asd_audio_offset_secs` to a real calibration result for that enhancer mode.
No calibrated live value exists yet; synthetic +0.20 s is not a trial setting.

Promotion remains pending separate labeled calibration/held-out A/V and gate
history in each enhancer mode. Require >=50% effective eligible post-warm-up ASD
use, fewer false ACCEPTs on silent video/mismatch, <=2 percentage-point customer
ACCEPT recall loss, p95 barge-in <=700 ms and p50 turn-end regression <=100 ms.
Model replay cannot measure these live latency targets. Human listening/kiosk
acceptance is a separate user-operated step.
