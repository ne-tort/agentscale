"""Bounded M06 grant mapping and shadow comparison; never an authorization owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from prodavan.application.scope_registry.contracts import RlsContext

SCHEMA_VERSION = "m06-grant-mapping-v4"
OPERATION_MAP_VERSION = "operation-constraints-v1"
METHOD_MAPPING_VERSION = "capability-method-mapping-v1"
APPROVED_CAPABILITIES = frozenset({
    "ui.render", "ui.navigation", "storage.kv", "storage.files", "network.fetch",
    "events.subscribe", "platform.commands", "platform.queries", "secrets.read", "identity.read",
})
CAPABILITY_OPERATIONS = MappingProxyType({
    "ui.render": frozenset({"render"}),
    "ui.navigation": frozenset({"navigate"}),
    "storage.kv": frozenset({"read", "write", "delete"}),
    "storage.files": frozenset({"read", "write", "delete"}),
    "network.fetch": frozenset({"fetch"}),
    "events.subscribe": frozenset({"subscribe", "publish"}),
    "platform.commands": frozenset({"execute"}),
    "platform.queries": frozenset({"query"}),
    "secrets.read": frozenset({"read"}),
    "identity.read": frozenset({"read"}),
})


def schema_identity() -> Mapping[str, str]:
    """Identity of the approved mapping grammar; every evidence digest is bound to it."""
    return MappingProxyType({
        "schema_version": SCHEMA_VERSION,
        "operation_map_version": OPERATION_MAP_VERSION,
        "method_mapping_version": METHOD_MAPPING_VERSION,
    })


def schema_identity_digest() -> str:
    return digest(schema_identity())


class AdapterOutcome(StrEnum):
    MAPPED_EVIDENCE = "mapped_evidence"
    KNOWN_DENY = "known_deny"
    QUARANTINED = "quarantined"
    CONFLICT = "conflict"
    BLOCKED = "blocked"


class MappingOutcome(StrEnum):
    RECORDED = "recorded"
    REPLAY = "replay"
    CONFLICT = "conflict"


@dataclass(frozen=True, slots=True)
class AuthenticatedGrantContext:
    """Server-authenticated principal and transaction-local RLS context."""

    tenant_id: str | None
    company_id: str | None
    subject_kind: str | None
    subject_id: str | None
    session_id: str | None
    cabinet_id: str | None
    project_id: str | None
    authenticated: bool
    correlation_id: str
    transaction_id: str | None = None
    rls_context: RlsContext | None = None
    plugin_instance_id: str | None = None
    runtime_generation: int | None = None
    writer_generation: int | None = None
    app_definition_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.authenticated, bool):
            raise ValueError("authenticated must be an explicit boolean")
        if not self.correlation_id.strip():
            raise ValueError("correlation_id is required")
        for value in (self.tenant_id, self.subject_kind, self.subject_id, self.session_id,
                      self.cabinet_id, self.project_id, self.correlation_id,
                      self.transaction_id, self.plugin_instance_id, self.app_definition_id):
            if value is not None and not value.strip():
                raise ValueError("authenticated identity fields cannot be blank")
        for value in (self.runtime_generation, self.writer_generation):
            if value is not None and (not isinstance(value, int) or isinstance(value, bool) or value <= 0):
                raise ValueError("runtime and writer generations must be positive integers")
        fenced = (self.plugin_instance_id, self.runtime_generation, self.writer_generation,
                  self.app_definition_id)
        if any(value is not None for value in fenced) and not all(value is not None for value in fenced):
            raise ValueError("app definition, M05 instance and runtime/writer generations must be bound together")


@dataclass(frozen=True, slots=True)
class DependencyGates:
    m04_scope_digest: str | None = None
    m04_rls_digest: str | None = None
    m04e_definition_version_digest: str | None = None
    m05_instance_digest: str | None = None
    m06_schema_digest: str | None = None
    trusted_writer_epoch: int | None = None
    m04_scope_accepted: bool = False
    m04_rls_accepted: bool = False
    m04e_definition_version_accepted: bool = False
    m05_instance_runtime_accepted: bool = False
    atomic_evidence_accepted: bool = False
    m04_scope_evidence_id: str | None = None
    m04_rls_evidence_id: str | None = None
    m04e_definition_version_evidence_id: str | None = None
    m05_instance_runtime_evidence_id: str | None = None
    atomic_evidence_id: str | None = None
    request_identity_digest: str | None = None
    m06_schema_accepted: bool = False
    m06_schema_evidence_id: str | None = None
    m06_schema_evidence_digest: str | None = None
    tenant_id: str | None = None
    app_definition_id: str | None = None
    app_version_id: str | None = None
    scope_kind: str | None = None
    scope_id: str | None = None
    plugin_instance_id: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    runtime_generation: int | None = None
    writer_generation: int | None = None
    transaction_id: str | None = None
    correlation_id: str | None = None
    source_digest: str | None = None
    final_delta_checkpoint: str | None = None
    installation_scope_kind: str | None = None
    installation_scope_id: str | None = None
    company_id: str | None = None
    app_id: str | None = None
    manifest_version: str | None = None
    subject_kind: str | None = None
    cabinet_id: str | None = None
    project_id: str | None = None

    def __post_init__(self) -> None:
        if any(not isinstance(value, bool) for value in (
            self.m04_scope_accepted, self.m04_rls_accepted, self.m04e_definition_version_accepted,
            self.m05_instance_runtime_accepted, self.atomic_evidence_accepted,
            self.m06_schema_accepted,
        )):
            raise ValueError("handoff acceptance markers must be explicit booleans")
        if self.trusted_writer_epoch is not None and (
            not isinstance(self.trusted_writer_epoch, int)
            or isinstance(self.trusted_writer_epoch, bool)
            or self.trusted_writer_epoch <= 0
        ):
            raise ValueError("trusted writer epoch must be positive")
        for value in (self.runtime_generation, self.writer_generation):
            if value is not None and (
                not isinstance(value, int) or isinstance(value, bool) or value <= 0
            ):
                raise ValueError("runtime and writer generations must be positive integers")

    @property
    def ready(self) -> bool:
        return bool(
            all(is_sha256(value) for value in (
                self.m04_scope_digest, self.m04_rls_digest, self.m04e_definition_version_digest,
                self.m05_instance_digest, self.m06_schema_digest,
            ))
            and self.trusted_writer_epoch is not None and self.trusted_writer_epoch > 0
        )

    @property
    def upstream_ready(self) -> bool:
        """Accepted M04/M04E/M05/M06 partition handoffs exist, without an identity claim.

        Identity resolution itself depends on upstream readiness, so this marker must
        not require the resolved request identity. Every positive projection check
        additionally requires :attr:`positive_projection_ready`.
        """
        return bool(
            self.ready
            and self.m04_scope_accepted is True
            and self.m04_rls_accepted is True
            and self.m04e_definition_version_accepted is True
            and self.m05_instance_runtime_accepted is True
            and self.atomic_evidence_accepted is True
            and self.m06_schema_accepted is True
            and isinstance(self.m06_schema_evidence_id, str) and self.m06_schema_evidence_id.strip()
            and is_sha256(self.m06_schema_evidence_digest)
            and all(isinstance(value, str) and value.strip() for value in (
                self.m04_scope_evidence_id, self.m04_rls_evidence_id,
                self.m04e_definition_version_evidence_id,
                self.m05_instance_runtime_evidence_id, self.atomic_evidence_id,
            ))
            and self.writer_generation is not None and self.writer_generation > 0
            and self.trusted_writer_epoch == self.writer_generation
            and self.m06_schema_digest == schema_identity_digest()
        )

    @property
    def positive_projection_ready(self) -> bool:
        """Upstream handoffs plus one exactly attested request identity."""
        return bool(
            self.upstream_ready
            and self.runtime_generation is not None and self.runtime_generation > 0
            and is_sha256(self.source_digest)
            and is_sha256(self.request_identity_digest)
            and gates_request_identity_digest(self) == self.request_identity_digest
        )

    @property
    def missing_markers(self) -> tuple[str, ...]:
        missing = []
        if not self.m04_scope_accepted:
            missing.append("m04_scope_acceptance")
        if not self.m04_rls_accepted:
            missing.append("m04_rls_acceptance")
        if not self.m04e_definition_version_accepted:
            missing.append("m04e_definition_version_acceptance")
        if not self.m05_instance_runtime_accepted:
            missing.append("m05_instance_runtime_acceptance")
        if not self.atomic_evidence_accepted:
            missing.append("atomic_evidence_acceptance")
        if not self.m06_schema_accepted:
            missing.append("m06_schema_acceptance")
        if not isinstance(self.m06_schema_evidence_id, str) or not self.m06_schema_evidence_id.strip():
            missing.append("m06_schema_evidence_id")
        if not is_sha256(self.m06_schema_evidence_digest):
            missing.append("m06_schema_evidence_digest")
        for value, label in (
            (self.m04_scope_evidence_id, "m04_scope_evidence_id"),
            (self.m04_rls_evidence_id, "m04_rls_evidence_id"),
            (self.m04e_definition_version_evidence_id, "m04e_definition_version_evidence_id"),
            (self.m05_instance_runtime_evidence_id, "m05_instance_runtime_evidence_id"),
            (self.atomic_evidence_id, "atomic_evidence_id"),
        ):
            if not isinstance(value, str) or not value.strip():
                missing.append(label)
        for value, label in (
            (self.m04_scope_digest, "m04_scope_digest"),
            (self.m04_rls_digest, "m04_rls_digest"),
            (self.m04e_definition_version_digest, "m04e_definition_version_digest"),
            (self.m05_instance_digest, "m05_instance_digest"),
            (self.m06_schema_digest, "m06_schema_digest"),
        ):
            if not is_sha256(value):
                missing.append(label)
        if gates_request_identity_digest(self) is None:
            missing.append("exact_request_identity")
        if not is_sha256(self.request_identity_digest):
            missing.append("request_identity_digest")
        elif gates_request_identity_digest(self) != self.request_identity_digest:
            missing.append("request_identity_digest_mismatch")
        if self.runtime_generation is None or self.runtime_generation <= 0:
            missing.append("runtime_generation")
        if self.writer_generation is None or self.writer_generation <= 0:
            missing.append("writer_generation")
        if self.trusted_writer_epoch != self.writer_generation:
            missing.append("trusted_writer_epoch_mismatch")
        if not is_sha256(self.source_digest):
            missing.append("source_digest")
        if is_sha256(self.m06_schema_digest) and self.m06_schema_digest != schema_identity_digest():
            missing.append("m06_schema_version_mismatch")
        return tuple(dict.fromkeys(missing))


REQUEST_IDENTITY_FIELDS = (
    "tenant_id", "company_id", "app_definition_id", "app_id", "app_version_id",
    "manifest_version", "scope_kind", "scope_id", "installation_scope_kind",
    "installation_scope_id", "cabinet_id", "project_id", "subject_kind", "subject_id",
    "session_id", "plugin_instance_id", "runtime_generation", "writer_generation",
)
_OPTIONAL_IDENTITY_FIELDS = frozenset({"cabinet_id", "project_id"})


def _identity_mapping_payload(values: Mapping[str, Any]) -> Mapping[str, Any] | None:
    payload: dict[str, Any] = {}
    for name in REQUEST_IDENTITY_FIELDS:
        value = values.get(name)
        if isinstance(value, int) and not isinstance(value, bool):
            if value <= 0:
                return None
        elif value is None and name in _OPTIONAL_IDENTITY_FIELDS:
            pass
        elif not (isinstance(value, str) and value.strip()):
            return None
        payload[name] = value
    return MappingProxyType(payload)


def _request_identity_payload(source: object) -> Mapping[str, Any] | None:
    """Exact attested request identity; ``None`` when any anchor is absent.

    The full per-call binding digest cannot be pre-accepted partition evidence, so
    handoff gates attest exactly the fields that trusted resolution can re-derive
    before the call: tenant/company, definition/version, anchors, subject/session,
    exact instance and runtime/writer generations. Transaction, correlation and
    artifact digest are compared separately against resolved server state.
    """
    return _identity_mapping_payload({name: getattr(source, name, None)
                                      for name in REQUEST_IDENTITY_FIELDS})


def request_identity_digest_from_mapping(values: Mapping[str, Any]) -> str | None:
    """Digest of an explicit request-identity mapping, or ``None`` if incomplete."""
    payload = _identity_mapping_payload(values)
    return None if payload is None else digest(payload)


def gates_request_identity_digest(gates: "DependencyGates") -> str | None:
    payload = _request_identity_payload(gates)
    return None if payload is None else digest(payload)


def request_identity_digest(identity: "ResolvedGrantIdentity") -> str:
    payload = _request_identity_payload(identity)
    if payload is None:
        raise IdentityResolutionError("resolved_request_identity_incomplete")
    return digest(payload)


def source_payload_digest(source: "LegacyGrantSource") -> str:
    return digest({"source_kind": source.source_kind, "source_id": source.source_id,
                   "schema": source.schema_version, "app_id": source.app_id,
                   "manifest_version": source.manifest_version, "grants": source.grants,
                   "asserted_company_id": source.asserted_company_id,
                   "app_definition_id": source.app_definition_id,
                   "app_version_id": source.app_version_id,
                   "issued_at": source.issued_at.isoformat(),
                   "expires_at": source.expires_at.isoformat() if source.expires_at else None,
                   "asserted_scope_kind": source.asserted_scope_kind,
                   "asserted_scope_id": source.asserted_scope_id})


@dataclass(frozen=True, slots=True)
class LegacyGrantSource:
    source_kind: str
    source_id: str
    schema_version: str
    app_id: str
    manifest_version: str
    grants: tuple[Mapping[str, Any], ...]
    issued_at: datetime
    expires_at: datetime | None = None
    asserted_company_id: str | None = None
    asserted_scope_kind: str | None = None
    asserted_scope_id: str | None = None
    app_definition_id: str | None = None
    app_version_id: str | None = None

    @classmethod
    def from_legacy_app_grant(
        cls, grant: Any, *, app_id: str, manifest_version: str,
        source_kind: str = "legacy_app_grant",
    ) -> "LegacyGrantSource":
        """Snapshot the real AppGrant/CapabilityGrant domain shape without ORM access."""
        scope = getattr(grant, "scope", None)
        raw_entries = getattr(grant, "grants", None)
        if scope is None or not isinstance(raw_entries, (tuple, list)):
            raise ValueError("legacy AppGrant scope and grant tuple are required")
        scope_kind = getattr(getattr(scope, "kind", None), "value", getattr(scope, "kind", None))
        scope_id = getattr(scope, "id", None)
        entries: list[Mapping[str, Any]] = []
        for item in raw_entries:
            if isinstance(item, Mapping):
                entries.append(dict(item))
                continue
            dump = getattr(item, "model_dump", None)
            if not callable(dump):
                raise ValueError("legacy CapabilityGrant must expose a typed JSON snapshot")
            value = dump(mode="json")
            if not isinstance(value, Mapping):
                raise ValueError("legacy CapabilityGrant snapshot must be an object")
            entries.append(dict(value))
        return cls(
            source_kind=source_kind,
            source_id=str(getattr(grant, "id", "")),
            schema_version="grantset-v1",
            app_id=app_id,
            manifest_version=manifest_version,
            grants=tuple(entries),
            issued_at=getattr(grant, "issued_at", None),
            expires_at=getattr(grant, "expires_at", None),
            asserted_scope_kind=scope_kind,
            asserted_scope_id=scope_id,
            app_definition_id=getattr(grant, "app_definition_id", None),
            app_version_id=getattr(grant, "app_version_id", None),
        )

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.source_kind, self.source_id, self.schema_version, self.app_id, self.manifest_version)):
            raise ValueError("source identity and app/version are required")
        _aware(self.issued_at)
        _aware(self.expires_at)
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")
        if not isinstance(self.grants, tuple):
            raise ValueError("grants must be an immutable tuple")
        object.__setattr__(self, "grants", tuple(_freeze_json(value) for value in self.grants))
        _canonical_json(self.grants)
        if self.schema_version != "grantset-v1":
            raise ValueError("unsupported source schema version")


@dataclass(frozen=True, slots=True)
class ResolvedGrantIdentity:
    tenant_id: str
    company_id: str
    app_definition_id: str
    app_id: str
    app_version_id: str
    manifest_version: str
    artifact_digest: str
    scope_kind: str
    scope_id: str
    installation_scope_kind: str
    installation_scope_id: str
    cabinet_id: str | None
    project_id: str | None
    subject_kind: str
    subject_id: str
    session_id: str
    plugin_instance_id: str
    runtime_generation: int
    writer_generation: int
    transaction_id: str
    correlation_id: str
    rls_evidence_digest: str
    owner_evidence_digest: str | None = None
    definition_evidence_digest: str | None = None
    instance_evidence_digest: str | None = None
    requested_capabilities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        required = (
            self.tenant_id, self.company_id, self.app_definition_id, self.app_id, self.app_version_id,
            self.manifest_version, self.scope_kind, self.scope_id,
            self.installation_scope_kind, self.installation_scope_id,
            self.subject_kind, self.subject_id, self.session_id, self.plugin_instance_id,
            self.transaction_id, self.correlation_id,
        )
        if any(not isinstance(value, str) or not value.strip() for value in required):
            raise ValueError("resolved identity snapshot fields are required")
        if not isinstance(self.requested_capabilities, frozenset) or any(
            not isinstance(value, str) or not value.strip() for value in self.requested_capabilities
        ):
            raise ValueError("requested_capabilities must be a frozen set of exact keys")
        if self.scope_kind != "plugin_instance" or self.scope_id != self.plugin_instance_id:
            raise ValueError("runtime identity must bind exact plugin-instance scope")
        if self.installation_scope_kind not in {"tenant", "cabinet", "project"}:
            raise ValueError("installation scope must be tenant, cabinet or project")
        if self.installation_scope_kind == "tenant" and (
            self.installation_scope_id != self.tenant_id or self.cabinet_id is not None or self.project_id is not None
        ):
            raise ValueError("tenant installation scope anchors are inconsistent")
        if self.installation_scope_kind == "cabinet" and (
            self.installation_scope_id != self.cabinet_id or self.cabinet_id is None or self.project_id is not None
        ):
            raise ValueError("cabinet installation scope anchors are inconsistent")
        if self.installation_scope_kind == "project" and (
            self.installation_scope_id != self.project_id or self.cabinet_id is None or self.project_id is None
        ):
            raise ValueError("project installation scope anchors are inconsistent")
        if self.runtime_generation <= 0 or self.writer_generation <= 0:
            raise ValueError("runtime and writer generations must be positive")
        if not is_sha256(self.artifact_digest):
            raise ValueError("artifact_digest must be sha256")
        if not is_sha256(self.rls_evidence_digest):
            raise ValueError("RLS evidence digest must be sha256")
        evidence_values = (
            self.owner_evidence_digest, self.definition_evidence_digest,
            self.instance_evidence_digest,
        )
        if any(value is not None for value in evidence_values):
            if not all(is_sha256(value) for value in evidence_values):
                raise ValueError("identity handoff evidence must be complete sha256 digests")


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    allowed: bool
    reason_code: str
    binding_digest: str

    def __post_init__(self) -> None:
        if not isinstance(self.allowed, bool) or not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("shadow decision requires an explicit outcome and reason")
        positive_reasons = {"allowed", "allow", "granted"}
        if (self.allowed and self.reason_code.strip().lower() not in positive_reasons
                or not self.allowed and self.reason_code.strip().lower() in positive_reasons):
            raise ValueError("shadow decision outcome and reason must agree")
        if not is_sha256(self.binding_digest):
            raise ValueError("shadow decision binding must be sha256")


@dataclass(frozen=True, slots=True)
class ShadowObservation:
    tenant_id: str
    app_definition_id: str
    source_id: str
    capability_key: str
    source_digest: str
    canonical_digest: str | None
    binding_digest: str | None
    legacy_decision: ShadowDecision
    canonical_decision: ShadowDecision
    mismatch: bool
    mismatch_explained: bool
    checkpoint: str
    correlation_id: str
    observed_at: str
    operation_mapping_digest: str = ""
    mismatch_explanation: str | None = None

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.tenant_id, self.app_definition_id, self.source_id,
            self.capability_key, self.checkpoint, self.correlation_id,
        )):
            raise ValueError("shadow observation identity is required")
        if not is_sha256(self.source_digest):
            raise ValueError("source_digest must be sha256")
        if self.canonical_digest is not None and not is_sha256(self.canonical_digest):
            raise ValueError("canonical_digest must be sha256")
        if self.binding_digest is not None and not is_sha256(self.binding_digest):
            raise ValueError("binding_digest must be sha256")
        if not is_sha256(self.operation_mapping_digest):
            raise ValueError("approved operation mapping digest is required")
        if not isinstance(self.mismatch, bool) or not isinstance(self.mismatch_explained, bool):
            raise ValueError("shadow mismatch markers must be explicit booleans")
        if self.mismatch_explained and not self.mismatch:
            raise ValueError("an explained observation must still record its mismatch")
        if self.mismatch and not self.mismatch_explained and self.mismatch_explanation is not None:
            raise ValueError("unexplained mismatch cannot carry an explanation code")
        if self.mismatch_explained and not (
            isinstance(self.mismatch_explanation, str) and self.mismatch_explanation.strip()
        ):
            raise ValueError("explained mismatch requires a stable explanation code")
        observed_at = datetime.fromisoformat(self.observed_at)
        _aware(observed_at)


@dataclass(frozen=True, slots=True)
class SourceMappingRecord:
    source_kind: str
    source_id: str
    schema_version: str
    tenant_id: str | None
    source_digest: str
    canonical_digest: str | None
    binding_digest: str | None
    outcome: AdapterOutcome
    reason_code: str | None
    checkpoint: str
    correlation_id: str

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (
            self.source_kind, self.source_id, self.schema_version, self.checkpoint,
            self.correlation_id,
        )):
            raise ValueError("source mapping identity and checkpoint are required")
        if self.tenant_id is not None and not self.tenant_id.strip():
            raise ValueError("tenant_id cannot be empty")
        if not is_sha256(self.source_digest):
            raise ValueError("source_digest must be sha256")
        if self.canonical_digest is not None and not is_sha256(self.canonical_digest):
            raise ValueError("canonical_digest must be sha256")
        if self.binding_digest is not None and not is_sha256(self.binding_digest):
            raise ValueError("binding_digest must be sha256")


class DependencyHandoffPort(Protocol):
    async def resolve_gates(self, *, context: AuthenticatedGrantContext,
                            source: LegacyGrantSource, source_digest: str,
                            now: datetime) -> DependencyGates | None:
        """Load immutable M04/M04E/M05/M06 handoff evidence for this exact source binding."""
        ...


class IdentityResolutionPort(Protocol):
    async def resolve(self, *, context: AuthenticatedGrantContext, app_id: str,
                      manifest_version: str, source: LegacyGrantSource,
                      now: datetime | None = None,
                      dependency_gates: DependencyGates) -> ResolvedGrantIdentity:
        """Compose exact M04 scope, M04E version and M05 instance/runtime ports.

        The binding is server-derived and transaction/RLS-bound. It rejects missing
        or ambiguous tenant, scope, definition, version, instance, subject/session,
        artifact or generation evidence; source fields remain assertions only.
        """
        ...


class LegacyDecisionPort(Protocol):
    async def decide(self, *, identity: ResolvedGrantIdentity, capability: str,
                     operation: str, resource: str, domain: str | None, now: datetime,
                     method: str | None = None, requested_bytes: int | None = None,
                     params: Mapping[str, Any] | None = None,
                     constraints: Mapping[str, Any] | None = None) -> ShadowDecision: ...


class CanonicalShadowDecisionPort(Protocol):
    """Read-only canonical decision port; it must not issue/revoke/mutate grants."""

    async def decide_shadow(self, *, identity: ResolvedGrantIdentity, capability: str,
                            operation: str, resource: str, domain: str | None,
                            constraints: Mapping[str, Any], now: datetime) -> ShadowDecision: ...


@dataclass(frozen=True, slots=True)
class EvidenceTransaction:
    """Complete evidence unit; a trusted store commits all members or none."""

    mapping: SourceMappingRecord | None
    quarantine: Mapping[str, Any] | None
    shadow: ShadowObservation | None
    audit: Mapping[str, Any]
    conflict_mapping: SourceMappingRecord | None = None
    conflict_quarantine: Mapping[str, Any] | None = None
    conflict_audit: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.audit, Mapping) or not self.audit:
            raise ValueError("atomic evidence requires a redacted audit event")
        if self.mapping is None and self.quarantine is None and self.shadow is None:
            raise ValueError("atomic evidence transaction cannot be empty")
        if self.mapping is not None and self.mapping.outcome is AdapterOutcome.CONFLICT and self.quarantine is None:
            raise ValueError("source conflict requires quarantine evidence")
        if self.conflict_mapping is not None:
            if self.conflict_mapping.outcome is not AdapterOutcome.CONFLICT:
                raise ValueError("concurrent source-key conflict evidence is invalid")
            if not self.conflict_quarantine or not self.conflict_audit:
                raise ValueError("concurrent source-key conflict requires complete alternative evidence")
            if self.mapping is not None and (
                (self.conflict_mapping.source_kind, self.conflict_mapping.source_id,
                 self.conflict_mapping.schema_version)
                != (self.mapping.source_kind, self.mapping.source_id, self.mapping.schema_version)
            ):
                raise ValueError("conflict fallback must preserve the proposed source key")
        if self.mapping is not None and self.shadow is not None and (
            self.mapping.tenant_id != self.shadow.tenant_id
            or self.mapping.source_id != self.shadow.source_id
            or self.mapping.source_digest != self.shadow.source_digest
            or self.mapping.binding_digest != self.shadow.binding_digest
            or self.mapping.canonical_digest != self.shadow.canonical_digest
        ):
            raise ValueError("mapping and shadow evidence identities must match")
        object.__setattr__(self, "audit", _freeze_json(self.audit))
        if _contains_sensitive_key(self.audit):
            raise ValueError("raw arguments and secrets are forbidden in audit evidence")
        if self.quarantine is not None:
            object.__setattr__(self, "quarantine", _freeze_json(self.quarantine))
            if _contains_sensitive_key(self.quarantine):
                raise ValueError("raw arguments and secrets are forbidden in quarantine evidence")
        if self.conflict_quarantine is not None:
            object.__setattr__(self, "conflict_quarantine", _freeze_json(self.conflict_quarantine))
            if _contains_sensitive_key(self.conflict_quarantine):
                raise ValueError("raw arguments and secrets are forbidden in conflict evidence")
        if self.conflict_audit is not None:
            object.__setattr__(self, "conflict_audit", _freeze_json(self.conflict_audit))
            if _contains_sensitive_key(self.conflict_audit):
                raise ValueError("raw arguments and secrets are forbidden in conflict audit")


class EvidenceReadPort(Protocol):
    async def find_mapping(self, *, context: AuthenticatedGrantContext, source_kind: str,
                           source_id: str, schema_version: str) -> SourceMappingRecord | None:
        """Find the globally unique source key so cross-tenant replay is visible."""


class TrustedEvidenceTransactionPort(Protocol):
    """Trusted durable contract; one call appends all supplied evidence or none.

    Conflict outcomes must preserve the first immutable mapping and durably
    append redacted conflict/quarantine plus audit. Exact replay may append new
    shadow/audit evidence but must not rewrite mapping identity. On a unique-key
    race, atomically commit the conflict_mapping/quarantine/audit alternative
    rather than the proposed mapping and shadow.
    """

    async def commit_evidence(self, *, context: AuthenticatedGrantContext,
                              transaction: EvidenceTransaction) -> MappingOutcome: ...


@dataclass(frozen=True, slots=True)
class GrantMapping:
    capability_key: str
    decision: str
    operations: tuple[str, ...]
    resources: tuple[str, ...]
    domains: tuple[str, ...]
    constraints: Mapping[str, Any]
    issued_by: str
    expires_at: datetime | None
    payload_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "constraints", _freeze_json(self.constraints))


class GrantSetAdapter:
    """Map only bounded source semantics that the canonical decision can enforce."""

    _ENTRY_KEYS = frozenset({
        "name", "decision", "constraints", "issued_by", "granted_by", "grantedBy",
        "expires_at", "expiresAt",
    })
    _CONSTRAINT_KEYS = frozenset({"operations", "resources", "domains"})

    def adapt(self, entry: Mapping[str, Any]) -> GrantMapping:
        if set(entry) - self._ENTRY_KEYS:
            raise MappingError("grant_field_unsupported")
        name = entry.get("name")
        decision = entry.get("decision")
        constraints = entry.get("constraints")
        if not isinstance(name, str) or not name.strip():
            raise MappingError("capability_identity_missing")
        if name not in APPROVED_CAPABILITIES:
            raise MappingError("capability_unknown")
        if decision not in {"granted", "denied"}:
            raise MappingError("unsupported_decision")
        issuer_values = [entry[key] for key in ("issued_by", "granted_by", "grantedBy") if key in entry]
        if len(issuer_values) != 1 or not isinstance(issuer_values[0], str) or not issuer_values[0].strip():
            raise MappingError("grant_issuer_missing_or_ambiguous")
        expiry_keys = [key for key in ("expires_at", "expiresAt") if key in entry]
        if len(expiry_keys) > 1:
            raise MappingError("grant_expiry_ambiguous")
        expires_at = _parse_timestamp(entry[expiry_keys[0]], "expires_at") if expiry_keys else None
        if constraints is None:
            normalized = {
                "capability_key": name, "decision": decision, "operations": [],
                "resources": [], "domains": [], "constraints": None,
                "issued_by": issuer_values[0],
                "expires_at": expires_at.isoformat() if expires_at else None,
                "mapping_version": OPERATION_MAP_VERSION,
            }
            return GrantMapping(name, decision, (), (), (), {}, issuer_values[0],
                                expires_at, digest(normalized))
        if not isinstance(constraints, Mapping):
            raise MappingError("constraints_malformed")
        if set(constraints) - self._CONSTRAINT_KEYS:
            # Unsupported source semantics cannot be kept as decorative metadata.
            raise MappingError("constraint_unsupported")
        raw_operations = constraints.get("operations")
        if not isinstance(raw_operations, (list, tuple)) or not raw_operations or any(
            not isinstance(item, str) or not item.strip() for item in raw_operations
        ):
            raise MappingError("constraint_incomplete")
        if len(set(raw_operations)) != len(raw_operations):
            raise MappingError("constraint_duplicate")
        if any(item not in CAPABILITY_OPERATIONS[name] for item in raw_operations):
            raise MappingError("operation_unapproved")
        operations = tuple(sorted(raw_operations))
        resources = constraints.get("resources")
        domains = constraints.get("domains")
        if (not isinstance(resources, (list, tuple)) or not resources
                or not isinstance(domains, (list, tuple)) or not domains
                or any(not isinstance(item, str) or not item.strip() for item in (*resources, *domains))):
            raise MappingError("constraint_incomplete")
        if len(set(resources)) != len(resources) or len(set(domains)) != len(domains):
            raise MappingError("constraint_duplicate")
        if any(_has_wildcard(item) for item in (*operations, *resources, *domains)):
            raise MappingError("wildcard_grant_unrepresentable")
        if not set(operations).issubset(CAPABILITY_OPERATIONS[name]):
            raise MappingError("method_operation_unapproved")
        normalized_constraints = {
            "domains": sorted(domains), "operations": list(operations),
            "resources": sorted(resources),
        }
        normalized = {
            "capability_key": name, "decision": decision,
            "operations": list(operations), "resources": sorted(resources),
            "domains": sorted(domains), "constraints": normalized_constraints,
            "issued_by": issuer_values[0],
            "expires_at": expires_at.isoformat() if expires_at else None,
            "mapping_version": OPERATION_MAP_VERSION,
        }
        return GrantMapping(
            name, decision, operations,
            tuple(normalized["resources"]), tuple(normalized["domains"]),
            {}, issuer_values[0], expires_at, digest(normalized),
        )


class MethodOperationPort(Protocol):
    """Approved method -> operation registry; never an authorization decision."""

    async def resolve(self, *, identity: ResolvedGrantIdentity, method: str, capability: str,
                      operation: str, resource: str, domain: str | None
                      ) -> "MethodOperationResolution": ...


class CapabilityGatewayService:
    """Shadow facade; returns the legacy answer and only appends target evidence."""

    DECISION_OWNER = "legacy"
    CANONICAL_READ_OWNER = "legacy"
    CANONICAL_POSITIVE_WRITES = False

    def __init__(self, *, identity: IdentityResolutionPort, legacy: LegacyDecisionPort,
                 canonical_shadow: CanonicalShadowDecisionPort, handoffs: DependencyHandoffPort,
                 evidence_read: EvidenceReadPort,
                 evidence_transaction: TrustedEvidenceTransactionPort,
                 method_mapping: MethodOperationPort,
                 page_size: int = 100) -> None:
        if not 1 <= page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        self._identity, self._legacy, self._canonical = identity, legacy, canonical_shadow
        self._handoffs = handoffs
        self._evidence_read = evidence_read
        self._evidence_transaction, self._page_size = evidence_transaction, page_size
        self._method_mapping = method_mapping

    async def observe(self, *, source: LegacyGrantSource, context: AuthenticatedGrantContext,
                      capability: str, operation: str, resource: str, domain: str | None,
                      now: datetime | None = None, checkpoint: str,
                      constraints: Mapping[str, Any] | None = None,
                      params: Mapping[str, Any] | None = None,
                      method: str | None = None, requested_bytes: int | None = None) -> ShadowObservation | None:
        checked_at = now or datetime.now(UTC)
        _aware(checked_at)
        if not checkpoint.strip():
            raise ValueError("checkpoint is required")
        if not capability.strip() or not operation.strip() or not resource.strip():
            raise ValueError("capability, operation and resource are required")
        if domain is not None and not domain.strip():
            raise ValueError("domain must be non-empty when supplied")
        if not isinstance(method, str) or not method.strip():
            raise ValueError("method is required for exact legacy decision parity")
        if requested_bytes is not None and (not isinstance(requested_bytes, int)
                or isinstance(requested_bytes, bool) or requested_bytes < 0):
            raise ValueError("requested_bytes must be a non-negative integer")
        if constraints is not None and not isinstance(constraints, Mapping):
            raise ValueError("request constraints must be a mapping")
        if params is not None and not isinstance(params, Mapping):
            raise ValueError("request params must be a mapping")
        request_constraints = _freeze_json(constraints or {})
        request_params = _freeze_json(params or {})
        _canonical_json(request_constraints)
        _canonical_json(request_params)
        source_digest = source_payload_digest(source)
        if (not context.authenticated or not context.tenant_id or not context.company_id
                or not context.subject_id or not context.session_id or not context.transaction_id
                or not context.correlation_id.strip() or context.rls_context is None
                or context.rls_context.tenant_id != context.tenant_id
                or context.rls_context.user_id != context.subject_id
                or context.rls_context.cabinet_id != context.cabinet_id
                or context.rls_context.project_id != context.project_id):
            return None
        try:
            gates = await self._handoffs.resolve_gates(
                context=context, source=source, source_digest=source_digest, now=checked_at)
        except Exception:
            return None
        if (gates is None or not self._handoff_matches(gates, context, source, source_digest)
                or not gates.upstream_ready):
            return None
        try:
            prior = await self._evidence_read.find_mapping(
                context=context, source_kind=source.source_kind, source_id=source.source_id,
                schema_version=SCHEMA_VERSION)
        except Exception:
            return None
        try:
            identity = await self._identity.resolve(
                context=context, app_id=source.app_id, manifest_version=source.manifest_version,
                source=source, now=checked_at, dependency_gates=gates)
        except Exception:
            return None
        if not isinstance(identity, ResolvedGrantIdentity) or not self._identity_matches(
            identity, context, source
        ):
            return None
        exact_binding = binding_digest(identity)
        if (not gates.positive_projection_ready
                or identity.writer_generation != gates.writer_generation
                or identity.runtime_generation != gates.runtime_generation
                or request_identity_digest(identity) != gates.request_identity_digest
                or identity.transaction_id != context.transaction_id
                or not is_sha256(identity.owner_evidence_digest)
                or not is_sha256(identity.rls_evidence_digest)
                or not is_sha256(identity.definition_evidence_digest)
                or not is_sha256(identity.instance_evidence_digest)):
            return None
        if prior is not None and prior.tenant_id is None:
            unresolved = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                             identity.tenant_id, source_digest, None, exact_binding,
                                             AdapterOutcome.CONFLICT, "prior_mapping_tenant_unresolved",
                                             checkpoint, context.correlation_id)
            await self._commit_evidence(unresolved, context, "prior_mapping_tenant_unresolved",
                                         {"source_digest": source_digest})
            return None
        if prior is not None and prior.tenant_id != identity.tenant_id:
            conflict = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                           identity.tenant_id, source_digest, None, exact_binding,
                                           AdapterOutcome.CONFLICT, "cross_tenant_replay", checkpoint,
                                           context.correlation_id)
            await self._commit_evidence(conflict, context, "cross_tenant_replay",
                                         {"source_digest": source_digest,
                                          "prior_tenant_digest": digest(prior.tenant_id or "")})
            return None
        if prior is not None and prior.source_digest != source_digest:
            conflict = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                           identity.tenant_id, source_digest, None, prior.binding_digest,
                                           AdapterOutcome.CONFLICT, "source_digest_changed", checkpoint,
                                           context.correlation_id)
            await self._commit_evidence(conflict, context, "source_digest_changed",
                                         {"source_digest": source_digest})
            return None
        if checked_at < source.issued_at:
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                         identity.tenant_id, source_digest, None, exact_binding,
                                         AdapterOutcome.QUARANTINED, "source_grant_future", checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(record, context, "source_grant_future",
                                         {"checked_at": checked_at.isoformat(),
                                          "observed_at": checked_at.isoformat()})
            return None
        if prior is not None and prior.binding_digest is None:
            incomplete = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                             identity.tenant_id, source_digest, None, exact_binding,
                                             AdapterOutcome.CONFLICT, "prior_mapping_evidence_incomplete",
                                             checkpoint, context.correlation_id)
            await self._commit_evidence(incomplete, context, "prior_mapping_evidence_incomplete",
                                        {"source_digest": source_digest})
            return None
        if prior is not None and prior.binding_digest != exact_binding:
            conflict = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                           identity.tenant_id, source_digest, None, exact_binding,
                                           AdapterOutcome.CONFLICT, "source_binding_changed", checkpoint,
                                           context.correlation_id)
            await self._commit_evidence(conflict, context, "source_binding_changed",
                                         {"source_digest": source_digest})
            return None
        parser = GrantSetAdapter()
        try:
            if not source.grants or any(not isinstance(entry, Mapping) for entry in source.grants):
                raise MappingError("grant_entry_malformed")
            mapped_by_capability: dict[str, GrantMapping] = {}
            for entry in source.grants:
                name = entry.get("name")
                if not isinstance(name, str) or name in mapped_by_capability:
                    raise MappingError("capability_ambiguous")
                mapped_by_capability[name] = parser.adapt(entry)
            mapped = mapped_by_capability.get(capability)
            if mapped is None:
                raise MappingError("capability_unknown")
            if (mapped.operations and (operation not in mapped.operations
                    or resource not in mapped.resources or domain not in mapped.domains)):
                raise MappingError("requested_operation_resource_or_domain_unmapped")
            canonical_source_digest = digest(tuple(
                (name, item.payload_digest) for name, item in sorted(mapped_by_capability.items())
            ))
        except (MappingError, AttributeError, TypeError, ValueError) as exc:
            reason = str(exc) or "grant_entry_malformed"
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                         identity.tenant_id, source_digest, None, exact_binding,
                                         AdapterOutcome.QUARANTINED, reason, checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(record, context, reason, {"capability_key_digest": digest(capability)})
            return None
        try:
            method_resolution = await self._method_mapping.resolve(
                identity=identity, method=method, capability=capability, operation=operation,
                resource=resource, domain=domain or "")
        except Exception:
            failed = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                         identity.tenant_id, source_digest, canonical_source_digest,
                                         exact_binding, AdapterOutcome.QUARANTINED,
                                         "method_mapping_read_failed", checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(failed, context, "method_mapping_read_failed",
                                        {"method_digest": digest(method)})
            return None
        if not isinstance(method_resolution, MethodOperationResolution):
            return None
        if not method_resolution.approved:
            record = SourceMappingRecord(
                source.source_kind, source.source_id, SCHEMA_VERSION, identity.tenant_id,
                source_digest, canonical_source_digest, exact_binding, AdapterOutcome.QUARANTINED,
                method_resolution.reason_code, checkpoint, context.correlation_id)
            await self._commit_evidence(record, context, method_resolution.reason_code,
                                        {"method_digest": digest(method),
                                         "capability_key_digest": digest(capability)})
            return None
        canonical_source_digest = digest({
            "mapping": canonical_source_digest,
            "method_mapping": method_resolution.mapping_digest,
            "schema_identity": schema_identity(),
        })
        if mapped.expires_at is not None and mapped.expires_at <= source.issued_at:
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                         identity.tenant_id, source_digest, None, exact_binding,
                                         AdapterOutcome.QUARANTINED, "grant_expiry_not_after_issue",
                                         checkpoint, context.correlation_id)
            await self._commit_evidence(record, context, "grant_expiry_not_after_issue",
                                         {"source_digest": source_digest})
            return None
        lifecycle_expired = bool(
            (source.expires_at is not None and checked_at >= source.expires_at)
            or (mapped.expires_at is not None and checked_at >= mapped.expires_at)
        )
        known_deny = mapped.decision == "denied" or lifecycle_expired
        deny_reason = ("source_grant_expired" if lifecycle_expired else
                       "protocol_denied" if mapped.decision == "denied" else None)
        outcome = AdapterOutcome.KNOWN_DENY if known_deny else AdapterOutcome.MAPPED_EVIDENCE
        record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                     identity.tenant_id, source_digest, canonical_source_digest,
                                     exact_binding, outcome, deny_reason, checkpoint,
                                     context.correlation_id)
        if prior is not None and prior.outcome in {AdapterOutcome.CONFLICT, AdapterOutcome.BLOCKED}:
            # A conflict/blocked mapping is not repairable by replay: it needs an
            # operator-reviewed forward decision, not another automatic attempt.
            await self._commit_evidence(None, context, "non_promotable_source_replay",
                                         {"prior_outcome": prior.outcome.value,
                                          "source_digest": source_digest})
            return None
        repair = prior is not None and prior.outcome is AdapterOutcome.QUARANTINED
        if prior is not None and prior.canonical_digest is not None and (
                prior.canonical_digest != canonical_source_digest):
            conflict = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                           identity.tenant_id, source_digest, canonical_source_digest,
                                           exact_binding, AdapterOutcome.CONFLICT, "mapping_digest_changed",
                                           checkpoint, context.correlation_id)
            await self._commit_evidence(conflict, context, "mapping_digest_changed",
                                         {"canonical_digest": canonical_source_digest})
            return None
        if prior is not None and not repair and prior.canonical_digest is None:
            await self._commit_evidence(None, context, "prior_mapping_digest_missing",
                                         {"source_digest": source_digest})
            return None
        try:
            legacy_decision = await self._legacy.decide(
                identity=identity, capability=capability, operation=operation,
                resource=resource, domain=domain, now=checked_at, method=method,
                requested_bytes=requested_bytes, params=request_params,
                constraints=request_constraints)
            canonical_decision = await self._canonical.decide_shadow(
                identity=identity, capability=capability, operation=operation,
                resource=resource, domain=domain, constraints=request_constraints, now=checked_at)
            observation = self._observation(identity, source, mapped, canonical_source_digest,
                                            legacy_decision, canonical_decision,
                                            source_digest, checkpoint, context.correlation_id,
                                            checked_at, method_resolution.mapping_digest)
        except Exception as exc:
            reason = str(exc) if isinstance(exc, IdentityResolutionError) else "shadow_decision_failed"
            failed = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION,
                                         identity.tenant_id, source_digest, canonical_source_digest,
                                         exact_binding, AdapterOutcome.QUARANTINED, reason, checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(failed, context, reason, {"source_digest": source_digest})
            return None
        event = "shadow_replayed" if prior is not None else (
            "shadow_mismatch" if observation.mismatch else "shadow_compared")
        race_conflict = None if prior is not None else SourceMappingRecord(
            source.source_kind, source.source_id, SCHEMA_VERSION, identity.tenant_id,
            source_digest, canonical_source_digest, exact_binding, AdapterOutcome.CONFLICT,
            "concurrent_source_key_conflict", checkpoint, context.correlation_id)
        committed = await self._commit_transaction(context=context, transaction=EvidenceTransaction(
            record if (prior is None or repair) else None,
            ({"protocol_decision": mapped.decision, "reason_code": deny_reason}
             if known_deny else None),
            observation, self._audit_event(record, context, event),
            conflict_mapping=race_conflict,
            conflict_quarantine={"reason_code": "concurrent_source_key_conflict"} if race_conflict else None,
            conflict_audit=self._audit_event(race_conflict, context, "concurrent_source_key_conflict")
            if race_conflict else None))
        if repair:
            return observation if committed is MappingOutcome.RECORDED else None
        if prior is not None:
            return observation if committed is MappingOutcome.REPLAY else None
        return observation if committed in {MappingOutcome.RECORDED, MappingOutcome.REPLAY} else None

    @staticmethod
    def _handoff_matches(gates: DependencyGates, context: AuthenticatedGrantContext,
                         source: LegacyGrantSource, source_digest: str) -> bool:
        return bool(
            gates.tenant_id == context.tenant_id
            and gates.app_definition_id == source.app_definition_id
            and gates.app_definition_id == context.app_definition_id
            and gates.app_version_id == source.app_version_id
            and gates.scope_kind == "plugin_instance"
            and gates.scope_id == context.plugin_instance_id
            and gates.installation_scope_kind == source.asserted_scope_kind
            and gates.installation_scope_id == source.asserted_scope_id
            and gates.plugin_instance_id == context.plugin_instance_id
            and gates.subject_id == context.subject_id and gates.session_id == context.session_id
            and gates.runtime_generation == context.runtime_generation
            and gates.writer_generation == context.writer_generation
            and gates.transaction_id == context.transaction_id
            and gates.correlation_id == context.correlation_id
            and gates.source_digest == source_digest
            and bool(gates.final_delta_checkpoint and gates.final_delta_checkpoint.strip())
        )

    @staticmethod
    def _observation(identity: ResolvedGrantIdentity, source: LegacyGrantSource, mapped: GrantMapping,
                     canonical_digest: str, legacy_decision: ShadowDecision,
                     canonical_decision: ShadowDecision, source_digest: str,
                     checkpoint: str, correlation_id: str, observed_at: datetime,
                     method_digest: str) -> ShadowObservation:
        expected_binding = binding_digest(identity)
        if legacy_decision.binding_digest != expected_binding or canonical_decision.binding_digest != expected_binding:
            raise IdentityResolutionError("decision_binding_digest_mismatch")
        mismatch = (legacy_decision.allowed != canonical_decision.allowed
                    or _decision_class(legacy_decision.reason_code) != _decision_class(canonical_decision.reason_code))
        explanation = _mismatch_explanation(legacy_decision.reason_code,
                                            canonical_decision.reason_code) if mismatch else None
        return ShadowObservation(
            identity.tenant_id, identity.app_definition_id, source.source_id,
            mapped.capability_key, source_digest, canonical_digest,
            expected_binding, legacy_decision, canonical_decision, mismatch,
            explanation is not None, checkpoint, correlation_id, observed_at.isoformat(),
            method_digest, explanation,
        )

    @staticmethod
    def _audit_event(record: SourceMappingRecord | None, context: AuthenticatedGrantContext,
                     event: str) -> Mapping[str, Any]:
        if record is None:
            return {"event": event, "tenant_id": context.tenant_id,
                    "correlation_id": context.correlation_id,
                    "redaction": "raw_payloads_and_secrets_excluded"}
        return {"event": event, "tenant_id": context.tenant_id,
            "source_kind": record.source_kind, "source_id_digest": digest(record.source_id),
            "schema_version": record.schema_version, "source_digest": record.source_digest,
            "canonical_digest": record.canonical_digest, "binding_digest": record.binding_digest,
            "outcome": record.outcome.value, "reason_code": record.reason_code,
            "checkpoint": record.checkpoint, "correlation_id": context.correlation_id,
            "redaction": "raw_payloads_and_secrets_excluded"}

    async def _commit_evidence(self, record: SourceMappingRecord | None, context: AuthenticatedGrantContext,
                               event: str, quarantine: Mapping[str, Any]) -> MappingOutcome | None:
        if record is not None and record.outcome in {AdapterOutcome.BLOCKED, AdapterOutcome.QUARANTINED}:
            quarantine = {**dict(quarantine), "reason_code": record.reason_code,
                          "outcome": record.outcome.value, "source_kind": record.source_kind,
                          "source_id_digest": digest(record.source_id), "source_digest": record.source_digest}
            audit = self._audit_event(record, context, event)
            return await self._commit_transaction(context=context, transaction=EvidenceTransaction(
                record, quarantine, None, audit))
        if record is not None and record.outcome is AdapterOutcome.CONFLICT:
            conflict = record
            quarantine = {**dict(quarantine), "reason_code": conflict.reason_code,
                          "outcome": conflict.outcome.value, "source_kind": conflict.source_kind,
                          "source_id_digest": digest(conflict.source_id), "source_digest": conflict.source_digest}
            return await self._commit_transaction(context=context, transaction=EvidenceTransaction(
                None, quarantine, None, self._audit_event(conflict, context, event),
                conflict_mapping=conflict, conflict_quarantine=quarantine,
                conflict_audit=self._audit_event(conflict, context, event)))
        return await self._commit_transaction(context=context, transaction=EvidenceTransaction(
            record, quarantine, None, self._audit_event(record, context, event)))

    async def _commit_transaction(self, *, context: AuthenticatedGrantContext,
                                  transaction: EvidenceTransaction) -> MappingOutcome | None:
        """Evidence failure must never become an allow or interrupt the legacy owner."""
        try:
            return await self._evidence_transaction.commit_evidence(context=context, transaction=transaction)
        except Exception:
            return None

    @staticmethod
    def _identity_matches(identity: ResolvedGrantIdentity, context: AuthenticatedGrantContext,
                          source: LegacyGrantSource) -> bool:
        return bool(identity.tenant_id and identity.company_id == context.company_id
                    and identity.app_definition_id and identity.app_version_id
                    and identity.app_id and identity.manifest_version
                    and identity.subject_kind and context.subject_kind == identity.subject_kind
                    and identity.subject_id and identity.session_id and identity.plugin_instance_id
                    and identity.runtime_generation > 0 and identity.writer_generation > 0
                    and is_sha256(identity.artifact_digest)
                    and context.plugin_instance_id == identity.plugin_instance_id
                    and context.runtime_generation == identity.runtime_generation
                    and context.writer_generation == identity.writer_generation
                    and is_sha256(identity.owner_evidence_digest)
                    and identity.scope_kind in {"tenant", "cabinet", "project", "plugin_instance"}
                    and (identity.scope_kind != "project" or (identity.cabinet_id and identity.project_id == identity.scope_id))
                    and (identity.scope_kind != "cabinet" or (identity.cabinet_id == identity.scope_id and identity.project_id is None))
                    and (identity.scope_kind != "tenant" or (identity.scope_id == identity.tenant_id and identity.cabinet_id is None and identity.project_id is None))
                    and (identity.scope_kind != "plugin_instance" or identity.scope_id == identity.plugin_instance_id)
                    and identity.app_id == source.app_id and identity.manifest_version == source.manifest_version
                    and source.app_definition_id == identity.app_definition_id
                    and context.app_definition_id == identity.app_definition_id
                    and source.app_version_id == identity.app_version_id
                    and identity.tenant_id == context.tenant_id and identity.subject_id == context.subject_id
                    and identity.transaction_id == context.transaction_id
                    and identity.correlation_id == context.correlation_id
                    and is_sha256(identity.rls_evidence_digest)
                    and identity.session_id == context.session_id
                    and identity.cabinet_id == context.cabinet_id and identity.project_id == context.project_id
                    and is_sha256(identity.definition_evidence_digest)
                    and is_sha256(identity.instance_evidence_digest)
                    and identity.installation_scope_kind in {"tenant", "cabinet", "project"}
                    and (identity.installation_scope_kind != "tenant" or (
                        identity.installation_scope_id == identity.tenant_id
                        and identity.cabinet_id is None and identity.project_id is None))
                    and (identity.installation_scope_kind != "cabinet" or (
                        identity.installation_scope_id == identity.cabinet_id
                        and identity.cabinet_id is not None and identity.project_id is None))
                    and (identity.installation_scope_kind != "project" or (
                        identity.installation_scope_id == identity.project_id
                        and identity.cabinet_id is not None and identity.project_id is not None))
                    and (source.asserted_company_id is None or source.asserted_company_id == context.company_id)
                    and source.asserted_scope_kind == identity.installation_scope_kind
                    and source.asserted_scope_id == identity.installation_scope_id)


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(_thaw_json(value), sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      default=_json_default)


def _freeze_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_json(item) for item in value)
    return value


def _contains_sensitive_key(value: object) -> bool:
    forbidden = {
        "arg", "args", "arguments", "raw_input", "raw_arguments", "payload",
        "secret", "secrets", "token", "credential", "credentials",
    }
    if isinstance(value, Mapping):
        return any(
            isinstance(key, str) and key.casefold().replace("-", "_") in forbidden
            or _contains_sensitive_key(item)
            for key, item in value.items()
        )
    if isinstance(value, (list, tuple, set, frozenset)):
        return any(_contains_sensitive_key(item) for item in value)
    return False


def _thaw_json(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_json(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_thaw_json(item) for item in value]
    if isinstance(value, (set, frozenset)):
        return sorted(_thaw_json(item) for item in value)
    return value


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    if isinstance(value, Mapping):
        return dict(value)
    raise TypeError(f"unsupported digest value: {type(value).__name__}")


def observation_evidence(observation: ShadowObservation) -> Mapping[str, Any]:
    """Redacted, digest-only shadow evidence; safe for an append-only audit row."""
    if not isinstance(observation, ShadowObservation):
        raise TypeError("shadow evidence must be a ShadowObservation")
    return MappingProxyType({
        "tenant_id": observation.tenant_id,
        "app_definition_id": observation.app_definition_id,
        "source_id": observation.source_id,
        "capability_key": observation.capability_key,
        "source_digest": observation.source_digest,
        "canonical_digest": observation.canonical_digest,
        "binding_digest": observation.binding_digest,
        "operation_mapping_digest": observation.operation_mapping_digest,
        "legacy_allowed": observation.legacy_decision.allowed,
        "legacy_reason_class": _decision_class(observation.legacy_decision.reason_code),
        "canonical_allowed": observation.canonical_decision.allowed,
        "canonical_reason_class": _decision_class(observation.canonical_decision.reason_code),
        "mismatch": observation.mismatch,
        "mismatch_explained": observation.mismatch_explained,
        "mismatch_explanation": observation.mismatch_explanation,
        "checkpoint": observation.checkpoint,
        "correlation_id": observation.correlation_id,
        "observed_at": observation.observed_at,
    })


def observation_from_evidence(value: Mapping[str, Any]) -> ShadowObservation:
    """Rebuild a shadow observation from stored evidence; reject anything incomplete."""
    if not isinstance(value, Mapping):
        raise ValueError("stored shadow evidence must be an object")
    required = ("tenant_id", "app_definition_id", "source_id", "capability_key",
                "source_digest", "checkpoint", "correlation_id", "observed_at",
                "legacy_allowed", "legacy_reason_class", "canonical_allowed",
                "canonical_reason_class", "mismatch", "mismatch_explained",
                "operation_mapping_digest")
    if any(name not in value for name in required):
        raise ValueError("stored shadow evidence is incomplete")
    binding = value.get("binding_digest")
    if not isinstance(binding, str) or not is_sha256(binding):
        raise ValueError("stored shadow evidence requires the exact binding digest")
    canonical = value.get("canonical_digest")
    if canonical is not None and not is_sha256(canonical):
        raise ValueError("stored shadow canonical digest must be sha256")
    legacy_allowed = value["legacy_allowed"]
    canonical_allowed = value["canonical_allowed"]
    if not isinstance(legacy_allowed, bool) or not isinstance(canonical_allowed, bool):
        raise ValueError("stored shadow decisions must be explicit booleans")
    legacy_reason = "allowed" if legacy_allowed else str(value["legacy_reason_class"])
    canonical_reason = "allowed" if canonical_allowed else str(value["canonical_reason_class"])
    return ShadowObservation(
        tenant_id=str(value["tenant_id"]),
        app_definition_id=str(value["app_definition_id"]),
        source_id=str(value["source_id"]),
        capability_key=str(value["capability_key"]),
        source_digest=str(value["source_digest"]),
        canonical_digest=canonical,
        binding_digest=binding,
        legacy_decision=ShadowDecision(legacy_allowed, legacy_reason, binding),
        canonical_decision=ShadowDecision(canonical_allowed, canonical_reason, binding),
        mismatch=bool(value["mismatch"]),
        mismatch_explained=bool(value["mismatch_explained"]),
        checkpoint=str(value["checkpoint"]),
        correlation_id=str(value["correlation_id"]),
        observed_at=str(value["observed_at"]),
        operation_mapping_digest=str(value["operation_mapping_digest"]),
        mismatch_explanation=value.get("mismatch_explanation"),
    )


def binding_digest(identity: ResolvedGrantIdentity) -> str:
    return digest({"tenant_id": identity.tenant_id, "app_definition_id": identity.app_definition_id,
                   "app_version_id": identity.app_version_id, "app_id": identity.app_id,
                   "company_id": identity.company_id,
                   "manifest_version": identity.manifest_version, "scope_kind": identity.scope_kind,
                   "scope_id": identity.scope_id, "installation_scope_kind": identity.installation_scope_kind,
                   "installation_scope_id": identity.installation_scope_id,
                   "cabinet_id": identity.cabinet_id,
                   "project_id": identity.project_id, "subject_kind": identity.subject_kind,
                   "subject_id": identity.subject_id, "session_id": identity.session_id,
                   "plugin_instance_id": identity.plugin_instance_id,
                   "runtime_generation": identity.runtime_generation,
                   "writer_generation": identity.writer_generation,
                   "artifact_digest": identity.artifact_digest,
                   "owner_evidence_digest": identity.owner_evidence_digest,
                   "transaction_id": identity.transaction_id,
                   "correlation_id": identity.correlation_id,
                   "rls_evidence_digest": identity.rls_evidence_digest,
                   "definition_evidence_digest": identity.definition_evidence_digest,
                   "instance_evidence_digest": identity.instance_evidence_digest})


def _parse_timestamp(value: Any, field_name: str) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError as exc:
            raise MappingError(f"{field_name}_invalid") from exc
    else:
        raise MappingError(f"{field_name}_invalid")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MappingError(f"{field_name}_timezone_missing")
    return parsed


def _aware(value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamps must be timezone-aware")


APPROVED_MISMATCH_EXPLANATIONS = frozenset({
    "legacy_capability_not_requested_by_manifest",
    "legacy_and_canonical_lifecycle_disagree",
    "legacy_scope_policy_not_yet_canonical",
    "canonical_grant_not_yet_backfilled",
})
MISMATCH_EXPLANATIONS = MappingProxyType({
    ("allowed", "denied"): "canonical_grant_not_yet_backfilled",
    ("allowed", "quarantined_binding"): "canonical_grant_not_yet_backfilled",
    ("denied", "allowed"): "legacy_scope_policy_not_yet_canonical",
    ("expired", "allowed"): "legacy_and_canonical_lifecycle_disagree",
    ("revoked", "allowed"): "legacy_and_canonical_lifecycle_disagree",
    ("allowed", "expired"): "legacy_and_canonical_lifecycle_disagree",
    ("allowed", "revoked"): "legacy_and_canonical_lifecycle_disagree",
})
# Explicit, reviewable mismatch classes; nothing else may be auto-explained.

LEGACY_SCOPE_POLICY_REASONS = frozenset({
    "scope_denied", "capability_not_requested", "capability_mismatch",
    "operation_not_granted", "resource_not_granted", "domain_not_granted",
    "constraints_not_satisfied",
})
"""Legacy denials that are a named scope-policy decision, and only those.

``denied -> allowed`` is the mismatch direction that would *widen* access, so it is
reconcilable only when the legacy owner denied on purpose over scope, capability or
constraint policy. A denial because the legacy gateway does not know the method
(``method_unknown``/``method_ambiguous``), because the grant/request lifecycle is not
valid, or because the request was malformed is a genuine disagreement with a canonical
allow: it stays unexplained so it blocks parity review instead of being absorbed by a
class-level explanation.
"""


def _mismatch_explanation(legacy_reason: str, canonical_reason: str) -> str | None:
    """Explanation code for one decision disagreement, or ``None`` if unreconciled."""
    pair = (_decision_class(legacy_reason), _decision_class(canonical_reason))
    explanation = MISMATCH_EXPLANATIONS.get(pair)
    if explanation is None:
        return None
    if pair == ("denied", "allowed") and legacy_reason.strip().lower() not in LEGACY_SCOPE_POLICY_REASONS:
        return None
    return explanation


def _decision_class(reason_code: str) -> str:
    normalized = reason_code.strip().lower()
    if normalized in {"expired", "grant_expired", "expired_grant", "source_grant_expired"}:
        return "expired"
    if normalized in {"revoked", "grant_revoked", "revoked_grant", "protocol_revoked"}:
        return "revoked"
    if normalized in {"quarantined_binding", "binding_unresolved"}:
        return "quarantined_binding"
    if normalized in {"allowed", "allow", "granted"}:
        return "allowed"
    if normalized in {"denied", "deny", "not_granted", "protocol_denied",
                      "grant_missing", "grant_denied", "scope_denied", "quota_exceeded",
                      "method_unknown", "method_ambiguous", "capability_not_requested",
                      "capability_mismatch", "operation_not_granted", "resource_not_granted",
                      "domain_not_granted", "constraints_not_satisfied", "lifecycle_denied",
                      "cross_version", "app_missing", "grants_missing", "invalid_request"}:
        return "denied"
    return normalized


def is_sha256(value: str | None) -> bool:
    return bool(isinstance(value, str) and len(value) == 71 and value.startswith("sha256:")
                and all(character in "0123456789abcdef" for character in value[7:]))


def _has_wildcard(value: str) -> bool:
    return any(token in value for token in ("*", "?", "[", "]"))


class MappingError(ValueError):
    """Stable reason code for an unrepresentable source grant."""


class IdentityResolutionError(RuntimeError):
    """A resolver or shadow decision violated the server-bound identity contract."""


@dataclass(frozen=True, slots=True)
class MethodOperationResolution:
    """Outcome of the approved method -> operation registry lookup.

    A rejected lookup is quarantine evidence, never a positive mapping and never an
    allow: the legacy decision stays authoritative for the shadow comparison.
    """

    approved: bool
    reason_code: str
    mapping_digest: str
    capability_key: str | None = None
    operation: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.approved, bool):
            raise ValueError("method mapping approval must be explicit")
        if not isinstance(self.reason_code, str) or not self.reason_code.strip():
            raise ValueError("method mapping outcome requires a stable reason code")
        if not is_sha256(self.mapping_digest):
            raise ValueError("method mapping digest must be sha256")
        if self.approved and (self.reason_code != "approved"
                              or not (self.capability_key or "").strip()):
            raise ValueError("approved method mapping requires the exact capability identity")

    @classmethod
    def approved_mapping(cls, *, mapping_digest: str, capability_key: str,
                         operation: str) -> "MethodOperationResolution":
        return cls(True, "approved", mapping_digest, capability_key, operation)

    @classmethod
    def rejected(cls, reason_code: str) -> "MethodOperationResolution":
        return cls(False, reason_code, digest({"rejected": reason_code}))
