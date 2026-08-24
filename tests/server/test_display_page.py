"""Customer display source stays React-rendered and markup-safe."""

from __future__ import annotations

from pathlib import Path

FRONTEND = Path(__file__).resolve().parents[2] / "frontend/src"
DISPLAY_PAGE = FRONTEND / "pages/CustomerDisplayPage.tsx"
DISPLAY_STATE = FRONTEND / "pages/customerDisplayState.ts"
KIOSK_PAGE = FRONTEND / "pages/KioskPage.tsx"

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
