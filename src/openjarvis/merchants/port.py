"""What an ordering tool may ask a merchant, and what it gets back.

Shaped after a real merchant API rather than an imagined one:

* A size is a **variant** -- its own slug, its own price -- not an option on a
  product. There is no modifier map anywhere in this port.
* Anything the merchant does not model (sugar level, ice, "no straw") is free
  text in ``note``. Nothing validates it and nothing verifies it; a person
  reads it.
* Menus are **branch-scoped**, so every read takes a branch.
* An order carries a **type**: eating in, taking away, or delivery.

The port also mirrors the mutation/observation doctrine the tools enforce:
``add_to_cart`` and ``place_order`` return an identifier and nothing else, so a
tool built on them *cannot* report resulting state without calling an observing
method.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Protocol, Tuple

# What an order can be. The customer saying "mang đi" is choosing "take-out".
ORDER_TYPES: Tuple[str, ...] = ("at-table", "take-out", "delivery")


@dataclass(frozen=True, slots=True)
class Branch:
    slug: str
    name: str
    address: str


@dataclass(frozen=True, slots=True)
class Variant:
    """A separately priced SKU. This is what goes in a cart, not a product."""

    slug: str
    size: str
    price: int


@dataclass(frozen=True, slots=True)
class Product:
    slug: str
    name: str
    category: str
    available: bool
    variants: Tuple[Variant, ...]


@dataclass(frozen=True, slots=True)
class CartLine:
    line_id: str
    variant_slug: str
    name: str
    size: str
    note: str
    quantity: int
    line_total: int


@dataclass(frozen=True, slots=True)
class Cart:
    lines: Tuple[CartLine, ...]
    total: int


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    order_type: str
    branch_slug: str
    lines: Tuple[CartLine, ...]
    total: int
    status: str


class MerchantPort(Protocol):
    """The whole merchant surface. Nothing else may be called from a tool."""

    def list_branches(self) -> List[Branch]: ...

    def search_menu(self, query: str, branch_slug: str) -> List[Product]: ...

    def get_product(
        self, product_slug: str, branch_slug: str
    ) -> Optional[Product]: ...

    def add_to_cart(self, variant_slug: str, quantity: int, note: str) -> str: ...

    def remove_from_cart(self, line_id: str) -> bool: ...

    def read_cart(self) -> Cart: ...

    def place_order(self, order_type: str, branch_slug: str) -> str: ...

    def read_order(self, order_id: str) -> Optional[Order]: ...


__all__ = [
    "ORDER_TYPES",
    "Branch",
    "Cart",
    "CartLine",
    "MerchantPort",
    "Order",
    "Product",
    "Variant",
]
