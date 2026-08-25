"""payment_trusted_origins parses into the exact tuple display_payment_qr gets.

Unit-level on purpose: SystemBuilder.build() is expensive to stand up and
these two static methods are the entire path from config string to tool
attribute -- no need to build a whole system to pin it down.
"""

from __future__ import annotations

import pytest

from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.display import DisplayMenuTool, DisplayPaymentQrTool


def test_parse_trusted_origins_blank_is_empty():
    assert SystemBuilder._parse_trusted_origins("") == ()


def test_parse_trusted_origins_normalizes_scheme_host_and_effective_port():
    assert SystemBuilder._parse_trusted_origins(
        " HTTPS://TrendCoffee.net , https://B.example:8443 "
    ) == (
        ("https", "trendcoffee.net", 443),
        ("https", "b.example", 8443),
    )


def test_parse_trusted_origins_drops_empty_entries():
    assert SystemBuilder._parse_trusted_origins(
        "https://a.example,,http://b.example,"
    ) == (("https", "a.example", 443), ("http", "b.example", 80))


def test_parse_trusted_origins_all_commas_is_empty():
    assert SystemBuilder._parse_trusted_origins(",") == ()


def test_parse_trusted_origins_rejects_host_only_values():
    with pytest.raises(ValueError, match="scheme"):
        SystemBuilder._parse_trusted_origins("trendcoffee.net")


def test_inject_payment_trusted_origins_reaches_the_payment_tool():
    tool = DisplayPaymentQrTool()
    origins = (("https", "trendcoffee.net", 443),)

    SystemBuilder._inject_payment_trusted_origins(tool, origins)

    assert tool._payment_trusted_origins == origins


def test_inject_payment_trusted_origins_skips_a_non_payment_display_tool():
    tool = DisplayMenuTool()

    SystemBuilder._inject_payment_trusted_origins(
        tool, (("https", "trendcoffee.net", 443),)
    )

    assert not hasattr(tool, "_payment_trusted_origins")
