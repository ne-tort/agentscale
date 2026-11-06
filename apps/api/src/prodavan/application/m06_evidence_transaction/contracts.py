"""Persistence-neutral, immutable M06 evidence transaction contracts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping

from prodavan.application.shared_identity import IdentityEnvelope


class QuarantineAllowResult(StrEnum):
    ALLOW = "allow"
    QUARANTINE = "quarantine"
    DENY = "deny"


@dataclass(frozen=True, slots=True)
class TransactionContext:
    tenant_id: str
    actor_id: str
    writer_generation: int
    request_id: str

    def __post_init__(self) -> None:
        _required(self.tenant_id, "tenant_id")
        _required(self.actor_id, "actor_id")
        _required(self.request_id, "request_id")
        if self.writer_generation <= 0:
            raise ValueError("writer_generation must be positive")


@dataclass(frozen=True, slots=True)
class RedactedProjection:
    """MCP projection: digests and metadata only, never raw arguments/secrets."""

    argument_digest: str
    result: QuarantineAllowResult
    error_code: str | None
    occurred_at: datetime
    tool_name: str
    correlation_id: str
    trace_id: str
    span_id: str
    payload_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.result, QuarantineAllowResult):
            raise ValueError("result must be a quarantine/allow/deny decision")
        _digest(self.argument_digest, "argument_digest")
        _digest(self.payload_digest, "payload_digest")
        _required(self.tool_name, "tool_name")
        _required(self.correlation_id, "correlation_id")
        _required(self.trace_id, "trace_id")
        _required(self.span_id, "span_id")
        _aware(self.occurred_at, "occurred_at")
        if self.result in (QuarantineAllowResult.QUARANTINE, QuarantineAllowResult.DENY) and not self.error_code:
            raise ValueError("quarantine and deny projections require error_code")
        if self.result is QuarantineAllowResult.ALLOW and self.error_code is not None:
            raise ValueError("allow projection cannot contain error_code")
        if self.error_code is not None:
            _required(self.error_code, "error_code")



@dataclass(frozen=True, slots=True)
class PromotionLineage:
    parent_record_id: str
    source_snapshot_id: str
    source_digest: str
    canonical_digest: str
    binding_digest: str
    lineage_reference: str

    def __post_init__(self) -> None:
        for name in ("parent_record_id", "source_snapshot_id", "lineage_reference"):
            _required(getattr(self, name), name)
        for name in ("source_digest", "canonical_digest", "binding_digest"):
            _digest(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class BundleSnapshot:
    snapshot_id: str
    schema_version: str
    bundle_digest: str
    captured_at: datetime

    def __post_init__(self) -> None:
        _required(self.snapshot_id, "snapshot_id")
        _required(self.schema_version, "schema_version")
        _digest(self.bundle_digest, "bundle_digest")
        _aware(self.captured_at, "captured_at")


@dataclass(frozen=True, slots=True)
class AppendOnlyAudit:
    event_id: str
    event_type: str
    occurred_at: datetime
    audit_digest: str
    correlation_id: str

    def __post_init__(self) -> None:
        for name in ("event_id", "event_type", "correlation_id"):
            _required(getattr(self, name), name)
        _digest(self.audit_digest, "audit_digest")
        _aware(self.occurred_at, "occurred_at")


@dataclass(frozen=True, slots=True)
class AuditOutcomeEvidenceV2:
    """Complete M06 envelope. It is the sole trusted transaction input."""

    event_id: str
    tenant_id: str
    decision: QuarantineAllowResult
    occurred_at: datetime
    identity_digest: str
    capability_key: str
    promotion: PromotionLineage
    quarantine_or_allow: QuarantineAllowResult
    redacted: RedactedProjection
    bundle: BundleSnapshot
    audit: AppendOnlyAudit
    metadata: Mapping[str, Any]
    identity: IdentityEnvelope

    def __post_init__(self) -> None:
        _required(self.event_id, "event_id")
        _required(self.tenant_id, "tenant_id")
        if not isinstance(self.decision, QuarantineAllowResult):
            raise ValueError("decision must be a quarantine/allow/deny decision")
        if not isinstance(self.quarantine_or_allow, QuarantineAllowResult):
            raise ValueError("quarantine_or_allow must be a quarantine/allow/deny decision")
        _digest(self.identity_digest, "identity_digest")
        _required(self.capability_key, "capability_key")
        _aware(self.occurred_at, "occurred_at")
        _json_mapping(self.metadata)
        object.__setattr__(self, "metadata", _freeze_mapping(self.metadata))
        if self.identity.tenant_id != self.tenant_id:
            raise ValueError("identity tenant must match evidence tenant")
        if self.identity.envelope_digest != self.identity_digest:
            raise ValueError("identity digest must match shared identity envelope")
        if self.audit.event_id != self.event_id:
            raise ValueError("audit event_id must match evidence event_id")
        if self.audit.correlation_id != self.identity.correlation_id:
            raise ValueError("audit correlation must match shared identity")
        if self.redacted.correlation_id != self.identity.correlation_id:
            raise ValueError("redacted correlation must match shared identity")
        if self.redacted.trace_id != self.identity.trace_id:
            raise ValueError("redacted trace must match shared identity")
        if self.redacted.occurred_at != self.occurred_at:
            raise ValueError("redacted timestamp must match evidence timestamp")
        if self.audit.occurred_at != self.occurred_at:
            raise ValueError("audit timestamp must match evidence timestamp")
        if self.promotion.source_snapshot_id != self.bundle.snapshot_id:
            raise ValueError("promotion source snapshot must match bundle snapshot")
        if self.promotion.source_digest != self.bundle.bundle_digest:
            raise ValueError("promotion source digest must match bundle digest")
        if self.promotion.lineage_reference != self.identity.correlation_id:
            raise ValueError("promotion lineage must match shared identity")
        if self.quarantine_or_allow is not self.decision:
            raise ValueError("decision must match quarantine/allow result")
        if self.redacted.result is not self.quarantine_or_allow:
            raise ValueError("redacted result must match quarantine/allow result")


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _digest(value: str, name: str) -> None:
    _required(value, name)
    if not value.startswith("sha256:") or len(value) != 71 or any(c not in "0123456789abcdefABCDEF" for c in value[7:]):
        raise ValueError(f"{name} must be a sha256: digest")


def _aware(value: datetime, name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _json_mapping(value: Mapping[str, Any]) -> None:
    if not isinstance(value, Mapping) or any(not isinstance(k, str) or not k.strip() or _forbidden_key(k) or not _json_value(v) for k, v in value.items()):
        raise ValueError("metadata must contain redacted JSON values and no raw arguments or secrets")


def _forbidden_key(value: str) -> bool:
    key = value.casefold().replace("-", "_")
    return key in {"arg", "args", "arguments", "raw_input", "payload", "secret", "secrets", "token", "credential", "credentials"}
def _freeze_mapping(value: Mapping[str, Any]) -> MappingProxyType:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, list):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze_value(item) for item in value)
    return value


def _json_value(value: Any) -> bool:
    if value is None or isinstance(value, (str, bool, int, float)):
        return True
    if isinstance(value, Mapping):
        return all(isinstance(k, str) and k.strip() and not _forbidden_key(k) and _json_value(v) for k, v in value.items())
    if isinstance(value, (tuple, list)):
        return all(_json_value(item) for item in value)
    return False
