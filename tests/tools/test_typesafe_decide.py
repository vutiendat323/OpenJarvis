"""Tests for the TypeSafe Jev structured-decision tool."""

from __future__ import annotations

import json

import httpx

from openjarvis.mcp.server import MCPServer
from openjarvis.tools import typesafe_decide
from openjarvis.tools.typesafe_decide import TypeSafeDecideTool


def _response(status: int, payload: dict) -> httpx.Response:
    return httpx.Response(
        status,
        json=payload,
        request=httpx.Request("POST", typesafe_decide._SYSTEM_ONE_URL),
    )


class TestTypeSafeDecideTool:
    def test_spec_is_narrow_and_structured(self):
        spec = TypeSafeDecideTool().spec

        assert spec.name == "typesafe_decide"
        assert spec.category == "inference"
        assert spec.parameters["required"] == ["state", "questions"]
        assert spec.required_capabilities == ["network:fetch"]

    def test_auto_discovery_registers_tool(self):
        names = {tool.spec.name for tool in MCPServer().get_tools()}

        assert "typesafe_decide" in names

    def test_missing_openrouter_key_does_not_call_provider(
        self, monkeypatch
    ):
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        called = False

        def _post(*args, **kwargs):
            nonlocal called
            called = True

        monkeypatch.setattr(typesafe_decide.httpx, "post", _post)

        result = TypeSafeDecideTool().execute(
            state="hello",
            questions={"intent": {"type": "noul", "instructions": "Greeting?"}},
        )

        assert result.success is False
        assert "OPENROUTER_API_KEY" in result.content
        assert called is False

    def test_success_uses_pinned_jev_model_and_reports_confidence(
        self, monkeypatch
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
        captured = {}

        def _post(url, **kwargs):
            captured["url"] = url
            captured.update(kwargs)
            return _response(
                200,
                {
                    "model": "typesafe/jev-1.13-20260917",
                    "answers": {
                        "intent": {
                            "type": "choice",
                            "choice": "greeting",
                            "probabilities": {"greeting": 0.97, "other": 0.03},
                            "confidence": 0.94,
                        }
                    },
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 20,
                        "cost": 0.0000042,
                    },
                    "id": "gen-dec-test",
                    "provider": "TypeSafe",
                },
            )

        monkeypatch.setattr(typesafe_decide.httpx, "post", _post)
        questions = {
            "intent": {
                "type": "choice",
                "instructions": "Classify intent.",
                "criteria": {
                    "greeting": "A greeting",
                    "other": "Anything else",
                },
            }
        }

        result = TypeSafeDecideTool().execute(
            state="hello",
            questions=questions,
            confidence_threshold=0.85,
        )

        assert result.success is True
        assert captured["url"] == typesafe_decide._SYSTEM_ONE_URL
        assert captured["json"] == {
            "model": "typesafe/jev-1.13",
            "state": "hello",
            "questions": questions,
        }
        assert captured["headers"]["Authorization"] == "Bearer test-openrouter-key"
        assert captured["timeout"] == 10.0
        assert result.usage["input_tokens"] == 100
        assert result.cost_usd == 0.0000042
        assert result.metadata == {
            "accepted": True,
            "generation_id": "gen-dec-test",
            "model": "typesafe/jev-1.13-20260917",
            "provider": "TypeSafe",
            "low_confidence_questions": [],
        }
        assert "test-openrouter-key" not in result.content
        assert json.loads(result.content)["accepted"] is True

    def test_low_confidence_is_advisory_not_a_provider_failure(
        self, monkeypatch
    ):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(
            typesafe_decide.httpx,
            "post",
            lambda *args, **kwargs: _response(
                200,
                {
                    "model": "typesafe/jev-1.13-20260917",
                    "answers": {
                        "route": {
                            "type": "choice",
                            "choice": "menu",
                            "probabilities": {"menu": 0.55, "other": 0.45},
                            "confidence": 0.1,
                        }
                    },
                    "usage": {"cost": 0.000001},
                    "provider": "TypeSafe",
                },
            ),
        )

        result = TypeSafeDecideTool().execute(
            state="ambiguous",
            questions={
                "route": {
                    "type": "choice",
                    "instructions": "Choose route.",
                    "criteria": {"menu": "Menu", "other": "Other"},
                }
            },
        )

        assert result.success is True
        assert result.metadata["accepted"] is False
        assert result.metadata["low_confidence_questions"] == ["route"]
        assert json.loads(result.content)["accepted"] is False

    def test_provider_error_is_returned_without_secret(self, monkeypatch):
        monkeypatch.setenv("OPENROUTER_API_KEY", "test-openrouter-key")
        monkeypatch.setattr(
            typesafe_decide.httpx,
            "post",
            lambda *args, **kwargs: _response(
                404,
                {"error": {"message": "Model blocked by guardrail"}},
            ),
        )

        result = TypeSafeDecideTool().execute(
            state="hello",
            questions={"intent": {"type": "noul", "instructions": "Greeting?"}},
        )

        assert result.success is False
        assert "Model blocked by guardrail" in result.content
        assert "test-openrouter-key" not in result.content
