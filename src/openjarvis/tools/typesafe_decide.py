"""TypeSafe Jev structured decisions through OpenRouter."""

from __future__ import annotations

import json
import os
import time
from typing import Any

import httpx

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

_SYSTEM_ONE_URL = "https://openrouter.ai/api/v1/systemone"
_MODEL = "typesafe/jev-1.13"
_DEFAULT_CONFIDENCE_THRESHOLD = 0.85


def _provider_error(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"TypeSafe Jev request failed with HTTP {response.status_code}."

    error = payload.get("error", {}) if isinstance(payload, dict) else {}
    message = error.get("message") if isinstance(error, dict) else None
    if not isinstance(message, str) or not message:
        message = "Provider rejected the request."
    return f"TypeSafe Jev request failed with HTTP {response.status_code}: {message}"


@ToolRegistry.register("typesafe_decide")
class TypeSafeDecideTool(BaseTool):
    """Make narrow, typed decisions with Jev instead of generating prose."""

    tool_id = "typesafe_decide"
    is_local = False

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="typesafe_decide",
            description=(
                "Make one policy-approved, narrow structured classification,"
                " routing, scoring, or yes/no decision with TypeSafe Jev through"
                " OpenRouter. Supply explicit criteria and use the returned"
                " probabilities. Never use this for menu search/classification,"
                " obvious user intent, chat, factual answers, arithmetic, or prose"
                " generation. If accepted is false, treat the decision as"
                " uncertain and let Luna handle it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "state": {
                        "description": "Text or JSON-compatible state to evaluate.",
                    },
                    "questions": {
                        "type": "object",
                        "description": (
                            "Named Jev questions. Each value uses type choice, score,"
                            " or noul with instructions and the matching criteria."
                        ),
                        "minProperties": 1,
                    },
                    "confidence_threshold": {
                        "type": "number",
                        "minimum": 0,
                        "maximum": 1,
                        "default": _DEFAULT_CONFIDENCE_THRESHOLD,
                        "description": (
                            "Minimum confidence for choice and score answers to be"
                            " accepted. Noul returns probability rather than"
                            " confidence and is left to the caller's policy."
                        ),
                    },
                },
                "required": ["state", "questions"],
            },
            category="inference",
            cost_estimate=0.00002,
            latency_estimate=1.2,
            timeout_seconds=12.0,
            required_capabilities=["network:fetch"],
        )

    def execute(self, **params: Any) -> ToolResult:
        api_key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        if not api_key:
            return ToolResult(
                tool_name=self.tool_id,
                content="OPENROUTER_API_KEY is not configured.",
                success=False,
            )

        state = params.get("state")
        if state is None or (isinstance(state, str) and not state.strip()):
            return ToolResult(
                tool_name=self.tool_id,
                content="A non-empty state is required.",
                success=False,
            )

        questions = params.get("questions")
        if not isinstance(questions, dict) or not questions:
            return ToolResult(
                tool_name=self.tool_id,
                content="At least one named question is required.",
                success=False,
            )

        try:
            threshold = float(
                params.get("confidence_threshold", _DEFAULT_CONFIDENCE_THRESHOLD)
            )
        except (TypeError, ValueError):
            threshold = -1.0
        if not 0.0 <= threshold <= 1.0:
            return ToolResult(
                tool_name=self.tool_id,
                content="confidence_threshold must be between 0 and 1.",
                success=False,
            )

        started = time.perf_counter()
        try:
            response = httpx.post(
                _SYSTEM_ONE_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={"model": _MODEL, "state": state, "questions": questions},
                timeout=10.0,
            )
        except httpx.TimeoutException:
            return ToolResult(
                tool_name=self.tool_id,
                content="TypeSafe Jev request timed out; no automatic retry was made.",
                success=False,
                latency_seconds=time.perf_counter() - started,
            )
        except httpx.RequestError as exc:
            return ToolResult(
                tool_name=self.tool_id,
                content=f"TypeSafe Jev network error: {type(exc).__name__}.",
                success=False,
                latency_seconds=time.perf_counter() - started,
            )

        latency = time.perf_counter() - started
        if not response.is_success:
            return ToolResult(
                tool_name=self.tool_id,
                content=_provider_error(response),
                success=False,
                latency_seconds=latency,
            )

        try:
            payload = response.json()
        except ValueError:
            return ToolResult(
                tool_name=self.tool_id,
                content="TypeSafe Jev returned invalid JSON.",
                success=False,
                latency_seconds=latency,
            )

        answers = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(answers, dict):
            return ToolResult(
                tool_name=self.tool_id,
                content="TypeSafe Jev response did not contain structured answers.",
                success=False,
                latency_seconds=latency,
            )

        low_confidence = sorted(
            name
            for name, answer in answers.items()
            if isinstance(answer, dict)
            and isinstance(answer.get("confidence"), (int, float))
            and float(answer["confidence"]) < threshold
        )
        accepted = not low_confidence
        model = payload.get("model", _MODEL)
        provider = payload.get("provider", "TypeSafe")
        generation_id = payload.get("id")
        usage = payload.get("usage", {})
        if not isinstance(usage, dict):
            usage = {}
        cost = usage.get("cost", 0.0)
        cost_usd = float(cost) if isinstance(cost, (int, float)) else 0.0

        content = json.dumps(
            {
                "model": model,
                "provider": provider,
                "answers": answers,
                "accepted": accepted,
                "low_confidence_questions": low_confidence,
                "confidence_threshold": threshold,
                "generation_id": generation_id,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return ToolResult(
            tool_name=self.tool_id,
            content=content,
            success=True,
            usage=usage,
            cost_usd=cost_usd,
            latency_seconds=latency,
            metadata={
                "accepted": accepted,
                "generation_id": generation_id,
                "model": model,
                "provider": provider,
                "low_confidence_questions": low_confidence,
            },
        )


__all__ = ["TypeSafeDecideTool"]
