# Kiểm chứng intent normalization

## Thay đổi

OrchestratorAgent có tùy chọn intent_normalization; mặc định tắt. Preset kiosk bật tùy chọn này. Core đọc tên intent và mục tiêu từ metadata của các tool hiện có, thêm trường _intent vào schema gọi tool, bỏ trường giao thức trước khi dispatch, rồi giữ intent đó đến khi mục tiêu hoàn tất hoặc cần hỏi thêm. Không có tool mới, tên merchant hay bộ nhận diện câu tiếng Việt trong core.

Các bước chuẩn bị không kết thúc sớm lượt có mục tiêu. Khi model còn thiếu dữ kiện, câu trả lời phải dùng trạng thái có cấu trúc; câu trả lời thường không được phát ra trước khi kiểm tra. Nếu lần gọi mục tiêu chưa rõ kết quả, chỉ tool được khai báo read_only mới được kiểm tra trạng thái; không tự gửi lại thao tác có thể tạo tác dụng phụ.

Metadata runtime_arguments cho phép một tool khai báo trường nào cần lấy từ runtime context đã xác thực. Benchmark dùng cơ chế này cho nonce phiên checkout. Nonce được gắn ngay trước khi gọi executor có sẵn, nên model không thể làm checkout hỏng bằng cách chép nhầm nonce; các guard của tool vẫn chạy bình thường.

Intent theo lĩnh vực được khai báo ngoài core: ví dụ ADD_TO_CART và CHECKOUT_NOW ở metadata skill của kiosk. Test dùng thêm EXPLAIN_LESSON, SUMMARIZE_REPORT và FINALIZE để kiểm tra cơ chế không phụ thuộc luồng cửa hàng. Các nhãn này kiểm tra điều phối, không phải đánh giá kiến thức y tế hay giáo dục của model.

## Model và API giả lập

Chạy model gpt-6-luna với HTTP merchant giả lập trong bộ benchmark. Không tạo đơn hay thanh toán thật. Có 27 lượt qua 9 kịch bản, mỗi kịch bản 3 lần; cả **27/27** đạt tiêu chí của kịch bản.

Hai câu được yêu cầu đều có cùng kết quả trong cả 3 lần chạy:

| Câu | Intent | Đường chạy | Số lượt model |
|---|---|---|---:|
| thanh toán 2 cà phê sữa mang đi | CHECKOUT_NOW | skill_trendcoffee-add-to-cart → skill_trendcoffee-checkout | 2 |
| bạn thanh toán 2 cà phê sữa mang đi | CHECKOUT_NOW | skill_trendcoffee-add-to-cart → skill_trendcoffee-checkout | 2 |

Trong fixture, cả hai đều tạo đơn mang đi với đúng 2 món, hoàn tất bước hiển thị QR và không báo đã thanh toán. Median phản hồi của 6 lượt checkout này là 7,789 giây; median của 27 lượt gồm mọi kịch bản là 3,827 giây. Đây là thời gian end-to-end từ model và fixture, không phải benchmark latency riêng của state machine.

Các kịch bản còn lại bao gồm phủ định, hành động đã xảy ra trong quá khứ, hỏi cách thanh toán, chỉ thêm giỏ, thiếu lựa chọn tại bàn/mang đi, xem giỏ và xem menu. Trong 3 lượt phủ định, model chọn NONE, không gọi tool có tác dụng phụ và không gửi yêu cầu merchant. Hành động cũ và câu hỏi trợ giúp cũng không tạo đơn. Chi tiết từng lượt nằm trong [live-runtime-bound.json](live-runtime-bound.json).

## Kiểm thử tự động

- Focused regression: **288 passed**.
- Ruff: All checks passed.
- Regression suite rộng trước chỉnh sửa transactional cuối: **1.247 passed, 15 skipped**.
- Full offline suite (loại cloud/live và module thiếu Polars): dừng ở mốc **36%**, với **3.330 passed, 12 skipped, 22 deselected**. Lỗi ghi nhận là `tests/cli/test_cli.py::TestStartupResilience::test_importing_cli_does_not_import_numpy`, một lỗi baseline đã xuất hiện trước thay đổi intent. Bộ suite chạy chậm ở nhóm kế tiếp nên đã dừng, không báo cáo là full pass.
- Các test focused chạy lại sau chỉnh sửa cuối: **288 passed**.
- Ruff và `git diff --check`: đều sạch.

Không chạy tests/skills/test_integration_live.py vì các test này cần cài skill và inference engine bên ngoài môi trường kiểm thử. Suite offline loại marker cloud/live và module cần Polars. Model benchmark vẫn gọi model inference; chỉ HTTP merchant được thay bằng fixture cục bộ.

## Giới hạn

Model vẫn là nơi diễn giải ngôn ngữ và chọn nhãn intent; normalization không thể đảm bảo mọi model hoặc câu mới sẽ luôn phân loại đúng. Runtime giữ nguyên nhãn sau khi được khai báo, buộc chạy hết mục tiêu, chặn phát câu thành công sớm và giữ nguyên các guard nghiệp vụ. Muốn xác nhận độ ổn định cho model, ngôn ngữ hoặc domain khác, cần thêm các bộ câu đại diện chạy qua model tương ứng.

Preset kiosk có bật tùy chọn trong cấu hình. Khi ghi báo cáo, backend, frontend và vision service đều đang dừng; chưa khởi động lại app.

## Sửa lỗi thêm món rồi lưu lựa chọn mang đi

Trace thực tế `2400a8881bac4e0da3066a9ece883910` ghi nhận một Đậu đỏ đá xay được thêm thành công, nhưng order_type vẫn rỗng. Tool trả continue_agent=true để xử lý tiếp. IntentState đã đánh dấu target hoàn tất rồi chặn các lần gọi tiếp theo; finish() trả nguyên JSON FAILED vì mục tiêu đã được đánh dấu đạt. Đây là lỗi điều phối và định dạng phản hồi trong core, không phải lỗi API merchant.

Core hiện cho phép bước tiếp theo khi target thành công nhưng tool yêu cầu tiếp tục; vẫn chặn gọi lại target đã hoàn tất và chặn chuyển sang target intent khác. Terminal envelope FAILED được đọc thành message tiếng Việt và không ghi nhận toàn bộ yêu cầu là hoàn tất. Prompt kiosk xác nhận lựa chọn mang đi sau khi trạng thái giỏ đã lưu nó.

SkillTool cũng trả execution_started=false cho lỗi validate tham số trước pipeline. Core dùng tín hiệu chung này để cho phép sửa tham số; kết quả chưa rõ sau khi đã thực thi vẫn chặn retry. Benchmark chỉ kiểm tra metadata intent khi tùy chọn normalization bật.

Kiểm chứng bổ sung: **381 test pass**, Ruff và diff check sạch. Ba lượt model với câu “Cho mình một cà phê sữa mang đi” đều đạt: một món, order_type=take-out, hai lượt model, không tạo đơn/thanh toán. Kết quả nằm trong [live-add-takeout.json](live-add-takeout.json). Test streaming xác nhận JSON FAILED không được đọc nguyên văn cho khách.

Backend thực tế đang chạy tại thời điểm kiểm tra trace; các sửa đổi này mới nằm trên disk và chưa được nạp vào tiến trình đang chạy.

## Kết thúc ngay sau phản hồi hiển thị đã xác nhận

Trace `8d34f43f723c4fccabc0a8593f9097f5` của câu “menu” mất 18,246 giây: 6 lượt model tổng cộng khoảng 17,733 giây, HTTP đọc menu 0,443 giây và display_menu 0,004 giây. Menu đã hiển thị tại khoảng 2,642 giây. Intent NONE vẫn bị ép tiếp tục và bỏ customer_message của tool; agent phải sinh lại terminal envelope dù đã có phản hồi được display xác nhận.

Core hiện giữ customer_message và kết thúc khi intent là NONE, tool thành công, completed_display=true, có message không rỗng, không có pending approval và không có yêu cầu continue_agent. Một lượt có mục tiêu hành động khác vẫn phải hoàn tất target. Tool lỗi, thiếu lời xác nhận hoặc yêu cầu tiếp tục không được dùng đường kết thúc này. Không có điều kiện dựa vào từ “menu” hay tên merchant trong core.

Kiểm chứng: 106 test agent/orchestrator đạt khi sửa; sau khi bổ sung case menu dùng skill thật, 36 test focused intent/skill đạt. Ruff, format check và diff check sạch. Benchmark model với HTTP fixture: ba câu lọc menu đều dùng một lượt model, median 4,219 giây (một lượt model vẫn mất 10,918 giây). Đúng câu “menu” mở toàn bộ fixture menu đạt trong một lượt model, 2,985 giây. Đây không phải phép so sánh latency production trực tiếp vì HTTP merchant của benchmark là giả lập.

Kết quả: [live-menu-fast-finish.json](live-menu-fast-finish.json), [live-menu-all-fast-finish.json](live-menu-all-fast-finish.json). Ở lượt sửa này các service đang dừng; backend sẽ nạp bản sửa khi được khởi động.

## Yêu cầu mua hoàn chỉnh đi tới checkout

Trace `be5262a3412f4b75b8f084b889415911` của câu “Bán cho mình một cà phê đen mang về” chọn ADD_TO_CART, chạy menu và hai thao tác giỏ rồi hỏi thêm về thanh toán. Prompt hướng dẫn draft trước đây áp dụng quá rộng, khiến yêu cầu mua cũng kết thúc ở giỏ. Hướng dẫn kiosk hiện chọn mục tiêu mua/đặt trước khi chọn tool; khi đủ món, số lượng và cách nhận, yêu cầu bán/mua/đặt đi tới CHECKOUT_NOW mà không cần khách nói thêm “ngay”, “thanh toán” hoặc “QR”. Skill add-to-cart chỉ là bước chuẩn bị của yêu cầu mua này.

Người dùng chọn chính sách: câu đầy đủ như “Cho mình một cà phê sữa mang đi” cũng tạo đơn và QR luôn. ADD_TO_CART dành cho yêu cầu giữ ở giỏ, chọn thêm hoặc chưa thanh toán. Câu hỏi “quán có bán…?” và phủ định không tạo đơn. Scenario add_takeout trong benchmark hiện nói rõ “thêm vào giỏ, chưa thanh toán”; câu “cho mình…” được kiểm chứng riêng như một yêu cầu mua. Đây là thay đổi chính sách được người dùng chọn, không phải đổi kỳ vọng để che lỗi benchmark.

Thay đổi nằm trong prompt kiosk và mô tả skill merchant hiện có; không thêm tool hoặc sửa core agents ở lượt này. Test TOML/preset/skill: **151 passed**, Ruff và diff check sạch. Nhóm bán/mua/đặt với các cách nói và menu đã hiển thị đạt **15/15** trong fixture. Sau khi chốt chính sách cho câu “cho/lấy”, bộ cuối đạt **16/16**, gồm tám lượt mua và tám lượt kiểm tra câu hỏi/phủ định/giỏ-only. Món đã xác thực trên màn hình checkout trong một lượt model; món chưa xác thực chạy add-to-cart rồi checkout trong hai lượt model. HTTP merchant của toàn bộ benchmark là giả lập; không tạo đơn thật khi kiểm chứng.

Kết quả: [live-explicit-sale.json](live-explicit-sale.json), [live-sale-final.json](live-sale-final.json). File [live-explicit-sale-controls.json](live-explicit-sale-controls.json) giữ lại hai kết quả “cho mình…” không khớp kỳ vọng draft cũ, trước khi người dùng chọn chính sách mới.

Đã nạp cấu hình mới bằng launcher restart --no-vision. Backend và frontend khởi động lại, health đều ok; Vision vẫn chạy với PID 23751. Khi khách thử lại một yêu cầu mua đủ thông tin, app dùng hướng dẫn mới và tạo đơn/QR qua các guard checkout hiện có. QR vẫn là thanh toán đang chờ xác nhận.
