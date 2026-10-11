# Task 7 — Kiểm thử thủ công và nghiệm thu
Ngày: 2026-10-11. Trạng thái: **DONE_WITH_CONCERNS — người dùng chấp nhận đóng Task 7 sau kiểm thử và đối chiếu log**. Kết quả kỹ thuật từng TC được giữ nguyên bên dưới; đóng task không có nghĩa mọi TC đều PASS.

## Chuẩn bị

- Stack khởi động lúc 11:16:15 +07; Vision, backend, frontend khỏe. Backend được khởi động sau các sửa đổi nguồn của tính năng.
- Model kiểm thử: gpt-6-luna; effort Luna low; giữ nguyên model và giọng đọc trong các lần chạy.
- Mở http://localhost:5173/kiosk hoặc https://fedora-1.tailaa7688.ts.net/kiosk trên thiết bị có micro.
- Mỗi TC dùng một phiên thoại mới. Kiểm tra giỏ trống trước khi bắt đầu; giỏ có thể giữ qua phiên, nên tải lại trang hoặc mở phiên mới chưa đủ để chứng minh giỏ trống.
- Một người nói, micro hoạt động. Ghi giờ bắt đầu/kết thúc và giỏ cuối mỗi TC.
- Chỉ thử giỏ/menu. Không nói số bàn hoặc yêu cầu thanh toán. TC3 đã bỏ “bàn 57” theo P3 trong ledger vì câu đó có thể tạo đơn thật.

## Kịch bản

### TC1 / AC-001 — Ngắt lời để xem menu khi lần thêm món còn chạy

1. Nói: “Thêm hai cà phê sữa vào giỏ hàng”.
2. Ngay khi Jarvis bắt đầu trả lời, chen vào: “Cho mình xem menu”.
3. Đợi Jarvis xử lý xong; quan sát menu và giỏ.

ĐẠT khi:
- Menu xuất hiện, giỏ cuối có đúng 2 cà phê sữa, không tăng thành 4.
- Jarvis nhận câu mới; không đọc tiếp câu cũ đè lên lời khách sau khi hệ thống đã nhận ngắt lời.
- Kết quả thêm thành công được xác nhận bằng lời đúng một lần; câu “đang thêm/đang xử lý” không phải xác nhận đã hoàn tất.
- Log chứng minh lần thêm đã dispatch và chưa xong khi câu xem menu bắt đầu. Lần đọc menu không bị chặn bởi pending_operation vì lần ghi trước; không quy thời gian chờ LLM hoặc mạng thành thời gian chờ write gate.

CHƯA ĐỦ BẰNG CHỨNG nếu lần thêm đã xong trước câu thứ hai, hoặc chưa dispatch khi bị ngắt. Chạy lại đúng cửa sổ thời gian thay vì kết luận tính năng đã đạt.

### TC2 / AC-002 — Sửa yêu cầu khi Jarvis chỉ mới suy nghĩ

1. Nói: “Thêm một trà đào vào giỏ hàng”.
2. Ngay lập tức nói tiếp: “À không, lấy một cà phê đen”.
3. Đợi xử lý xong và kiểm tra giỏ.

ĐẠT khi:
- STT ghi nhận đủ cả yêu cầu ban đầu và câu sửa.
- Với lần chạy mà trà đào chưa được dispatch trước khi ngắt, giỏ cuối có đúng 1 cà phê đen và không có trà đào.
- Không phát sinh lần thêm trà đào từ yêu cầu cũ được mang sang; không thêm cà phê đen hai lần.
- Jarvis phản hồi theo yêu cầu mới, không xác nhận trà đào đã được thêm khi thực tế chưa thêm.

Nếu log cho thấy trà đào đã dispatch trước câu sửa, ghi lại hành vi để audit nhưng không dùng lần đó để nghiệm thu nhánh “chỉ suy nghĩ”; chạy lại TC2 sớm hơn.

### TC3 / AC-003 — Báo kết quả khi khách không nói tiếp

1. Nói: “Thêm một cà phê sữa vào giỏ hàng”.
2. Giữ im lặng, giữ phiên thoại mở và đợi Jarvis kết thúc.
3. Quan sát giỏ và ghi lại câu xác nhận nghe được.

ĐẠT khi:
- Giỏ tăng đúng 1 cà phê sữa.
- Có xác nhận thành công bằng lời đúng một lần; không cần khách hỏi lại.
- Xác nhận không cắt mất phần cuối câu trả lời đang phát.
- Sau khi tác vụ hoàn tất và Jarvis hết nói, không còn kết quả thành công chưa được báo.
- Nếu chưa có xác nhận, ghi thời gian chờ kể từ khi tác vụ hoàn tất; 15 giây là mốc quan sát để ghi lỗi chậm, không phải SLA mới.

## AC-004 — Đối chiếu log cho cả ba TC

- Đối chiếu theo run/call, không chỉ tên tool. Mỗi hành động ghi thực sự đã dispatch có một started, một finished, và một reported trước khi phiên kết thúc.
- Lần bị từ chối/hủy trước dispatch không được tính thành tác vụ nền đã hoàn tất.
- Thành công: giỏ, tool outcome và câu xác nhận phải khớp. Failed/unknown: không được nói đã thành công hoặc tự lặp lại hành động chưa biết kết quả.
- Lần đọc menu không bị từ chối bởi pending_operation vì một lần ghi đang chạy.
- Không có checkout, tạo đơn, khởi tạo thanh toán hoặc QR trong các kịch bản này.
- Log reported không đủ chứng minh khách đã nghe: phải đối chiếu với âm thanh người kiểm thử thực sự nghe được.

Lọc log cục bộ bằng:
```bash
rg -n "transcript '|Generating TTS|agent_round|agent_dispatch_blocked|background_action_|pending_operation|checkout|orders/public|payment/initiate/public" .stack-logs/backend.log
```
Chỉ lấy đoạn thuộc thời gian của TC; không lấy 80 dòng cuối làm bằng chứng đầy đủ nếu chúng đã mất phần started.

## Phiếu kết quả

| TC | Giờ bắt đầu–kết thúc | Phiên/điểm ngắt qua log | Giỏ cuối | Xác nhận nghe được và số lần | Kết quả |
| --- | --- | --- | --- | --- | --- |
| TC1 | 11:24:50–11:25:05 | Pipeline #2; lần thêm đã finished/reported trước interruption 11:24:58.974 | 2 cà phê sữa trong trace; menu được publish | TTS xác nhận thêm một lần; chưa có bảng xác nhận nghe từ người kiểm thử | CHƯA ĐỦ BẰNG CHỨNG cho nhánh ghi còn chạy khi bị ngắt |
| TC2 | 11:23:08–11:23:38 và 11:26:21–11:26:50 | Biến thể trà ổi hồng; lượt gộp sửa + xem menu chỉ mở menu; lượt sửa sau thêm đã hoàn tất | Lượt sửa sau: 2 cà phê sữa + 1 trà ổi hồng, không có cà phê đen; revision 6 không đổi | TTS hỏi có muốn đổi cà phê sữa; tool chỉ update quantity=2, không đổi món | CHƯA ĐẠT luồng sửa món; nhánh trước dispatch cũng chưa được chạy tách biệt đúng mẫu |
| TC3 | 11:31:10–11:31:17 | Pipeline #6, gpt-5.6-luna; một cà phê sữa sau câu làm rõ, không bị ngắt trước bot-stop | Đúng 1 cà phê sữa trong trace, revision 10 | Một TTS “Đã thêm vào giỏ.”; BotStopped 11:31:17.529; lời khách tiếp theo 11:31:19.438 | ĐẠT đường server; chưa có xác nhận số lần nghe thực tế |

Mẫu gửi lại sau từng TC:
```text
TC1 | bắt đầu HH:MM:SS, kết thúc HH:MM:SS
Giỏ trước: ...
Giỏ cuối: ...
Jarvis nói: ... (xác nhận ... lần)
Điểm bất thường: ... / không có
```

## Điều kiện đóng Task 7

Người dùng đã thông báo thực hiện kiểm thử, sau đó yêu cầu “fine result ! close to Done task!”. Task 7 được đóng **DONE_WITH_CONCERNS** theo quyết định đó; không đổi kết quả CHƯA ĐẠT/CHƯA ĐỦ BẰNG CHỨNG thành PASS. Tiêu chí nghiệm thu kỹ thuật đầy đủ vẫn cần TC1–TC3 hợp lệ, đối chiếu AC-004 và xác nhận nghe thực tế. Các điểm chưa đạt được lưu làm việc tiếp theo, không tự sửa mã hoặc chạy lại trong lần đóng task này.

Kết quả audit: 14/14 hành động ghi có một started, finished và reported (đều via=model); không có interruption nằm giữa started và finished, không có nhánh reporter/event_turn thực sự được nghiệm thu. Hai trace DeepSeek chứng minh yêu cầu được mang sang nhưng lượt mới chỉ mở menu. Lượt sửa món sau đó không thay đổi giỏ. Snapshot và trace trích xuất chỉ lưu trên máy, xem report.md để biết đường dẫn bằng chứng.

Chuẩn bị đã xác minh: 63 kiểm thử hồi quy đạt trong 10.61 giây (background scenarios, action ledger, voice background, voice lifecycle). Đây là bằng chứng tự động bổ sung, không thay thế ba lần kiểm thử trực tiếp. Không tạo commit và không stage.
