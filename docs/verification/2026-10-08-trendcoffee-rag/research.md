# Khảo sát TREND Coffee cho RAG

Ngày: 08/10/2026. Chỉ đọc trang công khai trên `trendcoffee.net`, render JavaScript bằng Chromium. Không đăng nhập, gửi biểu mẫu, tạo giao dịch hoặc thu thập dữ liệu khách. Không dùng doanh nghiệp cùng tên trên `trendcoffee.com` hay nguồn bên thứ ba.

## Nguồn đã xác nhận

| Nguồn | Nội dung phù hợp cho corpus |
|---|---|
| [Về chúng tôi](https://trendcoffee.net/about) | Không gian rustic; cà phê, trà, bánh thủ công, món nhẹ/món chính; dịch vụ sinh nhật, họp mặt, workshop, mini event. Đây là mô tả doanh nghiệp. |
| [Đặt tiệc](https://trendcoffee.net/booking) | Tiệc cá nhân có set cho hai người và trang trí theo chủ đề; tiệc nhóm có menu/setup theo yêu cầu. Hotline riêng **0367820500**, Fanpage Trend Coffee and Foods. Không có bằng chứng giá hoặc sức chứa. |
| [Trang chủ](https://trendcoffee.net/) | Hỗ trợ **0886128008**, email **trend.coffee.tea@gmail.com**; địa chỉ **03 Nguyễn Công Trứ, Thủ Đức, TP. HCM**. Footer pháp lý dùng số **0945867620**. Không gộp vai trò các số. |
| [Đặt hàng](https://trendcoffee.net/order-instructions) | Đăng nhập → chọn chi nhánh → tìm món/kích cỡ/số lượng → giỏ/bàn → đặt hàng. Theo dõi tại “Đơn hàng của tôi”; hướng dẫn cho phép sửa/hủy khi chưa trả tiền. Hotline **8h–21h hằng ngày** là giờ hỗ trợ. |
| [Thanh toán](https://trendcoffee.net/payment-instructions) | Hướng dẫn QR, kiểm tra số tiền/nội dung, hạn 15 phút. Đã trả nhưng chưa cập nhật: giữ biên lai/mã giao dịch, liên hệ hỗ trợ. Phương thức/thời hạn/trạng thái thực tế phải xác nhận bằng API hiện tại. |
| [Chính sách](https://trendcoffee.net/policy) | Bản 11/2025 nêu hoàn tiền khi sai món, lỗi sản phẩm hoặc lỗi xử lý kỹ thuật. “Thời gian phản hồi” 30 phút từ nhận hàng còn mơ hồ; không bảo đảm SLA. Xóa: tối đa 30 ngày, có ngoại lệ giữ giao dịch. |
| [Bảo mật](https://trendcoffee.net/security) | Quyền xem/sửa/xóa dữ liệu, từ chối tiếp thị, hỏi cách sử dụng thông tin. Mô tả chia sẻ cần thiết cho dịch vụ/pháp luật/chống gian lận. Không áp dụng chính sách này tự động cho camera/micro/memory Jarvis. |
| [Xóa tài khoản](https://trendcoffee.net/delete-account) | Yêu cầu qua hotline/email đăng ký/nhân viên; cần xác minh, thao tác vĩnh viễn. Trang nêu 3–5 ngày làm việc và xóa toàn bộ dữ liệu, mâu thuẫn nguồn chính sách. Không đưa SLA/phạm vi giữ dữ liệu vào corpus. |

## Mâu thuẫn và khoảng trống

- **Phường:** footer ghi Bình Thọ; [API chi nhánh công khai](https://trendcoffee.net/api/latest/branch) trả Hiệp Phú cho cùng địa chỉ đường. Chỉ dùng số nhà/đường/thành phố trước khi doanh nghiệp xác nhận; không suy diễn thay đổi địa giới.
- **Xóa tài khoản:** hai nguồn ở bảng chưa thống nhất. Giữ hướng dẫn liên hệ và chuyển hỗ trợ khi khách hỏi thời hạn/lưu giữ.
- **Liên hệ:** giữ riêng hotline hỗ trợ, số đặt tiệc và điện thoại pháp lý. `/contact` render “Trang không tồn tại”.
- Chưa xác nhận giờ mở cửa quán, sức chứa, Wi-Fi, đỗ xe, phí giao hàng, thành phần/dị ứng/caffeine, chính sách thú cưng. Không khẳng định từ tên món.
- `/menu` yêu cầu chọn chi nhánh trong lần khảo sát. [API danh mục](https://trendcoffee.net/api/latest/catalogs) trả danh mục công khai; [API sản phẩm](https://trendcoffee.net/api/latest/products?branch=ba9355f797&limit=100&page=1) trả 122 bản ghi trong response quan sát, gồm vật tư/phụ phí, nhiều mô tả rỗng hoặc chỉ là tên tiếng Anh. Không xem tất cả bản ghi là món đang bán. Giá, tồn kho, ưu đãi, lịch sự kiện và trạng thái đơn cần API cập nhật.

## Kết quả chuẩn bị

Corpus nằm tại `configs/openjarvis/knowledge/trendcoffee/documents/`: 7 tài liệu FAQ được tóm tắt theo chủ đề. `manifest.json` bên cạnh lưu nguồn, phiên bản và checksum; `README.md` chỉ cách nạp đúng thư mục. Không đưa DOM, báo cáo hoặc catalog thô vào chỉ mục. Khảo sát mới xác nhận nội dung nguồn; chưa đo retrieval, latency hoặc độ đúng của câu trả lời agent.

## Quy tắc chuẩn bị corpus

Chia đoạn theo chủ đề, gắn URL/ngày lấy/phạm vi chi nhánh/trạng thái xác nhận; thay thế đoạn cũ khi tài liệu đổi. Không sao chép dài chính sách. Câu trả lời phân biệt hướng dẫn được công bố với giao dịch hiện tại. Không dùng RAG xác nhận đơn đã thanh toán.

Bằng chứng tạm: `/tmp/trendcoffee-research-{about,booking,policy,security,delete-account,order-instructions,payment-instructions}-20261008.{html,txt}`. Nghiên cứu chưa nạp chỉ mục, thay đổi runtime, chạy kiểm thử, stage hoặc commit.
