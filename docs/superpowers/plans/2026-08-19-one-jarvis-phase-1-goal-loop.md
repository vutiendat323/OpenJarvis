# One Jarvis Phase 1 — Goal Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the Agent an ordering capability whose every step forces a
reasoning turn, prove it against a fake merchant, and put what it is doing on
the kiosk screen.

**Architecture:** Ordering tools are ordinary `BaseTool` classes registered in
`ToolRegistry` and enabled by config name, exactly like `calculator`. They talk
to a merchant through a narrow `MerchantPort` protocol; Phase 1 supplies an
in-process fake. Every tool declares `mutates` XOR `observes` in its spec
metadata, and a machine-checked invariant forbids a tool that does both — which
is what structurally prevents a `commerce_checkout()`. Display updates ride the
EventBus to the WebSocket the kiosk browser already holds, and render in a
plain-HTML iframe.

**Tech Stack:** Python 3.11+, pytest, FastAPI, dataclasses, plain HTML/CSS/JS,
React (one line in `KioskPage.tsx`).

**Spec:** `docs/superpowers/specs/2026-08-19-one-jarvis-goal-execution-design.md`
(sections 3 and 5; section 4 is Phase 3, payment is Phase 4)

## Global Constraints

- Do not modify `agents/orchestrator.py`, `agents/runtime.py` or
  `agents/_stubs.py`. They carry the heaviest fork drift (+561, +181, +284).
- Do not modify `workflow/engine.py`. WorkflowEngine stays off the goal path.
- No second composition root. No `CommerceSystem`, `OrderingSystem`,
  `CheckoutEngine`, or any object that decides what to do next. Tools return
  data; only `OrchestratorAgent.run_stream` decides.
- **Every ordering tool declares exactly one of `metadata["mutates"]` or
  `metadata["observes"]`, never both, never neither.** A mutating tool returns
  a minimal acknowledgement and never reports the resulting state.
- No payment tool in this phase. `payment_start` and `payment_verify` are
  Phase 4.
- No capability store, no `merchant_learn`, no network recording. That is
  Phase 3.
- The LLM never emits HTML. Display tools take structured data only.
- Run tests with `.venv/bin/pytest` from `/home/robber/Work/jarvis/OpenJarvis-v2`.
- Branch is `native/runtime`. Do not push or merge. Commit only the files each
  task names.

## Background an engineer needs

**How a tool becomes available to the Agent.** Three steps, all existing:

1. A class decorated `@ToolRegistry.register("name")` subclasses `BaseTool`
   (`tools/_stubs.py:48`) with a `spec` property returning `ToolSpec`
   (`tools/_stubs.py:28`) and an `execute(**params) -> ToolResult`.
2. Its module is imported in `tools/__init__.py` inside a `try/except
   ImportError`, which fires the decorator.
3. `MCPServer()` auto-discovers registered tools (`mcp/server.py:54-58`), and
   `SystemBuilder.build()` keeps the ones named in `config.tools.enabled` or
   `config.agent.tools` (`system/builder.py:476-493`).

Read `tools/calculator.py` for the shape to copy.

**Dependency injection into tools.** `SystemBuilder._inject_tool_deps`
(`system/builder.py:523`) is a hardcoded `if/elif` chain keyed on
`tool.spec.name`. That is the established seam; ordering tools join it rather
than inventing a new mechanism.

**The display channel already exists.** The kiosk browser holds an
authenticated loopback WebSocket at `/v1/agents/events`
(`server/ws_bridge.py:67`; loopback allowed at `server/auth_middleware.py:30`).
`create_ws_router` forwards any event whose type is in `_AGENT_EVENTS`
(`ws_bridge.py:19-32`) to every connected client as
`{"type", "timestamp", "data"}`. Adding a display event means adding one enum
member and one set entry.

**One session at a time.** Confirmed constraint 1 of the spec. The fake
merchant therefore holds a single cart rather than a per-thread map. This is a
deliberate ceiling, marked in code.

## File Structure

| File | Responsibility |
|---|---|
| `src/openjarvis/merchants/__init__.py` (new) | Package marker. |
| `src/openjarvis/merchants/port.py` (new) | `MerchantPort` protocol plus the small dataclasses tools exchange (`MenuItem`, `CartLine`, `Cart`, `Order`). No logic. |
| `src/openjarvis/merchants/fake.py` (new) | `FakeMerchant` — an in-process merchant with a fixed coffee menu. Deterministic, no I/O. |
| `src/openjarvis/tools/ordering.py` (new) | The seven ordering tools. One module: they share the port and are read together. |
| `src/openjarvis/tools/display.py` (new) | `display_menu`, `display_cart`, `display_clear`. Publishes on the bus. |
| `src/openjarvis/server/static/display.html` (new) | Plain HTML/CSS/JS. Own WebSocket, fixed templates, escapes everything. |
| `src/openjarvis/core/events.py` | One new `EventType` member. |
| `src/openjarvis/server/ws_bridge.py` | One new entry in `_AGENT_EVENTS`. |
| `src/openjarvis/tools/__init__.py` | Two new guarded imports. |
| `src/openjarvis/system/builder.py` | Merchant and bus injection for the new tools. |
| `frontend/src/pages/KioskPage.tsx` | One iframe element. |
| `src/openjarvis/skills/data/ordering.toml` (new) | Domain guidance. |
| `tests/merchants/`, `tests/tools/`, `tests/integration/` | Tests per task. |

---

### Task 1: Merchant port and fake merchant

**Files:**
- Create: `src/openjarvis/merchants/__init__.py`
- Create: `src/openjarvis/merchants/port.py`
- Create: `src/openjarvis/merchants/fake.py`
- Test: `tests/merchants/test_fake_merchant.py`

**Interfaces:**
- Produces:
  ```python
  @dataclass(frozen=True, slots=True)
  class MenuItem:
      id: str
      name: str
      price: int          # VND, integer minor-unit-free
      available: bool
      category: str
      options: dict[str, list[str]]   # {"size": ["M","L"], "sugar": ["100","70","50","0"]}

  @dataclass(frozen=True, slots=True)
  class CartLine:
      line_id: str
      item_id: str
      name: str
      options: dict[str, str]
      quantity: int
      line_total: int

  @dataclass(frozen=True, slots=True)
  class Cart:
      lines: tuple[CartLine, ...]
      total: int

  @dataclass(frozen=True, slots=True)
  class Order:
      order_id: str
      lines: tuple[CartLine, ...]
      total: int
      status: str          # "placed" | "unknown"

  class MerchantPort(Protocol):
      def search_menu(self, query: str) -> list[MenuItem]: ...
      def get_item(self, item_id: str) -> MenuItem | None: ...
      def add_to_cart(self, item_id: str, options: dict[str, str], quantity: int) -> str: ...
      def remove_from_cart(self, line_id: str) -> bool: ...
      def read_cart(self) -> Cart: ...
      def place_order(self) -> str: ...
      def read_order(self, order_id: str) -> Order | None: ...

  class FakeMerchant:   # implements MerchantPort
      def __init__(self) -> None: ...
      def set_price(self, item_id: str, price: int) -> None: ...   # test seam
  ```

- [ ] **Step 1: Write the failing test**

Create `tests/merchants/test_fake_merchant.py`:

```python
"""The fake merchant is the ground truth Phase 1 reasons against."""

from __future__ import annotations

from openjarvis.merchants.fake import FakeMerchant


def test_search_finds_by_name_case_insensitively():
    merchant = FakeMerchant()
    results = merchant.search_menu("LATTE")
    assert [item.id for item in results] == ["latte"]


def test_search_with_empty_query_returns_the_whole_menu():
    merchant = FakeMerchant()
    assert len(merchant.search_menu("")) >= 5


def test_add_returns_a_line_id_and_does_not_return_the_cart():
    """The port mirrors the doctrine: a mutation acknowledges, it does not report."""
    merchant = FakeMerchant()
    line_id = merchant.add_to_cart("latte", {"size": "L"}, 1)
    assert isinstance(line_id, str) and line_id


def test_cart_total_reflects_quantity_and_size():
    merchant = FakeMerchant()
    merchant.add_to_cart("latte", {"size": "L"}, 2)
    cart = merchant.read_cart()
    assert len(cart.lines) == 1
    assert cart.lines[0].quantity == 2
    assert cart.total == cart.lines[0].line_total


def test_remove_empties_the_cart():
    merchant = FakeMerchant()
    line_id = merchant.add_to_cart("latte", {}, 1)
    assert merchant.remove_from_cart(line_id) is True
    assert merchant.read_cart().lines == ()


def test_place_order_snapshots_the_cart_and_clears_it():
    merchant = FakeMerchant()
    merchant.add_to_cart("latte", {}, 1)
    order_id = merchant.place_order()

    order = merchant.read_order(order_id)
    assert order is not None
    assert order.status == "placed"
    assert len(order.lines) == 1
    assert merchant.read_cart().lines == ()


def test_read_order_of_an_unknown_id_is_none():
    assert FakeMerchant().read_order("nope") is None


def test_set_price_changes_what_the_merchant_reports():
    """The seam the fast-path safety test will need in Phase 3."""
    merchant = FakeMerchant()
    merchant.set_price("latte", 80_000)
    assert merchant.get_item("latte").price == 80_000
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/merchants/test_fake_merchant.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openjarvis.merchants'`

- [ ] **Step 3: Write the port**

Create `src/openjarvis/merchants/__init__.py`:

```python
"""Merchant access — the narrow surface ordering tools speak to."""
```

Create `src/openjarvis/merchants/port.py`:

```python
"""What an ordering tool may ask a merchant, and what it gets back.

The port deliberately mirrors the mutation/observation doctrine the tools
enforce: ``add_to_cart`` and ``place_order`` return an identifier and nothing
else, so a tool built on them *cannot* report resulting state without calling
an observing method.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional, Protocol, Tuple


@dataclass(frozen=True, slots=True)
class MenuItem:
    id: str
    name: str
    price: int
    available: bool
    category: str
    options: Dict[str, List[str]]


@dataclass(frozen=True, slots=True)
class CartLine:
    line_id: str
    item_id: str
    name: str
    options: Dict[str, str]
    quantity: int
    line_total: int


@dataclass(frozen=True, slots=True)
class Cart:
    lines: Tuple[CartLine, ...]
    total: int


@dataclass(frozen=True, slots=True)
class Order:
    order_id: str
    lines: Tuple[CartLine, ...]
    total: int
    status: str


class MerchantPort(Protocol):
    """The whole merchant surface. Nothing else may be called from a tool."""

    def search_menu(self, query: str) -> List[MenuItem]: ...

    def get_item(self, item_id: str) -> Optional[MenuItem]: ...

    def add_to_cart(
        self, item_id: str, options: Dict[str, str], quantity: int
    ) -> str: ...

    def remove_from_cart(self, line_id: str) -> bool: ...

    def read_cart(self) -> Cart: ...

    def place_order(self) -> str: ...

    def read_order(self, order_id: str) -> Optional[Order]: ...


__all__ = ["Cart", "CartLine", "MenuItem", "MerchantPort", "Order"]
```

- [ ] **Step 4: Write the fake merchant**

Create `src/openjarvis/merchants/fake.py`:

```python
"""An in-process merchant, so the goal loop can be proven without a website."""

from __future__ import annotations

import itertools
from typing import Dict, List, Optional

from openjarvis.merchants.port import Cart, CartLine, MenuItem, Order

_SIZE_SURCHARGE = {"M": 0, "L": 6_000}

_MENU: tuple[MenuItem, ...] = (
    MenuItem("latte", "Latte", 45_000, True, "coffee",
             {"size": ["M", "L"], "sugar": ["100", "70", "50", "0"]}),
    MenuItem("americano", "Americano", 39_000, True, "coffee",
             {"size": ["M", "L"], "ice": ["100", "50", "0"]}),
    MenuItem("cold-brew", "Cold Brew", 55_000, True, "coffee",
             {"size": ["M", "L"]}),
    MenuItem("matcha-latte", "Matcha Latte", 52_000, True, "tea",
             {"size": ["M", "L"], "sugar": ["100", "70", "50", "0"]}),
    MenuItem("peach-tea", "Peach Tea", 42_000, True, "tea",
             {"size": ["M", "L"], "ice": ["100", "50", "0"]}),
    MenuItem("croissant", "Butter Croissant", 35_000, True, "food", {}),
    MenuItem("tiramisu", "Tiramisu", 48_000, False, "food", {}),
)


class FakeMerchant:
    """A deterministic merchant with a fixed menu and one cart.

    One cart, not a map keyed by thread: the design's first confirmed
    constraint is one conversation session at a time per server process.

    # ponytail: single cart, no thread scoping. A second concurrent session
    #   would share this one. Scope by thread_id at the same time as the other
    #   single-session ceilings (NativeAgentRuntime._lock, the voice lease,
    #   the TTS renderer global) -- they only make sense changed together.
    """

    def __init__(self) -> None:
        self._menu: Dict[str, MenuItem] = {item.id: item for item in _MENU}
        self._lines: Dict[str, CartLine] = {}
        self._orders: Dict[str, Order] = {}
        self._line_seq = itertools.count(1)
        self._order_seq = itertools.count(1)

    # -- observation ---------------------------------------------------

    def search_menu(self, query: str) -> List[MenuItem]:
        needle = query.strip().lower()
        if not needle:
            return list(self._menu.values())
        return [
            item
            for item in self._menu.values()
            if needle in item.name.lower() or needle in item.category
        ]

    def get_item(self, item_id: str) -> Optional[MenuItem]:
        return self._menu.get(item_id)

    def read_cart(self) -> Cart:
        lines = tuple(self._lines.values())
        return Cart(lines=lines, total=sum(line.line_total for line in lines))

    def read_order(self, order_id: str) -> Optional[Order]:
        return self._orders.get(order_id)

    # -- mutation ------------------------------------------------------

    def add_to_cart(
        self, item_id: str, options: Dict[str, str], quantity: int
    ) -> str:
        item = self._menu.get(item_id)
        if item is None or not item.available or quantity < 1:
            raise ValueError(item_id)
        unit = item.price + _SIZE_SURCHARGE.get(options.get("size", "M"), 0)
        line_id = f"L{next(self._line_seq)}"
        self._lines[line_id] = CartLine(
            line_id=line_id,
            item_id=item_id,
            name=item.name,
            options=dict(options),
            quantity=quantity,
            line_total=unit * quantity,
        )
        return line_id

    def remove_from_cart(self, line_id: str) -> bool:
        return self._lines.pop(line_id, None) is not None

    def place_order(self) -> str:
        cart = self.read_cart()
        order_id = f"ORD{next(self._order_seq):04d}"
        self._orders[order_id] = Order(
            order_id=order_id,
            lines=cart.lines,
            total=cart.total,
            status="placed",
        )
        self._lines.clear()
        return order_id

    # -- test seam -----------------------------------------------------

    def set_price(self, item_id: str, price: int) -> None:
        """Change a price behind the Agent's back, as a real site could."""
        item = self._menu[item_id]
        self._menu[item_id] = MenuItem(
            item.id, item.name, price, item.available, item.category, item.options
        )


__all__ = ["FakeMerchant"]
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/merchants/test_fake_merchant.py -v`
Expected: all 8 PASS

- [ ] **Step 6: Commit**

```bash
git add src/openjarvis/merchants/ tests/merchants/
git commit -m "feat(merchants): a port and an in-process merchant

The port mirrors the doctrine it serves: add_to_cart and place_order
return an identifier and nothing else, so a tool built on them cannot
report resulting state without asking for it.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Menu observation tools

**Files:**
- Create: `src/openjarvis/tools/ordering.py`
- Modify: `src/openjarvis/tools/__init__.py`
- Test: `tests/tools/test_ordering_menu.py`

**Interfaces:**
- Consumes: `MerchantPort`, `FakeMerchant` from Task 1.
- Produces: registered tool names `menu_search`, `menu_item`. Both carry
  `spec.metadata == {"observes": True}`. Each tool instance exposes a
  `_merchant` attribute that `SystemBuilder` injects in Task 5; it is `None`
  until then and every tool returns a failed `ToolResult` when it is `None`.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_ordering_menu.py`:

```python
"""Menu tools observe. They never change anything."""

from __future__ import annotations

import json

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.tools.ordering import MenuItemTool, MenuSearchTool


def _tool(cls):
    tool = cls()
    tool._merchant = FakeMerchant()
    return tool


def test_menu_search_declares_itself_an_observation():
    assert MenuSearchTool().spec.metadata == {"observes": True}


def test_menu_search_returns_matching_items_as_json():
    result = _tool(MenuSearchTool).execute(query="latte")
    assert result.success
    payload = json.loads(result.content)
    assert [item["id"] for item in payload["items"]] == ["latte"]
    assert payload["items"][0]["price"] == 45_000
    assert payload["items"][0]["available"] is True


def test_menu_search_reports_unavailable_items_rather_than_hiding_them():
    """The Agent must be able to say 'tiramisu is sold out' instead of
    silently pretending it does not exist."""
    result = _tool(MenuSearchTool).execute(query="tiramisu")
    payload = json.loads(result.content)
    assert payload["items"][0]["available"] is False


def test_menu_item_returns_options():
    result = _tool(MenuItemTool).execute(item_id="latte")
    payload = json.loads(result.content)
    assert payload["options"]["size"] == ["M", "L"]


def test_menu_item_unknown_id_fails_without_raising():
    result = _tool(MenuItemTool).execute(item_id="unicorn-frappe")
    assert result.success is False
    assert "unknown_item" in result.content


def test_tool_without_a_merchant_fails_clearly():
    result = MenuSearchTool().execute(query="latte")
    assert result.success is False
    assert "merchant_unavailable" in result.content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/tools/test_ordering_menu.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openjarvis.tools.ordering'`

- [ ] **Step 3: Write the module head and the two menu tools**

Create `src/openjarvis/tools/ordering.py`:

```python
"""Ordering tools.

Every tool here declares exactly one of ``mutates`` or ``observes`` in its spec
metadata, and ``tests/tools/test_ordering_doctrine.py`` enforces it. That is
what structurally prevents a single ``commerce_checkout()``: such a tool would
have to both change the order and report what the order became, and no tool is
allowed to do both. The Agent therefore has to reason between every action and
its consequence.

Internals may be as deterministic as they like. Combining a mutation with its
observation is the only thing forbidden.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Optional

from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.merchants.port import MerchantPort
from openjarvis.tools._stubs import BaseTool, ToolSpec

OBSERVES = {"observes": True}
MUTATES = {"mutates": True}


class _MerchantTool(BaseTool):
    """Shared plumbing: the injected merchant and the failure when it is absent."""

    def __init__(self) -> None:
        # Set by SystemBuilder._inject_tool_deps. None outside a built system.
        self._merchant: Optional[MerchantPort] = None

    def _require_merchant(self) -> Optional[ToolResult]:
        if self._merchant is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="merchant_unavailable: no merchant is configured",
            )
        return None

    @staticmethod
    def _ok(name: str, payload: dict[str, Any]) -> ToolResult:
        return ToolResult(tool_name=name, success=True, content=json.dumps(payload))

    @staticmethod
    def _fail(name: str, reason: str) -> ToolResult:
        return ToolResult(tool_name=name, success=False, content=reason)


@ToolRegistry.register("menu_search")
class MenuSearchTool(_MerchantTool):
    """Find items. This is where recommendation gets its material."""

    tool_id = "menu_search"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="menu_search",
            description=(
                "Search the merchant's menu. Returns matching items with id, "
                "name, price and availability. An empty query returns the "
                "whole menu. Use this to recommend, compare or check what "
                "exists. Read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "Name or category to match; empty for all.",
                    }
                },
                "required": ["query"],
            },
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        items = self._merchant.search_menu(str(params.get("query", "")))
        return self._ok("menu_search", {"items": [asdict(item) for item in items]})


@ToolRegistry.register("menu_item")
class MenuItemTool(_MerchantTool):
    """Read one item in full, including the options it accepts."""

    tool_id = "menu_item"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="menu_item",
            description=(
                "Read one menu item by id: price, availability and the "
                "options it accepts (size, sugar, ice). Call this before "
                "adding an item whose options you have not seen. Read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "description": "Menu item id."}
                },
                "required": ["item_id"],
            },
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        item = self._merchant.get_item(str(params.get("item_id", "")))
        if item is None:
            return self._fail("menu_item", "unknown_item")
        return self._ok("menu_item", asdict(item))
```

- [ ] **Step 4: Register the module so the decorators fire**

In `src/openjarvis/tools/__init__.py`, add alongside the other guarded imports:

```python
try:
    import openjarvis.tools.ordering  # noqa: F401
except ImportError:
    pass
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/tools/test_ordering_menu.py -v`
Expected: all 6 PASS

- [ ] **Step 6: Commit**

```bash
git add src/openjarvis/tools/ordering.py src/openjarvis/tools/__init__.py tests/tools/test_ordering_menu.py
git commit -m "feat(ordering): menu observation tools

menu_search reports sold-out items rather than hiding them, so the Agent
can say what is unavailable instead of pretending it does not exist.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Cart tools — the mutation/observation pair

**Files:**
- Modify: `src/openjarvis/tools/ordering.py`
- Test: `tests/tools/test_ordering_cart.py`

**Interfaces:**
- Consumes: `_MerchantTool`, `OBSERVES`, `MUTATES` from Task 2.
- Produces: registered tool names `cart_add` (`{"mutates": True}`),
  `cart_remove` (`{"mutates": True}`), `cart_view` (`{"observes": True}`).

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_ordering_cart.py`:

```python
"""cart_add acknowledges. Only cart_view says what is true."""

from __future__ import annotations

import json

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.tools.ordering import CartAddTool, CartRemoveTool, CartViewTool


def _wired(merchant, *classes):
    tools = []
    for cls in classes:
        tool = cls()
        tool._merchant = merchant
        tools.append(tool)
    return tools


def test_cart_add_declares_a_mutation_and_cart_view_an_observation():
    assert CartAddTool().spec.metadata == {"mutates": True}
    assert CartRemoveTool().spec.metadata == {"mutates": True}
    assert CartViewTool().spec.metadata == {"observes": True}


def test_cart_add_returns_only_an_acknowledgement():
    """The doctrine, enforced at the payload level: no total, no lines,
    nothing the Agent could mistake for the state of the cart."""
    add, = _wired(FakeMerchant(), CartAddTool)
    result = add.execute(item_id="latte", options={"size": "L"}, quantity=1)

    assert result.success
    payload = json.loads(result.content)
    assert set(payload) == {"added", "line_id"}
    assert payload["added"] is True


def test_cart_view_reports_lines_and_total():
    merchant = FakeMerchant()
    add, view = _wired(merchant, CartAddTool, CartViewTool)
    add.execute(item_id="latte", options={"size": "L"}, quantity=2)

    payload = json.loads(view.execute().content)
    assert len(payload["lines"]) == 1
    assert payload["lines"][0]["quantity"] == 2
    assert payload["total"] == payload["lines"][0]["line_total"]


def test_cart_add_rejects_an_unavailable_item():
    add, = _wired(FakeMerchant(), CartAddTool)
    result = add.execute(item_id="tiramisu", options={}, quantity=1)
    assert result.success is False
    assert "item_unavailable" in result.content


def test_cart_add_rejects_an_unknown_item():
    add, = _wired(FakeMerchant(), CartAddTool)
    result = add.execute(item_id="unicorn-frappe", options={}, quantity=1)
    assert result.success is False
    assert "item_unavailable" in result.content


def test_cart_remove_acknowledges_and_cart_view_confirms():
    merchant = FakeMerchant()
    add, remove, view = _wired(merchant, CartAddTool, CartRemoveTool, CartViewTool)
    line_id = json.loads(
        add.execute(item_id="latte", options={}, quantity=1).content
    )["line_id"]

    removed = json.loads(remove.execute(line_id=line_id).content)
    assert set(removed) == {"removed"}
    assert removed["removed"] is True
    assert json.loads(view.execute().content)["lines"] == []


def test_cart_remove_of_an_unknown_line_fails():
    remove, = _wired(FakeMerchant(), CartRemoveTool)
    result = remove.execute(line_id="L999")
    assert result.success is False
    assert "unknown_line" in result.content
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/tools/test_ordering_cart.py -v`
Expected: FAIL — `ImportError: cannot import name 'CartAddTool'`

- [ ] **Step 3: Append the three cart tools**

Append to `src/openjarvis/tools/ordering.py`:

```python
@ToolRegistry.register("cart_add")
class CartAddTool(_MerchantTool):
    """Add one item. Returns an acknowledgement, never the cart."""

    tool_id = "cart_add"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cart_add",
            description=(
                "Add an item to the cart. Returns only whether it was added "
                "and the new line id -- it does NOT tell you what the cart "
                "now contains. Call cart_view afterwards to see the real "
                "cart and check it against what the customer asked for."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "item_id": {"type": "string", "description": "Menu item id."},
                    "options": {
                        "type": "object",
                        "description": (
                            "Chosen options, e.g. {\"size\": \"L\", "
                            "\"sugar\": \"50\"}. Use menu_item to see what "
                            "an item accepts."
                        ),
                    },
                    "quantity": {"type": "integer", "description": "How many."},
                },
                "required": ["item_id"],
            },
            category="ordering",
            metadata=dict(MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        options = params.get("options") or {}
        if not isinstance(options, dict):
            return self._fail("cart_add", "options_must_be_an_object")
        try:
            line_id = self._merchant.add_to_cart(
                str(params.get("item_id", "")),
                {str(k): str(v) for k, v in options.items()},
                int(params.get("quantity", 1) or 1),
            )
        except (ValueError, TypeError):
            return self._fail("cart_add", "item_unavailable")
        return self._ok("cart_add", {"added": True, "line_id": line_id})


@ToolRegistry.register("cart_remove")
class CartRemoveTool(_MerchantTool):
    """Remove one line. Returns an acknowledgement, never the cart."""

    tool_id = "cart_remove"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cart_remove",
            description=(
                "Remove one line from the cart by its line id. Returns only "
                "whether it was removed. Call cart_view afterwards to see "
                "what the cart now contains."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "line_id": {
                        "type": "string",
                        "description": "Line id returned by cart_add or cart_view.",
                    }
                },
                "required": ["line_id"],
            },
            category="ordering",
            metadata=dict(MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        if not self._merchant.remove_from_cart(str(params.get("line_id", ""))):
            return self._fail("cart_remove", "unknown_line")
        return self._ok("cart_remove", {"removed": True})


@ToolRegistry.register("cart_view")
class CartViewTool(_MerchantTool):
    """Read the real cart. This is the only thing that says what is in it."""

    tool_id = "cart_view"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="cart_view",
            description=(
                "Read the current cart: every line with its options, "
                "quantity and price, plus the total. This is the only way "
                "to know what the cart contains. Read-only."
            ),
            parameters={"type": "object", "properties": {}},
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        cart = self._merchant.read_cart()
        return self._ok(
            "cart_view",
            {
                "lines": [asdict(line) for line in cart.lines],
                "total": cart.total,
            },
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/tools/test_ordering_cart.py -v`
Expected: all 7 PASS

- [ ] **Step 5: Commit**

```bash
git add src/openjarvis/tools/ordering.py tests/tools/test_ordering_cart.py
git commit -m "feat(ordering): cart tools, acknowledgement separated from truth

cart_add returns {added, line_id} and nothing else. A test asserts the
exact key set, so a well-meaning future change that folds the total into
the acknowledgement fails rather than quietly removing a reasoning step.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Order tools

**Files:**
- Modify: `src/openjarvis/tools/ordering.py`
- Test: `tests/tools/test_ordering_order.py`

**Interfaces:**
- Produces: registered tool names `order_place` (`{"mutates": True}`),
  `order_verify` (`{"observes": True}`).

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_ordering_order.py`:

```python
"""order_place hands back an id. order_verify says what the merchant thinks."""

from __future__ import annotations

import json

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.tools.ordering import (
    CartAddTool,
    OrderPlaceTool,
    OrderVerifyTool,
)


def _wired(merchant, *classes):
    tools = []
    for cls in classes:
        tool = cls()
        tool._merchant = merchant
        tools.append(tool)
    return tools


def test_metadata_declares_one_kind_each():
    assert OrderPlaceTool().spec.metadata == {"mutates": True}
    assert OrderVerifyTool().spec.metadata == {"observes": True}


def test_order_place_returns_only_an_order_id():
    merchant = FakeMerchant()
    add, place = _wired(merchant, CartAddTool, OrderPlaceTool)
    add.execute(item_id="latte", options={}, quantity=1)

    payload = json.loads(place.execute().content)
    assert set(payload) == {"placed", "order_id"}
    assert payload["placed"] is True


def test_order_place_refuses_an_empty_cart():
    place, = _wired(FakeMerchant(), OrderPlaceTool)
    result = place.execute()
    assert result.success is False
    assert "cart_empty" in result.content


def test_order_verify_reports_what_the_merchant_says():
    merchant = FakeMerchant()
    add, place, verify = _wired(
        merchant, CartAddTool, OrderPlaceTool, OrderVerifyTool
    )
    add.execute(item_id="latte", options={"size": "L"}, quantity=2)
    order_id = json.loads(place.execute().content)["order_id"]

    payload = json.loads(verify.execute(order_id=order_id).content)
    assert payload["order_id"] == order_id
    assert payload["status"] == "placed"
    assert payload["lines"][0]["quantity"] == 2
    assert payload["total"] == payload["lines"][0]["line_total"]


def test_order_verify_of_an_unknown_order_fails():
    verify, = _wired(FakeMerchant(), OrderVerifyTool)
    result = verify.execute(order_id="ORD9999")
    assert result.success is False
    assert "unknown_order" in result.content


def test_a_price_change_behind_the_agent_is_visible_through_verify():
    """The merchant is the authority. If it disagrees with what the Agent
    believed, verify is where that surfaces."""
    merchant = FakeMerchant()
    add, place, verify = _wired(
        merchant, CartAddTool, OrderPlaceTool, OrderVerifyTool
    )
    add.execute(item_id="latte", options={}, quantity=1)
    order_id = json.loads(place.execute().content)["order_id"]

    merchant.set_price("latte", 80_000)

    payload = json.loads(verify.execute(order_id=order_id).content)
    assert payload["total"] == 45_000  # the order was priced when placed
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/tools/test_ordering_order.py -v`
Expected: FAIL — `ImportError: cannot import name 'OrderPlaceTool'`

- [ ] **Step 3: Append the two order tools**

Append to `src/openjarvis/tools/ordering.py`:

```python
@ToolRegistry.register("order_place")
class OrderPlaceTool(_MerchantTool):
    """Place the order. Does not pay, and does not say what was placed."""

    tool_id = "order_place"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="order_place",
            description=(
                "Place the current cart as an order. Does NOT take payment. "
                "Returns only the new order id -- it does not tell you what "
                "the order contains. Call order_verify with that id to read "
                "back what the merchant actually recorded, and compare it "
                "with what the customer asked for before going further."
            ),
            parameters={"type": "object", "properties": {}},
            category="ordering",
            metadata=dict(MUTATES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        if not self._merchant.read_cart().lines:
            return self._fail("order_place", "cart_empty")
        order_id = self._merchant.place_order()
        return self._ok("order_place", {"placed": True, "order_id": order_id})


@ToolRegistry.register("order_verify")
class OrderVerifyTool(_MerchantTool):
    """Read the order back from the merchant."""

    tool_id = "order_verify"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="order_verify",
            description=(
                "Read an order back from the merchant by id: its lines, "
                "total and status, as the merchant records them. This is "
                "the merchant's answer, not yours -- check it against what "
                "the customer asked for. Read-only."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "order_id": {
                        "type": "string",
                        "description": "Order id returned by order_place.",
                    }
                },
                "required": ["order_id"],
            },
            category="ordering",
            metadata=dict(OBSERVES),
        )

    def execute(self, **params: Any) -> ToolResult:
        unavailable = self._require_merchant()
        if unavailable is not None:
            return unavailable
        order = self._merchant.read_order(str(params.get("order_id", "")))
        if order is None:
            return self._fail("order_verify", "unknown_order")
        return self._ok(
            "order_verify",
            {
                "order_id": order.order_id,
                "status": order.status,
                "lines": [asdict(line) for line in order.lines],
                "total": order.total,
            },
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/tools/test_ordering_order.py -v`
Expected: all 6 PASS

- [ ] **Step 5: Commit**

```bash
git add src/openjarvis/tools/ordering.py tests/tools/test_ordering_order.py
git commit -m "feat(ordering): order_place and order_verify

order_place returns an id and nothing else, so the Agent must ask the
merchant what it recorded before telling the customer anything.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: The doctrine invariant, and wiring the merchant in

**Files:**
- Create: `tests/tools/test_ordering_doctrine.py`
- Modify: `src/openjarvis/system/builder.py:523` (`_inject_tool_deps`)
- Modify: `src/openjarvis/core/config.py`
- Test: `tests/system/test_ordering_wiring.py`

**Interfaces:**
- Produces: `SystemBuilder` injects a `FakeMerchant` into every tool whose
  `spec.category == "ordering"` when `config.merchants.backend == "fake"`.
  New config section:
  ```python
  class MerchantsConfig:
      backend: str = "fake"     # "fake" | "none"
  ```
  `JarvisConfig.merchants: MerchantsConfig`.

- [ ] **Step 1: Write the doctrine invariant test**

Create `tests/tools/test_ordering_doctrine.py`:

```python
"""Mutation and observation never share a tool.

This is the rule that structurally prevents a single commerce_checkout():
such a tool would have to both change the order and report what it became.
If this test fails, a reasoning step has been removed from the Agent's loop.
"""

from __future__ import annotations

import inspect

import pytest

from openjarvis.tools import ordering
from openjarvis.tools.ordering import _MerchantTool


def _ordering_tools():
    """Enumerate the tool classes from the module, not from ToolRegistry.

    `tests/conftest.py::_clean_registries` calls `ToolRegistry.clear()` autouse
    before every test, so a registry walk here would iterate nothing and every
    assertion below would pass vacuously -- a silent hole in the one test that
    guards the doctrine.
    """
    return [
        member()
        for _, member in inspect.getmembers(ordering, inspect.isclass)
        if issubclass(member, _MerchantTool)
        and member is not _MerchantTool
        and member.__module__ == ordering.__name__
    ]


def test_every_tool_in_the_module_is_checked():
    """A new ordering tool is covered automatically; this pins the set so a
    silently emptied list cannot make the parametrized tests vacuous."""
    assert {tool.spec.name for tool in _ordering_tools()} == {
        "menu_search",
        "menu_item",
        "cart_add",
        "cart_remove",
        "cart_view",
        "order_place",
        "order_verify",
    }


def test_every_tool_is_in_the_ordering_category():
    assert all(tool.spec.category == "ordering" for tool in _ordering_tools())


@pytest.mark.parametrize("tool", _ordering_tools(), ids=lambda t: t.spec.name)
def test_each_tool_declares_exactly_one_kind(tool):
    metadata = tool.spec.metadata
    mutates = bool(metadata.get("mutates"))
    observes = bool(metadata.get("observes"))
    assert mutates != observes, (
        f"{tool.spec.name} declares mutates={mutates} observes={observes}; "
        "a tool must be exactly one of the two"
    )


@pytest.mark.parametrize("tool", _ordering_tools(), ids=lambda t: t.spec.name)
def test_a_mutating_tool_takes_no_observation_shortcut(tool):
    """A mutating tool's description must send the Agent to an observer.

    Prose, not structure -- but the failure it guards against is a future
    edit that quietly makes cart_add return the cart 'for convenience'.
    """
    if not tool.spec.metadata.get("mutates"):
        pytest.skip("observation tool")
    assert any(
        observer in tool.spec.description
        for observer in ("cart_view", "order_verify")
    ), f"{tool.spec.name} does not tell the Agent how to see the result"
```

- [ ] **Step 2: Run it**

Run: `.venv/bin/pytest tests/tools/test_ordering_doctrine.py -v`
Expected: PASS — tasks 2 through 4 already satisfy it. If it fails, an earlier
task is wrong; fix that task rather than relaxing this test.

- [ ] **Step 3: Write the wiring test**

Create `tests/system/test_ordering_wiring.py`:

```python
"""Ordering tools reach a merchant through the builder's existing seam."""

from __future__ import annotations

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools.ordering import CartViewTool, MenuSearchTool


def test_inject_tool_deps_gives_ordering_tools_a_merchant():
    merchant = FakeMerchant()
    tools = [MenuSearchTool(), CartViewTool()]

    for tool in tools:
        SystemBuilder._inject_ordering_merchant(tool, merchant)

    assert all(tool._merchant is merchant for tool in tools)


def test_non_ordering_tools_are_untouched():
    from openjarvis.tools.calculator import CalculatorTool

    tool = CalculatorTool()
    SystemBuilder._inject_ordering_merchant(tool, FakeMerchant())

    assert not hasattr(tool, "_merchant") or tool._merchant is None
```

If `tools/calculator.py` names its class something other than
`CalculatorTool`, use the real name — read the file.

- [ ] **Step 4: Run it to verify it fails**

Run: `.venv/bin/pytest tests/system/test_ordering_wiring.py -v`
Expected: FAIL — `AttributeError: type object 'SystemBuilder' has no attribute
'_inject_ordering_merchant'`

- [ ] **Step 5: Add the config section**

In `src/openjarvis/core/config.py`, following the pattern of the neighbouring
config dataclasses, add:

```python
@dataclass
class MerchantsConfig:
    """Which merchant the ordering tools talk to.

    ``fake`` is an in-process merchant with a fixed menu, which is what the
    goal loop is proven against. ``none`` leaves the tools without a merchant,
    so each one fails with ``merchant_unavailable`` rather than guessing.
    """

    backend: str = "fake"
```

and add the field to `JarvisConfig`:

```python
    merchants: MerchantsConfig = field(default_factory=MerchantsConfig)
```

- [ ] **Step 6: Add the injection helper and call it**

In `src/openjarvis/system/builder.py`, add this static method next to
`_inject_tool_deps`:

```python
    @staticmethod
    def _inject_ordering_merchant(tool, merchant) -> None:
        """Hand the merchant to every ordering tool.

        Keyed on category rather than on each tool name, so adding an eighth
        ordering tool does not mean remembering to edit this method.
        """
        if tool.spec.category == "ordering" and hasattr(tool, "_merchant"):
            tool._merchant = merchant
```

Then, in `build()`, immediately after the existing loop that calls
`self._inject_tool_deps(...)` (`system/builder.py:473-474`), add:

```python
        merchant = None
        if getattr(config, "merchants", None) is not None:
            if config.merchants.backend == "fake":
                from openjarvis.merchants.fake import FakeMerchant

                merchant = FakeMerchant()
        if merchant is not None:
            for tool in internal_server.get_tools():
                self._inject_ordering_merchant(tool, merchant)
```

One `FakeMerchant` per built system, shared by every ordering tool — the cart
must be the same object the whole conversation sees.

- [ ] **Step 7: Run both test files**

Run: `.venv/bin/pytest tests/tools/test_ordering_doctrine.py tests/system/test_ordering_wiring.py -v`
Expected: PASS

- [ ] **Step 8: Run the existing system tests for regressions**

Run: `.venv/bin/pytest tests/system/ tests/tools/ -v`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add tests/tools/test_ordering_doctrine.py tests/system/test_ordering_wiring.py src/openjarvis/system/builder.py src/openjarvis/core/config.py
git commit -m "feat(system): wire ordering tools to a merchant, pin the doctrine

Injection is keyed on the ordering category rather than tool names, so an
eighth tool does not mean remembering to edit the builder. The doctrine
test fails if any tool ever declares both mutates and observes.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: The display channel and its tools

**Files:**
- Modify: `src/openjarvis/core/events.py`
- Modify: `src/openjarvis/server/ws_bridge.py:19-32`
- Create: `src/openjarvis/tools/display.py`
- Modify: `src/openjarvis/tools/__init__.py`
- Modify: `src/openjarvis/system/builder.py`
- Test: `tests/tools/test_display.py`

**Interfaces:**
- Produces: `EventType.DISPLAY_UPDATE = "display_update"`; registered tool names
  `display_menu`, `display_cart`, `display_clear`, all with
  `spec.category == "display"` and `spec.metadata == {"displays": True}` —
  deliberately neither `mutates` nor `observes`, because they do not touch
  merchant state. Each publishes
  `{"view": "menu"|"cart"|"none", ...}` on `EventType.DISPLAY_UPDATE`.
  Tool instances carry a `_bus` attribute injected by `SystemBuilder`.

- [ ] **Step 1: Write the failing test**

Create `tests/tools/test_display.py`:

```python
"""Display tools publish structured data. They never emit markup."""

from __future__ import annotations

from openjarvis.core.events import EventBus, EventType
from openjarvis.tools.display import DisplayCartTool, DisplayClearTool, DisplayMenuTool


class _Recorder:
    def __init__(self, bus):
        self.events = []
        bus.subscribe(EventType.DISPLAY_UPDATE, self.events.append)


def _wired(cls):
    bus = EventBus()
    recorder = _Recorder(bus)
    tool = cls()
    tool._bus = bus
    return tool, recorder


def test_display_tools_are_neither_mutations_nor_observations():
    """They draw; they do not touch merchant state, so the doctrine test
    must not classify them."""
    for cls in (DisplayMenuTool, DisplayCartTool, DisplayClearTool):
        metadata = cls().spec.metadata
        assert metadata == {"displays": True}
        assert cls().spec.category == "display"


def test_display_menu_publishes_the_items_it_was_given():
    tool, recorder = _wired(DisplayMenuTool)
    result = tool.execute(
        items=[
            {"id": "latte", "name": "Latte", "price": 45000, "available": True},
        ]
    )

    assert result.success
    assert len(recorder.events) == 1
    data = recorder.events[0].data
    assert data["view"] == "menu"
    assert data["items"][0]["name"] == "Latte"


def test_display_menu_keeps_only_the_fields_the_page_renders():
    """Anything else the model invents must not reach the page."""
    tool, recorder = _wired(DisplayMenuTool)
    tool.execute(
        items=[
            {
                "id": "latte",
                "name": "Latte",
                "price": 45000,
                "available": True,
                "html": "<script>alert(1)</script>",
                "onclick": "steal()",
            }
        ]
    )

    item = recorder.events[0].data["items"][0]
    assert set(item) <= {"id", "name", "price", "available", "image_url", "note"}


def test_display_cart_publishes_lines_and_total():
    tool, recorder = _wired(DisplayCartTool)
    tool.execute(
        lines=[{"name": "Latte", "quantity": 2, "line_total": 102000}],
        total=102000,
    )

    data = recorder.events[0].data
    assert data["view"] == "cart"
    assert data["total"] == 102000
    assert data["lines"][0]["quantity"] == 2


def test_display_clear_publishes_an_empty_view():
    tool, recorder = _wired(DisplayClearTool)
    tool.execute()
    assert recorder.events[0].data == {"view": "none"}


def test_a_display_tool_without_a_bus_fails_rather_than_silently_doing_nothing():
    result = DisplayMenuTool().execute(items=[])
    assert result.success is False
    assert "display_unavailable" in result.content


def test_display_update_is_forwarded_to_websocket_clients():
    from openjarvis.server.ws_bridge import _AGENT_EVENTS

    assert EventType.DISPLAY_UPDATE in _AGENT_EVENTS
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/pytest tests/tools/test_display.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'openjarvis.tools.display'`

- [ ] **Step 3: Add the event type**

In `src/openjarvis/core/events.py`, add to `EventType` after
`KIOSK_STATE_CHANGED`:

```python
    DISPLAY_UPDATE = "display_update"
```

- [ ] **Step 4: Forward it to connected browsers**

In `src/openjarvis/server/ws_bridge.py`, add to `_AGENT_EVENTS`:

```python
    EventType.DISPLAY_UPDATE,
```

- [ ] **Step 5: Write the display tools**

Create `src/openjarvis/tools/display.py`:

```python
"""What the customer sees, as structured data on the event bus.

The LLM never emits markup. It supplies fields; the page renders them with
fixed templates and escapes every string. Anything else would be an XSS
surface and an unpredictable layout, on a screen a customer is looking at.

These tools are exempt from the mutation/observation doctrine: they do not
touch merchant state, they draw. They declare ``displays`` so the doctrine
test does not classify them as either.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from openjarvis.core.events import EventBus, EventType
from openjarvis.core.registry import ToolRegistry
from openjarvis.core.types import ToolResult
from openjarvis.tools._stubs import BaseTool, ToolSpec

DISPLAYS = {"displays": True}

# Only these reach the page. A model that invents an "html" or "onclick" field
# gets it dropped here rather than at render time.
_ITEM_FIELDS = ("id", "name", "price", "available", "image_url", "note")
_LINE_FIELDS = ("name", "options", "quantity", "line_total")


class _DisplayTool(BaseTool):
    def __init__(self) -> None:
        # Set by SystemBuilder._inject_display_bus.
        self._bus: Optional[EventBus] = None

    def _publish(self, payload: dict[str, Any]) -> ToolResult:
        if self._bus is None:
            return ToolResult(
                tool_name=self.spec.name,
                success=False,
                content="display_unavailable: no event bus is configured",
            )
        self._bus.publish(EventType.DISPLAY_UPDATE, payload)
        return ToolResult(
            tool_name=self.spec.name,
            success=True,
            content=json.dumps({"shown": payload["view"]}),
        )


def _picked(row: Any, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    return {key: row[key] for key in fields if key in row}


@ToolRegistry.register("display_menu")
class DisplayMenuTool(_DisplayTool):
    """Put a shortlist of items on the customer's screen."""

    tool_id = "display_menu"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_menu",
            description=(
                "Show items on the customer's screen. Pass the few you are "
                "actually recommending, not everything menu_search returned "
                "-- choosing what to show is part of the recommendation. "
                "Each item: id, name, price, available, and optionally "
                "image_url and note."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "description": "Items to show, in the order to show them.",
                        "items": {"type": "object"},
                    }
                },
                "required": ["items"],
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        rows = params.get("items") or []
        items = [_picked(row, _ITEM_FIELDS) for row in rows]
        return self._publish({"view": "menu", "items": [i for i in items if i]})


@ToolRegistry.register("display_cart")
class DisplayCartTool(_DisplayTool):
    """Put the current cart on the customer's screen."""

    tool_id = "display_cart"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_cart",
            description=(
                "Show the cart on the customer's screen so they can check it "
                "before ordering. Pass the lines and total you read from "
                "cart_view -- do not invent them."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "lines": {
                        "type": "array",
                        "description": "Cart lines from cart_view.",
                        "items": {"type": "object"},
                    },
                    "total": {"type": "integer", "description": "Cart total."},
                },
                "required": ["lines", "total"],
            },
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        rows = params.get("lines") or []
        lines = [_picked(row, _LINE_FIELDS) for row in rows]
        try:
            total = int(params.get("total", 0) or 0)
        except (TypeError, ValueError):
            total = 0
        return self._publish(
            {"view": "cart", "lines": [line for line in lines if line], "total": total}
        )


@ToolRegistry.register("display_clear")
class DisplayClearTool(_DisplayTool):
    """Return the screen to its idle state."""

    tool_id = "display_clear"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="display_clear",
            description="Clear the customer's screen back to its idle state.",
            parameters={"type": "object", "properties": {}},
            category="display",
            metadata=dict(DISPLAYS),
        )

    def execute(self, **params: Any) -> ToolResult:
        return self._publish({"view": "none"})
```

- [ ] **Step 6: Register the module**

In `src/openjarvis/tools/__init__.py`, add alongside the other guarded imports:

```python
try:
    import openjarvis.tools.display  # noqa: F401
except ImportError:
    pass
```

- [ ] **Step 7: Inject the bus**

In `src/openjarvis/system/builder.py`, add next to `_inject_ordering_merchant`:

```python
    @staticmethod
    def _inject_display_bus(tool, bus) -> None:
        """Hand the event bus to every display tool."""
        if tool.spec.category == "display" and hasattr(tool, "_bus"):
            tool._bus = bus
```

and in `build()`, in the same loop added in Task 5:

```python
        for tool in internal_server.get_tools():
            if merchant is not None:
                self._inject_ordering_merchant(tool, merchant)
            self._inject_display_bus(tool, bus)
```

replacing the loop written in Task 5 Step 6. Use whatever local name the
surrounding code already has for the event bus — read `build()` and match it.

- [ ] **Step 8: Run the tests to verify they pass**

Run: `.venv/bin/pytest tests/tools/test_display.py -v`
Expected: all 7 PASS

- [ ] **Step 9: Run the ws_bridge tests for regressions**

Run: `.venv/bin/pytest tests/server/test_ws_bridge.py -v`
Expected: PASS

- [ ] **Step 10: Commit**

```bash
git add src/openjarvis/core/events.py src/openjarvis/server/ws_bridge.py src/openjarvis/tools/display.py src/openjarvis/tools/__init__.py src/openjarvis/system/builder.py tests/tools/test_display.py
git commit -m "feat(display): structured screen updates over the existing socket

The kiosk browser already holds an authenticated loopback WebSocket, so
this adds one event type and one forwarding entry rather than a transport.
Only known fields reach the page; an invented html or onclick is dropped
at the tool, not at render time.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: The display page

**Files:**
- Create: `src/openjarvis/server/static/display.html`
- Modify: `frontend/src/pages/KioskPage.tsx`
- Test: `tests/server/test_display_page.py`

**Interfaces:**
- Consumes: `EventType.DISPLAY_UPDATE` payloads from Task 6.
- Produces: a page served at the existing static mount that renders
  `{"view": "menu"|"cart"|"none", ...}`.

- [ ] **Step 1: Write the failing test**

Create `tests/server/test_display_page.py`:

```python
"""The page escapes merchant text and never builds markup from data."""

from __future__ import annotations

from pathlib import Path

import pytest

PAGE = (
    Path(__file__).resolve().parents[2]
    / "src/openjarvis/server/static/display.html"
)


def test_the_page_exists():
    assert PAGE.is_file()


def test_the_page_never_assigns_innerhtml():
    """textContent only. innerHTML with merchant-supplied text is an XSS hole
    on a screen a customer is standing in front of."""
    source = PAGE.read_text(encoding="utf-8")
    assert "innerHTML" not in source
    assert "insertAdjacentHTML" not in source
    assert "document.write" not in source


def test_the_page_connects_to_the_existing_event_socket():
    source = PAGE.read_text(encoding="utf-8")
    assert "/v1/agents/events" in source


def test_the_page_loads_nothing_from_a_third_party():
    source = PAGE.read_text(encoding="utf-8")
    assert "http://" not in source.replace("http://localhost", "")
    assert "https://" not in source
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/pytest tests/server/test_display_page.py -v`
Expected: FAIL on `test_the_page_exists`

- [ ] **Step 3: Write the page**

Create `src/openjarvis/server/static/display.html`:

```html
<!doctype html>
<meta charset="utf-8">
<title>OpenJarvis Display</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; padding: 2rem;
    font: 400 1.25rem/1.5 system-ui, -apple-system, "Segoe UI", sans-serif;
    background: #14100c; color: #f5efe6;
  }
  h1 { font-size: 1.5rem; margin: 0 0 1.5rem; opacity: .7; font-weight: 500; }
  ul { list-style: none; margin: 0; padding: 0; display: grid; gap: .75rem; }
  li {
    display: grid; grid-template-columns: 1fr auto; align-items: baseline;
    gap: 1rem; padding: 1rem 1.25rem; border-radius: .75rem;
    background: #241d16;
  }
  li[data-out] { opacity: .45; }
  .name { font-weight: 600; }
  .note, .options { display: block; font-size: .9rem; opacity: .65; }
  .price { font-variant-numeric: tabular-nums; white-space: nowrap; }
  .total {
    margin-top: 1.5rem; padding-top: 1rem; border-top: 1px solid #3a2f24;
    display: flex; justify-content: space-between; font-weight: 700;
  }
  .idle { display: grid; place-items: center; height: 70vh; opacity: .35; }
</style>

<main id="root"></main>

<script>
  const root = document.getElementById("root");
  const vnd = new Intl.NumberFormat("vi-VN");

  // textContent everywhere, never innerHTML: every string below is merchant
  // data, and the customer is looking at this screen.
  const el = (tag, text, cls) => {
    const node = document.createElement(tag);
    if (text !== undefined && text !== null) node.textContent = String(text);
    if (cls) node.className = cls;
    return node;
  };

  const money = (n) => `${vnd.format(Number(n) || 0)}đ`;

  function idle(message) {
    root.replaceChildren(el("div", message, "idle"));
  }

  function renderMenu(data) {
    const list = el("ul");
    for (const item of data.items || []) {
      const row = el("li");
      if (item.available === false) row.setAttribute("data-out", "");
      const left = el("div");
      left.append(el("span", item.name, "name"));
      if (item.note) left.append(el("span", item.note, "note"));
      if (item.available === false) left.append(el("span", "Hết hàng", "note"));
      row.append(left, el("span", money(item.price), "price"));
      list.append(row);
    }
    root.replaceChildren(el("h1", "Thực đơn"), list);
  }

  function renderCart(data) {
    const list = el("ul");
    for (const line of data.lines || []) {
      const row = el("li");
      const left = el("div");
      left.append(el("span", `${line.quantity || 1}× ${line.name || ""}`, "name"));
      const options = Object.entries(line.options || {})
        .map(([key, value]) => `${key}: ${value}`)
        .join(" · ");
      if (options) left.append(el("span", options, "options"));
      row.append(left, el("span", money(line.line_total), "price"));
      list.append(row);
    }
    const total = el("div", null, "total");
    total.append(el("span", "Tổng cộng"), el("span", money(data.total)));
    root.replaceChildren(el("h1", "Đơn của bạn"), list, total);
  }

  function render(data) {
    if (data.view === "menu") return renderMenu(data);
    if (data.view === "cart") return renderCart(data);
    return idle("");
  }

  function connect() {
    idle("Đang kết nối…");
    const socket = new WebSocket(
      `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}/v1/agents/events`
    );
    socket.onmessage = (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }
      if (payload.type === "display_update") render(payload.data || {});
    };
    socket.onopen = () => idle("");
    // The kiosk runs unattended for hours; a dropped socket must heal itself.
    socket.onclose = () => setTimeout(connect, 2000);
  }

  connect();
</script>
```

- [ ] **Step 4: Run the page tests**

Run: `.venv/bin/pytest tests/server/test_display_page.py -v`
Expected: all 4 PASS

- [ ] **Step 5: Embed it in the kiosk page**

Read `frontend/src/pages/KioskPage.tsx` and find the element that holds the
page's main content area. Add one iframe inside it:

```tsx
        <iframe
          src="/display"
          title="Display"
          className="absolute inset-0 h-full w-full border-0"
        />
```

Match the surrounding styling convention — if the file does not use Tailwind
classes, use whatever it does use. The iframe must sit *behind* the existing
voice status and consent controls in stacking order, so those stay clickable.

If `/display` is not served by the existing static mount, check how
`server/static` is mounted in `server/app.py` and add a route that returns
`display.html`; do not add a second static mount.

- [ ] **Step 6: Build the frontend**

Run: `cd frontend && npm run build`
Expected: build succeeds. If the project uses a different command, read
`frontend/package.json` and use its build script.

- [ ] **Step 7: Commit**

```bash
git add src/openjarvis/server/static/display.html frontend/src/pages/KioskPage.tsx tests/server/test_display_page.py
git commit -m "feat(display): a plain-HTML page in the kiosk

textContent only, no innerHTML: every string it renders is merchant data
on a screen a customer is standing in front of. Kept out of the React
build so the customer-facing layout can be edited without rebuilding.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Ordering guidance and the end-to-end reasoning test

**Files:**
- Create: `src/openjarvis/skills/data/ordering.toml`
- Create: `tests/integration/test_ordering_loop.py`
- Modify: `configs/openjarvis/examples/` — a new preset for the ordering kiosk

**Interfaces:**
- Consumes: every tool from tasks 2 through 6.
- Produces: nothing further tasks depend on. This is the phase gate.

- [ ] **Step 1: Write the guidance skill**

Read one existing file in `src/openjarvis/skills/data/` first — for example
`web-summarize.toml` — and match its key names exactly. Then create
`src/openjarvis/skills/data/ordering.toml` with the same top-level keys and
this description and guidance content:

```toml
name = "ordering"
version = "0.1.0"
description = "Take a food and drink order: explore the menu, recommend, confirm, place, verify."
tags = ["ordering", "commerce"]
user_invocable = false

markdown_content = """
# Taking an order

You are helping a customer order. Talk normally. Only reach for a tool when
the conversation actually needs one.

## What the tools will and will not tell you

`cart_add`, `cart_remove` and `order_place` return an acknowledgement and
nothing more. They do **not** tell you what the cart or the order now
contains. To know that, call `cart_view` or `order_verify`. Never describe a
cart or an order you have not read back in this turn.

## The shape of an order

1. Find out what they want. `menu_search` for recommendations, `menu_item`
   when you need an item's options before choosing them.
2. Show what you are recommending: `display_menu` with the two or three items
   you are actually suggesting, not everything the search returned. Choosing
   what to show is part of recommending.
3. Ask only for what is genuinely missing. If they said "a latte", do not
   interrogate them about size and sugar — offer the default and let them
   correct it.
4. `cart_add`, then `cart_view`, then `display_cart` with what you read.
   Check it against what they asked for before saying it is right.
5. Read the cart back to them and get a clear yes before `order_place`.
6. After `order_place`, call `order_verify` and compare the merchant's answer
   with what the customer asked for. If they disagree, say so plainly and fix
   it — do not report success.

## Sold out

If an item is unavailable, say so and offer the nearest alternative. Do not
quietly leave it out.
"""
```

- [ ] **Step 2: Write the end-to-end reasoning test**

Create `tests/integration/test_ordering_loop.py`:

```python
"""The order flow forces a reasoning turn between every action.

If ordering ever completes in one or two inference turns, a tool has been
built that both changes something and reports the result -- the giant
commerce_checkout() this design exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from openjarvis.merchants.fake import FakeMerchant
from openjarvis.tools.ordering import (
    CartAddTool,
    CartViewTool,
    MenuSearchTool,
    OrderPlaceTool,
    OrderVerifyTool,
)

pytestmark = pytest.mark.integration


def _wired(merchant, *classes):
    tools = []
    for cls in classes:
        tool = cls()
        tool._merchant = merchant
        tools.append(tool)
    return tools


def test_no_single_tool_can_take_an_order_from_start_to_finish():
    """Walk the whole flow and count the points where a decision is needed.

    Each step below is a place the Agent must look at a result before it can
    choose the next call. There is no shortcut through them, because no tool
    returns both an effect and its consequence.
    """
    merchant = FakeMerchant()
    search, add, view, place, verify = _wired(
        merchant,
        MenuSearchTool,
        CartAddTool,
        CartViewTool,
        OrderPlaceTool,
        OrderVerifyTool,
    )

    # 1. what exists
    items = json.loads(search.execute(query="latte").content)["items"]
    assert items[0]["id"] == "latte"

    # 2. change something -- and learn nothing about the result
    added = json.loads(
        add.execute(item_id="latte", options={"size": "L"}, quantity=2).content
    )
    assert set(added) == {"added", "line_id"}

    # 3. so the cart has to be read
    cart = json.loads(view.execute().content)
    assert cart["lines"][0]["quantity"] == 2
    assert cart["total"] == 102_000

    # 4. change something again -- again learning nothing
    placed = json.loads(place.execute().content)
    assert set(placed) == {"placed", "order_id"}

    # 5. so the order has to be read back from the merchant
    order = json.loads(verify.execute(order_id=placed["order_id"]).content)
    assert order["status"] == "placed"
    assert order["total"] == cart["total"]


def test_the_merchant_is_the_authority_when_the_agent_is_wrong():
    """A belief that disagrees with the merchant loses."""
    merchant = FakeMerchant()
    add, view = _wired(merchant, CartAddTool, CartViewTool)

    merchant.set_price("latte", 80_000)
    add.execute(item_id="latte", options={}, quantity=1)

    assert json.loads(view.execute().content)["total"] == 80_000


def test_ordering_and_display_tools_never_overlap():
    """A display tool must not be able to change an order, and an ordering
    tool must not be able to draw.

    Enumerated from the modules, not from ToolRegistry: conftest clears the
    registry autouse before every test, so a registry walk would iterate
    nothing and pass without checking anything.
    """
    import inspect

    from openjarvis.tools import display, ordering
    from openjarvis.tools._stubs import BaseTool

    checked = 0
    for module in (ordering, display):
        for _, member in inspect.getmembers(module, inspect.isclass):
            if (
                not issubclass(member, BaseTool)
                or member.__module__ != module.__name__
                or inspect.isabstract(member)
            ):
                continue
            spec = member().spec
            kinds = {
                key
                for key in ("mutates", "observes", "displays")
                if spec.metadata.get(key)
            }
            assert len(kinds) == 1, f"{spec.name} declares {kinds}"
            checked += 1

    assert checked == 10, f"expected 7 ordering + 3 display tools, saw {checked}"
```

- [ ] **Step 3: Run it**

Run: `.venv/bin/pytest tests/integration/test_ordering_loop.py -v -m integration`
Expected: all 3 PASS

- [ ] **Step 4: Add the preset**

Create `configs/openjarvis/examples/ordering-kiosk.toml`, modelled on
`browser-agent-playwright-mcp.toml`. Read that file first and match its
structure. The parts specific to this phase:

```toml
[agent]
default_agent = "orchestrator"
max_turns = 30

[merchants]
backend = "fake"

[tools]
enabled = "menu_search,menu_item,cart_add,cart_remove,cart_view,order_place,order_verify,display_menu,display_cart,display_clear"
```

`max_turns` is 30 because a full order is several observation/decision pairs
and the default of 10 would strand it mid-flow. `LoopGuard` remains the real
brake against a runaway loop.

- [ ] **Step 5: Verify the preset builds a system with the tools wired**

Run:
```bash
.venv/bin/python -c "
from openjarvis.core.config import load_config
from openjarvis.system import SystemBuilder
from unittest.mock import MagicMock
config = load_config('configs/openjarvis/examples/ordering-kiosk.toml')
engine = MagicMock(); engine.health.return_value = True
engine.list_models.return_value = [config.intelligence.default_model]
system = SystemBuilder(config).engine_instance(engine, key='ollama').build()
names = sorted(t.spec.name for t in system.tools)
print(names)
assert 'cart_add' in names and 'display_menu' in names, names
ordering = [t for t in system.tools if t.spec.category == 'ordering']
assert all(t._merchant is not None for t in ordering), 'merchant not injected'
carts = {id(t._merchant) for t in ordering}
assert len(carts) == 1, 'ordering tools must share one merchant'
system.close(); print('ok')
"
```
Expected: prints the tool list and `ok`.

- [ ] **Step 6: Run the whole affected surface**

Run:
```bash
.venv/bin/pytest tests/merchants/ tests/tools/ tests/system/ tests/server/ -v
```
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/openjarvis/skills/data/ordering.toml tests/integration/test_ordering_loop.py configs/openjarvis/examples/ordering-kiosk.toml
git commit -m "feat(ordering): guidance skill, preset, and the phase gate

The end-to-end test walks the flow and asserts that each mutation returns
only an acknowledgement, so ordering cannot collapse into one call. That
is the giant commerce_checkout() this design exists to prevent.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Phase 1 completion checklist

Do not declare the phase complete on a green test run alone.

- [ ] `tests/tools/test_ordering_doctrine.py` passes: every ordering tool
      declares `mutates` XOR `observes`.
- [ ] `tests/integration/test_ordering_loop.py` passes.
- [ ] `grep -n "innerHTML\|insertAdjacentHTML\|document.write"
      src/openjarvis/server/static/display.html` returns nothing.
- [ ] `git diff --stat a97c64c..HEAD -- src/openjarvis/agents/` shows no change
      to `orchestrator.py`, `runtime.py` or `_stubs.py` beyond the baseline.
- [ ] `git diff --stat a97c64c..HEAD -- src/openjarvis/workflow/` shows no
      change from this phase.
- [ ] `grep -rn "class .*System\b" src/openjarvis/merchants/ src/openjarvis/tools/ordering.py`
      returns nothing — no second composition root was introduced.
- [ ] No payment tool exists yet: `grep -rn "payment" src/openjarvis/tools/`
      returns nothing.
- [ ] **A real conversation orders a drink.** Start the server on the
      `ordering-kiosk.toml` preset, open the kiosk page, and ask for two large
      lattes by voice. The screen shows the menu, then the cart; the Agent
      reads the cart back before placing; the order verifies. This is a manual
      check — the tests cover the tool contract, not whether the model uses it
      well.
- [ ] The working tree is clean.

## What this phase deliberately does not do

- **No payment.** `payment_start`, `payment_verify`, the approval gate and
  `display_qr` are Phase 4, last, because payment is the irreversible step.
- **No capability store, no `merchant_learn`, no fast path.** Phase 3.
- **No real merchant.** The fake is the ground truth here; interactive
  execution against a real site through Playwright MCP is Phase 2.
- **No thread scoping of the cart.** One session at a time is a confirmed
  constraint, and the ceiling is marked in `merchants/fake.py`.
