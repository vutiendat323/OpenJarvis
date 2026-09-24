"""Customer display source stays React-rendered and markup-safe."""

from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend/src"
DISPLAY_PAGE = FRONTEND / "pages/CustomerDisplayPage.tsx"
DISPLAY_STATE = FRONTEND / "pages/customerDisplayState.ts"
KIOSK_PAGE = FRONTEND / "pages/KioskPage.tsx"
APP = FRONTEND / "App.tsx"

UNSAFE_MARKUP_APIS = (
    "dangerouslySetInnerHTML",
    "innerHTML",
    "insertAdjacentHTML",
    "document.write",
)


def test_react_customer_display_sources_exist_and_do_not_build_markup():
    for path in (DISPLAY_PAGE, DISPLAY_STATE):
        assert path.is_file()
        source = path.read_text(encoding="utf-8")
        for unsafe_api in UNSAFE_MARKUP_APIS:
            assert unsafe_api not in source


def test_kiosk_has_no_legacy_display_surface():
    source = KIOSK_PAGE.read_text(encoding="utf-8")
    assert "display.html" not in source
    assert "<iframe" not in source


def test_customer_display_uses_a_route_exclusive_shell():
    source = APP.read_text(encoding="utf-8")
    app_start = source.index("export default function App()")
    shell_start = source.index("function ApplicationShell()")
    route_shell = source[app_start:shell_start]

    assert "appRouteMode(location.pathname)" in route_shell
    assert "<CustomerDisplayPage />" in route_shell
    for global_chrome in (
        "<UpdateChecker",
        "<Toaster",
        "<CommandPalette",
        "<OptInModal",
    ):
        assert global_chrome not in route_shell
