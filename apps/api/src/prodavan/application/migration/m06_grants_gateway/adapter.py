"""Bounded M06 grant mapping and shadow comparison; never an authorization owner."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence

SCHEMA_VERSION = "m06-grant-mapping-v1"
METHOD_MAP_VERSION = "methods-operations-v1"
APPROVED_CAPABILITIES = frozenset({
    "ui.render", "ui.navigation", "storage.kv", "storage.files", "network.fetch",
    "events.subscribe", "platform.commands", "platform.queries", "secrets.read", "identity.read",
})


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
    """Resolved only by trusted server ports. Assertions are never authority."""

    tenant_id: str | None
    subject_kind: str | None
    subject_id: str | None
    session_id: str | None
    cabinet_id: str | None
    project_id: str | None
    authenticated: bool
    correlation_id: str


@dataclass(frozen=True, slots=True)
class DependencyGates:
    m04_scope_digest: str | None = None
    m04e_definition_version_digest: str | None = None
    m05_instance_digest: str | None = None
    m06_schema_digest: str | None = None
    trusted_writer_epoch: int | None = None

    @property
    def ready(self) -> bool:
        return bool(
            all(is_sha256(value) for value in (
                self.m04_scope_digest, self.m04e_definition_version_digest,
                self.m05_instance_digest, self.m06_schema_digest,
            ))
            and self.trusted_writer_epoch is not None and self.trusted_writer_epoch > 0
        )


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

    def __post_init__(self) -> None:
        if not all(value.strip() for value in (self.source_kind, self.source_id, self.schema_version, self.app_id, self.manifest_version)):
            raise ValueError("source identity and app/version are required")
        _aware(self.issued_at)
        _aware(self.expires_at)
        if self.expires_at is not None and self.expires_at <= self.issued_at:
            raise ValueError("expires_at must be after issued_at")


@dataclass(frozen=True, slots=True)
class ResolvedGrantIdentity:
    tenant_id: str
    app_definition_id: str
    app_id: str
    app_version_id: str
    manifest_version: str
    artifact_digest: str
    scope_kind: str
    scope_id: str
    cabinet_id: str | None
    project_id: str | None
    subject_kind: str
    subject_id: str
    session_id: str
    plugin_instance_id: str
    runtime_generation: int
    writer_generation: int


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    allowed: bool
    reason_code: str
    binding_digest: str


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


@dataclass(frozen=True, slots=True)
class SourceMappingRecord:
    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    canonical_digest: str | None
    binding_digest: str | None
    outcome: AdapterOutcome
    reason_code: str | None
    checkpoint: str
    correlation_id: str


class IdentityResolutionPort(Protocol):
    async def resolve(self, *, context: AuthenticatedGrantContext, app_id: str,
                      manifest_version: str, source: LegacyGrantSource) -> ResolvedGrantIdentity: ...


class LegacyDecisionPort(Protocol):
    async def decide(self, *, identity: ResolvedGrantIdentity, capability: str,
                     operation: str, resource: str, domain: str | None,
                     now: datetime) -> ShadowDecision: ...


class CanonicalShadowDecisionPort(Protocol):
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


class EvidenceReadPort(Protocol):
    async def find_mapping(self, source_kind: str, source_id: str, schema_version: str) -> SourceMappingRecord | None: ...


class TrustedEvidenceTransactionPort(Protocol):
    """Trusted persistence contract, not an in-memory/fake transaction implementation."""

    async def commit_evidence(self, transaction: EvidenceTransaction) -> MappingOutcome: ...


@dataclass(frozen=True, slots=True)
class GrantMapping:
    capability_key: str
    decision: str
    operations: tuple[str, ...]
    resources: tuple[str, ...]
    domains: tuple[str, ...]
    constraints: Mapping[str, Any]
    payload_digest: str


class GrantSetAdapter:
    """Strict parser: unsupported semantics are quarantined, never broadened."""

    def adapt(self, entry: Mapping[str, Any]) -> GrantMapping:
        name = entry.get("name")
        decision = entry.get("decision")
        constraints = entry.get("constraints") or {}
        if not isinstance(name, str) or not name.strip():
            raise MappingError("capability_identity_missing")
        if name not in APPROVED_CAPABILITIES:
            raise MappingError("capability_unknown")
        if decision not in {"granted", "denied"}:
            raise MappingError("unsupported_decision")
        if not isinstance(constraints, Mapping):
            raise MappingError("constraints_malformed")
        allowed_keys = {"methods", "resources", "domains", "eventTypes", "maxBytesPerRequest"}
        if set(constraints) - allowed_keys:
            raise MappingError("constraint_unsupported")
        event_types = constraints.get("eventTypes")
        if event_types is not None and (
            not isinstance(event_types, (list, tuple)) or not event_types
            or any(not isinstance(item, str) or not item.strip() for item in event_types)
            or len(set(event_types)) != len(event_types)
        ):
            raise MappingError("event_types_invalid")
        methods = constraints.get("methods")
        resources = constraints.get("resources")
        domains = constraints.get("domains")
        if not all(isinstance(value, (list, tuple)) and value and all(isinstance(item, str) and item.strip() for item in value)
                   for value in (methods, resources, domains)):
            raise MappingError("constraint_incomplete")
        if len(set(methods)) != len(methods) or len(set(resources)) != len(resources) or len(set(domains)) != len(domains):
            raise MappingError("constraint_duplicate")
        max_bytes = constraints.get("maxBytesPerRequest")
        if max_bytes is not None and (not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 0):
            raise MappingError("max_bytes_invalid")
        normalized = {
            "capability_key": name,
            "decision": decision,
            "operations": sorted(methods),
            "resources": sorted(resources),
            "domains": sorted(domains),
            "constraints": {key: sorted(value) if isinstance(value, (list, tuple)) else value
                            for key, value in sorted(constraints.items())},
            "mapping_version": METHOD_MAP_VERSION,
        }
        return GrantMapping(name, decision, tuple(normalized["operations"]), tuple(normalized["resources"]),
                            tuple(normalized["domains"]), normalized["constraints"], digest(normalized))


class CapabilityGatewayService:
    """Shadow facade; returns the legacy answer and only appends target evidence."""

    def __init__(self, *, identity: IdentityResolutionPort, legacy: LegacyDecisionPort,
                 canonical_shadow: CanonicalShadowDecisionPort, evidence_read: EvidenceReadPort,
                 evidence_transaction: TrustedEvidenceTransactionPort,
                 gates: DependencyGates, page_size: int = 100) -> None:
        if not 1 <= page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        self._identity, self._legacy, self._canonical = identity, legacy, canonical_shadow
        self._evidence_read = evidence_read
        self._evidence_transaction, self._page_size = evidence_transaction, page_size
        self._dependency_gates = gates

    async def observe(self, *, source: LegacyGrantSource, context: AuthenticatedGrantContext,
                      capability: str, operation: str, resource: str, domain: str | None,
                      now: datetime | None = None, checkpoint: str) -> ShadowObservation | None:
        checked_at = now or datetime.now(UTC)
        _aware(checked_at)
        if not checkpoint.strip():
            raise ValueError("checkpoint is required")
        if not capability.strip() or not operation.strip() or not resource.strip():
            raise ValueError("capability, operation and resource are required")
        source_digest = digest({"schema": source.schema_version, "app_id": source.app_id,
                                "manifest_version": source.manifest_version,
                                "grants": source.grants, "asserted_company_id": source.asserted_company_id,
                                "issued_at": source.issued_at.isoformat(),
                                "expires_at": source.expires_at.isoformat() if source.expires_at else None,
                                "asserted_scope_kind": source.asserted_scope_kind,
                                "asserted_scope_id": source.asserted_scope_id})
        prior = await self._evidence_read.find_mapping(source.source_kind, source.source_id, SCHEMA_VERSION)
        if source.expires_at is not None and checked_at >= source.expires_at:
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                         None, None, AdapterOutcome.QUARANTINED, "source_grant_expired", checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(record, context, "source_grant_expired",
                                         {"checked_at": checked_at.isoformat(), "observed_at": checked_at.isoformat()})
            return None
        if checked_at < source.issued_at:
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                         None, None, AdapterOutcome.QUARANTINED, "source_grant_future", checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(record, context, "source_grant_future",
                                         {"checked_at": checked_at.isoformat(), "observed_at": checked_at.isoformat()})
            return None
        if prior and prior.source_digest != source_digest:
            conflict = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                           None, None, AdapterOutcome.CONFLICT, "source_digest_changed", checkpoint,
                                           context.correlation_id)
            await self._commit_evidence(conflict, context, "source_digest_changed", {"source_digest": source_digest})
            return None
        if not context.authenticated or not context.tenant_id or not context.subject_id \
                or not context.correlation_id.strip() or not self._dependency_gates.ready:
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                         None, None, AdapterOutcome.BLOCKED, "dependency_gate_missing", checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(record, context, "dependency_gate_missing",
                                         {"company_assertion_present": bool(source.asserted_company_id)})
            return None
        identity = await self._identity.resolve(context=context, app_id=source.app_id,
                                                manifest_version=source.manifest_version, source=source)
        if not self._identity_matches(identity, context, source):
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                         None, None, AdapterOutcome.QUARANTINED, "identity_assertion_mismatch", checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(record, context, "identity_assertion_mismatch", {"identity_resolved": True})
            return None
        parser = GrantSetAdapter()
        try:
            matching_entries = [entry for entry in source.grants
                                if isinstance(entry, Mapping) and entry.get("name") == capability]
            if not matching_entries:
                raise MappingError("capability_unknown")
            if len(matching_entries) != 1:
                raise MappingError("capability_ambiguous")
            mapped = parser.adapt(matching_entries[0])
        except (MappingError, AttributeError, TypeError, ValueError) as exc:
            reason = str(exc) or "grant_entry_malformed"
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                         None, binding_digest(identity), AdapterOutcome.QUARANTINED, reason, checkpoint,
                                         context.correlation_id)
            await self._commit_evidence(record, context, reason, {"capability_key_digest": digest(capability)})
            return None
        outcome = AdapterOutcome.KNOWN_DENY if mapped.decision == "denied" else AdapterOutcome.MAPPED_EVIDENCE
        record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                     mapped.payload_digest, binding_digest(identity), outcome,
                                     "protocol_denied" if mapped.decision == "denied" else None, checkpoint,
                                     context.correlation_id)
        replay = (MappingOutcome.CONFLICT if prior and prior.canonical_digest != mapped.payload_digest
                  else MappingOutcome.REPLAY if prior else MappingOutcome.RECORDED)
        if replay is MappingOutcome.REPLAY:
            legacy_decision = await self._legacy.decide(identity=identity, capability=capability, operation=operation,
                                                        resource=resource, domain=domain, now=checked_at)
            canonical_decision = await self._canonical.decide_shadow(identity=identity, capability=capability,
                                                                      operation=operation, resource=resource, domain=domain,
                                                                      constraints=mapped.constraints, now=checked_at)
            observation = self._observation(identity, source, mapped, legacy_decision, canonical_decision,
                                            source_digest, checkpoint, context.correlation_id)
            await self._evidence_transaction.commit_evidence(EvidenceTransaction(
                None, {"protocol_decision": "denied"} if mapped.decision == "denied" else None,
                observation, self._audit_event(record, context, "shadow_replayed")))
            return observation
        if replay is MappingOutcome.CONFLICT:
            record = SourceMappingRecord(source.source_kind, source.source_id, SCHEMA_VERSION, source_digest,
                                         mapped.payload_digest, binding_digest(identity), AdapterOutcome.CONFLICT,
                                         "mapping_digest_conflict", checkpoint, context.correlation_id)
            await self._commit_evidence(record, context, "mapping_digest_conflict",
                                         {"capability_key_digest": digest(capability)})
            return None
        legacy_decision = await self._legacy.decide(identity=identity, capability=capability, operation=operation,
                                                    resource=resource, domain=domain, now=checked_at)
        canonical_decision = await self._canonical.decide_shadow(identity=identity, capability=capability,
                                                                  operation=operation, resource=resource, domain=domain,
                                                                  constraints=mapped.constraints, now=checked_at)
        observation = self._observation(identity, source, mapped, legacy_decision, canonical_decision,
                                        source_digest, checkpoint, context.correlation_id)
        await self._evidence_transaction.commit_evidence(EvidenceTransaction(
            record, {"protocol_decision": "denied"} if mapped.decision == "denied" else None,
            observation, self._audit_event(record, context, "shadow_mismatch" if observation.mismatch else "shadow_compared")))
        return observation

    @staticmethod
    def _observation(identity: ResolvedGrantIdentity, source: LegacyGrantSource, mapped: GrantMapping,
                     legacy_decision: ShadowDecision, canonical_decision: ShadowDecision,
                     source_digest: str, checkpoint: str, correlation_id: str) -> ShadowObservation:
        expected_binding = binding_digest(identity)
        if legacy_decision.binding_digest != expected_binding or canonical_decision.binding_digest != expected_binding:
            raise IdentityResolutionError("decision_binding_digest_mismatch")
        mismatch = (legacy_decision.allowed != canonical_decision.allowed
                    or _decision_class(legacy_decision.reason_code) != _decision_class(canonical_decision.reason_code))
        return ShadowObservation(identity.tenant_id, identity.app_definition_id, source.source_id,
                                 mapped.capability_key, source_digest, mapped.payload_digest,
                                 expected_binding, legacy_decision, canonical_decision, mismatch,
                                 mismatch and canonical_decision.reason_code == "quarantined_binding",
                                 checkpoint, correlation_id)

    @staticmethod
    def _audit_event(record: SourceMappingRecord, context: AuthenticatedGrantContext, event: str) -> Mapping[str, Any]:
        return {"event": event, "tenant_id": context.tenant_id,
            "source_kind": record.source_kind, "source_id": record.source_id,
            "schema_version": record.schema_version, "source_digest": record.source_digest,
            "canonical_digest": record.canonical_digest, "binding_digest": record.binding_digest,
            "outcome": record.outcome.value, "reason_code": record.reason_code,
            "checkpoint": record.checkpoint, "correlation_id": context.correlation_id,
            "redaction": "raw_payloads_and_secrets_excluded"}

    async def _commit_evidence(self, record: SourceMappingRecord, context: AuthenticatedGrantContext,
                               event: str, quarantine: Mapping[str, Any]) -> None:
        await self._evidence_transaction.commit_evidence(EvidenceTransaction(
            record, quarantine, None, self._audit_event(record, context, event)))

    @staticmethod
    def _identity_matches(identity: ResolvedGrantIdentity, context: AuthenticatedGrantContext,
                          source: LegacyGrantSource) -> bool:
        return bool(identity.tenant_id and identity.app_definition_id and identity.app_version_id
                    and identity.subject_kind and context.subject_kind == identity.subject_kind
                    and identity.subject_id and identity.session_id and identity.plugin_instance_id
                    and identity.runtime_generation > 0 and identity.writer_generation > 0
                    and is_sha256(identity.artifact_digest)
                    and identity.scope_kind in {"tenant", "cabinet", "project", "plugin_instance"}
                    and (identity.scope_kind != "project" or (identity.cabinet_id and identity.project_id == identity.scope_id))
                    and (identity.scope_kind != "cabinet" or (identity.cabinet_id == identity.scope_id and identity.project_id is None))
                    and (identity.scope_kind != "tenant" or (identity.scope_id == identity.tenant_id and identity.cabinet_id is None and identity.project_id is None))
                    and (identity.scope_kind != "plugin_instance" or identity.scope_id == identity.plugin_instance_id)
                    and identity.app_id == source.app_id and identity.manifest_version == source.manifest_version
                    and identity.tenant_id == context.tenant_id and identity.subject_id == context.subject_id
                    and identity.session_id == context.session_id
                    and identity.cabinet_id == context.cabinet_id and identity.project_id == context.project_id
                    and (source.asserted_company_id is None or source.asserted_company_id == identity.tenant_id)
                    and (source.asserted_scope_kind is None or source.asserted_scope_kind == identity.scope_kind)
                    and (source.asserted_scope_id is None or source.asserted_scope_id == identity.scope_id))


def digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=_json_default)


def _json_default(value: Any) -> Any:
    if hasattr(value, "isoformat"):
        return value.isoformat()
    if isinstance(value, (set, frozenset)):
        return sorted(value)
    raise TypeError(f"unsupported digest value: {type(value).__name__}")


def binding_digest(identity: ResolvedGrantIdentity) -> str:
    return digest({"tenant_id": identity.tenant_id, "app_definition_id": identity.app_definition_id,
                   "app_version_id": identity.app_version_id, "scope_kind": identity.scope_kind,
                   "scope_id": identity.scope_id, "cabinet_id": identity.cabinet_id,
                   "project_id": identity.project_id, "subject_kind": identity.subject_kind,
                   "subject_id": identity.subject_id, "session_id": identity.session_id,
                   "plugin_instance_id": identity.plugin_instance_id,
                   "runtime_generation": identity.runtime_generation,
                   "writer_generation": identity.writer_generation,
                   "artifact_digest": identity.artifact_digest})


def _aware(value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError("timestamps must be timezone-aware")


def _decision_class(reason_code: str) -> str:
    normalized = reason_code.strip().lower()
    if normalized in {"expired", "grant_expired", "expired_grant"}:
        return "expired"
    if normalized in {"revoked", "grant_revoked", "revoked_grant"}:
        return "revoked"
    if normalized in {"quarantined_binding", "binding_unresolved"}:
        return "quarantined_binding"
    if normalized in {"allowed", "allow", "granted"}:
        return "allowed"
    if normalized in {"denied", "deny", "grant_missing", "not_granted"}:
        return "denied"
    return normalized


def is_sha256(value: str | None) -> bool:
    return bool(value and len(value) == 71 and value.startswith("sha256:")
                and all(character in "0123456789abcdef" for character in value[7:]))


class MappingError(ValueError):
    """Stable reason code for an unrepresentable source grant."""


class IdentityResolutionError(RuntimeError):
    """A resolver or shadow decision violated the server-bound identity contract."""
