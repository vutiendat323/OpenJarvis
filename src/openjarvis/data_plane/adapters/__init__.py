"""Registry for structured source adapters."""

from openjarvis.core.registry import SourceAdapterRegistry
from openjarvis.data_plane.adapters.trendcoffee import TrendCoffeeAdapter

__all__ = ["SourceAdapterRegistry", "TrendCoffeeAdapter"]
