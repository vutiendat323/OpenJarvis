"""Smoke test: every shipped preset config must load cleanly.

Presets are installed via `jarvis init --preset <name>`, which copies
`configs/openjarvis/examples/<name>.toml` to `~/.openjarvis/config.toml`.
A preset that fails to parse via `load_config()` would break first-time
setup, so we validate the whole set on every commit.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


# One turn of the ordering kiosk was measured at 13-36s. A snapshot age gate
# below that expires mid-conversation, so the ordering reads fail closed on the
# customer's next sentence and the Agent reports the menu as unavailable.
_MIN_SNAPSHOT_MAX_AGE_SECONDS = 600


@pytest.mark.parametrize(
    "preset_path",
    _preset_paths(),
    ids=lambda p: p.stem,
)
def test_merchant_preset_snapshot_age_outlives_a_conversation(
    preset_path: Path,
) -> None:
    cfg = load_config(path=preset_path)
    if cfg.merchants.backend == "none":
        pytest.skip("preset configures no merchant")
    assert cfg.merchants.snapshot_max_age_seconds >= _MIN_SNAPSHOT_MAX_AGE_SECONDS, (
        f"{preset_path.stem}: snapshot_max_age_seconds="
        f"{cfg.merchants.snapshot_max_age_seconds} expires mid-conversation"
    )


def test_unattended_kiosk_preset_grants_no_standing_write() -> None:
    """The two kiosk presets differ only here, and it is the dangerous place.

    `ordering-kiosk-mcp.toml` is the attended demo preset and may carry an
    authorized write grant. `ordering-kiosk.toml` is the public terminal
    nobody is watching: a grant copied into it would let an injected page
    place real orders.
    """
    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk.toml")

    assert cfg.data_plane.trusted_write_operations == ""
    # Nobody is standing at an unattended terminal to be the approver.
    assert cfg.data_plane.conversational_approval is False


def test_preset_write_grants_are_exact_identities() -> None:
    """A malformed grant must fail here, not at the first live mutation."""
    from openjarvis.data_plane.approval import parse_trusted_write_operations

    for preset_path in _preset_paths():
        configured = load_config(path=preset_path).data_plane.trusted_write_operations
        # Raises on a wildcard, a missing fingerprint, or stray whitespace.
        parse_trusted_write_operations(configured)


def test_kiosk_mcp_preset_runs_on_generic_tools_only() -> None:
    """The kiosk transacts through http_request, not through ordering tools."""
    cfg = load_config(path=PRESETS_DIR / "ordering-kiosk-mcp.toml")
    enabled = {name.strip() for name in cfg.tools.enabled.split(",")}

    assert {"http_request", "repl", "skill_manage"} <= enabled
    assert (
        not {
            "branch_list",
            "menu_search",
            "menu_item",
            "cart_set",
            "cart_view",
            "order_place",
            "order_verify",
            "source_discover",
            "source_sync",
            "structured_query",
            "source_execute",
            "source_verify",
        }
        & enabled
    )
    assert "browser_network_requests" in cfg.tools.mcp.servers
    assert cfg.tools.payment_trusted_origins == "trendcoffee.net"
