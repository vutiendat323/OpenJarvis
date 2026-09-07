# Barge-in giữa lúc reasoning: vì sao Voice mode "đứng hình" (2026-09-05)

Nghiên cứu chẩn đoán, dựa trên log chạy thật `/tmp/openjarvis-stack/backend.log`
(backend pid 6614, phiên 17:26–18:13 ngày 2026-09-05) và mã nguồn hiện tại của
worktree `OpenJarvis-v2`. Không sửa code, không chạy test.

## Quan hệ với tài liệu đã có

`docs/architecture/voice-mode-interruption-and-tool-calling-patterns.md` (prior art)
đã bao quát tốt phần *nên làm gì*: không bao giờ hardware-mute mic, phân loại tool
idempotent vs mutating, và ý tưởng cancellation hook. Tài liệu này **giữ nguyên** các
kết luận đó.

Điểm khác biệt: prior art quy root cause của vụ treo 41 giây cho **"Lock Contention"**
— "*the lock could not be released until the 41s HTTP/LLM call completed*". Bằng chứng
log dưới đây **bác bỏ cơ chế đó**. Lock chưa bao giờ bị tranh chấp, kể cả một lần, trong
toàn bộ file log 500 KB. Con số `41.137s` mà prior art trích dẫn là thời gian lock được
**giữ** (`elapsed=`), không phải thời gian **chờ** (`after=`). Đây là chỗ tài liệu này
sửa lại và thay thế phần §5 của prior art.

---

## 1. Bằng chứng quyết định: không hề có lock contention

Quét toàn bộ log, chỉ có **đúng một giá trị duy nhất** cho thời gian chờ lock:

```text
$ grep -o "LOCK ACQUIRED after [0-9.]*s" backend.log | sort -u
LOCK ACQUIRED after 0.000s
```

Và số lần LLM service bị gọi lại khi đang bận:

```text
$ grep -c "RE-ENTERED" backend.log
0
```

Nghĩa là: mọi lượt barge-in đều **giành được `NativeAgentRuntime._lock` tức thì**
(`src/openjarvis/agents/runtime.py:153`), và `OpenJarvisLLMService.process_frame`
(`src/openjarvis/server/voice/llm.py:245`) không bao giờ bị re-enter chồng lượt.
Pipeline **không** deadlock, **không** bị nghẽn lock.

Trong khi đó thời gian **giữ** lock trải dài tới 41 giây:

```text
$ grep -o "LOCK RELEASED elapsed=[0-9.]*" backend.log | sort -t= -k2 -rn | head -6
LOCK RELEASED elapsed=41.137
LOCK RELEASED elapsed=36.902
LOCK RELEASED elapsed=36.457
LOCK RELEASED elapsed=29.700
LOCK RELEASED elapsed=27.802
LOCK RELEASED elapsed=27.234
```

Vấn đề vì vậy không phải "lượt sau không vào được", mà là **một lượt chạy quá lâu mà
không phát ra tiếng nào**.

## 2. Dựng lại đúng hai khoảnh khắc trong ảnh chụp màn hình

### 2.1 Màn hình "Dừng lại" — 36.9 giây im lặng tuyệt đối

```text
18:10:25.490  GATE-DEBUG tool=http_request ok=True status=201
              url=https://trendcoffee.net/api/latest/orders/public      ← ĐƠN ĐÃ ĐƯỢC TẠO
18:10:26.307  LLMUserAggregator#3: broadcasting interruption
              LOCK RELEASED elapsed=10.799s, input='Các em mình muốn đặt lại mình muốn đặt l'
18:10:26.311  VieNeuTTSService#3: Generating TTS [Dạ vâng, em đặt lại đơn mới: ...]
18:10:27.969  [Transcription:user] [Dừng lại.]
18:10:27.969  [Transcription:user] [ Dừng lại.]
18:10:27.970  [Transcription:user] [ Dừng lại.]
              TEMP-DIAG process_frame: START prompt='Dừng lại. Dừng lại. Dừng lại.'
              TEMP-DIAG _run_stream: LOCK ACQUIRED after 0.000s
          ↓   (36.9 giây — không một dòng log nào của agent/tool/TTS)
18:11:04.867  LLMUserAggregator#3: broadcasting interruption
              unsubscribe steps=2 input='Dừng lại. Dừng lại. Dừng lại.'
              LOCK RELEASED elapsed=36.902s
```

Khách nói **"Dừng lại"** ba lần liên tiếp — chính là hành vi của người tưởng máy đã
chết. Lượt đó chạy 36.9 giây, chỉ đạt `steps=2`, và bị barge-in kế tiếp huỷ **trước khi
kịp nói một chữ nào**. Không có dòng TTS nào cho lượt này.

### 2.2 Màn hình "alo, bạn có lắng nghe mình nói hay không?" — nói chữ đầu tiên ở giây thứ 27

```text
18:11:06.972  [Transcription:user] [alo, bạn có lắng nghe mình nói hay không?]
18:11:06.973  TEMP-DIAG process_frame: START
              TEMP-DIAG _run_stream: LOCK ACQUIRED after 0.000s     ← 0 giây chờ
          ↓   (24.19 giây — hoàn toàn trống, không tool, không text, không TTS)
18:11:31.163  GATE-DEBUG tool=http_request ok=True status=200 .../menu/specific/public
18:11:31.172  GATE-DEBUG tool=display_menu   ok=True content='presentation_published'
18:11:31.172  GATE-DEBUG tool=skill_manage   ok=True content='presentation_published'
18:11:34.206  LLMUserAggregator#3: broadcasting interruption
              unsubscribe steps=5
              LOCK RELEASED elapsed=27.234s
18:11:34.210  VieNeuTTSService#3: Generating TTS [Dạ, em nghe bạn nói rõ ạ. ...]  ← chữ đầu tiên
```

Ba tool chạy xong trong **9 mili-giây** (`18:11:31.163` → `18:11:31.172`). Tool **không
phải** thủ phạm. `18:11:06.973 + 27.234s = 18:11:34.207`, tức lượt này **kết thúc tự
nhiên**, chỉ trước tiếng nói đầu tiên đúng 3 ms — và trước đó là 27 giây câm lặng.

## 3. Root cause

> **Mọi `AgentTextDelta` sinh ra sau tool call đầu tiên đều bị giữ lại trong bộ đệm và
> chỉ được phát sau khi TOÀN BỘ vòng lặp ReAct kết thúc.** Với `max_turns = 30`, một
> lượt có tool = 24–41 giây không có âm thanh nào. Khách tưởng máy treo nên nói tiếp;
> mỗi lần nói tiếp lại huỷ lượt đang chạy và khởi động một lượt 27–40 giây mới.

Cơ chế nằm ở `OpenJarvisLLMService.stream_agent`
(`src/openjarvis/server/voice/llm.py:198-243`):

```python
async for event in self._binding.run_stream(prompt, context):
    if isinstance(event, AgentToolStarted):
        ...
        tool_round_started = True          # ← bật ở tool đầu tiên
    if isinstance(event, AgentTextDelta):
        if tool_round_started:
            later_round.append(event.content)   # ← ĐỆM LẠI, không phát
        else:
            speech = projector.push(event.content)
            if speech:
                yield LLMTextFrame(speech)
if tool_round_started:                     # ← chỉ xả sau khi vòng lặp KẾT THÚC
    projector = _SpeechProjector()
    for content in later_round:
        ...
```

Chủ đích của thiết kế này là đúng và được ghi rõ trong docstring: *"keeps tool-loop
drafts from being spoken as repeated answers"* — tránh việc đọc to từng bản nháp giữa
các vòng tool, lỗi đã từng khiến hệ thống đọc lặp cùng một đơn hàng. Nhưng hệ quả là:
nếu model **đi thẳng vào tool call mà không sinh chữ nào ở vòng đầu** (đúng trường hợp
lượt "alo": model gọi ngay `skill_manage`), thì `tool_round_started` bật lên khi chưa có
gì được phát, và từ đó tới hết lượt **không còn gì thoát ra TTS nữa**.

Nhân với `max_turns = 30` (`configs/openjarvis/examples/ordering-kiosk.toml:33`, kèm
chú thích *"max_turns is 30, not the default 10 -- a full order is several"*) và model
`qwen3.5:9b`, mỗi vòng là một lần inference đầy đủ. 24 giây trống ở §2.2 chính là chuỗi
inference đó.

**Đây là livelock do trải nghiệm, không phải deadlock kỹ thuật.** Cancellation của
Pipecat hoạt động đúng: `broadcast_interruption` phát ra, generator bị đóng, `finally`
chạy, lock nhả, lượt kế vào trong 0.000 giây.

### 3.1 Nguy cơ tiềm ẩn (chưa phát tác trong log này, nhưng có thật trong code)

Đường nhả lock bị hoãn sau các sync worker **không thể huỷ**:

1. `_run_stream` nhả lock qua `worker_lease.when_settled(_log_release)`
   (`src/openjarvis/agents/runtime.py:185`). `when_settled`
   (`src/openjarvis/agents/_stubs.py:100`) chỉ gọi callback ngay khi `self._pending`
   rỗng; nếu còn worker, việc nhả lock **bị hoãn**.
2. `AgentWorkerLease.run_sync` (`_stubs.py:59-71`) dùng `await asyncio.shield(task)`.
   Caller bị cancel **không** huỷ được thread; `_signal_cancelled()` chỉ bắn callback
   hợp tác (MCP dùng, `http_request` **không** đăng ký cái nào).
3. `ToolExecutor.execute` (`src/openjarvis/tools/_stubs.py:248-257`) dùng
   `with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:` rồi
   `future.result(timeout=timeout)`. Khi `TimeoutError` bật ra, `__exit__` của khối
   `with` gọi `shutdown(wait=True)` **trước** khi tới `except` — nghĩa là **`timeout`
   không thực sự chặn được cuộc gọi**; nó vẫn đợi thread chạy xong.

Chuỗi 1→2→3 nghĩa là: barge-in đúng lúc một `http_request` chậm đang chạy sẽ giữ lock
tới khi httpx tự trả về (mặc định 30 giây). Trong log này tool chỉ mất 9 ms nên chưa bao
giờ chạm tới — đó là lý do `after=0.000s` ở mọi mẫu. **Không nên coi đây là nguyên nhân
đã quan sát được**, nhưng nó là hình dạng deadlock thật sự nếu độ trễ merchant tăng.

### 3.2 Bằng chứng còn thiếu

Log không ghi mốc bắt đầu của từng lần inference (không có `INFERENCE_START/END` cho
đường voice). Vì vậy 24.19 giây ở §2.2 được suy ra là inference **bằng cách loại trừ**
(ba tool chỉ chiếm 9 ms, không có dòng log nào khác trong khoảng đó), chứ không đo trực
tiếp. Muốn chắc chắn tuyệt đối cần bật timing per-round trong `OrchestratorAgent.run_stream`.

## 4. Ngành công nghiệp làm thế nào (nguồn sơ cấp)

### Pipecat — tool call là *uninterruptible*, không phải bị giết

Từ `pipecat/frames/frames.py`
([source](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/frames/frames.py)):

- `UninterruptibleFrame`: *"A marker for data or control frames that must not be
  interrupted. Frames with this mixin are still ordered normally, but unlike other
  frames, they are **preserved during interruptions**"*
- `FunctionCallInProgressFrame`: *"This is an **uninterruptible** frame because we always
  want to update the context."*
- `FunctionCallResultFrame`: *"This is an **uninterruptible** frame because once a result
  is generated we always want to update the context."*
- `FunctionCallCancelFrame`: có cờ `run_llm`; *"Only a call cancelled by its own timeout
  sets this"* — tức huỷ tool là đường **riêng, có timeout riêng**, không phải hệ quả của
  barge-in.

Từ `pipecat/processors/frame_processor.py`
([source](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/processors/frame_processor.py)):

- `broadcast_interruption()` đẩy `InterruptionFrame` **cả upstream lẫn downstream**.
- `_start_interruption()` kiểm tra `UninterruptibleFrame`; nếu uninterruptible thì chỉ
  *"flush non-uninterruptible frames from the queue"*, ngược lại
  `await self.__cancel_process_task()` rồi `self.__create_process_task()` — **huỷ và tạo
  lại task xử lý**, chứ không chờ nó tự xong.
- Hàng đợi mỗi processor có ưu tiên: `StartFrame` (1) > `SystemFrame` (10) > còn lại (20),
  nên frame hệ thống không xếp sau dữ liệu tồn đọng.
- `__cancel_input_task()` có timeout guard, kèm chú thích: *"if a library swallows
  asyncio.CancelledError, the task would otherwise never be cancelled"* — chính là hiểm
  hoạ treo mà §3.1 mô tả, và Pipecat đã phòng sẵn.

### OpenAI Realtime API

[developers.openai.com/api/docs/guides/realtime-conversations](https://developers.openai.com/api/docs/guides/realtime-conversations):
khi VAD phát hiện `input_audio_buffer.speech_started`, *"The server will automatically
cancel any in-progress model response and a `response.cancelled` event will be emitted."*
Client gửi `conversation.item.truncate` để *"remove the unplayed portion of the model's
last response from the conversation."* Tài liệu **không** nêu rõ số phận của function
call đang chờ.

### Gemini Live API

[ai.google.dev/gemini-api/docs/live-guide](https://ai.google.dev/gemini-api/docs/live-guide):
*"When VAD detects an interruption, the ongoing generation is canceled and discarded.
Only the information already sent to the client is retained in the session history."*
Và rõ ràng về tool: *"The Gemini server then **discards any pending function calls** and
sends a `BidiGenerateContentServerContent` message with the IDs of the canceled calls."*
Audio mic được stream **liên tục** qua `send_realtime_input` trước, trong và sau khi model
sinh — không có khoảng dừng nào.

### Tổng hợp: mic không bao giờ tắt, và không lượt nào được phép im lặng

Cả ba đều giữ mic stream liên tục (khớp với §1 prior art). Khác biệt nằm ở tool đang bay:
Gemini **huỷ và báo id**, Pipecat **giữ context bằng uninterruptible frame**. Nhưng điểm
chung quan trọng nhất cho ca này: **không hệ nào để một lượt trôi qua 27 giây mà không
gửi gì về client**. OpenAI/Gemini phát audio ngay từ token đầu; Pipecat đẩy `LLMTextFrame`
xuống TTS ngay khi có.

## 5. Đề xuất

Xếp theo tỉ lệ hiệu quả trên công sức. Chỉ mô tả hướng, không kèm code.

### 5.1 (Ưu tiên cao nhất) Đừng để lượt có tool bị câm

`stream_agent` (`src/openjarvis/server/voice/llm.py:198-243`): vấn đề không phải việc
đệm `later_round` — mục đích chống đọc lặp là đúng — mà là **không có gì được phát trong
lúc đệm**. Hai cách, chọn một:

- **Phát câu mở đầu của vòng đầu tiên trước khi tool chạy.** Hiện tại nếu model gọi tool
  ngay mà không sinh chữ nào, `projector.finish()` tại `llm.py:221` trả về rỗng và khách
  không nghe gì. Khi vòng đầu không sinh text, cần phát một câu xác nhận ngắn (lấy từ
  `event.tool_name` qua `voice_activity_frame("tool", ...)` đã có sẵn) để khách biết máy
  đang làm việc. Đây đúng là khuyến nghị §6.2 của prior art ("Audio Fillers, TTFT < 1.5s"),
  và bằng chứng ở §2 cho thấy nó là thứ quan trọng nhất chứ không phải phụ trợ.
- **Hoặc** hạ ngưỡng đệm: chỉ đệm từ vòng tool **thứ hai** trở đi. Vòng đầu vốn đã được
  phát; lỗi đọc lặp mà docstring nhắc tới đến từ các vòng nháp giữa chừng.

### 5.2 Cắt số vòng cho đường voice

`max_turns = 30` là hợp lý cho đường text nhưng là 30 lần inference nối tiếp cho một lượt
nói. Đường voice nên có trần riêng, thấp hơn hẳn, và khi chạm trần thì trả lời bằng lời
thay vì im lặng. Chỗ đặt: cấu hình voice trong
`configs/openjarvis/examples/ordering-kiosk.toml` hoặc lúc `bind()` ở
`NativeAgentRuntime.bind` (`src/openjarvis/agents/runtime.py:111`).

### 5.3 Sửa timeout không có tác dụng của ToolExecutor

`src/openjarvis/tools/_stubs.py:248` — bỏ khối `with` quanh `ThreadPoolExecutor` (hoặc
gọi `shutdown(wait=False)`) để `future.result(timeout=...)` thật sự bounded. Hiện tại
`shutdown(wait=True)` chạy trước `except TimeoutError`, khiến timeout chỉ là trang trí.
Đây là sửa một chỗ, có lợi cho **mọi** caller chứ không riêng voice.

### 5.4 Phân loại tool interruptible, dùng đúng cơ chế Pipecat đã có

Prior art §6.4 đã đề xuất phân loại; nay có thể ánh xạ thẳng vào nguyên thuỷ có sẵn thay
vì tự dựng: tool đọc (`display_*`, GET) → để barge-in huỷ; tool mutating
(`POST /orders/public`, `POST /payment/initiate/public`) → `UninterruptibleFrame` /
`FunctionCallInProgressFrame` của Pipecat, cho chạy xong rồi hoà kết quả vào context.
Lưu ý bằng chứng ở §2.1: lúc 18:10:25 một đơn **đã được tạo thật** (`status=201`) ngay
trước khi khách kêu "Dừng lại" — đúng kịch bản "ghost order" mà prior art §3 cảnh báo.
`evidence.claim_mutation` / `finish_mutation` (`src/openjarvis/tools/evidence.py`) đã
chống gửi trùng, nên phần còn thiếu chỉ là hoà kết quả vào ngữ cảnh lượt sau.

### 5.5 Chưa cần đụng tới `NativeAgentRuntime._lock`

Lock **không** phải nguyên nhân: 0.000 giây chờ ở mọi mẫu, 0 lần re-enter. Đừng tái cấu
trúc nó dựa trên giả định của prior art. Việc đáng làm là gỡ chuỗi hoãn nhả lock ở §3.1
(qua 5.3) để nó không trở thành nguyên nhân khi merchant chậm đi.

---

## Phụ lục: cách tái lập bằng chứng

```bash
LOG=/tmp/openjarvis-stack/backend.log
grep -o "LOCK ACQUIRED after [0-9.]*s" "$LOG" | sort -u          # → chỉ 0.000s
grep -c "RE-ENTERED" "$LOG"                                      # → 0
grep -o "LOCK RELEASED elapsed=[0-9.]*" "$LOG" | sort -t= -k2 -rn | head
awk '/18:11:06.973/,/18:11:34.3/' "$LOG" | grep -v "kiosk tick\|savings"
```

Nguồn sơ cấp đã tra:
[pipecat frames.py](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/frames/frames.py) ·
[pipecat frame_processor.py](https://github.com/pipecat-ai/pipecat/blob/main/src/pipecat/processors/frame_processor.py) ·
[OpenAI Realtime conversations](https://developers.openai.com/api/docs/guides/realtime-conversations) ·
[Gemini Live guide](https://ai.google.dev/gemini-api/docs/live-guide)
