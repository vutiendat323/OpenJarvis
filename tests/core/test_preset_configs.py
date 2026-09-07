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


def test_kiosk_mcp_preset_keeps_browser_tools_off_the_default_agent_surface() -> None:
    """The kiosk fast path stays narrow while Playwright remains connected."""
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
    assert len(enabled) == len(expected_enabled)
    assert not any(name.startswith("browser_") for name in enabled)
    assert "repl" not in enabled
    assert cfg.agent.max_turns == 8
    assert cfg.skills.enabled is False
    assert set(include_tools) == expected_playwright_tools
    assert len(include_tools) == len(expected_playwright_tools)
    assert cfg.tools.payment_trusted_origins == "https://trendcoffee.net"
    assert "merchants" not in raw_toml
    assert "data_plane" not in raw_toml


def test_kiosk_mcp_general_menu_policy_requires_one_fresh_complete_category() -> None:
    """A general menu turn must show the complete default category in one read."""
    from openjarvis.system.agent_construction import resolve_agent_system_prompt

    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    prompt = resolve_agent_system_prompt(cfg.agent)

    assert prompt is not None
    general_policy = prompt.split("### 1. General menu inquiry", 1)[1].split(
        "### 2. Category menu inquiry", 1
    )[0]
    assert "display_menu(items=[])" not in general_policy
    assert "exactly one" in general_policy
    assert "fresh" in general_policy
    assert "d07665b001" in general_policy
    assert "size=100" in general_policy
    assert "page=1" in general_policy
    assert re.search(r"all.*products", general_policy, flags=re.IGNORECASE | re.DOTALL)


def test_kiosk_mcp_prompt_separates_category_browsing_from_filtered_search() -> None:
    """Browsing shows every item; keyword and ingredient searches stay filtered."""
    from openjarvis.system.agent_construction import resolve_agent_system_prompt

    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    prompt = resolve_agent_system_prompt(cfg.agent)

    assert prompt is not None
    category_policy = prompt.split("### 2. Category menu inquiry", 1)[1].split(
        "### 3.", 1
    )[0]
    filtered_policy = prompt.split("### 5.", 1)[1].split("### 6.", 1)[0]
    normalized_filtered_policy = " ".join(filtered_policy.split())

    assert "size=100" in category_policy
    assert "page=1" in category_policy
    assert re.search(
        r"all.*returned.*items", category_policy, flags=re.IGNORECASE | re.DOTALL
    )
    assert "all_from_latest_http=true" in category_policy
    assert "`món ăn` means `24dffe01f6`" in category_policy
    assert "`món bánh` means `29e0552928`" in category_policy
    assert "2 or 3" not in category_policy
    for example in ("món gà", "dâu tây", "trứng"):
        assert example in filtered_policy
    assert "`gà` -> `24dffe01f6`" in normalized_filtered_policy
    assert (
        "`dâu tây` -> `e6cfb79d94` + `a87d969ab5`"
        in normalized_filtered_policy
    )
    assert (
        "`trứng` -> `24dffe01f6` + `29e0552928`"
        in normalized_filtered_policy
    )
    assert re.search(r"filter", filtered_policy, flags=re.IGNORECASE)
    assert re.search(r"matching items", filtered_policy, flags=re.IGNORECASE)
    assert "exactly one GET per likely category" in filtered_policy
    assert "size=100" in filtered_policy
    assert "Do not add a `search`" in filtered_policy
    assert "Do not repeat" in filtered_policy


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
        "GET /catalogs",
        "GET /menu/specific/public",
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
        '"orderSlug": "<order_slug>"',
        "`qrCode`, `slug`, `status` and `order`",
        "Omit `qr_code`",
        "Accept: application/json",
        "Content-Type: application/json",
    ):
        assert provider_fact in prompt

    assert "Full catalogue | `GET /products`" not in prompt
    assert re.search(
        r"GET /products.*?do not use it", prompt, flags=re.IGNORECASE | re.DOTALL
    )


def test_kiosk_mcp_preset_learns_turns_without_training_a_model() -> None:
    """Turn learning is the point; model training is not part of this preset."""
    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")

    assert cfg.learning.enabled is True
    assert cfg.learning.auto_update is True
    assert cfg.learning.training_enabled is False
    # A learned skill must reach the customer through skill_manage, never by
    # loading the workspace `./skills` directory into the agent's tool list.
    assert cfg.skills.enabled is False


def test_kiosk_mcp_prompt_gates_a_learned_skill_on_a_current_confirmation() -> None:
    """Replaying a past order without asking again is the failure to prevent."""
    from openjarvis.system.agent_construction import resolve_agent_system_prompt

    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    prompt = resolve_agent_system_prompt(cfg.agent)

    assert prompt is not None
    assert "skill_manage" in prompt
    assert "same intent" in prompt
    assert re.search(
        r"confirmed.*?in this conversation", prompt, flags=re.IGNORECASE | re.DOTALL
    )
    assert re.search(
        r"past confirmation is never a current one", prompt, flags=re.IGNORECASE
    )


def test_kiosk_mcp_prompt_separates_cold_discovery_from_warm_live_reads() -> None:
    from openjarvis.system.agent_construction import resolve_agent_system_prompt

    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    prompt = resolve_agent_system_prompt(cfg.agent)

    assert prompt is not None
    assert "Cold discovery" in prompt
    assert "browser_network_requests" in prompt
    assert "learned-read-" in prompt
    assert re.search(r"still.*fresh.*HTTP", prompt, flags=re.IGNORECASE | re.DOTALL)
    assert "display_menu" in prompt
