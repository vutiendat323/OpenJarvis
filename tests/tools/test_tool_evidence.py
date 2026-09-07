"""Successful tool output is recorded per conversation, whoever dispatched it."""

from __future__ import annotations

import json
from typing import Any

import pytest

from openjarvis.core.conversation import conversation_scope
from openjarvis.core.types import ToolCall, ToolResult
from openjarvis.tools import evidence
from openjarvis.tools._stubs import BaseTool, ToolExecutor, ToolSpec


@pytest.fixture(autouse=True)
def _clean_store():
    """The store is module-level, so one test must not seed the next."""
    evidence.reset()
    yield
    evidence.reset()


class _Echo(BaseTool):
    """Returns whatever content and metadata the call asks for."""

    tool_id = "echo"

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="http_request",
            description="test double",
            parameters={"type": "object", "properties": {}},
        )

    def execute(self, **params: Any) -> ToolResult:
        return ToolResult(
            tool_name="http_request",
            content=params.get("body", ""),
            success=params.get("ok", True),
            metadata={
                "status_code": params.get("status", 200),
                "final_url": params.get("final_url"),
            },
        )


def _call(**arguments: Any) -> ToolCall:
    return ToolCall(id="1", name="http_request", arguments=json.dumps(arguments))


def test_a_successful_result_becomes_evidence():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="QR_REAL", url="https://trendcoffee.net/x"))

        assert evidence.observed_in_tool_output("QR_REAL") is True
        assert evidence.observed_in_tool_output("QR_FAKE") is False


def test_every_executor_writes_the_same_conversation_store():
    """The defect this module exists to prevent.

    A saved skill dispatches its steps through the builder's executor while the
    agent calls display tools through its own. Two instance-owned buffers would
    mean a skill could fetch a real payment QR that the guard cannot see. A
    module-level store keyed per-executor (e.g. by ``id(executor)``) would also
    defeat the guard, so the test must write through one executor and read
    through a genuinely different one.
    """
    agent_executor = ToolExecutor([_Echo()])
    skill_executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        skill_executor.execute(
            _call(body="QR_FROM_SKILL", url="https://trendcoffee.net/x")
        )
        agent_executor.execute(_call(body="unrelated"))

        assert evidence.observed_in_tool_output("QR_FROM_SKILL") is True
        assert evidence.last_result("http_request") == "unrelated"


def test_evidence_does_not_cross_conversations():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="QR_A", url="https://trendcoffee.net/x"))
    with conversation_scope("b"):
        assert evidence.observed_in_tool_output("QR_A") is False


def test_a_failed_call_leaves_no_evidence():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="QR_REAL", ok=False))

        assert evidence.observed_in_tool_output("QR_REAL") is False


def test_evidence_can_require_a_2xx_status_and_a_trusted_origin():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(
            _call(body="QR_500", status=500, url="https://trendcoffee.net/x")
        )
        executor.execute(
            _call(body="QR_ELSEWHERE", status=200, url="https://evil.example/x")
        )
        executor.execute(
            _call(body="QR_GOOD", status=201, url="https://trendcoffee.net/x")
        )

        origins = (("https", "trendcoffee.net", 443),)
        assert not evidence.observed_in_tool_output(
            "QR_500", require_ok=True, trusted_origins=origins
        )
        assert not evidence.observed_in_tool_output(
            "QR_ELSEWHERE", require_ok=True, trusted_origins=origins
        )
        assert evidence.observed_in_tool_output(
            "QR_GOOD", require_ok=True, trusted_origins=origins
        )


def test_evidence_is_bounded_by_entry_count(monkeypatch):
    monkeypatch.setattr(evidence, "_MAX_ENTRIES", 2)
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        for marker in ("one", "two", "three"):
            executor.execute(_call(body=marker))

        assert evidence.observed_in_tool_output("one") is False
        assert evidence.observed_in_tool_output("three") is True


def test_old_conversations_are_evicted_whole(monkeypatch):
    """Otherwise every chat request leaves a bucket behind forever."""
    monkeypatch.setattr(evidence, "_MAX_CONVERSATIONS", 2)
    executor = ToolExecutor([_Echo()])

    for name in ("first", "second", "third"):
        with conversation_scope(name):
            executor.execute(_call(body=f"body-{name}"))

    with conversation_scope("first"):
        assert evidence.observed_in_tool_output("body-first") is False
    with conversation_scope("third"):
        assert evidence.observed_in_tool_output("body-third") is True


def test_last_result_returns_the_most_recent_matching_body():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="older"))
        executor.execute(_call(body="newer"))

        assert evidence.last_result("http_request") == "newer"
        assert evidence.last_result("nothing_called_this") == ""


def test_model_context_is_bounded_without_changing_raw_evidence():
    executor = ToolExecutor([_Echo()])
    large_body = json.dumps(
        {
            "products": [
                {
                    "id": f"item-{index:03d}",
                    "description": "x" * 500,
                }
                for index in range(100)
            ]
        }
    )

    with conversation_scope("a"):
        executor.execute(_call(body=large_body, url="https://example.test/products"))
        executor.execute(
            _call(
                body='{"orderId":"ord-compact-7"}',
                status=201,
                url="https://example.test/orders",
            )
        )

        context = evidence.model_context(max_chars=2_000)

        assert len(context) <= 2_000
        assert "ord-compact-7" in context
        assert evidence.last_result("http_request") == '{"orderId":"ord-compact-7"}'


def test_model_context_is_isolated_and_requires_a_framework_scope():
    with conversation_scope("a"):
        evidence.record("http_request", '{"orderId":"ord-a"}', 201, "https://a.test")
        assert "ord-a" in evidence.model_context()

    with conversation_scope("b"):
        assert evidence.model_context() == ""

    assert evidence.model_context() == ""


def test_evidence_is_bounded_by_total_bytes(monkeypatch):
    monkeypatch.setattr(evidence, "_MAX_BYTES", 10)
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="0123456789"))  # exactly fills the bound
        executor.execute(_call(body="recent"))  # pushes the old entry out

        assert evidence.observed_in_tool_output("0123456789") is False
        assert evidence.observed_in_tool_output("recent") is True


def test_evidence_byte_cap_counts_utf8_encoded_content(monkeypatch):
    monkeypatch.setattr(evidence, "_MAX_BYTES", 4)
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="éé"))  # four UTF-8 bytes
        executor.execute(_call(body="x"))

        assert evidence.observed_in_tool_output("éé") is False
        assert evidence.observed_in_tool_output("x") is True


def test_evidence_does_not_retain_tool_arguments():
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(_call(body="retained body", authorization="sensitive-token"))
        entry = evidence._conversation_evidence()[0]

    assert not hasattr(entry, "arguments")
    assert "sensitive-token" not in repr(entry)


def test_from_tool_filter_excludes_other_tools():
    """The mechanism that stops a hostile page's browser_* output from
    satisfying a guard scoped to http_request."""
    with conversation_scope("a"):
        evidence.record(
            "browser_read", "hostile page content", 200, "https://evil.example"
        )

        assert evidence.observed_in_tool_output("hostile page content") is True
        assert (
            evidence.observed_in_tool_output(
                "hostile page content", from_tool="http_request"
            )
            is False
        )


def test_a_redirect_to_an_untrusted_host_is_not_trusted():
    """The requested URL must not stand in for the URL actually fetched.

    ``http_request`` follows redirects manually and only re-checks SSRF on
    each hop, not host trust -- a 30x from a trusted host can land on an
    attacker-controlled host. The evidence store must record the URL the
    response actually came from (``final_url``), not the one the model asked
    for, or a forged body from the redirect target would pass a
    trusted-host check meant for the original host.
    """
    executor = ToolExecutor([_Echo()])

    with conversation_scope("a"):
        executor.execute(
            _call(
                body="QR_FORGED",
                status=200,
                url="https://trendcoffee.net/open-redirect",
                final_url="https://evil.example/x",
            )
        )

        origins = (("https", "trendcoffee.net", 443),)
        assert not evidence.observed_in_tool_output(
            "QR_FORGED", require_ok=True, trusted_origins=origins
        )


# ---------------------------------------------------------------------------
# Exact mutation deduplication
# ---------------------------------------------------------------------------


def test_an_identical_mutation_is_claimed_once():
    """The second identical POST must never reach the network."""
    with conversation_scope("dedupe-1"):
        first = evidence.claim_mutation(
            "POST", "https://shop.example/orders", {"drink": "latte", "qty": 1}
        )
        evidence.finish_mutation(first, "response_seen")
        second = evidence.claim_mutation(
            "POST", "https://shop.example/orders", {"drink": "latte", "qty": 1}
        )

    assert first.allowed is True
    assert second.allowed is False
    assert second.prior_outcome == "response_seen"


def test_equivalent_json_bodies_are_one_fingerprint():
    """Key order and whitespace are not a customer's second order."""
    with conversation_scope("dedupe-2"):
        first = evidence.claim_mutation(
            "POST", "https://shop.example/orders", {"a": 1, "b": [1, 2]}
        )
        evidence.finish_mutation(first, "response_seen")
        second = evidence.claim_mutation(
            "post", "https://shop.example/orders", {"b": [1, 2], "a": 1}
        )

    assert second.allowed is False


def test_a_different_quantity_is_a_different_mutation():
    with conversation_scope("dedupe-3"):
        first = evidence.claim_mutation(
            "POST", "https://shop.example/orders", {"drink": "latte", "qty": 1}
        )
        evidence.finish_mutation(first, "response_seen")
        second = evidence.claim_mutation(
            "POST", "https://shop.example/orders", {"drink": "latte", "qty": 2}
        )

    assert second.allowed is True


def test_two_conversations_do_not_collide():
    """One customer's order must not silence another's identical one."""
    with conversation_scope("dedupe-a"):
        first = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})
        evidence.finish_mutation(first, "response_seen")
    with conversation_scope("dedupe-b"):
        other = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})

    assert first.allowed is True
    assert other.allowed is True


def test_a_request_proven_not_sent_can_be_retried():
    with conversation_scope("dedupe-4"):
        first = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})
        evidence.finish_mutation(first, "not_sent")
        retry = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})

    assert retry.allowed is True


def test_an_ambiguous_request_is_never_retried():
    """A timeout after a mutation does not prove the mutation did not happen."""
    with conversation_scope("dedupe-5"):
        first = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})
        evidence.finish_mutation(first, "ambiguous")
        retry = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})

    assert retry.allowed is False
    assert retry.prior_outcome == "ambiguous"


def test_an_unfinished_claim_blocks_a_second_one():
    with conversation_scope("dedupe-6"):
        first = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})
        second = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})

    assert first.allowed is True
    assert second.allowed is False
    assert second.prior_outcome == "in_flight"


def test_the_claim_interface_does_not_accept_headers():
    """Retaining expanded headers process-wide is how secrets leaked before."""
    import inspect

    parameters = inspect.signature(evidence.claim_mutation).parameters

    assert "headers" not in parameters
    assert set(parameters) == {"method", "url", "body"}


def test_a_claim_retains_no_raw_body():
    """Only a digest may survive; the order body itself must not be stored."""
    with conversation_scope("dedupe-7"):
        claim = evidence.claim_mutation(
            "POST", "https://shop.example/o", {"secret_note": "cardholder-name"}
        )
        evidence.finish_mutation(claim, "response_seen")
        stored = repr(evidence._MUTATIONS)

    assert "cardholder-name" not in stored
    assert len(claim.fingerprint) == 64


def test_concurrent_identical_claims_admit_exactly_one():
    """Two threads racing the same order must produce one network dispatch."""
    import contextvars
    import threading

    barrier = threading.Barrier(2)
    results: list = []
    results_lock = threading.Lock()

    def attempt() -> None:
        barrier.wait(timeout=5)
        claim = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})
        with results_lock:
            results.append(claim)

    with conversation_scope("dedupe-race"):
        # One Context cannot be entered by two threads at once, so each thread
        # gets its own copy of this conversation's scope.
        threads = [
            threading.Thread(target=contextvars.copy_context().run, args=(attempt,))
            for _ in range(2)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=5)

    assert len(results) == 2
    assert sum(1 for claim in results if claim.allowed) == 1


def test_fingerprints_are_bounded_per_conversation():
    """A long conversation must not accumulate claims forever."""
    with conversation_scope("dedupe-bound"):
        for index in range(evidence._MAX_MUTATIONS + 5):
            claim = evidence.claim_mutation(
                "POST", "https://shop.example/o", {"n": index}
            )
            evidence.finish_mutation(claim, "response_seen")
        held = len(evidence._MUTATIONS["dedupe-bound"])

    assert held == evidence._MAX_MUTATIONS


def test_a_scopeless_claim_is_always_granted():
    """No conversation means no cross-turn replay to prevent."""
    first = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})
    evidence.finish_mutation(first, "response_seen")
    second = evidence.claim_mutation("POST", "https://shop.example/o", {"x": 1})

    assert first.allowed is True
    assert second.allowed is True
    assert "" not in evidence._MUTATIONS
