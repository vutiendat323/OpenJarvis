"""Two confirmed drafts may buy the same items without replaying one write."""

import json

import httpx
import pytest

from openjarvis.core.conversation import agent_turn_scope, conversation_scope
from openjarvis.core.events import EventBus
from openjarvis.skills.executor import SkillExecutor
from openjarvis.skills.tool_adapter import SkillTool
from openjarvis.skills.types import SkillManifest, SkillStep
from openjarvis.system.builder import SystemBuilder
from openjarvis.tools import evidence
from openjarvis.tools._stubs import ToolExecutor
from openjarvis.tools.display import DisplayCartTool
from openjarvis.tools.http_request import HttpRequestTool
from openjarvis.workflow.engine import WorkflowEngine


def _checkout(monkeypatch, *, timeout=False, workflow=False):
    cart = DisplayCartTool()
    cart._bus = EventBus()
    http = HttpRequestTool()
    writes = []

    def send(method, url, **kwargs):
        writes.append((method, url, kwargs.get("content")))
        if timeout:
            raise httpx.ReadTimeout("unknown write outcome")
        return httpx.Response(
            201, json={"order_id": len(writes)}, request=httpx.Request(method, url)
        )

    monkeypatch.setattr(http, "_request_following_redirects", send)
    monkeypatch.setattr("openjarvis.tools.http_request.check_ssrf", lambda _url: None)
    manifest = SkillManifest(
        name="checkout",
        checkout=True,
        steps=[
            SkillStep(
                tool_name="http_request",
                arguments_template=json.dumps(
                    {
                        "url": "https://shop.example/orders",
                        "method": "POST",
                        "body": '{"item":"coffee","quantity":1}',
                    }
                ),
            )
        ],
    )
    if workflow:
        manifest.steps[:0] = [
            SkillStep(
                tool_name="http_request",
                arguments_template=json.dumps(
                    {"url": f"https://shop.example/{resource}", "method": "GET"}
                ),
                output_key=resource,
            )
            for resource in ("menu", "tables")
        ]
    tools = [cart, http]
    executor = SkillExecutor(
        ToolExecutor(tools), workflow_engine=WorkflowEngine() if workflow else None
    )
    SystemBuilder._inject_checkout_guard(tools + [SkillTool(manifest, executor)])
    return cart, http, executor, manifest, writes


def _add(cart):
    result = cart.execute(
        action="add",
        item={
            "variant_id": "coffee",
            "name": "Coffee",
            "unit_price": 35000,
            "quantity": 1,
        },
    )
    assert result.success
    return result.metadata["cart_revision"]


@pytest.mark.parametrize("workflow", [False, True])
def test_identical_new_drafts_are_distinct_confirmed_writes(monkeypatch, workflow):
    cart, _, executor, manifest, requests = _checkout(monkeypatch, workflow=workflow)
    with conversation_scope("new-identical-order"):
        for _ in range(2):
            revision = _add(cart)
            with agent_turn_scope() as nonce:
                result = executor.run(
                    manifest,
                    initial_context={"turn_nonce": nonce, "cart_revision": revision},
                )
            assert result.success
    writes = [request for request in requests if request[0] == "POST"]
    assert len(requests) == (6 if workflow else 2)
    assert len(writes) == 2
    assert writes[0] == writes[1]


@pytest.mark.parametrize("timeout", [False, True])
def test_one_checkout_claim_cannot_send_the_same_post_twice(monkeypatch, timeout):
    cart, http, _, manifest, writes = _checkout(monkeypatch, timeout=timeout)
    with conversation_scope(f"same-checkout-{timeout}"), agent_turn_scope() as nonce:
        revision = _add(cart)
        cart.begin_checkout(nonce, revision, manifest.manifest_bytes())
        params = {
            "url": "https://shop.example/orders",
            "method": "POST",
            "body": '{"item":"coffee","quantity":1}',
        }
        first = http.execute(**params)
        second = http.execute(**params)
        cart.end_checkout()
        assert first.success is not timeout
        assert not second.success
        assert second.metadata["duplicate_of_outcome"] == (
            "ambiguous" if timeout else "response_seen"
        )
        with agent_turn_scope() as next_nonce:
            with pytest.raises(ValueError, match="already consumed"):
                cart.begin_checkout(next_nonce, revision, manifest.manifest_bytes())
    assert len(writes) == 1


@pytest.fixture(autouse=True)
def _reset_evidence():
    evidence.reset()
    yield
    evidence.reset()
