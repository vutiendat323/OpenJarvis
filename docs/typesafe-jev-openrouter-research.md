# TypeSafe Jev qua OpenRouter: ghi chú nghiên cứu (2026-09-22)

## Kết luận cho OpenJarvis

Jev phù hợp với **quyết định hẹp, có tập đáp án định trước** trong luồng agent; Luna vẫn cần cho Chat/Voice, diễn giải, lập kế hoạch và tool-calling. TypeSafe gọi đây là mô hình System One: đầu vào là trạng thái văn bản/JSON, đầu ra là câu trả lời có kiểu cùng xác suất; Jev không tạo văn bản trả lời, mã, hay giải thích. Nó chưa nhận ảnh, âm thanh hoặc video. [TypeSafe System One](https://docs.typesafe.ai/concepts/system-one)

Ba primitive:

| Primitive | Câu hỏi phù hợp | Trường đầu ra chính |
| --- | --- | --- |
| `choice` | Chọn đúng một nhãn trong tập cố định, như nhóm công cụ cần xử lý | `choice`, `probabilities`, `confidence` |
| `score` | Chấm mức độ theo các mốc có thứ tự, như mức khẩn cấp | `score`, `probabilities`, `confidence` |
| `noul` | Mệnh đề đúng/sai, như người dùng có đang yêu cầu trợ giúp trực tiếp hay không | `noul` (xác suất đúng; **không có** `confidence` riêng) |

Nguồn: [Choice](https://docs.typesafe.ai/primitives/choice), [Score](https://docs.typesafe.ai/primitives/score), [Noul](https://docs.typesafe.ai/primitives/noul). `confidence` không bảo đảm từng dự đoán đúng; TypeSafe mô tả calibration trên *nhóm* dự đoán. [TypeSafe System One](https://docs.typesafe.ai/concepts/system-one), [Confidence](https://docs.typesafe.ai/confidence)

## Giao diện OpenRouter

OpenRouter niêm yết ID cố định `typesafe/jev-1.13` và alias cập nhật `~typesafe/jev-latest`. Với hành vi production cần ổn định, ưu tiên bản cố định; alias có thể đổi phiên bản. Trang OpenRouter niêm yết ngữ cảnh 32K, giá hiện tại $0.042/triệu input token và $0/triệu output token; cả giá và thông số có thể thay đổi. TypeSafe trực tiếp mô tả giới hạn khác chi tiết hơn: tối đa 64K cho toàn request nhưng 32K cho `state` cộng câu hỏi dài nhất; không suy ra giới hạn 64K đó được OpenRouter áp dụng y hệt. [Danh mục TypeSafe trên OpenRouter](https://openrouter.ai/typesafe), [trang Jev 1.13 trên OpenRouter](https://openrouter.ai/typesafe/jev-1.13), [TypeSafe Models](https://docs.typesafe.ai/models)

Quan trọng: Jev dùng giao diện **System One/Decisions**, không nên thay model trong luồng `chat.completions` rồi mong có câu trả lời chat. Với OpenJarvis viết bằng Python, đường tích hợp có tài liệu trực tiếp là `TypeSafeClient(api_key=OPENROUTER_API_KEY, base_url="https://openrouter.ai/api")` rồi `client.system_one(model="jev-1.13", state=..., questions=...)`. SDK gửi `POST https://openrouter.ai/api/v1/systemone`; OpenRouter ánh xạ `jev-1.13` thành `typesafe/jev-1.13`, `jev-latest` thành `~typesafe/jev-latest`. `answers` chứa kết quả theo ID câu hỏi. [OpenRouter TypeSafe SDK integration](https://openrouter.ai/docs/guides/community/typesafe-sdk)

Đường khác trong TypeScript SDK là `openRouter.alpha.decisions.create({ decisionsRequest: { model: "typesafe/jev-1.13", state, questions } })`. Đây là giao diện được đánh dấu `alpha`; không cần dùng nó nếu Python System One SDK đã đáp ứng nhu cầu. [OpenRouter Alpha.Decisions](https://openrouter.ai/docs/client-sdks/typescript/sdks/decisions/README), [OpenRouter Jev demo](https://openrouter.ai/labs/jev/compile)

Thiết kế áp dụng *đề xuất, chưa được triển khai/đo*: đặt Jev sau STT cho một số quyết định phân loại có tiêu chí rõ ràng, ví dụ `choice` chọn nhóm xử lý yêu cầu, `noul` phát hiện cần chuyển người thật, hoặc `score` đánh giá độ khẩn cấp. Gộp các câu hỏi cần thiết vào một request; TypeSafe nói các câu hỏi trong một request được xử lý song song, nhưng câu hỏi thừa vẫn tăng token. Kết quả Jev chỉ là tín hiệu; mã OpenJarvis giữ quyền chọn nhánh và kiểm tra điều kiện cứng. Không dùng xác suất Jev để tự cấp quyền ghi đơn hàng, thanh toán hay hành động khó đảo ngược. [TypeSafe Choice](https://docs.typesafe.ai/primitives/choice), [How to build with TypeSafe](https://docs.typesafe.ai/concepts/how-to-build-with-system-one)

## Latency và accuracy: điều đã biết/chưa biết

Trang model OpenRouter hiển thị P50 khoảng 0.27 giây cho nhà cung cấp tốt nhất khi tra cứu, nhưng đây là số liệu động theo cửa sổ/vị trí, **không phải** độ trễ đầu-cuối của OpenJarvis tại Việt Nam hay SLA. TypeSafe tự công bố 70–500 ms cho dịch vụ trực tiếp và so sánh benchmark workflow của họ; đây là số liệu do chính nhà cung cấp đo, không được phép suy rộng thành hiệu quả trên dữ liệu Kiosk tiếng Việt. [OpenRouter Jev 1.13](https://openrouter.ai/typesafe/jev-1.13), [TypeSafe introduction và caveats](https://typesafe.ai/blog/introducing-system-one-models-and-jev), [TypeSafe workflow evals](https://evals.typesafe.ai/)

Thêm một lời gọi Jev nối tiếp trước mọi lời gọi Luna có thể **tăng** latency Chat/Voice. Jev chỉ có khả năng giảm thời gian/chi phí nếu nó thay thế một bước phân loại Luna hiện hữu, tránh được một vòng LLM lớn, hoặc chạy song song với công việc độc lập. Cần đo P50/P95 request và toàn vòng, tỷ lệ fallback, độ đúng theo tập mẫu tiếng Việt gắn nhãn (bao gồm nhập nhằng, giọng nói/STT lỗi, và thao tác nhạy cảm). Không có kết quả accuracy riêng cho OpenJarvis; xác suất được hiệu chuẩn không đồng nghĩa với đúng tuyệt đối. Evals do TypeSafe công bố đo độ khớp với nhãn **đồng thuận của hai mô hình lớn**, không phải ground truth độc lập; đó không phải chứng cứ accuracy trên tác vụ của OpenJarvis. Đây là suy luận kiến trúc dựa trên hợp đồng API, chưa phải kết quả benchmark của repo. [TypeSafe System One](https://docs.typesafe.ai/concepts/system-one), [TypeSafe workflow evals](https://evals.typesafe.ai/)

TypeSafe xác nhận tiếng Anh là ngôn ngữ huấn luyện chính và có độ chính xác tốt nhất; các ngôn ngữ khác được xử lý nhưng không đồng đều. Vì vậy dữ liệu Kiosk/Voice tiếng Việt phải được đánh giá riêng trước khi Jev điều khiển nhánh quan trọng. [TypeSafe Models: language support](https://docs.typesafe.ai/models)

Không gửi inference, không thay key/guardrail/BYOK hay cấu hình runtime khi lập ghi chú này.
