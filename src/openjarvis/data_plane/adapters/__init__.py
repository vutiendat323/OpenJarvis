"""Registry for structured source adapters."""

from openjarvis.core.registry import SourceAdapterRegistry
from openjarvis.data_plane.adapters.generic import GenericAdapter
from openjarvis.data_plane.adapters.trendcoffee import TrendCoffeeAdapter

__all__ = ["GenericAdapter", "SourceAdapterRegistry", "TrendCoffeeAdapter"]
