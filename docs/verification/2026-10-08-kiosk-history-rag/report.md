# History + RAG kiosk — triển khai và kiểm chứng 2026-10-08

Cập nhật sau code review: hai heuristic regex/query rewriting bên dưới đã được bỏ theo yêu cầu. Xem [báo cáo hiện tại](raw-query-report.md) và [số đo query nguyên vẹn](raw-query-retrieval.json). Nội dung và số đo bên dưới lưu lại phiên bản triển khai ban đầu.

## Kết quả

Backend và frontend đã chạy lại với preset `ordering-kiosk-mcp.toml`. `/health` trả `ok`, kiosk runtime báo running, `/v1/memory/config` báo SQLite available và context_from_memory=true; `/v1/memory/stats` xác nhận 7 records. Database riêng: `~/.openjarvis/trendcoffee-rag.db`. Chỉ index 7 tài liệu trong manifest; index lặp lại vẫn có 7 records. Giới hạn recall: 3 đoạn / 800 token xấp xỉ. Automatic personal facts tắt; session persistence bật.

Vision không lắng nghe tại cổng 9876 trước khi triển khai. PID file cũ trỏ vào process Codex, không phải Vision; khởi động backend/frontend bằng `scripts/launcher.sh restart --no-vision` để tránh kill nhầm. Voice availability báo Gemini Live enabled; điều này chưa chứng minh audio/Gemini connection, ASD lock hoặc TSE ready.

## Thay đổi

- Voice giữ history của context hiện tại, tối đa 4096 token xấp xỉ theo lượt user hoàn chỉnh; giữ nguyên lượt mới nhất khi riêng lượt đó vượt ngân sách. Giữ tool-call/result IDs, không thay đổi conversation gốc hoặc tải lịch sử khách cũ.
- Recall chạy ngoài event loop, deadline 100 ms và tối đa một lookup chưa hoàn tất trong mỗi voice service. Lookup lỗi/quá hạn giữ history, không áp dụng kết quả trễ cho lượt mới, báo agent chưa xác minh được reference.
- Binding Rust SQLite nhả GIL trong `retrieve`; extension đã rebuild và cài vào đúng `.venv` chạy backend. Chỉ đưa sang Python thread chưa đủ: reproducer trước sửa có heartbeat 20 ms chạy trễ tới khoảng 633 ms khi DB bị khóa.
- Query bỏ các cụm đệm hội thoại hoàn chỉnh; không bỏ các từ đơn có thể là tên như Long. Câu nối tiếp ngắn có đại từ bổ sung lượt user trước vào truy vấn, không đổi nguyên văn input của agent.
- Merchant prompt cho phép trả lời FAQ có nguồn, giữ API/prepared tools làm căn cứ giá/tồn kho/giỏ/đơn/QR/thanh toán. Không tạo knowledge từ facts cá nhân hoặc catalog thô.
- `security.public_contact_values` cho phép đúng hotline/email doanh nghiệp đã xác minh qua PII scanner. Tests kiểm tra private phone/secret vẫn bị che, cards và địa chỉ email chứa chuỗi public không được miễn trừ. Ngoại lệ mặc định rỗng; số cấu hình dùng chữ số liền nhau. Các tests này xác nhận đường `generate()`; không khẳng định thay đổi bảo vệ streaming post-hoc sẵn có.

## Đo retrieval và latency

| Phép đo | Kết quả | Phạm vi |
|---|---|---|
| Đúng nguồn đầu tiên | 21/24 | Bộ câu cố định, bao gồm một câu ngoài miền |
| Đúng nguồn trong top 3 / có trong injected context | 24/24 | Tiếng Việt, không dấu, câu nối tiếp, giới hạn thông tin |
| Recall/context SQLite warm p50 / p95 | 0,260 / 0,375 ms | 456 mẫu; 7 documents |
| Chuẩn bị context voice async p50 / p95 | 1,472 / 1,660 ms | 99 mẫu warm, thêm 1 mẫu đầu 3,061 ms |
| HTTP retrieval p95 lớn nhất trong 4 query | 4,349 ms | 25 request/query, HTTP trên localhost |
| Nội dung trả lời đầu tiên qua agent HTTP | 2,256–4,474 s | 8 câu/lượt tổng hợp, model `gpt-6-luna` |

Các số liệu là phép đo trên máy này với tập nhỏ. First context trong script đi sau một lần đọc ranking, không phải phép đo cold disk cache. Token budget dùng khoảng trắng, không phải tokenizer của model. Latency HTTP agent đo lúc SSE có content; nhiều lượt phát toàn bộ câu trả lời trong một đoạn. Chưa đo microphone → STT → agent → TTS hoặc tải nhiều khách đồng thời.

## Kiểm tra câu trả lời model

6 câu FAQ và 2 lượt kiểm tra history, dùng văn bản tổng hợp, không dùng capture khách:

- Hotline hỗ trợ và đặt tiệc trả đúng số, khác nhau theo mục đích.
- Không gán giờ hotline thành giờ mở cửa.
- Không hứa thời hạn xóa hoặc xóa toàn bộ dữ liệu khi nguồn mâu thuẫn.
- Thanh toán đã trừ tiền: hướng dẫn giữ biên lai và liên hệ hỗ trợ, không xác nhận đơn đã trả tiền.
- Phường địa chỉ: giữ rõ mâu thuẫn Bình Thọ/Hiệp Phú.
- Lượt workshop → “Số điện thoại đó?” giữ số đặt tiệc. Request mới hỏi hỗ trợ chung trả số hỗ trợ.

Tám câu trả lời đều đáp ứng các điều kiện trên theo kiểm tra nội dung của agent thực hiện khảo sát. Không có tool call trong SSE quan sát. Đây chưa phải đánh giá độc lập trên nhiều mẫu hoặc mọi cách diễn đạt. Đại từ không dấu như “do/ay/vay” chưa được contextual expansion xử lý; các câu không dấu trực tiếp đã có trong bộ retrieval.

## Kiểm thử và review

- Các regression mới đã được chạy FAIL trước khi sửa: query đệm, giới hạn history/tool IDs, slow recall, contextual follow-up, native GIL lock, tìm tên Long và ngoại lệ public contacts.
- Focused suites sau sửa cuối: **763 passed, 14 skipped**; Ruff lint/format và diff checks đạt cho files thay đổi.
- Review độc lập tìm hai lỗi GIL và pruning từ đơn; cả hai đã sửa và review lại không còn Critical/Important. Review bổ sung public contacts không còn Critical/Important.
- Full suite cuối: **8869 passed, 44 skipped, 5 failed, 1 collection error**, 199,14 s. Command: `PYTHONDONTWRITEBYTECODE=1 PYTEST_XDIST_AUTO_NUM_WORKERS=2 env -u FORCE_COLOR .venv/bin/python -m pytest tests/ -n auto -q --tb=line -p no:cacheprovider -m 'not live and not cloud and not hub'`. Log: `/tmp/kiosk-rag-full-tests-final-20261008.log`.

Các failures đã xuất hiện trong handoff Target audio trước tác vụ này:

1. `tests/cli/test_cli.py::TestStartupResilience::test_importing_cli_does_not_import_numpy`
2. `tests/learning/test_device_selection.py::TestSelectTorchDevice::test_no_torch_returns_none`
3. `tests/integration/test_browser_capability.py::test_discovery_produces_mcp_tool_adapters`
4. `tests/pearl/test_model_converter.py::test_converter_quantizes_linear_weights_and_writes_pearl_config`
5. `tests/pearl/test_model_converter.py::test_converter_adds_gemma4_preprocessor_compat_file`
6. Collection: `tests/evals/comparison/test_export_to_table_gen_roundtrip.py` thiếu `polars`.

Không xem toàn suite là xanh. Chưa sửa các lỗi ngoài phạm vi trên.

## Bằng chứng và chạy lại

- [Retrieval/context](retrieval.json), [HTTP retrieval](http-retrieval.json), [voice context](voice-context.json).
- [6 câu trả lời](answers.json), [history/fresh request](history-answers.json); tất cả là văn bản tổng hợp.
- Index + benchmark: `.venv/bin/python scripts/benchmark_kiosk_rag.py --index --output docs/verification/2026-10-08-kiosk-history-rag/retrieval.json`.
- Native regression: `.venv/bin/python -m pytest tests/server/test_voice_llm.py::test_native_sqlite_lock_does_not_block_voice_deadline -q`.
- Native rebuild: `VIRTUAL_ENV=/home/metek/Projects/jarvis/OpenJarvis/.venv .venv/bin/maturin develop --release -m rust/crates/openjarvis-python/Cargo.toml`.
- Logs: `/tmp/kiosk-rag-final-focused-tests-20261008.log`, `/tmp/kiosk-rag-native-build-20261008.log`, `/tmp/kiosk-rag-launch-20261008.log`.

Rollback recall: đặt `agent.context_from_memory=false` trong preset rồi restart backend lúc phù hợp; database corpus được giữ để bật lại. Không xóa/reset database memory chung. Đổi tên/xóa file corpus cần dọn source cũ riêng; script chưa tự xóa source bị bỏ khỏi manifest. Không stage/commit trong tác vụ này; thay đổi Vision và diagnostic cũ được giữ nguyên.
