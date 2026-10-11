# Agentic Immediate Response: kiểm chứng

Ngày 2026-10-09. Spec: `docs/superpowers/specs/2026-10-09-immediate-response-design.md`.
Plan: `docs/superpowers/plans/2026-10-09-immediate-response.md`.

## Kết luận

Code chạy đúng thiết kế. Text model viết kèm tool call được phát trước khi tool chạy, các vòng sau được phát theo thứ tự, và pipeline không phát đoạn nào hai lần. Toàn bộ test unit và tích hợp đều pass.

Tuy vậy, **tiêu chí độ trễ của spec chưa đạt.** Trong 18 lần chạy với model thật, model không viết câu nào kèm tool call, nên thời gian tới câu nói đầu tiên không đổi.

Nguyên nhân là prompt của merchant `configs/openjarvis/prompts/ordering-kiosk.md:7-9` (commit `733ba601`):

> "call the matching prepared tool immediately, with no spoken text before or during execution: no acknowledgement, confirmation, narration or explanation."

Model tuân thủ quy định này và đưa lời nói vào tham số `customer_message` của tool. Câu đó chỉ được phát sau khi tool thành công. Đổi quy định này là quyết định sản phẩm; báo cáo này không sửa prompt.

## Phạm vi và đầu vào

- Commit gốc `46139bb9`. Thay đổi chưa commit.
- Model `gpt-6-luna` qua Responses API (`reasoning.effort=high`). Bật `--intent-normalization`, giống cấu hình kiosk.
- Merchant giả (`OrderingFixture`): không gọi merchant thật, không tạo đơn hay thanh toán thật.
- Flow: `menu`, `add_shown`, `view_cart`, `checkout_draft`, `checkout_named`, `tables`. Mỗi flow lặp 3 lần, mỗi bản 18 lần chạy.
- Harness đã được sửa để chạy turn qua `agent_input` của voice, có `VOICE_SYSTEM_PROMPT`. Không so được với số liệu của `2026-10-02-ordering-voice-latency`.
- Hash của ba file đầu vào có thay đổi chưa commit của người khác (`ordering-kiosk.md`, `intent.py`, `display.py`) giống nhau ở cả hai lần chạy. Xem `inputs.txt`.
- Dữ liệu thô: `baseline.json`, `patched.json`.

## Kết quả (trung vị, giây)

| Chỉ số | Baseline | Patched |
|---|---:|---:|
| `time_to_first_speech_s` | 3.393 | 3.940 |
| `time_to_first_tool_s` | 2.797 | 3.207 |
| `time_to_final_response_s` | 3.393 | 3.940 |
| `model_calls` | 1 | 1 |
| `behavior_ok` | 18/18 | 18/18 |
| `checkout_done` | 6 | 6 |
| Lỗi | 0 | 0 |
| Số lần chạy có câu nói trước tool đầu tiên | 0 | 0 |
| Số lần chạy có đoạn bị phát trùng nguyên văn | 0 | 0 |

Theo từng flow (câu nói đầu / tool đầu / câu cuối / số lần gọi model, trung vị):

| Flow | Baseline | Patched |
|---|---|---|
| menu | 2.845 / 2.843 / 2.845 / 1 | 3.277 / 3.275 / 3.277 / 1 |
| add_shown | 3.048 / 3.047 / 3.048 / 1 | 3.725 / 3.724 / 3.725 / 1 |
| view_cart | 2.572 / 2.190 / 2.572 / 1 | 2.713 / 1.965 / 2.713 / 1 |
| checkout_draft | 5.362 / 5.358 / 5.362 / 1 | 3.860 / 3.857 / 3.860 / 1 |
| checkout_named | 7.525 / 3.802 / 7.525 / 2 | 9.104 / 3.965 / 9.104 / 2 |
| tables | 3.472 / 1.661 / 3.472 / 2 | 7.059 / 2.571 / 7.059 / 3 |

Chênh lệch giữa hai bản là dao động thời gian suy luận của model, không phải tác động của thay đổi. Cả hai bản đều có đúng một đoạn nói mỗi lần chạy, phát sau tool. Lần lặp thứ 2 và 3 của `tables` ở bản patched cần 3 lần gọi model thay vì 2. Nguyên nhân là lựa chọn của model, không liên quan tới đường code đã đổi, vì không có text nào được nhả trước tool.

## Đọc lại transcript

Đã đọc đủ 36 lần chạy.

- **Lặp về nghĩa:** 0. Mỗi lần chạy chỉ có một đoạn nói.
- **Nói về kết quả trước khi tool chạy:** 0. Không có đoạn nào trước tool.
- **Ghi nhận thêm (không thuộc thay đổi này):** ở `checkout_named`, giữa tool đầu tiên (khoảng 3–6 giây) và câu cuối (khoảng 6–9 giây) là 3–5 giây im lặng, vì model không viết gì ở vòng giữa. Đây đúng là khoảng thời gian tính năng nhắm tới, nhưng prompt merchant đang tắt nó.

## Test đã chạy

- `uv run pytest tests/agents/test_benchmark_speech_metrics.py tests/agents/test_intent_existing_skills.py -q`: 16 passed.
- `uv run pytest tests/agents/test_immediate_response.py -q`: RED 10 failed, 2 passed (trước khi sửa); GREEN 12 passed.
- `uv run pytest tests/agents/ -q -n auto`: 723 passed, 1 skipped.
- `uv run pytest tests/server/test_voice_llm.py -q`: RED 1 failed (`test_voice_speaks_every_tool_round_once_in_order`); GREEN 25 passed.
- `uv run pytest tests/server/ -q -n auto -k voice`: 183 passed, 1 skipped.
- `uv run pytest tests/server tests/kiosk tests/traces tests/tools tests/skills -q -n auto -m "not live and not cloud and not hub"`: 2327 passed, 14 skipped.
- Ruff check và format check trên mọi file đã sửa: không có lỗi.

Các test cũ đã sửa vì chúng khẳng định hành vi giữ text:
- `test_orchestrator.py`:
  - `test_orchestrator_stream_keeps_tool_call_arguments_out_of_the_text` → đổi tên thành `..._speaks_tool_round_prose_but_never_arguments`;
  - `test_streaming_result_message_is_spoken_after_the_tool`;
  - `test_streaming_display_text_finishes_after_the_display_call` → đổi tên thành `..._display_round_text_is_released_once_before_the_call`;
  - `test_streamed_response_items_reach_next_tool_round`.
- `test_ordering_voice_latency.py`: nhánh `streaming=True` của bốn test.
- `test_voice_llm.py`: `test_voice_speaks_the_first_tool_round_and_final_answer_only` → đổi tên thành `test_voice_speaks_every_tool_round_once_in_order`.

## Giới hạn

- Harness đo thời điểm có text, không đo âm thanh. Âm thanh đầu tiên ≈ `time_to_first_speech_s` cộng `ttfa_ms` của VieNeu.
- Tool của merchant giả trả về trong vài mili giây. Với HTTP thật, câu nói kèm tool sẽ che được nhiều độ trễ hơn.
- Chưa kiểm chứng trên phiên kiosk live.
- Ở mọi flow, phần lớn thời gian tới tool đầu tiên (khoảng 2–4 giây) là thời gian suy luận của model với `reasoning.effort=high`. Đây là đầu vào cho spec `reasoning_effort` đang hoãn.

## Lần chạy 3: nới prompt merchant (`patched-prompt`)

Theo quyết định của người dùng, câu ở `ordering-kiosk.md:7-9` được đổi thành: được phép viết *một* mệnh đề ngắn kèm tool call khi khách sẽ phải chờ trong im lặng, không nói hay ngụ ý kết quả, không lặp chi tiết đơn, không hỏi xác nhận; nếu không thì không viết gì. Phần thay đổi chưa commit của người khác (khoảng dòng 184–193) giữ nguyên. Hash mới của file nằm trong `inputs-prompt.txt`. Code gồm cả bản sửa theo review: không nhả text vòng tool nếu text có dạng JSON.

| Chỉ số (trung vị) | Baseline | Patched | Patched + prompt nới |
|---|---:|---:|---:|
| `time_to_first_speech_s` | 3.393 | 3.940 | 2.996 |
| `time_to_first_tool_s` | 2.797 | 3.207 | 2.609 |
| `model_calls` | 1 | 1 | 1 |
| `behavior_ok` | 18/18 | 18/18 | 18/18 |
| `checkout_done` | 6 | 6 | 6 |
| Lần chạy có câu nói trước tool | 0 | 0 | **0** |
| Lần chạy có đoạn trùng nguyên văn | 0 | 0 | 0 |

**Model vẫn không viết câu nào kèm tool call.** Mỗi lần chạy vẫn đúng một đoạn nói, phát sau tool. Mức giảm trung vị của `time_to_first_speech_s` đi cùng mức giảm của `time_to_first_tool_s`, nên đó là dao động thời gian suy luận, không phải do câu mở đầu.

Lý do: các tool đã chuẩn bị sẵn của kiosk nhận tham số `customer_message`, và prompt dặn model viết lời nói vào đó (`ordering-kiosk.md`, các mục về `customer_message` và `finish_turn`). Câu này chỉ được nói sau khi tool thành công, nên an toàn hơn. Khi chỉ được *cho phép* chứ không bị *yêu cầu*, model chọn đường này. Có câu nghe như lời mở đầu ("Mình sẽ tìm các món latte…") nhưng thực ra là `customer_message`, phát sau khi tool xong.

Chưa xác định được: harness không ghi text thô của vòng có tool. Vì vậy chưa loại trừ được khả năng model có viết text dạng JSON ở vòng tool và bị bộ chặn JSON giữ lại. Muốn biết chắc cần thêm một trường log và một lần chạy nữa.

Đọc lại transcript (18 lần chạy): 0 lần lặp về nghĩa, 0 lần nói về kết quả trước tool. `checkout_named` vẫn có khoảng im lặng dài giữa tool đầu (khoảng 3–7 giây) và câu nói (khoảng 10–13 giây).

## Lần chạy 4: khuyến khích câu mở đầu (`patched-encouraged`)

Theo quyết định của người dùng, `ordering-kiosk.md:7-13` được đổi lại thành: với call có thể mất thời gian (tìm menu, đọc bàn, tạo đơn hoặc thanh toán), viết thêm *một* mệnh đề văn bản thường ngắn kèm call. Câu này được nói trước khi tool chạy: không nói hay ngụ ý kết quả, không lặp chi tiết đơn, không hỏi xác nhận. `customer_message` vẫn mang kết quả đã xác nhận và không được lặp lại câu mở đầu. Hash mới nằm trong `inputs-prompt-encouraged.txt`. Harness có thêm `tool_round_texts`: text thô của các vòng có tool call, lấy trước khi bộ lọc quyết định có phát hay không.

| Chỉ số | Baseline | Patched + khuyến khích |
|---|---:|---:|
| Số lần chạy có câu nói trước tool đầu | 0/18 | **11/18** |
| Flow nhiều vòng: im lặng từ tool đầu tới câu nói đầu (trung vị) | 2.642 | **0.000** |
| Flow nhiều vòng: im lặng lớn nhất | 3.723 | 4.894 |
| `time_to_first_speech_s` (trung vị) | 3.393 | 3.876 |
| `time_to_first_tool_s` (trung vị) | 2.797 | 3.775 |
| `model_calls` (trung vị) | 1 | 1 |
| `behavior_ok` / `checkout_done` | 18/18 / 6 | 18/18 / 6 |
| Lần chạy có đoạn trùng nguyên văn | 0 | 0 |

- **Cách đọc:** với merchant giả, tool trả về gần như tức thì. Ở flow một vòng, câu mở đầu và `customer_message` đến gần như cùng lúc. Lợi ích thấy rõ ở flow nhiều vòng: `checkout_named` nói ở khoảng 3,5–4,9 giây thay vì chờ tới câu cuối (khoảng 7,8–10,4 giây); `tables` nói ngay lúc tool đầu chạy. Với HTTP thật, khoảng chờ được che sẽ lớn hơn.
- **`time_to_first_tool_s` tăng (2,80 → 3,78).** Một phần do dao động: `menu` có một lần mất 9,98 giây, `add_shown` một lần 10,34 giây. Một phần có thể do model sinh vài token văn bản trước function call. Cần lặp nhiều hơn để tách hai nguyên nhân. Đây là chi phí cần theo dõi.
- **Model tự quyết:** ở `add_shown` và `view_cart` (thao tác nhanh), `tool_round_texts` rỗng, tức model chọn im lặng.
- **Bộ chặn JSON:** không có text vòng tool nào dạng JSON, nên bộ chặn không giữ lại gì.

Đọc lại transcript (18 lần chạy):
- **Nói về kết quả trước tool:** 0. Các câu mở đầu đều ở dạng đang hoặc sẽ làm ("Mình kiểm tra tình trạng bàn…", "Mình tiến hành thanh toán…").
- **Lặp về nghĩa:** có ở cả 3/3 lần chạy `checkout_named`. Câu mở đầu của vòng 2 ("Mình hoàn tất tạo đơn và mã QR thanh toán nhé.") gần như trùng với `customer_message` ngay sau đó ("Đơn mang về đã được tạo và mã QR đang hiển thị…"): một câu báo trước, một câu xác nhận, cách nhau vài mili giây. Các flow khác không có.
- Câu mở đầu của vòng sau trong `checkout_named` được nói **giữa chừng** (khoảng 7,8–10,4 giây) thay vì bị bỏ, đúng yêu cầu "các vòng sau cũng được nói".

## Lần chạy 5: chống lặp nghĩa (`patched-norepeat`)

Theo quyết định của người dùng, thêm vào turn contract của `ordering-kiosk.md` một quy tắc chung: sau mệnh đề mở đầu đầu tiên của turn, chỉ viết thêm khi nó nói điều mới mà `customer_message` sắp tới sẽ không nói; không báo trước hành động mà message đó sắp xác nhận. Hash mới nằm trong `inputs-prompt-norepeat.txt`.

| Chỉ số | Baseline | Khuyến khích | Chống lặp |
|---|---:|---:|---:|
| Số lần chạy có câu nói trước tool đầu | 0/18 | 11/18 | **12/18** |
| Flow nhiều vòng: im lặng sau tool đầu (trung vị / lớn nhất, giây) | 2.642 / 3.723 | 0.000 / 4.894 | **0.000 / 2.126** |
| `time_to_first_speech_s` (trung vị) | 3.393 | 3.876 | 3.395 |
| `time_to_first_tool_s` (trung vị) | 2.797 | 3.775 | 3.294 |
| `model_calls` (trung vị) | 1 | 1 | 1 |
| `behavior_ok` / `checkout_done` | 18/18 / 6 | 18/18 / 6 | 18/18 / 6 |
| Lần chạy có đoạn trùng nguyên văn | 0 | 0 | 0 |

Đọc lại transcript (18 lần chạy):
- **Nói về kết quả trước tool:** 0.
- **Lặp về nghĩa ở `checkout_named`:** giảm từ 3/3 (báo trước "tạo đơn và mã QR" rồi xác nhận lại) xuống 2/3, và dạng lặp khác hẳn. Câu ở vòng 2 giờ là một lưu ý mới ("Lưu ý, mã QR không có nghĩa là khoản thanh toán đã hoàn tất."), nhưng `customer_message` ngay sau đó cũng nói giao dịch "đang chờ xác nhận", nên vẫn trùng một phần về trạng thái thanh toán. Lần lặp thứ 3 không có câu ở vòng 2.
- **Chất lượng câu mở đầu:** đa số cụ thể ("Mình kiểm tra tình trạng bàn hiện tại nhé."). Có hai câu chung chung: "Mình hỗ trợ bạn nhé." (`menu`) và "Mình đang xử lý yêu cầu của bạn." (`checkout_draft`).
- **Thời gian:** `time_to_first_tool_s` vẫn cao hơn baseline (2,80 → 3,29 giây), nhưng thấp hơn lần chạy 4. Chưa tách được dao động với chi phí do sinh token câu mở đầu.

## Việc cần người dùng quyết định

1. **Giữ prompt chống lặp** (lần chạy 5). Phần trùng còn lại liên quan trạng thái "chờ xác nhận thanh toán", vốn luôn có trong `customer_message` của bước thanh toán. Nếu muốn bỏ hẳn, có thể thêm một dòng riêng của merchant: câu mở đầu không nhắc tới trạng thái thanh toán. Sau đó chạy lại benchmark.
2. (Đã làm ở lần chạy 4.) Khuyến khích mạnh hơn trong prompt merchant.
3. **Giữ như hiện tại.** Đường code đã sẵn sàng; model tự chọn nói qua `customer_message` sau khi tool thành công, an toàn hơn nhưng không giảm độ trễ.
4. **Hoàn nguyên câu prompt** về bản cũ, vì việc nới không đổi hành vi.
5. Đòn bẩy độ trễ lớn hơn vẫn là thời gian suy luận trước tool đầu tiên (khoảng 2–4 giây, `reasoning.effort=high`). Đó là spec `reasoning_effort` đang hoãn.

## Kiểm thử tay trên kiosk live (18:32–18:38)

Stack chạy code và prompt của lần chạy 5. Trace ghi model là `deepseek-flash`. Kiosk gọi merchant thật.

**Câu mở đầu bị TTS giữ tới cuối lượt (đã sửa).** Ở lượt "Còn bạn nào trống thế?", Orchestrator nhả câu "Để mình kiểm tra bàn trống cho bạn nhé." lúc 18:35:23.71, ngay trước khi tool tra bàn chạy. Nhưng TTS chỉ bắt đầu tổng hợp câu đó lúc 18:35:30.06, khi lượt kết thúc.

- Nguyên nhân: `SimpleTextAggregator` của Pipecat chỉ cắt câu khi sau dấu câu có thêm một ký tự không phải khoảng trắng. Text tiếp theo chỉ tới sau khi tool chạy xong, nên câu nằm chờ.
- Benchmark không thấy lỗi này vì nó đo thời điểm `AgentTextDelta`, không đo tới TTS.
- Cách sửa:
  - `stream_agent` đánh dấu text frame ở mỗi `AgentToolStarted` bằng `FLUSH_TTS_METADATA`.
  - `VieNeuTTSService` flush bộ gom ngay sau frame đó, giống cách Pipecat tự flush ở `LLMFullResponseEndFrame`.
- Test `test_tool_round_text_reaches_tts_while_the_tool_still_runs`: RED trước khi sửa, GREEN sau khi sửa. `tests/server`, `tests/agents`, `tests/kiosk`: 1693 passed, 2 skipped.
- Chưa kiểm chứng lại trên kiosk live sau khi sửa.

**Các phát hiện khác, không do thay đổi này gây ra và chưa sửa:**
1. **Trả lời sai câu hỏi.** Lượt "Alo. Còn bạn nào trống thế?" có gọi skill tra bàn, nhưng sau đó hiển thị lại menu latte và nói `customer_message` của menu ("Mình đã lọc các món latte…"). Kết quả tra bàn không được nói ra. Lượt hỏi bàn lúc 18:37:38 thì trả lời đúng.
2. **Đã tạo đơn thật trên merchant.** Đơn `02759bef75` (2 cà phê sữa mang đi), đã khởi tạo thanh toán chuyển khoản, đang ở trạng thái chờ, lúc 18:35:50.
3. **Mất lượt "kẹp hotdog"** do lỗi media stream WebRTC; phiên voice tự kết nối lại lúc 18:36:43.
4. Câu mở đầu khi thanh toán ("Mình đang chuẩn bị hai cà phê sữa mang về cho bạn.") **lặp lại chi tiết đơn**, trái với quy định trong prompt.
5. Lỗi ASGI `websocket.send` sau khi đóng `/v1/agents/events` lúc mở phiên.
