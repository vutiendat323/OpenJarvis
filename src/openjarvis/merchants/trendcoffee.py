"""Trend Coffee's ``MerchantPort`` facade over the Data Plane read model."""

from __future__ import annotations

import itertools
import threading
from typing import TYPE_CHECKING

from openjarvis.data_plane.adapters.trendcoffee import TrendCoffeeAdapter
from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.types import StructuredQuery
from openjarvis.merchants.port import (
    ORDER_TYPES,
    Branch,
    Cart,
    CartLine,
    Order,
    Product,
    Variant,
)

if TYPE_CHECKING:
    from openjarvis.system.bundles import DataPlaneRuntime


class TrendCoffeeMerchant:
    """Maps validated snapshots to ordering and owns one local draft cart.

    The snapshot store is the authority for every observation.  The cart is a
    short-lived process-local draft because the Phase 1 port has no session
    identifier; it is deliberately never written back to a provider.
    """

    def __init__(
        self, runtime: DataPlaneRuntime, *, source_id: str = "trend-coffee"
    ) -> None:
        self._runtime = runtime
        self._source_id = source_id
        self._lines: dict[str, CartLine] = {}
        self._line_seq = itertools.count(1)
        self._lock = threading.RLock()

    def list_branches(self) -> list[Branch]:
        return [
            Branch(
                slug=record.resource_id,
                name=_text(record.payload, "name"),
                address=_text(record.payload, "address"),
            )
            for record in self._records("branch")
            if _available(record.payload)
        ]

    def search_menu(self, query: str, branch_slug: str) -> list[Product]:
        if not self._branch_exists(branch_slug):
            return []
        needle = query.strip().casefold()
        products = [
            product
            for record in self._records("menu_item")
            if _on_branch(record.payload, branch_slug)
            if (product := _product(record.resource_id, record.payload)) is not None
        ]
        if not needle:
            return products
        return [
            product
            for product in products
            if needle in product.name.casefold()
            or needle in product.category.casefold()
        ]

    def get_product(self, product_slug: str, branch_slug: str) -> Product | None:
        if not self._branch_exists(branch_slug):
            return None
        for record in self._records("menu_item"):
            if record.resource_id == product_slug and _on_branch(
                record.payload, branch_slug
            ):
                return _product(record.resource_id, record.payload)
        return None

    def add_to_cart(self, variant_slug: str, quantity: int, note: str) -> str:
        with self._lock:
            if isinstance(quantity, bool) or quantity < 1:
                raise ValueError("variant_unavailable")
            found = self._available_variant(variant_slug)
            if found is None:
                raise ValueError("variant_unavailable")
            product, variant = found
            line_id = f"L{next(self._line_seq)}"
            self._lines[line_id] = CartLine(
                line_id=line_id,
                variant_slug=variant.slug,
                name=product.name,
                size=variant.size,
                note=note,
                quantity=quantity,
                line_total=variant.price * quantity,
            )
            return line_id

    def remove_from_cart(self, line_id: str) -> bool:
        with self._lock:
            return self._lines.pop(line_id, None) is not None

    def read_cart(self) -> Cart:
        with self._lock:
            lines = tuple(self._lines.values())
            return Cart(lines=lines, total=sum(line.line_total for line in lines))

    def place_order(self, order_type: str, branch_slug: str) -> str:
        with self._lock:
            if order_type not in ORDER_TYPES:
                raise ValueError("invalid_order_type")
            if not self._branch_exists(branch_slug):
                raise ValueError("unknown_branch")
            cart = self.read_cart()
            if not cart.lines:
                raise ValueError("cart_empty")
            TrendCoffeeAdapter().build_request(
                "order.place",
                {
                    "order_type": order_type,
                    "branch_slug": branch_slug,
                    "items": [
                        {
                            "quantity": line.quantity,
                            "variant_slug": line.variant_slug,
                            "note": line.note,
                        }
                        for line in cart.lines
                    ],
                },
            )
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_QUARANTINED,
                "Trend Coffee orders require Task 9 approval before execution",
            )

    def read_order(self, order_id: str) -> Order | None:
        for record in self._records("order"):
            if record.resource_id == order_id:
                return _order(record.resource_id, record.payload)
        return None

    def _branch_exists(self, branch_slug: str) -> bool:
        return any(branch.slug == branch_slug for branch in self.list_branches())

    def _available_variant(self, variant_slug: str) -> tuple[Product, Variant] | None:
        for record in self._records("menu_item"):
            if not _available(record.payload):
                continue
            product = _product(record.resource_id, record.payload)
            if product is None:
                continue
            variants = record.payload.get("variants")
            if not isinstance(variants, list):
                continue
            for raw_variant, variant in zip(variants, product.variants, strict=True):
                if (
                    variant.slug == variant_slug
                    and isinstance(raw_variant, dict)
                    and _available(raw_variant)
                ):
                    return product, variant
        return None

    def _records(self, resource_type: str):
        return self._runtime.snapshots.query(
            StructuredQuery(source_id=self._source_id, resource_type=resource_type)
        ).items


def _available(payload: dict[str, object]) -> bool:
    return payload.get("available") is not False


def _text(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    return value if isinstance(value, str) else ""


def _product(resource_id: str, payload: dict[str, object]) -> Product | None:
    raw_variants = payload.get("variants")
    if not isinstance(raw_variants, list):
        return None
    variants: list[Variant] = []
    for raw_variant in raw_variants:
        if not isinstance(raw_variant, dict):
            return None
        slug = raw_variant.get("slug")
        size = raw_variant.get("size")
        price = raw_variant.get("price")
        if (
            not isinstance(slug, str)
            or not isinstance(size, str)
            or isinstance(price, bool)
            or not isinstance(price, int)
        ):
            return None
        variants.append(Variant(slug=slug, size=size, price=price))
    return Product(
        slug=_text(payload, "slug") or resource_id,
        name=_text(payload, "name"),
        category=_text(payload, "category"),
        available=_available(payload),
        variants=tuple(variants),
    )


def _on_branch(payload: dict[str, object], branch_slug: str) -> bool:
    branch = payload.get("branch")
    if branch is None:
        return True
    if isinstance(branch, str):
        return branch == branch_slug
    if isinstance(branch, list):
        return branch_slug in branch
    return False


def _order(order_id: str, payload: dict[str, object]) -> Order:
    raw_lines = payload.get("orderItems", payload.get("items", []))
    lines: list[CartLine] = []
    if isinstance(raw_lines, list):
        for index, raw_line in enumerate(raw_lines, start=1):
            if not isinstance(raw_line, dict):
                continue
            quantity = raw_line.get("quantity")
            price = raw_line.get("price", 0)
            if (
                isinstance(quantity, bool)
                or not isinstance(quantity, int)
                or isinstance(price, bool)
                or not isinstance(price, int)
            ):
                continue
            lines.append(
                CartLine(
                    line_id=_text(raw_line, "slug") or f"{order_id}:{index}",
                    variant_slug=_text(raw_line, "variant")
                    or _text(raw_line, "variant_slug"),
                    name=_text(raw_line, "name"),
                    size=_text(raw_line, "size"),
                    note=_text(raw_line, "note"),
                    quantity=quantity,
                    line_total=price * quantity,
                )
            )
    total = payload.get("total")
    if isinstance(total, bool) or not isinstance(total, int):
        total = sum(line.line_total for line in lines)
    return Order(
        order_id=order_id,
        order_type=_text(payload, "type"),
        branch_slug=_text(payload, "branch"),
        lines=tuple(lines),
        total=total,
        status=_text(payload, "status"),
    )


__all__ = ["TrendCoffeeMerchant"]
