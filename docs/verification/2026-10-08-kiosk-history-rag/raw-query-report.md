# Bỏ heuristic regex khỏi truy vấn RAG — 2026-10-08

Theo yêu cầu giữ truy vấn nguyên vẹn:

- `inject_context` truyền trực tiếp `query` cho backend; bỏ regex/danh sách đại từ và ngưỡng 12 từ. History vẫn nằm trong conversation của agent.
- `SQLiteMemory.retrieve` truyền nguyên chuỗi cho Rust; bỏ regex cụm hội thoại, lowercasing, Unicode normalization và tokenization/deduplication bổ sung ở Python. Kiểm tra query trắng chỉ phục vụ trả kết quả rỗng; FTS tokenization an toàn vẫn do Rust thực hiện.
- Script benchmark dùng đúng truy vấn hiện tại, không tự ghép câu trước để đo một đường khác runtime.
- Giữ hỗ trợ `~` cho đường dẫn database, chỉ mục đã nạp và deadline/GIL fix của voice recall.

## Kiểm chứng

Hai regression tái hiện FAIL trước sửa và PASS sau sửa:

1. Literal `La Gi` và `Vui Long` tìm được document đã lưu, không bị xóa khỏi query.
2. Câu mới hỏi hoàn tiền không bị ghép chủ đề đặt tiệc cũ và lấy sai document.

Suite liên quan: **242 passed, 14 skipped**, 10,78 s. Command: `.venv/bin/python -m pytest tests/memory tests/server/test_voice_llm.py tests/server/test_routes.py tests/cli/test_memory_cmd.py -q -p no:cacheprovider`. Log: `/tmp/kiosk-rag-raw-query-tests-20261008.log`. Lint/format và diff checks đạt. Full suite không chạy lại trong chỉnh sửa này; kết quả rộng ở báo cáo trước thuộc phiên bản trước khi bỏ regex.

[Đo retrieval hiện tại](raw-query-retrieval.json): 7 documents, 24 câu cố định, **22/24 top-1**, **24/24 top-3 và injected context**. 456 mẫu warm context: p50 **0,238 ms**, p95 **0,549 ms**. Đây là đo retrieval/context, không phải mic → loa.

Các câu nối tiếp chỉ còn history cho model hiểu ngữ cảnh; lớp retrieval không tự giải đại từ hoặc diễn giải lại truy vấn. Kết quả bộ 24 câu không chứng minh mọi câu nối tiếp đều tìm đúng tài liệu. Không thêm classifier, regex khác hoặc lời gọi model để viết lại truy vấn.

Backend/frontend được reload bằng launcher `restart --no-vision`; chỉ mục riêng vẫn là `~/.openjarvis/trendcoffee-rag.db`. Không stage/commit hoặc sửa các diagnostic/Vision ngoài phạm vi.
