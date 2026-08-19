"""An in-process merchant, so the goal loop can be proven without a website.

Menu, prices and slugs are modelled on a real Vietnamese coffee chain that was
probed while writing this: two branches with different menus, sizes as
separately priced variants, and no modifier system at all -- "ít đường" is a
free-text note a person reads.
"""

from __future__ import annotations

import itertools
from typing import Dict, List, Optional, Tuple

from openjarvis.merchants.port import (
    ORDER_TYPES,
    Branch,
    Cart,
    CartLine,
    Order,
    Product,
    Variant,
)

_BRANCHES: Tuple[Branch, ...] = (
    Branch("br-thu-duc", "Chi nhánh 1", "Thủ Đức"),
    Branch("br-quan-1", "Chi nhánh 2", "Quận 1"),
)


def _product(slug, name, category, available, sizes) -> Product:
    return Product(
        slug=slug,
        name=name,
        category=category,
        available=available,
        variants=tuple(
            Variant(f"{slug}-{key}", size, price) for key, size, price in sizes
        ),
    )


_CATALOGUE: Tuple[Product, ...] = (
    _product("ca-phe-den", "Cà phê đen", "coffee", True,
             [("std", "tiêu chuẩn", 35_000)]),
    _product("ca-phe-sua", "Cà phê sữa", "coffee", True,
             [("std", "tiêu chuẩn", 39_000)]),
    _product("americano", "Americano", "coffee", True,
             [("std", "tiêu chuẩn", 50_000)]),
    _product("espresso", "Espresso", "coffee", True,
             [("std", "tiêu chuẩn", 45_000)]),
    _product("latte", "Latte", "coffee", True,
             [("m", "vừa", 55_000), ("l", "lớn", 61_000)]),
    _product("tra-dao", "Trà đào", "tea", True,
             [("m", "vừa", 45_000), ("l", "lớn", 52_000)]),
    _product("banh-mi", "Bánh mì", "food", True,
             [("std", "tiêu chuẩn", 35_000)]),
    _product("tiramisu", "Tiramisu", "food", False,
             [("std", "tiêu chuẩn", 64_000)]),
)

# Menus are per branch, as they are on a real chain. The second branch carries
# a subset, so a test can prove that searching without a branch is meaningless.
_MENUS: Dict[str, Tuple[str, ...]] = {
    "br-thu-duc": tuple(product.slug for product in _CATALOGUE),
    "br-quan-1": ("ca-phe-den", "ca-phe-sua", "latte", "banh-mi"),
}


class FakeMerchant:
    """A deterministic merchant with branch menus and one cart.

    One cart, not a map keyed by thread: the design's first confirmed
    constraint is one conversation session at a time per server process.

    # ponytail: single cart, no thread scoping. A second concurrent session
    #   would share this one. Scope by thread_id at the same time as the other
    #   single-session ceilings (NativeAgentRuntime._lock, the voice lease,
    #   the TTS renderer global) -- they only make sense changed together.
    """

    def __init__(self) -> None:
        self._products: Dict[str, Product] = {p.slug: p for p in _CATALOGUE}
        self._variant_owner: Dict[str, str] = {
            variant.slug: product.slug
            for product in _CATALOGUE
            for variant in product.variants
        }
        self._lines: Dict[str, CartLine] = {}
        self._orders: Dict[str, Order] = {}
        self._line_seq = itertools.count(1)
        self._order_seq = itertools.count(1)

    # -- observation ---------------------------------------------------

    def list_branches(self) -> List[Branch]:
        return list(_BRANCHES)

    def search_menu(self, query: str, branch_slug: str) -> List[Product]:
        on_menu = _MENUS.get(branch_slug, ())
        products = [self._products[slug] for slug in on_menu]
        needle = query.strip().lower()
        if not needle:
            return products
        return [
            product
            for product in products
            if needle in product.name.lower() or needle in product.category
        ]

    def get_product(
        self, product_slug: str, branch_slug: str
    ) -> Optional[Product]:
        if product_slug not in _MENUS.get(branch_slug, ()):
            return None
        return self._products.get(product_slug)

    def read_cart(self) -> Cart:
        lines = tuple(self._lines.values())
        return Cart(lines=lines, total=sum(line.line_total for line in lines))

    def read_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    # -- mutation ------------------------------------------------------

    def add_to_cart(self, variant_slug: str, quantity: int, note: str) -> str:
        product_slug = self._variant_owner.get(variant_slug)
        if product_slug is None or quantity < 1:
            raise ValueError(variant_slug)
        product = self._products[product_slug]
        if not product.available:
            raise ValueError(variant_slug)
        variant = next(v for v in product.variants if v.slug == variant_slug)

        line_id = f"L{next(self._line_seq)}"
        self._lines[line_id] = CartLine(
            line_id=line_id,
            variant_slug=variant_slug,
            name=product.name,
            size=variant.size,
            # Stored verbatim. Nothing here interprets it, and nothing later
            # can verify it -- that is what a free-text note means.
            note=note or "",
            quantity=quantity,
            line_total=variant.price * quantity,
        )
        return line_id

    def remove_from_cart(self, line_id: str) -> bool:
        return self._lines.pop(line_id, None) is not None

    def place_order(self, order_type: str, branch_slug: str) -> str:
        if order_type not in ORDER_TYPES:
            raise ValueError(order_type)
        if branch_slug not in _MENUS:
            raise ValueError(branch_slug)
        cart = self.read_cart()
        order_id = f"ORD{next(self._order_seq):04d}"
        self._orders[order_id] = Order(
            order_id=order_id,
            order_type=order_type,
            branch_slug=branch_slug,
            lines=cart.lines,
            total=cart.total,
            status="placed",
        )
        self._lines.clear()
        return order_id

    # -- test seam -----------------------------------------------------

    def set_price(self, variant_slug: str, price: int) -> None:
        """Change a price behind the Agent's back, as a real site could."""
        product_slug = self._variant_owner[variant_slug]
        product = self._products[product_slug]
        self._products[product_slug] = Product(
            slug=product.slug,
            name=product.name,
            category=product.category,
            available=product.available,
            variants=tuple(
                Variant(v.slug, v.size, price if v.slug == variant_slug else v.price)
                for v in product.variants
            ),
        )


__all__ = ["FakeMerchant"]
