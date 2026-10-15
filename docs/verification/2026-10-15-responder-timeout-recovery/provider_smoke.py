"""Two paid synthetic Responder calls; Agent is local and has no merchant tools.

Measures speech-frame delivery, not TTS playback. Run from the repository root.
"""

import asyncio
import json
import os
import time
from contextlib import aclosing, nullcontext
from pathlib import Path

from dotenv import dotenv_values
from pipecat.frames.frames import LLMTextFrame

from openjarvis.agents._stubs import (
    AgentContext,
    AgentResult,
    AgentRunCompleted,
    AgentTextDelta,
)
from openjarvis.engine.cloud import CloudEngine
from openjarvis.server.voice.llm import OpenJarvisLLMService
from openjarvis.server.voice.responder import VoiceResponder, load_responder_settings

ARTIFACT = Path(
    "docs/verification/2026-10-15-responder-timeout-recovery/provider-smoke.json"
)


async def main():
    if not os.environ.get("DEEPSEEK_API_KEY"):
        key = dotenv_values(".env").get("DEEPSEEK_API_KEY")
        if not key:
            raise RuntimeError("DeepSeek credential unavailable")
        os.environ["DEEPSEEK_API_KEY"] = key
    os.environ["OPENJARVIS_CONFIG"] = (
        "configs/openjarvis/examples/ordering-kiosk-mcp.toml"
    )
    settings = load_responder_settings()
    engine = CloudEngine()
    rows = []

    for label, utterance in (
        ("menu_vi", "Cho mình xem menu."),
        ("menu_en", "Can I see the menu?"),
    ):
        row = {"case": label, "model": settings.model, "agent_fixture_delay_s": 3.0}
        started = time.monotonic()

        def elapsed():
            return round(time.monotonic() - started, 4)

        class TimedEngine:
            def reasoning_off_options(self, model):
                return engine.reasoning_off_options(model)

            async def stream_full(self, *args, **kwargs):
                async with aclosing(engine.stream_full(*args, **kwargs)) as stream:
                    async for chunk in stream:
                        if chunk.content:
                            row.setdefault("first_provider_text_s", elapsed())
                        if chunk.tool_calls:
                            row.setdefault("first_provider_tool_s", elapsed())
                        yield chunk

        class FixtureAgent:
            model = "local-test-fixture"

            async def run_stream(self, prompt, context):
                row["agent_started_s"] = elapsed()
                await asyncio.sleep(row["agent_fixture_delay_s"])
                text = "Fixture result."
                yield AgentTextDelta(text)
                yield AgentRunCompleted(AgentResult(content=text, turns=1))

        service = OpenJarvisLLMService(
            FixtureAgent(),
            responder=VoiceResponder(TimedEngine(), settings.model, settings.prompt),
        )
        row["speech_frames"] = []

        async def capture(frame):
            if isinstance(frame, LLMTextFrame):
                row["speech_frames"].append({"seconds": elapsed(), "text": frame.text})

        service.push_frame = capture
        try:
            row["completed"] = await asyncio.wait_for(
                service._answer(
                    utterance, AgentContext(), nullcontext(), preamble=(utterance, "")
                ),
                timeout=12,
            )
        except Exception as exc:
            row["error"] = type(exc).__name__
        row["total_s"] = elapsed()
        rows.append(row)
        ARTIFACT.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
        print(json.dumps(row, ensure_ascii=False), flush=True)

    if engine._deepseek_async_client is not None:
        await engine._deepseek_async_client.close()


if __name__ == "__main__":
    asyncio.run(main())
