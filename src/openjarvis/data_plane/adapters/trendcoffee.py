"""Structured read adapter for the public Trend Coffee API."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime, timedelta, timezone
from typing import cast
from urllib.parse import urlsplit

from openjarvis.core.registry import SourceAdapterRegistry
from openjarvis.data_plane.types import (
    DiscoveryEvidence,
    MatchResult,
    NormalizedBatch,
    OperationContract,
    ResourceRecord,
    SourceCapability,
    TransportKind,
    TrustState,
)

_ORIGIN = "https://trendcoffee.net"
_BASE_URL = f"{_ORIGIN}/api/latest"
_SCRIPT_SRC_RE = re.compile(
    r"<script\b[^>]*\bsrc=[\"'](?P<src>[^\"']+)[\"'][^>]*>", re.IGNORECASE
)


def _hash(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode()
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def _projection_hash(value: object) -> str:
    return _hash(json.dumps(value, sort_keys=True, separators=(",", ":")))


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _non_empty_string(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _free_text_note(value: object) -> bool:
    return isinstance(value, str) and (value == "" or bool(value.strip()))


def _envelope_result(payload: object) -> object:
    if not isinstance(payload, dict) or payload.get("statusCode") != 200:
        raise ValueError("Trend Coffee payload must be a successful provider envelope")
    return payload.get("result")


def _order_projection(payload: dict[str, object]) -> dict[str, object]:
    return {
        "type": payload.get("type"),
        "branch": payload.get("branch"),
        "orderItems": payload.get("orderItems"),
    }


@SourceAdapterRegistry.register("trendcoffee")
class TrendCoffeeAdapter:
    """Compile only observed public reads; retain mutations in quarantine."""

    def match(self, evidence: DiscoveryEvidence) -> MatchResult:
        parsed = urlsplit(evidence.source_url)
        if parsed.scheme == "https" and parsed.hostname == "trendcoffee.net":
            return MatchResult(True, provider="trendcoffee", confidence=1.0)
        return MatchResult(False)

    @staticmethod
    def known_read_targets() -> tuple[tuple[str, str], ...]:
        """The bounded public GETs required for a complete read capability."""
        return (
            ("branch", f"{_BASE_URL}/branch"),
            ("menu_item", f"{_BASE_URL}/products"),
        )

    @staticmethod
    def source_id() -> str:
        return "trend-coffee"

    def compile(
        self, evidence: DiscoveryEvidence | Sequence[DiscoveryEvidence]
    ) -> SourceCapability:
        """Compile the fixed provider contract from attributable GET evidence."""
        evidence_tuple = (
            (evidence,) if isinstance(evidence, DiscoveryEvidence) else tuple(evidence)
        )
        required_reads = {f"{_BASE_URL}/branch", f"{_BASE_URL}/products"}
        observed_reads = {
            item.source_url.rstrip("?")
            for item in evidence_tuple
            if item.method == "GET"
            and item.status_code == 200
            and item.provenance == "publisher_response"
            and item.source_url.rstrip("?") in required_reads
        }
        if observed_reads != required_reads or not any(
            item.method == "GET"
            and item.status_code == 200
            and item.source_url.rstrip("/") == _ORIGIN
            and item.provenance == "publisher_document"
            for item in evidence_tuple
        ):
            raise ValueError(
                "Trend Coffee compile requires attributable homepage and read evidence"
            )

        operations = {
            "branch.list": OperationContract(
                "branch.list",
                "GET",
                "/branch",
                "branch",
                TrustState.READ_VALIDATED,
                True,
            ),
            "menu.list": OperationContract(
                "menu.list",
                "GET",
                "/products?page={page}&size={size}",
                "menu_item",
                TrustState.READ_VALIDATED,
                True,
            ),
            "order.read": OperationContract(
                "order.read",
                "GET",
                "/orders/{order_id}",
                "order",
                TrustState.READ_VALIDATED,
                True,
            ),
            "order.place": OperationContract(
                "order.place",
                "POST",
                "/orders/public",
                "order",
                TrustState.QUARANTINED,
                False,
                verify_operation="order.read",
            ),
            "payment.initiate": OperationContract(
                "payment.initiate",
                "POST",
                "/payment/initiate/public",
                "payment",
                TrustState.QUARANTINED,
                False,
                verify_operation="order.read",
            ),
        }
        fingerprints = sorted(
            item.body_hash for item in evidence_tuple if item.body_hash
        )
        now = datetime.now(timezone.utc)
        return SourceCapability(
            source_id="trend-coffee",
            provider="trendcoffee",
            origin=_ORIGIN,
            base_url=_BASE_URL,
            auth_mode="none",
            credential_ref="",
            transport=TransportKind.REST,
            operations=operations,
            fingerprint=_hash("\n".join(fingerprints)),
            schema_hash=_hash(
                json.dumps(
                    {key: value.to_dict() for key, value in operations.items()},
                    sort_keys=True,
                )
            ),
            evidence=evidence_tuple,
            validated_at=now.isoformat(),
            expires_at=(now + timedelta(days=1)).isoformat(),
            revision=1,
        )

    def normalize(self, resource_type: str, payload: object) -> NormalizedBatch:
        result = _envelope_result(payload)
        if resource_type == "branch":
            items = result if isinstance(result, list) else []
        elif resource_type == "menu_item":
            items = result.get("items", []) if isinstance(result, dict) else []
        elif resource_type in {"order", "payment"}:
            items = [result] if isinstance(result, dict) else []
        else:
            raise ValueError(f"Unsupported Trend Coffee resource type: {resource_type}")
        if not all(
            isinstance(item, dict) and isinstance(item.get("slug"), str)
            for item in items
        ):
            raise ValueError(
                f"Trend Coffee {resource_type} payload has no slugged records"
            )
        records = tuple(
            ResourceRecord(resource_id=item["slug"], payload=dict(item))
            for item in cast(list[dict[str, object]], items)
        )
        return NormalizedBatch(
            source_id="trend-coffee",
            resource_type=resource_type,
            records=records,
            synced_at=_utc_now(),
            provenance=("trendcoffee_public_api",),
        )

    def iter_menu_pages(
        self,
        fetch_page: Callable[[int, int], object],
        *,
        size: int = 100,
    ) -> Iterator[NormalizedBatch]:
        """Yield pages until the provider's ``hasNext`` flag says to stop."""
        page = 1
        while True:
            payload = fetch_page(page, size)
            yield self.normalize("menu_item", payload)
            result = _envelope_result(payload)
            if not isinstance(result, dict) or result.get("hasNext") is not True:
                return
            page += 1

    def build_request(
        self, operation: str, payload: dict[str, object]
    ) -> dict[str, object]:
        """Build exact observed request shapes without performing I/O."""
        if operation == "payment.initiate":
            if (
                set(payload) == {"order", "paymentMethod"}
                and _non_empty_string(payload.get("order"))
                and payload.get("paymentMethod") == "bank-transfer"
            ):
                return {"order": payload["order"], "paymentMethod": "bank-transfer"}
        elif operation == "order.place":
            order = self._build_take_out_order(payload)
            if order is not None:
                return order
        raise ValueError("Only allowlisted Trend Coffee request shapes are supported")

    def create_verification_claim(
        self, operation: str, request: dict[str, object]
    ) -> dict[str, str]:
        if operation == "order.place":
            return {"projection_hash": _projection_hash(_order_projection(request))}
        if operation == "payment.initiate" and _non_empty_string(request.get("order")):
            order_ref = str(request["order"])
            return {
                "projection_hash": _projection_hash({"order": order_ref}),
                "order_ref": order_ref,
            }
        raise ValueError("Trend Coffee operation has no verification claim")

    def verify_verification_claim(
        self,
        operation: str,
        claim: dict[str, str],
        candidate: NormalizedBatch,
        observed: NormalizedBatch,
    ) -> bool:
        if operation == "order.place":
            candidate_refs = {record.resource_id for record in candidate.records}
            return any(
                record.resource_id in candidate_refs
                and claim.get("projection_hash")
                == _projection_hash(_order_projection(record.payload))
                for record in observed.records
            )
        if operation == "payment.initiate":
            order_ref = claim.get("order_ref")
            return (
                isinstance(order_ref, str)
                and claim.get("projection_hash")
                == _projection_hash({"order": order_ref})
                and any(
                    record.payload.get("order") == order_ref
                    for record in candidate.records
                )
                and any(record.resource_id == order_ref for record in observed.records)
            )
        return False

    @staticmethod
    def _build_take_out_order(payload: dict[str, object]) -> dict[str, object] | None:
        if (
            set(payload) != {"order_type", "branch_slug", "items"}
            or payload.get("order_type") != "take-out"
            or not _non_empty_string(payload.get("branch_slug"))
            or not isinstance(payload.get("items"), list)
            or not payload["items"]
        ):
            return None
        items: list[dict[str, object]] = []
        for item in payload["items"]:
            if (
                not isinstance(item, dict)
                or set(item) != {"quantity", "variant_slug", "note"}
                or isinstance(item.get("quantity"), bool)
                or not isinstance(item.get("quantity"), int)
                or item["quantity"] <= 0
                or not _non_empty_string(item.get("variant_slug"))
                or not _free_text_note(item.get("note"))
            ):
                return None
            items.append(
                {
                    "quantity": item["quantity"],
                    "variant": item["variant_slug"],
                    "promotion": None,
                    "note": item["note"],
                }
            )
        return {
            "type": "take-out",
            "timeLeftTakeOut": 0,
            "deliveryTo": "",
            "deliveryPhone": "",
            "table": "",
            "branch": payload["branch_slug"],
            "owner": "",
            "approvalBy": "",
            "orderItems": items,
            "voucher": None,
            "description": "",
        }

    def bundle_evidence(self, home_url: str, bundle_body: bytes) -> DiscoveryEvidence:
        """Keep bundle content identity without persisting its mutable filename."""
        if urlsplit(home_url).hostname != "trendcoffee.net":
            raise ValueError(
                "Trend Coffee bundle evidence must originate from trendcoffee.net"
            )
        return DiscoveryEvidence(
            kind="bundle_fingerprint",
            source_url=_ORIGIN,
            status_code=200,
            content_type="application/javascript",
            body_hash=_hash(bundle_body),
            provenance="publisher_bundle",
        )

    @staticmethod
    def discover_bundle_paths(home_html: str) -> tuple[str, ...]:
        """Re-discover script references from each current publisher homepage."""
        return tuple(match.group("src") for match in _SCRIPT_SRC_RE.finditer(home_html))
