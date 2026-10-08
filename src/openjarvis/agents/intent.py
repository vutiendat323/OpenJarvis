"""Per-run goals declared by existing tool contracts, independent of domain."""

from __future__ import annotations

import copy
import json
from typing import Any

from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools._stubs import BaseTool


class IntentState:
    def __init__(self, tools: list[BaseTool]) -> None:
        self.tools = {tool.spec.name: tool for tool in tools}
        self.targets = {}
        for tool in tools:
            bindings = tool.spec.metadata.get("runtime_arguments", {})
            if not isinstance(bindings, dict) or any(
                argument not in tool.spec.parameters.get("properties", {})
                or argument == "_intent"
                or not isinstance(source, str)
                or not source
                or not all(source.split("."))
                for argument, source in bindings.items()
            ):
                raise ValueError(
                    "runtime argument bindings must reference declared parameters "
                    "and verified paths"
                )
            contract = tool.spec.metadata.get("intent_contract")
            if not isinstance(contract, dict):
                continue
            name = contract.get("name")
            if not isinstance(name, str) or not name or name == "NONE":
                raise ValueError("intent contract needs a nonempty name")
            targets = self.targets.setdefault(name, [])
            targets.append({"tool": tool.spec.name, "arguments": {}})
            for alias in contract.get("aliases", []):
                if alias.get("tool") not in self.tools or not isinstance(
                    alias.get("arguments", {}), dict
                ):
                    raise ValueError("intent contract references an unavailable tool")
                targets.append(alias)
        self.declared: str | None = None
        self.name: str | None = None
        self.reached = False
        self.target_attempted = False
        self.continuing = False
        self.path: list[str] = []
        self.terminal_reason = "INCOMPLETE"
        self.runtime_context: dict[str, Any] = {}

    def schemas(self, schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = copy.deepcopy(schemas)
        for entry in result:
            schema = entry["function"]["parameters"]
            tool = self.tools[entry["function"]["name"]]
            for argument in tool.spec.metadata.get("runtime_arguments", {}):
                schema.get("properties", {}).pop(argument, None)
                if argument in schema.get("required", []):
                    schema["required"].remove(argument)
            schema.setdefault("properties", {})["_intent"] = {
                "type": "string",
                "enum": ["NONE", *self.targets],
                "description": (
                    "Canonical requested outcome; keep it across preparation "
                    "and target execution. NONE is for unrelated information."
                ),
            }
            if "_intent" not in schema.setdefault("required", []):
                schema["required"].append("_intent")
        return result

    def instruction(self) -> str:
        return (
            "For each tool call supply _intent, selecting the user's requested "
            "outcome once. Polite wording does not change the outcome. "
            "Available intent targets: " + json.dumps(self.targets) + ". "
            "Preparation does not fulfill the requested outcome. Keep the same "
            "intent and execute its target after prerequisites are verified. "
            "Do not execute a negated request or treat a statement about a past "
            "action as authorization. Do not retry a target with unknown outcome; "
            "only tools explicitly marked read_only may observe its outcome. "
            "For a final informational answer return JSON with _intent NONE, "
            "state ANSWER and message. If unable to continue, return only JSON "
            "with _intent, state NEEDS_INPUT or FAILED and a message explaining "
            "the missing information or failure. Never claim target completion "
            "without a successful target result."
        )

    def _declare(self, declared: Any) -> None:
        if not isinstance(declared, str) or declared not in ["NONE", *self.targets]:
            raise ValueError("a declared canonical intent is required")
        if self.declared is not None and declared != self.declared:
            raise ValueError("the normalized intent cannot change during this turn")
        self.declared = declared
        self.name = None if declared == "NONE" else declared

    def _matches(self, name: str, call: ToolCall, arguments: dict[str, Any]) -> bool:
        return any(
            target["tool"] == call.name
            and all(
                arguments.get(k) == v for k, v in target.get("arguments", {}).items()
            )
            for target in self.targets.get(name, [])
        )

    def prepare(self, calls: list[ToolCall]) -> list[ToolCall]:
        parsed = []
        declared_goal = self.declared
        for call in calls:
            arguments = json.loads(call.arguments or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("intent arguments must be an object")
            declared = arguments.pop("_intent", None)
            if not isinstance(declared, str) or declared not in ["NONE", *self.targets]:
                raise ValueError("a declared canonical intent is required")
            if declared_goal is not None and declared != declared_goal:
                raise ValueError("the normalized intent cannot change during this turn")
            declared_goal = declared
            tool = self.tools.get(call.name)
            if tool is not None:
                for argument, source in tool.spec.metadata.get(
                    "runtime_arguments", {}
                ).items():
                    value = self.runtime_context
                    for key in source.split("."):
                        if not isinstance(value, dict) or key not in value:
                            raise ValueError(
                                "required verified runtime argument is unavailable"
                            )
                        value = value[key]
                    arguments[argument] = copy.deepcopy(value)
            if declared == "NONE" and any(
                self._matches(name, call, arguments) for name in self.targets
            ):
                raise ValueError("a target operation requires its declared intent")
            target = declared != "NONE" and self._matches(declared, call, arguments)
            if self.target_attempted and not self.reached:
                if (
                    target
                    or tool is None
                    or tool.spec.metadata.get("read_only") is not True
                ):
                    raise ValueError(
                        "target already attempted; observe outcome, do not retry"
                    )
            if self.reached:
                if target or not self.continuing:
                    raise ValueError("requested outcome already completed")
                if any(self._matches(name, call, arguments) for name in self.targets):
                    raise ValueError(
                        "a completed goal cannot execute another intent target"
                    )
            if declared != "NONE" and not target and not self.reached:
                if tool is not None:
                    arguments.update(
                        tool.spec.metadata.get("continuation_arguments", {})
                    )
            parsed.append(
                (ToolCall(call.id, call.name, json.dumps(arguments)), bool(target))
            )
        if len(calls) > 1 and any(target for _, target in parsed):
            raise ValueError("execute the intent target separately after prerequisites")
        if declared_goal is not None:
            self._declare(declared_goal)
        if any(target for _, target in parsed):
            self.target_attempted = True
        return [call for call, _ in parsed]

    def observe(self, call: ToolCall, result: ToolResult) -> None:
        self.path.append(call.name)
        arguments = json.loads(call.arguments or "{}")
        is_target = bool(self.name and self._matches(self.name, call, arguments))
        if (
            is_target
            and not result.success
            and result.metadata.get("execution_started") is False
        ):
            self.target_attempted = False
        if (
            self.name
            and self._matches(self.name, call, arguments)
            and result.success
            and not result.metadata.get("pending_approval")
        ):
            self.reached = True
            self.terminal_reason = "TARGET_COMPLETED"
        message = result.metadata.get("customer_message")
        acknowledged_display = (
            self.declared == "NONE"
            and result.success
            and result.metadata.get("completed_display") is True
            and result.metadata.get("continue_agent") is not True
            and not result.metadata.get("pending_approval")
            and isinstance(message, str)
            and bool(message.strip())
        )
        if acknowledged_display:
            self.terminal_reason = "ANSWER"
        elif not self.reached:
            result.metadata = {**result.metadata, "continue_agent": True}
            result.metadata.pop("customer_message", None)
        self.continuing = result.metadata.get("continue_agent") is True
        result.metadata.update(self.metadata())

    def finish(self, content: str) -> str | None:
        try:
            value = json.loads(content)
        except (ValueError, TypeError):
            return content if self.reached else None
        if not isinstance(value, dict):
            return content if self.reached else None
        if self.reached and not {"state", "message"}.issubset(value):
            return content
        declared = value.get("_intent", self.declared)
        valid_states = {"NEEDS_INPUT", "FAILED"}
        if declared == "NONE":
            valid_states.add("ANSWER")
        message = value.get("message")
        if (
            value.get("state") not in valid_states
            or not isinstance(message, str)
            or not message.strip()
        ):
            return None
        try:
            self._declare(declared)
        except ValueError:
            return None
        self.terminal_reason = value["state"]
        if value["state"] == "FAILED":
            self.reached = False
        return message

    def metadata(self) -> dict[str, Any]:
        return {
            "normalized_intent": self.declared,
            "intent_satisfied": self.reached,
            "execution_path": list(self.path),
            "terminal_reason": self.terminal_reason,
        }
