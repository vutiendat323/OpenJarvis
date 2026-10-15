"""Four paid, synthetic text-only probes; no merchant API calls.

Run from OpenJarvis with the existing provider key in the environment.
Measures first content and delegation without the production 2-second cutoff.
"""

import asyncio
import json
import os
import time
from contextlib import aclosing
from pathlib import Path

from openjarvis.engine.cloud import CloudEngine
from openjarvis.server.voice.responder import VoiceResponder, load_responder_settings


async def main():
    os.environ["OPENJARVIS_CONFIG"] = (
        "configs/openjarvis/examples/ordering-kiosk-mcp.toml"
    )
    settings = load_responder_settings()
    engine = CloudEngine()
    client = engine._deepseek_async_client
    if client is None:
        raise RuntimeError("DeepSeek credential unavailable")
    active = {}
    started = 0.0

    async def http_trace(name, _info):
        active.setdefault("transport_events", []).append(
            [name, round(time.monotonic() - started, 4)]
        )

    async def request_hook(request):
        request.extensions["trace"] = http_trace

    async def response_hook(response):
        active["response_headers_s"] = round(time.monotonic() - started, 4)
        active["http_status"] = response.status_code
        active.setdefault("http_responses", []).append(
            {"seconds": active["response_headers_s"], "status": response.status_code}
        )

    client._client.event_hooks["request"] = [request_hook]
    client._client.event_hooks["response"] = [response_hook]

    class TimedEngine:
        def reasoning_off_options(self, model):
            return engine.reasoning_off_options(model)

        async def stream_full(self, *args, **kwargs):
            active["thinking_disabled"] = (
                kwargs.get("extra_body", {}).get("thinking", {}).get("type")
                == "disabled"
            )
            stream = engine.stream_full(*args, **kwargs)
            async with aclosing(stream):
                async for chunk in stream:
                    elapsed = round(time.monotonic() - started, 4)
                    if chunk.content:
                        active.setdefault("first_content_s", elapsed)
                        active["content_chars"] = active.get("content_chars", 0) + len(
                            chunk.content
                        )
                    if chunk.reasoning_content:
                        active.setdefault("first_reasoning_s", elapsed)
                        active["reasoning_chars"] = active.get(
                            "reasoning_chars", 0
                        ) + len(chunk.reasoning_content)
                    if chunk.tool_calls:
                        active.setdefault("first_tool_call_s", elapsed)
                    yield chunk

    rows = []
    cases = [
        ("full_cold", settings.prompt, 0),
        ("full_warm", settings.prompt, 0),
        ("full_after_8s_idle", settings.prompt, 8),
        (
            "short_prompt_warm",
            (
                "Reply in Vietnamese with one short neutral sentence saying you are "
                "looking up the request, then call getNextResponseFromSupervisor. "
                "Never claim a result."
            ),
            0,
        ),
    ]
    for label, prompt, idle in cases:
        if idle:
            await asyncio.sleep(idle)
        active = {
            "case": label,
            "model": settings.model,
            "prompt_chars": len(prompt),
            "idle_seconds": idle,
        }
        started = time.monotonic()
        responder = VoiceResponder(TimedEngine(), settings.model, prompt)
        delegated = asyncio.get_running_loop().create_future()

        def on_delegate(filler):
            if not delegated.done():
                delegated.set_result(filler)

        task = asyncio.create_task(
            responder.respond("Cho mình xem menu.", "", on_delegate)
        )
        try:
            await asyncio.wait(
                {task, delegated}, timeout=12, return_when=asyncio.FIRST_COMPLETED
            )
            if delegated.done():
                active["decision_s"] = round(time.monotonic() - started, 4)
                active["filler"] = delegated.result()
                active["outcome"] = "delegated"
            elif task.done():
                reply = task.result()
                active["outcome"] = reply.kind if reply else "empty"
                active["decision_s"] = round(time.monotonic() - started, 4)
            else:
                active["outcome"] = "probe_timeout_12s"
        except Exception as exc:
            active["outcome"] = "error:" + type(exc).__name__
        finally:
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        rows.append(dict(active))
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    Path(
        "docs/verification/2026-10-15-responder-latency/provider-probe.json"
    ).write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
