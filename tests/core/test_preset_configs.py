"""Smoke test: every shipped preset config must load cleanly.

Presets are installed via `jarvis init --preset <name>`, which copies
`configs/openjarvis/examples/<name>.toml` to `~/.openjarvis/config.toml`.
A preset that fails to parse via `load_config()` would break first-time
setup, so we validate the whole set on every commit.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
import tomllib

from openjarvis.core.config import JarvisConfig, load_config
from openjarvis.system.builder import SystemBuilder

PRESETS_DIR = (
    Path(__file__).resolve().parents[2] / "configs" / "openjarvis" / "examples"
)


def _preset_paths() -> list[Path]:
    return sorted(PRESETS_DIR.glob("*.toml"))


def test_presets_directory_exists() -> None:
    assert PRESETS_DIR.is_dir(), f"presets dir missing: {PRESETS_DIR}"


def test_at_least_one_preset_ships() -> None:
    assert _preset_paths(), f"no preset .toml files in {PRESETS_DIR}"


@pytest.mark.parametrize(
    "preset_path",
    _preset_paths(),
    ids=lambda p: p.stem,
)
def test_preset_loads(preset_path: Path) -> None:
    cfg = load_config(path=preset_path)
    assert isinstance(cfg, JarvisConfig)
    # A preset must at least name an engine and an agent — those are the two
    # slots `jarvis init` expects to be populated for a working first run.
    assert cfg.engine.default, f"{preset_path.stem}: engine.default is empty"
    assert cfg.agent.default_agent, f"{preset_path.stem}: agent.default_agent is empty"


def test_kiosk_mcp_preset_limits_agent_tool_surface() -> None:
    preset_path = PRESETS_DIR / "ordering-kiosk-mcp.toml"
    cfg = load_config(path=preset_path)
    raw_toml = tomllib.loads(preset_path.read_text())
    enabled = {name.strip() for name in cfg.tools.enabled.split(",")}
    hidden = {name.strip() for name in cfg.tools.model_hidden.split(",")}
    servers = json.loads(cfg.tools.mcp.servers)
    playwright = next(server for server in servers if server["name"] == "playwright")

    assert enabled == {
        "http_request",
        "skill_manage",
        "typesafe_decide",
        "display_menu",
        "display_cart",
        "display_bill",
        "display_payment_qr",
        "display_clear",
    }
    assert hidden == {
        "http_request",
        "skill_manage",
        "typesafe_decide",
        "display_bill",
        "display_payment_qr",
    }
    assert playwright["presentation_only"] is True
    assert not any(name.startswith("browser_") for name in enabled)
    assert cfg.agent.max_turns == 8
    assert cfg.tools.payment_trusted_origins == "https://trendcoffee.net"
    assert "merchants" not in raw_toml
    assert "data_plane" not in raw_toml

    tools = [SimpleNamespace(spec=SimpleNamespace(name=name)) for name in enabled]
    visible = SystemBuilder._model_visible_tools(tools, cfg.tools.model_hidden)
    assert {tool.spec.name for tool in visible} == {
        "display_menu",
        "display_cart",
        "display_clear",
    }


def test_kiosk_mcp_active_skills_are_prepared_composites(tmp_path: Path) -> None:
    from openjarvis.core.events import EventBus
    from openjarvis.skills.manager import SkillManager

    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    manager = SkillManager(EventBus(), overlay_dir=tmp_path)
    manager.discover([PRESETS_DIR.parents[2] / "skills"])
    tools = manager.get_skill_tools(active=cfg.skills.active)

    assert {tool.spec.name for tool in tools} == {
        "skill_trendcoffee-menu",
        "skill_trendcoffee-tables",
        "skill_trendcoffee-add-to-cart",
        "skill_trendcoffee-checkout",
    }
    assert cfg.learning.enabled is True
    assert cfg.learning.auto_update is True
    assert cfg.learning.training_enabled is False


def test_model_visible_menu_tool_only_accepts_verified_memory_selection() -> None:
    from openjarvis.tools.display import (
        DisplayCartMemoryTool,
        DisplayCartTool,
        DisplayMenuTool,
    )

    visible = SystemBuilder._model_visible_tools(
        [DisplayMenuTool(), DisplayCartTool()], ""
    )
    assert len(visible) == 2
    assert visible[0].spec.name == "display_menu"
    assert isinstance(visible[1], DisplayCartMemoryTool)
    properties = visible[0].spec.parameters["properties"]
    assert "item_indices" in properties
    assert "item_ids" not in properties
    assert "items" not in properties
    assert "all_from_latest_http" not in properties


def test_kiosk_mcp_prompt_uses_prepared_reads_and_verified_cart() -> None:
    from openjarvis.system.agent_construction import resolve_agent_system_prompt

    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    prompt = resolve_agent_system_prompt(cfg.agent)
    assert prompt is not None
    folded = " ".join(prompt.casefold().split())

    for name in (
        "skill_trendcoffee-menu",
        "skill_trendcoffee-tables",
        "skill_trendcoffee-add-to-cart",
        "skill_trendcoffee-checkout",
    ):
        assert name in folded
    assert "runtime_context.displayed_menu" in folded
    assert "do not also call `skill_trendcoffee-add-to-cart`" in folded
    assert "display_menu(item_indices=[...])" in folded
    assert "do not call a menu skill or any http tool for this refinement" in folded
    assert "even without the words “trong danh sách này”" in folded
    assert "for a new topic, ingredient or category" in folded
    assert "more than one item" in folded
    assert "single isolated item" in folded
    assert "treat its `visible_items` as the active screen" in folded
    assert "shortest ingredient or product noun" in folded
    assert "minprice=maxprice" in folded
    assert "never put prices or currency words in `itemterms`" in folded
    assert "leave `categoryterms` empty unless the customer explicitly asks" in folded
    assert "runtime_context.customer_screen_search.visible_items" in folded
    assert "one atomic cart publication" in folded
    assert "line_id" in folded
    assert "cart_revision" in folded
    assert "turn_nonce" in folded
    assert "update_order_type=true" in folded
    assert "call checkout in this same turn" in folded
    assert "if an item is missing or ambiguous, stop and ask before checkout" in folded
    assert "pending" in folded
    assert "does not mean the bank received money" in folded
    assert "status" in folded and "available" in folded
    assert "never navigate to `trendcoffee.net`" in folded
    assert "do not list/load learned skills or probe endpoints" in folded
    assert "browser_network_requests" not in folded
    assert "browser_navigate" not in folded
    assert "http_request(" not in folded
