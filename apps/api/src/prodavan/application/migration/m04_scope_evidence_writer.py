"""Trusted append-only writer for ``m04_scope_promotion_evidence`` (MIG-M04-WRITER-01).

Why this module exists
----------------------
``infrastructure/persistence/models/m04_scope_evidence.py`` registers the table and
``application/migration/evidence_ports.py::SqlPartitionHandoffReadStore.read_partition``
consumes it, but until now nothing in production appended a row, so the M04 -> M06 chain
refused with ``m04_scope_promotion_evidence_missing`` (open blocker
``m04_scope_evidence_writer_absent``). This module is the producer side of exactly that
reader contract: one M04-02 resolution bundle plus a trusted :class:`TrustedEvidenceScope`
becomes the single append-only row :func:`review_m04_scope_evidence` accepts.

The contract is the reader, not a guess
--------------------------------------
A stored row is consumable only when every one of these holds, and each is produced here:

* ``(tenant_id, scope_kind, scope_id)`` is the partition and its typed shape is the one
  ``evidence_ports.m04_scope_anchor`` reconstructs: ``tenant`` -> ``scope_id = tenant_id``
  with both child anchors NULL, ``cabinet`` -> ``scope_id = cabinet_id`` with
  ``project_id`` NULL, ``project`` -> ``scope_id = project_id`` with ``cabinet_id`` set.
  ``plugin_instance`` is not representable, so a plugin-instance request is refused before
  any SQL with ``plugin_instance_scope_denied`` and no row is written.
* ``outcome`` is ``ready`` for an accepted resolution and ``blocked``/``quarantined`` for a
  recorded refusal; a non-ready row always carries a redacted ``reason_code`` and a ready
  row never does, mirroring ``ck_m04_scope_promotion_ready_requires_actor`` and
  ``ck_m04_scope_promotion_non_ready_requires_reason``.
* a ready row names the actor whose tenant membership was proven in the same transaction.
* all five digest columns are ``sha256:<64 hex>``.
* ``canonical_digest`` is the M04-02 accepted canonical digest: the M05 promotion record
  cites it as its ``M04_scope`` gate (``M04ScopeGateEvidence.canonical_digest``) and the
  reader compares the two, so it is computed with the *same* formula the M04-02 coordinator
  itself validates against (``ScopeMigrationCoordinator._canonical_digest``): canonical
  schema version, typed scope identity, owner state. A ready row can therefore never carry
  a digest the coordinator would not have accepted, and a refusal row carries the digest of
  a ``null`` owner state, which is not an acceptance digest.
* ``revision_id`` is the append-only identity inside the partition
  (``uq_m04_scope_promotion_revision``): an exact repeat is a replay that returns the
  recorded row, while different content under the same revision is a typed
  :class:`MigrationEvidenceConflict` -- never an overwrite.

Nothing raw is persisted
------------------------
Only identifiers, digests, counts, gate names and redacted reason codes reach a column.
``gate_evidence`` is a JSONB envelope of *derived* evidence (parent cardinalities, RLS
enforcement booleans, policy names, actor membership tuple, checkpoint identity); its
digest is ``gate_evidence_digest``. Source payloads, manifests, tokens and request
material never enter the envelope, and the writer refuses a key that could carry them
(``redaction_violation:*``) instead of silently dropping evidence.

Fail-closed boundaries
----------------------
Every refusal that must happen before SQL (foreign tenant, untrusted writer generation,
plugin-instance scope, missing actor for a ready row, missing reason for a refusal,
malformed digest, registry identity absent) is raised by
:func:`scope_evidence_row_values`, which runs before the transaction is opened. The write
itself is one transaction whose tenant context is installed and read back by
``evidence_ports.bind_evidence_transaction``, and the stored row is re-read inside that
transaction and compared field by field (``m04_scope_evidence_readback_mismatch``).

This module deliberately does not wire itself into ``composition.py`` and does not touch
``evidence_ports.py``; both are owned by other tasks.

Open gaps this writer does not close
------------------------------------
* ``composition.py`` still does not construct the writer, so nothing calls it in production
  yet (the module is import-safe and fail-closed on its own).
* ``evidence_ports.SCHEMA_BLOCKERS`` still lists ``m04_scope_evidence_writer_absent``; that
  text is stale now but the module owns it.
* The table's RLS policy is installed by the follow-up registry re-apply revision
  (``2026110313`` only creates the table), so the database-side append-only gate and the
  policy are still pending, exactly as the model records in
  ``M04_SCOPE_EVIDENCE_DEFERRED``.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, AsyncIterator, Mapping

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.evidence_ports import (
    REDACTION,
    AppendPlan,
    MigrationEvidenceBlocked,
    MigrationEvidenceConflict,
    MigrationEvidenceCorrupt,
    TrustedEvidenceScope,
    bind_evidence_transaction,
    digest,
    is_sha256,
    m04_scope_anchor,
)
from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    ScopeActorMembershipEvidence,
    ScopeParentEvidence,
    ScopeResolution,
    ScopeResolutionEvidence,
    ScopeResolutionResult,
    ScopeRlsEvidence,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    ScopeAnchors,
    ScopeKind,
    ScopeRegistryEntry,
)
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
)

#: Envelope identity of the redacted evidence this writer appends. A future reader can
#: tell a row this writer produced from a foreign row without decoding the columns.
M04_SCOPE_EVIDENCE_SCHEMA = "m04-scope-evidence.v1"
M04_SCOPE_EVIDENCE_KIND = "m04_scope_promotion_evidence"

#: Column widths, taken from ``M04ScopePromotionEvidenceRow``, not guessed.
M04_SCOPE_EVIDENCE_ID_WIDTH = 80
M04_SCOPE_EVIDENCE_REVISION_WIDTH = 80
M04_SCOPE_EVIDENCE_TENANT_WIDTH = 40
M04_SCOPE_EVIDENCE_SCOPE_ID_WIDTH = 40
M04_SCOPE_EVIDENCE_REGISTRY_WIDTH = 40
M04_SCOPE_EVIDENCE_CANONICAL_SCHEMA_WIDTH = 32
M04_SCOPE_EVIDENCE_REASON_WIDTH = 80
M04_SCOPE_EVIDENCE_ACTOR_WIDTH = 128
M04_SCOPE_EVIDENCE_CORRELATION_WIDTH = 128
M04_SCOPE_EVIDENCE_REVIEW_WIDTH = 128
M04_SCOPE_EVIDENCE_ROLE_WIDTH = 120

#: Prefix of the deterministic row/revision identities this writer derives. Both stay
#: inside the 80-char columns (9 + 64 and 6 + 64).
M04_SCOPE_EVIDENCE_ROW_ID_PREFIX = "m04scope:"
M04_SCOPE_EVIDENCE_REVISION_ID_PREFIX = "sha256:"


#: ``m04_scope_promotion_evidence`` admits exactly these three states; the two oldest
#: promotion tables stay ``('blocked','quarantined')`` on purpose.
class ScopeEvidenceOutcome(StrEnum):
    """The stored ``outcome`` vocabulary of the table."""

    BLOCKED = "blocked"
    QUARANTINED = "quarantined"
    READY = "ready"


#: The same vocabulary as literal strings, exposed so a test or a composition reviewer can
#: compare the writer's output with the model's ``ck_m04_scope_promotion_outcome`` without
#: parsing that check text.
SCOPE_EVIDENCE_OUTCOMES = tuple(item.value for item in ScopeEvidenceOutcome)


#: M04-02 verdict -> stored outcome. ``ACTIVE`` is acceptance; ``QUARANTINED`` is the
#: resolver's own quarantine; ``DENY`` and ``DEFERRED`` are recorded refusals (a deferred
#: plugin-instance scope never becomes acceptance in this table).
_OUTCOME_BY_RESOLUTION: Mapping[ScopeResolution, ScopeEvidenceOutcome] = {
    ScopeResolution.ACTIVE: ScopeEvidenceOutcome.READY,
    ScopeResolution.DENY: ScopeEvidenceOutcome.BLOCKED,
    ScopeResolution.DEFERRED: ScopeEvidenceOutcome.BLOCKED,
    ScopeResolution.QUARANTINED: ScopeEvidenceOutcome.QUARANTINED,
}

#: Exact, stable refusal texts. They mirror the database check names where a check exists,
#: so an operator reading a refusal can find the constraint it protects.
PLUGIN_INSTANCE_SCOPE_DENIED = "plugin_instance_scope_denied"
M04_SCOPE_EVIDENCE_TENANT_MISSING = "m04_scope_evidence_tenant_missing"
M04_SCOPE_EVIDENCE_PARTITION_OUTSIDE_TRUSTED_TENANT = (
    "m04_scope_evidence_partition_outside_trusted_tenant"
)
M04_SCOPE_EVIDENCE_SCOPE_SHAPE_INVALID = "m04_scope_evidence_scope_shape_invalid"
M04_SCOPE_EVIDENCE_SCOPE_WIDTH_INVALID = "m04_scope_evidence_scope_id_exceeds_column_width"
M04_SCOPE_EVIDENCE_TENANT_WIDTH_INVALID = "m04_scope_evidence_tenant_id_exceeds_column_width"
M04_SCOPE_EVIDENCE_WRITER_GENERATION_MISSING = "m04_scope_evidence_writer_generation_missing"
M04_SCOPE_EVIDENCE_WRITER_GENERATION_NOT_TRUSTED = (
    "m04_scope_evidence_writer_generation_not_trusted"
)
M04_SCOPE_EVIDENCE_CHECKPOINT_GENERATION_MISMATCH = (
    "m04_scope_evidence_checkpoint_generation_mismatch"
)
M04_SCOPE_EVIDENCE_CORRELATION_INVALID = "m04_scope_evidence_correlation_id_invalid"
M04_SCOPE_EVIDENCE_CORRELATION_MISMATCH = "m04_scope_evidence_correlation_mismatch"
M04_SCOPE_EVIDENCE_RESOLUTION_INVALID = "m04_scope_evidence_resolution_invalid"
M04_SCOPE_EVIDENCE_REGISTRY_MISSING = "m04_scope_evidence_registry_id_missing"
M04_SCOPE_EVIDENCE_REGISTRY_WIDTH_INVALID = "m04_scope_evidence_registry_id_exceeds_column_width"
M04_SCOPE_EVIDENCE_READY_REQUIRES_EVIDENCE = "m04_scope_evidence_ready_requires_evidence"
M04_SCOPE_EVIDENCE_READY_REQUIRES_RESOLVED_ENTRY = (
    "m04_scope_evidence_ready_requires_resolved_entry"
)
M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTIVE_OWNER = "m04_scope_evidence_ready_requires_active_owner"
M04_SCOPE_EVIDENCE_READY_REQUIRES_TRANSACTION = (
    "m04_scope_evidence_ready_requires_same_transaction"
)
M04_SCOPE_EVIDENCE_READY_REQUIRES_PARENT = "m04_scope_evidence_ready_requires_parent_proof"
M04_SCOPE_EVIDENCE_READY_REQUIRES_RLS = "m04_scope_evidence_ready_requires_rls_proof"
M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR = "m04_scope_evidence_ready_requires_actor"
M04_SCOPE_EVIDENCE_READY_ACTOR_MISMATCH = "m04_scope_evidence_ready_actor_membership_mismatch"
M04_SCOPE_EVIDENCE_NON_READY_REQUIRES_REASON = (
    "m04_scope_evidence_non_ready_requires_reason_code"
)
M04_SCOPE_EVIDENCE_REASON_INVALID = "m04_scope_evidence_reason_code_invalid"
M04_SCOPE_EVIDENCE_ACTOR_INVALID = "m04_scope_evidence_actor_id_invalid"
M04_SCOPE_EVIDENCE_DIGEST_INVALID = "m04_scope_evidence_digest_invalid"
M04_SCOPE_EVIDENCE_CANONICAL_DIGEST_DISAGREES = (
    "m04_scope_evidence_canonical_digest_disagrees_with_resolution"
)
M04_SCOPE_EVIDENCE_REVISION_WIDTH_INVALID = "m04_scope_evidence_revision_id_exceeds_column_width"
M04_SCOPE_EVIDENCE_ID_WIDTH_INVALID = "m04_scope_evidence_row_id_exceeds_column_width"
M04_SCOPE_EVIDENCE_RLS_IDENTITY_INVALID = "m04_scope_evidence_rls_identity_exceeds_column_width"
M04_SCOPE_EVIDENCE_REVISION_CYCLE = "m04_scope_evidence_previous_revision_is_itself"
M04_SCOPE_EVIDENCE_PREVIOUS_REVISION_MISSING = "m04_scope_evidence_previous_revision_missing"
M04_SCOPE_EVIDENCE_RECORDED_AT_INVALID = "m04_scope_evidence_recorded_at_invalid"
M04_SCOPE_EVIDENCE_REDACTION_VIOLATION = "redaction_violation:m04_scope_evidence"
M04_SCOPE_EVIDENCE_REVISION_CONTENT_CONFLICT = "m04_scope_evidence_revision_content_conflict"
M04_SCOPE_EVIDENCE_CONCURRENT_APPEND_CONFLICT = "m04_scope_evidence_concurrent_append_conflict"
M04_SCOPE_EVIDENCE_CONSTRAINT_VIOLATION = "m04_scope_evidence_constraint_violation"
M04_SCOPE_EVIDENCE_REGISTRY_REFERENCE_INVALID = (
    "m04_scope_evidence_registry_reference_invalid"
)
M04_SCOPE_EVIDENCE_READBACK_MISSING = "m04_scope_evidence_readback_missing"
M04_SCOPE_EVIDENCE_READBACK_MISMATCH = "m04_scope_evidence_readback_mismatch"

#: Placeholders for the two RLS identity columns, which are ``NOT NULL`` and non-empty for
#: every row. A refusal row may legitimately have no reviewed RLS proof, and inventing a
#: review id would be a lie, so the writer states the absence explicitly. A ``ready`` row
#: can never carry them: :func:`_ready_evidence` refuses an unproven RLS proof first.
UNPROVEN_RLS_MARKER_PREFIX = "unproven:"
UNPROVEN_RLS_SECURITY_REVIEW_ID = UNPROVEN_RLS_MARKER_PREFIX + "rls_evidence_missing"
UNPROVEN_RLS_DATABASE_ROLE = UNPROVEN_RLS_MARKER_PREFIX + "database_role_missing"

#: Enforcement booleans the reviewed M04 manifest proves, in a fixed order so
#: ``rls_policy_digest`` cannot drift with a dictionary iteration order.
RLS_ENFORCEMENT_FLAGS = (
    "row_security_enabled",
    "row_security_forced",
    "tenant_policy_enforced",
    "using_policy_enforced",
    "with_check_policy_enforced",
    "missing_context_denies",
    "role_not_bypass_rls",
    "typed_parent_policy_enforced",
)

#: Membership relationships ``ScopeActorMembershipEvidence.valid_for`` accepts.
_ACTOR_RELATIONSHIPS = frozenset({"employee_membership", "company_principal_binding"})

#: Payload-bearing key markers. Byte-identical to
#: ``evidence_ports._REDACTED_KEY_MATERIAL`` on purpose: the envelope this writer persists
#: must satisfy the same redaction contract as every other migration evidence payload.
_REDACTED_KEY_MATERIAL = ("payload", "manifest", "token", "secret", "password", "body")

#: Every column that may never change after the insert: the model's
#: ``_APPEND_ONLY_FIELDS`` minus ``recorded_at``, which the database stamps. A test asserts
#: the parity, so the append-only guard and this writer cannot drift apart.
M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS = (
    "id",
    "tenant_id",
    "scope_kind",
    "scope_id",
    "cabinet_id",
    "project_id",
    "scope_registry_id",
    "revision_id",
    "previous_revision_id",
    "outcome",
    "source_snapshot_id",
    "source_snapshot_digest",
    "canonical_digest",
    "canonical_schema_version",
    "checkpoint_digest",
    "gate_evidence_digest",
    "rls_policy_digest",
    "rls_security_review_id",
    "rls_database_role",
    "gate_evidence",
    "reason_code",
    "writer_generation",
    "correlation_id",
    "actor_id",
    "canonical_positive_writes",
)


# ---------------------------------------------------------------------------
# Canonical digests
# ---------------------------------------------------------------------------


def m04_scope_canonical_identity(
    anchors: ScopeAnchors,
) -> tuple[str, str, str, str | None, str | None, str | None]:
    """Canonical identity tuple of one typed owner scope.

    Byte-identical to ``ScopeMigrationCoordinator._canonical_identity`` (including the
    always-``None`` ``plugin_instance_id`` slot), so a digest computed from it can be
    compared with the M04-02 coordinator's accepted canonical digest.
    """
    return (
        anchors.scope_kind.value,
        anchors.scope_id,
        anchors.tenant_id,
        anchors.cabinet_id,
        anchors.project_id,
        anchors.plugin_instance_id,
    )


def m04_scope_canonical_digest(
    *,
    canonical_schema_version: str,
    anchors: ScopeAnchors,
    owner_state: OwnerState | None,
) -> str:
    """Canonical digest of accepted M04-02 scope evidence (see module notes).

    ``owner_state=None`` is the refusal form: it digests a scope whose owner state was not
    proven, which no accepted M04-02 row can produce, so a ``blocked``/``quarantined`` row
    can never collide with the ``M04_scope`` gate digest of a ready partition.
    """
    return digest({
        "canonical_schema_version": canonical_schema_version,
        "identity": list(m04_scope_canonical_identity(anchors)),
        "owner_state": owner_state.value if owner_state is not None else None,
    })


def scope_checkpoint_digest(checkpoint: BackfillCheckpoint) -> str:
    """Deterministic digest of one M04-02 backfill checkpoint.

    ``BackfillCheckpoint`` exposes ``stream_key``/``lineage_key`` but no digest, and the
    type lives outside this task's file zone, so the writer digests its whole redacted
    content here instead of re-inventing a per-caller convention.
    """
    if not isinstance(checkpoint, BackfillCheckpoint):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_DIGEST_INVALID,))
    return digest({
        "tenant_id": checkpoint.tenant_id,
        "stream_kind": checkpoint.stream_kind,
        "source_snapshot_id": checkpoint.source_snapshot_id,
        "source_schema_version": checkpoint.source_schema_version,
        "source_snapshot_digest": checkpoint.source_snapshot_digest,
        "canonical_schema_version": checkpoint.canonical_schema_version,
        "after_source_id": checkpoint.after_source_id,
        "sequence": checkpoint.sequence,
        "writer_generation": checkpoint.writer_generation,
    })


def m04_scope_evidence_revision_id(*, values: Mapping[str, Any]) -> str:
    """Append-only identity of one evidence revision inside its partition.

    Derived from the immutable evidence content, so replaying the same resolution names the
    same revision (a replay) while a changed digest names a new revision (a forward append,
    never an overwrite of the older one).
    """
    return M04_SCOPE_EVIDENCE_REVISION_ID_PREFIX + digest([
        values["tenant_id"],
        values["scope_kind"],
        values["scope_id"],
        values["scope_registry_id"],
        values["outcome"],
        values["source_snapshot_id"],
        values["source_snapshot_digest"],
        values["canonical_digest"],
        values["checkpoint_digest"],
        values["rls_policy_digest"],
        values["reason_code"],
        values["actor_id"],
        values["writer_generation"],
    ]).removeprefix("sha256:")


def m04_scope_evidence_row_id(
    *, tenant_id: str, scope_kind: str, scope_id: str, revision_id: str
) -> str:
    """Deterministic primary key of one partition revision (``m04scope:`` + 64 hex)."""
    return M04_SCOPE_EVIDENCE_ROW_ID_PREFIX + digest([
        tenant_id,
        scope_kind,
        scope_id,
        revision_id,
    ]).removeprefix("sha256:")


# ---------------------------------------------------------------------------
# The request offered for append
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ScopeEvidenceWrite:
    """One M04-02 resolution offered to the append-only table.

    ``anchors`` is the typed owner scope the resolver was asked about, ``resolution`` its
    verdict, ``evidence`` the transaction-bound proof bundle (required for acceptance) and
    ``checkpoint`` the M04-02 stream checkpoint the resolution ran under. The trusted
    :class:`TrustedEvidenceScope` is supplied to the writer, never by this object, so a
    request cannot assert its own tenant or writer epoch.
    """

    anchors: ScopeAnchors
    resolution: ScopeResolutionResult
    checkpoint: BackfillCheckpoint
    scope_registry_id: str
    writer_generation: int
    correlation_id: str
    evidence: ScopeResolutionEvidence | None = None
    actor_id: str | None = None
    expected_canonical_digest: str | None = None
    revision_id: str | None = None
    previous_revision_id: str | None = None
    recorded_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ScopeEvidenceAppend:
    """Outcome of one ``append``: the row that now holds the evidence."""

    status: str
    row_id: str
    revision_id: str
    outcome: str
    canonical_digest: str
    recorded_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.status not in {"inserted", "replayed"}:
            raise ValueError("a scope evidence append is either an insert or a replay")

    @property
    def inserted(self) -> bool:
        return self.status == "inserted"

    @property
    def replayed(self) -> bool:
        return self.status == "replayed"


# ---------------------------------------------------------------------------
# Redacted evidence helpers
# ---------------------------------------------------------------------------


def _assert_redacted(mapping: Mapping[str, Any], *, where: str) -> None:
    """Refuse to persist a key that could carry payload material.

    Stricter than a filter and deeper than ``evidence_ports._assert_redacted``: silently
    dropping evidence would hide a redaction bug, and a nested envelope is exactly where a
    raw source payload would hide. Every dict/list level is walked.
    """
    for key, value in mapping.items():
        lowered = str(key).lower()
        for marker in _REDACTED_KEY_MATERIAL:
            if marker in lowered:
                raise MigrationEvidenceBlocked(
                    (f"{M04_SCOPE_EVIDENCE_REDACTION_VIOLATION}:{where}:{key}",)
                )
        if isinstance(value, Mapping):
            _assert_redacted(value, where=f"{where}:{key}")
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, Mapping):
                    _assert_redacted(item, where=f"{where}:{key}:{index}")


def _blank(value: object) -> bool:
    return not isinstance(value, str) or not value.strip()


def _rls_proof(rls: ScopeRlsEvidence | None) -> dict[str, Any]:
    """Redacted RLS proof of one scope, with a fixed key set for every outcome."""
    return {
        "review_id": UNPROVEN_RLS_SECURITY_REVIEW_ID
        if rls is None or _blank(rls.security_review_id)
        else rls.security_review_id.strip(),
        "database_role": UNPROVEN_RLS_DATABASE_ROLE
        if rls is None or _blank(rls.database_role)
        else rls.database_role.strip(),
        "policy_names": sorted(
            name.strip() for name in (rls.policy_names if rls is not None else ()) if isinstance(name, str)
        ),
        "transaction_id": rls.transaction_id if rls is not None else None,
        "scope": {
            "tenant_id": rls.tenant_id if rls is not None else None,
            "cabinet_id": rls.cabinet_id if rls is not None else None,
            "project_id": rls.project_id if rls is not None else None,
        },
        "enforcement": {
            flag: bool(getattr(rls, flag, False)) if rls is not None else False
            for flag in RLS_ENFORCEMENT_FLAGS
        },
    }


def rls_policy_digest(rls: ScopeRlsEvidence | None) -> str:
    """``sha256`` digest of the reviewed RLS proof actually observed for the scope."""
    return digest(_rls_proof(rls))


def rls_identity(rls: ScopeRlsEvidence | None) -> tuple[str, str]:
    """The two non-empty RLS identity columns, as stored."""
    proof = _rls_proof(rls)
    return str(proof["review_id"]), str(proof["database_role"])


def _parent_proof(
    parent: ScopeParentEvidence | None,
    anchors: ScopeAnchors,
    proof: ParentCompatibilityProof | None,
) -> dict[str, Any]:
    """Redacted parent-chain evidence: cardinalities, lifecycle flags, gate reason."""
    if parent is None:
        return {
            "transaction_id": None,
            "reason_code": None,
            "counts": None,
            "lifecycle": None,
            "compatibility": None,
        }
    counts = {
        "tenant_matches": parent.tenant_matches,
        "cabinet_matches": parent.cabinet_matches,
        "active_cabinet_memberships": parent.active_cabinet_memberships,
        "project_matches": parent.project_matches,
        "active_registry_matches": parent.active_registry_matches,
    }
    lifecycle = {
        "tenant_deleted": bool(parent.tenant_deleted),
        "tenant_archived": bool(parent.tenant_archived),
        "cabinet_deleted": bool(parent.cabinet_deleted),
        "cabinet_archived": bool(parent.cabinet_archived),
        "project_deleted": bool(parent.project_deleted),
        "project_archived": bool(parent.project_archived),
        "cabinet_membership_kind": parent.cabinet_membership_kind,
    }
    return {
        "transaction_id": parent.transaction_id,
        "reason_code": parent.invalid_reason_for(anchors),
        "counts": counts,
        "lifecycle": lifecycle,
        "compatibility": proof.model_dump(mode="json") if proof is not None else None,
    }


def _actor_proof(actor: ScopeActorMembershipEvidence | None) -> dict[str, Any]:
    """Redacted actor-membership tuple; identifiers and counts only."""
    if actor is None:
        return {
            "actor_id": None,
            "tenant_id": None,
            "relationship_kind": None,
            "relationship_matches": None,
            "active": False,
            "transaction_id": None,
        }
    return {
        "actor_id": (actor.actor_id or None),
        "tenant_id": actor.tenant_id,
        "relationship_kind": actor.relationship_kind,
        "relationship_matches": actor.relationship_matches,
        "active": bool(actor.active),
        "transaction_id": actor.transaction_id,
    }


def _gate_evidence(
    *,
    write: ScopeEvidenceWrite,
    outcome: ScopeEvidenceOutcome,
    reason_code: str | None,
    anchor: tuple[str, str, str | None, str | None],
    owner_state: OwnerState | None,
    canonical_schema_version: str,
    checkpoint_digest: str,
    rls: ScopeRlsEvidence | None,
) -> dict[str, Any]:
    """Redacted, derived evidence envelope; its digest is ``gate_evidence_digest``."""
    scope_kind, scope_id, cabinet_id, project_id = anchor
    evidence = write.evidence
    entry: ScopeRegistryEntry | None = evidence.entry if evidence is not None else None
    envelope = {
        "schema": M04_SCOPE_EVIDENCE_SCHEMA,
        "kind": M04_SCOPE_EVIDENCE_KIND,
        "outcome": outcome.value,
        "reason_code": reason_code,
        "scope": {
            "kind": scope_kind,
            "scope_id": scope_id,
            "cabinet_id": cabinet_id,
            "project_id": project_id,
            "tenant_id": write.anchors.tenant_id,
        },
        "registry": {
            "scope_registry_id": write.scope_registry_id.strip(),
            "owner_state": owner_state.value if owner_state is not None else None,
            "source_digest": entry.provenance.payload_digest
            if entry is not None and is_sha256(entry.provenance.payload_digest)
            else None,
            "canonical_schema_version": canonical_schema_version,
        },
        "resolution": {
            "verdict": write.resolution.outcome.value,
            "reason_code": write.resolution.reason_code,
            "correlation_id": write.resolution.correlation_id,
        },
        "parent": _parent_proof(
            evidence.parent_evidence if evidence is not None else None,
            write.anchors,
            evidence.parent_proof if evidence is not None else None,
        ),
        "actor": _actor_proof(
            evidence.actor_membership_evidence if evidence is not None else None
        ),
        "rls": _rls_proof(rls),
        "checkpoint": {
            "source_snapshot_id": write.checkpoint.source_snapshot_id,
            "source_schema_version": write.checkpoint.source_schema_version,
            "source_snapshot_digest": write.checkpoint.source_snapshot_digest,
            "canonical_schema_version": canonical_schema_version,
            "stream_kind": write.checkpoint.stream_kind,
            "after_source_id": write.checkpoint.after_source_id,
            "sequence": write.checkpoint.sequence,
            "writer_generation": write.checkpoint.writer_generation,
            "digest": checkpoint_digest,
        },
        "writer_generation": write.writer_generation,
        "redaction": REDACTION,
    }
    _assert_redacted(envelope, where="gate_evidence")
    return envelope


# ---------------------------------------------------------------------------
# Ready-path re-verification
# ---------------------------------------------------------------------------


def _rls_is_proven(rls: ScopeRlsEvidence, anchors: ScopeAnchors) -> bool:
    """``ScopeRlsEvidence.valid_for`` without the caller-supplied request context."""
    return bool(
        rls.tenant_id == anchors.tenant_id
        and rls.cabinet_id == anchors.cabinet_id
        and rls.project_id == anchors.project_id
        and not _blank(rls.database_role)
        and not _blank(rls.security_review_id)
        and rls.policy_names
        and all(isinstance(name, str) and name.strip() for name in rls.policy_names)
        and all(bool(getattr(rls, flag)) for flag in RLS_ENFORCEMENT_FLAGS)
    )


def _actor_is_proven(actor: ScopeActorMembershipEvidence, anchors: ScopeAnchors) -> bool:
    """``ScopeActorMembershipEvidence.valid_for`` without the request context."""
    return bool(
        not _blank(actor.actor_id)
        and actor.tenant_id == anchors.tenant_id
        and actor.relationship_kind in _ACTOR_RELATIONSHIPS
        and actor.relationship_matches == 1
        and actor.active
    )


def _ready_evidence(write: ScopeEvidenceWrite) -> tuple[ScopeRegistryEntry, str]:
    """Re-verify an ``ACTIVE`` verdict and return its entry plus the proven actor.

    A verdict of ``ACTIVE`` whose bundle does not prove acceptance is a *contradiction*,
    not a refusal to record: writing a ``blocked`` row for it would launder an untrustworthy
    resolution into durable evidence, so this refuses and appends nothing.
    """
    evidence = write.evidence
    if evidence is None:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_EVIDENCE,))
    entry = evidence.entry
    resolved = write.resolution.entry
    if (
        resolved is None
        or resolved.anchors != entry.anchors
        or resolved.owner_state != entry.owner_state
    ):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_RESOLVED_ENTRY,))
    if entry.owner_state is not OwnerState.ACTIVE or entry.anchors != write.anchors:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTIVE_OWNER,))
    transactions = {
        evidence.parent_compatibility_transaction_id,
        evidence.parent_evidence.transaction_id,
        evidence.rls_evidence.transaction_id,
        evidence.actor_membership_evidence.transaction_id,
    }
    if len(transactions) != 1 or any(_blank(value) for value in transactions):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_TRANSACTION,))
    if (
        not evidence.parent_proof.valid_for_scope(entry.anchors.scope_kind)
        or evidence.parent_evidence.invalid_reason_for(entry.anchors) is not None
    ):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_PARENT,))
    if not _rls_is_proven(evidence.rls_evidence, write.anchors):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_RLS,))
    actor = evidence.actor_membership_evidence
    if not _actor_is_proven(actor, write.anchors):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR,))
    proven_actor = str(actor.actor_id).strip()
    if _blank(write.actor_id):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR,))
    if str(write.actor_id).strip() != proven_actor:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_READY_ACTOR_MISMATCH,))
    return entry, proven_actor


# ---------------------------------------------------------------------------
# Pure row builder
# ---------------------------------------------------------------------------


def scope_evidence_row_values(
    write: ScopeEvidenceWrite, *, scope: TrustedEvidenceScope
) -> dict[str, Any]:
    """Build the exact column mapping of one append, refusing before any SQL.

    Every refusal here is a named :class:`MigrationEvidenceBlocked` (or its conflict
    subclass), so a caller cannot persist a row the reader would reject and cannot widen a
    partition by asserting a tenant of its own.
    """
    if not isinstance(scope, TrustedEvidenceScope) or not scope.usable:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_TENANT_MISSING,))
    if not isinstance(write, ScopeEvidenceWrite):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_RESOLUTION_INVALID,))
    if not isinstance(write.resolution, ScopeResolutionResult):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_RESOLUTION_INVALID,))
    try:
        # Fail closed on an unknown provider verdict: the table's check admits only three
        # states, and a string that is not one of the resolver's outcomes has no mapping.
        verdict = ScopeResolution(write.resolution.outcome)
    except ValueError as exc:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_RESOLUTION_INVALID,)) from exc
    if not isinstance(write.anchors, ScopeAnchors):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_SCOPE_SHAPE_INVALID,))
    anchors = write.anchors
    if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
        # An instance cannot own registry evidence: M04-02 refuses it and the table's
        # ``ck_m04_scope_promotion_scope_kind`` cannot represent it either.
        raise MigrationEvidenceBlocked((PLUGIN_INSTANCE_SCOPE_DENIED,))
    anchor = m04_scope_anchor(
        tenant_id=anchors.tenant_id,
        cabinet_id=anchors.cabinet_id,
        project_id=anchors.project_id,
    )
    if anchor is None or (anchor[0], anchor[1]) != (anchors.scope_kind.value, anchors.scope_id):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_SCOPE_SHAPE_INVALID,))

    tenant_id = anchors.tenant_id.strip()
    if len(tenant_id) > M04_SCOPE_EVIDENCE_TENANT_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_TENANT_WIDTH_INVALID,))
    if len(anchor[1]) > M04_SCOPE_EVIDENCE_SCOPE_ID_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_SCOPE_WIDTH_INVALID,))
    checkpoint = write.checkpoint
    if not isinstance(checkpoint, BackfillCheckpoint):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_DIGEST_INVALID,))
    if tenant_id != scope.tenant_id or checkpoint.tenant_id != scope.tenant_id:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_PARTITION_OUTSIDE_TRUSTED_TENANT,))

    generation = scope.writer_generation
    if not isinstance(generation, int) or isinstance(generation, bool) or generation <= 0:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_WRITER_GENERATION_MISSING,))
    if write.writer_generation != generation:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_WRITER_GENERATION_NOT_TRUSTED,))
    if checkpoint.writer_generation != generation:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_CHECKPOINT_GENERATION_MISMATCH,))

    if _blank(write.correlation_id) or len(str(write.correlation_id).strip()) > (
        M04_SCOPE_EVIDENCE_CORRELATION_WIDTH
    ):
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_CORRELATION_INVALID,))
    correlation_id = str(write.correlation_id).strip()
    if write.resolution.correlation_id != correlation_id:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_CORRELATION_MISMATCH,))

    registry_id = str(write.scope_registry_id or "").strip()
    if not registry_id:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REGISTRY_MISSING,))
    if len(registry_id) > M04_SCOPE_EVIDENCE_REGISTRY_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REGISTRY_WIDTH_INVALID,))

    outcome = _OUTCOME_BY_RESOLUTION.get(verdict)
    if outcome is None:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_RESOLUTION_INVALID,))

    owner_state: OwnerState | None = None
    actor_id: str | None = None
    reason_code: str | None = None
    if outcome is ScopeEvidenceOutcome.READY:
        entry, proven_actor = _ready_evidence(write)
        owner_state = entry.owner_state
        actor_id = proven_actor
    else:
        reason_code = write.resolution.reason_code
        if _blank(reason_code):
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_NON_READY_REQUIRES_REASON,))
        reason_code = str(reason_code).strip()
        if len(reason_code) > M04_SCOPE_EVIDENCE_REASON_WIDTH:
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REASON_INVALID,))
        evidence = write.evidence
        if evidence is not None and evidence.entry.owner_state is not OwnerState.ACTIVE:
            owner_state = evidence.entry.owner_state
        if write.actor_id is not None:
            if _blank(write.actor_id) or len(str(write.actor_id).strip()) > (
                M04_SCOPE_EVIDENCE_ACTOR_WIDTH
            ):
                raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_ACTOR_INVALID,))
            actor_id = str(write.actor_id).strip()
    if actor_id is not None and len(actor_id) > M04_SCOPE_EVIDENCE_ACTOR_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_ACTOR_INVALID,))

    canonical_schema_version = checkpoint.canonical_schema_version
    canonical_digest = m04_scope_canonical_digest(
        canonical_schema_version=canonical_schema_version,
        anchors=anchors,
        owner_state=owner_state,
    )
    if write.expected_canonical_digest is not None:
        if not is_sha256(write.expected_canonical_digest):
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_DIGEST_INVALID,))
        if write.expected_canonical_digest != canonical_digest:
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_CANONICAL_DIGEST_DISAGREES,))

    checkpoint_digest = scope_checkpoint_digest(checkpoint)
    rls = write.evidence.rls_evidence if write.evidence is not None else None
    review_id, database_role = rls_identity(rls)
    if len(review_id) > M04_SCOPE_EVIDENCE_REVIEW_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_RLS_IDENTITY_INVALID,))
    if len(database_role) > M04_SCOPE_EVIDENCE_ROLE_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_RLS_IDENTITY_INVALID,))
    policy_digest = rls_policy_digest(rls)

    envelope = _gate_evidence(
        write=write,
        outcome=outcome,
        reason_code=reason_code,
        anchor=anchor,
        owner_state=owner_state,
        canonical_schema_version=canonical_schema_version,
        checkpoint_digest=checkpoint_digest,
        rls=rls,
    )

    values: dict[str, Any] = {
        "id": "",
        "tenant_id": tenant_id,
        "scope_kind": anchor[0],
        "scope_id": anchor[1],
        "cabinet_id": anchor[2],
        "project_id": anchor[3],
        "scope_registry_id": registry_id,
        "revision_id": "",
        "previous_revision_id": write.previous_revision_id,
        "outcome": outcome.value,
        "source_snapshot_id": checkpoint.source_snapshot_id,
        "source_snapshot_digest": checkpoint.source_snapshot_digest,
        "canonical_digest": canonical_digest,
        "canonical_schema_version": canonical_schema_version,
        "checkpoint_digest": checkpoint_digest,
        "gate_evidence_digest": digest(envelope),
        "rls_policy_digest": policy_digest,
        "rls_security_review_id": review_id,
        "rls_database_role": database_role,
        "gate_evidence": envelope,
        "reason_code": reason_code,
        "writer_generation": write.writer_generation,
        "correlation_id": correlation_id,
        "actor_id": actor_id,
        "canonical_positive_writes": False,
    }
    revision_id = str(write.revision_id or m04_scope_evidence_revision_id(values=values)).strip()
    if not revision_id:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REVISION_WIDTH_INVALID,))
    if len(revision_id) > M04_SCOPE_EVIDENCE_REVISION_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REVISION_WIDTH_INVALID,))
    if write.previous_revision_id is not None:
        previous = str(write.previous_revision_id).strip()
        if not previous:
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REVISION_WIDTH_INVALID,))
        if len(previous) > M04_SCOPE_EVIDENCE_REVISION_WIDTH:
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REVISION_WIDTH_INVALID,))
        if previous == revision_id:
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REVISION_CYCLE,))
        values["previous_revision_id"] = previous
    values["revision_id"] = revision_id
    values["id"] = m04_scope_evidence_row_id(
        tenant_id=tenant_id,
        scope_kind=anchor[0],
        scope_id=anchor[1],
        revision_id=revision_id,
    )
    if len(values["id"]) > M04_SCOPE_EVIDENCE_ID_WIDTH:
        raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_ID_WIDTH_INVALID,))
    if write.recorded_at is not None:
        recorded_at = write.recorded_at
        if (
            not isinstance(recorded_at, datetime)
            or recorded_at.tzinfo is None
            or recorded_at.utcoffset() is None
        ):
            raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_RECORDED_AT_INVALID,))
        values["recorded_at"] = recorded_at
    return values


# ---------------------------------------------------------------------------
# Append decision (pure)
# ---------------------------------------------------------------------------


def _differing_fields(stored: Any, values: Mapping[str, Any]) -> tuple[str, ...]:
    """Immutable fields whose stored value disagrees with the intended value."""
    differing: list[str] = []
    for name in M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS:
        if getattr(stored, name, None) != values.get(name):
            differing.append(name)
    return tuple(differing)


def plan_scope_evidence_append(
    *, stored: Any | None, values: Mapping[str, Any]
) -> AppendPlan:
    """Decide insert, exact replay or typed conflict from the row already read.

    ``stored`` is ``None`` for a new revision. An identical immutable content is a replay
    (the original row is returned, nothing is written); anything else under the same
    ``revision_id`` is a conflict and never an overwrite.
    """
    if stored is None:
        return AppendPlan("insert")
    differing = _differing_fields(stored, values)
    if not differing:
        return AppendPlan("replay", stored=stored)
    return AppendPlan(
        "conflict",
        (
            M04_SCOPE_EVIDENCE_REVISION_CONTENT_CONFLICT,
            *(f"{M04_SCOPE_EVIDENCE_REVISION_CONTENT_CONFLICT}:{name}" for name in differing),
        ),
        stored=stored,
    )


# ---------------------------------------------------------------------------
# Writer
# ---------------------------------------------------------------------------


#: Fragments identifying which database constraint rejected the insert. Classified by name
#: rather than swallowed: a lost insert race is a conflict to retry, an unproven registry
#: reference or a check violation is a bug that must not be reported as "try again".
_UNIQUE_RACE_FRAGMENTS = ("uq_m04_scope_promotion_revision", "duplicate key value")
_REGISTRY_REFERENCE_FRAGMENTS = ("fk_m04_scope_promotion_registry", "foreign key")


def _classify_integrity_error(exc: IntegrityError) -> Exception:
    """Map one rejected insert onto the refusal class its constraint means."""
    message = str(getattr(exc, "orig", "") or exc).lower()
    if any(fragment in message for fragment in _UNIQUE_RACE_FRAGMENTS):
        return MigrationEvidenceConflict((M04_SCOPE_EVIDENCE_CONCURRENT_APPEND_CONFLICT,))
    if any(fragment in message for fragment in _REGISTRY_REFERENCE_FRAGMENTS):
        return MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_REGISTRY_REFERENCE_INVALID,))
    return MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_CONSTRAINT_VIOLATION,))


class _WriterBoundaries:
    """One database transaction per append, always tenant-bound first."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], scope: TrustedEvidenceScope
    ) -> None:
        if not isinstance(scope, TrustedEvidenceScope) or not scope.usable:
            raise ValueError("the M04 scope evidence writer requires a resolved trusted evidence scope")
        self._session_factory = session_factory
        self._scope = scope

    @property
    def scope(self) -> TrustedEvidenceScope:
        return self._scope

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            async with session.begin():
                await bind_evidence_transaction(session, self._scope)
                yield session


class SqlM04ScopeEvidenceWriter:
    """``m04_scope_promotion_evidence`` producer: one trusted, append-only row per revision.

    The tenant and writer generation come from the injected :class:`TrustedEvidenceScope`;
    the request supplies the M04-02 resolution only. Every append is one transaction whose
    tenant GUCs are installed and read back before the row is written, and the row is read
    back inside the same transaction.
    """

    def __init__(
        self, *, session_factory: async_sessionmaker[AsyncSession], scope: TrustedEvidenceScope
    ) -> None:
        self._boundaries = _WriterBoundaries(session_factory, scope)

    @property
    def scope(self) -> TrustedEvidenceScope:
        return self._boundaries.scope

    async def append(self, write: ScopeEvidenceWrite) -> ScopeEvidenceAppend:
        """Append (or replay) the evidence row of one M04-02 resolution."""
        values = scope_evidence_row_values(write, scope=self._boundaries.scope)
        async with self._boundaries.write() as session:
            stored = await self._load_revision(session, values)
            plan = plan_scope_evidence_append(stored=stored, values=values)
            if plan.is_conflict:
                raise MigrationEvidenceConflict(plan.blockers)
            if plan.is_replay:
                return _append_result("replayed", plan.stored)
            previous = values["previous_revision_id"]
            if previous is not None and await self._load_revision(
                session, {**values, "revision_id": previous}
            ) is None:
                raise MigrationEvidenceBlocked((M04_SCOPE_EVIDENCE_PREVIOUS_REVISION_MISSING,))
            session.add(M04ScopePromotionEvidenceRow(**values))
            try:
                await session.flush()
            except IntegrityError as exc:
                raise _classify_integrity_error(exc) from exc
            readback = await self._load_revision(session, values)
            if readback is None:
                raise MigrationEvidenceCorrupt((M04_SCOPE_EVIDENCE_READBACK_MISSING,))
            differing = _differing_fields(readback, values)
            if differing:
                raise MigrationEvidenceCorrupt(
                    (
                        M04_SCOPE_EVIDENCE_READBACK_MISMATCH,
                        *(f"{M04_SCOPE_EVIDENCE_READBACK_MISMATCH}:{name}" for name in differing),
                    )
                )
            return _append_result("inserted", readback)

    async def _load_revision(
        self, session: AsyncSession, values: Mapping[str, Any]
    ) -> Any | None:
        """The stored revision of the partition named by ``values``, if any."""
        return await session.scalar(
            select(M04ScopePromotionEvidenceRow)
            .where(
                M04ScopePromotionEvidenceRow.tenant_id == values["tenant_id"],
                M04ScopePromotionEvidenceRow.scope_kind == values["scope_kind"],
                M04ScopePromotionEvidenceRow.scope_id == values["scope_id"],
                M04ScopePromotionEvidenceRow.revision_id == values["revision_id"],
            )
            .limit(1)
        )


def _append_result(status: str, stored: Any) -> ScopeEvidenceAppend:
    return ScopeEvidenceAppend(
        status=status,
        row_id=str(stored.id),
        revision_id=str(stored.revision_id),
        outcome=str(stored.outcome),
        canonical_digest=str(stored.canonical_digest),
        recorded_at=getattr(stored, "recorded_at", None),
    )


__all__ = [
    "M04_SCOPE_EVIDENCE_ACTOR_WIDTH",
    "M04_SCOPE_EVIDENCE_CANONICAL_SCHEMA_WIDTH",
    "M04_SCOPE_EVIDENCE_IMMUTABLE_FIELDS",
    "M04_SCOPE_EVIDENCE_KIND",
    "M04_SCOPE_EVIDENCE_REGISTRY_REFERENCE_INVALID",
    "M04_SCOPE_EVIDENCE_SCHEMA",
    "PLUGIN_INSTANCE_SCOPE_DENIED",
    "RLS_ENFORCEMENT_FLAGS",
    "SCOPE_EVIDENCE_OUTCOMES",
    "ScopeEvidenceAppend",
    "ScopeEvidenceOutcome",
    "ScopeEvidenceWrite",
    "SqlM04ScopeEvidenceWriter",
    "UNPROVEN_RLS_DATABASE_ROLE",
    "UNPROVEN_RLS_SECURITY_REVIEW_ID",
    "m04_scope_canonical_digest",
    "m04_scope_canonical_identity",
    "m04_scope_evidence_revision_id",
    "m04_scope_evidence_row_id",
    "plan_scope_evidence_append",
    "rls_identity",
    "rls_policy_digest",
    "scope_checkpoint_digest",
    "scope_evidence_row_values",
]
