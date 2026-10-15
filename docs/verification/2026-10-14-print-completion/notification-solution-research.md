# Nghiên cứu thông báo sau khi in: giữ Agent và giảm độ trễ

Ngày kiểm tra: 2026-10-14. Đây là nghiên cứu và đề xuất; chưa sửa code, cấu hình, benchmark hay dịch vụ. Nguồn gồm code hiện tại, OpenAI Docs, Pipecat và IPP/CUPS.

## Kết luận

Cập nhật quyết định cuối trong hội thoại: user chấp nhận **chọn model riêng cho event-turn**, sau khi xác nhận lượt khách tiếp theo vẫn dùng model Agent chính. Vì vậy đề xuất giữ phương án B bên dưới, dùng model từ cấu hình, không gán cứng model. Việc ép thinking off là một thay đổi riêng; sự chấp nhận phạm vi model chưa xác nhận thay đổi effort này. Đề xuất effort tiếp tục do operator quyết định. Độ trễ cần được đo từng chặng; chưa đủ bằng chứng để hứa Luna `medium` hoặc `low` phát tiếng dưới một giây.

```text
CUPS xác nhận đúng job hoàn tất
  → InvoiceAutoPrinter → EventBus
  ├→ cập nhật Order Status ngay
  └→ NativeAgentRuntime → ActionLedger → BackgroundReporter
       → event-turn khi voice rảnh → cùng Agent, model thông báo từ cấu hình
       → TTS → loa

Lượt xem menu/đặt món tiếp theo → Agent/model chính từ launcher → suy luận và gọi tool
```

Luồng này đã có trong code. Runtime ghi kết quả theo conversation; reporter chuyển kết quả thành event input và chỉ đánh dấu đã báo khi event-turn hoàn thành. UI không cần chờ model. [app.py:153](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/app.py:153), [runtime.py:126](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/agents/runtime.py:126), [background.py:79](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/background.py:79).

## Ai tạo lời thông báo?

| Phương án | Người tạo câu nói | Đánh giá theo yêu cầu hiện tại |
| --- | --- | --- |
| A. Agent với binding đang chọn | Agent, model/effort chính của operator | Phương án đáp ứng yêu cầu gốc nếu cần mọi lượt dùng cùng model. |
| B. Agent đổi sang model cấu hình của Responder | Vẫn Agent; chỉ đổi model cho lượt đó | User đã chấp nhận phạm vi này ở cập nhật cuối. Giữ workflow, prompt, trace và event-turn; không gọi Responder trực tiếp. Effort là chính sách riêng. |
| C. Responder nhận kết quả rồi thông báo | Responder chịu trách nhiệm diễn đạt | Pattern hợp lệ nếu được chọn rõ ràng; chưa có hợp đồng thông báo này trong Responder hiện tại. |

Phương án B nằm ở `run_event_turn`: `with_model(self._responder.model)`. Orchestrator còn lấy `reasoning_off_options` khi có `voice_event_turn`. Cùng runtime không có nghĩa là cùng model/effort. [llm.py:749](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/llm.py:749), [orchestrator.py:373](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/agents/orchestrator.py:373).

Phương án C có cơ sở từ **Responder–Thinker / Chat-Supervisor**: một thành phần nói chuyện diễn đạt kết quả do thành phần xử lý cung cấp. Đó là vai trò có giao diện rõ ràng, không phải âm thầm thay model của Agent. [OpenAI voice prompting](https://developers.openai.com/api/docs/guides/voice-prompting#rephrase-supervisor-tool-responder-thinker-architecture), [OpenAI demo Chat-Supervisor](https://github.com/openai/openai-realtime-agents#agentic-pattern-1-chat-supervisor).

Tuy nhiên, `VoiceResponder.acknowledgement` hiện coi input là lời khách, chỉ nhận prompt Responder và hội thoại rút gọn, cấm khẳng định kết quả. Nó gom toàn bộ stream rồi mới trả câu nói. Gọi thẳng hàm này bằng `PRINT_COMPLETED` sẽ sai ngữ nghĩa; chưa chứng minh nhanh hơn event-turn. Muốn C cần hợp đồng nhận **verified event**, prompt thông báo riêng, ownership/cancellation/deduplication/trace và effort do operator chọn; vẫn dùng pipeline voice hiện có. Đây là thay đổi vai trò, cần user chọn rõ ràng. [responder.py:136](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/responder.py:136), [voice-responder.md:3](/home/metek/Projects/jarvis/OpenJarvis/configs/openjarvis/prompts/voice-responder.md:3).

## Model và effort

OpenAI xác nhận cả `gpt-5.6-luna` và `gpt-6-luna` hỗ trợ `low`, `medium` cùng các mức khác. Effort thấp thường giảm reasoning và thời gian trả lời; không đảm bảo mức latency cố định. [GPT-5.6 Luna](https://developers.openai.com/api/docs/models/gpt-5.6-luna), [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna), [reasoning effort](https://developers.openai.com/api/docs/guides/reasoning#reasoning-effort).

Các cờ `--openai-effort gpt-6-luna=medium` đặt effort **cho model đó**, không chọn model. Launcher lấy model chính từ `OPENJARVIS_MODEL`, mặc định `gpt-6-luna`; đặt effort cho cả hai Luna không tạo hai Agent. Ở B, riêng thông báo lấy model từ `voice.responder.model`; lượt khách tiếp theo vẫn dùng binding chính. Nếu model thông báo cũng là Luna, đề xuất lấy effort đúng preset Luna của operator; nếu là provider khác, lấy preset tương ứng, không ép thinking off vì loại event. Backend Responses hiện lấy effort từ operator settings, chỉ nhận `low/medium/high`; loại bỏ caller `reasoning_effort` rồi dùng cấu hình operator. Không cần mở rộng các mức để giải quyết mục tiêu này. [launcher.sh:60](/home/metek/Projects/jarvis/OpenJarvis/scripts/launcher.sh:60), [llm.py:770](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/llm.py:770), [cloud.py:142](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/engine/cloud.py:142), [cloud.py:2014](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/engine/cloud.py:2014).

Responder hiện tự lấy reasoning-off options khi tạo lời mở đầu; hành vi đó đã có trước thay đổi thông báo. Đừng nhầm nó với override mới cho Agent event-turn. Báo cáo này không đề xuất sửa luồng lời mở đầu ngoài phạm vi. [responder.py:129](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/responder.py:129).

## Những chặng có thể chậm

| Chặng cần đo | Bằng chứng hiện tại | Hướng xử lý nếu đo thấy chậm |
| --- | --- | --- |
| CUPS hoàn tất → worker thấy kết quả | Worker mặc định poll 2 giây; còn có thời gian query/xử lý. | Poll CUPS nhanh hơn cho job đã submitted, giữ nhịp merchant payment; vẫn cùng worker. |
| Event → voice được phép báo | Lifecycle chờ khách/bot nói và lượt trả lời; có reply grace. | Giữ ưu tiên khách; ghi thời gian hoãn riêng. |
| Runtime admission → chữ đầu tiên | Trace có queue wait và first text. | Xác định chờ runtime hay inference; operator có thể thử cùng Luna ở `low`. |
| Chữ đầu → câu đầu đưa vào TTS | Pipeline hiện dùng sentence aggregation mặc định. | Câu đầu ngắn, có kết thúc câu; đo aggregation trước khi đổi chunking. |
| TTS → audio → loa | VieNeu render theo stream; transport và thiết bị còn độ trễ. | Đo warm renderer, TTS first audio và playback thực tế. |

Nguồn: [printing.py:38](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/kiosk/printing.py:38), [printing.py:121](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/kiosk/printing.py:121), [lifecycle.py:214](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/lifecycle.py:214), [collector.py:118](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/traces/collector.py:118), [pipeline.py:335](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/pipeline.py:335), [tts_engine.py:291](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/tts_engine.py:291).

Nếu job hoàn tất ngay sau một lần kiểm tra, riêng nhịp poll có thể thêm gần 2 giây; đây là suy luận từ lịch worker, chưa phải số đo live. Hằng số 120 ký tự trong voice/runtime không được truyền vào `VieNeuTTSService` ở pipeline này, nên chưa thể quy lỗi cho ngưỡng đó. [runtime.py:16](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/runtime.py:16), [Pipecat TTS](https://docs.pipecat.ai/pipecat/learn/text-to-speech).

OpenAI khuyến nghị streaming, câu trả lời ngắn, ít request và prefix ổn định. Áp dụng trong cùng Agent; không cần thêm model/service. Không tự chuyển sang token TTS: VieNeu hiện nhận đoạn text rồi render audio, nên cần kiểm chứng chất lượng và khả năng render liên tục. [OpenAI latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization), [tts.py:72](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/tts.py:72).

## Giới hạn bằng chứng cũ

Benchmark trước chỉ có 3 cặp, `tools=[]`, history rỗng; đo **chữ Agent đầu tiên**, chưa đo loa. Script không ghi effective effort/operator effort file. Vì vậy số `2,59–7,38 s` so với `0,62–1,00 s` không chứng minh Luna `medium` chậm hơn `low`, cũng không xác nhận phương án B đạt mục tiêu live. [Báo cáo cũ](notification-latency.md), [script:21](/tmp/jarvis-notification-latency-20261014.py:21). File `/tmp` có thể bị dọn.

IPP cho phép trạng thái `completed` có cảnh báo/lỗi; forwarding server có thể dùng `completed` kèm `queued-in-device`. Phải giữ kiểm tra job ID/name/queue và explicit successful reason. Không được bỏ xác minh để giảm latency. CUPS success là bằng chứng server cung cấp; độ tin cậy với giấy thực tế vẫn cần kiểm tra đúng queue/backend. [RFC 8011 §5.3.7–5.3.8](https://www.rfc-editor.org/rfc/rfc8011.html#section-5.3.7), [cups.py:120](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/kiosk/cups.py:120).

## Đề xuất sửa tối thiểu, chưa thực hiện

1. Theo lựa chọn cuối của user, giữ binding model riêng cho event-turn từ cấu hình và giữ nguyên binding chính cho các lượt khách. Đề xuất bỏ đoạn reasoning-off dành riêng cho event để effort theo operator; chưa thực hiện. Giữ runtime recorder, EventBus, reporter, lifecycle và business prompt. Nếu sau này user chọn A, chỉ khi đó trả event về binding chính.
2. Giữ lời nói do model tạo: hóa đơn sẵn sàng, chờ khoảng 15 phút/nhận tại quầy, hỏi menu hoặc đơn mới; tránh lặp nhắc này ở payment và chờ khách chọn trước khi thao tác. [ordering-kiosk.md:95](/home/metek/Projects/jarvis/OpenJarvis/configs/openjarvis/prompts/ordering-kiosk.md:95).
3. Đo trước. Nếu inference chậm, thử `low` qua operator. Nếu poll chậm, rút khoảng kiểm tra CUPS trong worker hiện có; không tăng merchant polling. Nếu aggregation/render chậm, sửa chính chặng đó sau thử nghiệm.

CUPS có subscription/notifier chuẩn, nhưng thêm cơ chế subscription/recovery lúc này chưa có bằng chứng cần thiết. Reuse polling hiện có là sửa nhỏ hơn. [CUPS design](https://openprinting.github.io/cups/doc/spec-design.html).

## Đo và tiêu chí nghiệm thu

Reuse TraceCollector và Pipecat metrics. Pipeline đã bật metrics; Pipecat 1.8.1 TTS base có hooks aggregation, TTFB/TTFA nhưng cần `can_generate_metrics=True`. VieNeu hiện kế thừa mặc định False: chưa thể nói các số TTS đã được thu. Đề xuất bật capability này và dùng observer/event metadata hiện có; không tạo service/timer trùng. UserBotLatencyObserver đo từ user speech; event in cần mốc riêng từ `PRINT_COMPLETED`. [pipeline.py:345](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/pipeline.py:345), [tts.py:19](/home/metek/Projects/jarvis/OpenJarvis/src/openjarvis/server/voice/tts.py:19), [TTSService 1.8.1](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/services/tts_service.py), [FrameProcessor 1.8.1](https://github.com/pipecat-ai/pipecat/blob/v1.8.1/src/pipecat/processors/frame_processor.py), [metrics](https://docs.pipecat.ai/pipecat/fundamentals/metrics), [UserBotLatencyObserver](https://docs.pipecat.ai/api-reference/server/utilities/observers/user-bot-latency-observer).

Ghi các mốc: CUPS success quan sát được, event phát, voice idle, runtime acquired, chữ đầu, TTS request, audio đầu, transport và tiếng thực tế. Ghi effective model/effort, history/tool count, warm/cold, ngôn ngữ; đo cùng cấu hình, tối thiểu 30 lần để có baseline ban đầu, báo median/p95 và số mẫu. Thời gian hoãn vì khách đang nói phải tách riêng.

| Kiểm tra | Tiêu chí |
| --- | --- |
| Model/effort | 100% lượt event dùng model thông báo từ cấu hình và effective effort đúng preset operator; không gọi Responder acknowledgement ở B. Các lượt khách tiếp theo giữ model/effort chính và tool workflow. |
| Print truth | Chỉ verified successful job phát completion; pending/unknown/failed không nói in xong, không submit lại. |
| Priority | Không chen khi khách/Agent đang trả lời; khách bắt đầu nói hủy event và không rò audio cũ. [Pipecat interruptions](https://docs.pipecat.ai/pipecat/fundamentals/interruptions). |
| Delivery/UI | Retry không thêm outcome trùng; đúng conversation/order; Order Status cập nhật độc lập inference. |
| Latency | Mục tiêu đề xuất: idle + warm event → tiếng đầu p95 ≤ 2 giây; event → UI p95 ≤ 250 ms. Chưa được đo/đạt; từng chặng phải có số riêng. |

Regression nên dùng fake model/CUPS cho ownership, duplicate, priority và kiểm tra giữ cấu hình; sau đó controlled live run đo CUPS/loa với cùng Luna/effort và tools/history thực tế. Giữ checkout/payment, không thêm đơn hay chuyển menu chỉ vì event. Nghiên cứu này không chạy live transaction, in giấy, paid inference, restart, commit hoặc push.
