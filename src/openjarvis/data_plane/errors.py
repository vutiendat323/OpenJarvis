"""Structured errors for the Universal Data Plane."""

from __future__ import annotations

from enum import Enum


class DataPlaneErrorCode(str, Enum):
    CAPABILITY_MISSING = "capability_missing"
    CAPABILITY_QUARANTINED = "capability_quarantined"
    CAPABILITY_STALE = "capability_stale"
    SCHEMA_MISMATCH = "schema_mismatch"
    AUTHENTICATION_REQUIRED = "authentication_required"
    AUTHENTICATION_EXPIRED = "authentication_expired"
    RATE_LIMITED = "rate_limited"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    DISCOVERY_BUDGET_EXCEEDED = "discovery_budget_exceeded"
    DISCOVERY_UNSAFE_METHOD = "discovery_unsafe_method"
    MUTATION_AMBIGUOUS = "mutation_ambiguous"
    VERIFICATION_FAILED = "verification_failed"
    ARTIFACT_REJECTED = "artifact_rejected"


class DataPlaneError(RuntimeError):
    def __init__(self, code: DataPlaneErrorCode, message: str, **details: object):
        super().__init__(message)
        self.code = code
        self.details = details


__all__ = ["DataPlaneError", "DataPlaneErrorCode"]
