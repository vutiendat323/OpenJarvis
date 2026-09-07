"""Smoke test: every shipped preset config must load cleanly.

Presets are installed via `jarvis init --preset <name>`, which copies
`configs/openjarvis/examples/<name>.toml` to `~/.openjarvis/config.toml`.
A preset that fails to parse via `load_config()` would break first-time
setup, so we validate the whole set on every commit.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
import tomllib

from openjarvis.core.config import JarvisConfig, load_config

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


def test_kiosk_mcp_preset_runs_on_generic_tools_only() -> None:
    """The kiosk transacts through http_request, not through ordering tools."""
    preset_path = PRESETS_DIR / "ordering-kiosk-mcp.toml"
    cfg = load_config(path=preset_path)
    raw_toml = tomllib.loads(preset_path.read_text())
    enabled = {name.strip() for name in cfg.tools.enabled.split(",")}
    expected_enabled = {
        "http_request",
        "skill_manage",
        "display_menu",
        "display_cart",
        "display_bill",
        "display_payment_qr",
        "display_clear",
        "browser_snapshot",
        "browser_find",
        "browser_navigate",
        "browser_navigate_back",
        "browser_tabs",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_hover",
        "browser_press_key",
        "browser_wait_for",
        "browser_handle_dialog",
        "browser_drag",
        "browser_drop",
        "browser_resize",
        "browser_close",
        "browser_network_requests",
        "browser_verify_element_visible",
        "browser_verify_text_visible",
        "browser_verify_value",
        "browser_verify_list_visible",
    }
    expected_playwright_tools = {
        "browser_snapshot",
        "browser_find",
        "browser_navigate",
        "browser_navigate_back",
        "browser_tabs",
        "browser_click",
        "browser_type",
        "browser_fill_form",
        "browser_select_option",
        "browser_hover",
        "browser_press_key",
        "browser_wait_for",
        "browser_handle_dialog",
        "browser_drag",
        "browser_drop",
        "browser_resize",
        "browser_close",
        "browser_network_requests",
        "browser_verify_element_visible",
        "browser_verify_text_visible",
        "browser_verify_value",
        "browser_verify_list_visible",
    }
    servers = json.loads(cfg.tools.mcp.servers)
    playwright = next(server for server in servers if server["name"] == "playwright")
    include_tools = playwright["include_tools"]

    assert enabled == expected_enabled
    assert len(enabled) == 29
    assert set(include_tools) == expected_playwright_tools
    assert len(include_tools) == len(expected_playwright_tools)
    assert cfg.tools.payment_trusted_origins == "https://trendcoffee.net"
    assert "merchants" not in raw_toml
    assert "data_plane" not in raw_toml


def test_kiosk_mcp_prompt_uses_http_output_and_keeps_provider_contract() -> None:
    """The resolved public prompt must match the preset's HTTP-only surface."""
    from openjarvis.system.agent_construction import resolve_agent_system_prompt

    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    prompt = resolve_agent_system_prompt(cfg.agent)

    assert prompt is not None
    assert re.search(r"\brepl\b", prompt, flags=re.IGNORECASE) is None
    assert "last_result" not in prompt
    for provider_fact in (
        "https://trendcoffee.net/api/latest",
        "GET /branch",
        "GET /products",
        "POST /orders/public",
        "GET /orders/{order_slug}",
        "POST /payment/initiate/public",
        "Any 2xx is success",
        "101006",
        "105002",
        "127000",
        "hasNext",
        "all 121 items",
        'size["name"]',
        "Every one of those eleven fields is required",
        "`at-table`",
        "`take-out`",
        "`delivery`",
        '"paymentMethod": "bank-transfer"',
        "`qrCode`, `slug`, `status` and `order`",
    ):
        assert provider_fact in prompt
