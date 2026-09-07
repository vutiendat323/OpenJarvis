"""SkillManageTool — create, list, load, or delete agent-authored skills."""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

from openjarvis.core.paths import get_config_dir
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

logger = logging.getLogger(__name__)

_SAFE_SKILL_NAME = re.compile(r"^[a-z0-9]+(?:[._-][a-z0-9]+)*$")


def _toml_string(value: Any) -> str:
    """Serialize one untrusted value as a TOML basic string."""
    return json.dumps(str(value), ensure_ascii=False)


@ToolRegistry.register("skill_manage")
class SkillManageTool(BaseTool):
    """Manage agent-authored procedural skills."""

    def __init__(
        self,
        skills_dir: Path | str | None = None,
        *,
        skill_manager: Any = None,
        memory_backend: Any = None,
    ) -> None:
        if skills_dir is None:
            skills_dir = get_config_dir() / "skills"
        self._skills_dir = Path(skills_dir).expanduser()
        self._skill_manager = skill_manager
        self._memory_backend = memory_backend

    def bind_runtime(
        self,
        *,
        skill_manager: Any,
        memory_backend: Any,
        skills_dir: Path | str | None = None,
    ) -> None:
        """Attach the live services created during system composition."""
        self._skill_manager = skill_manager
        self._memory_backend = memory_backend
        if skills_dir is not None:
            self._skills_dir = Path(skills_dir).expanduser()

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="skill_manage",
            description="Create, list, load, delete, or run agent-authored skills.",
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["create", "list", "load", "delete", "run"],
                        "description": "Action to perform.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Skill name (for create/load/delete).",
                    },
                    "description": {
                        "type": "string",
                        "description": "Skill description (for create).",
                    },
                    "steps": {
                        "type": "array",
                        "description": (
                            "List of step dicts with tool_name and optional"
                            " arguments_template (for create)."
                        ),
                    },
                    "context": {
                        "type": "object",
                        "description": "Initial context for run.",
                    },
                    "intent": {
                        "type": "string",
                        "description": "Optional intent remembered for create.",
                    },
                    "requires_fresh_confirmation": {
                        "type": "boolean",
                        "description": "Whether replay requires a new confirmation.",
                    },
                },
                "required": ["action"],
            },
            category="skill",
        )

    def execute(self, **params: Any) -> ToolResult:
        action = params.get("action", "list")
        name = params.get("name", "")
        if action == "create":
            return self._create(
                name,
                params.get("description", ""),
                params.get("steps", []),
                params.get("intent"),
                params.get("requires_fresh_confirmation", True),
            )
        elif action == "list":
            return self._list()
        elif action == "load":
            return self._load(name)
        elif action == "delete":
            return self._delete(name)
        elif action == "run":
            return self._run(name, params.get("context", {}))
        return ToolResult(
            tool_name=self.spec.name,
            success=False,
            content=f"Unknown action: {action}",
        )

    def _create(
        self,
        name: str,
        description: str,
        steps: List[dict],
        intent: Optional[str] = None,
        requires_fresh_confirmation: bool = True,
    ) -> ToolResult:
        path = self._skill_path(name)
        if path is None:
            return self._invalid_name()
        self._skills_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "[skill]",
            f"name = {_toml_string(name)}",
            f"description = {_toml_string(description)}",
            "",
        ]
        for step in steps:
            lines.append("[[skill.steps]]")
            lines.append(f"tool_name = {_toml_string(step.get('tool_name', ''))}")
            if "arguments_template" in step:
                lines.append(
                    f"arguments_template = {_toml_string(step['arguments_template'])}"
                )
            if "output_key" in step:
                lines.append(f"output_key = {_toml_string(step['output_key'])}")
            lines.append("")
        fd, temp_path = tempfile.mkstemp(dir=self._skills_dir, suffix=".toml")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                stream.write("\n".join(lines))
            os.replace(temp_path, path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

        if self._skill_manager is not None:
            try:
                self._skill_manager.discover(paths=[self._skills_dir])
            except Exception as exc:
                return ToolResult(
                    tool_name=self.spec.name,
                    success=False,
                    content=f"Created skill but failed to discover it: {exc}",
                )
        normalized_intent = self._normalize_intent(intent)
        if normalized_intent and self._memory_backend is not None:
            try:
                replay_policy = (
                    "Run only after fresh confirmation."
                    if requires_fresh_confirmation
                    else "No confirmation is required; this skill only reads live data."
                )
                self._memory_backend.store(
                    (
                        f"Intent '{normalized_intent}' maps to skill '{name}'. "
                        f"{replay_policy}"
                    ),
                    source="openjarvis.skill_learning",
                    metadata={
                        "intent": normalized_intent,
                        "skill_name": name,
                        "requires_fresh_confirmation": requires_fresh_confirmation,
                    },
                )
            except Exception:
                # The skill is already written and discovered; losing only the
                # recall hint must not turn a successful create into a raise.
                logger.warning("Failed to remember skill intent", exc_info=True)
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=f"Created skill: {name}",
        )

    def _run(self, name: Any, context: Any) -> ToolResult:
        if self._skill_manager is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="Skill manager unavailable: cannot run skills in this runtime.",
            )
        if not isinstance(context, dict):
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="Skill context must be an object.",
            )
        context = {
            "today": datetime.now().astimezone().date().isoformat(),
            **context,
        }
        try:
            result = self._skill_manager.execute(name, context)
        except Exception as exc:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"Skill run failed: {exc}",
            )
        if not result.step_results:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="Skill run produced no result.",
            )
        final = result.step_results[-1]
        metadata = dict(final.metadata)
        if result.success and final.success and final.tool_name.startswith("display_"):
            metadata["completed_display"] = True
        return ToolResult(
            tool_name=self.spec.name,
            content=final.content,
            success=final.success,
            usage=final.usage,
            cost_usd=final.cost_usd,
            latency_seconds=final.latency_seconds,
            metadata=metadata,
        )

    def _list(self) -> ToolResult:
        if not self._skills_dir.exists():
            return ToolResult(
                tool_name=self.spec.name,
                success=True,
                content="No skills directory found.",
            )
        skills = []
        for f in sorted(self._skills_dir.glob("*.toml")):
            skills.append(f.stem)
        if not skills:
            return ToolResult(
                tool_name=self.spec.name,
                success=True,
                content="No skills found.",
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content="Available skills:\n" + "\n".join(f"- {s}" for s in skills),
        )

    def _load(self, name: str) -> ToolResult:
        path = self._skill_path(name)
        if path is None:
            return self._invalid_name()
        if not path.exists():
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"Skill not found: {name}",
            )
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=path.read_text(encoding="utf-8"),
        )

    def _delete(self, name: str) -> ToolResult:
        path = self._skill_path(name)
        if path is None:
            return self._invalid_name()
        if not path.exists():
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content=f"Skill not found: {name}",
            )
        path.unlink()
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=f"Deleted skill: {name}",
        )

    def _skill_path(self, name: Any) -> Path | None:
        """Return a resolved direct child, or reject before touching a file."""
        if not isinstance(name, str) or _SAFE_SKILL_NAME.fullmatch(name) is None:
            return None
        root = self._skills_dir.resolve()
        path = (root / f"{name}.toml").resolve()
        if path.parent != root:
            return None
        return path

    def _invalid_name(self) -> ToolResult:
        return ToolResult(
            tool_name=self.spec.name,
            success=False,
            content="Invalid skill name.",
        )

    @staticmethod
    def _normalize_intent(intent: Any) -> str:
        if not isinstance(intent, str):
            return ""
        return " ".join(intent.lower().split())
