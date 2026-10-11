# Kết quả kiểm thử trực tiếp (2026-10-10, 11:15–11:29)

Nguồn: `.stack-logs/backend.log` và `~/.openjarvis/traces.db` (chỉ đọc), 27 lượt
nói, 6 phiên, backend khởi động lúc 11:11:22 với ba thay đổi trong
`agents/orchestrator.py`. Người dùng nói các bàn 1, 2, 9, 10, 31, 34, 35, 40, 41,
50, 51, 52, 58, 59, 60 (không dùng đúng bàn 56/57 trong kế hoạch).

## Sự cố ngoài kế hoạch: một đơn thật đã được tạo

11:29:19 `POST /orders/public` (type `at-table`, bàn 9) thành công, `status:
pending`, tổng 243.000 đ, sau đó `POST /payment/initiate/public`
(`bank-transfer`, mã thanh toán đang hiển thị). Đơn chưa trả tiền; cần huỷ phía
merchant. Giỏ chứa mọi món đã thêm trong các phiên trước vì giỏ giữ qua phiên.

Chuỗi sự kiện: STT nghe `'tại bàn số 9 và thêm hai cà phê đá dĩa.' 'vào'
'Thanh toán luôn.'` (11:28:39–43); món không có trong menu nên không có gì được
thêm; người dùng nói tiếp `'cà phê hạt dẻ'` (11:29:00); vòng 2 của deepseek-flash
gọi `skill_trendcoffee-checkout` (11:29:18). Không rõ "Thanh toán luôn" là lời
người dùng hay STT; câu đó nằm trong lịch sử hội thoại và model chạy tiếp.

## Ba thay đổi

| Thay đổi | Kết quả |
|---|---|
| `agent_round` hiện trong log | Có: 45 dòng, đủ `finish`, `completion_tokens`, `reasoning_chars`, `tool_calls` |
| Lý do chặn gửi cho model | 8 lần chặn trong 27 lượt, mỗi lượt tối đa 1 lần, vòng sau không còn bị chặn lặp. Hôm trước: 5–6 lần liên tiếp rồi hết `max_turns` |
| Hết `max_turns` không đọc câu tiếng Anh | Không kiểm chứng được trên máy thật: không có `agent_max_turns` nào. Chỉ có test đơn vị |

Không có lượt nào đọc "Maximum turns reached…". Chưa đủ để nói lỗi đã hết: mẫu
nhỏ, đầu vào mỗi lần khác nhau.

## Nguyên nhân lượt chậm của deepseek-flash (đã có bằng chứng)

4 vòng `finish=length`, `visible_chars=0`, không có tool call,
`reasoning_chars` 14.921–16.307: toàn bộ giới hạn token bị suy luận dùng hết.
Đó là các lượt 24,4 s, 26,6 s và 50,5 s (hai vòng liên tiếp). Cả 4 vòng nằm trong
11:16–11:19, với các câu STT rời rạc ("Dùng tạm 41.", "41 đi."). 16 vòng còn lại
của deepseek-flash không chạm giới hạn (suy luận 0,2–12,4k ký tự).

## Lượt chậm của gpt-6-luna (suy luận, chưa kiểm chứng)

Hai lượt 38,9 s và 42,3 s, mỗi lượt 4–5 vòng, mỗi vòng 350–1.800 token
completion dù chỉ nói khoảng 50 ký tự. `reasoning_chars=0` vì API không trả
suy luận, nên không đo được trực tiếp.

## Theo model (không kiểm soát đầu vào, chỉ để tham khảo)

| Model | Vòng | `length` | Lượt dài nhất |
|---|---|---|---|
| deepseek-flash | 20 | 4 | 50,5 s |
| gpt-5.6-luna | 12 | 0 | 14,6 s |
| gpt-6-luna | 13 | 0 | 42,3 s |

## Quan sát khác

- Bàn đã `reserved` (1, 2): món được thêm, bàn không được chọn, kiosk hỏi có dùng
  chung bàn không. `ordering-kiosk.md:162` nói không bao giờ nhận bàn reserved;
  chưa xác minh "dùng chung" có phải luồng chủ ý.
- Lượt gpt-6-luna 11:24:44 (42,3 s, bàn 59) chỉ nói "Bàn bạn chọn hiện còn trống",
  không xác nhận món hay bàn đã được ghi.
- STT là nguồn nhiễu chính: "thẻ bài", "thẻ vàng số 50", "số 50admin", "đá dĩa".
- 4 lần `Media stream error` đều ngay trước khi phiên mới mở lại trong 5–10 s,
  khớp với việc tải lại trang/đổi model; chưa xác minh.

## Sửa độ trễ DeepSeek: điều khiển suy luận (11:50)

Không tăng `max_tokens` (4096 giữ nguyên): suy luận chia chung ngân sách với câu trả lời
và tool call, tăng giới hạn chỉ kéo dài thời gian chờ ở vòng `finish=length`.

`CloudEngine` đọc `OPENJARVIS_DEEPSEEK_THINKING` mỗi lần gọi DeepSeek (cả đường
streaming của agent, đường blocking và stream văn bản):

| Giá trị | Request gửi đi |
|---|---|
| (không đặt) | không đổi, mặc định của provider (thinking bật, effort `high`) |
| `off` / `disabled` | `extra_body={"thinking": {"type": "disabled"}}` |
| `low` / `high` / `max` | `reasoning_effort=<giá trị>` |
| giá trị khác | `ValueError` ngay (không bỏ qua im lặng) |

Test: `tests/engine/test_cloud_deepseek_thinking.py` (12 test). Cách dùng:
`OPENJARVIS_DEEPSEEK_THINKING=low OPENJARVIS_MODEL=deepseek-flash scripts/launcher.sh restart --no-vision`.

Thăm dò thật (2026-10-10, 6 lần gọi `deepseek-flash`, prompt ngắn có 1 tool): API chấp nhận
cả ba chế độ, không lỗi 400. Prompt ngắn không tái hiện được vòng 16k ký tự suy luận
nên **chưa đo được mức giảm độ trễ**; cần chạy `scripts/benchmark_ordering_voice.py`
(prompt kiosk thật, merchant giả, tốn phí API) cho mặc định so với `low` và `off`,
đo cả độ chính xác chọn tool.

## Hạ effort của Luna (`gpt-6-luna`, `gpt-5.6-luna`)

Từ log 11:11–11:30 (24 lượt, từ lời cuối của khách đến câu nói đầu tiên của kiosk):

| Model | Lượt | Câu đầu tiên sau (giây), trung vị | Khoảng |
|---|---|---|---|
| deepseek-flash | 12 | 8,6 | 3,6–56,6 |
| gpt-5.6-luna | 6 | 7,1 | 4,2–8,0 |
| gpt-6-luna | 6 | **13,9** | 5,9–21,2 |

`OPENJARVIS_OPENAI_REASONING_EFFORT` (`low` / `medium` / `high`; không đặt = `high`)
được đọc ở cả đường streaming và blocking của Responses API. Người gọi vẫn không
hạ được effort (`reasoning_effort="low"` bị bỏ qua như cũ, các test hiện có giữ nguyên).
Test: `tests/engine/test_cloud_openai_effort.py` (9 test).

Thăm dò thật, prompt ngắn có 1 tool, 2 lần mỗi chế độ: `low` và `medium` đều được chấp nhận
cho cả hai model; gpt-6-luna mặc định 1,5–3,7 s, `low` 1,3–1,5 s, `medium` 1,8–1,9 s.
Prompt này không đại diện cho prompt kiosk thật, nên mức giảm trên các lượt 14–21 s và độ chính xác
chọn tool chưa được đo.

## Giảm độ trễ lần 2 (11:46): effort `low` mặc định + câu nói ngắn

Backend khởi động lại 11:46:23; `launcher.sh` đặt mặc định
`OPENJARVIS_OPENAI_REASONING_EFFORT=low` và `OPENJARVIS_DEEPSEEK_THINKING=low`
(kiểm tra trong `/proc/<pid>/environ`). Ghi đè từng lần chạy bằng biến cùng tên.

**Câu nói ngắn** (`ordering-kiosk.md`, `VOICE_SYSTEM_PROMPT`): xác nhận tối đa 6 từ, câu
hỏi tối đa 8 từ, cả câu trả lời tối đa 12 từ; không đọc tổng tiền; câu mở đầu chỉ cho tool
tạo đơn/thanh toán (tối đa 4 từ), không cho menu, bàn, sửa giỏ. Câu chữ do model viết nên
giới hạn là hướng dẫn, không bảo đảm; test `tests/agents/test_speech_brevity.py` chỉ giữ
cho ngân sách đã viết không bị đổi ngược.

**Không làm, có lý do:**
- *Nhả câu mở đầu sớm hơn*: Responses API (Luna) chỉ trả tool call lúc `response.completed`,
  không có tín hiệu sớm. Với DeepSeek chỉ tiết kiệm phần sinh tham số tool (ước tính dưới 1 s,
  chưa đo) và phải đổi đường flush TTS.
- *Đọc nhanh hơn*: `vieneu` v3turbo không có tham số tốc độ (`infer_stream` chỉ có
  temperature, top_k, top_p, max_new_frames, repetition_penalty). Artifact đã nghiệm thu có
  TTFA 349 ms, RTF 0,68, nên TTS không phải điểm nghẽn. Kéo giãn thời gian ở đầu ra sẽ đổi
  âm thanh đưa vào kiểm tra echo của giọng bot (`on_audio`).

## Kết quả sau khi hạ effort và rút ngắn câu (11:52–11:57, 8 lượt, 6 lượt có agent)

| Giờ | Model | STT | Thời gian | Câu kiosk nói |
|---|---|---|---|---|
| 11:54:38 | deepseek-flash | "Thêm cà phê sữa vào giỏ hàng dùng tại bàn số 51" | **29,7 s**, 7 vòng | "Xin lỗi, mình chưa thêm được món." (món **không** được thêm) |
| 11:55:43 | deepseek-flash | "mở menu" | 2,4 s | "Dạ menu đây ạ, bạn xem qua nhé." |
| 11:55:57 | deepseek-flash | "Dùng thẻ số 51" | 9,2 s, 2 vòng | "Đã chọn bàn 51." |
| 11:56:32 | gpt-6-luna | "Mở menu" | 2,9 s | "Menu đang mở, bạn chọn món nhé." |
| 11:56:48 | gpt-6-luna | "Thêm hai cà phê muối … bàn 30" | 8,7 s, 4 vòng | "Đã thêm món vào giỏ." / "Bàn 30 chưa được chọn; bạn muốn tiếp tục?" |
| 11:57:29 | gpt-6-luna | "Dùng tại bàn số 30" | 2,0 s | "Đã chọn bàn 30." |
| 11:57:42 | gpt-6-luna | "Thêm hai cà phê muối, thêm hai cà phê latte" | ~2,8 s | "Đã thêm vào giỏ." |

- `completion_tokens` mỗi vòng của gpt-6-luna: 25–127 (trước đây 350–1.800).
- `reasoning_chars` của deepseek-flash: trung vị 1,4k (10 vòng) so với 4,8k (20 vòng trước), không còn `finish=length`.
- Không có `agent_max_turns`, không có câu tiếng Anh.
- Lượt 29,7 s của DeepSeek: vòng 1 bị chặn vì gọi song song; vòng 2 đọc bàn với `_intent=NONE`
  nên `declared=NONE` khoá cả lượt; vòng 3, 4, 5 gọi add-to-cart đều bị chặn
  (`intent cannot change` / `target operation requires its declared intent`); vòng 6 trả lời
  văn xuôi bị từ chối; vòng 7 báo lỗi. Đây là lỗi luồng intent, không phải do effort.

## Sửa khoá intent (12:10)

Nguyên nhân lượt 29,7 s của deepseek-flash (11:54): lượt gộp bị từ chối trước khi mục tiêu
được ghi nhận (`IntentState.prepare` kiểm tra "một target mỗi lượt" trước `_declare`), nên
bước đọc bàn khai `NONE` trở thành mục tiêu của cả lượt và mọi lần gọi target sau đó bị chặn.

- `agents/intent.py`: ghi nhận mục tiêu mà model đã khai báo *trước* khi từ chối lô gọi song
  song. Mục tiêu này là khai báo của model lúc chưa có bằng chứng, không phải từ output của
  tool, nên không mở đường nâng cấp `NONE` thành đơn hàng.
- `agents/orchestrator.py`: lý do chặn nêu rõ mục tiêu cần giữ
  (`Keep _intent=ADD_TO_CART on every call this turn, prerequisite reads included; …`).
- Test: `tests/agents/test_intent_goal_registration.py` (5 test, 4 fail trước khi sửa).
- Giới hạn còn lại: nếu lệnh đầu tiên của lượt là một bước đọc đơn lẻ khai `NONE`, mục tiêu
  vẫn bị khoá (đó là chủ ý bảo mật); khách phải nói lại.

## Thử lại sau khi sửa khoá intent (12:09, 1 lượt, deepseek-flash)

"Thêm một bánh lạnh vào giỏ hàng ở bàn số 51" → 4 vòng, 18,8 s, cả món và bàn đều được ghi
("Đã thêm bánh lạnh và chọn bàn 51 nhé."). Vòng 1 bị chặn (gọi song song) nhưng lần này
`declared=ADD_TO_CART` được ghi nhận; vòng 2 đọc bàn, vòng 3 thêm món, vòng 4 chọn bàn đều qua.
Trước khi sửa: 29,7 s, 7 vòng, thất bại. Một lượt, chưa đủ để nói tỷ lệ.

Thời gian từng vòng: 3,6 / 1,6 / 1,5 / **11,2 s** (vòng chọn bàn, `reasoning_chars=8409` dù đã
`low`). Khách im lặng 20,4 s rồi mới nghe câu đầu tiên: không vòng nào có câu mở đầu, vì
lần sửa prompt lúc 11:46 chỉ giữ câu mở đầu cho tool tạo đơn/thanh toán. Đó là quyết định
sai cho lượt nhiều vòng; đã đổi lại: câu mở đầu tối đa 4 từ ở lời gọi tool đầu tiên của mỗi
lượt (`ordering-kiosk.md`, phần Turn contract), chưa chạy trên kiosk.

## `OPENJARVIS_DEEPSEEK_THINKING=off` (12:12–12:17, 10 lượt, deepseek-flash)

- Mọi vòng `reasoning_chars=0`: tắt suy luận có hiệu lực.
- Thời gian mỗi lượt: 1,3 / 1,8 / 3,4 / 3,5 / 3,7 / 4,0 / 4,2 / 5,6 / 6,8 / 10,0 s (trước: 18,8 s
  với `low`, tới 50 s với mặc định). Lượt 10,0 s: bàn 56 `reserved`, 6 vòng, 3 lần
  `display_cart` bị chặn sau khi đã thêm món; kiosk hỏi có dùng chung bàn không.
- **Câu mở đầu: 0/10 lượt.** Không vòng đầu nào có `visible_chars > 0` dù prompt yêu cầu.
  Không tách được do prompt hay do tắt suy luận, vì hai thay đổi xảy ra cùng lúc.
- **3 đơn thật được tạo** (đều `pending`, đã khởi tạo thanh toán chuyển khoản):
  `75b832f0e5` (12:14, bàn 51, 344.000 đ), `449b7a905b` (12:15, bàn 20, 379.000 đ),
  `6780468464` (12:16, bàn 50, 70.000 đ). Hai đơn đầu sau câu "Thanh toán ngay giúp tôi" và
  "thanh toán đi"; đơn thứ ba sau "Hai cà phê không sữa mang ra hàng." + "bàn số 50", không có từ
  thanh toán: prompt coi một yêu cầu mua đủ thông tin là `CHECKOUT_NOW`.

## Câu mở đầu theo ngữ cảnh qua `_progress` (12:30)

Yêu cầu: không câu cố định; câu mở đầu phải dựa trên yêu cầu của khách. Vì chỉ dặn trong prompt
thì model bỏ qua (0/10 lượt), câu mở đầu giờ là một tham số bắt buộc của mọi tool:

- `[agent] progress_speech = true` (mặc định `false`, chỉ bật trong `ordering-kiosk-mcp.toml`).
- `OrchestratorAgent` thêm `_progress` (string, required) vào schema của mọi tool; model tự viết
  từ yêu cầu hiện tại. Đường streaming nói câu `_progress` đầu tiên của lượt trước khi tool chạy,
  chỉ khi lượt chưa nói gì; các vòng sau bỏ qua. `_progress` bị bỏ khỏi tham số trước khi gọi tool
  (cả đường streaming lẫn blocking). Prompt kiosk quy định tối đa 4 từ tiếng Việt.
- Test: `tests/agents/test_progress_speech.py` (8 test, đều fail trước khi sửa).
- Thăm dò thật, schema có `_progress`: deepseek-flash (`off`) điền "Đang thêm bạc xỉu",
  "Đang thêm trà đào" (1,1–1,2 s); gpt-6-luna (`low`) điền "Thêm bạc xỉu vào giỏ",
  "Thêm trà đào vào giỏ" (2,3–2,4 s). Chưa chạy trên kiosk.

## Lượt thử 12:24–12:28 (backend 12:12:30, chưa có `_progress`)

Backend lúc đó khởi động trước khi `_progress` được viết (12:21), nên chỉ có câu mở đầu dặn trong
prompt. deepseek-flash (`off`): 0/4 lượt có câu mở đầu. gpt-6-luna (`low`): 4/5 lượt có tool
viết câu mở đầu ("Mình kiểm tra bàn nhé.", "Mình thêm vào giỏ nhé.", "Mình cập nhật giỏ nhé.",
"Mình ghi chú lại nhé."). Thêm 1 đơn thật: `fbeb7be025` (12:25:59, 220.000 đ, sau "thanh toán
đường này").

Restart 12:31:29 với `_progress` và mặc định mới trong launcher: Luna `medium`, DeepSeek `low`.

## `_progress` trên kiosk + Luna `medium` / DeepSeek `low` (12:49–12:55)

- **`_progress` hoạt động**: 10/10 lượt có tool đều nói câu mở đầu trước kết quả ("Đang kiểm tra
  món", "Đang kiểm tra bàn", "Đang cập nhật giỏ", "Đang thêm món", "Đang thanh toán lại", "Đang kiểm
  tra bàn và món"…), cả ba model. Một lượt lặp nghĩa: "Mở menu nhé" rồi "Mình mở menu cho bạn nhé."
- **gpt-6-luna `medium`**: câu đầu sau 2,3–6,9 s; lượt nhiều vòng 13,7–16,9 s; 73–594
  `completion_tokens`/vòng (với `low`: 2,0–2,9 s, lượt 4 vòng 8,7 s, 25–127 token/vòng).
- **gpt-5.6-luna `medium`**: câu đầu 3,2–8,0 s; lượt 5,7–21,9 s.
- **deepseek-flash `low`**: lượt 47,8 s; vòng 1 `finish=length` hai lần liên tiếp (16.348 và 15.179
  ký tự suy luận) trước khi gọi tool; câu mở đầu chỉ đến sau 45,6 s. Đầu vào mâu thuẫn ("thanh toán
  ngay" ×2 rồi "Chưa thanh toán"), gộp từ 3 lượt bị ngắt.
- DeepSeek không có mức `medium`: API chỉ có `low`/`high`/`max`, và `medium` được ánh xạ thành `high`.
- **3 đơn thật mới**: `dd55c4fa19` (12:49:59, 115.000 đ, không có từ thanh toán),
  `1333c8667e` (12:51:17, 184.000 đ), `335643548d` (12:53:44, 987.000 đ).

## DeepSeek `off` + `_progress` (12:59–13:01, 6 lượt)

| Giờ | Khách nói | Câu đầu sau | Kết thúc sau | Kiosk nói cuối | Đúng? |
|---|---|---|---|---|---|
| 12:59:31 | thêm hai cà phê, bàn 50 | 3,3 s | 5,0 s | Bàn 50 đang được đặt trước, chọn bàn khác? | đúng (bàn 50 nay `reserved`, có thể do đơn thử `6780468464` lúc 12:16) |
| 12:59:43 | bàn 52 | 1,5 s | 6,0 s | Cho mình biết rõ tên món cà phê | đúng |
| 12:59:56 | cà phê đen | 1,9 s | 7,1 s | Chưa thêm được món | **thất bại** |
| 13:00:18 | cà phê sữa, bàn 52 | 3,0 s | 5,2 s | Đã thêm cà phê sữa và chọn bàn 52 | đúng |
| 13:00:52 | tìm trà, lấy kết quả đầu, bàn 54 | 1,7 s | 11,2 s | Đã thêm trà … và đổi sang bàn 54 | **sai: bàn không đổi** |
| 13:01:32 | tăng số lượng trà, đổi bàn 55, thanh toán | 2,5 s | 11,9 s | Chưa thanh toán được | thất bại (không tạo đơn) |

- 12:59:56: model gọi `display_cart add` với món không có bằng chứng menu mới → `cart_item_unverified`;
  guard coi đó là "đã thử target" nên chặn cách đúng (`add-to-cart` skill) với
  `target already attempted; observe outcome, do not retry`.
- 13:00:52: món được thêm, nhưng hai lần `display_cart` chọn bàn bị chặn
  (`requested outcome already completed`, `intent cannot change`) và câu cuối vẫn nói đã đổi bàn.
  Cùng mẫu với 10:52 (DeepSeek) và 11:56 (Luna): sau khi thêm món, một lần đọc bàn làm
  `IntentState.continuing` về `False` (`observe` gán lại từ mọi kết quả), nên lệnh chọn bàn sau đó bị chặn.
- 13:01:32: lệnh đầu là sửa số lượng khai `NONE`, khoá lượt ở `NONE`, nên thanh toán bị chặn 3 lần.
  Đây là chủ ý bảo mật (`NONE` không được nâng thành thanh toán).

## Sửa lỗi 1 và 2 (13:06), restart 13:10:10

- `agents/intent.py` (`observe`): sau khi target đã xong, kết quả của một tool `read_only` không
  còn đặt lại `continuing`; chỉ các bước có tác động mới tiếp tục hoặc kết thúc lượt.
  `skills/trendcoffee-tables.toml` khai báo `[skill.metadata.openjarvis] read_only = true`.
  Test: `tests/agents/test_intent_continuation_reads.py` (4 test; 2 fail trước khi sửa, 2 giữ hành vi cũ).
- `tools/display.py` (`DisplayCartMemoryTool`): hai chỗ trả `cart_item_unverified` thêm
  `execution_started: False`, để guard cho phép thử lại qua skill thêm món.
  Test: `tests/tools/test_display_refusal_not_started.py` (2 test, đều fail trước khi sửa).
- Suite: 8991 pass; lỗi còn lại là lỗi môi trường đã biết (pearl ×2, numpy, cuda, polars).

## Effort chỉnh qua launcher, không qua biến môi trường (13:21)

- Mặc định khoá: **DeepSeek `low`, gpt-6-luna `low`**. Mỗi `start`/`restart` ghi lại
  `.stack-logs/effort.json` bằng các tuỳ chọn, hoặc mặc định nếu không có.
- Chỉnh khi đang chạy, không restart:
  `scripts/launcher.sh effort --deepseek-effort off --luna-effort medium`.
  `scripts/launcher.sh effort` không tham số: chỉ hiển thị; `status` cũng có dòng `effort`.
- Giá trị hợp lệ: DeepSeek `off|low|high|max`; Luna `low|medium|high`. Giá trị sai bị từ chối ngay
  (mã thoát 2) và không ghi file.
- Backend (`engine/effort.py`) đọc file ở mỗi lần gọi model (chỉ parse lại khi file đổi);
  file thắng biến `OPENJARVIS_DEEPSEEK_THINKING`/`OPENJARVIS_OPENAI_REASONING_EFFORT`, hai biến
  này chỉ còn là phương án dự phòng khi không có file.
- Test: `tests/engine/test_cloud_effort_file.py` (7), `tests/scripts/test_launcher_effort.py` (8).

## DeepSeek `low` trên kiosk (13:24–13:26) và Luna `low` (13:27–13:28)

- deepseek-flash `low`, 4 lượt có agent: "bàn 25" 13,4 s (4 vòng, thành công); lượt gộp nhiều yêu cầu
  ("hai cà phê dĩa, tăng đen lên 15, bàn 26 chứ không phải 25") **32 s**: vòng 1 `finish=length`
  với 15.829 ký tự suy luận, vòng tiếp theo vẫn 6.902 ký tự, không có câu `_progress`, rồi hỏi lại
  tên món; "cà phê hạt dẻ" 18 s (vòng 1: 7.613 ký tự suy luận).
- gpt-5.6-luna `low`: câu đầu 4,0–4,6 s, hoàn tất 5,6–11,6 s; không có `finish=length`.
- Không tạo đơn thật nào trong phiên này.
- 13:28 Luna: sửa số lượng sau khi thêm món bị chặn (`intent cannot change`) và kiosk nói đúng
  "chưa chỉnh số lượng và bàn 27"; lượt sau mới chỉnh xong.
- Đặt DeepSeek về `off` khi đang chạy (13:29): `scripts/launcher.sh effort --deepseek-effort off`.

## Giảm vòng thừa và mặc định DeepSeek `off` (13:50)

- **Mặc định launcher: DeepSeek `off`, Luna `low`** (đổi từ `low`/`low`): ở `low`, deepseek-flash có
  lượt 31,9 s (`finish=length`, 15.829 ký tự suy luận), 47,8 s, 17,9 s; ở `off` các lượt 1,3–10 s.
  Đổi lại một lệnh: `scripts/launcher.sh effort --deepseek-effort low`.
- **Không bỏ phí lần đọc trong lô gọi song song** (`IntentState.prepare(defer_targets=True)`,
  `orchestrator._collect_function_tool_results`): khi một vòng có `[target, đọc bàn]`, phần đọc
  (tool khai báo `read_only`) chạy ngay trong vòng đó và chỉ target bị hoãn sang vòng sau
  ("intent_target_deferred … gọi target riêng"). Trước đây cả lô bị từ chối và lần đọc lặp lại
  một vòng sau (3–6 s). Lô có tiền đề *không* `read_only` vẫn bị từ chối cả lô, giữ nguyên hợp
  đồng cũ (`test_unknown_intent_and_target_batch_dispatch_nothing`).
- Test: `tests/agents/test_intent_deferred_target.py` (6 test). Suite: 9011 pass; 9 fail + 1 lỗi
  thu thập: môi trường (pearl ×2, numpy, cuda, polars) và 5 test kiosk/browser chỉ fail khi chạy
  song song (pass khi chạy riêng).

## Đường thoát `finish=length` và mặc định lại `low` (13:41)

- **Đường thoát** (`orchestrator._run_stream_in_turn`, `engine.reasoning_off_options`): khi một vòng kết
  thúc `finish=length` mà không có chữ nào và không có tool call, vòng đó được chạy lại **một lần**
  với suy luận tắt (`extra_body.thinking.type=disabled`) thay vì thúc "Continue from where you left
  off" và suy luận tiếp. Suy luận ở lại tắt đến hết lượt. Log: `agent_reasoning_exhausted … retry=reasoning_off`.
  Chỉ DeepSeek báo có công tắc này; engine khác giữ hành vi cũ; vòng đã có chữ vẫn dùng nhắc tiếp.
- Lô gọi song song chỉ hoãn target khi tiền đề `read_only` (13:50, mục trước).
- Mặc định launcher trở lại **DeepSeek `low`, Luna `low`** vì lượt xấu nhất nay có cận trên
  (vòng suy luận hết ngân sách → chạy lại không suy luận). Dùng `off` bằng
  `scripts/launcher.sh effort --deepseek-effort off`.
- Test: `tests/agents/test_reasoning_exhausted_retry.py` (5), `tests/engine/test_reasoning_off_options.py` (7).
  Suite: 9022 pass; 10 fail + 1 lỗi thu thập là môi trường (pearl ×2, numpy, cuda, polars) và 5 test
  kiosk/browser chỉ fail khi chạy song song (pass khi chạy riêng).

## Trần thời gian cho suy luận (14:05)

Phiên 13:41–13:50 (deepseek-flash `low` + đường thoát `finish=length`): lượt "thanh toán ngay + thêm
hai cà phê sữa + bàn 55" (13:49:54) bị kẹt ở vòng thứ hai hơn 12 s không có chữ nào cho tới khi khách
đóng phiên. Đường thoát `finish=length` chỉ chạy sau khi hết 4096 token (~20 s), quá muộn.

- `[agent] reasoning_deadline_seconds = 8.0` (mặc định core `0` = tắt): nếu một vòng chỉ có suy luận
  quá ngưỡng này mà chưa có chữ hay tool call, huỷ luồng và chạy lại vòng với suy luận tắt (sticky
  hết lượt). Chỉ bật khi engine báo có công tắc (`reasoning_off_options`: DeepSeek). Luna không bị ảnh
  hưởng (Responses API không phát chunk suy luận). Log: `agent_reasoning_exhausted … reason=reasoning_deadline`.
- Test: `tests/agents/test_reasoning_deadline.py` (5).
- Wrapper engine (`multi`, `guardrails`, `instrumented`) chịu được engine không có phương thức này
  (2 test orchestrator hiện có đã fail ở lần đầu).

Các lượt khác trong phiên 13:41–13:50: gpt-6-luna `low` có "món + bàn" 3 vòng, câu đầu sau 3,5 s;
nhưng bốn lượt liền nhau bị `agent_final_rejected` (trả lời văn xuôi thay vì JSON) và cần thêm một
vòng 5–8 s mỗi lượt; lượt 13:45:47 ("thành năm", đổi số lượng) khai `CHECKOUT_NOW` và gọi checkout
từ một câu "thanh toán" nằm trong các mảnh STT trước đó (checkout thất bại, không tạo đơn).
Đơn thật mới: `98404de130` (13:46:47, 55.000 đ, bàn 25, sau "Cho tôi đặt tại bàn số 25").


## Background actions (live)

### Chuẩn bị nghiệm thu Task 7 — 2026-10-11

Trạng thái: **SẴN SÀNG KIỂM THỬ; chưa nghiệm thu trực tiếp**.

- Stack khởi động 11:16:15 +07, backend PID 221079; Vision 221075 và frontend 221151. Cả ba health check đạt. Backend mới hơn các thay đổi nguồn của tính năng, gồm presentation.py (02:03:14 ngày 11/10).
- Model mặc định gpt-6-luna, effort Luna low/DeepSeek low; cần giữ nguyên model thực tế trong ba TC.
- Kiểm thử hồi quy mới: 63 passed, 2 dependency warnings, 10.61 s; lệnh: .venv/bin/python -m pytest tests/agents/test_background_scenarios.py tests/agents/test_action_ledger.py tests/server/test_voice_background.py tests/server/test_voice_lifecycle.py -q -p no:cacheprovider.
- [Kịch bản và tiêu chí nghiệm thu](task-7-manual-acceptance-2026-10-11.md). TC3 bỏ bàn 57 theo P3 để không tạo đơn thật.
- TC1/TC2/TC3: **CHƯA CHẠY**; transcript, log theo run/call, giỏ cuối và xác nhận nghe được còn chờ người kiểm thử. Không đánh dấu DONE bằng kết quả tự động.

### Kết quả nghiệm thu và đóng Task 7 — 2026-10-11

**Trạng thái tác vụ: DONE_WITH_CONCERNS.** Người dùng báo đã kiểm thử (“xong,doc log di| acceptance result”) và sau đó chấp nhận đóng (“fine result ! close to Done task!”). Đây là sign-off đóng bước kiểm thử/audit với các giới hạn và lỗi đã ghi nhận; **không phải kết luận tất cả tiêu chí kỹ thuật đều PASS**.

#### Nguồn và phạm vi bằng chứng

- Snapshot backend chụp lúc **11:31:59.680932 +07**, 22.514 dòng. SHA-256: `b2608c2e65b57ac7970ca698042f7fe13d3ea73b2110b54dfee6c2c604a5f079`.
- Lưu cục bộ tại `.superpowers/sdd/2026-10-10-background-actions/tmp/task7-live-2026-10-11/`: `backend.snapshot.log`, `events.txt` (có số dòng snapshot), `evidence.json` (log theo run/call và trace đã lọc).
- Đọc SQLite bằng `mode=ro`; không dùng constructor TraceStore để tránh ghi/migrate DB. Chỉ lấy trace từ lần khởi động 11:16:15 đến thời điểm snapshot.
- Đợt đầu 11:21:37–11:26:56 dùng **deepseek-flash**, 15 trace gồm cả lượt bị ngắt. Đợt bổ sung từ 11:28 dùng **gpt-5.6-luna**. Mặc định launcher gpt-6-luna không phải model thực tế của các lượt này; không gộp các kết quả thành phép so sánh model có kiểm soát. File effort tại lúc audit là low/low.

#### Kết quả theo tiêu chí

| Tiêu chí | Kết quả | Bằng chứng và giới hạn |
| --- | --- | --- |
| TC1: ngắt trong lúc thao tác ghi đang chạy | CHƯA ĐỦ BẰNG CHỨNG | 11:24:52 lần thêm finished/reported, sau đó mới interruption 11:24:58.974; giỏ có 2 cà phê sữa và menu được publish, nhưng không có ghi đang chạy ở điểm ngắt. Trong toàn snapshot, không interruption nào nằm giữa started và finished của 14 hành động ghi. |
| TC2: sửa yêu cầu trước dispatch | CHƯA ĐẠT luồng sửa được quan sát; nhánh chuẩn chưa được kiểm tra tách biệt | Biến thể trà ổi hồng + câu sửa cà phê đen lúc 11:23 có prompt mang sang nhưng chỉ mở menu. Lần 11:26 trà đã được thêm trước khi sửa; tool sửa là update số lượng dòng cà phê sữa hiện có, không đổi sang cà phê đen. |
| TC3: thêm một món rồi im lặng | ĐẠT đường server | 11:31:10.635 STT “Cà phê sữa nhé bạn.” sau câu làm rõ; trace giỏ đúng 1 cà phê sữa, revision 10; TTS xác nhận một lần lúc 11:31:13.918; bot-stop 11:31:17.529, lời khách tiếp theo 11:31:19.438. Không suy ra người dùng đã nghe toàn bộ từ riêng log TTS/transport. |
| AC-004: vòng đời hành động | ĐẠT trong mẫu | **14/14** khóa run/call có đúng 1 started, 1 finished và 1 reported. Cả 14 finished=succeeded, reported via=model. Đây không chứng minh lời xác nhận của từng hành động đã nghe đúng một lần. |
| Nhánh báo kết quả sau ngắt qua reporter/event_turn; failed/unknown | CHƯA KIỂM CHỨNG TRỰC TIẾP | Không có via=reporter/event_turn hoặc finished=failed/unknown trong mẫu. Hồi quy 63 test đạt là bằng chứng bổ sung, không thay thế live. |
| Đọc menu không bị chặn bởi pending_operation | ĐẠT cho các lượt đọc quan sát; thiếu nhánh cạnh tranh | Không có pending_operation trong snapshot, các trace menu có HTTP GET và display_menu thành công; không có lần đọc đồng thời với một ghi đang chạy để nghiệm thu cạnh tranh trực tiếp. |
| Không tạo đơn/thanh toán | ĐẠT trong trace đã chụp | Không có tool checkout hoặc HTTP không phải GET. Có lời nói về bàn 20/36 ngoài mẫu kiểm thử an toàn và tool set_table, nhưng không ghi nhận POST tạo đơn/khởi tạo thanh toán trong khoảng audit. Không kiểm tra hệ thống merchant bên ngoài để khẳng định tuyệt đối không có đơn từ nguồn khác. |

#### Hai phát hiện cần xử lý tiếp

1. **Yêu cầu được mang sang nhưng model bỏ qua.** Trace `019303c38cff44c7830d832052fc8967`, bắt đầu 11:22:14.365: prompt chứa earlier_unfinished_request “Mình xem Thêm hai cà phê đen vào giỏ hàng.” và latest_request “Cho mình xem menu.”. Lượt mới chỉ gọi http_request GET, display_menu, skill_trendcoffee-menu rồi kết thúc ANSWER; không có tool thêm cà phê đen. Runtime đã đưa yêu cầu cũ cho model, nên không quy kết bộ nhớ carry-over làm mất chuỗi. Trace `81963a7f7deb4fb1b86d3e0d3c315fa9` lúc 11:23:26.732 tương tự: prompt có yêu cầu trà cũ và sửa thành cà phê đen, nhưng chỉ mở menu. Chưa điều tra nguyên nhân sâu hoặc sửa mã trong lần audit này.

2. **Tool succeeded nhưng không thực hiện đổi món.** Trace `6a5f325d23df4b048054da2257848807`, bắt đầu 11:26:35.227: câu mới “À không, mình muốn đổi mình muốn đổi thành cà phê đen.”. Sau reasoning_deadline và retry=reasoning_off, model gọi display_cart với `action=update`, dòng cà phê sữa, `quantity=2`; không có variant mới, remove hoặc add cà phê đen. Tool output vẫn là **2 cà phê sữa + 1 trà ổi hồng**, revision **6**, tổng **135.000 đ**. Phản hồi “Bạn muốn đổi cà phê sữa thành cà phê đen, đúng không ạ?” là hỏi làm rõ, không phải xác nhận đã đổi. Không nghiệm thu đổi món chỉ từ status=succeeded.

Việc giỏ ở pipeline #0 có 4 cà phê sữa không được tính thành lỗi lặp: có hai yêu cầu thêm 2 riêng biệt (11:21 và 11:22). Phải đối chiếu lệnh và trace, không chỉ nhìn tổng số lượng.

#### Quan sát bổ sung

- gpt-5.6-luna có ví dụ carry-over xử lý được: trace `ef9de5161c7545e0a4b899c33cabde96` lúc 11:31:43.998 giữ yêu cầu chọn bàn 36, nhận câu mới chưa thanh toán + mở giỏ, gọi đọc bàn và set_table; giỏ vẫn 1 cà phê sữa + 1 dâu kem đá xay, bàn 36, không checkout. Đây là kết quả của biến thể bổ sung, không thay TC1/TC2.
- **39 callback lỗi STUN retry** trong snapshot, bắt đầu sau khi pipeline #3 kết thúc: `aioice.stun.Transaction.__retry` gọi transport đã đóng và `NoneType.sendto`. Các phiên sau vẫn hoạt động; chưa xác định tác động đầy đủ, chưa sửa.
- Menu/customer display đã có tool publish thành công trong trace, khác với blocker Chromium localhost ghi trong ledger trước đó. Không suy ra mọi kiểu reconnect/browser đều đã được kiểm chứng.

#### Đóng tác vụ

Task 7 đã thực hiện bước khởi động, cung cấp kịch bản, kiểm tra log/trace, lưu bằng chứng và ghi kết quả. **Đóng DONE_WITH_CONCERNS theo xác nhận người dùng**, giữ hai phát hiện và các nhánh thiếu bằng chứng ở trên cho lần làm việc tiếp theo. Không thay đổi mã sản phẩm, không stage, không commit; không tự chạy checkout hoặc tạo đơn.

Tự đánh giá audit: chính xác 4/5 (đối chiếu trạng thái giỏ nhưng không có xác nhận nghe độc lập), đầy đủ 5/5 (đọc log, trace và ghi giới hạn/sign-off), rõ ràng 5/5 (tách DONE khỏi PASS), khả năng sử dụng 5/5 (trace ID và số dòng để tái kiểm), ngắn gọn 4/5 (report dài để giữ bằng chứng; kết luận cho người dùng rút gọn). Trung bình 4.6/5. Không còn công việc bắt buộc trong bước audit được người dùng yêu cầu đóng.
