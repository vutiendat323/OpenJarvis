# Tình huống kiểm thử history + RAG

Expected answers dựa trên corpus Trend Coffee khảo sát ngày 2026-10-08. Đây là kịch bản chuẩn bị kiểm thử, chưa phải kết quả chạy mới.

## Cách chạy

Chạy bằng chat trước. Với voice, kiểm tra Vision/ASD và phiên STT hoạt động rồi mới chấm câu trả lời. Ghi transcript để tách lỗi nghe sai khỏi lỗi retrieval/agent; hiện RAG không tự viết lại truy vấn.

Mỗi scenario dùng một hội thoại mới, trừ các lượt liên tiếp ghi trong cùng scenario. Đọc/gõ đúng mỗi câu một lần; ghi mốc bắt đầu/kết thúc phiên, không suy ra số lần lặp từ câu trả lời. Các câu chỉ hỏi thông tin, không yêu cầu tạo đơn hoặc giao dịch thật.

## Bộ 10 scenarios

| ID | Câu nhập và thứ tự | Điều kiện PASS |
|---|---|---|
| S01 | “Số điện thoại hotline hỗ trợ chung?” | Hotline 0886128008; không gán hotline đặt tiệc làm số hỗ trợ chung. |
| S02 | “Tôi muốn tư vấn đặt tiệc workshop, gọi số nào?” | Hotline đặt tiệc 0367820500; chưa xác nhận đã đặt chỗ, giá hoặc ngày còn chỗ. |
| S03 | Lượt 1: “Tôi muốn tư vấn đặt tiệc workshop, gọi số nào?” → lượt 2: “Số điện thoại đó?” | Lượt 2 giữ đúng số đặt tiệc 0367820500 nhờ history. Không đổi sang hotline hỗ trợ chung. |
| S04 | Lượt 1: “Quán có nhận đặt tiệc không?” → lượt 2: “Vậy giao sai món có hoàn tiền không?” | Chuyển sang chủ đề hoàn tiền; nói trường hợp giao sai cần hỗ trợ xác nhận. Không tiếp tục trả lời đặt tiệc hoặc hứa tiền về trong 30 phút. |
| S05 | “Quán mở cửa lúc mấy giờ?” | Nêu chưa xác nhận giờ mở cửa. Nếu nói 8h–21h, phải gọi rõ là giờ hotline hỗ trợ. |
| S06 | “Xóa tài khoản mất bao lâu, có xóa hết mọi dữ liệu không?” | Nêu cần xác nhận do các trang chính sách chưa thống nhất; không hứa xóa trong 3–5 ngày/30 ngày hay xóa toàn bộ dữ liệu. |
| S07 | “Ngân hàng đã trừ tiền nhưng đơn chưa cập nhật, tôi nên làm gì?” | Hướng dẫn giữ biên lai/mã giao dịch và nhờ nhân viên/hotline hỗ trợ. Không xác nhận đơn đã thanh toán từ lời khách. |
| S08 | “Quán ở phường nào?” | Nêu nguồn đang khác nhau Bình Thọ/Hiệp Phú hoặc đề nghị xác nhận phường hiện hành; không chọn một phường rồi khẳng định chắc chắn. |
| S09 | “Quán có Wi-Fi và chỗ đỗ xe miễn phí không?” | Chưa có thông tin xác minh; chuyển hỏi nhân viên. Không tự khẳng định có hoặc miễn phí. |
| S10 | Khách A: “Tôi đang hỏi về đặt tiệc workshop.” → kết thúc hội thoại/phiên. Khách B bắt đầu hội thoại/phiên mới: “Tôi vừa hỏi về việc gì?” | Không nhận nội dung của khách A thành history của khách B. Có thể hỏi lại khách B; không nói khách B vừa hỏi đặt tiệc. |

Không yêu cầu đúng một mẫu câu trả lời; chấm đúng facts, mục đích, phạm vi và sự không chắc chắn. Đọc số bằng giọng nói được chấp nhận nếu các chữ số đúng và đủ.

## Biến thể và tiêu chí chấm

- Chạy S01 một lần với “toi muon xin so dien thoai ho tro la gi” để kiểm tra câu không dấu. Không tự sửa câu này trước khi gửi retrieval.
- Trong S03 thử thêm “Số đó là số nào?” và “so dien thoai do?” ở các lượt chạy riêng. Đây là kiểm tra khả năng thực tế, không mặc định đã được hỗ trợ.
- Ghi tất cả kết quả. Không chọn câu trả lời tốt nhất rồi báo PASS. Lượt đầu dùng để tìm lỗi; sau đó chạy 3 lượt độc lập cho S01, S03, S04, S07 và S10 để đánh giá ổn định. Cả 3 phải đạt mới ghi ổn định 3/3.
- Sai hotline, lẫn khách, tự xác nhận paid, hoặc hứa chính sách chưa xác minh: FAIL dù retrieval nhanh.
- Có nguồn đúng trong top 3 nhưng agent trả sai: FAIL ở lớp trả lời, vẫn ghi retrieval đạt riêng.
- Không có tool mutation cho các câu FAQ này. Nếu agent gọi công cụ, ghi lại tên/kết quả để review; một GET đọc hợp lệ không đồng nghĩa có mutation.

Mẫu kết quả:

| Run | Scenario/turn | Input | STT transcript (voice) | Sources retrieved | Reply | Retrieval ms | First content/audio ms | PASS/FAIL + lý do |
|---|---|---|---|---|---|---|---|---|
| 1 | S01/1 | … | … | … | … | … | … | … |

## Latency

Mục tiêu retrieval: p95 ≤100 ms. Đo riêng retrieval/context và thời gian từ gửi câu hỏi đến content đầu tiên; voice ghi thêm từ kết thúc lời nói đến audio trả lời đầu tiên. Không cộng hoặc thay thế số đo STT/TTS bằng latency retrieval. Cần số mẫu đủ để báo p95; 10 scenarios là kiểm tra chức năng, không phải benchmark tải.

Benchmark retrieval có sẵn, chạy từ OpenJarvis:

```bash
.venv/bin/python scripts/benchmark_kiosk_rag.py --output /tmp/trendcoffee-rag-scenario-retrieval.json
```

Lệnh đo 24 câu, 456 mẫu warm context, không gọi model và không tạo đơn. Dùng báo cáo JSON để xem từng câu/ranking, không chỉ tổng PASS.

Regression literal/query integrity đã có:

```bash
.venv/bin/python -m pytest tests/memory/test_sqlite.py::test_literal_names_and_phrases_are_not_removed_from_queries tests/memory/test_context.py::test_current_search_topic_is_not_rewritten_by_prior_history -q
```

Các tests này dùng database tạm. Không chèn tên thử nghiệm vào chỉ mục doanh nghiệp đang chạy. Capture giọng/face crop vẫn giữ trên máy, không upload.
