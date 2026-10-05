# Local Target Audio Monitor — 05/10/2026

Thêm toggle **Listen to target audio** trong Audio monitor của Operator Console.
Monitor phát trên output local của máy backend, mặc định OFF cho mỗi voice session.
PCM được tap tại `OpenJarvisGeminiSTTService.run_stt`, ngay trước khi Pipecat gửi
bytes đó vào Gemini: sau gate/masking/TSE, gồm cả silence của những đoạn bị lọc.

## Implementation

- `target_audio_monitor.py`: worker riêng, buffer PCM tối đa 200 ms/32 frames,
  lock producer không chờ; bỏ frame cũ hoặc frame không thể enqueue ngay.
  Worker bỏ packet quá 200 ms, giới hạn pipe Linux 4096 bytes, timeout write 200 ms.
- `paplay` nhận PCM16 mono ở sample rate của STT. `pactl` chọn output mặc định
  qua Unix socket local; timeout query 500 ms. Chặn `auto_null`, pin sink đã chọn
  vào player. Không có PCM monitor được gửi tới Vision, browser hay Internet.
- Worker lỗi thì monitor tự tắt. OFF/cleanup hủy backlog và kill/reap player;
  control và cleanup chạy off-thread. Hủy STT finalization task trước khi chờ cleanup.
- Tái dùng `voice_set` và telemetry của console. Chỉ nhận boolean thật;
  toggle không nằm trong settings được Save. UI lấy trạng thái từ backend,
  xử lý pending/timeout, mất kết nối, dữ liệu stale, đổi phiên và tiếng Việt.
- Giữ các settings, ngưỡng, provider, model và thuật toán audio hiện có.

Playback là best-effort: mỗi frame phát giữ nguyên bytes của feed STT. Khi quá tải
(kể cả TSE nhả audio thành burst), monitor có thể bỏ frame để giữ backlog giới hạn;
STT vẫn nhận đầy đủ. Con số 200 ms là giới hạn queue Python, không phải tổng độ trễ
âm thanh nghe được; pipe/device và delay gate/TSE hiện có cũng tham gia.

## Benchmark source cuối

Script: [`benchmark_target_audio_monitor.py`](../../../scripts/benchmark_target_audio_monitor.py).
Raw samples, counters và SHA-256 của sáu file backend:
[`benchmark.json`](benchmark.json). Hash đã đối chiếu với source hiện tại.

Mỗi case 3 lượt × 500 frames, frame PCM mono 16 kHz/20 ms; pacing 20 ms.
Thứ tự case xoay vòng giữa các lượt. Dùng adapter Gemini thật và fixture final PCM
(target, silence, output TSE giả lập); thay session Gemini bằng receiver nội bộ.
Player normal/stalled là subprocess thật; stalled không đọc pipe. Missing device
ném lỗi ở factory. Không gọi cloud/model API. Benchmark cuối chạy sau regression tests.

| Case | Mẫu enqueue | Enqueue p99 (µs) | Audio → STT p99 toàn case (µs) | p99 khi enabled (µs) | Δ p99 toàn case so OFF (µs) |
|---|---:|---:|---:|---:|---:|
| OFF | 0 | — | 204.289 | — | — |
| ON | 1500 | 38.466 | 232.576 | 232.576 | +28.287 |
| Player nghẽn | 36 | 41.212 | 239.143 | 269.105 | +34.854 |
| Mất device | 3 | 15.321 | 201.361 | 146.961 | −2.928 |

**9/9 checks đạt:** enqueue p99 <100 µs, delta p99 audio → STT <1000 µs cho cả
toàn case và khoảng enabled. ON tăng khoảng **0.028 ms** tại điểm gửi STT.
Heartbeat event loop 5 ms: p99 OFF 0.785 ms, ON 0.800 ms (delta 0.015 ms).
**6000/6000 frames tới receiver STT**, đúng số lượng và SHA-256.

Case lỗi tự disable sớm: khoảng enabled chỉ có 36 và 3 mẫu, sau đó lần lượt có
1464 và 1497 mẫu recovery. p99 enabled của các case này chỉ mang tính mô tả,
chưa đủ kết luận tail latency thống kê. Delta âm ở missing device không chứng minh
tăng tốc. Benchmark đo overhead local và scheduling, chưa đo latency Gemini/Agent
qua mạng hay workload voice + camera + GPU thật.

## Output local thực tế

`pactl --server=unix:/run/user/1000/pulse/native get-default-sink` trả `auto_null`.
`list short sinks` chỉ có sink ảo PipeWire này. Có các node ALSA trong `/dev/snd`,
nhưng output mặc định của daemon hiện chưa trỏ tới output vật lý.

Smoke cuối dùng `_open_player` thật và gửi 25 frames silence qua adapter Gemini:
monitor báo **error/unavailable**, đúng guard `auto_null`; **25/25 frames vẫn tới STT**.
Chưa xác nhận nghe được âm thanh speaker/headphone. Cần output vật lý được chọn
trong cấu hình sound của máy backend để kiểm chứng phần nghe thực tế.

## Verification và review

- **392 backend voice/speaker tests passed, 1 skipped, 6 deselected** trên source
  cuối. Loại GPU/live/cloud/hub theo markers.
- **58 backend tests passed** trên source có guard cuối: monitor, console, STT,
  unheard-turn và capture. Bao gồm subprocess PCM thật, byte identity sau TSE/mask,
  queue contention/full/stale, stalled pipe, mất device, ON/OFF/retry/teardown,
  control không chặn event loop và regression thứ tự cancel finalization.
- **90 Vision presentation tests passed**, gồm bool control relay và rendered template.
  Lần chạy đầu từ OpenJarvis gặp collection error `No module named tests`;
  chạy lại từ Vision với PYTHONPATH đúng đã pass.
- **6 JS DOM tests passed**: active-session gate, bool ON/OFF, telemetry confirmation,
  device/command error, reconnect/new session, stale data và tiếng Việt.
- Ruff check trên các file backend/script/tests của feature: pass; format check ba
  file mới: pass. Vision test file có bốn E501 đã có ở HEAD, được đối chiếu bằng
  `git show`; các lỗi này không nằm trong hunk thêm mới. Diff whitespace check
  trong phạm vi feature ở cả hai repo: pass.
- [Chromium 154 screenshot](console-on.png): fixture offline với telemetry ON;
  kiểm tra bố cục toggle, không phải ảnh chụp voice session thật.
- Review độc lập theo `superpowers:requesting-code-review`: **Ready**,
  không có Critical/Important. Minor về sample-size của case lỗi đã được ghi rõ
  và tách enabled/recovery trong raw artifact.

Warnings khi pytest: google-auth/grpc FutureWarning và các deprecation có sẵn của
Pipecat/Starlette/SWIG. Không thay dependency để xử lý chúng trong feature này.

## Tái chạy

Từ OpenJarvis:

```sh
uv run --frozen pytest tests/server/test_speaker*.py tests/server/test_voice*.py tests/server/test_stt_capture.py tests/server/test_target_audio_monitor.py -m 'not nvidia and not live and not cloud and not hub' -q
uv run --frozen pytest tests/server/test_target_audio_monitor.py tests/server/test_speaker_console.py tests/server/test_voice_stt.py tests/server/test_voice_unheard_turn.py tests/server/test_stt_capture.py -q
uv run --frozen python scripts/benchmark_target_audio_monitor.py --hardware-smoke --output docs/verification/2026-10-05-target-audio-monitor/benchmark.json
```

Từ workspace cha:

```sh
node --test vision/tests/presentation/target_audio_monitor.test.cjs
```

Từ Vision:

```sh
PYTHONPATH=/home/metek/Projects/jarvis/vision:/home/metek/Projects/jarvis:/home/metek/Projects/jarvis/OpenJarvis/src uv run --project ../OpenJarvis --frozen pytest tests/presentation -q
```

Áp dụng code bằng restart backend rồi reload Operator Console. Toggle khả dụng
khi voice session và speaker console đang hoạt động; mỗi phiên mới bắt đầu OFF.
Scope hiện tại dùng Linux PulseAudio/PipeWire với `paplay`/`pactl` có sẵn.
Các thay đổi có trước trong workspace được giữ nguyên; không commit hoặc restart stack.
