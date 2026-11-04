"""Test doubles for the migration composition root (``MIG-ASSEMBLY-01``).

Authored during the migration **verification freeze**: nothing in this package was executed.

Every double here is *inert on purpose*. Constructors of the assembled services are pure —
they store ports and validate scalars — so a double only has to exist to be stored; any
attempt to actually run a migration step through it raises :class:`CompositionDoubleUsed`.
That keeps two promises at once:

* the acceptance artifacts prove the object graph is complete without a database;
* a double can never silently become a working migration writer if a future test forgets to
  wire a real adapter.

The one exception is :func:`server_definition_repository`, which returns the *real*
``ServerDefinitionVersionRepository``: its constructor is pure and the composition treats it
as caller-owned state, so testing with the production class is more faithful than a fake.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any

from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    HandoffGate,
    HandoffStatus,
    M05EvidenceExport,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    ServerDefinitionVersionRepository,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    M04ScopeGateEvidence,
    M05RuntimeGateEvidence,
    M05UpstreamEvidence,
    TrustedRuntimeContext,
)
from prodavan.application.migration.m06_grants_gateway.composition import ShadowGatewayPorts

PARTITION = ("tenant-1", "definition-1")
SNAPSHOT_DIGEST = "sha256:" + "a" * 64
CANONICAL_DIGEST = "sha256:" + "b" * 64

#: Distinguishes "argument omitted" from "explicitly ``None``" for the optional legacy ports.
_UNSET = object()


def digest(seed: str) -> str:
    """A syntactically valid sha256 digest, deterministic in ``seed``."""

    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


class CompositionDoubleUsed(AssertionError):
    """A test double reached the point where a real migration port would do work."""


def _refuse(*_args: Any, **_kwargs: Any) -> Any:
    raise CompositionDoubleUsed(
        "a composition test double was executed; assembly must not run a migration step"
    )


@dataclass(frozen=True, slots=True)
class InertPort:
    """Stand-in for any protocol: every call refuses."""

    name: str = "inert-port"

    def __getattr__(self, attribute: str) -> Any:
        return _refuse


def session_factory() -> Any:
    """A callable session factory that refuses to open a session during assembly."""

    return _refuse


def tenant_scope_context() -> Any:
    return InertPort("tenant-scope-context")


def trusted_tenant_context() -> Any:
    return InertPort("trusted-tenant-context")


def runtime_context(*, tenant: str = PARTITION[0], usable: bool = True) -> TrustedRuntimeContext:
    """A synthetic but well-formed run context; ``usable=False`` exercises the refusal path."""

    return TrustedRuntimeContext(
        resolved_tenant_id=tenant if usable else None,
        subject_id="subject-1" if usable else None,
        session_id="session-1" if usable else None,
        authenticated=usable,
        correlation_id="corr-composition",
    )


def definition_resolver() -> Any:
    return InertPort("definition-resolver")


def server_definition_repository() -> ServerDefinitionVersionRepository:
    """The production facade over an inert resolver (no scopes/evidence/artifacts yet)."""

    return ServerDefinitionVersionRepository(definition_resolver())


def backfill_checkpoint(*, tenant: str = PARTITION[0], definition: str = PARTITION[1]) -> BackfillCheckpoint:
    """A drained ``version`` checkpoint: the only shape M04E may hand off."""

    return BackfillCheckpoint(
        partition=(tenant, definition),
        phase="version",
        after_source_id=None,
        sequence=3,
        source_snapshot_id="snapshot-1",
        source_schema_version="v1",
        source_snapshot_digest=SNAPSHOT_DIGEST,
        canonical_schema_version="v2",
        writer_generation=1,
        phase_complete=True,
    )


def m04_scope_gate_evidence(*, tenant: str = PARTITION[0], definition: str = PARTITION[1]) -> M04ScopeGateEvidence:
    return M04ScopeGateEvidence(
        partition=(tenant, definition),
        source_snapshot_digest=SNAPSHOT_DIGEST,
        canonical_digest=CANONICAL_DIGEST,
        checkpoint_digest=digest("m04-checkpoint"),
        parent_validation_digest=digest("m04-parent"),
        rls_validation_digest=digest("m04-rls"),
        typed_constraint_digests=(digest("m04-typed"),),
        active_scope_count=1,
    )


def m05_runtime_gate_evidence(*, tenant: str = PARTITION[0], definition: str = PARTITION[1]) -> M05RuntimeGateEvidence:
    return M05RuntimeGateEvidence(
        partition=(tenant, definition),
        source_snapshot_digest=SNAPSHOT_DIGEST,
        canonical_digest=CANONICAL_DIGEST,
        checkpoint_digest=digest("m05-checkpoint"),
        m04_scope_digest=CANONICAL_DIGEST,
        m04e_export_digest=CANONICAL_DIGEST,
        exact_instance_key_digest=digest("m05-exact-key"),
        registry_to_instance_fk_digest=digest("m05-fk-1"),
        instance_to_registry_fk_digest=digest("m05-fk-2"),
        artifact_validation_digest=digest("m05-artifact"),
        rls_review_digest=digest("m05-rls"),
        writer_cas_review_digest=digest("m05-writer-cas"),
        runtime_cas_review_digest=digest("m05-runtime-cas"),
        stale_worker_denial_digest=digest("m05-stale-worker"),
        audit_atomicity_digest=digest("m05-audit"),
    )


def m04e_export(*, tenant: str = PARTITION[0], definition: str = PARTITION[1]) -> M05EvidenceExport:
    """A blocked M04E export: no marker, no accepted rows — the honest current state."""

    gates = tuple(
        HandoffGate(name=name, passed=False, reason="upstream_owner_scope_gate_unproven")
        for name in (
            "MIG-M04-01", "MIG-M04-02", "MIG-M04E-01",
            "active_typed_parent_scope", "same_definition_version", "immutable_artifact",
        )
    )
    return M05EvidenceExport(
        tenant_id=tenant,
        app_definition_id=definition,
        source_snapshot_digest=SNAPSHOT_DIGEST,
        canonical_digest=CANONICAL_DIGEST,
        checkpoint=backfill_checkpoint(tenant=tenant, definition=definition),
        accepted=(),
        quarantined=(),
        gates=gates,
        status=HandoffStatus.BLOCKED,
    )


def upstream_evidence(*, tenant: str = PARTITION[0], definition: str = PARTITION[1]) -> M05UpstreamEvidence:
    """M04 + M04E + M05 gate bundle wired with the same partition and snapshot digests."""

    return M05UpstreamEvidence(
        m04_scope=m04_scope_gate_evidence(tenant=tenant, definition=definition),
        m04e_export=m04e_export(tenant=tenant, definition=definition),
        m05_runtime=m05_runtime_gate_evidence(tenant=tenant, definition=definition),
    )


def shadow_gateway_ports(*, legacy_scope: Any = _UNSET, legacy_quota: Any = _UNSET,
                         upstream: M05UpstreamEvidence | None = None) -> ShadowGatewayPorts:
    """Every M06 port present; ``legacy_scope``/``legacy_quota`` stay overridable to test refusal.

    Omitting a legacy port supplies an inert one; passing ``None`` explicitly keeps ``None`` so
    the ``m06_legacy_scope_and_quota_ports_required`` refusal path stays reachable.
    """

    method = "cap.read"
    return ShadowGatewayPorts(
        company=InertPort("authenticated-company"),
        m04_scope=InertPort("m04-resolve-scope"),
        m04e_definition_versions=server_definition_repository(),
        m05_instances=InertPort("m05-exact-resolver"),
        m05_upstream=upstream if upstream is not None else upstream_evidence(),
        handoff_partitions=InertPort("m06-partition-handoff"),
        legacy_repository=InertPort("legacy-dynamic-plugin-repository"),
        legacy_scope=InertPort("legacy-scope") if legacy_scope is _UNSET else legacy_scope,
        legacy_quota=InertPort("legacy-quota") if legacy_quota is _UNSET else legacy_quota,
        canonical_grants=InertPort("canonical-grants"),
        method_mappings=InertPort("method-mappings"),
        method_servers={method: "server-1"},
        method_versions={method: "1.0.0"},
        evidence_read=InertPort("evidence-read"),
        evidence_transaction=InertPort("evidence-transaction"),
    )


def accepted_source_digest() -> str:
    """Digest for a hypothetical accepted source row (used only in signature-level tests)."""

    return digest("accepted-source")


__all__ = [
    "CANONICAL_DIGEST",
    "CompositionDoubleUsed",
    "InertPort",
    "PARTITION",
    "SNAPSHOT_DIGEST",
    "accepted_source_digest",
    "backfill_checkpoint",
    "definition_resolver",
    "digest",
    "m04_scope_gate_evidence",
    "m04e_export",
    "m05_runtime_gate_evidence",
    "runtime_context",
    "server_definition_repository",
    "session_factory",
    "shadow_gateway_ports",
    "tenant_scope_context",
    "trusted_tenant_context",
    "upstream_evidence",
]
