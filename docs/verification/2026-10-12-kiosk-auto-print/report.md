# Kiosk auto-print — triển khai và kiểm chứng 2026-10-12

## Hành vi

Kiosk publish QR đã xác thực → worker nền lưu order/payment IDs → poll merchant → chỉ khi order `paid`, payment `statusCode` và `statusMessage` đều `completed`, invoice đúng đơn có ID → tải PDF merchant → raster toàn bộ trang → một job CUPS giữ chờ → lưu job ID → kiểm tra ID/tên/queue/trạng thái job CUPS → release job đó.

Không dùng scan event hoặc đối soát ngân hàng độc lập. Không đổi bước checkout, prompt, UI hoặc realtime voice pipeline. Không tự dựng nội dung hóa đơn. `submitted` nghĩa CUPS chấp nhận release, không phải xác nhận giấy đã ra.

## Thay đổi

- `kiosk/printing.py`: subscriber chỉ enqueue IDs/wake worker; HTTP, SQLite, PDF và spool ngoài event loop. Theo dõi đơn tiếp tục khi browser reset/voice kết thúc.
- Journal riêng `~/.openjarvis/printing.db`: unique merchant/order và merchant/invoice; atomic claim ngăn hai worker tạo bản gốc trùng; giữ invoice ID, PDF hash, job ID/title và error code.
- `kiosk/cups.py`: dùng `pdftoppm` xuất PBM rồi ESC/POS; không dependency Python mới. CUPS `lp -H hold -n 1 -o raw`, `ipptool` chỉ đọc job identity, rồi `lp -H resume`; không dùng restart-job.
- Thêm metadata vào checkout recipe: invoice export URL, queue `GP-C180I`, width `576` dots. Các checkout steps giữ nguyên; core không chứa TREND URLs.
- Backend sở hữu startup/shutdown; shutdown flush IDs còn trong queue, không bắt đầu HTTP/print mới. Worker retry lỗi trước spool tối đa 5 lần; spool/release không rõ kết quả không tự retry.
- Read-only diagnostics: `GET /api/kiosk/print-jobs` → enabled + journal states/IDs/error codes, không trả nội dung invoice hoặc thông tin khách.

## Kiểm chứng

| Kiểm tra | Kết quả |
|---|---|
| CUPS/worker/lifecycle/metadata suite | **54 passed**, 5 dependency/FastAPI deprecation warnings |
| Ruff check + format | Pass trên 10 file Python thuộc thay đổi |
| Git diff whitespace | Pass |
| Raster PDF merchant thật, không spool | 1 trang, **144.792 bytes ESC/POS**; reset/cut đúng cấu trúc |
| IPP lookup CUPS thật, không release | Đọc completed job GP-C180I-1; adapter từ chối resume do identity/state không khớp |
| Review độc lập | Không còn Important/Critical; reviewer tự chạy lại 54 test pass |
| Backend suite rộng | **9.167 passed, 40 skipped, 5 failed, 1 collection error**, 294 warnings, 87,32 giây |

Test RED→GREEN đã chạy trước implementation. Review bắt được và đã sửa: job ID CUPS tái sử dụng phải kiểm tra title/queue/held state; lỗi identity bị trễ không được ghi đè state của worker khác. Test còn bao phủ pending/missing invoice, sai order/payment/invoice, duplicate/replay, hai worker, invoice dùng lại cho đơn khác, restart tại spool/release, held resume, PDF lỗi, subscriber không chờ HTTP, shutdown lưu queue.

Commands:

```bash
.venv/bin/python -m pytest tests/kiosk/test_cups.py tests/kiosk/test_printing.py tests/server/test_kiosk_printing.py tests/system/test_payment_trusted_hosts_wiring.py -q -p no:cacheprovider
.venv/bin/python -m pytest tests/ -n 4 -q --tb=short -m 'not live and not cloud and not hub and not nvidia and not amd and not modal and not live_external and not live_channel' -p no:cacheprovider
```

Không báo full suite pass. Các failure rộng:

- `tests/cli/test_cli.py::TestStartupResilience::test_importing_cli_does_not_import_numpy`: CLI import kéo NumPy; kiểm tra thêm cho thấy auto-print module chưa được load trong import này.
- `tests/integration/test_browser_capability.py::test_discovery_produces_mcp_tool_adapters`: browser tools set rỗng; có logging stream closed trong xdist.
- `tests/learning/test_device_selection.py::TestSelectTorchDevice::test_no_torch_returns_none`: nhận CUDA device thay vì None.
- Hai test `tests/pearl/test_model_converter.py`: converter loader gây dataclass `sys.modules` None trên Python 3.13.
- Collection `tests/evals/comparison/test_export_to_table_gen_roundtrip.py`: thiếu `polars`.

Không sửa các phần ngoài scope. Full log local: `.superpowers/sdd/2026-10-12-kiosk-auto-print/backend-suite.log`.

## Cấu hình và xử lý lỗi

Recipe hiện tại opt-in queue GP-C180I; chỉ khởi động worker khi kiosk enabled và recipe có invoice URL + queue. `KIOSK_PRINT_QUEUE` override tên queue; đặt chuỗi rỗng để tắt auto-print. Độ rộng cấu hình bằng `print_width_dots` trong recipe, cần khớp khổ in thực tế.

`waiting`: chờ merchant xác nhận/invoice hoặc retry lỗi trước spool. `held`: job đã có trong CUPS, chưa release. `submitted`: đã gửi release; không tạo bản gốc mới. `needs_review`: không tự in lại; xem error/job ID/title bằng diagnostics, kiểm tra CUPS và giấy trước thao tác operator. Với held job đúng title/queue và chắc chắn chưa in, operator có thể resume chính job đó trong CUPS; không restart completed job hoặc tạo bản sao để thử.

Restart: `submitting`/`releasing` chuyển `needs_review`; held job phải xác minh merchant/invoice và CUPS identity trước resume. Không suy ra timeout là chưa in. Không tự xóa/reset journal để reprint.

Giới hạn: registration được journal hóa ở worker ngay sau wake; hard-kill trước khi worker ghi có thể làm mất event chưa flush. Shutdown bình thường flush queue. CUPS/USB không đảm bảo exactly-once trên giấy khi mất điện/in một phần; application ngăn tự tạo/spool bản gốc trùng và giữ outcome không rõ để kiểm tra.

## Trạng thái runtime

Trước activation, launcher báo Vision/backend/frontend đều stopped. Theo yêu cầu “triển khai auto-print”, chạy `scripts/launcher.sh start` lúc **10:15:26 +07**. Vision phục vụ :9876 sau 6 giây, backend :8000 healthy sau 9 giây, frontend :5173 ready; kiosk/customer-display GET thành công, runtime running. Model gpt-6-luna; launcher giữ saved OpenAI effort presets.

GET `/api/kiosk/print-jobs` trả **`{"enabled":true,"jobs":[]}`**: worker đã active và journal chưa có đơn mới. CUPS vẫn chỉ có hai job lịch sử GP-C180I-1/-2 ngày 2026-10-09; không tạo job test, chuyển tiền hoặc in lại invoice cũ. Launcher có warning browser tools 0/0; health kiosk/display và auto-print diagnostics vẫn đạt, không sửa discovery ngoài scope.

Chưa thực hiện acceptance bằng giấy trên một giao dịch khách thanh toán thật. Chỉ khi có QR checkout mới được publish và merchant xác nhận paid/completed + invoice tồn tại thì worker mới submit CUPS job.

## Tự đánh giá

Chính xác 4/5: test và IPP/raster có bằng chứng, chưa test giấy. Đầy đủ 4/5: flow, dedup, recovery và wiring đủ; physical acceptance còn chưa chạy. Rõ ràng 4/5: phân biệt submitted với printed và ghi full-suite failures. Khả năng sử dụng 4/5: diagnostics/config/recovery cụ thể; operator cần đối chiếu giấy khi uncertain. Ngắn gọn 4/5: hai module native và wiring nhỏ, report giữ bằng chứng. Trung bình 4,0/5.
