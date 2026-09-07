"""What tools have actually observed, per conversation.

The store deliberately does not belong to a ``ToolExecutor``. There are at
least three instances in a running system -- the agent builds its own
(``tools/_stubs.py:607``), ``SystemBuilder`` makes one, and ``SkillExecutor``
dispatches through whichever it was handed (``skills/executor.py:131``). A
saved skill therefore fetches through a different executor than the one the
agent calls display tools on. Instance-owned buffers would let a skill obtain
a real payment QR that the guard cannot see, and the refusal would look like
the agent's fault.

Every executor writes here; every reader reads here; the conversation owns the
contents.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Deque, Dict, Optional, Tuple
from urllib.parse import urlsplit

from openjarvis.core.conversation import current_conversation_id


@dataclass(frozen=True, slots=True)
class ToolEvidence:
    """One thing a tool actually observed, kept so output can cite it.

    ``url`` and ``status_code`` are carried because provenance is not only
    "some tool said this" -- a payment QR has to have come from a successful
    HTTP call to a trusted host, not from a page a hostile site rendered.
    """

    tool_name: str
    content: str
    status_code: Optional[int]
    url: Optional[str]


@dataclass(frozen=True, slots=True)
class MutationClaim:
    """Permission to send one exact state-changing request, once.

    ``allowed`` is false when this conversation already sent this exact
    request -- whether it is still in flight, came back, or timed out in a way
    that does not prove it never arrived. ``prior_outcome`` says which.
    """

    fingerprint: str
    allowed: bool
    prior_outcome: str = ""


# Three bounds, because two are not enough. One response body in this system is
# 89 KB, so an entry cap alone does not bound memory; and a per-conversation cap
# alone does not either, since every chat request scopes itself to a fresh id
# and would leave its bucket behind. Module-level so tests can lower them.
_MAX_ENTRIES = 20
_MAX_BYTES = 2_000_000
_MAX_CONVERSATIONS = 8

_EVIDENCE: "OrderedDict[str, Deque[ToolEvidence]]" = OrderedDict()
_BYTES: Dict[str, int] = {}
_LOCK = threading.Lock()

# One conversation's exact state-changing requests, newest last. Only a digest
# and an outcome are kept: an order body carries the customer's own details and
# a header carries credentials, so neither may survive the request.
_MAX_MUTATIONS = 64
_MUTATIONS: "OrderedDict[str, OrderedDict[str, str]]" = OrderedDict()


def record(
    tool_name: str,
    content: str,
    status_code: Optional[int],
    url: Optional[str],
) -> None:
    """Remember one successful observation for the calling conversation."""
    if not content:
        return
    entry = ToolEvidence(
        tool_name=tool_name,
        content=content,
        status_code=status_code,
        url=url,
    )
    key = current_conversation_id()
    with _LOCK:
        bucket = _EVIDENCE.setdefault(key, deque())
        _EVIDENCE.move_to_end(key)
        while len(_EVIDENCE) > _MAX_CONVERSATIONS:
            stale, _ = _EVIDENCE.popitem(last=False)
            _BYTES.pop(stale, None)
        bucket.append(entry)
        _BYTES[key] = _BYTES.get(key, 0) + len(content.encode("utf-8"))
        while bucket and (len(bucket) > _MAX_ENTRIES or _BYTES[key] > _MAX_BYTES):
            dropped = bucket.popleft()
            _BYTES[key] -= len(dropped.content.encode("utf-8"))


def observed_in_tool_output(
    needle: str,
    *,
    from_tool: Optional[str] = None,
    require_ok: bool = False,
    trusted_origins: Tuple[Tuple[str, str, int], ...] = (),
) -> bool:
    """Whether this exact string came back from a qualifying tool call."""
    if not needle:
        return False
    for entry in _conversation_evidence():
        if from_tool is not None and entry.tool_name != from_tool:
            continue
        if require_ok and not (
            entry.status_code is not None and 200 <= entry.status_code < 300
        ):
            continue
        if trusted_origins:
            origin = _url_origin(entry.url)
            if origin not in trusted_origins:
                continue
        if needle in entry.content:
            return True
    return False


def last_result(tool_name: Optional[str] = None) -> str:
    """The most recent successful body in this conversation, or ""."""
    for entry in reversed(_conversation_evidence()):
        if tool_name is None or entry.tool_name == tool_name:
            return entry.content
    return ""


def model_context(*, max_chars: int = 24_000) -> str:
    """Return bounded recent evidence for the model in this conversation."""
    if not current_conversation_id() or max_chars <= 0:
        return ""

    from openjarvis.tools.result_projection import project_tool_content

    parts: list[str] = []
    remaining = max_chars
    for entry in reversed(_conversation_evidence()):
        heading = (
            f"tool={entry.tool_name} status={entry.status_code} url={entry.url or ''}\n"
        )
        if len(heading) >= remaining:
            break
        projected = project_tool_content(
            entry.content,
            max_chars=min(16_000, remaining - len(heading)),
        )
        block = heading + projected
        separator = "\n\n" if parts else ""
        if len(separator) + len(block) > remaining:
            break
        parts.append(separator + block)
        remaining -= len(separator) + len(block)
    return "".join(parts)


def latest_json_string(
    field: str,
    *,
    from_tool: Optional[str] = None,
    require_ok: bool = False,
    trusted_origins: Tuple[Tuple[str, str, int], ...] = (),
) -> str:
    """Return a string field from the newest qualifying JSON observation."""
    for entry in reversed(_conversation_evidence()):
        if from_tool is not None and entry.tool_name != from_tool:
            continue
        if require_ok and not (
            entry.status_code is not None and 200 <= entry.status_code < 300
        ):
            continue
        if trusted_origins and _url_origin(entry.url) not in trusted_origins:
            continue
        start = entry.content.find("{")
        if start < 0:
            continue
        try:
            payload = json.loads(entry.content[start:])
        except (TypeError, json.JSONDecodeError):
            continue
        value = _find_json_string(payload, field)
        if value:
            return value
    return ""


def latest_json_number(
    field: str,
    *,
    from_tool: Optional[str] = None,
    require_ok: bool = False,
    trusted_origins: Tuple[Tuple[str, str, int], ...] = (),
) -> Optional[int]:
    """Return a numeric field from the newest qualifying JSON observation."""
    for entry in reversed(_conversation_evidence()):
        if from_tool is not None and entry.tool_name != from_tool:
            continue
        if require_ok and not (
            entry.status_code is not None and 200 <= entry.status_code < 300
        ):
            continue
        if trusted_origins and _url_origin(entry.url) not in trusted_origins:
            continue
        start = entry.content.find("{")
        if start < 0:
            continue
        try:
            payload = json.loads(entry.content[start:])
        except (TypeError, json.JSONDecodeError):
            continue
        value = _find_json_number(payload, field)
        if value is not None:
            return value
    return None



def evidence_snapshot() -> Tuple[Dict[str, str], str]:
    """Plain-data view of this conversation's evidence: ``(by_tool, most_recent)``.

    ``by_tool[name]`` is that tool's most recent content; ``most_recent`` is
    the most recent content from any tool -- the same two views
    ``last_result(name)`` and ``last_result(None)`` give, just handed back as
    ``str``/``dict[str, str]`` instead of a live function bound to this
    module. Callers that must expose "read the evidence" to code they do not
    trust (``repl``) build their reader from this snapshot rather than from
    :func:`last_result` directly, so that reader has no attribute path back
    to :func:`record`. Copies under the lock via ``_conversation_evidence``,
    same as every other reader here.
    """
    by_tool: Dict[str, str] = {}
    most_recent = ""
    for entry in reversed(_conversation_evidence()):
        if not most_recent:
            most_recent = entry.content
        by_tool.setdefault(entry.tool_name, entry.content)
    return by_tool, most_recent


def reset() -> None:
    """Drop everything. For tests; the store is process-wide by design."""
    with _LOCK:
        _EVIDENCE.clear()
        _BYTES.clear()
        _MUTATIONS.clear()


def claim_mutation(method: str, url: str, body: object) -> MutationClaim:
    """Claim the right to send this exact mutation in this conversation.

    Atomic: the record is created under the same lock that checks for it, so
    two threads racing the same order cannot both be admitted. A refused claim
    means the request must not be dispatched at all -- an ambiguous outcome is
    never retried, because a timeout after a state change does not prove the
    state did not change.

    Outside a conversation there is nothing to deduplicate across: no turns, no
    replay. Claiming into the empty-id bucket would instead make one process-wide
    ledger that refuses an unrelated caller's identical request forever, so a
    scopeless claim is always granted. Mirrors :func:`model_context`.
    """
    fingerprint = _mutation_fingerprint(method, url, body)
    key = current_conversation_id()
    if not key:
        return MutationClaim(fingerprint=fingerprint, allowed=True)
    with _LOCK:
        bucket = _MUTATIONS.setdefault(key, OrderedDict())
        _MUTATIONS.move_to_end(key)
        while len(_MUTATIONS) > _MAX_CONVERSATIONS:
            _MUTATIONS.popitem(last=False)
        prior = bucket.get(fingerprint)
        if prior is not None:
            return MutationClaim(
                fingerprint=fingerprint, allowed=False, prior_outcome=prior
            )
        bucket[fingerprint] = "in_flight"
        while len(bucket) > _MAX_MUTATIONS:
            bucket.popitem(last=False)
    return MutationClaim(fingerprint=fingerprint, allowed=True)


def finish_mutation(claim: MutationClaim, outcome: str) -> None:
    """Record how a claimed mutation ended.

    ``not_sent`` releases the claim -- the request provably never reached the
    server, so retrying it is safe. ``response_seen`` and ``ambiguous`` keep it,
    which is what stops the same order being placed twice.
    """
    if not claim.allowed:
        return
    key = current_conversation_id()
    with _LOCK:
        bucket = _MUTATIONS.get(key)
        if bucket is None:
            return
        if outcome == "not_sent":
            bucket.pop(claim.fingerprint, None)
        elif claim.fingerprint in bucket:
            bucket[claim.fingerprint] = outcome


def _mutation_fingerprint(method: str, url: str, body: object) -> str:
    """A digest that ignores formatting but not meaning."""
    canonical = "\n".join(
        (
            method.strip().upper(),
            _canonical_url(url),
            _canonical_body(body),
        )
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _canonical_url(url: str) -> str:
    try:
        parsed = urlsplit(url.strip())
    except ValueError:
        return url.strip()
    if not parsed.scheme or parsed.hostname is None:
        return url.strip()
    scheme = parsed.scheme.lower()
    default_port = 443 if scheme == "https" else 80
    try:
        port = parsed.port or default_port
    except ValueError:
        return url.strip()
    authority = parsed.hostname.lower()
    if port != default_port:
        authority = f"{authority}:{port}"
    # Query order is left alone: two orderings are two different requests as
    # far as any server is concerned, and guessing otherwise could merge them.
    return f"{scheme}://{authority}{parsed.path}?{parsed.query}"


def _canonical_body(body: object) -> str:
    payload = body
    if isinstance(payload, (bytes, bytearray)):
        payload = payload.decode("utf-8", "replace")
    if isinstance(payload, str):
        stripped = payload.strip()
        try:
            payload = json.loads(stripped)
        except (TypeError, ValueError):
            return stripped
    try:
        return json.dumps(
            payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError):
        return repr(payload)


def _conversation_evidence() -> list:
    with _LOCK:
        return list(_EVIDENCE.get(current_conversation_id(), ()))


def _find_json_string(value: object, field: str) -> str:
    if isinstance(value, dict):
        candidate = value.get(field)
        if isinstance(candidate, str) and candidate.strip():
            return candidate
        for nested in value.values():
            found = _find_json_string(nested, field)
            if found:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_json_string(nested, field)
            if found:
                return found
    return ""


def _find_json_number(value: object, field: str) -> Optional[int]:
    if isinstance(value, dict):
        candidate = value.get(field)
        if isinstance(candidate, (int, float)) and candidate > 0:
            return int(candidate)
        for nested in value.values():
            found = _find_json_number(nested, field)
            if found is not None:
                return found
    elif isinstance(value, list):
        for nested in value:
            found = _find_json_number(nested, field)
            if found is not None:
                return found
    return None


def _url_origin(url: Optional[str]) -> Optional[Tuple[str, str, int]]:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        scheme = parsed.scheme.lower()
        host = parsed.hostname
        if scheme not in {"http", "https"} or host is None:
            return None
        port = parsed.port or (443 if scheme == "https" else 80)
    except ValueError:
        return None
    return scheme, host, port


__all__ = [
    "MutationClaim",
    "ToolEvidence",
    "claim_mutation",
    "evidence_snapshot",
    "finish_mutation",
    "last_result",
    "latest_json_number",
    "latest_json_string",
    "model_context",
    "observed_in_tool_output",
    "record",
    "reset",
]
