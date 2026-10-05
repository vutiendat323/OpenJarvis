"""Which skill steps may share a WorkflowEngine stage."""

from __future__ import annotations

import json
from pathlib import Path

from openjarvis.skills.loader import load_skill
from openjarvis.skills.step_graph import build_step_graph
from openjarvis.skills.types import SkillManifest, SkillStep
from openjarvis.workflow.types import NodeType

SKILLS = Path(__file__).resolve().parents[2] / "skills"


def _http(method: str, url: str = "https://shop.example/x", output_key: str = ""):
    return SkillStep(
        tool_name="http_request",
        arguments_template=json.dumps({"url": url, "method": method}),
        output_key=output_key,
    )


def _stages(manifest: SkillManifest) -> list[list[int]]:
    graph = build_step_graph(manifest)
    assert graph is not None
    assert {node.node_type for node in graph.nodes} == {NodeType.TOOL}
    return [
        sorted(graph.get_node(nid).config["step_index"] for nid in stage)
        for stage in graph.execution_stages()
    ]


def test_checkout_reads_menu_and_tables_together_and_orders_every_write():
    manifest = load_skill(SKILLS / "trendcoffee-checkout.toml")

    # GET menu | GET tables -> POST order -> bill -> POST payment -> QR
    assert _stages(manifest) == [[0, 1], [2], [3], [4], [5]]


def test_skills_with_nothing_to_overlap_keep_the_sequential_path():
    for name in ("menu", "add-to-cart", "tables"):
        manifest = load_skill(SKILLS / f"trendcoffee-{name}.toml")
        assert build_step_graph(manifest) is None, name


def test_a_read_waits_for_the_read_whose_output_it_uses():
    first = _http("GET", output_key="menu")
    second = _http("GET", url="https://shop.example/{menu.id}", output_key="item")
    assert build_step_graph(SkillManifest(name="s", steps=[first, second])) is None


def test_a_read_after_a_write_waits_for_that_write():
    steps = [
        _http("GET", output_key="a"),
        _http("POST", output_key="b"),
        _http("GET", output_key="c"),
        _http("HEAD", output_key="d"),
    ]
    assert _stages(SkillManifest(name="s", steps=steps)) == [[0], [1], [2, 3]]


def test_a_read_named_in_a_later_assertion_is_still_a_dependency():
    first = _http("GET", output_key="a")
    second = _http("GET", output_key="b")
    second.assertions = [{"actual": "{a.result}", "equals": "{b.result}"}]
    assert build_step_graph(SkillManifest(name="s", steps=[first, second])) is None


def test_steps_that_are_not_plain_reads_are_barriers():
    templated_method = SkillStep(
        tool_name="http_request",
        arguments_template='{"url":"https://shop.example/x","method":"{verb}"}',
        output_key="m",
    )
    for barrier in (
        templated_method,
        SkillStep(tool_name="http_request", arguments_template="not json"),
        SkillStep(tool_name="display_menu", arguments_template="{}"),
        SkillStep(skill_name="other", arguments_template="{}"),
    ):
        steps = [_http("GET", output_key="a"), barrier, _http("GET", output_key="c")]
        assert build_step_graph(SkillManifest(name="s", steps=steps)) is None


def test_a_reused_output_key_keeps_the_sequential_path():
    steps = [_http("GET", output_key="a"), _http("GET", output_key="a")]
    assert build_step_graph(SkillManifest(name="s", steps=steps)) is None
