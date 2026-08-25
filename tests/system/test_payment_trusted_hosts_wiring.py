"""payment_trusted_origins parses into the exact tuple display_payment_qr gets.

Unit-level on purpose: SystemBuilder.build() is expensive to stand up and
these two static methods are the entire path from config string to tool
attribute -- no need to build a whole system to pin it down.
"""

from __future__ import annotations

from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.display import DisplayMenuTool, DisplayPaymentQrTool


def test_parse_trusted_hosts_blank_is_empty():
    assert SystemBuilder._parse_trusted_hosts("") == ()


def test_parse_trusted_hosts_strips_and_lowercases():
    assert SystemBuilder._parse_trusted_hosts(" TrendCoffee.net , B.example ") == (
        "trendcoffee.net",
        "b.example",
    )


def test_parse_trusted_hosts_drops_empty_entries():
    assert SystemBuilder._parse_trusted_hosts("a,,b,") == ("a", "b")


def test_parse_trusted_hosts_all_commas_is_empty():
    assert SystemBuilder._parse_trusted_hosts(",") == ()


def test_inject_payment_trusted_hosts_reaches_the_payment_tool():
    tool = DisplayPaymentQrTool()

    SystemBuilder._inject_payment_trusted_hosts(tool, ("trendcoffee.net",))

    assert tool._payment_trusted_hosts == ("trendcoffee.net",)


def test_inject_payment_trusted_hosts_skips_a_non_payment_display_tool():
    tool = DisplayMenuTool()

    SystemBuilder._inject_payment_trusted_hosts(tool, ("trendcoffee.net",))

    assert not hasattr(tool, "_payment_trusted_hosts")
