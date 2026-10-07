# Target audio playback verification — 2026-10-07

## Problem and evidence

The local Target audio monitor dropped 1,154 frames during the live session
10:09:43–10:12:11. Its queue reached 200 ms while status remained `playing`.
The corresponding capture is `/home/metek/stt-capture/20261007-100942`;
36.8 seconds labelled `kept` or `kept_onset` matched raw PCM sample for sample.
Monitor drops increased during retained-audio intervals as well. The counter
does not identify the contents of each dropped frame.

The monitor allowed only 200 ms of queued PCM, less than a 240 ms diarizer
release. It also aged every packet from its enqueue time, so normal time spent
playing earlier PCM could make later packets in a TSE burst appear stale.

## Change

Allow bounded queued audio up to the TSE model's 8 s window, with a 512-packet
limit. Calculate each packet's expected playback time from the queued sample
count. Keep the existing 200 ms lateness grace and stalled-write timeout.
The STT producer still uses a nonblocking queue offer; audio processing,
speaker authority, turn detection and Gemini input are outside this change.

## Verification

- Regression tests were run before production edits: a 240 ms burst lost
  2/12 frames; a 4 s burst lost 190/200 frames. Both now deliver exact PCM
  in order while the test clock advances at the audio sample rate.
- Target monitor, speaker console, voice STT and capture tests: **61 passed**.
- Ruff lint and format checks on both changed Python files: passed.
- Local hardware playback with synthetic silence, 16 kHz mono int16:

| Case | Offered frames | Dropped frames | Final queued ms |
| --- | ---: | ---: | ---: |
| HEAD monitor, twenty 240 ms bursts | 240 | 40 | 0 |
| Patched monitor, same bursts | 240 | 0 | 0 |
| Patched monitor, one 4 s TSE burst | 200 | 0 | 0 |

Hardware results: `/tmp/jarvis-target-audio-hardware-20261007.json`.
Live telemetry: `/tmp/jarvis-target-audio-live-20261007.jsonl`.

## Full repository test result

Command: `env -u FORCE_COLOR PYTEST_XDIST_AUTO_NUM_WORKERS=2 uv run pytest tests/ -n auto -q --tb=line -p no:cacheprovider -m "not live and not cloud and not hub"`.

**8,860 passed, 44 skipped, 5 failed, 1 collection error**, in 223.29 s.
Log: `/tmp/jarvis-target-audio-full-tests-20261007.log`.

Failures already identified in the incoming handoff:

- `tests/cli/test_cli.py::TestStartupResilience::test_importing_cli_does_not_import_numpy`
- `tests/learning/test_device_selection.py::TestSelectTorchDevice::test_no_torch_returns_none`
- `tests/pearl/test_model_converter.py::test_converter_quantizes_linear_weights_and_writes_pearl_config`
- `tests/pearl/test_model_converter.py::test_converter_adds_gemma4_preprocessor_compat_file`
- Collection error in `tests/evals/comparison/test_export_to_table_gen_roundtrip.py`: missing `polars`.

Additional suite failure:
`tests/integration/test_browser_capability.py::test_discovery_produces_mcp_tool_adapters`.
It passed in isolation both with the HEAD monitor loaded in memory and with
the patched monitor. Its dependency on the full-suite environment remains
unresolved; no browser discovery code was changed.

## Runtime status and limits

The running backend was not restarted to load this patch. The hardware
comparison instantiated fresh monitor code in a separate process. No claim
is made that the running kiosk has already adopted the fix, or that retained
speech is correctly classified by the speaker gate. No customer audio was
uploaded for this verification.
