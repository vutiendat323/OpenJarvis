# Discovery và kiểm chứng bank-transfer — 2026-10-12

## Kết quả

**Phạm vi được người dùng làm rõ:** chỉ cần xác minh trạng thái từ TREND Coffee. `result.payment.statusCode=completed` và `result.payment.statusMessage=completed` được hiểu là payment thành công theo merchant; không yêu cầu kiosk chứng minh ghi có ngân hàng độc lập. Bằng chứng bank/provider bên dưới là khảo sát bổ sung, không phải điều kiện mới chặn workflow in.

Đã xác minh **request khởi tạo bank-transfer và response pending thực tế**, QR từ response trỏ tới ngân hàng ACB. Đã tìm thấy connector ACB trong frontend TREND và hợp đồng ngân hàng mô tả ghi có → thông báo merchant → cập nhật thanh toán. **Chưa có bằng chứng giao dịch bank-transfer paid/ghi có thực tế trong mẫu khảo sát**, chưa thấy callback riêng của backend TREND.

Không sửa sản phẩm, tạo đơn, khởi tạo payment mới, chuyển tiền, giả lập callback, đọc cấu hình/credentials ngân hàng hoặc gửi job in. SQLite được mở bằng `mode=ro`, `PRAGMA query_only=ON`; không dùng constructor có thể migrate/ghi DB. Có đọc lịch sử HTTP response của kiosk và GET các đơn đã có ID trong lịch sử đó. Không liệt kê đơn khách khác từ merchant.

Yêu cầu in đã được người dùng cập nhật ngày 2026-10-11: merchant `paid` + invoice đúng đơn tồn tại + chưa in gốc. Không khôi phục điều kiện scan hoặc thay đổi checkout.

## Kiosk → TREND: payload và response đã kiểm chứng

Nguồn: trace local `df394b801d2c42a8b3e5c2e007c80169`, tool step 5, timestamp epoch `1791768920.6109152`. Đây là **request đã chạy trước lúc khảo sát**, không phải POST mới trong phiên discovery.

```http
POST https://trendcoffee.net/api/latest/payment/initiate/public
Content-Type: application/json

{"orderSlug":"56494e01e6","paymentMethod":"bank-transfer"}
```

HTTP response metadata 200. JSON response, chỉ giữ các trường liên quan:

```json
{
  "statusCode": 200,
  "message": "Payment has been initiated successfully",
  "result": {
    "slug": "86bfbbd11b",
    "paymentMethod": "bank-transfer",
    "amount": 355000,
    "statusCode": "pending",
    "statusMessage": "pending",
    "transactionId": "5bb67c70-10ec-4e39-b492-045865aa8867",
    "qrCode": "<QR image omitted>"
  }
}
```

Placeholder qrCode là phần lược bỏ của báo cáo, không phải dữ liệu server trả về. Không chép QR/base64, tài khoản nhận tiền hoặc userId vào repo. Payment slug khác order slug.

HTTP/envelope `200` xác nhận thao tác khởi tạo thành công; `result.statusCode=pending` xác nhận payment đang chờ. `transactionId` đã có khi pending, nên riêng ID này không chứng minh tiền đã chuyển/ghi có.

## Kiosk → TREND: đọc trạng thái

```http
GET https://trendcoffee.net/api/latest/orders/56494e01e6
```

GET trong phiên khảo sát trả 200, các trường thực tế liên quan:

```json
{
  "statusCode": 200,
  "result": {
    "slug": "56494e01e6",
    "status": "pending",
    "payment": {
      "paymentMethod": "bank-transfer",
      "statusCode": "pending"
    },
    "invoice": null
  }
}
```

Đây là projection của response, không phải toàn bộ order JSON. Không trình bày mẫu `paid/completed` giả như response đã nhận được.

## Xác minh ngân hàng từ QR thật

- Decode ảnh QR trực tiếp từ response historical trên bằng OpenCV, chỉ xuất cấu trúc liên quan: currency `704`, amount `355000`, bank BIN `970416`, không phải URL HTTP(S).
- [VietQR bank list](https://api.vietqr.io/v2/banks) ánh xạ BIN `970416` tới `ACB` / Ngân hàng TMCP Á Châu.
- Bằng chứng này xác nhận ngân hàng nhận tiền được mã hóa trong QR mẫu, **không xác nhận đã ghi có hoặc callback đã đến TREND**.

## Connector merchant và hợp đồng ngân hàng

Nguồn first-party:

- [TREND bank-card module](https://trendcoffee.net/assets/bank-card-DRnRhhNA.js): client gọi `/acb-connector`, form có `xProviderId`, `xOwnerNumber`, `xService`, `xOwnerType`, `virtualAccountPrefix`, `beneficiaryName`. Chỉ đọc code công khai, không GET live connector config.
- [TREND main bundle](https://trendcoffee.net/assets/index-DmriD68d.js): error map có `103008: toast.getAcbTokenFail`, `103009: toast.initiateQrCodeFail`; translations nhắc ACB token và cấu hình connector.
- [TREND payment page](https://trendcoffee.net/assets/index-DW_5KOyj.js): refetch order mỗi 2000ms, chuyển sang thành công khi order `paid`; đọc `order.payment.qrCode`/amount/statusMessage. Không thấy callback auth/settlement implementation trong client.
- [TREND payment instruction API](https://trendcoffee.net/api/latest/static-page/PAYMENT_INSTRUCTION): hướng dẫn khách dùng app ngân hàng/ví, xác nhận số tiền và nội dung chuyển, giữ biên lai nếu trạng thái chưa cập nhật. Guide nêu có thể chờ 30 giây–1 phút; đây là nội dung hướng dẫn, không phải SLA đã đo.
- [ACB QR Product Mapping](https://developer.acb.com.vn/acb/open/vi/node/2342): ACB nhận thanh toán và ghi có doanh nghiệp trước, sau đó gửi thông báo trạng thái cho API do doanh nghiệp tự phát triển; doanh nghiệp cập nhật trạng thái thành công. Đối soát gồm danh sách giao dịch đã ghi có ngày T−1 và query lịch sử theo `orderID`, `fromDate`, `toDate`.
- [ACB dynamic QR product](https://acb.com.vn/doanh-nghiep-giai-phap-thanh-toan/qr-code-dong): tích hợp cập nhật giao dịch/đối soát qua API hoặc QR Portal.

**Suy luận có căn cứ:** TREND có đường tích hợp ACB và QR mẫu thực sự dùng BIN ACB. Hợp đồng ACB giải thích mô hình bank credit → merchant notification. Tuy nhiên, nó không chứng minh code private của TREND thực hiện callback xác thực, đối chiếu amount/order, xử lý replay hay đối soát đúng cho một giao dịch cụ thể. Không đưa ra URL/body/ACK callback TREND tự suy đoán.

[ACB API catalog](https://developer.acb.com.vn/acb/open/en/product/10489) công khai tên API status notification v1.0.5, operation `POST /status`, cùng initiate/retrieve/daily notification. Schema request/response yêu cầu đăng nhập và chưa đọc được. `/status` là operation trong catalog ACB, **không phải endpoint TREND đã xác minh**. Chưa có body callback hoặc ACK JSON riêng của TREND để cung cấp.

## Phạm vi bằng chứng giao dịch

- Lọc lịch sử HTTP tool response local được lưu trong `traces.db`: 259 response khởi tạo bank-transfer, 259 order slug; tất cả payment `pending` ở thời điểm khởi tạo. Không suy ra các đơn vẫn pending ở hiện tại từ lịch sử này.
- GET có giới hạn 12 order slug gần nhất trong lịch sử đó, concurrency 3: 4 response hợp lệ 200, đều order pending/payment bank-transfer pending/invoice null; 8 response 500. Không diễn giải response lỗi 500 là đơn chưa có invoice hoặc đã bị hủy.
- Không có mẫu bank-transfer `paid + completed + invoice` trong phần dữ liệu kiểm tra này. PDF bill đã kiểm chứng ở báo cáo ngày 2026-10-11 thuộc đơn thanh toán `point`, không phải bank-transfer.
- Không có bank statement, provider-credit receipt, callback log hay backend source để chứng minh thanh toán đầu-cuối.

Evidence đã lọc chỉ lưu local tại `/tmp/trendcoffee-bank-transfer-2026-10-12/trace-evidence.json` và `live-order-evidence.json`; không chứa QR/account/name/transcript. Chỉ report này được thêm vào repo, chưa stage/commit.

## Giới hạn và cách đọc kết quả

Khởi tạo QR chuyển khoản ACB đã có bằng chứng thực tế. Cơ chế ACB thông báo sau ghi có có tài liệu chính thức. Giao dịch bank-transfer đã thanh toán và mapping private TREND callback → paid/completed → invoice **chưa được kiểm chứng thực tế** trong mẫu khảo sát; đây không phải điều kiện chặn workflow theo phạm vi người dùng vừa làm rõ. Một response merchant completed/paid là xác nhận của merchant. Không giả lập callback hoặc tự chuyển tiền để tạo bằng chứng.

## Xác minh trực tiếp completed trên TREND — 2026-10-12 02:06:54 UTC

GET `/api/latest/orders/ee83f8fedb` trả envelope `statusCode=200`, order `status=paid`, payment `statusCode=completed`, payment `statusMessage=completed`, invoice tồn tại với slug `158d144966`. PaymentMethod của mẫu là `point`; mẫu này xác minh ý nghĩa trạng thái completed của merchant, không được trình bày thành test chuyển khoản ngân hàng thực tế.

GET order bank-transfer `56494e01e6` ở lượt này lỗi HTTP nên không có response hợp lệ để kết luận trạng thái mới nhất của đơn đó. Không dùng response pending ở lượt trước làm trạng thái hiện tại.

Phân biệt các trường: `statusCode=200` ngoài envelope là thành công của API; `result.payment.statusCode=completed` là trạng thái payment thành công; `result.status=paid` là trạng thái order đã thanh toán. `pending` nghĩa chờ xác nhận, không phải thất bại. Invoice được kiểm tra độc lập theo đúng order ID trước tải PDF/in; việc hiểu completed là thành công không tự chứng minh invoice đã tồn tại.

Evidence đã lọc lưu local tại `/tmp/trendcoffee-bank-transfer-2026-10-12/payment-status-verification.json`.

## Tự đánh giá

Chính xác 4/5: payload và QR thật, nguồn ACB chính thức; mapping backend vẫn thiếu. Đầy đủ 4/5: phân biệt initiation/status/callback/reconciliation, chưa có bank-transfer paid mẫu. Rõ ràng 4/5: response projection được đánh dấu, không lẫn 200 với paid. Khả năng sử dụng 4/5: endpoint/body và evidence IDs cụ thể; thiếu callback log hoặc credit receipt cho kết luận đầu-cuối. Ngắn gọn 4/5: report giữ chi tiết để tái kiểm, phản hồi người dùng rút gọn. Trung bình 4,0/5.
