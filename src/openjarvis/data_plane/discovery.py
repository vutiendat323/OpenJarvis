"""HTTP-first discovery cascade for structured source capabilities."""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
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
    OperationContract,
    SourceCapability,
    SourceRef,
    TransportKind,
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
_SAFE_BROWSER_ROUTE = re.compile(r"^/(?:api/)?[A-Za-z0-9_./{}:-]+$")
_SAFE_CONTENT_TYPE = re.compile(r"^[a-z0-9!#$&^_.+-]+/[a-z0-9!#$&^_.+-]+$")
_SAFE_BODY_HASH = re.compile(r"^sha256:[0-9a-f]{64}$")


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

        if not root_response.is_success:
            return self._persist_partial(
                source_id,
                requested_origin,
                evidence,
                started_at,
                _response_error_code(root_response.status_code),
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
        if (
            prior is not None
            and _has_attributable_schema(evidence)
            and capability.schema_hash != prior.schema_hash
        ):
            return self._mismatch_result(prior, evidence, started_at)

        capability, validation_evidence, validation_error = self._validate_safe_reads(
            capability,
            requested_origin,
            disallowed_paths,
            deadline,
        )
        evidence.extend(validation_evidence)
        self._stage_completed(source_id, "safe_read_validation", evidence)
        if validation_error is not None:
            return self._persist_partial(
                source_id,
                requested_origin,
                evidence,
                started_at,
                validation_error,
                prior,
            )
        capability = replace(capability, evidence=tuple(evidence))
        if _has_validated_read(capability):
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

    def _validate_safe_reads(
        self,
        capability: SourceCapability,
        source_origin: str,
        disallowed_paths: tuple[str, ...],
        deadline: float,
    ) -> tuple[
        SourceCapability,
        list[DiscoveryEvidence],
        DataPlaneErrorCode | None,
    ]:
        operations = dict(capability.operations)
        validation_evidence: list[DiscoveryEvidence] = []
        for name, operation in capability.operations.items():
            if operation.trust is not TrustState.QUARANTINED:
                continue
            validation_url = _validation_url(capability, operation)
            if not validation_url or not operation.response_schema:
                continue
            if _origin(validation_url) == source_origin and _is_denied(
                validation_url,
                disallowed_paths,
            ):
                continue
            if self._clock() >= deadline:
                return (
                    replace(capability, operations=operations),
                    validation_evidence,
                    DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED,
                )
            try:
                response = self.http.fetch(validation_url, method="GET")
            except DataPlaneError as exc:
                if exc.code is DataPlaneErrorCode.DISCOVERY_BUDGET_EXCEEDED:
                    return (
                        replace(capability, operations=operations),
                        validation_evidence,
                        exc.code,
                    )
                continue

            matched = False
            if response.is_success and _is_json_content_type(_content_type(response)):
                try:
                    payload = response.json()
                except ValueError:
                    payload = None
                matched = _matches_schema(payload, operation.response_schema)
            validation_evidence.append(
                DiscoveryEvidence(
                    kind="safe_read_validation",
                    source_url=_redacted_url(str(response.url)),
                    method="GET",
                    status_code=response.status_code,
                    content_type=_content_type(response),
                    payload={"operation": name, "schema_match": matched},
                    body_hash=f"sha256:{hashlib.sha256(response.content).hexdigest()}",
                    provenance="publisher_safe_read",
                )
            )
            if matched:
                operations[name] = replace(operation, trust=TrustState.READ_VALIDATED)

        has_validated = any(
            operation.trust is TrustState.READ_VALIDATED
            for operation in operations.values()
        )
        if has_validated and not capability.validated_at:
            observed = datetime.now(timezone.utc)
            capability = replace(
                capability,
                operations=operations,
                validated_at=observed.isoformat(),
                expires_at=(observed + timedelta(hours=1)).isoformat(),
            )
        else:
            capability = replace(capability, operations=operations)
        return capability, validation_evidence, None

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
            if any(item.kind == "origin_change" for item in evidence):
                return self._mismatch_result(
                    prior,
                    evidence,
                    started_at,
                    browser_actions,
                )
            candidate = compile_capability(
                source_id=source_id,
                origin=origin,
                evidence=tuple(evidence),
                revision=prior.revision + 1,
            )
            if (
                _has_attributable_schema(evidence)
                and candidate.schema_hash != prior.schema_hash
            ):
                return self._mismatch_result(
                    prior,
                    evidence,
                    started_at,
                    browser_actions,
                )
            return self._prior_error_result(
                prior,
                evidence,
                started_at,
                error_code,
                browser_actions,
            )

        plausible = any(
            item.kind
            in {
                "graphql",
                "openapi",
                "origin_change",
                "route_candidate",
                "service_desc",
            }
            for item in evidence
        )
        state = TrustState.QUARANTINED if plausible else TrustState.CANDIDATE
        partial = compile_capability(
            source_id=source_id,
            origin=origin,
            evidence=tuple(evidence),
            revision=1,
        )
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

    def _prior_error_result(
        self,
        prior: SourceCapability,
        evidence: list[DiscoveryEvidence],
        started_at: float,
        error_code: DataPlaneErrorCode | None,
        browser_actions: int,
    ) -> DiscoveryResult:
        state = (
            TrustState.READ_VALIDATED
            if any(
                operation.trust is TrustState.READ_VALIDATED
                for operation in prior.operations.values()
            )
            else TrustState.QUARANTINED
        )
        result = DiscoveryResult(
            source_id=prior.source_id,
            state=state,
            capability=prior,
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


def _response_error_code(status_code: int) -> DataPlaneErrorCode:
    if status_code == 429:
        return DataPlaneErrorCode.RATE_LIMITED
    if status_code >= 500:
        return DataPlaneErrorCode.PROVIDER_UNAVAILABLE
    return DataPlaneErrorCode.CAPABILITY_QUARANTINED


def _has_validated_read(capability: SourceCapability) -> bool:
    return any(
        operation.trust is TrustState.READ_VALIDATED
        for operation in capability.operations.values()
    )


def _has_attributable_schema(evidence: list[DiscoveryEvidence]) -> bool:
    return any(
        item.kind in {"embedded_json", "graphql", "json_ld", "openapi"}
        and item.provenance in {"publisher_declared", "publisher_document"}
        for item in evidence
    )


def _validation_url(
    capability: SourceCapability,
    operation: OperationContract,
) -> str:
    if operation.method.upper() != "GET":
        return ""
    if capability.transport is TransportKind.GRAPHQL:
        arguments = operation.request_schema.get("arguments", [])
        if isinstance(arguments, list) and any(
            isinstance(argument, dict) and argument.get("required")
            for argument in arguments
        ):
            return ""
        template = operation.request_schema.get("query_template")
        if not isinstance(template, str) or not template:
            return ""
        return _append_query(capability.base_url, "query", template)

    required_parameters = operation.request_schema.get("required_parameters", [])
    if required_parameters or "{" in operation.path or "}" in operation.path:
        return ""
    return f"{capability.base_url.rstrip('/')}/{operation.path.lstrip('/')}"


def _is_json_content_type(content_type: str) -> bool:
    return content_type == "application/json" or content_type.endswith("+json")


def _matches_schema(value: object, schema: dict[str, object]) -> bool:
    schema_type = schema.get("type")
    if schema_type == "object":
        if not isinstance(value, dict):
            return False
        required = schema.get("required", [])
        if isinstance(required, list) and any(name not in value for name in required):
            return False
        properties = schema.get("properties", {})
        if isinstance(properties, dict):
            for name, nested_schema in properties.items():
                if name not in value:
                    continue
                if isinstance(nested_schema, dict) and not _matches_schema(
                    value[name],
                    nested_schema,
                ):
                    return False
        return True
    if schema_type == "array":
        if not isinstance(value, list):
            return False
        item_schema = schema.get("items")
        return not isinstance(item_schema, dict) or all(
            _matches_schema(item, item_schema) for item in value
        )
    if schema_type == "string":
        return isinstance(value, str)
    if schema_type == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if schema_type == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if schema_type == "boolean":
        return isinstance(value, bool)
    if schema_type == "null":
        return value is None
    return False


def _redacted_url(url: str) -> str:
    parsed = _split_http_url(url)
    return f"{_origin(url)}{parsed.path or '/'}"


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


def _compact_browser_evidence(
    evidence: tuple[DiscoveryEvidence, ...],
    origin: str,
) -> tuple[DiscoveryEvidence, ...]:
    compact: list[DiscoveryEvidence] = []
    for item in evidence[:64]:
        kind = item.kind if item.kind == "route_candidate" else "browser_observation"
        payload: dict[str, object] = {}
        if kind == "route_candidate":
            routes = item.payload.get("routes", [])
            if isinstance(routes, (list, tuple)):
                payload["routes"] = [
                    route[:256]
                    for route in routes[:64]
                    if isinstance(route, str) and _SAFE_BROWSER_ROUTE.fullmatch(route)
                ]
        method = item.method.upper() if isinstance(item.method, str) else "GET"
        if method not in {"GET", "HEAD", "OPTIONS"}:
            method = "GET"
        status_code = (
            item.status_code
            if isinstance(item.status_code, int) and 100 <= item.status_code <= 599
            else 0
        )
        content_type = (
            item.content_type.split(";", 1)[0].casefold()[:128]
            if isinstance(item.content_type, str)
            else ""
        )
        if not _SAFE_CONTENT_TYPE.fullmatch(content_type):
            content_type = ""
        body_hash = (
            item.body_hash.casefold()
            if isinstance(item.body_hash, str)
            and _SAFE_BODY_HASH.fullmatch(item.body_hash.casefold())
            else ""
        )
        compact.append(
            DiscoveryEvidence(
                kind=kind,
                source_url=origin,
                method=method,
                status_code=status_code,
                content_type=content_type,
                payload=payload,
                body_hash=body_hash,
                provenance="browser_observer",
                untrusted=True,
                observed_at="",
            )
        )
    return tuple(compact)


__all__ = ["BrowserObservationPort", "DiscoveryEngine"]
