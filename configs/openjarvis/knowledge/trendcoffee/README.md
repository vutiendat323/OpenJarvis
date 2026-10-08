# Bộ tài liệu RAG TREND Coffee

Khảo sát nguồn chính thức ngày 2026-10-08 bằng GET công khai và Chromium dựng trang JavaScript. Bộ tài liệu đã được index và bật recall trong preset kiosk: SQLite riêng `~/.openjarvis/trendcoffee-rag.db`, 3 đoạn / 800 token xấp xỉ. Kết quả thực tế tại [báo cáo triển khai](../../../../docs/verification/2026-10-08-kiosk-history-rag/report.md).

## Nội dung được chọn

Chỉ index các file `documents/` trong manifest: 7 tài liệu ngắn về giới thiệu, liên hệ, đặt tiệc, hướng dẫn website, hỗ trợ thanh toán, yêu cầu hoàn tiền và tài khoản. Mỗi tài liệu giữ URL nguồn, ngày khảo sát, phạm vi và giới hạn ngay trong nội dung; script nạp thêm metadata từ manifest.

Không index README, báo cáo nghiên cứu, DOM, bundle JavaScript hoặc toàn bộ API catalog. Giá, tồn kho, trạng thái bán, khuyến mãi, QR, số tiền và trạng thái đơn phải lấy từ API hiện tại. Không đưa thông tin khách vào knowledge chung hoặc `facts_path`.

## Các điểm cần xác nhận

| Chủ đề | Phát hiện | Cách trả lời trong corpus |
|---|---|---|
| Phường của địa chỉ | Footer: Bình Thọ; API chi nhánh: Hiệp Phú | Nêu số 3 Nguyễn Công Trứ, Thủ Đức, HCM; xác nhận phần phường |
| Số liên hệ | Hỗ trợ 0886128008; đặt tiệc 0367820500; footer pháp lý 0945867620 | Giữ đúng mục đích từng số |
| Giờ | Hướng dẫn ghi hotline 8h–21h | Chỉ gọi là giờ hỗ trợ |
| Xóa tài khoản | `/policy`: tối đa 30 ngày, có giữ giao dịch; `/delete-account`: thường 3–5 ngày làm việc, mô tả xóa hết dữ liệu | Không cam kết SLA hoặc phạm vi xóa |
| Hoàn tiền | Cụm “thời gian phản hồi” chưa rõ trách nhiệm của bên nào | Chuyển hỗ trợ, không hứa thời gian tiền về |
| Thanh toán | Hướng dẫn có QR và thời hạn; là bài viết, chưa đối chiếu backend | Dùng API để xác nhận đơn cụ thể |
| Mô tả món | API sản phẩm có nhiều description rỗng/ngắn, cùng vật tư/phí/mục nội bộ | Chưa thêm mô tả món riêng; không suy ra thành phần/dị ứng |

Nguồn chi tiết và phương pháp: [báo cáo nghiên cứu](../../../../docs/verification/2026-10-08-trendcoffee-rag/research.md).

`manifest.json` lưu phiên bản corpus, nguồn từng file và SHA-256 để theo dõi cập nhật. Script index kiểm tra checksum, lưu metadata và dùng source là đường dẫn tuyệt đối ổn định. Database riêng phục vụ Trend Coffee; metadata không tự tạo tenant filter cho database trộn nhiều cửa hàng.

## Index lại và đo retrieval

Từ repo OpenJarvis, sau khi cập nhật checksum trong manifest cho tài liệu đã duyệt:

```bash
.venv/bin/python scripts/benchmark_kiosk_rag.py --index --output docs/verification/2026-10-08-kiosk-history-rag/retrieval.json
```

Lệnh đã được chạy; index lặp lại vẫn có 7 records. Backend SQLite yêu cầu `openjarvis_rust`; binding phải được rebuild sau thay đổi Rust. Đổi tên/xóa tài liệu cần xử lý source cũ để không lưu lại kiến thức lỗi thời. Script chưa tự xóa records của source bị bỏ khỏi manifest.

Truy vấn hiện được truyền nguyên vẹn tới SQLite: không regex đại từ, không xóa cụm hội thoại và không tự ghép câu trước. History vẫn truyền cho agent. [Đo lại sau thay đổi](../../../../docs/verification/2026-10-08-kiosk-history-rag/raw-query-report.md): 22/24 top-1, 24/24 top-3/context; p95 context offline 0,549 ms.

24 câu cố định kiểm tra retrieval/context; câu trả lời model và HTTP trước thay đổi được lưu riêng. Các số đo không đại diện mọi cách nói hoặc latency mic → STT → model → TTS. History voice giữ lượt hoàn chỉnh trong ngân sách 4096 token xấp xỉ; lượt mới nhất được giữ nguyên nếu riêng nó vượt ngân sách.

Preset cho phép các hotline/email doanh nghiệp đã xác minh đi qua bộ lọc PII bằng `security.public_contact_values`; số cấu hình dùng chữ số liền nhau. Dữ liệu cá nhân khác và secrets vẫn được lọc trong đường `generate()`. Khảo sát không bổ sung cơ chế lọc streaming ngoài hành vi sẵn có.
