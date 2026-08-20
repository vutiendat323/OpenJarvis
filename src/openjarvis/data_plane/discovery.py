"""HTTP-first discovery cascade for structured source capabilities."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from urllib.parse import SplitResult, parse_qsl, urlencode, urlsplit, urlunsplit

from openjarvis.core.events import EventBus, EventType
from openjarvis.data_plane.adapters.generic import (
    HtmlExtraction,
    compile_capability,
    extract_graphql,
    extract_html,
    extract_openapi,
    extract_routes,
    is_graphql_declaration,
    is_openapi_declaration,
)
from openjarvis.data_plane.capability_store import SQLiteCapabilityStore
from openjarvis.data_plane.discovery_http import DiscoveryHttpClient
from openjarvis.data_plane.errors import DataPlaneError, DataPlaneErrorCode
from openjarvis.data_plane.types import (
    DiscoveryConstraints,
    DiscoveryEvidence,
    DiscoveryResult,
    SourceCapability,
    SourceRef,
    TrustState,
)

_GRAPHQL_INTROSPECTION_QUERY = """query IntrospectionQuery {
  __schema {
    queryType { name }
    types {
      kind
      name
      fields(includeDeprecated: false) {
        name
        args { name type { kind name ofType { kind name } } }
        type { kind name ofType { kind name } }
      }
    }
  }
}"""
_UNSAFE_PAYLOAD_KEY = re.compile(
    r"authorization|cookie|credential|password|permission|secret|token",
    re.IGNORECASE,
)
_UNTRUSTED_CLAIM = re.compile(
    r"system\s+instruction|ignore\s+previous|request\s+(?:api\s+)?credentials|"
    r"provide\s+credentials|promote\s+(?:this\s+)?capability|grant\s+permission",
    re.IGNORECASE,
)


@runtime_checkable
class BrowserObservationPort(Protocol):
    """Injected final-stage browser observation boundary.

    Task 4 deliberately defines the port only. It neither imports nor starts a
    browser runtime.
    """

    def observe(
        self,
        origin: str,
        timeout_seconds: float,
    ) -> tuple[DiscoveryEvidence, ...]: ...


class DiscoveryEngine:
    """Discover and persist compact, attributable structured capabilities."""

    def __init__(
        self,
        capability_store: SQLiteCapabilityStore,
        *,
        http_client: DiscoveryHttpClient | object | None = None,
        browser_observer: BrowserObservationPort | None = None,
        event_bus: EventBus | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._store = capability_store
        self.http = http_client or DiscoveryHttpClient()
        self._browser_observer = browser_observer
        self._event_bus = event_bus
        self._clock = clock

    def discover(
        self,
        source_ref: SourceRef,
        constraints: DiscoveryConstraints,
    ) -> DiscoveryResult:
        return self._discover(source_ref, constraints, prior=None, force_refresh=False)

    def refresh(self, source_id: str) -> DiscoveryResult:
        prior = self._store.get(source_id)
        if prior is None:
            raise DataPlaneError(
                DataPlaneErrorCode.CAPABILITY_MISSING,
                f"No capability exists for source {source_id!r}",
            )
        return self._discover(
            SourceRef(prior.origin),
            DiscoveryConstraints(),
            prior=prior,
            force_refresh=True,
            source_id_override=source_id,
        )

    def get_capability(self, source_id: str) -> SourceCapability | None:
        return self._store.get(source_id)

    def _discover(
        self,
        source_ref: SourceRef,
        constraints: DiscoveryConstraints,
        *,
        prior: SourceCapability | None,
        force_refresh: bool,
        source_id_override: str = "",
    ) -> DiscoveryResult:
        started_at = self._clock()
        deadline = started_at + max(0.0, constraints.budget_seconds)
        requested_origin = _origin(source_ref.value)
        source_id = source_id_override or _source_id(source_ref.value)

        self._publish(
            EventType.SOURCE_DISCOVERY_STARTED,
            {"source_id": source_id, "refresh": force_refresh},
        )

        if not force_refresh:
            cached = self._store.get(source_id)
            if _is_cache_valid(cached, requested_origin):
                result = DiscoveryResult(
                    source_id=source_id,
                    state=TrustState.READ_VALIDATED,
                    capability=cached,
                    evidence=cached.evidence,
                    elapsed_ms=int((self._clock() - started_at) * 1000),
                    cache_hit=True,
                )
                self._completed(result)
                return result

        set_deadline = getattr(self.http, "set_deadline", None)
        if callable(set_deadline):
            set_deadline(deadline)
        set_require_https = getattr(self.http, "set_require_https", None)
        if callable(set_require_https):
            set_require_https(constraints.require_https)

        evidence: list[DiscoveryEvidence] = [
            DiscoveryEvidence(
                kind="source",
                source_url=requested_origin,
                payload={"origin": requested_origin},
                provenance="source_ref",
            )
        ]

        if self._clock() >= deadline:
            return self._persist_partial(
                source_id,
                requested_origin,
                evidence,
                started_at,
                DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                prior,
            )

        try:
            root_response = self.http.fetch(source_ref.value, method="GET")
        except DataPlaneError as exc:
            return self._persist_partial(
                source_id,
                requested_origin,
                evidence,
                started_at,
                exc.code,
                prior,
            )
        root_url = str(root_response.url)
        observed_origin = _origin(root_url)
        if observed_origin != requested_origin:
            evidence.append(
                DiscoveryEvidence(
                    kind="origin_change",
                    source_url=requested_origin,
                    status_code=root_response.status_code,
                    payload={"observed_origin": observed_origin},
                    provenance="http_redirect",
                )
            )
            return self._persist_partial(
                source_id,
                requested_origin,
                evidence,
                started_at,
                DataPlaneErrorCode.SCHEMA_MISMATCH,
                prior,
            )

        content_type = _content_type(root_response)
        html = HtmlExtraction((), (), ())
        if root_response.is_success and content_type in {
            "text/html",
            "application/xhtml+xml",
            "",
        }:
            html = extract_html(
                root_url,
                root_response.text,
                status_code=root_response.status_code,
                content_type=content_type,
            )
            evidence.extend(html.evidence)
        elif root_response.is_success:
            openapi = extract_openapi(
                root_url,
                root_response.text,
                status_code=root_response.status_code,
                content_type=content_type,
            )
            if openapi is not None:
                evidence.append(openapi)
        self._stage_completed(source_id, "publisher_document", evidence)

        if self._clock() >= deadline:
            return self._persist_partial(
                source_id,
                requested_origin,
                evidence,
                started_at,
                DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                prior,
            )

        disallowed_paths: tuple[str, ...] = ()
        robots_url = f"{requested_origin}/robots.txt"
        try:
            robots = self.http.fetch(robots_url, method="GET")
        except DataPlaneError:
            robots = None
        if robots is not None and robots.is_success:
            disallowed_paths = _parse_robots(robots.text)
            evidence.append(
                DiscoveryEvidence(
                    kind="robots_policy",
                    source_url=robots_url,
                    status_code=robots.status_code,
                    content_type=_content_type(robots),
                    payload={"disallow": list(disallowed_paths)},
                    provenance="publisher_policy",
                )
            )
        self._stage_completed(source_id, "publisher_policy", evidence)

        for endpoint in html.declared_endpoints:
            if self._clock() >= deadline:
                return self._persist_partial(
                    source_id,
                    requested_origin,
                    evidence,
                    started_at,
                    DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                    prior,
                )
            if _origin(endpoint.url) == requested_origin and _is_denied(
                endpoint.url, disallowed_paths
            ):
                continue
            target_url = endpoint.url
            if is_graphql_declaration(endpoint):
                target_url = _append_query(
                    endpoint.url,
                    "query",
                    _GRAPHQL_INTROSPECTION_QUERY,
                )
            try:
                response = self.http.fetch(target_url, method="GET")
            except DataPlaneError:
                continue
            if not response.is_success:
                continue
            endpoint_type = _content_type(response) or endpoint.content_type
            if is_graphql_declaration(endpoint):
                structured = extract_graphql(
                    endpoint.url,
                    response.text,
                    status_code=response.status_code,
                    content_type=endpoint_type,
                )
            elif is_openapi_declaration(endpoint):
                structured = extract_openapi(
                    endpoint.url,
                    response.text,
                    status_code=response.status_code,
                    content_type=endpoint_type,
                )
            else:
                structured = extract_openapi(
                    endpoint.url,
                    response.text,
                    status_code=response.status_code,
                    content_type=endpoint_type,
                )
            if structured is not None:
                evidence.append(structured)
        self._stage_completed(source_id, "declared_structured", evidence)

        capability = compile_capability(
            source_id=source_id,
            origin=requested_origin,
            evidence=tuple(evidence),
            revision=(prior.revision + 1) if prior else 1,
        )
        if capability.operations:
            return self._persist_validated(
                capability,
                evidence,
                started_at,
                prior,
            )

        policy_values = {
            str(item.payload.get("policy", ""))
            for item in evidence
            if item.kind == "publisher_policy"
        }
        if "no-static-assets" not in policy_values:
            for asset_url in html.static_assets:
                if self._clock() >= deadline:
                    return self._persist_partial(
                        source_id,
                        requested_origin,
                        evidence,
                        started_at,
                        DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                        prior,
                    )
                if _origin(asset_url) != requested_origin or _is_denied(
                    asset_url, disallowed_paths
                ):
                    continue
                try:
                    asset = self.http.fetch(asset_url, method="GET")
                except DataPlaneError:
                    continue
                if not asset.is_success:
                    continue
                route_evidence = extract_routes(
                    asset_url,
                    asset.text,
                    status_code=asset.status_code,
                    content_type=_content_type(asset),
                )
                if route_evidence is not None:
                    evidence.append(route_evidence)
        self._stage_completed(source_id, "static_assets", evidence)

        browser_actions = 0
        if (
            constraints.browser_fallback
            and self._browser_observer is not None
            and "no-browser" not in policy_values
        ):
            if self._clock() >= deadline:
                return self._persist_partial(
                    source_id,
                    requested_origin,
                    evidence,
                    started_at,
                    DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                    prior,
                )
            remaining = max(0.0, deadline - self._clock())
            observed = self._browser_observer.observe(requested_origin, remaining)
            evidence.extend(_compact_browser_evidence(observed, requested_origin))
            browser_actions = 1
            self._stage_completed(source_id, "browser_observer", evidence)

        return self._persist_partial(
            source_id,
            requested_origin,
            evidence,
            started_at,
            DataPlaneErrorCode.CAPABILITY_QUARANTINED
            if any(
                item.kind in {"route_candidate", "service_desc"} for item in evidence
            )
            else None,
            prior,
            browser_actions=browser_actions,
        )

    def _persist_validated(
        self,
        capability: SourceCapability,
        evidence: list[DiscoveryEvidence],
        started_at: float,
        prior: SourceCapability | None,
    ) -> DiscoveryResult:
        if prior is not None and (
            capability.origin != prior.origin
            or capability.schema_hash != prior.schema_hash
        ):
            return self._mismatch_result(prior, evidence, started_at)

        if prior is None or prior.provider == "generic":
            self._store.save(capability)
            persisted = capability
        else:
            persisted = prior
        self._publish(
            EventType.SOURCE_CAPABILITY_VALIDATED,
            {
                "source_id": capability.source_id,
                "operation_count": len(persisted.operations),
                "revision": persisted.revision,
            },
        )
        result = DiscoveryResult(
            source_id=capability.source_id,
            state=TrustState.READ_VALIDATED,
            capability=persisted,
            evidence=tuple(evidence),
            elapsed_ms=int((self._clock() - started_at) * 1000),
        )
        self._completed(result)
        return result

    def _persist_partial(
        self,
        source_id: str,
        origin: str,
        evidence: list[DiscoveryEvidence],
        started_at: float,
        error_code: DataPlaneErrorCode | None,
        prior: SourceCapability | None,
        *,
        browser_actions: int = 0,
    ) -> DiscoveryResult:
        if prior is not None:
            return self._mismatch_result(prior, evidence, started_at, browser_actions)

        plausible = any(
            item.kind in {"origin_change", "route_candidate", "service_desc"}
            for item in evidence
        )
        state = TrustState.QUARANTINED if plausible else TrustState.CANDIDATE
        partial = compile_capability(
            source_id=source_id,
            origin=origin,
            evidence=tuple(evidence),
            revision=1,
        )
        if partial.operations:
            partial = replace(partial, operations={})
        self._store.save(partial)
        result = DiscoveryResult(
            source_id=source_id,
            state=state,
            capability=partial,
            evidence=tuple(evidence),
            elapsed_ms=int((self._clock() - started_at) * 1000),
            browser_actions=browser_actions,
            error_code=error_code.value if error_code is not None else "",
        )
        self._completed(result)
        return result

    def _mismatch_result(
        self,
        prior: SourceCapability,
        evidence: list[DiscoveryEvidence],
        started_at: float,
        browser_actions: int = 0,
    ) -> DiscoveryResult:
        affected = [
            name
            for name, operation in prior.operations.items()
            if not operation.safe
            or operation.method.upper() not in {"GET", "HEAD", "OPTIONS"}
            or operation.trust is TrustState.WRITE_VALIDATED
        ]
        if affected:
            self._store.demote(prior.source_id, affected, "schema_mismatch")
        persisted = self._store.get(prior.source_id) or prior
        self._publish(
            EventType.SOURCE_CAPABILITY_DEMOTED,
            {
                "source_id": prior.source_id,
                "operation_count": len(affected),
                "reason": DataPlaneErrorCode.SCHEMA_MISMATCH.value,
            },
        )
        result = DiscoveryResult(
            source_id=prior.source_id,
            state=TrustState.QUARANTINED,
            capability=persisted,
            evidence=tuple(evidence),
            elapsed_ms=int((self._clock() - started_at) * 1000),
            browser_actions=browser_actions,
            error_code=DataPlaneErrorCode.SCHEMA_MISMATCH.value,
        )
        self._completed(result)
        return result

    def _stage_completed(
        self,
        source_id: str,
        stage: str,
        evidence: list[DiscoveryEvidence],
    ) -> None:
        self._publish(
            EventType.SOURCE_DISCOVERY_STAGE_COMPLETED,
            {
                "source_id": source_id,
                "stage": stage,
                "evidence_count": len(evidence),
            },
        )

    def _completed(self, result: DiscoveryResult) -> None:
        self._publish(
            EventType.SOURCE_DISCOVERY_COMPLETED,
            {
                "source_id": result.source_id,
                "state": result.state.value,
                "evidence_count": len(result.evidence),
                "browser_actions": result.browser_actions,
                "cache_hit": result.cache_hit,
                "error_code": result.error_code,
            },
        )

    def _publish(self, event_type: EventType, data: dict[str, object]) -> None:
        if self._event_bus is not None:
            self._event_bus.publish(event_type, data)


def _content_type(response: object) -> str:
    headers = getattr(response, "headers", {})
    return str(headers.get("content-type", "")).split(";", 1)[0].casefold()


def _origin(url: str) -> str:
    parsed = _split_http_url(url)
    host = parsed.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parsed.scheme.casefold() == "https" and parsed.port == 443) or (
        parsed.scheme.casefold() == "http" and parsed.port == 80
    )
    port = f":{parsed.port}" if parsed.port is not None and not default_port else ""
    return urlunsplit((parsed.scheme.casefold(), f"{host}{port}", "", "", ""))


def _source_id(url: str) -> str:
    parsed = _split_http_url(url)
    host = parsed.hostname.casefold()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    default_port = (parsed.scheme.casefold() == "https" and parsed.port == 443) or (
        parsed.scheme.casefold() == "http" and parsed.port == 80
    )
    port = f":{parsed.port}" if parsed.port is not None and not default_port else ""
    return f"{host}{port}"


def _split_http_url(url: str) -> SplitResult:
    parsed = urlsplit(url)
    try:
        parsed.port
    except ValueError as exc:
        raise DataPlaneError(
            DataPlaneErrorCode.CAPABILITY_QUARANTINED,
            "Source reference has an invalid port",
        ) from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise DataPlaneError(
            DataPlaneErrorCode.CAPABILITY_QUARANTINED,
            "Source reference must be an HTTP(S) URL with a hostname",
        )
    if parsed.username is not None or parsed.password is not None:
        raise DataPlaneError(
            DataPlaneErrorCode.CAPABILITY_QUARANTINED,
            "Source reference URL credentials are forbidden",
        )
    return parsed


def _is_cache_valid(capability: SourceCapability | None, origin: str) -> bool:
    if capability is None or capability.origin != origin:
        return False
    if not any(
        operation.trust is TrustState.READ_VALIDATED
        for operation in capability.operations.values()
    ):
        return False
    try:
        expires_at = datetime.fromisoformat(
            capability.expires_at.replace("Z", "+00:00")
        )
    except ValueError:
        return False
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    return expires_at > datetime.now(timezone.utc)


def _parse_robots(body: str) -> tuple[str, ...]:
    groups: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    agents: list[str] = []
    rules: list[str] = []
    saw_rule = False
    for raw_line in body.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line or ":" not in line:
            continue
        name, value = (part.strip() for part in line.split(":", 1))
        if name.casefold() == "user-agent":
            if saw_rule:
                groups.append((tuple(agents), tuple(rules)))
                agents = []
                rules = []
                saw_rule = False
            agents.append(value.casefold())
        elif name.casefold() == "disallow" and agents:
            saw_rule = True
            if value.startswith("/"):
                rules.append(value[:256])
    if agents:
        groups.append((tuple(agents), tuple(rules)))

    exact_rules = [rules for agents, rules in groups if "openjarvis" in agents]
    selected = exact_rules or [rules for agents, rules in groups if "*" in agents]
    disallowed = [path for rules in selected for path in rules]
    return tuple(sorted(set(disallowed))[:64])


def _append_query(url: str, name: str, value: str) -> str:
    parsed = urlsplit(url)
    query = parse_qsl(parsed.query, keep_blank_values=True)
    query.append((name, value))
    return urlunsplit(parsed._replace(query=urlencode(query)))


def _is_denied(url: str, disallowed_paths: tuple[str, ...]) -> bool:
    path = urlsplit(url).path or "/"
    return any(path.startswith(prefix) for prefix in disallowed_paths if prefix)


def _compact_value(value: object, *, depth: int = 0) -> object:
    if depth >= 4:
        return None
    if isinstance(value, dict):
        compact: dict[str, object] = {}
        for raw_key, nested in list(value.items())[:64]:
            key = str(raw_key)[:128]
            if _UNSAFE_PAYLOAD_KEY.search(key):
                continue
            compact[key] = _compact_value(nested, depth=depth + 1)
        return compact
    if isinstance(value, (list, tuple)):
        return [_compact_value(item, depth=depth + 1) for item in value[:64]]
    if isinstance(value, str):
        if _UNTRUSTED_CLAIM.search(value):
            return ""
        return value[:256]
    if isinstance(value, (bool, int, float)) or value is None:
        return value
    return str(type(value).__name__)


def _compact_browser_evidence(
    evidence: tuple[DiscoveryEvidence, ...],
    origin: str,
) -> tuple[DiscoveryEvidence, ...]:
    compact: list[DiscoveryEvidence] = []
    for item in evidence[:64]:
        try:
            parsed_source = _split_http_url(item.source_url)
            same_origin = _origin(item.source_url) == origin
        except DataPlaneError:
            source_url = origin
        else:
            source_url = (
                urlunsplit(
                    (
                        parsed_source.scheme,
                        parsed_source.netloc,
                        parsed_source.path or "/",
                        "",
                        "",
                    )
                )
                if same_origin
                else origin
            )
        payload = _compact_value(item.payload)
        compact.append(
            replace(
                item,
                source_url=source_url,
                payload=payload if isinstance(payload, dict) else {},
                provenance="browser_observer",
                untrusted=True,
            )
        )
    return tuple(compact)


__all__ = ["BrowserObservationPort", "DiscoveryEngine"]
