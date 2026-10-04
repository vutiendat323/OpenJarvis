# Audit và benchmark ordering voice latency

> **SUPERSEDED — 03/10/2026:** Báo cáo và benchmark này mô tả implementation trước khi bỏ các module hardcode merchant. Các số đo, 72/72 lượt và 489 tests dưới đây là bằng chứng lịch sử, không xác nhận source hiện tại. Xem [bản sửa runtime dùng chung](../2026-10-03-site-agnostic-runtime/report.md).

Thực hiện 02–03/10/2026. Phạm vi: prompt gọi món và runtime cần thiết để thực thi tool ngay, kết thúc lượt đúng, phát lời ngắn; không thay đổi tính năng AV Fusion/STT/UI trong đợt này.

## Kết quả

- Prompt giảm từ **1736 xuống 1152 từ** theo khoảng trắng (**33,6%**). Giữ đủ quy tắc merchant, speaker authority, draft/order integrity, idempotency, checkout và pending payment.
- Benchmark cuối: **72/72 lượt đúng hành vi**, mỗi lượt **1 model inference**, **0–1 tool call trực tiếp từ model**, **3–10 từ** được đưa ra speech stream. Tất cả 72 lượt <=10 từ. Không preamble; không gọi model để nói lại sau terminal tool.
- **489 tests passed, 1 skipped**, 8 dependency warnings; Ruff và `git diff --check` exit 0.
- Chỉ benchmark **OpenAI GPT‑5.6 Luna, GPT‑6 Luna, DeepSeek V4 Flash**. Không thêm hoặc sử dụng cấu hình `response_policy="kiosk_voice"`. Không giảm ngân sách token/reasoning hay đổi model đang phục vụ Prod.

## Audit trước chỉnh sửa và thay đổi

| Thành phần | Vấn đề đã xác định | Thay đổi |
| --- | --- | --- |
| `configs/openjarvis/prompts/ordering-kiosk.md` | Quy tắc dài/lặp, cho phép narration; chưa có hợp đồng lời nói 15 từ cho toàn lượt | Gom thành turn contract + route bằng state + draft + safety; gọi tool ngay, tối đa một câu hỏi, không đọc lại nội dung đang hiển thị |
| `agents/_stubs.py` + `cli/serve.py` | Có cả configured prompt và generic prompt builder; builder được ưu tiên nên prompt Kiosk có thể bị thay thế | Configured prompt được ưu tiên, vẫn ghép persona files; serve truyền `intelligence.max_tokens` đã cấu hình thay vì âm thầm dùng default 1024 |
| `agents/orchestrator.py` | Text có thể phát trước tool; nối thêm lời model vào runtime response; tables cần model round hai | Nhận diện ordering qua prepared checkout tool đang hoạt động; giữ text đến khi biết quyết định, bỏ preamble, phát đúng một response từ metadata đã xác minh; terminal kết thúc cả khi không có text |
| `tools/display.py` + add skill | Default `finish_turn` phụ thuộc action/open_cart, gây continuation hoặc kết thúc add trước checkout | Standalone mặc định true; compound dùng false rõ ràng; input default của add skill được runtime áp dụng |
| `kiosk/ordering_tools.py` | Named take-out buy + QR cần add rồi model quyết định checkout; baseline đôi lúc dừng ở draft | Prepared composition resolve names một lần rồi gọi nguyên add + guarded checkout; dùng đúng revision vừa tạo, bind nonce từ turn context khi model bỏ qua (không mint nonce), bind nonce từ turn context khi model bỏ qua (không mint nonce), kiểm tra nonce trước edit; lỗi dừng, không có đường POST/payment mới |
| `skills/tool_adapter.py` | Table read thành công vẫn cần model nói lại; thiếu verified table rows trong context | Runtime phát bàn available; cache theo conversation, tối đa 32 conversations, TTL 15 giây; checkout vẫn đọc dữ liệu mới |
| `kiosk/menu_search.py` | Luna đôi lúc gửi tên sản phẩm “Latte” vào categoryTerms, trả rỗng dù menu có sản phẩm | Trên menu vừa đọc và đã qua assertions: category không khớp category thật nhưng khớp tên/mô tả sản phẩm được chuyển sang itemTerms. Category thật và term không khớp đều giữ nguyên; không mở rộng rỗng thành full menu |
| `kiosk/voice_response.py` | Tool/menu/cart response dài; output model có thể lộ internal fields hoặc nói paid khi chỉ có pending | Câu runtime ngắn deterministic; lọc internal markers; chặn paid claim chưa có evidence; giữ câu hoàn chỉnh <=15 từ, không cắt amount/negation; chỉ giữ câu hỏi đầu tiên, không nối yêu cầu phụ; fallback không khẳng định kết quả ghi chưa xác minh |

Menu normalization nằm trong helper Kiosk; generic SkillExecutor chỉ cung cấp callback sau response assertions. Test hiện có cấm business field registry trong executor vẫn pass. Prepared composition nhận `customer_message` tùy chọn để tránh schema retry do model truyền nhầm, nhưng **bỏ qua hoàn toàn giá trị này**; runtime phát pending response đã xác minh.

### Safety được giữ và kiểm tra

- Endpoint/origin/branch fixed và request recipe cũ; chỉ prepared merchant data là nguồn sự thật. Không có raw HTTP/tool lookup bổ sung do model.
- Unconfirmed speaker không thể gọi composition để sửa giỏ/đặt/thanh toán; nonce hết hạn bị chặn trước mọi đọc/edit.
- Lookup/add atomic; ambiguous/unavailable names không tạo order. Checkout vẫn đọc fresh menu, kiểm tra giá, quantities/notes/type/table và order/payment evidence trước bill/QR.
- Composition dùng revision trong **kết quả add**, không lấy revision mới nhất có thể đã bị touch sửa. Test edit xen giữa add và checkout từ chối mọi POST.
- Scoped nonce không đòi model chép opaque state; explicit stale nonce và expired copied worker context bị chặn trước đọc/edit. Same-turn replay không tạo/pay lại. Sau write timeout, gọi lại composition vẫn chỉ có **một POST tổng cộng**; không publish QR.
- Sai giá, số lượng, payment amount/status đều bị chặn; pending không được nói thành paid. Không có thay thế món tự động hoặc request credentials/OTP.

## Phương pháp đo

Mỗi model chạy 8 flow × 3 lần cho trước và sau: **24 lượt/model/phiên bản, 144 lượt chính**. Dùng cùng model API, temperature 0.7, max_tokens 4096, reasoning do engine hiện có quản lý (không thay đổi). Mỗi turn có session mới, fixture và draft/menu seed như nhau. Ba process model chạy song song; các flow chạy theo cùng thứ tự trong mỗi process. Không warmup/randomization/cold-start separation.

Real model API đi qua `CloudEngine`; merchant là `FakeMerchant` nội bộ, không mở network đến TrendCoffee, không tạo đơn thật. Prepared manifests, display tools, ToolExecutor, checkout guard và payment evidence dùng implementation thật. Fake merchant cũng assert checkout claim trước mọi POST.

- **Model calls:** số generate/stream_full invocations.
- **Tool calls:** số tool do model gọi trực tiếp. `Internal` đếm tất cả TOOL_CALL_START, **bao gồm outer call** và các bước trong prepared tool; không phải số merchant HTTP request.
- **First tool:** thời gian từ bắt đầu agent turn đến TOOL_CALL_START đầu tiên; câu hỏi không dùng tool ghi “—”.
- **Final:** đến khi nhận AgentRunCompleted, chưa gồm TTS playback, STT, browser render hoặc merchant network thực tế.
- **Words:** text gửi vào AgentTextDelta tách theo khoảng trắng, tổng toàn lượt; chưa đo audio TTS. Phát âm số/giá có thể có số âm tiết khác.
- Số thời gian bên dưới là **median**, không phải p95/p99. N=3/flow không đủ kết luận tail latency hoặc significance.

`before-*` dùng source snapshot trước chỉnh sửa; các runtime files đã thay đổi trong đợt này khớp baseline commit `50a65d9b58d72480935c8a4825d80f0de501a5f9`. Baseline benchmark truyền ordering prompt trực tiếp, vì vậy **không đo lợi ích sửa serve/prompt-builder**. Benchmark cũng dùng 4096 cả hai phiên bản, vì vậy không tính việc sửa forwarding budget vào cải thiện timing.

Metric chính được đo lại trên source cuối, có checks exact item/quantity/order type/note/table và menu IDs, cùng tiêu chí cho trước và sau. Final timestamp lấy ngay khi AgentRunCompleted, trước các assertions benchmark. Hash source cuối và raw artifacts nằm trong source-manifest.json; output pytest trong verification.log.

## Tổng hợp trước → sau

Số liệu toàn bộ lượt, kể cả baseline không hoàn tất, dùng để mô tả workload. Không coi baseline dừng sớm ở draft là checkout nhanh thành công.

| Model | Model calls/turn, trung bình | Tool calls/turn, trung bình | First tool p50 (s) | Final p50 (s) | Words p50 | Đúng hành vi trước/sau |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| gpt-5.6-luna | 1.21 → 1.00 | 0.83 → 0.75 | 2.038 → 1.826 | 2.241 → 1.818 | 10.0 → 5.0 | 19/24 → 24/24 |
| gpt-6-luna | 1.33 → 1.00 | 0.88 → 0.75 | 1.808 → 1.773 | 2.177 → 1.752 | 10.0 → 5.0 | 23/24 → 24/24 |
| deepseek-v4-flash | 1.29 → 1.00 | 0.88 → 0.75 | 1.348 → 1.274 | 1.677 → 1.262 | 11.5 → 5.0 | 24/24 → 24/24 |

Với **chỉ các repeat mà baseline hoàn tất đúng**, so sánh cùng các case tương ứng:

| Model | Case tương ứng | Final p50 trước → sau (s) |
| --- | ---: | ---: |
| gpt-5.6-luna | 19 | 2.220 → 1.745 |
| gpt-6-luna | 23 | 2.290 → 1.772 |
| deepseek-v4-flash | 24 | 1.677 → 1.262 |

Baseline có 18 lượt >15 từ và 6 lượt preamble. Bản cuối có 0 lượt vượt cap và 0 lượt preamble. Words p50 sau là 5; chưa quy đổi thành ms TTS vì chưa synthesize audio.

## Chi tiết từng flow

Mỗi ô “trước → sau” lấy median của 3 repeats, trừ model/tool calls ghi range. Internal bao gồm outer tool call. Đúng hành vi được đối chiếu với state thực tế, không chỉ ToolResult.success.

### gpt-5.6-luna

| Flow | Model calls | Tool calls | Internal p50 | First tool (s) | Final (s) | Words | Đúng trước/sau |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tìm các món latte | 1 → 1 | 1 → 1 | 3 → 3 | 2.719 → 2.267 | 2.725 → 2.273 | 11.0 → 5.0 | 3/3 → 3/3 |
| Thêm 2 Latte đã hiển thị | 1 → 1 | 1 → 1 | 1 → 1 | 2.284 → 1.743 | 2.286 → 1.745 | 9.0 → 5.0 | 3/3 → 3/3 |
| Xem giỏ hàng | 1 → 1 | 1 → 1 | 1 → 1 | 1.235 → 1.419 | 1.237 → 1.421 | 9.0 → 5.0 | 3/3 → 3/3 |
| Thanh toán draft mang về | 1–2 → 1 | 1–2 → 1 | 8 → 7 | 2.107 → 1.976 | 3.533 → 1.986 | 8.0 → 7.0 | 1/3 → 3/3 |
| Mua 2 Yaourt dâu mang về + QR | 1 → 1 | 1 → 1 | 3 → 9 | 2.121 → 2.120 | 2.127 → 2.133 | 22.0 → 7.0 | 0/3 → 3/3 |
| Món đó: tham chiếu mơ hồ | 1 → 1 | 0 → 0 | 0 → 0 | — → — | 1.492 → 1.686 | 11.0 → 4.0 | 3/3 → 3/3 |
| Hỏi bàn trống | 2 → 1 | 1 → 1 | 2 → 2 | 1.381 → 1.479 | 3.076 → 1.482 | 5.0 → 3.0 | 3/3 → 3/3 |
| Checkout thiếu hình thức dùng | 1 → 1 | 0 → 0 | 0 → 0 | — → — | 2.040 → 2.256 | 22.0 → 8.0 | 3/3 → 3/3 |

### gpt-6-luna

| Flow | Model calls | Tool calls | Internal p50 | First tool (s) | Final (s) | Words | Đúng trước/sau |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tìm các món latte | 1 → 1 | 1 → 1 | 3 → 3 | 2.404 → 1.691 | 2.409 → 1.697 | 11.0 → 5.0 | 2/3 → 3/3 |
| Thêm 2 Latte đã hiển thị | 1–2 → 1 | 1 → 1 | 1 → 1 | 2.309 → 1.809 | 2.311 → 1.811 | 9.0 → 5.0 | 3/3 → 3/3 |
| Xem giỏ hàng | 1–2 → 1 | 1 → 1 | 1 → 1 | 1.229 → 1.503 | 1.308 → 1.505 | 9.0 → 5.0 | 3/3 → 3/3 |
| Thanh toán draft mang về | 1 → 1 | 1 → 1 | 7 → 7 | 1.649 → 1.783 | 1.660 → 1.794 | 7.0 → 7.0 | 3/3 → 3/3 |
| Mua 2 Yaourt dâu mang về + QR | 2 → 1 | 2 → 1 | 10 → 9 | 2.369 → 2.118 | 9.855 → 2.132 | 12.0 → 7.0 | 3/3 → 3/3 |
| Món đó: tham chiếu mơ hồ | 1 → 1 | 0 → 0 | 0 → 0 | — → — | 1.402 → 1.648 | 13.0 → 4.0 | 3/3 → 3/3 |
| Hỏi bàn trống | 2 → 1 | 1 → 1 | 2 → 2 | 1.240 → 1.365 | 2.349 → 1.368 | 7.0 → 3.0 | 3/3 → 3/3 |
| Checkout thiếu hình thức dùng | 1 → 1 | 0 → 0 | 0 → 0 | — → — | 2.064 → 1.731 | 20.0 → 8.0 | 3/3 → 3/3 |

### deepseek-v4-flash

| Flow | Model calls | Tool calls | Internal p50 | First tool (s) | Final (s) | Words | Đúng trước/sau |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Tìm các món latte | 1 → 1 | 1 → 1 | 3 → 3 | 1.315 → 1.258 | 1.327 → 1.265 | 11.0 → 5.0 | 3/3 → 3/3 |
| Thêm 2 Latte đã hiển thị | 1–2 → 1 | 1 → 1 | 1 → 1 | 1.509 → 1.420 | 1.574 → 1.422 | 9.0 → 5.0 | 3/3 → 3/3 |
| Xem giỏ hàng | 1 → 1 | 1 → 1 | 1 → 1 | 0.806 → 1.118 | 0.808 → 1.121 | 9.0 → 5.0 | 3/3 → 3/3 |
| Thanh toán draft mang về | 1 → 1 | 1 → 1 | 7 → 7 | 1.633 → 1.419 | 1.643 → 1.430 | 7.0 → 7.0 | 3/3 → 3/3 |
| Mua 2 Yaourt dâu mang về + QR | 2 → 1 | 2 → 1 | 10 → 9 | 2.493 → 1.443 | 4.817 → 1.455 | 11.0 → 7.0 | 3/3 → 3/3 |
| Món đó: tham chiếu mơ hồ | 1 → 1 | 0 → 0 | 0 → 0 | — → — | 1.387 → 1.087 | 19.0 → 4.0 | 3/3 → 3/3 |
| Hỏi bàn trống | 2 → 1 | 1 → 1 | 2 → 2 | 1.094 → 1.093 | 2.174 → 1.096 | 15.0 → 3.0 | 3/3 → 3/3 |
| Checkout thiếu hình thức dùng | 1 → 1 | 0 → 0 | 0 → 0 | — → — | 2.048 → 1.292 | 24.0 → 8.0 | 3/3 → 3/3 |

## Token và lời nói

| Model | Input tokens/turn p50 trước → sau | Completion tokens/turn p50 trước → sau |
| --- | ---: | ---: |
| gpt-5.6-luna | 4149.0 → 3512.0 | 93.5 → 88.5 |
| gpt-6-luna | 4157.5 → 3516.5 | 96.5 → 60.5 |
| deepseek-v4-flash | — → — | — → — |

DeepSeek streaming adapter không cung cấp usage trong lần đo này: “—” là **unavailable**, không phải zero tokens. Completion usage của Luna là usage provider, có thể gồm reasoning và tool arguments; không đồng nhất với số từ được phát ra.

Runtime responses điển hình: “Đã hiển thị 2 món.”, “Đã cập nhật giỏ hàng.”, “Giỏ hàng đã hiển thị.”, “QR sẵn sàng, đang chờ thanh toán.”, “Bàn trống: 1.”. Clarification mơ hồ: “Bạn chọn món nào?”; thiếu hình thức dùng: một câu hỏi mang đi/tại bàn.

## Behavior regressions và giới hạn

1. **Không thấy safety regression trong tests đã chạy và các lượt benchmark cuối.** Đây không phải chứng minh cho mọi utterance/provider output hoặc live merchant failure. Flow/iteration nào fail vẫn giữ trong raw và aggregate; không lựa lượt nhanh hoặc đúng để thay raw chính.
2. **Latency không giảm đồng đều.** Các flow sau tăng final median trong mẫu (thời gian toàn flow đều có ở bảng chi tiết):

   - gpt-5.6-luna / view_cart: 1.237 → 1.421 s.
   - gpt-5.6-luna / checkout_named: 2.127 → 2.133 s; baseline có lượt chưa hoàn tất.
   - gpt-5.6-luna / clarify: 1.492 → 1.686 s.
   - gpt-5.6-luna / missing_type: 2.040 → 2.256 s.
   - gpt-6-luna / view_cart: 1.308 → 1.505 s.
   - gpt-6-luna / checkout_draft: 1.660 → 1.794 s.
   - gpt-6-luna / clarify: 1.402 → 1.648 s.
   - deepseek-v4-flash / view_cart: 0.808 → 1.121 s.

   N=3/flow chưa phân định nguyên nhân hay significance. First tool có thể tăng dù final giảm nhờ bỏ inference thứ hai; không tuyên bố mọi flow đều nhanh hơn.
3. **Baseline và checkout dừng sớm:** số lượt không hoàn tất theo flow:

   - gpt-5.6-luna: checkout_draft 2/3, checkout_named 3/3.
   - gpt-6-luna: menu 1/3.
   - deepseek-v4-flash: không thấy.
4. **Lỗi bản trung gian đã sửa:** wrapper reject customer_message gây inference thêm; categoryTerms Latte gây kết quả rỗng; GPT‑5.6 một lượt chép sai nonce bị chặn trước mọi merchant write. Diagnostic records giữ trong diagnostics/. Tool ghép nay mặc định dùng **nonce của context hiện hữu**, không tạo/bỏ kiểm tra nonce; explicit stale nonce và expired copied worker context vẫn bị chặn trước đọc/edit.
5. **Thay đổi kết thúc lượt:** finish_turn mặc định true cho standalone edits/views; compound caller phải gửi false. Table read terminal; follow-up chọn bàn dùng verified context TTL 15 s. Tên bàn dài vượt cap được tóm tắt số bàn và hỏi một câu, full rows vẫn ở context. Các thay đổi này có regression tests.
6. **Serving/prompt priority và buffering:** configured prompt được ưu tiên hơn generic template; generic sections không thay ordering instructions. Persona files vẫn có và 8 tests persona persistent pass. Ordering no-tool speech được giữ đến hết generation để enforce cap/no preamble; agent khác giữ streaming behavior cũ.
7. **Prod/live voice chưa được đo:** không restart/deploy, không real orders/payment, không audio quality trial S1–S7. Operator vẫn cần live validation trước khi tuyên bố latency thoại Prod đạt acceptance criteria.

## Verification

```sh
PYTHONPATH=src .venv/bin/python -m pytest -q \
  tests/agents/test_ordering_voice_latency.py \
  tests/agents/test_system_prompt_builder_integration.py \
  tests/agents/test_persona_persistent.py \
  tests/agents/test_persona_persistent.py \
  tests/skills/test_bundled_skills.py tests/skills/test_skills.py \
  tests/skills/test_tool_adapter_v2.py tests/agents/test_orchestrator.py \
  tests/core/test_speaker_authority.py tests/agents/test_checkout_procedure.py \
  tests/tools/test_display.py tests/system/test_agent_construction.py \
  tests/cli/test_serve_system_prompt_fatal.py \
  tests/agents/test_kiosk_http_runtime_hardening.py \
  tests/server/test_conversation_scope.py
```

Kết quả: **489 passed, 1 skipped, 8 warnings in 5.67s**. Skip là opt-in live merchant test; không tạo đơn thật. Warnings là Starlette/AnyIO, SWIG/FastAPI deprecations và google-auth/grpc compatibility notice; có trong verification.log.

Ruff kiểm tra 13 files Python thay đổi/thêm thuộc đợt này: **All checks passed**. `git diff --check`: exit 0. Các thay đổi có sẵn ngoài phạm vi ở UI/STT/browser/launcher được giữ nguyên; không reset/clean/commit/push.

## Tái chạy benchmark

Chạy từ root OpenJarvis, dùng `.env`/credentials hiện có (script không in chúng):

```sh
PYTHONPATH=src .venv/bin/python scripts/benchmark_ordering_voice.py \
  --model gpt-6-luna --compositions --variant after --repetitions 3 \
  --output /tmp/ordering-after-gpt-6-luna.json
```

Thay model bằng `gpt-5.6-luna` hoặc `deepseek-v4-flash`. Chỉ chạy model API; merchant vẫn giả lập. Có thể lọc bằng `--flow checkout_named` để kiểm tra regression.

Baseline source có thể dựng lại riêng, không sửa working tree:

```sh
snapshot_dir="$(mktemp -d /tmp/openjarvis-ordering-baseline.XXXXXX)"
git archive 50a65d9b58d72480935c8a4825d80f0de501a5f9 src | tar -x -C "$snapshot_dir"
PYTHONPATH="$snapshot_dir/src" .venv/bin/python scripts/benchmark_ordering_voice.py \
  --model gpt-6-luna --variant before --repetitions 3 \
  --prompt docs/verification/2026-10-02-ordering-voice-latency/prompt-before.md \
  --skills-dir docs/verification/2026-10-02-ordering-voice-latency/skills-before \
  --output /tmp/ordering-before-gpt-6-luna.json
```

Raw chính: `before-{model}.json`, `after-{model}.json`; aggregate: `summary.json`; baseline prompt/skills: `prompt-before.md`, `skills-before/`; final source hashes: `source-manifest.json`. Mọi dữ liệu trong `diagnostics/` là lượt lỗi provider, fixture cũ hoặc bản trung gian, **không thuộc metric chính**.

