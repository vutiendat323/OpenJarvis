"""Generic extraction of publisher-attributable structured read evidence."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

from openjarvis.data_plane.types import (
    DiscoveryEvidence,
    OperationContract,
    SourceCapability,
    TransportKind,
    TrustState,
)

SUPPORTED_LINK_RELS = {"api-catalog", "service-desc", "service-doc"}
JSON_LD_TYPES = {"Restaurant", "Menu", "MenuItem", "Product", "Offer"}
ROUTE_PATTERN = re.compile(r"[\"'](/(?:api/)?[A-Za-z0-9_./{}:-]+)[\"']")

_OPENAPI_CONTENT_TYPES = {
    "application/json",
    "application/openapi+json",
    "application/vnd.oai.openapi+json",
}
_GRAPHQL_CONTENT_TYPES = {"application/graphql", "application/graphql+json"}
_UNSAFE_KEY_PARTS = {
    "authorization",
    "cookie",
    "credential",
    "password",
    "permission",
    "secret",
    "token",
}
_UNTRUSTED_CLAIM = re.compile(
    r"(?:system\s+instruction|ignore\s+previous|request\s+(?:api\s+)?credentials|"
    r"provide\s+credentials|promote\s+(?:this\s+)?capability|grant\s+permission)",
    re.IGNORECASE,
)
_MAX_ITEMS = 64
_MAX_STRING = 256


def _hash(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode()
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _public_url(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return ""
    if (
        parsed.scheme.casefold() not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return ""
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    try:
        parsed_port = parsed.port
    except ValueError:
        return ""
    port = f":{parsed_port}" if parsed_port is not None else ""
    return f"{parsed.scheme.casefold()}://{host.casefold()}{port}{parsed.path or '/'}"


def _canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def _safe_name(value: object) -> str:
    text = str(value)[:_MAX_STRING]
    if _UNTRUSTED_CLAIM.search(text):
        return ""
    return text


def _safe_property_name(value: object) -> str:
    text = _safe_name(value)
    folded = text.casefold()
    if any(part in folded for part in _UNSAFE_KEY_PARTS):
        return ""
    return text


def _schema_sample(value: object, *, depth: int = 0) -> dict[str, object]:
    """Describe JSON shape without retaining publisher values or prose."""
    if depth >= 4:
        return {"type": "object"}
    if isinstance(value, dict):
        properties: dict[str, object] = {}
        for raw_key, nested in list(value.items())[:_MAX_ITEMS]:
            key = _safe_property_name(raw_key)
            if key:
                properties[key] = _schema_sample(nested, depth=depth + 1)
        return {"type": "object", "properties": properties}
    if isinstance(value, list):
        item = value[0] if value else None
        return {"type": "array", "items": _schema_sample(item, depth=depth + 1)}
    if isinstance(value, bool):
        return {"type": "boolean"}
    if isinstance(value, int):
        return {"type": "integer"}
    if isinstance(value, float):
        return {"type": "number"}
    if value is None:
        return {"type": "null"}
    return {"type": "string"}


def _sanitize_schema(value: object, *, depth: int = 0) -> dict[str, object]:
    """Keep structural JSON Schema fields and drop descriptions/claims."""
    if not isinstance(value, dict) or depth >= 6:
        return {}
    result: dict[str, object] = {}
    schema_type = value.get("type")
    if isinstance(schema_type, str) and schema_type in {
        "array",
        "boolean",
        "integer",
        "null",
        "number",
        "object",
        "string",
    }:
        result["type"] = schema_type
    schema_format = value.get("format")
    if isinstance(schema_format, str) and len(schema_format) <= 64:
        result["format"] = schema_format
    properties = value.get("properties")
    if isinstance(properties, dict):
        clean_properties: dict[str, object] = {}
        for raw_name, nested in list(properties.items())[:_MAX_ITEMS]:
            name = _safe_property_name(raw_name)
            if name:
                clean_properties[name] = _sanitize_schema(nested, depth=depth + 1)
        result["properties"] = clean_properties
    if "items" in value:
        result["items"] = _sanitize_schema(value["items"], depth=depth + 1)
    required = value.get("required")
    if isinstance(required, list):
        result["required"] = [
            name
            for item in required[:_MAX_ITEMS]
            if (name := _safe_property_name(item))
        ]
    return result


def _json_values(value: object) -> list[dict[str, object]]:
    if isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            return [item for item in graph if isinstance(item, dict)]
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _json_ld_type(value: dict[str, object]) -> str:
    raw_type = value.get("@type", "")
    if isinstance(raw_type, list):
        candidates = raw_type
    else:
        candidates = [raw_type]
    for candidate in candidates:
        text = str(candidate).rsplit("/", 1)[-1]
        if text in JSON_LD_TYPES:
            return text
    return ""


@dataclass(frozen=True, slots=True)
class DeclaredEndpoint:
    url: str
    relation: str
    content_type: str


@dataclass(frozen=True, slots=True)
class HtmlExtraction:
    evidence: tuple[DiscoveryEvidence, ...]
    declared_endpoints: tuple[DeclaredEndpoint, ...]
    static_assets: tuple[str, ...]


class _PublisherHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[dict[str, str]] = []
        self.assets: list[str] = []
        self.policies: list[str] = []
        self.structured_scripts: list[tuple[str, str]] = []
        self._script_type = ""
        self._script_chunks: list[str] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        values = {key.casefold(): value or "" for key, value in attrs}
        if tag.casefold() == "link":
            self.links.append(values)
        elif tag.casefold() == "meta" and values.get("name", "").casefold() == (
            "openjarvis-discovery-policy"
        ):
            self.policies.append(values.get("content", ""))
        elif tag.casefold() == "script":
            source = values.get("src")
            if source:
                self.assets.append(source)
            self._script_type = values.get("type", "").split(";", 1)[0].casefold()
            self._script_chunks = []

    def handle_data(self, data: str) -> None:
        if self._script_type in {"application/ld+json", "application/json"}:
            self._script_chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() != "script":
            return
        if self._script_type and self._script_chunks:
            self.structured_scripts.append(
                (self._script_type, "".join(self._script_chunks))
            )
        self._script_type = ""
        self._script_chunks = []


def extract_html(
    source_url: str,
    body: str,
    *,
    status_code: int,
    content_type: str,
) -> HtmlExtraction:
    parser = _PublisherHtmlParser()
    parser.feed(body)
    public_source_url = _public_url(source_url)
    evidence: list[DiscoveryEvidence] = []
    declared: list[DeclaredEndpoint] = []

    for link in parser.links[:_MAX_ITEMS]:
        rels = set(link.get("rel", "").casefold().split())
        matched = rels & SUPPORTED_LINK_RELS
        href = link.get("href", "")
        if not matched or not href:
            continue
        try:
            endpoint_url = urljoin(source_url, href)
        except ValueError:
            continue
        public_endpoint_url = _public_url(endpoint_url)
        if not public_endpoint_url:
            continue
        relation = sorted(matched)[0]
        declared_type = link.get("type", "").split(";", 1)[0].casefold()
        declared.append(
            DeclaredEndpoint(
                url=endpoint_url,
                relation=relation,
                content_type=declared_type,
            )
        )
        evidence.append(
            DiscoveryEvidence(
                kind="service_desc",
                source_url=public_source_url,
                status_code=status_code,
                content_type=content_type,
                payload={
                    "endpoint": public_endpoint_url[:_MAX_STRING],
                    "relation": relation,
                    "declared_type": declared_type,
                },
                body_hash=_hash(f"{relation}\0{endpoint_url}\0{declared_type}"),
                provenance="publisher_document",
            )
        )

    for script_type, script_body in parser.structured_scripts[:_MAX_ITEMS]:
        try:
            decoded = json.loads(script_body)
        except (json.JSONDecodeError, TypeError):
            continue
        if script_type == "application/ld+json":
            typed_values = [
                (item, structured_type)
                for item in _json_values(decoded)
                if (structured_type := _json_ld_type(item))
            ]
            if not typed_values:
                continue
            evidence.append(
                DiscoveryEvidence(
                    kind="json_ld",
                    source_url=public_source_url,
                    status_code=status_code,
                    content_type=script_type,
                    payload={
                        "types": sorted({item[1] for item in typed_values}),
                        "schema": _schema_sample(typed_values[0][0]),
                    },
                    body_hash=_hash(script_body),
                    provenance="publisher_document",
                )
            )
        elif isinstance(decoded, (dict, list)):
            evidence.append(
                DiscoveryEvidence(
                    kind="embedded_json",
                    source_url=public_source_url,
                    status_code=status_code,
                    content_type=script_type,
                    payload={"schema": _schema_sample(decoded)},
                    body_hash=_hash(script_body),
                    provenance="publisher_document",
                )
            )

    for policy in parser.policies[:_MAX_ITEMS]:
        normalized = policy.casefold().strip()
        if normalized not in {"no-browser", "no-static-assets", "structured-only"}:
            continue
        evidence.append(
            DiscoveryEvidence(
                kind="publisher_policy",
                source_url=public_source_url,
                status_code=status_code,
                content_type="text/html",
                payload={"policy": normalized},
                body_hash=_hash(normalized),
                provenance="publisher_document",
            )
        )

    assets: list[str] = []
    for asset in parser.assets[:8]:
        try:
            resolved = urljoin(source_url, asset)
        except ValueError:
            continue
        if _public_url(resolved):
            assets.append(resolved)
    return HtmlExtraction(
        evidence=tuple(evidence),
        declared_endpoints=tuple(declared),
        static_assets=tuple(assets),
    )


def extract_openapi(
    source_url: str,
    body: str,
    *,
    status_code: int,
    content_type: str,
) -> DiscoveryEvidence | None:
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(document, dict) or not (
        isinstance(document.get("openapi"), str)
        or isinstance(document.get("swagger"), str)
    ):
        return None
    paths = document.get("paths")
    if not isinstance(paths, dict):
        return None
    operations: list[dict[str, object]] = []
    for raw_path, path_item in list(paths.items())[:_MAX_ITEMS]:
        if not isinstance(path_item, dict):
            continue
        path = _safe_name(raw_path)
        if not path.startswith("/"):
            continue
        for method in ("get", "head", "options"):
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            operation_id = _safe_name(operation.get("operationId", ""))
            if not operation_id:
                operation_id = f"{method}_{path.strip('/').replace('/', '_') or 'root'}"
            response_schema: dict[str, object] = {}
            responses = operation.get("responses")
            if isinstance(responses, dict):
                response = next(
                    (
                        value
                        for key, value in responses.items()
                        if str(key).startswith("2") and isinstance(value, dict)
                    ),
                    None,
                )
                if response:
                    content = response.get("content")
                    if isinstance(content, dict):
                        media = content.get("application/json")
                        if isinstance(media, dict):
                            response_schema = _sanitize_schema(media.get("schema"))
            operations.append(
                {
                    "name": operation_id,
                    "method": method.upper(),
                    "path": path,
                    "response_schema": response_schema,
                }
            )
    if not operations:
        return None
    payload = {
        "api_origin": _origin(source_url),
        "operations": operations,
        "version": _safe_name(document.get("openapi", document.get("swagger", ""))),
    }
    return DiscoveryEvidence(
        kind="openapi",
        source_url=_public_url(source_url),
        status_code=status_code,
        content_type=content_type,
        payload=payload,
        body_hash=_hash(body),
        provenance="publisher_declared",
    )


def extract_graphql(
    source_url: str,
    body: str,
    *,
    status_code: int,
    content_type: str,
) -> DiscoveryEvidence | None:
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(document, dict):
        return None
    data = document.get("data")
    schema = data.get("__schema") if isinstance(data, dict) else None
    if not isinstance(schema, dict):
        return None
    query_type = schema.get("queryType")
    query_name = query_type.get("name") if isinstance(query_type, dict) else "Query"
    fields: list[str] = []
    for type_item in schema.get("types", []):
        if not isinstance(type_item, dict) or type_item.get("name") != query_name:
            continue
        for field in type_item.get("fields", [])[:_MAX_ITEMS]:
            if isinstance(field, dict) and (
                name := _safe_property_name(field.get("name", ""))
            ):
                fields.append(name)
    if not fields:
        return None
    return DiscoveryEvidence(
        kind="graphql",
        source_url=_public_url(source_url),
        status_code=status_code,
        content_type=content_type,
        payload={
            "endpoint": _public_url(source_url)[:_MAX_STRING],
            "query_fields": sorted(set(fields)),
        },
        body_hash=_hash(body),
        provenance="publisher_declared",
    )


def extract_routes(
    source_url: str,
    body: str,
    *,
    status_code: int,
    content_type: str,
) -> DiscoveryEvidence | None:
    routes = sorted(set(ROUTE_PATTERN.findall(body)))[:_MAX_ITEMS]
    if not routes:
        return None
    return DiscoveryEvidence(
        kind="route_candidate",
        source_url=_public_url(source_url),
        status_code=status_code,
        content_type=content_type,
        payload={"routes": routes},
        body_hash=_hash(body),
        provenance="publisher_static_asset",
    )


def _origin(url: str) -> str:
    parsed = urlsplit(url)
    host = parsed.hostname or ""
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return f"{parsed.scheme.casefold()}://{host.casefold()}{port}"


def compile_capability(
    *,
    source_id: str,
    origin: str,
    evidence: tuple[DiscoveryEvidence, ...],
    revision: int,
    now: datetime | None = None,
) -> SourceCapability:
    operations: dict[str, OperationContract] = {}
    transport = TransportKind.EMBEDDED
    base_url = origin
    attributable_kinds: set[str] = set()
    for item in evidence:
        if item.provenance not in {"publisher_declared", "publisher_document"}:
            continue
        if item.kind == "openapi":
            transport = TransportKind.REST
            api_origin = item.payload.get("api_origin")
            if isinstance(api_origin, str) and api_origin:
                base_url = api_origin
            for raw_operation in item.payload.get("operations", []):
                if not isinstance(raw_operation, dict):
                    continue
                method = str(raw_operation.get("method", "")).upper()
                if method not in {"GET", "HEAD", "OPTIONS"}:
                    continue
                name = _safe_property_name(raw_operation.get("name", ""))
                path = _safe_name(raw_operation.get("path", ""))
                if not name or not path.startswith("/"):
                    continue
                operations[name] = OperationContract(
                    name=name,
                    method=method,
                    path=path,
                    resource_type="structured_record",
                    trust=TrustState.READ_VALIDATED,
                    safe=True,
                    response_schema=_sanitize_schema(
                        raw_operation.get("response_schema")
                    ),
                )
            attributable_kinds.add(item.kind)
        elif item.kind == "graphql":
            if transport is TransportKind.EMBEDDED:
                transport = TransportKind.GRAPHQL
                base_url = str(item.payload.get("endpoint", origin))
            for field in item.payload.get("query_fields", []):
                name = _safe_property_name(field)
                if name:
                    operation_name = f"graphql.{name}"
                    operations[operation_name] = OperationContract(
                        name=operation_name,
                        method="GET",
                        path=urlsplit(str(item.payload.get("endpoint", origin))).path
                        or "/",
                        resource_type="structured_record",
                        trust=TrustState.READ_VALIDATED,
                        safe=True,
                    )
            attributable_kinds.add(item.kind)
        elif item.kind in {"json_ld", "embedded_json"}:
            operation_name = (
                "structured.read" if item.kind == "json_ld" else "embedded.read"
            )
            operations.setdefault(
                operation_name,
                OperationContract(
                    name=operation_name,
                    method="GET",
                    path=urlsplit(item.source_url).path or "/",
                    resource_type="structured_record",
                    trust=TrustState.READ_VALIDATED,
                    safe=True,
                    response_schema=_sanitize_schema(item.payload.get("schema")),
                ),
            )
            attributable_kinds.add(item.kind)

    schema_material = [
        {"kind": item.kind, "payload": item.payload}
        for item in evidence
        if item.kind in attributable_kinds
    ]
    fingerprint_material = [
        {
            "kind": item.kind,
            "source_url": item.source_url,
            "body_hash": item.body_hash,
            "provenance": item.provenance,
        }
        for item in evidence
    ]
    observed = now or datetime.now(timezone.utc)
    return SourceCapability(
        source_id=source_id,
        provider="generic",
        origin=origin,
        base_url=base_url,
        auth_mode="none",
        credential_ref="",
        transport=transport,
        operations=operations,
        fingerprint=_hash(_canonical(fingerprint_material)),
        schema_hash=_hash(_canonical(schema_material)),
        evidence=evidence,
        validated_at=observed.isoformat() if operations else "",
        expires_at=(observed + timedelta(hours=1)).isoformat() if operations else "",
        revision=revision,
    )


def is_graphql_declaration(endpoint: DeclaredEndpoint) -> bool:
    return endpoint.content_type in _GRAPHQL_CONTENT_TYPES


def is_openapi_declaration(endpoint: DeclaredEndpoint) -> bool:
    return (
        endpoint.content_type in _OPENAPI_CONTENT_TYPES
        or endpoint.url.casefold().endswith(("/openapi.json", "/swagger.json"))
    )


__all__ = [
    "JSON_LD_TYPES",
    "ROUTE_PATTERN",
    "SUPPORTED_LINK_RELS",
    "DeclaredEndpoint",
    "HtmlExtraction",
    "compile_capability",
    "extract_graphql",
    "extract_html",
    "extract_openapi",
    "extract_routes",
    "is_graphql_declaration",
    "is_openapi_declaration",
]
