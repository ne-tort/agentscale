"""Doubles and stored-evidence builders for the M05 context tests.

Two rules, the same ones ``tests/migration/partition_chain/partition_fixtures.py`` states:

**1. Fixtures model only states the domain allows.**  The M04-02 row, the M04E export and
its marker are built by the *production* constructors (``export_m05_evidence``,
``M05HandoffMarker``) with mutually consistent digests, so a positive readiness result below
is a statement about the builder, not about a hand-written dict that happens to type-check.

**2. Inert where the adapter would do work.**  The resolver and the session factory refuse
every call: building contexts must not resolve a principal or open a session.
"""

from __future__ import annotations

import hashlib
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any
from unittest import mock

from prodavan.application.migration.m04e_definition_version import backfill as backfill_module
from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillCheckpoint,
    BackfillRecord,
    EvidenceStatus,
    HandoffGate,
    M05EvidenceExport,
    M05HandoffMarker,
    export_m05_evidence,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    HandoffStatus as M04EHandoffStatus,
)
from prodavan.application.migration.m05_contexts import (
    M05ContextInputs,
    M05InstanceCounts,
    ReviewedM05RuntimeGate,
)

TENANT = "tenant-alpha"
DEFINITION = "definition-alpha"
OTHER_TENANT = "tenant-beta"
SUBJECT = "employee-subject"
SESSION = "session-correlation"
CORRELATION = "corr-m05-context"
WRITER_GENERATION = 3

SNAPSHOT_ID = "snapshot-m05-1"
SOURCE_SCHEMA = "legacy-plugin-instance.v1"
CANONICAL_SCHEMA = "m05.v1"
MANIFEST_VERSION = "1.0.0"
RECORDED_AT = "2026-11-04T00:00:00+00:00"


def digest(seed: str) -> str:
    """A syntactically valid sha256 digest, deterministic in ``seed``."""

    return "sha256:" + hashlib.sha256(seed.encode("utf-8")).hexdigest()


SOURCE_SNAPSHOT_DIGEST = digest("m05-context-source-snapshot")
CANONICAL_DIGEST = digest("m05-context-canonical")
CHECKPOINT_DIGEST = digest("m05-context-checkpoint")
GATE_ENVELOPE_DIGEST = digest("m05-context-gate-envelope")
RLS_POLICY_DIGEST = digest("m05-context-rls-policy")


# ---------------------------------------------------------------------------
# Inert collaborators
# ---------------------------------------------------------------------------


class RecordingSessionFactory:
    """Callable ``async_sessionmaker`` stand-in: records and refuses every use."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError(
            "building an M05 context must not open a database session: the builders compose "
            "and validate, they never read"
        )


class InertResolver:
    """A ``TrustedPrincipalResolver`` that is only ever stored, never asked."""

    def __init__(self) -> None:
        self.calls = 0

    async def resolve(self, *, correlation_id: str) -> Any:
        self.calls += 1
        raise AssertionError("assembly must not resolve a principal")


class NoResolveMethod:
    """Shaped like a resolver but without ``resolve`` -- a host mistake the builder refuses."""


# ---------------------------------------------------------------------------
# Stored M04-02 scope evidence row
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class StoredScopeRow:
    """The columns ``build_m04_scope_gate`` reads off ``m04_scope_promotion_evidence``.

    A plain frozen value, not an ORM row, because the builder must work over whatever the
    caller read (an ORM object, a ``Mapping`` row, a decoded record).  The envelope matches
    :func:`m04_scope_evidence_writer._gate_evidence`, including the parent counts the builder
    requires.
    """

    tenant_id: str
    scope_kind: str
    scope_id: str
    outcome: str
    source_snapshot_digest: str
    canonical_digest: str
    checkpoint_digest: str
    gate_evidence_digest: str
    rls_policy_digest: str
    gate_evidence: dict[str, Any]

    def as_mapping(self) -> dict[str, Any]:
        return {
            "tenant_id": self.tenant_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "outcome": self.outcome,
            "source_snapshot_digest": self.source_snapshot_digest,
            "canonical_digest": self.canonical_digest,
            "checkpoint_digest": self.checkpoint_digest,
            "gate_evidence_digest": self.gate_evidence_digest,
            "rls_policy_digest": self.rls_policy_digest,
            "gate_evidence": self.gate_evidence,
        }


def gate_envelope(*, tenant: str = TENANT, active_scopes: int = 1) -> dict[str, Any]:
    """The redacted M04-02 envelope, in the shape the production writer emits."""

    return {
        "schema": "m04-scope-evidence.v1",
        "kind": "m04_scope_promotion_evidence",
        "outcome": "ready",
        "scope": {
            "kind": "tenant",
            "scope_id": tenant,
            "cabinet_id": None,
            "project_id": None,
            "tenant_id": tenant,
        },
        "registry": {
            "scope_registry_id": f"registry-{tenant}",
            "owner_state": "active",
            "source_digest": digest("scope-source"),
            "canonical_schema_version": "scope-registry.v1",
        },
        "parent": {
            "transaction_id": "m04-tx",
            "reason_code": None,
            "counts": {
                "tenant_matches": 1,
                "cabinet_matches": 0,
                "active_cabinet_memberships": 0,
                "project_matches": 0,
                "active_registry_matches": active_scopes,
            },
            "lifecycle": {
                "tenant_deleted": False,
                "tenant_archived": False,
                "cabinet_deleted": False,
                "cabinet_archived": False,
                "project_deleted": False,
                "project_archived": False,
                "cabinet_membership_kind": None,
            },
            "compatibility": None,
        },
        "actor": {
            "actor_id": "actor-1",
            "tenant_id": tenant,
            "relationship_kind": "employee_membership",
            "relationship_matches": 1,
            "active": True,
            "transaction_id": "m04-tx",
        },
        "rls": {"review_id": "rls-registry:test", "database_role": "app_tenant"},
        "checkpoint": {
            "source_snapshot_id": SNAPSHOT_ID,
            "source_schema_version": SOURCE_SCHEMA,
            "source_snapshot_digest": SOURCE_SNAPSHOT_DIGEST,
            "canonical_schema_version": "scope-registry.v1",
            "stream_kind": "scope_owner_registry",
            "after_source_id": None,
            "sequence": 2,
            "writer_generation": WRITER_GENERATION,
            "digest": CHECKPOINT_DIGEST,
        },
        "writer_generation": WRITER_GENERATION,
        "redaction": "migration-evidence.v1",
    }


def stored_scope_row(**overrides: Any) -> StoredScopeRow:
    """An accepted M04-02 row of this partition's tenant scope."""

    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "outcome": "ready",
        "source_snapshot_digest": SOURCE_SNAPSHOT_DIGEST,
        "canonical_digest": CANONICAL_DIGEST,
        "checkpoint_digest": CHECKPOINT_DIGEST,
        "gate_evidence_digest": GATE_ENVELOPE_DIGEST,
        "rls_policy_digest": RLS_POLICY_DIGEST,
        "gate_evidence": gate_envelope(),
    }
    values.update(overrides)
    return StoredScopeRow(**values)


# ---------------------------------------------------------------------------
# Reviewed M05 runtime-gate manifest and the measured inventory
# ---------------------------------------------------------------------------


def reviewed_gate(**overrides: Any) -> ReviewedM05RuntimeGate:
    """A complete, syntactically real review manifest."""

    values: dict[str, Any] = {
        "review_id": "m05-runtime-gate-review:test",
        "database_role": "app_tenant",
        "exact_instance_key_digest": digest("m05-exact-key"),
        "registry_to_instance_fk_digest": digest("m05-fk-registry-to-instance"),
        "instance_to_registry_fk_digest": digest("m05-fk-instance-to-registry"),
        "artifact_validation_digest": digest("m05-artifact"),
        "rls_review_digest": digest("m05-rls"),
        "writer_cas_review_digest": digest("m05-writer-cas"),
        "runtime_cas_review_digest": digest("m05-runtime-cas"),
        "stale_worker_denial_digest": digest("m05-stale-worker"),
        "audit_atomicity_digest": digest("m05-audit"),
    }
    values.update(overrides)
    return ReviewedM05RuntimeGate(**values)


def instance_counts(**overrides: Any) -> M05InstanceCounts:
    values: dict[str, Any] = {
        "unresolved_required_instances": 0,
        "quarantined_required_instances": 0,
    }
    values.update(overrides)
    return M05InstanceCounts(**values)


# ---------------------------------------------------------------------------
# M04E export + handoff marker (built by the production constructors)
# ---------------------------------------------------------------------------


def backfill_checkpoint(
    *, tenant: str = TENANT, definition: str = DEFINITION
) -> BackfillCheckpoint:
    return BackfillCheckpoint(
        partition=(tenant, definition),
        phase="version",
        after_source_id="version-1",
        sequence=2,
        source_snapshot_id=SNAPSHOT_ID,
        source_schema_version=SOURCE_SCHEMA,
        source_snapshot_digest=SOURCE_SNAPSHOT_DIGEST,
        canonical_schema_version=CANONICAL_SCHEMA,
        writer_generation=WRITER_GENERATION,
        phase_complete=True,
    )


def _definition_row(checkpoint: BackfillCheckpoint) -> BackfillRecord:
    return BackfillRecord(
        source_kind="legacy-definition",
        source_id=DEFINITION,
        schema_version=SOURCE_SCHEMA,
        source_digest=digest("definition-source"),
        canonical_digest=digest("definition-canonical"),
        canonical_identity=(TENANT, "app-alpha"),
        checkpoint=BackfillCheckpoint(
            checkpoint.partition,
            "definition",
            DEFINITION,
            1,
            checkpoint.source_snapshot_id,
            checkpoint.source_schema_version,
            checkpoint.source_snapshot_digest,
            checkpoint.canonical_schema_version,
            checkpoint.writer_generation,
            False,
        ),
        status=EvidenceStatus.ACCEPTED,
        reason_code=None,
        correlation_id=CORRELATION,
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        phase="definition",
        source_snapshot_digest=checkpoint.source_snapshot_digest,
        proof_evidence=(("owner", digest("owner")), ("active_scope", CANONICAL_DIGEST)),
        transaction_id="m04e-tx",
        writer_generation=WRITER_GENERATION,
    )


def _version_row(checkpoint: BackfillCheckpoint, *, active_scope_digest: str) -> BackfillRecord:
    return BackfillRecord(
        source_kind="legacy-version",
        source_id="version-1",
        schema_version=SOURCE_SCHEMA,
        source_digest=digest("version-source"),
        canonical_digest=digest("version-canonical"),
        canonical_identity=(TENANT, DEFINITION, MANIFEST_VERSION),
        checkpoint=checkpoint,
        status=EvidenceStatus.ACCEPTED,
        reason_code=None,
        correlation_id=CORRELATION,
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        phase="version",
        source_snapshot_digest=checkpoint.source_snapshot_digest,
        proof_evidence=(
            ("owner", digest("owner")),
            ("active_scope", active_scope_digest),
            ("same_definition", digest("same-definition")),
            ("immutable_artifact", digest("immutable-artifact")),
            # ``_record_has_positive_proof`` requires the artifact-content proof for every
            # version-phase row: a version is accepted only when its bytes were read.
            ("artifact_content", digest("artifact-content")),
        ),
        artifact_digest=digest("artifact"),
        transaction_id="m04e-tx",
        writer_generation=WRITER_GENERATION,
    )


def handoff_gates(*, parent_digest: str, m04_digest: str) -> tuple[HandoffGate, ...]:
    """The six handoff gates, bound to the M04 digests the M05 runtime gate will cite."""

    return (
        HandoffGate("MIG-M04-01", True, parent_digest),
        HandoffGate("MIG-M04-02", True, m04_digest),
        HandoffGate("MIG-M04E-01", True, None),
        HandoffGate("active_typed_parent_scope", True, m04_digest),
        HandoffGate("same_definition_version", True, digest("same-definition")),
        HandoffGate("immutable_artifact", True, digest("immutable-artifact")),
    )


@contextmanager
def release_gate_open():
    """Run with the static upstream-proof release gate *open*.

    ``readiness.UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED`` is a release switch, not a property of
    any one partition: ``export_m05_evidence`` adds a blocking ``upstream_owner_scope_gate``
    and ``ready_for_import`` answers ``False`` unconditionally while it is closed.  A fixture
    that wants to model "the gates were reviewed" therefore has to model that switch, and
    patching the module attribute is exactly how ``test_runtime_acceptance.py`` does it.
    The patch is scoped and restored, so no other test sees an open gate.
    """

    with mock.patch.object(backfill_module, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True):
        yield


def m04e_export(
    *,
    tenant: str = TENANT,
    definition: str = DEFINITION,
    parent_digest: str = GATE_ENVELOPE_DIGEST,
    m04_digest: str = CANONICAL_DIGEST,
    with_marker: bool = True,
) -> M05EvidenceExport:
    """An accepted M04E export with its ``MIG-M04E-02`` marker, or its blocked absence.

    The export is computed twice on purpose: ``export_m05_evidence`` derives
    ``canonical_digest`` from the accepted rows, and the ``MIG-M04E-01`` gate must cite that
    digest -- so the gate is rebuilt from the first computation and the export recomputed.
    That is exactly what ``persist_m05_handoff`` does in production.
    """

    with release_gate_open():
        return _m04e_export(
            tenant=tenant,
            definition=definition,
            parent_digest=parent_digest,
            m04_digest=m04_digest,
            with_marker=with_marker,
        )


def _m04e_export(
    *,
    tenant: str,
    definition: str,
    parent_digest: str,
    m04_digest: str,
    with_marker: bool,
) -> M05EvidenceExport:
    checkpoint = backfill_checkpoint(tenant=tenant, definition=definition)
    rows = (_definition_row(checkpoint), _version_row(checkpoint, active_scope_digest=m04_digest))
    gates = handoff_gates(parent_digest=parent_digest, m04_digest=m04_digest)
    first = export_m05_evidence(
        tenant_id=tenant,
        app_definition_id=definition,
        records=rows,
        source_snapshot_digest=checkpoint.source_snapshot_digest,
        checkpoint=checkpoint,
        gates=gates,
    )
    gates = tuple(
        HandoffGate(gate.name, gate.passed, first.canonical_digest if gate.name == "MIG-M04E-01"
                    else gate.evidence_digest)
        for gate in gates
    )
    export = export_m05_evidence(
        tenant_id=tenant,
        app_definition_id=definition,
        records=rows,
        source_snapshot_digest=checkpoint.source_snapshot_digest,
        checkpoint=checkpoint,
        gates=gates,
    )
    if not with_marker:
        return export
    marker = M05HandoffMarker(
        record_id=digest("handoff-record"),
        task_id="MIG-M04E-02",
        status=M04EHandoffStatus.READY,
        partition=(tenant, definition),
        source_snapshot_digest=export.source_snapshot_digest,
        canonical_digest=export.canonical_digest,
        checkpoint=checkpoint,
        accepted_count=len(export.accepted),
        quarantined_count=len(export.quarantined),
        gate_evidence=export.gates,
        actor="migration-operator",
        recorded_at=RECORDED_AT,
        decision_owner="coordinator",
        writer_generation=WRITER_GENERATION,
        correlation_id=CORRELATION,
        canonical_positive_writes=False,
    )
    return M05EvidenceExport(
        tenant_id=export.tenant_id,
        app_definition_id=export.app_definition_id,
        source_snapshot_digest=export.source_snapshot_digest,
        canonical_digest=export.canonical_digest,
        checkpoint=export.checkpoint,
        accepted=export.accepted,
        quarantined=export.quarantined,
        gates=export.gates,
        status=export.status,
        canonical_positive_writes=False,
        handoff_marker=marker,
    )


# ---------------------------------------------------------------------------
# The whole input bundle
# ---------------------------------------------------------------------------


def context_inputs(**overrides: Any) -> M05ContextInputs:
    """Every per-partition input the four builders need, stated once."""

    values: dict[str, Any] = {
        "principal_resolver": InertResolver(),
        "tenant_id": TENANT,
        "subject_id": SUBJECT,
        "session_id": SESSION,
        "correlation_id": CORRELATION,
        "authenticated": True,
        "session_factory": RecordingSessionFactory(),
        "m04_scope_row": stored_scope_row(),
        "m04e_export": m04e_export(),
        "review": reviewed_gate(),
        "instance_counts": instance_counts(),
    }
    values.update(overrides)
    return M05ContextInputs(**values)


__all__ = [
    "CANONICAL_DIGEST",
    "CANONICAL_SCHEMA",
    "CHECKPOINT_DIGEST",
    "CORRELATION",
    "DEFINITION",
    "GATE_ENVELOPE_DIGEST",
    "InertResolver",
    "MANIFEST_VERSION",
    "NoResolveMethod",
    "OTHER_TENANT",
    "RECORDED_AT",
    "RLS_POLICY_DIGEST",
    "RecordingSessionFactory",
    "SESSION",
    "SNAPSHOT_ID",
    "SOURCE_SCHEMA",
    "SOURCE_SNAPSHOT_DIGEST",
    "StoredScopeRow",
    "SUBJECT",
    "TENANT",
    "WRITER_GENERATION",
    "backfill_checkpoint",
    "context_inputs",
    "digest",
    "gate_envelope",
    "instance_counts",
    "m04e_export",
    "reviewed_gate",
    "stored_scope_row",
]
