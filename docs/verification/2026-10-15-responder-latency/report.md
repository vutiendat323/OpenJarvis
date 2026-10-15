# Responder acknowledgements missing and slower first speech

Investigation of the customer's clarified symptom: fewer/late business
acknowledgements, not repeated speech. Phase 1 used existing logs; after the
follow-up request to identify why calls exceed two seconds, phase 2 ran six
bounded paid text-only probes with synthetic menu requests. No production
code/config changes, restart or merchant order/payment calls. Log counts are
frozen at **2026-10-15 08:45:50 local**.

## Confirmed findings

The responder frequently fails to produce a usable routing decision before the
fixed two-second deadline. Its text is then discarded, and the Agent only starts
after this wait. This explains both the missing quick acknowledgement and an
additional two seconds on the fallback path.

Counts from `.stack-logs/backend.log.1` and `backend.log`, reproduced in
`evidence.json`:

| Window | Attempts | Interrupted | With audible responder text | Timeout | Delegated with no filler |
| --- | ---: | ---: | ---: | ---: | ---: |
| Oct 14 23:36–Oct 15 00:00 | 33 | 1 | 30 | 0 | 2 |
| Oct 15 08:32–08:45 | 23 | 3 | 6 | 14 | 0 |

Excluding interruptions, morning responder timeouts are **14/20 (70%)** and only
**6/20 (30%)** have responder speech. The earlier window has speech on 30/32
noninterrupted attempts. These are observed samples, not provider benchmarks.
No morning `empty`/missing-filler outcome explains the loss: its logged failure
mode is `timeout`.

## One concrete timeline

Agent trace `34fe09125e044ba4b7fe8a143da53fcc`, request “Cho mình một cái menu.”:

| Local time | Event |
| --- | --- |
| 08:38:50.807 | User turn inference triggered |
| about 08:38:52.807 | Responder deadline; `outcome=timeout decision_seconds=2.00` |
| 08:38:52.811 | Agent trace starts, 2.004 s after trigger |
| 08:38:55.850 | First TTS request: “Mở menu cho bạn” |
| 08:38:56.050 | Bot started speaking, 5.243 s after trigger |
| 08:38:56.512 | Final TTS request: “Mời bạn xem menu nhé.” |

The late topic acknowledgement comes from the Agent path. The responder timed
out and supplied no text on this turn. The delay measured here starts at inference
trigger; STT and turn-ending time before it are additional, separate latency.

## Causal code path

1. `voice/llm.py:_answer` awaits `_ask_responder` before `_consume_agent`; Agent
   execution is sequential after the responder decision/fallback.
2. `VoiceResponder.respond` buffers text. Delegated filler becomes usable when
   the first tool-call chunk appears; direct answers wait until stream end.
3. `_ask_responder` waits at most `RESPONDER_DECISION_SECONDS = 2.0`. Missing that
   decision cancels the stream and returns `("agent", "")`, including when
   earlier uncommitted text was received. TTS gets no responder acknowledgement.
4. Without responder text, no PREAMBLE_SPOKEN marker is set. Agent progress speech
   can therefore appear later as the topic acknowledgement.

A local synthetic probe used the real VoiceResponder and `_ask_responder`, with
only the model stream replaced: text arrived at 0 ms, delegation remained pending,
and a 50 ms test deadline produced `route=agent`, empty responder TTS text, and a
canceled stream at 50 ms. This confirms the buffering/deadline mechanism. It does
not prove whether the live timed-out streams had already supplied text.

## Phase 2: reproduced upstream delay and HTTP 503 retries

Used the running backend's configured model (`deepseek-flash`), credential,
unchanged 3574-character prompt and real CloudEngine/VoiceResponder. Thinking
was explicitly disabled. A timing wrapper observed rich stream chunks; HTTPX
hooks measured TCP/TLS and response headers. The diagnostic deadline was 12 s,
so calls that production cancels at 2 s could reveal their result. Every call
was canceled at the first delegation; tool arguments were not executed.

Artifacts: `provider-probe.json` (four logical calls), `http-status-probe.json`
(two follow-up calls, stopped as soon as an HTTP error was captured), and
`probe_provider_timing.py` (reproducer for the first four probes).

| Probe | TCP/TLS complete | Successful HTTP headers | First text | First tool call |
| --- | ---: | ---: | ---: | ---: |
| Full prompt, cold client | 0.170 s | 2.224 s, after two retries | 4.502 s | 4.616 s |
| Full prompt, next call | 0.057 s | 0.245 s | 2.419 s | 2.571 s |
| Full prompt, after 8 s idle | 0.063 s | 0.230 s | 1.556 s | 1.724 s |
| Short control prompt | 0.063 s | 0.230 s | 1.381 s | 1.548 s |

The follow-up captured a specific retried error:

```
0.234 s  HTTP 503
         error.code/type = service_unavailable_error
0.884 s  retried request: HTTP 200
2.646 s  first content
2.885 s  first tool call
```

DeepSeek's official [error-code documentation](https://api-docs.deepseek.com/quick_start/error_codes/)
classifies HTTP 503 as server overload due to high traffic. This supports the
classification of the captured response, not a claim of a global outage or of
503 on every historical timeout.

Another full-prompt call returned HTTP 200 at 0.255 s, first text at 1.841 s,
and its tool call at 2.096 s. Production would drop this acknowledgement even
though text started arriving before its deadline, because delegation was late.

Conclusions supported by these probes:

- The API/stream has variable time to first content; one successful HTTP response
  reached the client at 0.245 s but yielded content only at 2.419 s. TCP/TLS and
  the 0.11–0.26 s text-to-tool interval do not account for most of that delay.
- The provider also returns HTTP 503 `service_unavailable_error`. CloudEngine
  does not override the OpenAI SDK's default two retries; HTTP 503 is retryable.
  Retry requests/backoff consume time inside the responder's two-second budget
  and are not visible in the existing `voice_preamble` outcome line.
- These upstream delays interact with the wait-for-delegation gate and sequential
  Agent startup. They explain the reproduced lost acknowledgement and additional
  fallback latency; merely increasing the deadline would retain that wait.
- The prompt/source mtimes precede both compared sessions. With the same full
  prompt, decisions varied from 1.724 to 4.616 s. The single short-prompt control
  is not enough to attribute the incident to prompt length or to replace the
  production prompt without its conformance eval.
- No private reasoning chunks were observed and requests specified thinking off.
  No runtime restarts, browser/TTS pipeline or Agent/tool execution were needed
  to reproduce the slow decision.

## Limits of attribution to the original 14 timeouts

The original logs prove a slower *usable decision* and the fallback penalty,
but have no first-token or per-HTTP-attempt timestamps. `_responder()` creates a plain
CloudEngine, separate from the Agent's instrumented engine, so responder calls
have no per-call trace/TTFT. Existing logs record only outcome and total decision
time. Phase 2 reproduces two real upstream causes, but does not establish which
of the original 14 calls encountered 503 versus a slow successful stream. The
evidence does not reveal the provider's internal cause of 503/first-token delay;
it supports an unstable upstream API/stream, not a specific internal outage,
capacity or network diagnosis.

The request path explicitly supplies DeepSeek thinking-disabled options and
forwards tool-call chunks as they arrive; inspection found no deliberate engine
buffering until full tool arguments complete.

Future per-turn measurement should separate responder first text, completed filler,
delegation, cancellation and first audio under one turn ID. Increasing the timeout
alone retains the sequential wait and can worsen fallback latency. No behavioral
fix was applied during this diagnosis.

Self-evaluation: accuracy 4 (live API 503/stream delay reproduced; historical
per-call attribution unavailable), completeness 4 (logs/Agent trace plus six
bounded provider probes; no provider-internal visibility),
clarity 4 (separates inference-trigger latency from earlier STT and provider
latency), actionability 4 (specific missing timing measurements), conciseness 4
(only relevant log counters/timeline retained). Overall 4.0; the next useful
improvement is per-turn responder timing and an evaluated recovery policy.
