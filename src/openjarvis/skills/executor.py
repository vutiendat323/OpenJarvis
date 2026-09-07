"""SkillExecutor — runs skill steps sequentially through ToolExecutor."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.skills.security import validate_capabilities
from openjarvis.skills.types import SkillManifest
from openjarvis.tools._stubs import ToolExecutor


@dataclass(slots=True)
class SkillResult:
    skill_name: str = ""
    success: bool = True
    step_results: List[ToolResult] = field(default_factory=list)
    context: Dict[str, Any] = field(default_factory=dict)


# Resolver callback: given a skill name and the current context, returns a SkillResult.
SkillResolver = Callable[[str, Dict[str, Any]], SkillResult]
_MISSING = object()


class SkillExecutor:
    """Execute a skill manifest step-by-step.

    Each step's arguments_template supports ``{key}`` placeholders
    that are resolved from the context dict (populated by prior step outputs).
    """

    def __init__(
        self,
        tool_executor: ToolExecutor,
        *,
        bus: Optional[EventBus] = None,
        allowed_capabilities: Optional[Set[str]] = None,
    ) -> None:
        self._tool_executor = tool_executor
        self._bus = bus
        self._skill_resolver: Optional[SkillResolver] = None
        # None means "no capability policy" — every skill runs, matching the
        # behavior before capability enforcement existed. Pass a set (even an
        # empty one) to enforce: skills whose required_capabilities are not a
        # subset of it are blocked before any step runs.
        self._allowed_capabilities: Optional[Set[str]] = allowed_capabilities

    def set_skill_resolver(self, resolver: SkillResolver) -> None:
        """Register a callback used to delegate ``skill_name`` steps."""
        self._skill_resolver = resolver

    def run(
        self,
        manifest: SkillManifest,
        *,
        initial_context: Optional[Dict[str, Any]] = None,
    ) -> SkillResult:
        """Execute all steps in a skill manifest."""
        missing = (
            validate_capabilities(manifest, self._allowed_capabilities)
            if self._allowed_capabilities is not None
            else []
        )
        if missing:
            if self._bus:
                self._bus.publish(
                    EventType.SKILL_EXECUTE_START,
                    {"skill": manifest.name, "steps": len(manifest.steps)},
                )
                self._bus.publish(
                    EventType.SKILL_EXECUTE_END,
                    {"skill": manifest.name, "success": False},
                )
            return SkillResult(
                skill_name=manifest.name,
                success=False,
                step_results=[
                    ToolResult(
                        tool_name=manifest.name,
                        content=(
                            f"Blocked: skill '{manifest.name}' requires "
                            f"capabilities {missing} that were not granted "
                            "for this session."
                        ),
                        success=False,
                    )
                ],
                context=dict(initial_context or {}),
            )

        ctx: Dict[str, Any] = dict(initial_context or {})
        all_results: List[ToolResult] = []

        if self._bus:
            self._bus.publish(
                EventType.SKILL_EXECUTE_START,
                {"skill": manifest.name, "steps": len(manifest.steps)},
            )

        for i, step in enumerate(manifest.steps):
            step_id = step.tool_name or step.skill_name

            # Render template
            try:
                rendered = self._render_template(step.arguments_template, ctx)
            except Exception as exc:
                result = ToolResult(
                    tool_name=step_id,
                    content=f"Template rendering error: {exc}",
                    success=False,
                )
                all_results.append(result)
                break

            if step.skill_name:
                # Delegate to sub-skill resolver
                result = self._run_sub_skill(
                    step.skill_name, rendered, ctx, manifest.name, i
                )
            else:
                # Execute via tool executor
                tool_call = ToolCall(
                    id=f"skill_{manifest.name}_{i}",
                    name=step.tool_name,
                    arguments=rendered,
                )
                result = self._tool_executor.execute(tool_call)

            all_results.append(result)

            if not result.success:
                break

            # Store output in context
            if step.output_key:
                ctx[step.output_key] = result.content

        success = all(r.success for r in all_results)

        if self._bus:
            self._bus.publish(
                EventType.SKILL_EXECUTE_END,
                {"skill": manifest.name, "success": success},
            )

        return SkillResult(
            skill_name=manifest.name,
            success=success,
            step_results=all_results,
            context=ctx,
        )

    def _run_sub_skill(
        self,
        skill_name: str,
        rendered_args: str,
        parent_ctx: Dict[str, Any],
        parent_skill: str,
        step_index: int,
    ) -> ToolResult:
        """Invoke the skill resolver and convert its result to a ToolResult."""
        if self._skill_resolver is None:
            return ToolResult(
                tool_name=skill_name,
                content=f"No skill resolver registered for sub-skill '{skill_name}'",
                success=False,
            )

        # Parse rendered args and merge into a copy of the parent context
        try:
            args: Dict[str, Any] = json.loads(rendered_args)
        except json.JSONDecodeError:
            args = {}

        child_ctx = {**parent_ctx, **args}

        sub_result: SkillResult = self._skill_resolver(skill_name, child_ctx)

        # Expose the final context value under the first output_key, or the
        # last step's content, as the synthetic "content" of this ToolResult.
        content: Any = ""
        if sub_result.context:
            # Return the last stored value from the child's context that is
            # not already in the parent context (i.e. the output of the sub-skill).
            new_keys = [k for k in sub_result.context if k not in parent_ctx]
            if new_keys:
                content = sub_result.context[new_keys[-1]]
            else:
                content = (
                    list(sub_result.context.values())[-1] if sub_result.context else ""
                )
        elif sub_result.step_results:
            content = sub_result.step_results[-1].content

        return ToolResult(
            tool_name=skill_name,
            content=content,
            success=sub_result.success,
        )

    @staticmethod
    def _render_template(template: str, ctx: Dict[str, Any]) -> str:
        """Render simple or dotted placeholders while preserving JSON syntax."""
        try:
            parsed = json.loads(template)
        except json.JSONDecodeError:
            return SkillExecutor._render_legacy_template(template, ctx)
        rendered = SkillExecutor._render_json_value(parsed, ctx)
        return json.dumps(rendered, ensure_ascii=False)

    @staticmethod
    def _render_json_value(value: Any, ctx: Dict[str, Any]) -> Any:
        if isinstance(value, dict):
            return {
                key: SkillExecutor._render_json_value(child, ctx)
                for key, child in value.items()
            }
        if isinstance(value, list):
            return [SkillExecutor._render_json_value(child, ctx) for child in value]
        if not isinstance(value, str):
            return value

        full_placeholder = re.fullmatch(r"\{(\w+(?:\.\w+)*)\}", value)
        if full_placeholder:
            resolved = SkillExecutor._resolve_placeholder(
                full_placeholder.group(1), ctx
            )
            if resolved is _MISSING:
                raise ValueError(f"Unresolved placeholder: {full_placeholder.group(1)}")
            return resolved

        def _replace(match: re.Match) -> str:
            resolved = SkillExecutor._resolve_placeholder(match.group(1), ctx)
            if resolved is _MISSING:
                raise ValueError(f"Unresolved placeholder: {match.group(1)}")
            if isinstance(resolved, str):
                return resolved
            return json.dumps(resolved, ensure_ascii=False)

        return re.sub(r"\{(\w+(?:\.\w+)*)\}", _replace, value)

    @staticmethod
    def _render_legacy_template(template: str, ctx: Dict[str, Any]) -> str:
        """Keep legacy placeholder behavior for non-JSON templates."""

        def _replace(match: re.Match) -> str:
            value = SkillExecutor._resolve_placeholder(match.group(1), ctx)
            if value is _MISSING:
                raise ValueError(f"Unresolved placeholder: {match.group(1)}")
            rendered = json.dumps(value, ensure_ascii=False)
            inside_json_string = (
                match.start() > 0
                and match.end() < len(template)
                and template[match.start() - 1] == '"'
                and template[match.end()] == '"'
            )
            if isinstance(value, str) and inside_json_string:
                return rendered[1:-1]
            return rendered

        return re.sub(r"\{(\w+(?:\.\w+)*)\}", _replace, template)

    @staticmethod
    def _resolve_placeholder(key: str, ctx: Dict[str, Any]) -> Any:
        root, *path = key.split(".")
        value = ctx.get(root, _MISSING)
        if value is _MISSING:
            return _MISSING
        if path and isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                return _MISSING
        for segment in path:
            if isinstance(value, dict) and segment in value:
                value = value[segment]
            elif (
                isinstance(value, list)
                and segment.isdigit()
                and int(segment) < len(value)
            ):
                value = value[int(segment)]
            else:
                return _MISSING
        return value


__all__ = ["SkillExecutor", "SkillResolver", "SkillResult"]
