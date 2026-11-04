"""Seeding helpers that make the four evidence tables look like a real cutover-ready partition.

The rows are built from the contract fixtures in ``acceptance_fixtures.py`` so the tests and
the contract assertions cannot drift apart: a gate produced by ``gate()`` is exactly what
these helpers persist, and the verifiers must accept it.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

import sqlalchemy as sa
from prodavan.application.migration.m06_cutover.cutover import (
    CanonicalRepairEvidence,
    GateEvidence,
    LegacyOwnerEvidence,
    RollbackEvidence,
)
from prodavan.application.migration.m06_cutover.ports import (
    CANONICAL_REPAIR_BLOCK,
    CUTOVER_SOURCE_TAIL_TABLE,
    LEGACY_OWNER_BLOCK,
    ROLLBACK_BLOCK,
)

from .sqlite_evidence import (
    EvidenceDatabase,
    fence_values,
    m04e_evidence_values,
    m05_evidence_values,
    m06_evidence_values,
    scope_evidence_values,
)

M04_TABLE = "m04_scope_promotion_evidence"
M04E_TABLE = "m04e_definition_version_promotion_evidence"
M05_TABLE = "m05_plugin_instance_promotion_evidence"
M06_TABLE = "m06_canonical_grant_promotion_evidence"
FENCE_TABLE = "dynamic_app_capability_writer_epochs"


def legacy_owner_block(evidence: LegacyOwnerEvidence) -> dict[str, Any]:
    """The ``legacy_owner`` envelope block a trusted producer writes on the owner row."""

    return {
        "evidence_id": evidence.evidence_id,
        "evidence_digest": evidence.evidence_digest,
        "writer_epoch": evidence.writer_epoch,
        "source_snapshot_digest": evidence.source_snapshot_digest,
        "exclusive_read_owner": evidence.exclusive_read_owner,
        "exclusive_decision_owner": evidence.exclusive_decision_owner,
        "exclusive_write_owner": evidence.exclusive_write_owner,
        "legacy_claims_enabled": evidence.legacy_claims_enabled,
    }


def rollback_block(evidence: RollbackEvidence) -> dict[str, Any]:
    """The ``rollback`` envelope block a trusted producer writes on the owner row."""

    return {
        "evidence_id": evidence.evidence_id,
        "evidence_digest": evidence.evidence_digest,
        "writer_epoch": evidence.writer_epoch,
        "legacy_owner_restored": evidence.legacy_owner_restored,
        "canonical_claims_stopped": evidence.canonical_claims_stopped,
        "active_canonical_writers_remaining": evidence.active_canonical_writers_remaining,
        "unresolved_legacy_claims": evidence.unresolved_legacy_claims,
    }


def repair_block(evidence: CanonicalRepairEvidence) -> dict[str, Any]:
    """The ``canonical_repair`` envelope block a trusted producer writes on the M06 row."""

    return {
        "evidence_id": evidence.evidence_id,
        "evidence_digest": evidence.evidence_digest,
        "writer_epoch": evidence.writer_epoch,
        "parent_evidence_id": evidence.parent_evidence_id,
        "parent_evidence_digest": evidence.parent_evidence_digest,
        "unresolved_conflicts": evidence.unresolved_conflicts,
        "unknown_mappings": evidence.unknown_mappings,
        "unresolved_positive_bindings": evidence.unresolved_positive_bindings,
        "stale_generation_acceptances": evidence.stale_generation_acceptances,
        "active_canonical_writers_remaining": evidence.active_canonical_writers_remaining,
        "canonical_writes_stopped": evidence.canonical_writes_stopped,
        "audit_complete": evidence.audit_complete,
    }


async def seed_legacy_owner_row(
    env: EvidenceDatabase,
    *,
    owner: LegacyOwnerEvidence,
    canonical_digest: str,
    rollback: RollbackEvidence | None = None,
    outcome: str = "ready",
    canonical_positive_writes: bool = False,
    extra_blocks: Mapping[str, Any] | None = None,
) -> str:
    """Persist the accepted M04-02 owner marker row for the partition."""

    blocks: dict[str, Any] = {LEGACY_OWNER_BLOCK: legacy_owner_block(owner)}
    if rollback is not None:
        blocks[ROLLBACK_BLOCK] = rollback_block(rollback)
    blocks.update(dict(extra_blocks or {}))
    await env.insert(M04_TABLE, **scope_evidence_values(
        evidence_id=owner.evidence_id,
        tenant_id=owner.partition.tenant_id,
        canonical_digest=canonical_digest,
        source_snapshot_digest=owner.source_snapshot_digest,
        evidence_digest=owner.evidence_digest,
        writer_generation=owner.writer_epoch,
        outcome=outcome,
        canonical_positive_writes=canonical_positive_writes,
        gate_evidence=blocks,
    ))
    return owner.evidence_id


async def seed_gate_rows(
    env: EvidenceDatabase,
    gate: GateEvidence,
    *,
    m04_outcome: str = "ready",
    m04e_outcome: str = "ready",
    m05_outcome: str = "ready",
    m06_outcome: str = "ready",
    m04e_canonical_digest: str | None = None,
    m05_canonical_digest: str | None = None,
    m06_writer_epoch: int | None = None,
) -> None:
    """Persist the M04/M04E/M05 lineage rows and the M06 gate row for ``gate``."""

    m04, m04e, m05 = gate.m04, gate.m04e, gate.m05
    drain, delta = gate.legacy_writer_drain, gate.final_delta
    assert m04 is not None and m04e is not None and m05 is not None
    assert drain is not None and delta is not None
    partition = gate.partition

    await env.insert(M04_TABLE, **scope_evidence_values(
        evidence_id=m04.evidence_id,
        tenant_id=partition.tenant_id,
        canonical_digest=m04.canonical_digest,
        source_snapshot_digest=m04.source_snapshot_digest,
        evidence_digest=m04.evidence_digest,
        writer_generation=gate.writer_epoch or 0,
        outcome=m04_outcome,
        gate_evidence={"gate": "M04"},
    ))
    await env.insert(M04E_TABLE, **m04e_evidence_values(
        evidence_id=m04e.evidence_id,
        tenant_id=partition.tenant_id,
        definition_id=partition.app_definition_id,
        canonical_digest=m04e_canonical_digest or m04e.canonical_digest,
        source_snapshot_digest=m04e.source_snapshot_digest,
        evidence_digest=m04e.evidence_digest,
        parent_id=m04.evidence_id,
        parent_digest=m04.evidence_digest,
        outcome=m04e_outcome,
    ))
    await env.insert(M05_TABLE, **m05_evidence_values(
        evidence_id=m05.evidence_id,
        tenant_id=partition.tenant_id,
        definition_id=partition.app_definition_id,
        canonical_digest=m05_canonical_digest or m05.canonical_digest,
        source_snapshot_digest=m05.source_snapshot_digest,
        evidence_digest=m05.evidence_digest,
        parent_id=m04e.evidence_id,
        parent_digest=m04e.evidence_digest,
        writer_generation=gate.writer_epoch,
        outcome=m05_outcome,
    ))
    await env.insert(M06_TABLE, **m06_evidence_values(
        evidence_id=gate.evidence_id or "",
        tenant_id=partition.tenant_id,
        definition_id=partition.app_definition_id,
        canonical_digest=gate.canonical_digest or "",
        source_snapshot_digest=gate.source_snapshot_digest or "",
        evidence_digest=gate.evidence_digest or "",
        writer_epoch=m06_writer_epoch or gate.writer_epoch or 0,
        references=(
            (m05.evidence_id, m05.evidence_digest),
            (gate.legacy_owner_evidence_id or "", gate.legacy_owner_evidence_digest),
            (drain.evidence_id, drain.evidence_digest),
            (delta.evidence_id, delta.evidence_digest),
            ("m06-parity-report", gate.report_digest),
        ),
        outcome=m06_outcome,
    ))


async def seed_fence(
    env: EvidenceDatabase,
    *,
    tenant_id: str,
    definition_id: str,
    writer_epoch: int,
    scope_id: str = "instance-1",
    fence_id: str | None = None,
) -> None:
    """Persist one canonical writer-epoch row for the partition."""

    await env.insert(FENCE_TABLE, **fence_values(
        fence_id=fence_id or f"fence-{writer_epoch}",
        tenant_id=tenant_id,
        definition_id=definition_id,
        writer_epoch=writer_epoch,
        scope_id=scope_id,
    ))


async def attach_repair_block(env: EvidenceDatabase, evidence: CanonicalRepairEvidence,
                              *, gate: GateEvidence) -> None:
    """Add the ``canonical_repair`` block to the existing M06 gate row."""

    row = (await env.rows(M06_TABLE))[0]
    envelope = dict(row["gate_evidence"])
    envelope[CANONICAL_REPAIR_BLOCK] = repair_block(evidence)
    await env.update(
        M06_TABLE,
        where={"id": gate.evidence_id},
        gate_evidence=envelope,
    )


async def seed_source_tail(
    env: EvidenceDatabase,
    *,
    tenant_id: str,
    definition_id: str,
    change_sequence: int,
    writer_epoch: int,
) -> None:
    """Persist the live legacy source tail the cutover CAS compares against.

    ``updated_at`` follows the container's declared type: the specification shim renders
    ``TIMESTAMPTZ`` as text, while the containers built from the registered models carry a
    real ``DateTime``. A single literal cannot satisfy both, and the fixture must not
    invent a type the schema does not declare.
    """

    column = env.tables[CUTOVER_SOURCE_TAIL_TABLE].c["updated_at"]
    updated_at: Any = datetime(2026, 11, 1, 13, 0, tzinfo=UTC)
    if not isinstance(column.type, sa.DateTime):
        updated_at = updated_at.isoformat()
    await env.insert(CUTOVER_SOURCE_TAIL_TABLE, **{
        "tenant_id": tenant_id,
        "app_definition_id": definition_id,
        "change_sequence": change_sequence,
        "writer_epoch": writer_epoch,
        "updated_at": updated_at,
    })


async def set_source_tail(env: EvidenceDatabase, *, tenant_id: str, definition_id: str,
                          change_sequence: int) -> None:
    """Move the live source tail forward (a legacy writer committed one more change)."""

    await env.execute(
        f"UPDATE {CUTOVER_SOURCE_TAIL_TABLE} SET change_sequence = :seq "
        "WHERE tenant_id = :tenant_id AND app_definition_id = :app_definition_id",
        seq=change_sequence,
        tenant_id=tenant_id,
        app_definition_id=definition_id,
    )
