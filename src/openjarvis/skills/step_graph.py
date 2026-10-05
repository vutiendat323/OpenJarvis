"""Schedule a skill's steps as a WorkflowEngine graph of TOOL nodes.

Only plain reads may share a stage: an ``http_request`` whose template fixes
its method to GET or HEAD. Every other step (a write, a display, a sub-skill,
a method decided at run time) is a barrier: it waits for every earlier step,
and every later step waits for it, so writes keep their manifest order.
"""

from __future__ import annotations

import json
import re

from openjarvis.skills.types import SkillManifest, SkillStep
from openjarvis.workflow.graph import WorkflowGraph
from openjarvis.workflow.types import NodeType, WorkflowEdge, WorkflowNode

_PLACEHOLDER_ROOT = re.compile(r"\{(\w+)(?:\.\w+)*(?:\|\w+)?\}")


def _is_plain_read(step: SkillStep) -> bool:
    if step.skill_name or step.tool_name != "http_request":
        return False
    try:
        method = json.loads(step.arguments_template).get("method")
    except (AttributeError, json.JSONDecodeError):
        return False
    return method in {"GET", "HEAD"}


def _referenced_keys(step: SkillStep) -> set[str]:
    text = step.arguments_template + json.dumps(step.assertions, ensure_ascii=False)
    return set(_PLACEHOLDER_ROOT.findall(text))


def build_step_graph(manifest: SkillManifest) -> WorkflowGraph | None:
    """The steps as a graph, or None when no two steps can run together."""
    keys = [step.output_key for step in manifest.steps if step.output_key]
    if len(keys) != len(set(keys)):
        return None
    graph = WorkflowGraph(name=manifest.name)
    producers: dict[str, int] = {}
    last_barrier: int | None = None
    for index, step in enumerate(manifest.steps):
        graph.add_node(
            WorkflowNode(
                id=f"step_{index}",
                node_type=NodeType.TOOL,
                config={"step_index": index},
            )
        )
        if _is_plain_read(step):
            needs = {
                producers[key] for key in _referenced_keys(step) & producers.keys()
            }
            if last_barrier is not None:
                needs.add(last_barrier)
        else:
            needs = set(range(index))
            last_barrier = index
        for earlier in sorted(needs):
            graph.add_edge(
                WorkflowEdge(source=f"step_{earlier}", target=f"step_{index}")
            )
        if step.output_key:
            producers[step.output_key] = index
    if all(len(stage) == 1 for stage in graph.execution_stages()):
        return None
    return graph


__all__ = ["build_step_graph"]
