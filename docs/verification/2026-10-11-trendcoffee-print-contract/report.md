# Xác minh hợp đồng scan QR và bill gốc — 2026-10-11

## Kết quả

**Cập nhật yêu cầu ngày 2026-10-11:** người dùng bỏ điều kiện xác nhận scan. Trigger in hiện tại là merchant xác nhận order `paid` và invoice đúng đơn đã tồn tại. Việc chưa tìm được event scan không còn chặn workflow in.

| Hợp đồng | Kết quả | Giới hạn |
|---|---|---|
| Download PDF invoice từ merchant | Đã xác minh trên invoice tồn tại | Chưa xác minh server từ chối export khi `invoice=null` |
| Xác nhận khách scan QR chuyển khoản thành công | Chưa xác minh được; không còn là điều kiện in | Không tìm thấy hợp đồng event trong nguồn công khai đã khảo sát; không khẳng định backend không có event |
| Scan thành công tương đương `paid` | Không được sử dụng | Đây là hai tín hiệu khác nhau |

Không sửa mã sản phẩm, tạo đơn, khởi tạo payment, gọi temporary/auto export, cấu hình máy in hoặc gửi job in. Có gọi **một POST download PDF** trên đơn người dùng cung cấp, sau khi GET xác nhận invoice đã tồn tại. Không liên hệ người khác.

## API lấy bill chính thức

Nguồn first-party: [main bundle](https://trendcoffee.net/assets/index-DmriD68d.js), [public order detail](https://trendcoffee.net/assets/index-CJnCy6sC.js).

Frontend `Bxe(orderSlug)` và mutation hook `YXe` dùng:

```http
POST https://trendcoffee.net/api/latest/invoice/export/public
Content-Type: application/json
Accept: application/pdf

{"order":"<order_slug>"}
```

`order` là **order slug**, không phải invoice slug hoặc reference number. Public order page truyền `result.slug` vào hook export; trong các nhánh đã đọc, UI invoice/export chỉ được render khi order `paid`. Đây là điều kiện UI quan sát được, không phải bằng chứng server yêu cầu `paid` hoặc bằng chứng thời điểm invoice được tạo.

Kiểm tra invoice tồn tại bằng `GET /api/latest/orders/{order_slug}` → `result.invoice`; đối chiếu `result.slug` với đơn yêu cầu. Worker in tương lai phải kiểm tra tồn tại trước export; không dùng export để tạo invoice khi chưa có.

### Kiểm chứng với PDF người dùng cung cấp

Đơn `ee83f8fedb`, reference number `26022`:

- GET trước export: `status=paid`, invoice tồn tại, `invoice.slug=158d144966`, payment `statusCode=completed`.
- POST export: HTTP **200**, `Content-Type: application/pdf`, **51.783 bytes**, tổng thời gian curl **3,008724 giây** trong một mẫu. Không suy ra SLA/median từ mẫu này.
- GET sau export: cùng order slug, `status=paid`, cùng invoice slug `158d144966`, invoice `status=paid`, cùng reference number `26022`; invoice `createdAt` là 2026-08-14 19:40:06 +07.
- Lượt kiểm tra paymentMethod bổ sung: payment và invoice đều có `paymentMethod=point`. Đây là bằng chứng đơn thanh toán bằng xu và invoice tồn tại, không phải kiểm chứng chuyển khoản ngân hàng. `paid` không tự xác định phương thức thanh toán.
- PDF tải về: 1 trang, 227,04 × 792 pt; title Invoice, producer Skia/PDF m131.
- `pdftotext -layout` của PDF mẫu và PDF server trả về **giống nhau hoàn toàn**, 965 ký tự; normalized text cũng giống.
- Binary hash khác; PDF server trả về có CreationDate/ModDate mới (2026-10-11 14:33:11 +07). Không báo byte-identical hoặc khẳng định chỉ metadata khác: toàn bộ nội dung binary chưa được phân tích.

SHA-256:

```text
PDF mẫu:    1a5c3a0371d4cd493eea122f28082c498f79072c186e05bbe14f3fb6cd1f0db9
PDF export: bb445687345c04ff25ba79398c55cb3a37a4af179c8772ae51b6b267c0889b0e
```

Kết luận có giới hạn: API thực sự trả PDF từ hệ thống cho invoice đã tồn tại, nội dung khớp bill mẫu; invoice liên kết vẫn giữ cùng ID sau export. Chưa có backend source/spec để kết luận endpoint luôn là existing-only hoặc không có tác dụng phụ nào khác.

PDF export và bundle khảo sát chỉ lưu local trong `/tmp/trendcoffee-contract-check-2026-10-11/`, không đưa vào repo/stage. Không lưu response chứa thông tin khách vào báo cáo.

## Sự kiện scan QR chuyển khoản

Nguồn: [payment page](https://trendcoffee.net/assets/index-DW_5KOyj.js), main bundle ở trên; local `src/openjarvis/kiosk/presentation.py:check_payment`.

- Payment page merchant dùng `setInterval(..., 2000)` để refetch order; khi `result.status === PAID`, nó chuyển sang trang thành công. Trong payment module đã đọc không thấy subscription/event scan riêng.
- Local OpenJarvis `check_payment()` cũng kiểm tra order `paid`. Không dùng đường này làm bằng chứng scan.
- Decode QR trong ảnh người dùng bằng OpenCV: payload dài 134 ký tự, bắt đầu `000201`, có identifier `A000000727`, **không phải URL HTTP(S)**. Payload không được chép vào báo cáo. Đây là QR dữ liệu chuyển khoản EMV/VietQR; việc đọc được payload không phải xác nhận scan từ merchant.
- Các nhãn `qrTokenReady`/“Đã quét QR” trong main bundle liên quan scanner QR token/thẻ thành viên, thanh toán xu hoặc voucher. Hàm dialog `qHe` chuyển chuỗi scanner vào `onQrTokenScanned`; callback local này không chứng minh server xác nhận khách scan bank-transfer QR.
- GET đơn QR `8eb6d66563` ở lượt khảo sát trước trả `pending`, `invoice=null`, payment không có scan field. Lượt GET mới trong lần xác minh này trả HTTP **500**; không khẳng định trạng thái trước vẫn là trạng thái mới nhất.

Nguồn tham chiếu độc lập: [EMVCo QR](https://www.emvco.com/emv-technologies/qr-codes/); [VietQR callback documentation](https://api-v2.vietqr.vn/api-vietqr-callback) mô tả callback **biến động số dư/giao dịch**. Không có bằng chứng TREND dùng dịch vụ này; tài liệu được dùng để phân biệt transaction notification với scan-only event, không để suy ra tích hợp của TREND.

## Phạm vi tìm backend source/spec

- Bounded GET các route docs phổ biến dưới `/api/latest/`, `/api/docs`, `/api-json`: 404 JSON. `/docs`, `/openapi.json`, `/swagger-json`, `/swagger`: HTML SPA, không phải OpenAPI.
- [GitHub search trendcoffee](https://api.github.com/search/repositories?q=trendcoffee&per_page=20), exact domain và repo public của author workspace: chưa tìm được backend tương ứng có source/contract đọc được.
- Main bundle SHA-256: `953b8dabc55a14ade6fbe985b0d9cdc9c99986e09ab65c920628dee8c5e29ca6`.

Không có tài khoản admin/backend source trong phạm vi xác minh; không dò credentials hoặc gọi webhook giả để tạo scan event.

## Quyết định cho workflow in

**Theo cập nhật của người dùng: bỏ điều kiện scan; in sau khi merchant xác nhận `paid` và invoice đúng đơn tồn tại.**

Workflow: `Checkout hiện có → hiển thị QR → merchant trả paid → xác minh invoice đúng đơn → lấy PDF từ merchant → worker nền/CUPS`.

Điều kiện: `merchant_order.status == paid AND invoice_exists_for_same_order AND original_invoice_not_already_printed`. Lấy trạng thái và invoice từ merchant theo order ID đã gắn với checkout; không dùng lời khách, trạng thái UI hoặc invoice của đơn khác làm bằng chứng. Nếu `paid` nhưng invoice chưa có, chờ invoice chính thức; nếu API lỗi hoặc trạng thái chưa paid, chưa in. Không cần tìm hoặc bổ sung scan-only event để tiếp tục workflow này.

Giữ các ràng buộc còn lại: PDF lấy trực tiếp từ `/invoice/export/public` bằng order slug sau kiểm tra invoice tồn tại; không tự dựng bill hoặc dùng temporary export; chống trùng bền vững và đối chiếu CUPS job khi outcome chưa rõ; worker in chạy ngoài realtime voice; không đổi checkout logic. Việc endpoint có tự từ chối invoice-null hay không vẫn là giới hạn kiểm chứng, không thay thế kiểm tra tồn tại ở worker.

Đây là cập nhật thiết kế/yêu cầu, chưa triển khai auto-print hoặc gửi job in.

## Discovery bổ sung theo yêu cầu người dùng

Người dùng không có backend repo/docs và yêu cầu tiếp tục discovery. Đã thực hiện thêm:

- Tải/đọc **87 module JavaScript công khai**, chọn từ asset manifest của main bundle (các module `index-*`, `page-*`, `use-*`, staff QR/payment/payer và DocsPage); tổng 2.708.041 ký tự, ngoài main bundle. Đây không phải toàn bộ backend hoặc tất cả dependency frontend; không diễn đạt là audit toàn hệ thống.
- Trích các API path và các event/subscription literal liên quan từ main + các module đã tải. Không tìm thấy hợp đồng bank-transfer scan-only trong phạm vi này. Không thực thi JavaScript, API ghi, fake callback hoặc thao tác scan/thanh toán.
- [Point QR page](https://trendcoffee.net/assets/page-LE4mzZk_.js) có hai API riêng: `GET /api/latest/payment/qr/point?qrData=...` để đọc/preview QR, và `POST /api/latest/payment/qr/point/pay` với `{qrData: ...}` để trả bằng xu. Chỉ đọc source, **không gọi** hai API này. Trang yêu cầu customer login, thao tác pay riêng và poll order `paid` sau pay; không áp dụng hợp đồng này cho payload VietQR chuyển khoản trong ảnh.
- Main bundle có **notification type** `ORDER_PAID="order-paid"` và printer-failure types. Đây là type frontend, không phải kiểm chứng delivery runtime của notification. Không thấy bank-transfer scan notification riêng trong enum/luồng đã đọc; không dùng `order-paid` thay scan.
- OrderStatus enum đã đọc: `all`, `pending`, `shipping`, `failed`, `completed`, `paid`; PointQrStatus: `initiate`, `cancelled`, `completed`. Không suy ra backend không có thêm trạng thái nội bộ từ các enum frontend này.
- [DocsPage](https://trendcoffee.net/assets/index-IQ8yjUCB.js) chỉ là UI placeholder với tám card “Docs”, không chứa liên kết/schema/API contract.
- GET `/v3/api-docs` và `/assets/index-DmriD68d.js.map` trả HTML SPA, không phải OpenAPI/source map; `/api/latest/swagger.json`, `/api/latest/openapi` trả 404. Không tiếp tục dò route ngẫu nhiên.
- Kiểm tra GitHub qua connector và CLI đã đăng nhập, tìm domain trong code và tên/description repo liên quan; không tìm được backend TREND. Không xuất giá trị token, không liên hệ chủ repo hoặc tạo issue.
- GET lặp có giới hạn cho order ảnh `8eb6d66563` vẫn trả `statusCode=500`, message `Internal server error`; không có result để xác minh invoice/scan mới nhất. Không diễn giải trường invoice bị thiếu trong response lỗi là `invoice=null` của một order hợp lệ.

Cache và inventory chỉ nằm local: `/tmp/trendcoffee-contract-check-2026-10-11/assets/`, `asset-inventory.json`. Không đưa bundle, PDF hoặc dữ liệu khách vào Git.

Kết luận discovery: có API lấy PDF chính thức đã kiểm chứng; **chưa có bank-transfer scan-only contract khả dụng được phát hiện từ các interface đọc được**. Bằng chứng công khai không cho phép khẳng định chắc chắn backend riêng không có event đó. Không thể xác nhận server-side scan bằng cách chỉ decode payload hoặc dùng event payment/point thay thế. Theo yêu cầu đã cập nhật, kết quả discovery scan chỉ còn là thông tin khảo sát, không phải điều kiện triển khai in sau paid.

## Tự đánh giá khảo sát trước khi cập nhật trigger

Chính xác 4/5: có kiểm chứng download thật và đối chiếu PDF, thiếu backend contract scan/existing-only. Đầy đủ 4/5: khảo sát hai hợp đồng, scan còn chưa xác minh do thiếu nguồn first-party backend. Rõ ràng 4/5: phân biệt UI, API runtime và giới hạn source; report giữ chi tiết để tái kiểm. Khả năng sử dụng 4/5: endpoint/body/ID và điều kiện worker cụ thể; tại thời điểm khảo sát còn thiếu hợp đồng scan. Ngắn gọn 4/5: kết quả chính nằm đầu báo cáo, chi tiết bằng chứng nằm sau. Trung bình 4,0/5. Sau khảo sát, người dùng chủ động bỏ điều kiện scan; quyết định workflow hiện tại đã cập nhật ở trên.
