"""Analytics enablement gating."""

from __future__ import annotations

import pytest

from openjarvis.analytics.identity import is_analytics_enabled
from openjarvis.core.config import AnalyticsConfig


def test_analytics_follows_config_when_no_env_override(monkeypatch):
    monkeypatch.delenv("POSTHOG_DISABLED", raising=False)
    assert is_analytics_enabled(AnalyticsConfig(enabled=True)) is True
    assert is_analytics_enabled(AnalyticsConfig(enabled=False)) is False


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes"])
def test_posthog_disabled_env_overrides_enabled_config(monkeypatch, value):
    """Tests set POSTHOG_DISABLED so the SDK never registers its atexit join."""
    monkeypatch.setenv("POSTHOG_DISABLED", value)
    assert is_analytics_enabled(AnalyticsConfig(enabled=True)) is False


@pytest.mark.parametrize("value", ["", "0", "false", "no"])
def test_unset_like_env_values_do_not_disable(monkeypatch, value):
    monkeypatch.setenv("POSTHOG_DISABLED", value)
    assert is_analytics_enabled(AnalyticsConfig(enabled=True)) is True
