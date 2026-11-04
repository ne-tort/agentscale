"""Durable SQL implementations of the four evidence ports that break the cutover chain.

`docs/08-migration/integration-seams-review.md` §2/§3 records four ports whose
declarations exist with no implementation, so the M04E -> M05 -> M06 -> cutover chain
cannot pass its first link. This module supplies them over the append-only tables that
already exist and are already registered in the RLS registry
(`infrastructure/persistence/rls/policies.py` 2026110302/03/04):

============================  =====================================================
Port                          Target table(s)
============================  =====================================================
`M05HandoffPort.append_marker`            `m04e_definition_version_promotion_evidence`
`EvidenceStore` (M04E backfill)           `m04e_definition_version_promotion_evidence`
`M05PromotionRecordPort.append_immutable` `m05_plugin_instance_promotion_evidence`
`PartitionHandoffReadPort.load_partition` `m05_plugin_instance_promotion_evidence`,
                                          `m05_plugin_instances`,
                                          `m04e_definition_version_promotion_evidence`,
                                          `m06_canonical_grant_promotion_evidence`,
                                          `scope_registry` (typed anchor id),
                                          `pg_class`/`pg_policies` (M04 RLS state)
============================  =====================================================

Design rules this module is built on
-----------------------------------
1. **No in-memory path.** Every byte that leaves a port came from a committed row of a
   registered table or from the live PostgreSQL catalog. There is no dict-backed
   fallback, no test double and no `IS_TEST_DOUBLE` marker, and no constructor default
   that silently produces an empty store.
2. **Append-only.** `append_*` never issues UPDATE/DELETE. An exact repeat (same
   identity, same digest) returns the originally recorded row; a different payload that
   claims the same logical key raises :class:`MigrationEvidenceConflict`.
3. **Trusted context only.** Every statement runs inside a transaction whose
   tenant/role GUCs were set by the caller-supplied :class:`TrustedEvidenceScope` and
   read back before use. A datum whose partition is outside the bound tenant is refused
   before any SQL; a foreign partition is invisible even if the database were to allow
   the read.
4. **Redaction.** Only identifiers, digests, counts, gate names and reason codes are
   persisted. Candidate payloads, manifests, artifact bytes, tokens and raw request
   material never reach a column: the ledger codec rejects an unknown key instead of
   silently dropping it.
5. **Fail-closed absence.** A missing row yields ``None`` (read port) or a typed
   :class:`MigrationEvidenceBlocked` naming the missing artefact (write port). No empty
   object is ever synthesised.

Known schema limits (see :data:`SCHEMA_BLOCKERS`)
-------------------------------------------------
The M04E and M05 promotion tables constrain ``outcome`` to ``('blocked','quarantined')``
while both handoff contracts carry a ``ready`` status. Readiness is therefore persisted
inside the redacted ``gate_evidence`` JSON envelope and the ``outcome`` column always
reads ``blocked`` -- which is also the documented meaning of these tables ("never
enables a positive writer"). :func:`decode_*` reconstructs the contract value exactly, so
an exact retry compares equal, but a reviewer querying ``outcome`` must not read it as
the handoff status.

No table stores an accepted M04 scope/RLS *evidence row*, so
:meth:`SqlPartitionHandoffReadStore.read_partition` refuses with
``m04_scope_promotion_evidence_missing`` until that artefact exists (the M04 RLS digest
itself is verified from the live catalog against a security-reviewed manifest).
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, AsyncIterator, Mapping, Sequence

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillBatch,
    BackfillCheckpoint,
    BackfillContext,
    BackfillOutcome,
    BackfillRecord,
    CandidateDecision,
    EvidenceStatus,
    HandoffGate,
    HandoffStatus as M04EHandoffStatus,
    M05HandoffMarker,
    UpstreamGateEvidence,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    HandoffStatus as M05HandoffStatus,
    ImportCheckpoint,
    PromotionGateEvidence,
    PromotionRecord,
)
from prodavan.application.migration.m06_grants_gateway.adapter import (
    schema_identity_digest,
)
from prodavan.application.migration.m06_grants_gateway.handoff import (
    AcceptedPartitionEvidence,
    AcceptedSourceKey,
    inventory_digest,
)
from prodavan.infrastructure.persistence.models.canonical_grants import (
    CanonicalGrantMappingRow,
    CanonicalGrantRow,
    M06CanonicalGrantPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    M04EDefinitionVersionPromotionEvidenceRow,
)
from prodavan.infrastructure.persistence.models.plugin_instance_registry import (
    M05PluginInstancePromotionEvidenceRow,
    PluginInstanceRegistryRow,
)

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")

M04E_HANDOFF_SCHEMA = "m04e-m05-handoff-marker.v1"
M04E_BACKFILL_SCHEMA = "m04e-backfill-page.v1"
M05_PROMOTION_SCHEMA = "m05-m06-promotion-record.v1"
M06_DEPENDENCY_KEYS = ("schema_identity_digest",)

REDACTION = "identifiers_digests_counts_and_reason_codes_only"

#: Owner-registry tables whose installed RLS policies are the M04 RLS evidence.
M04_OWNER_TABLES = (
    "scope_registry",
    "scope_owner_tenant_anchors",
    "scope_owner_cabinet_anchors",
    "scope_owner_project_anchors",
    "scope_owner_mappings",
    "scope_owner_quarantine",
    "scope_owner_audit",
)

#: Column widths taken from the registered ORM models, not guessed.
M04E_PROMOTION_ID_WIDTH = 80
M04E_PROMOTION_REVISION_WIDTH = 80
M05_PROMOTION_ID_WIDTH = 40
M05_PROMOTION_REVISION_WIDTH = 80
M05_PROMOTION_PARTITION_WIDTH = 128
M05_PROMOTION_SOURCE_SCHEMA_WIDTH = 32
M05_PROMOTION_CORRELATION_WIDTH = 128
M05_PROMOTION_ACTOR_WIDTH = 128
M05_PROMOTION_OWNER_WIDTH = 200
M06_PROMOTION_PARTITION_WIDTH = 160

#: Static blockers that live catalog or stored evidence cannot repair or infer.
#: Included in every partition read refusal so the remaining gap is named, not silent.
DECLARED_READ_BLOCKERS = (
    "m04_scope_promotion_evidence_missing: no registered table stores the accepted "
    "M04-02 scope/RLS promotion evidence row, so AcceptedPartitionEvidence cannot "
    "attest m04_scope_evidence_id from stored evidence",
)

#: Review inputs, not executed checks: the gaps this module cannot close.
SCHEMA_BLOCKERS = (
    "promotion_outcome_cannot_express_ready: ck_m04e_promotion_outcome and "
    "ck_m05_instance_promotion_outcome admit only ('blocked','quarantined'), so the "
    "handoff status is persisted in the redacted gate_evidence envelope and the "
    "outcome column always reads 'blocked'",
    "m04_scope_promotion_evidence_missing: no registered table stores the accepted M04 "
    "scope/RLS evidence row, so PartitionHandoffReadPort refuses with a named blocker",
    "m05_promotion_record_carries_no_source_inventory: PromotionRecord holds counts "
    "only; the accepted source inventory is reconstructed from m05_plugin_instances",
    "m05_writer_generation_zero_has_no_m06_epoch: M05 allows writer_generation=0 while "
    "AcceptedPartitionEvidence requires writer_epoch>0; this module refuses instead of "
    "normalising the value",
    "m04e_backfill_has_no_checkpoint_or_source_mapping_table: the page cursor and the "
    "first-mapping ledger live in the redacted gate_evidence JSON of the append-only "
    "promotion table, keyed by a unique revision_id that acts as the cursor CAS token",
)


class MigrationEvidenceBlocked(RuntimeError):
    """A durable evidence boundary is missing, unusable or contradicted."""

    def __init__(self, blockers: Sequence[str]) -> None:
        unique = tuple(dict.fromkeys(str(item) for item in blockers))
        if not unique:
            raise ValueError("a blocked evidence boundary needs at least one blocker")
        self.blockers = unique
        super().__init__(", ".join(unique))


class MigrationEvidenceConflict(MigrationEvidenceBlocked):
    """The same logical evidence key already holds a different payload."""


class MigrationEvidenceCorrupt(MigrationEvidenceBlocked):
    """A stored envelope cannot be decoded into its own contract type."""


# ---------------------------------------------------------------------------
# Canonical encoding, digests, redaction
# ---------------------------------------------------------------------------


def digest(value: Any) -> str:
    """Canonical digest, byte-identical to ``m04e``/``m05``/``m06`` ``_digest``."""
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def backfill_checkpoint_digest(checkpoint: BackfillCheckpoint) -> str:
    """Digest of an M04E checkpoint.

    ``BackfillCheckpoint`` has no ``.digest`` (review finding I4) and the type lives
    outside this task's file zone, so the composition root uses this helper instead of
    re-inventing one per adapter.
    """
    return digest({
        "partition": list(checkpoint.partition),
        "phase": checkpoint.phase,
        "after_source_id": checkpoint.after_source_id,
        "sequence": checkpoint.sequence,
        "source_snapshot_id": checkpoint.source_snapshot_id,
        "source_schema_version": checkpoint.source_schema_version,
        "source_snapshot_digest": checkpoint.source_snapshot_digest,
        "canonical_schema_version": checkpoint.canonical_schema_version,
        "writer_generation": checkpoint.writer_generation,
        "phase_complete": checkpoint.phase_complete,
    })


def m05_partition_id(tenant_id: str, app_definition_id: str) -> str:
    """M05 bounded partition key ``(tenant, definition)`` in column form."""
    value = f"{tenant_id}:{app_definition_id}"
    if len(value) > M05_PROMOTION_PARTITION_WIDTH:
        raise MigrationEvidenceBlocked(("m05_partition_id_exceeds_column_width",))
    return value


def m06_partition_id(tenant_id: str, app_definition_id: str, plugin_instance_id: str) -> str:
    """M06 bounded partition key in the registered writer-epoch form."""
    value = f"{tenant_id}:{app_definition_id}:plugin_instance:{plugin_instance_id}"
    if len(value) > M06_PROMOTION_PARTITION_WIDTH:
        raise MigrationEvidenceBlocked(("m06_partition_id_exceeds_column_width",))
    return value


_REDACTED_KEY_MATERIAL = ("payload", "manifest", "token", "secret", "password", "body")


def _assert_redacted(mapping: Mapping[str, Any], *, where: str) -> None:
    """Refuse to persist a key that could carry payload material.

    Stricter than a filter: silently dropping evidence would hide a redaction bug.
    """
    for key in mapping:
        lowered = str(key).lower()
        for marker in _REDACTED_KEY_MATERIAL:
            if marker in lowered:
                raise MigrationEvidenceBlocked((f"redaction_violation:{where}:{key}",))


def _recorded_at(value: str, *, where: str) -> datetime:
    if not isinstance(value, str) or not value.strip():
        raise MigrationEvidenceBlocked((f"recorded_at_missing:{where}",))
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:  # pragma: no cover - defensive
        raise MigrationEvidenceBlocked((f"recorded_at_invalid:{where}",)) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise MigrationEvidenceBlocked((f"recorded_at_timezone_missing:{where}",))
    return parsed


# ---------------------------------------------------------------------------
# Trusted transaction binding
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class TrustedEvidenceScope:
    """Tenant/role context supplied by the composition root, never by a request."""

    tenant_id: str
    writer_generation: int | None = None
    transaction_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("trusted evidence scope requires a resolved tenant")
        if self.writer_generation is not None and (
            not isinstance(self.writer_generation, int)
            or isinstance(self.writer_generation, bool)
            or self.writer_generation < 0
        ):
            raise ValueError("trusted writer generation must be a non-negative integer")

    @property
    def usable(self) -> bool:
        return bool(self.tenant_id.strip())

    def admits_partition(self, partition: Sequence[str]) -> bool:
        return len(partition) == 2 and partition[0] == self.tenant_id


async def bind_evidence_transaction(session: AsyncSession, scope: TrustedEvidenceScope) -> None:
    """Set the transaction-local GUCs and prove the readback matches.

    Every statement of every port runs after this call, so an unset or mismatched
    tenant context raises instead of degrading into a wider read.
    """
    if not scope.usable:
        raise MigrationEvidenceBlocked(("trusted_evidence_tenant_missing",))
    values = {
        "tenant_id": scope.tenant_id,
        "writer_generation": str(scope.writer_generation or 0),
        "migration_role": "true",
        "audit_writer": "true",
    }
    await session.execute(
        text(
            "SELECT set_config('app.tenant_id', :tenant_id, true), "
            "set_config('app.writer_generation', :writer_generation, true), "
            "set_config('app.writer_epoch', :writer_generation, true), "
            "set_config('app.migration_role', :migration_role, true), "
            "set_config('app.audit_writer', :audit_writer, true)"
        ),
        values,
    )
    readback = (
        await session.execute(
            text(
                "SELECT current_setting('app.tenant_id', true) AS tenant_id, "
                "current_setting('app.writer_generation', true) AS writer_generation, "
                "current_setting('app.migration_role', true) AS migration_role"
            )
        )
    ).mappings().one()
    if (
        readback["tenant_id"] != values["tenant_id"]
        or readback["writer_generation"] != values["writer_generation"]
        or readback["migration_role"] != values["migration_role"]
    ):
        raise MigrationEvidenceBlocked(("transaction_local_context_readback_mismatch",))


class _EvidenceSessionBoundaries:
    """One database transaction per port call, always tenant-bound first."""

    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], scope: TrustedEvidenceScope
    ) -> None:
        if not isinstance(scope, TrustedEvidenceScope) or not scope.usable:
            raise ValueError("evidence ports require a resolved trusted evidence scope")
        self._session_factory = session_factory
        self._scope = scope

    @property
    def scope(self) -> TrustedEvidenceScope:
        return self._scope

    @asynccontextmanager
    async def read(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            await bind_evidence_transaction(session, self._scope)
            yield session

    @asynccontextmanager
    async def write(self) -> AsyncIterator[AsyncSession]:
        async with self._session_factory() as session:
            async with session.begin():
                await bind_evidence_transaction(session, self._scope)
                yield session


# ---------------------------------------------------------------------------
# Port 1: M05HandoffPort over m04e_definition_version_promotion_evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MarkerAppendPlan:
    """Pure decision for one append: insert, exact replay, or typed conflict."""

    action: str
    blockers: tuple[str, ...] = ()
    stored: M05HandoffMarker | None = None

    def __post_init__(self) -> None:
        if self.action not in {"insert", "replay", "conflict"}:
            raise ValueError("marker plan action must be insert, replay or conflict")

    @property
    def is_insert(self) -> bool:
        return self.action == "insert"

    @property
    def is_replay(self) -> bool:
        return self.action == "replay"

    @property
    def is_conflict(self) -> bool:
        return self.action == "conflict"


def marker_ledger_key(marker: M05HandoffMarker) -> str:
    """Logical key of a handoff marker: partition plus immutable evidence digests."""
    return "sha256:" + digest([
        list(marker.partition),
        marker.source_snapshot_digest,
        marker.canonical_digest,
        backfill_checkpoint_digest(marker.checkpoint),
    ]).removeprefix("sha256:")


def marker_payload(marker: M05HandoffMarker, *, where: str = "m05_handoff_marker") -> dict[str, Any]:
    """Redacted, reversible envelope payload for one marker."""
    payload = {
        "schema": M04E_HANDOFF_SCHEMA,
        "kind": "handoff_marker",
        "record_id": marker.record_id,
        "task_id": marker.task_id,
        "status": _enum_value(marker.status),
        "partition": list(marker.partition),
        "source_snapshot_digest": marker.source_snapshot_digest,
        "canonical_digest": marker.canonical_digest,
        "checkpoint": _checkpoint_payload(marker.checkpoint),
        "accepted_count": marker.accepted_count,
        "quarantined_count": marker.quarantined_count,
        "gates": [
            {
                "name": gate.name,
                "passed": bool(gate.passed),
                "evidence_digest": gate.evidence_digest,
                "reason": gate.reason,
            }
            for gate in marker.gate_evidence
        ],
        "actor": marker.actor,
        "recorded_at": marker.recorded_at,
        "decision_owner": marker.decision_owner,
        "writer_generation": marker.writer_generation,
        "correlation_id": marker.correlation_id,
        "canonical_positive_writes": bool(marker.canonical_positive_writes),
        "redaction": REDACTION,
        "where": where,
    }
    _assert_redacted(payload, where=where)
    return payload


def plan_marker_append(
    marker: M05HandoffMarker,
    *,
    existing_by_id: Mapping[str, Any] | None,
    existing_by_key: Mapping[str, Any] | None,
) -> MarkerAppendPlan:
    """Decide insert/replay/conflict from the two rows the caller already read."""
    stored = decode_marker_row(existing_by_id) if existing_by_id is not None else None
    if stored is not None:
        if stored == marker:
            return MarkerAppendPlan("replay", stored=stored)
        return MarkerAppendPlan(
            "conflict",
            ("handoff_marker_payload_conflict",),
            stored=stored,
        )
    if existing_by_key is not None:
        other = decode_marker_row(existing_by_key)
        if other is not None and other != marker:
            return MarkerAppendPlan(
                "conflict",
                ("handoff_marker_identity_conflict",),
                stored=other,
            )
        if other is not None:
            return MarkerAppendPlan("replay", stored=other)
    return MarkerAppendPlan("insert")


def decode_marker_row(row: Any | None) -> M05HandoffMarker | None:
    """Reconstruct the marker from its stored row, exactly."""
    if row is None:
        return None
    envelope = _envelope(row)
    if envelope.get("schema") != M04E_HANDOFF_SCHEMA or envelope.get("kind") != "handoff_marker":
        return None
    marker = M05HandoffMarker(
        record_id=_text(envelope, "record_id"),
        task_id=_text(envelope, "task_id"),
        status=_handoff_status(envelope.get("status")),
        partition=_partition(envelope.get("partition")),
        source_snapshot_digest=_text(envelope, "source_snapshot_digest"),
        canonical_digest=_text(envelope, "canonical_digest"),
        checkpoint=_checkpoint(envelope.get("checkpoint")),
        accepted_count=_count(envelope, "accepted_count"),
        quarantined_count=_count(envelope, "quarantined_count"),
        gate_evidence=_gates(envelope.get("gates")),
        actor=_text(envelope, "actor"),
        recorded_at=_text(envelope, "recorded_at"),
        decision_owner=_text(envelope, "decision_owner"),
        writer_generation=_optional_count(envelope, "writer_generation"),
        correlation_id=_text(envelope, "correlation_id"),
        canonical_positive_writes=bool(envelope.get("canonical_positive_writes", False)),
    )
    if (row.id, row.tenant_id, row.definition_id) != (
        marker.record_id, marker.partition[0], marker.partition[1]
    ):
        raise MigrationEvidenceCorrupt(("handoff_marker_row_identity_drift",))
    return marker


def handoff_marker_row_values(marker: M05HandoffMarker) -> dict[str, Any]:
    """Column mapping for one marker append; only columns the model declares."""
    if not isinstance(marker, M05HandoffMarker):
        raise MigrationEvidenceBlocked(("handoff_marker_type_invalid",))
    blockers: list[str] = []
    if len(marker.record_id) > M04E_PROMOTION_ID_WIDTH:
        blockers.append("handoff_marker_record_id_exceeds_column_width")
    if marker.task_id != "MIG-M04E-02":
        blockers.append("handoff_marker_task_id_unexpected")
    if not is_sha256(marker.source_snapshot_digest):
        blockers.append("handoff_marker_source_snapshot_digest_invalid")
    if not is_sha256(marker.canonical_digest):
        blockers.append("handoff_marker_canonical_digest_invalid")
    if marker.canonical_positive_writes:
        blockers.append("handoff_marker_cannot_enable_positive_writes")
    if len(marker.partition[1]) > M04E_PROMOTION_ID_WIDTH:
        blockers.append("handoff_marker_definition_id_exceeds_column_width")
    resolved = {gate.name: gate for gate in marker.gate_evidence}
    for gate_name in ("same_definition_version", "immutable_artifact"):
        gate = resolved.get(gate_name)
        if gate is None or not is_sha256(gate.evidence_digest):
            blockers.append(f"handoff_marker_gate_evidence_missing:{gate_name}")
    if blockers:
        raise MigrationEvidenceBlocked(blockers)
    assert resolved["same_definition_version"].evidence_digest is not None
    assert resolved["immutable_artifact"].evidence_digest is not None
    return {
        "id": marker.record_id,
        "tenant_id": marker.partition[0],
        "definition_id": marker.partition[1],
        "revision_id": marker.record_id,
        "outcome": "blocked",
        "source_snapshot_id": marker.checkpoint.source_snapshot_id,
        "source_digest": marker.source_snapshot_digest,
        "target_digest": marker.canonical_digest,
        "valid_count": marker.accepted_count,
        "archived_count": 0,
        "quarantined_count": marker.quarantined_count,
        "current_version_resolution_digest": resolved["same_definition_version"].evidence_digest,
        "artifact_equality_digest": resolved["immutable_artifact"].evidence_digest,
        "gate_evidence": marker_payload(marker),
        "canonical_positive_writes": False,
        "reviewer": marker.decision_owner[:M05_PROMOTION_OWNER_WIDTH],
        "previous_revision_id": None,
        "recorded_at": _recorded_at(marker.recorded_at, where="m05_handoff_marker"),
        "notes": "redacted M04E->M05 handoff marker; no payload or secret material",
    }


class SqlM05HandoffEvidenceStore:
    """`M05HandoffPort` over the append-only M04E promotion evidence table.

    One call owns one transaction: the trusted tenant is bound first, the immutable
    primary key and the logical evidence key are read under that binding, and only then
    is a single row appended. A repeated exact marker returns the recorded marker; a
    different marker for the same primary key or the same immutable evidence key raises
    :class:`MigrationEvidenceConflict` instead of overwriting.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        scope: TrustedEvidenceScope,
    ) -> None:
        self._boundaries = _EvidenceSessionBoundaries(session_factory, scope)

    @property
    def schema_blockers(self) -> tuple[str, ...]:
        return SCHEMA_BLOCKERS

    async def append_marker(self, marker: M05HandoffMarker) -> M05HandoffMarker:
        if not self._boundaries.scope.admits_partition(marker.partition):
            raise MigrationEvidenceBlocked(("handoff_marker_partition_outside_trusted_tenant",))
        if marker.writer_generation != self._boundaries.scope.writer_generation:
            raise MigrationEvidenceBlocked(("handoff_marker_writer_generation_not_trusted",))
        values = handoff_marker_row_values(marker)
        async with self._boundaries.write() as session:
            existing_by_id = await session.get(M04EDefinitionVersionPromotionEvidenceRow, marker.record_id)
            existing_by_key = None
            if existing_by_id is None:
                existing_by_key = await self._find_marker_by_key(session, marker)
            plan = plan_marker_append(
                marker,
                existing_by_id=_row_view(existing_by_id),
                existing_by_key=_row_view(existing_by_key),
            )
            if plan.is_conflict:
                raise MigrationEvidenceConflict(plan.blockers)
            if plan.is_replay:
                if plan.stored != marker:
                    raise MigrationEvidenceCorrupt(("handoff_marker_replay_drift",))
                return plan.stored
            session.add(M04EDefinitionVersionPromotionEvidenceRow(**values))
            try:
                await session.flush()
            except IntegrityError as exc:
                raise MigrationEvidenceConflict(
                    ("handoff_marker_concurrent_append_conflict",)
                ) from exc
            return marker

    async def load_marker(
        self, *, tenant_id: str, app_definition_id: str, canonical_digest: str
    ) -> M05HandoffMarker | None:
        """Exact read of one accepted handoff marker inside the bound tenant."""
        if tenant_id != self._boundaries.scope.tenant_id:
            raise MigrationEvidenceBlocked(("handoff_read_partition_outside_trusted_tenant",))
        if not is_sha256(canonical_digest):
            raise MigrationEvidenceBlocked(("handoff_read_canonical_digest_invalid",))
        async with self._boundaries.read() as session:
            row = await session.scalar(
                select(M04EDefinitionVersionPromotionEvidenceRow)
                .where(
                    M04EDefinitionVersionPromotionEvidenceRow.tenant_id == tenant_id,
                    M04EDefinitionVersionPromotionEvidenceRow.definition_id == app_definition_id,
                    M04EDefinitionVersionPromotionEvidenceRow.target_digest == canonical_digest,
                )
                .order_by(
                    M04EDefinitionVersionPromotionEvidenceRow.recorded_at,
                    M04EDefinitionVersionPromotionEvidenceRow.id,
                )
                .limit(1)
            )
            return decode_marker_row(_row_view(row))

    async def _find_marker_by_key(
        self, session: AsyncSession, marker: M05HandoffMarker
    ) -> M04EDefinitionVersionPromotionEvidenceRow | None:
        key = marker_ledger_key(marker)
        rows = (
            await session.execute(
                select(M04EDefinitionVersionPromotionEvidenceRow)
                .where(
                    M04EDefinitionVersionPromotionEvidenceRow.tenant_id == marker.partition[0],
                    M04EDefinitionVersionPromotionEvidenceRow.definition_id == marker.partition[1],
                    M04EDefinitionVersionPromotionEvidenceRow.gate_evidence["schema"].astext
                    == M04E_HANDOFF_SCHEMA,
                )
                .order_by(
                    M04EDefinitionVersionPromotionEvidenceRow.recorded_at,
                    M04EDefinitionVersionPromotionEvidenceRow.id,
                )
            )
        ).scalars()
        for row in rows:
            decoded = decode_marker_row(_row_view(row))
            if decoded is not None and marker_ledger_key(decoded) == key:
                return row
        return None


# ---------------------------------------------------------------------------
# Port 2: EvidenceStore over m04e_definition_version_promotion_evidence
# ---------------------------------------------------------------------------

_EVIDENCE_ORDER = {"definition": 0, "version": 1}


def backfill_source_key(source_kind: str, source_id: str, schema_version: str) -> str:
    """Ledger key of one immutable M04E source identity."""
    return "sha256:" + digest([source_kind, source_id, schema_version]).removeprefix("sha256:")


def backfill_observation_key(
    source_kind: str,
    source_id: str,
    schema_version: str,
    *,
    observed_source_digest: str | None,
    observed_artifact_digest: str | None,
) -> str:
    return "sha256:" + digest([
        source_kind, source_id, schema_version,
        observed_source_digest, observed_artifact_digest,
    ]).removeprefix("sha256:")


def record_payload(record: BackfillRecord) -> dict[str, Any]:
    """Redacted, reversible payload of one durable backfill record."""
    payload = {
        "source_kind": record.source_kind,
        "source_id": record.source_id,
        "schema_version": record.schema_version,
        "source_digest": record.source_digest,
        "canonical_digest": record.canonical_digest,
        "canonical_identity": list(record.canonical_identity),
        "checkpoint": _checkpoint_payload(record.checkpoint),
        "status": _enum_value(record.status),
        "reason_code": record.reason_code,
        "correlation_id": record.correlation_id,
        "tenant_id": record.tenant_id,
        "app_definition_id": record.app_definition_id,
        "phase": record.phase,
        "source_snapshot_digest": record.source_snapshot_digest,
        "proof_evidence": [list(item) for item in record.proof_evidence],
        "artifact_uri": record.artifact_uri,
        "artifact_digest": record.artifact_digest,
        "verified_artifact_digest": record.verified_artifact_digest,
        "current_artifact_digest": record.current_artifact_digest,
        "transaction_id": record.transaction_id,
        "writer_generation": record.writer_generation,
        "outcome": None if record.outcome is None else _enum_value(record.outcome),
        "observed_source_digest": record.observed_source_digest,
        "observed_artifact_digest": record.observed_artifact_digest,
        "blocker_evidence": list(record.blocker_evidence),
    }
    _assert_redacted(payload, where="m04e_backfill_record")
    return payload


def record_from_payload(payload: Mapping[str, Any]) -> BackfillRecord:
    try:
        outcome = payload.get("outcome")
        return BackfillRecord(
            source_kind=_text(payload, "source_kind"),
            source_id=_text(payload, "source_id"),
            schema_version=_text(payload, "schema_version"),
            source_digest=_text(payload, "source_digest"),
            canonical_digest=_optional_text(payload, "canonical_digest"),
            canonical_identity=tuple(str(item) for item in payload.get("canonical_identity") or ()),
            checkpoint=_checkpoint(payload.get("checkpoint")),
            status=_evidence_status(payload.get("status")),
            reason_code=_optional_text(payload, "reason_code"),
            correlation_id=_text(payload, "correlation_id"),
            tenant_id=_optional_text(payload, "tenant_id"),
            app_definition_id=_optional_text(payload, "app_definition_id"),
            phase=_optional_text(payload, "phase"),
            source_snapshot_digest=_optional_text(payload, "source_snapshot_digest"),
            proof_evidence=tuple(
                (str(item[0]), str(item[1])) for item in payload.get("proof_evidence") or ()
            ),
            artifact_uri=_optional_text(payload, "artifact_uri"),
            artifact_digest=_optional_text(payload, "artifact_digest"),
            verified_artifact_digest=_optional_text(payload, "verified_artifact_digest"),
            current_artifact_digest=_optional_text(payload, "current_artifact_digest"),
            transaction_id=_optional_text(payload, "transaction_id"),
            writer_generation=_optional_count(payload, "writer_generation"),
            outcome=None if outcome is None else _backfill_outcome(outcome),
            observed_source_digest=_optional_text(payload, "observed_source_digest"),
            observed_artifact_digest=_optional_text(payload, "observed_artifact_digest"),
            blocker_evidence=tuple(str(item) for item in payload.get("blocker_evidence") or ()),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MigrationEvidenceCorrupt(("m04e_backfill_record_payload_invalid",)) from exc


def page_ledger_key(expected: BackfillCheckpoint, decisions: Sequence[CandidateDecision]) -> str:
    """Deterministic page identity: same cursor and same candidates replay exactly."""
    return "sha256:" + digest([
        list(expected.partition),
        backfill_checkpoint_digest(expected),
        [[
            decision.candidate.source_kind,
            decision.candidate.source_id,
            decision.candidate.schema_version,
            decision.candidate.source_digest,
            _enum_value(decision.status),
            decision.reason_code,
            decision.verified_artifact_digest,
        ] for decision in decisions],
    ]).removeprefix("sha256:")


def next_checkpoint(
    expected: BackfillCheckpoint,
    decisions: Sequence[CandidateDecision],
    records: Sequence[BackfillRecord],
    *,
    page_exhausted: bool,
) -> BackfillCheckpoint:
    """The durable successor cursor, exactly as the backfill contract validates it."""
    consumed = tuple(r for r in records if r.outcome is not BackfillOutcome.REPLAY)
    all_replays = bool(records) and not consumed
    if all_replays:
        return expected
    phase = expected.phase
    after = expected.after_source_id
    sequence = expected.sequence + len(consumed)
    complete = False
    last_source_id = decisions[-1].candidate.source_id if decisions else None
    page_drained = not records or (bool(consumed) and consumed[-1].source_id == last_source_id)
    if page_exhausted and page_drained:
        if phase == "definition":
            phase, after, sequence = "version", None, sequence + 1
        else:
            after = consumed[-1].source_id if consumed else expected.after_source_id
            complete = True
    if consumed and phase == expected.phase:
        after = consumed[-1].source_id
    return BackfillCheckpoint(
        partition=expected.partition,
        phase=phase,
        after_source_id=after,
        sequence=sequence,
        source_snapshot_id=expected.source_snapshot_id,
        source_schema_version=expected.source_schema_version,
        source_snapshot_digest=expected.source_snapshot_digest,
        canonical_schema_version=expected.canonical_schema_version,
        writer_generation=expected.writer_generation,
        phase_complete=complete,
    )


class SqlM04EBackfillEvidenceStore:
    """`EvidenceStore` (M04E bounded backfill) over the same append-only table.

    The page cursor and the first-mapping/observation ledgers are carried in the redacted
    ``gate_evidence`` JSON of one append-only row per committed page. ``revision_id`` is
    the digest of the expected checkpoint, so
    ``uq_m04e_promotion_revision (tenant_id, definition_id, revision_id)`` is the cursor
    compare-and-set: a second page claiming the same expected cursor fails at the
    database instead of silently re-advancing it. Replaying the same page (same cursor,
    same candidates) hits the primary key and returns the recorded batch unchanged.

    The M04E schema has no dedicated checkpoint or source-mapping table (review finding
    G4 assumes one); the ledger is therefore stored as evidence inside the append-only
    promotion row rather than in an in-memory cursor. See :data:`SCHEMA_BLOCKERS`.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        scope: TrustedEvidenceScope,
    ) -> None:
        self._boundaries = _EvidenceSessionBoundaries(session_factory, scope)

    async def find_existing(
        self,
        *,
        context: BackfillContext,
        source_kind: str,
        source_id: str,
        source_schema_version: str,
    ) -> BackfillRecord | None:
        self._require_context(context)
        key = backfill_source_key(source_kind, source_id, source_schema_version)
        async with self._boundaries.read() as session:
            return await self._ledger_lookup(session, context, "sources", key)

    async def find_observation(
        self,
        *,
        context: BackfillContext,
        source_kind: str,
        source_id: str,
        source_schema_version: str,
        observed_source_digest: str | None,
        observed_artifact_digest: str | None,
    ) -> BackfillRecord | None:
        self._require_context(context)
        if observed_source_digest is None and observed_artifact_digest is None:
            return None
        key = backfill_observation_key(
            source_kind, source_id, source_schema_version,
            observed_source_digest=observed_source_digest,
            observed_artifact_digest=observed_artifact_digest,
        )
        async with self._boundaries.read() as session:
            record = await self._ledger_lookup(session, context, "observations", key)
        if record is None:
            return None
        return record

    async def record_blocked(
        self,
        *,
        context: BackfillContext,
        checkpoint: BackfillCheckpoint,
        reason_code: str,
        blocker_evidence: Sequence[str] = (),
    ) -> BackfillRecord:
        self._require_context(context)
        self._require_checkpoint_lineage(context, checkpoint)
        if not isinstance(reason_code, str) or not reason_code.strip():
            raise MigrationEvidenceBlocked(("backfill_blocked_reason_missing",))
        record = BackfillRecord(
            source_kind="m04e_backfill",
            source_id="blocked",
            schema_version=context.source_schema_version,
            source_digest=context.source_snapshot_digest,
            canonical_digest=None,
            canonical_identity=(),
            checkpoint=checkpoint,
            status=EvidenceStatus.BLOCKED,
            reason_code=reason_code,
            correlation_id=context.correlation_id,
            tenant_id=context.tenant_id,
            app_definition_id=context.app_definition_id,
            phase=checkpoint.phase,
            source_snapshot_digest=context.source_snapshot_digest,
            transaction_id=context.transaction_id,
            writer_generation=context.writer_generation,
            outcome=None,
            blocker_evidence=tuple(blocker_evidence),
        )
        row_id = "m04eb:" + digest([
            "blocked", list(checkpoint.partition), backfill_checkpoint_digest(checkpoint),
            reason_code, context.correlation_id,
        ]).removeprefix("sha256:")
        revision_id = "hb-" + digest([
            backfill_checkpoint_digest(checkpoint), reason_code,
        ]).removeprefix("sha256:")
        async with self._boundaries.write() as session:
            existing = await session.get(M04EDefinitionVersionPromotionEvidenceRow, row_id)
            if existing is not None:
                stored = self._page_records(_row_view(existing))
                if len(stored) != 1 or stored[0] != record:
                    raise MigrationEvidenceConflict(("backfill_blocked_evidence_conflict",))
                return stored[0]
            session.add(M04EDefinitionVersionPromotionEvidenceRow(
                **self._page_row_values(
                    context=context, checkpoint=checkpoint, row_id=row_id,
                    revision_id=revision_id, outcome="blocked", records=(record,),
                    sources={}, observations={}, complete=False, page_digest=digest(
                        record_payload(record)
                    ), gates=None,
                )
            ))
            try:
                await session.flush()
            except IntegrityError as exc:
                raise MigrationEvidenceConflict(("backfill_blocked_concurrent_append",)) from exc
            return record

    async def classify_and_commit_page(
        self,
        *,
        context: BackfillContext,
        gates: UpstreamGateEvidence,
        expected_checkpoint: BackfillCheckpoint,
        decisions: Sequence[CandidateDecision],
        page_exhausted: bool,
    ) -> BackfillBatch:
        self._require_context(context)
        self._require_checkpoint_lineage(context, expected_checkpoint)
        if not isinstance(gates, UpstreamGateEvidence) or not gates.valid:
            raise MigrationEvidenceBlocked(("upstream_gate_evidence_invalid",))
        if (gates.tenant_id, gates.app_definition_id) != expected_checkpoint.partition:
            raise MigrationEvidenceBlocked(("upstream_gate_partition_mismatch",))
        if gates.source_snapshot_digest != context.source_snapshot_digest:
            raise MigrationEvidenceBlocked(("upstream_gate_snapshot_lineage_mismatch",))
        if gates.transaction_id != context.transaction_id:
            raise MigrationEvidenceBlocked(("upstream_gate_transaction_mismatch",))
        if gates.correlation_id != context.correlation_id:
            raise MigrationEvidenceBlocked(("upstream_gate_correlation_mismatch",))
        ordered = tuple(decisions)
        seen: set[str] = set()
        for decision in ordered:
            candidate = decision.candidate
            if candidate.phase != expected_checkpoint.phase:
                raise MigrationEvidenceBlocked(("candidate_phase_outside_checkpoint_phase",))
            if candidate.tenant_id != context.tenant_id or candidate.app_definition_id != context.app_definition_id:
                raise MigrationEvidenceBlocked(("candidate_partition_outside_trusted_tenant",))
            if candidate.schema_version != context.source_schema_version:
                raise MigrationEvidenceBlocked(("candidate_schema_outside_checkpoint_lineage",))
            if candidate.canonical_schema_version != context.canonical_schema_version:
                raise MigrationEvidenceBlocked(("candidate_canonical_schema_outside_lineage",))
            key = backfill_source_key(candidate.source_kind, candidate.source_id, candidate.schema_version)
            if key in seen:
                raise MigrationEvidenceBlocked(("candidate_source_identity_duplicated_in_page",))
            seen.add(key)

        row_id = "m04ep:" + page_ledger_key(expected_checkpoint, ordered).removeprefix("sha256:")
        revision_id = "cp-" + backfill_checkpoint_digest(expected_checkpoint).removeprefix("sha256:")
        async with self._boundaries.write() as session:
            existing = await session.get(M04EDefinitionVersionPromotionEvidenceRow, row_id)
            if existing is not None:
                view = _row_view(existing)
                return BackfillBatch(
                    tuple(self._page_records(view)),
                    self._page_checkpoint(view),
                    bool(_envelope(view).get("complete", False)),
                )
            durable = await self._load_durable_checkpoint(session, context)
            if durable is not None:
                self._require_cursor_cas(durable, expected_checkpoint)
            records, sources, observations = await self._resolve_decisions(
                session, context, expected_checkpoint, ordered
            )
            successor = next_checkpoint(
                expected_checkpoint, ordered, records, page_exhausted=page_exhausted
            )
            if records and all(r.outcome is BackfillOutcome.REPLAY for r in records) and durable is not None:
                successor = durable
            session.add(M04EDefinitionVersionPromotionEvidenceRow(
                **self._page_row_values(
                    context=context, checkpoint=successor, row_id=row_id,
                    revision_id=revision_id,
                    # The column admits only blocked/quarantined by check constraint; the
                    # page's true classification lives in the redacted envelope.
                    outcome=("blocked" if all(
                        r.status is EvidenceStatus.ACCEPTED for r in records
                    ) else "quarantined"),
                    records=records, sources=sources, observations=observations,
                    complete=successor.phase_complete,
                    page_digest=digest([record_payload(r) for r in records]),
                    gates=gates,
                )
            ))
            try:
                await session.flush()
            except IntegrityError as exc:
                raise MigrationEvidenceConflict(("backfill_page_cursor_cas_conflict",)) from exc
            return BackfillBatch(tuple(records), successor, successor.phase_complete)

    # -- internals ---------------------------------------------------------

    def _require_context(self, context: BackfillContext) -> None:
        if not isinstance(context, BackfillContext) or not context.usable:
            raise MigrationEvidenceBlocked(("backfill_context_unusable",))
        if not self._boundaries.scope.admits_partition((context.tenant_id, context.app_definition_id)):
            raise MigrationEvidenceBlocked(("backfill_context_outside_trusted_tenant",))
        if context.writer_generation != self._boundaries.scope.writer_generation:
            raise MigrationEvidenceBlocked(("backfill_writer_generation_not_trusted",))

    def _require_checkpoint_lineage(
        self, context: BackfillContext, checkpoint: BackfillCheckpoint
    ) -> None:
        if checkpoint.partition != (context.tenant_id, context.app_definition_id):
            raise MigrationEvidenceBlocked(("checkpoint_partition_outside_trusted_tenant",))
        if (
            checkpoint.source_snapshot_id != context.source_snapshot_id
            or checkpoint.source_schema_version != context.source_schema_version
            or checkpoint.source_snapshot_digest != context.source_snapshot_digest
            or checkpoint.canonical_schema_version != context.canonical_schema_version
            or checkpoint.writer_generation != context.writer_generation
        ):
            raise MigrationEvidenceBlocked(("checkpoint_outside_source_snapshot_lineage",))

    @staticmethod
    def _require_cursor_cas(durable: BackfillCheckpoint, expected: BackfillCheckpoint) -> None:
        if (
            durable.partition != expected.partition
            or durable.source_snapshot_id != expected.source_snapshot_id
            or durable.source_schema_version != expected.source_schema_version
            or durable.source_snapshot_digest != expected.source_snapshot_digest
            or durable.canonical_schema_version != expected.canonical_schema_version
            or durable.writer_generation != expected.writer_generation
        ):
            raise MigrationEvidenceConflict(("durable_checkpoint_lineage_conflict",))
        if _EVIDENCE_ORDER[durable.phase] > _EVIDENCE_ORDER[expected.phase]:
            raise MigrationEvidenceConflict(("durable_checkpoint_phase_ahead",))
        if durable.phase == expected.phase and durable.sequence > expected.sequence:
            raise MigrationEvidenceConflict(("durable_checkpoint_sequence_ahead",))
        if (
            durable.phase == expected.phase
            and durable.sequence == expected.sequence
            and durable.after_source_id != expected.after_source_id
        ):
            raise MigrationEvidenceConflict(("durable_checkpoint_cursor_conflict",))

    async def _resolve_decisions(
        self,
        session: AsyncSession,
        context: BackfillContext,
        expected: BackfillCheckpoint,
        decisions: Sequence[CandidateDecision],
    ) -> tuple[tuple[BackfillRecord, ...], dict[str, int], dict[str, int]]:
        records: list[BackfillRecord] = []
        sources: dict[str, int] = {}
        observations: dict[str, int] = {}
        seen_identity: set[tuple[str, ...]] = set()
        for decision in decisions:
            candidate = decision.candidate
            key = backfill_source_key(candidate.source_kind, candidate.source_id, candidate.schema_version)
            stored = await self._ledger_lookup(session, context, "sources", key)
            observation_key = None
            observation = None
            if stored is not None and stored.source_digest != candidate.source_digest:
                observation_key = backfill_observation_key(
                    candidate.source_kind, candidate.source_id, candidate.schema_version,
                    observed_source_digest=candidate.source_digest,
                    observed_artifact_digest=None,
                )
                observation = await self._ledger_lookup(
                    session, context, "observations", observation_key
                )
            elif (
                stored is not None
                and stored.verified_artifact_digest is not None
                and decision.verified_artifact_digest is not None
                and stored.verified_artifact_digest != decision.verified_artifact_digest
            ):
                observation_key = backfill_observation_key(
                    candidate.source_kind, candidate.source_id, candidate.schema_version,
                    observed_source_digest=None,
                    observed_artifact_digest=decision.verified_artifact_digest,
                )
                observation = await self._ledger_lookup(
                    session, context, "observations", observation_key
                )
            status = decision.status
            if stored is not None:
                source_changed = stored.source_digest != candidate.source_digest
                artifact_changed = (
                    stored.verified_artifact_digest is not None
                    and decision.verified_artifact_digest is not None
                    and stored.verified_artifact_digest != decision.verified_artifact_digest
                )
                if source_changed or artifact_changed:
                    status = (
                        EvidenceStatus.REPLAY if observation is not None
                        else EvidenceStatus.CONFLICT
                    )
                else:
                    # First mapping is immutable: an unchanged source and artifact is
                    # always a replay of the recorded evidence, never a second mapping.
                    status = EvidenceStatus.REPLAY
            identity = candidate.canonical_identity
            if status is EvidenceStatus.ACCEPTED and identity and identity in seen_identity:
                status = EvidenceStatus.CONFLICT
            if identity:
                seen_identity.add(identity)

            if stored is not None and status is EvidenceStatus.REPLAY:
                replay = BackfillRecord(
                    **{
                        **{f: getattr(stored, f) for f in _RECORD_FIELDS},
                        "status": stored.status,
                        "outcome": BackfillOutcome.REPLAY,
                    }
                )
                records.append(replay)
                continue

            record = self._build_record(
                context=context, checkpoint=expected, decision=decision, status=status,
                observation=observation,
            )
            records.append(record)
            index = len(records) - 1
            if stored is None:
                sources[key] = index
            if observation is None and observation_key is not None:
                observations[observation_key] = index
        return tuple(records), sources, observations

    @staticmethod
    def _build_record(
        *,
        context: BackfillContext,
        checkpoint: BackfillCheckpoint,
        decision: CandidateDecision,
        status: EvidenceStatus,
        observation: BackfillRecord | None,
    ) -> BackfillRecord:
        candidate = decision.candidate
        proof = decision.proof
        proof_evidence: tuple[tuple[str, str], ...] = ()
        if status is EvidenceStatus.ACCEPTED and proof is not None:
            proof_evidence = (
                ("owner", proof.owner_proof_digest),
                ("active_scope", proof.active_scope_proof_digest),
            )
            if candidate.phase == "version":
                proof_evidence += (
                    ("same_definition", proof.same_definition_proof_digest or ""),
                    ("immutable_artifact", proof.artifact_immutability_proof_digest or ""),
                    ("artifact_content", decision.verified_artifact_digest or ""),
                )
            if proof.current_version_id:
                proof_evidence += (
                    ("current_version", proof.current_version_proof_digest or ""),
                    ("current_same_definition", proof.current_version_same_definition_digest or ""),
                    ("current_artifact_immutability",
                     proof.current_version_immutable_artifact_digest or ""),
                    ("current_artifact_content", decision.verified_current_artifact_digest or ""),
                )
        outcome = {
            EvidenceStatus.ACCEPTED: BackfillOutcome.ACCEPTED,
            EvidenceStatus.QUARANTINED: BackfillOutcome.QUARANTINED,
            EvidenceStatus.CONFLICT: BackfillOutcome.CONFLICT,
        }.get(status, BackfillOutcome.QUARANTINED)
        observed_source_digest = None
        observed_artifact_digest = None
        if status is EvidenceStatus.CONFLICT:
            if decision.reason_code == "source_digest_changed":
                observed_source_digest = candidate.source_digest
            elif decision.reason_code == "artifact_digest_changed":
                observed_artifact_digest = decision.verified_artifact_digest
            elif observation is not None:
                observed_source_digest = observation.observed_source_digest
                observed_artifact_digest = observation.observed_artifact_digest
        return BackfillRecord(
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            source_digest=candidate.source_digest,
            canonical_digest=(
                candidate.canonical_digest if status is EvidenceStatus.ACCEPTED else None
            ),
            canonical_identity=candidate.canonical_identity,
            checkpoint=checkpoint,
            status=status,
            reason_code=decision.reason_code,
            correlation_id=context.correlation_id,
            tenant_id=context.tenant_id,
            app_definition_id=context.app_definition_id,
            phase=candidate.phase,
            source_snapshot_digest=context.source_snapshot_digest,
            proof_evidence=proof_evidence,
            artifact_uri=(
                proof.artifact_uri if candidate.phase == "version" and proof else None
            ),
            artifact_digest=(
                str(candidate.payload.get("artifact_digest"))
                if candidate.phase == "version" and candidate.payload.get("artifact_digest")
                else None
            ),
            verified_artifact_digest=decision.verified_artifact_digest,
            current_artifact_digest=decision.verified_current_artifact_digest,
            transaction_id=context.transaction_id,
            writer_generation=context.writer_generation,
            outcome=outcome,
            observed_source_digest=observed_source_digest,
            observed_artifact_digest=observed_artifact_digest,
            blocker_evidence=(
                decision.blocker_evidence
                if status is EvidenceStatus.QUARANTINED
                else (observation.blocker_evidence if observation is not None and status is EvidenceStatus.REPLAY else ())
            ),
        )

    async def _ledger_lookup(
        self, session: AsyncSession, context: BackfillContext, section: str, key: str
    ) -> BackfillRecord | None:
        rows = (
            await session.execute(
                select(M04EDefinitionVersionPromotionEvidenceRow)
                .where(
                    M04EDefinitionVersionPromotionEvidenceRow.tenant_id == context.tenant_id,
                    M04EDefinitionVersionPromotionEvidenceRow.definition_id
                    == context.app_definition_id,
                    M04EDefinitionVersionPromotionEvidenceRow.gate_evidence["schema"].astext
                    == M04E_BACKFILL_SCHEMA,
                    M04EDefinitionVersionPromotionEvidenceRow.gate_evidence[section].has_key(key),
                )
                .order_by(
                    M04EDefinitionVersionPromotionEvidenceRow.recorded_at,
                    M04EDefinitionVersionPromotionEvidenceRow.id,
                )
            )
        ).scalars()
        for row in rows:
            view = _row_view(row)
            envelope = _envelope(view)
            index = (envelope.get(section) or {}).get(key)
            if not isinstance(index, int):
                continue
            records = envelope.get("records")
            if not isinstance(records, list) or index < 0 or index >= len(records):
                raise MigrationEvidenceCorrupt(("backfill_ledger_index_out_of_range",))
            return record_from_payload(records[index])
        return None

    async def _load_durable_checkpoint(
        self, session: AsyncSession, context: BackfillContext
    ) -> BackfillCheckpoint | None:
        rows = (
            await session.execute(
                select(M04EDefinitionVersionPromotionEvidenceRow)
                .where(
                    M04EDefinitionVersionPromotionEvidenceRow.tenant_id == context.tenant_id,
                    M04EDefinitionVersionPromotionEvidenceRow.definition_id
                    == context.app_definition_id,
                    M04EDefinitionVersionPromotionEvidenceRow.gate_evidence["schema"].astext
                    == M04E_BACKFILL_SCHEMA,
                )
            )
        ).scalars()
        best: BackfillCheckpoint | None = None
        for row in rows:
            checkpoint = self._page_checkpoint(_row_view(row))
            if best is None or _checkpoint_precedes(best, checkpoint):
                best = checkpoint
        return best

    @staticmethod
    def _page_row_values(
        *,
        context: BackfillContext,
        checkpoint: BackfillCheckpoint,
        row_id: str,
        revision_id: str,
        outcome: str,
        records: Sequence[BackfillRecord],
        sources: Mapping[str, int],
        observations: Mapping[str, int],
        complete: bool,
        page_digest: str,
        gates: UpstreamGateEvidence | None,
    ) -> dict[str, Any]:
        accepted = sum(1 for r in records if r.status is EvidenceStatus.ACCEPTED)
        blocked = sum(1 for r in records if r.status is not EvidenceStatus.ACCEPTED)
        artifact_digests = sorted({
            value for record in records
            for value in (record.verified_artifact_digest, record.current_artifact_digest)
            if is_sha256(value)
        })
        envelope = {
            "schema": M04E_BACKFILL_SCHEMA,
            "kind": "backfill_page",
            "checkpoint": _checkpoint_payload(checkpoint),
            "complete": bool(complete),
            "page_digest": page_digest,
            "records": [record_payload(record) for record in records],
            "sources": dict(sources),
            "observations": dict(observations),
            "upstream_gates": None if gates is None else {
                "m04_gate_digest": gates.m04_gate_digest,
                "m04e_gate_digest": gates.m04e_gate_digest,
                "active_scope_digest": gates.active_scope_digest,
                "transaction_id_digest": digest(gates.transaction_id),
                "correlation_id_digest": digest(gates.correlation_id),
            },
            "redaction": REDACTION,
        }
        _assert_redacted(envelope, where="m04e_backfill_page")
        return {
            "id": row_id[:M04E_PROMOTION_ID_WIDTH],
            "tenant_id": context.tenant_id,
            "definition_id": context.app_definition_id,
            "revision_id": revision_id[:M04E_PROMOTION_REVISION_WIDTH],
            "outcome": outcome,
            "source_snapshot_id": checkpoint.source_snapshot_id,
            "source_digest": (
                context.source_snapshot_digest
                if is_sha256(context.source_snapshot_digest)
                else digest(context.source_snapshot_digest)
            ),
            "target_digest": page_digest,
            "valid_count": accepted,
            "archived_count": 0,
            "quarantined_count": blocked,
            "current_version_resolution_digest": (
                gates.m04e_gate_digest if gates is not None else digest(backfill_checkpoint_digest(checkpoint))
            ),
            "artifact_equality_digest": digest(artifact_digests),
            "gate_evidence": envelope,
            "canonical_positive_writes": False,
            "reviewer": context.decision_owner[:200],
            "previous_revision_id": None,
            "notes": "redacted M04E backfill page evidence; no payload or secret material",
        }

    @staticmethod
    def _page_records(view: Any) -> tuple[BackfillRecord, ...]:
        records = _envelope(view).get("records")
        if not isinstance(records, list):
            raise MigrationEvidenceCorrupt(("backfill_page_records_missing",))
        return tuple(record_from_payload(item) for item in records)

    @staticmethod
    def _page_checkpoint(view: Any) -> BackfillCheckpoint:
        return _checkpoint(_envelope(view).get("checkpoint"))


def _checkpoint_precedes(current: BackfillCheckpoint, candidate: BackfillCheckpoint) -> bool:
    """True when ``candidate`` is a later durable cursor than ``current``."""
    if _EVIDENCE_ORDER[candidate.phase] != _EVIDENCE_ORDER[current.phase]:
        return _EVIDENCE_ORDER[candidate.phase] > _EVIDENCE_ORDER[current.phase]
    if candidate.sequence != current.sequence:
        return candidate.sequence > current.sequence
    if candidate.after_source_id is None:
        return False
    if current.after_source_id is None:
        return True
    return candidate.after_source_id > current.after_source_id


# ---------------------------------------------------------------------------
# Port 3: M05PromotionRecordPort over m05_plugin_instance_promotion_evidence
# ---------------------------------------------------------------------------


def promotion_payload(record: PromotionRecord) -> dict[str, Any]:
    """Redacted, reversible envelope payload of one M05 promotion record."""
    payload = {
        "schema": M05_PROMOTION_SCHEMA,
        "kind": "m05_promotion_record",
        "record_id": record.record_id,
        "task_id": record.task_id,
        "status": _enum_value(record.status),
        "partition": list(record.partition),
        "source_snapshot_digest": record.source_snapshot_digest,
        "canonical_digest": record.canonical_digest,
        "checkpoint": {
            "partition": list(record.checkpoint.partition),
            "source_snapshot_id": record.checkpoint.source_snapshot_id,
            "source_schema_version": record.checkpoint.source_schema_version,
            "source_snapshot_digest": record.checkpoint.source_snapshot_digest,
            "after_source_id": record.checkpoint.after_source_id,
            "sequence": record.checkpoint.sequence,
        },
        "imported_count": record.imported_count,
        "replay_count": record.replay_count,
        "quarantined_count": record.quarantined_count,
        "conflict_count": record.conflict_count,
        "gates": [
            {
                "name": gate.name,
                "passed": bool(gate.passed),
                "evidence_digest": gate.evidence_digest,
                "reason_code": gate.reason_code,
            }
            for gate in record.gate_evidence
        ],
        "actor": record.actor,
        "recorded_at": record.recorded_at,
        "decision_owner": record.decision_owner,
        "writer_generation": record.writer_generation,
        "correlation_id": record.correlation_id,
        "m06_handoff": _enum_value(record.m06_handoff),
        "legacy_read_write_owner": record.legacy_read_write_owner,
        "canonical_positive_writes": bool(record.canonical_positive_writes),
        "retry_evidence_digest": record.retry_evidence_digest,
        "final_delta_checkpoint_digest": record.final_delta_checkpoint_digest,
        "parity_evidence_digest": record.parity_evidence_digest,
        "legacy_writer_fenced": bool(record.legacy_writer_fenced),
        "unknown_mappings": record.unknown_mappings,
        "unresolved_positive_bindings": record.unresolved_positive_bindings,
        "redaction": REDACTION,
    }
    _assert_redacted(payload, where="m05_promotion_record")
    return payload


def promotion_record_from_payload(payload: Mapping[str, Any]) -> PromotionRecord:
    try:
        return PromotionRecord(
            record_id=_text(payload, "record_id"),
            task_id=_text(payload, "task_id"),
            status=_m05_handoff_status(payload.get("status")),
            partition=_partition(payload.get("partition")),
            source_snapshot_digest=_text(payload, "source_snapshot_digest"),
            canonical_digest=_text(payload, "canonical_digest"),
            checkpoint=_import_checkpoint(payload.get("checkpoint")),
            imported_count=_count(payload, "imported_count"),
            replay_count=_count(payload, "replay_count"),
            quarantined_count=_count(payload, "quarantined_count"),
            conflict_count=_count(payload, "conflict_count"),
            gate_evidence=tuple(
                PromotionGateEvidence(
                    name=str(item["name"]),
                    passed=bool(item["passed"]),
                    evidence_digest=item.get("evidence_digest"),
                    reason_code=item.get("reason_code"),
                )
                for item in payload.get("gates") or ()
            ),
            actor=_text(payload, "actor"),
            recorded_at=_text(payload, "recorded_at"),
            decision_owner=_text(payload, "decision_owner"),
            writer_generation=_optional_count(payload, "writer_generation"),
            correlation_id=_text(payload, "correlation_id"),
            m06_handoff=_m05_handoff_status(payload.get("m06_handoff")),
            legacy_read_write_owner=_text(payload, "legacy_read_write_owner"),
            canonical_positive_writes=bool(payload.get("canonical_positive_writes", False)),
            retry_evidence_digest=_optional_text(payload, "retry_evidence_digest"),
            final_delta_checkpoint_digest=_optional_text(payload, "final_delta_checkpoint_digest"),
            parity_evidence_digest=_optional_text(payload, "parity_evidence_digest"),
            legacy_writer_fenced=bool(payload.get("legacy_writer_fenced", False)),
            unknown_mappings=_count(payload, "unknown_mappings"),
            unresolved_positive_bindings=_count(payload, "unresolved_positive_bindings"),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise MigrationEvidenceCorrupt(("m05_promotion_record_payload_invalid",)) from exc


def decode_promotion_row(row: Any | None) -> PromotionRecord | None:
    if row is None:
        return None
    envelope = _envelope(row)
    if envelope.get("schema") != M05_PROMOTION_SCHEMA or envelope.get("kind") != "m05_promotion_record":
        return None
    record = promotion_record_from_payload(envelope.get("record") or {})
    if (row.id, row.tenant_id, row.definition_id) != (
        record.record_id, record.partition[0], record.partition[1]
    ):
        raise MigrationEvidenceCorrupt(("m05_promotion_row_identity_drift",))
    return record


def plan_promotion_append(
    record: PromotionRecord,
    *,
    existing_by_id: Mapping[str, Any] | None,
    existing_by_key: Mapping[str, Any] | None,
) -> MarkerAppendPlan:
    stored = decode_promotion_row(existing_by_id) if existing_by_id is not None else None
    if stored is not None:
        if stored == record:
            return MarkerAppendPlan("replay", stored=stored)
        return MarkerAppendPlan("conflict", ("m05_promotion_record_payload_conflict",), stored=stored)
    if existing_by_key is not None:
        other = decode_promotion_row(existing_by_key)
        if other is not None and other != record:
            return MarkerAppendPlan(
                "conflict", ("m05_promotion_record_identity_conflict",), stored=other
            )
        if other is not None:
            return MarkerAppendPlan("replay", stored=other)
    return MarkerAppendPlan("insert")


def promotion_row_values(record: PromotionRecord) -> dict[str, Any]:
    """Column mapping for one M05 promotion append; only declared columns."""
    if not isinstance(record, PromotionRecord):
        raise MigrationEvidenceBlocked(("m05_promotion_record_type_invalid",))
    blockers: list[str] = []
    if len(record.record_id) > M05_PROMOTION_ID_WIDTH:
        blockers.append("m05_promotion_record_id_exceeds_column_width")
    if len(record.checkpoint.source_schema_version) > M05_PROMOTION_SOURCE_SCHEMA_WIDTH:
        blockers.append("m05_promotion_source_schema_exceeds_column_width")
    if len(record.correlation_id) > M05_PROMOTION_CORRELATION_WIDTH:
        blockers.append("m05_promotion_correlation_id_exceeds_column_width")
    if len(record.actor) > M05_PROMOTION_ACTOR_WIDTH:
        blockers.append("m05_promotion_actor_exceeds_column_width")
    if len(record.decision_owner) > M05_PROMOTION_OWNER_WIDTH:
        blockers.append("m05_promotion_decision_owner_exceeds_column_width")
    if record.canonical_positive_writes:
        blockers.append("m05_promotion_cannot_enable_positive_writes")
    if not is_sha256(record.canonical_digest):
        blockers.append("m05_promotion_canonical_digest_invalid")
    if record.partition != record.checkpoint.partition:
        blockers.append("m05_promotion_partition_differs_from_checkpoint")
    if blockers:
        raise MigrationEvidenceBlocked(blockers)
    gates = {gate.name: gate for gate in record.gate_evidence}
    partition_id = m05_partition_id(record.partition[0], record.partition[1])
    fenced = bool(record.legacy_writer_fenced) and record.writer_generation is not None
    return {
        "id": record.record_id,
        "tenant_id": record.partition[0],
        "definition_id": record.partition[1],
        "partition_id": partition_id,
        "revision_id": record.record_id,
        "outcome": "blocked",
        "source_snapshot_id": record.checkpoint.source_snapshot_id,
        "source_schema_version": record.checkpoint.source_schema_version,
        "source_digest": record.checkpoint.source_snapshot_digest,
        "target_digest": record.canonical_digest,
        "composite_key_evidence": {
            "partition_id_digest": digest(partition_id),
            "checkpoint_digest": record.checkpoint.digest,
            "canonical_digest": record.canonical_digest,
            "record_id_digest": digest(record.record_id),
        },
        "reciprocal_fk_evidence": {
            "legacy_writer_fenced": bool(record.legacy_writer_fenced),
            "legacy_read_write_owner": record.legacy_read_write_owner,
            "final_delta_checkpoint_digest": record.final_delta_checkpoint_digest,
        },
        "quarantine_counts": {
            "quarantined": record.quarantined_count,
            "conflicts": record.conflict_count,
            "unknown_mappings": record.unknown_mappings,
            "unresolved_positive_bindings": record.unresolved_positive_bindings,
        },
        "lifecycle_distribution": {
            "imported": record.imported_count,
            "replayed": record.replay_count,
        },
        "dependency_digests": {
            "m04_scope": _gate_digest(gates, "M04_scope"),
            "m04e_definition_version": _gate_digest(gates, "M04E_definition_version"),
            "m05_runtime": _gate_digest(gates, "M05_runtime"),
            "final_delta_checkpoint": _gate_digest(gates, "final_delta_checkpoint"),
            "legacy_writer_fence": _gate_digest(gates, "legacy_writer_fence"),
            "parity": record.parity_evidence_digest,
            "retry": record.retry_evidence_digest,
        },
        "checkpoint": record.checkpoint.digest,
        "gate_evidence": {
            "schema": M05_PROMOTION_SCHEMA,
            "kind": "m05_promotion_record",
            "record": promotion_payload(record),
        },
        "accepted_count": record.imported_count + record.replay_count,
        "quarantined_count": record.quarantined_count,
        "archived_count": 0,
        "canonical_positive_writes": False,
        "writer_generation": record.writer_generation or 0,
        "fencing_generation": (record.writer_generation or 0) if fenced else 0,
        "correlation_id": record.correlation_id,
        "actor_id": record.actor,
        "decision_owner": record.decision_owner,
        "reviewer": None,
        "previous_revision_id": None,
        "recorded_at": _recorded_at(record.recorded_at, where="m05_promotion_record"),
    }


def _gate_digest(gates: Mapping[str, PromotionGateEvidence], name: str) -> str | None:
    gate = gates.get(name)
    return gate.evidence_digest if gate is not None and is_sha256(gate.evidence_digest) else None


class SqlM05PromotionEvidenceStore:
    """`M05PromotionRecordPort` over the append-only M05 promotion evidence table.

    The immutable key is ``record_id``; the logical key is the partition plus the
    checkpoint digest. A repeated exact record replays; a different record for either key
    raises :class:`MigrationEvidenceConflict`. Readiness is persisted in the redacted
    ``gate_evidence`` envelope because the table's ``outcome`` check admits only
    ``blocked``/``quarantined``.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        scope: TrustedEvidenceScope,
    ) -> None:
        self._boundaries = _EvidenceSessionBoundaries(session_factory, scope)

    @property
    def schema_blockers(self) -> tuple[str, ...]:
        return SCHEMA_BLOCKERS

    async def append_immutable(self, record: PromotionRecord) -> None:
        if not self._boundaries.scope.admits_partition(record.partition):
            raise MigrationEvidenceBlocked(("m05_promotion_partition_outside_trusted_tenant",))
        if record.writer_generation != self._boundaries.scope.writer_generation:
            raise MigrationEvidenceBlocked(("m05_promotion_writer_generation_not_trusted",))
        values = promotion_row_values(record)
        async with self._boundaries.write() as session:
            existing_by_id = await session.get(M05PluginInstancePromotionEvidenceRow, record.record_id)
            existing_by_key = None
            if existing_by_id is None:
                existing_by_key = await self._find_by_identity(session, record)
            plan = plan_promotion_append(
                record,
                existing_by_id=_row_view(existing_by_id),
                existing_by_key=_row_view(existing_by_key),
            )
            if plan.is_conflict:
                raise MigrationEvidenceConflict(plan.blockers)
            if plan.is_replay:
                return
            session.add(M05PluginInstancePromotionEvidenceRow(**values))
            try:
                await session.flush()
            except IntegrityError as exc:
                raise MigrationEvidenceConflict(
                    ("m05_promotion_concurrent_append_conflict",)
                ) from exc

    async def load_latest(
        self, *, tenant_id: str, app_definition_id: str
    ) -> PromotionRecord | None:
        if tenant_id != self._boundaries.scope.tenant_id:
            raise MigrationEvidenceBlocked(("m05_promotion_read_outside_trusted_tenant",))
        async with self._boundaries.read() as session:
            row = await session.scalar(
                select(M05PluginInstancePromotionEvidenceRow)
                .where(
                    M05PluginInstancePromotionEvidenceRow.tenant_id == tenant_id,
                    M05PluginInstancePromotionEvidenceRow.definition_id == app_definition_id,
                    M05PluginInstancePromotionEvidenceRow.gate_evidence["schema"].astext
                    == M05_PROMOTION_SCHEMA,
                )
                .order_by(
                    M05PluginInstancePromotionEvidenceRow.recorded_at.desc(),
                    M05PluginInstancePromotionEvidenceRow.id.desc(),
                )
                .limit(1)
            )
            return decode_promotion_row(_row_view(row))

    async def _find_by_identity(
        self, session: AsyncSession, record: PromotionRecord
    ) -> M05PluginInstancePromotionEvidenceRow | None:
        return await session.scalar(
            select(M05PluginInstancePromotionEvidenceRow)
            .where(
                M05PluginInstancePromotionEvidenceRow.tenant_id == record.partition[0],
                M05PluginInstancePromotionEvidenceRow.definition_id == record.partition[1],
                M05PluginInstancePromotionEvidenceRow.checkpoint == record.checkpoint.digest,
                M05PluginInstancePromotionEvidenceRow.gate_evidence["schema"].astext
                == M05_PROMOTION_SCHEMA,
            )
            .limit(1)
        )


# ---------------------------------------------------------------------------
# Port 4: PartitionHandoffReadPort over M05 + M04E + M06 evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewedM04RlsEvidence:
    """Security-reviewed M04 policy snapshot supplied by trusted deployment wiring.

    Mirrors ``infra/.../migration_scope/adapter.py::ReviewedScopeRlsManifest``: the
    digests cover exact policy names, roles, commands, USING and WITH CHECK expressions.
    No approved M04 manifest is present in this repository, so the production default is
    ``None`` and the read port refuses rather than guessing.
    """

    review_id: str
    database_role: str
    policy_digests: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.review_id.strip() or not self.database_role.strip():
            raise ValueError("M04 RLS review ID and database role are required")
        names = tuple(name for name, _ in self.policy_digests)
        if set(names) != set(M04_OWNER_TABLES) or len(names) != len(M04_OWNER_TABLES):
            raise ValueError("M04 RLS review must bind every owner-registry table exactly once")
        if any(not is_sha256(digest) for _, digest in self.policy_digests):
            raise ValueError("M04 RLS review policy digests must use sha256 format")

    def digest_for(self, table_name: str) -> str | None:
        return dict(self.policy_digests).get(table_name)

    @property
    def digest(self) -> str:
        return digest({
            "review_id": self.review_id,
            "database_role": self.database_role,
            "policies": dict(sorted(self.policy_digests)),
        })


@dataclass(frozen=True, slots=True)
class PartitionHandoffRead:
    """Read result: exactly one of the accepted evidence or its named refusals."""

    evidence: AcceptedPartitionEvidence | None
    blockers: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if (self.evidence is None) == (not self.blockers):
            raise ValueError("a partition read is either accepted evidence or named refusals")
        if self.evidence is not None and not isinstance(self.evidence, AcceptedPartitionEvidence):
            raise ValueError("partition read evidence must be AcceptedPartitionEvidence")

    @property
    def accepted(self) -> bool:
        return self.evidence is not None


def policy_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    """Digest of one table's installed policies.

    Byte-identical to ``infra/.../migration_scope/adapter.py::_policy_digest`` so the two
    reviewed manifests cannot disagree about the same catalog rows (review finding I8/D8).
    """
    material = [
        {
            "policyname": str(row["policyname"]),
            "permissive": str(row["permissive"]),
            "roles": sorted(str(role) for role in (row["roles"] or ())),
            "cmd": str(row["cmd"]),
            "qual": row["qual"],
            "with_check": row["with_check"],
        }
        for row in rows
    ]
    encoded = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def policy_shape_is_restrictive(row: Mapping[str, Any]) -> bool:
    """Same predicate as the reviewed M04 transaction adapter."""
    command = str(row.get("cmd") or "").upper()
    if str(row.get("permissive") or "").upper() != "RESTRICTIVE":
        return False
    using = row.get("qual")
    with_check = row.get("with_check")
    if command in {"SELECT", "UPDATE", "DELETE", "ALL"} and not using:
        return False
    if command in {"INSERT", "UPDATE", "ALL"} and not with_check:
        return False
    return command in {"SELECT", "INSERT", "UPDATE", "DELETE", "ALL"}


@dataclass(frozen=True, slots=True)
class _PartitionParts:
    m05_record: PromotionRecord
    m04e_marker: M05HandoffMarker
    m04e_marker_row_id: str
    instance_row_id: str
    scope_registry_id: str
    instance_version_id: str
    accepted_sources: tuple[AcceptedSourceKey, ...]
    m06_row_id: str
    m06_schema_identity_digest: str
    m04_rls_evidence: ReviewedM04RlsEvidence


def assemble_partition_evidence(
    *,
    tenant_id: str,
    app_definition_id: str,
    app_version_id: str,
    plugin_instance_id: str,
    writer_generation: int,
    parts: _PartitionParts,
) -> AcceptedPartitionEvidence:
    """Build the M06 partition evidence from stored, re-verified artefacts.

    Every field is either a stored digest/id or a digest recomputed from stored rows;
    anything absent is a named blocker and never a placeholder.
    """
    blockers: list[str] = []
    record = parts.m05_record
    marker = parts.m04e_marker
    gates = {gate.name: gate for gate in record.gate_evidence}
    m04_scope_digest = _gate_digest(gates, "M04_scope")
    m05_instance_digest = _gate_digest(gates, "M05_runtime")
    if m04_scope_digest is None:
        blockers.append("m04_scope_evidence_digest_missing")
    if m05_instance_digest is None:
        blockers.append("m05_runtime_evidence_digest_missing")
    if record.status is not M05HandoffStatus.READY:
        blockers.append("m05_promotion_not_ready")
    if record.m06_handoff is not M05HandoffStatus.READY:
        blockers.append("m05_m06_handoff_not_ready")
    if marker.status is not M04EHandoffStatus.READY:
        blockers.append("m04e_handoff_marker_not_ready")
    if marker.partition != (tenant_id, app_definition_id):
        blockers.append("m04e_handoff_marker_partition_mismatch")
    if not is_sha256(record.canonical_digest):
        blockers.append("m05_promotion_canonical_digest_invalid")
    if marker.canonical_digest != _gate_digest(gates, "M04E_definition_version"):
        blockers.append("m04e_gate_digest_differs_from_stored_marker")
    if not parts.scope_registry_id.strip():
        blockers.append("m04_scope_evidence_id_missing")
    if not parts.instance_row_id.strip():
        blockers.append("m05_instance_runtime_evidence_id_missing")
    if not parts.m06_row_id.strip():
        blockers.append("m06_schema_evidence_id_missing")
    if not is_sha256(parts.m06_schema_identity_digest):
        blockers.append("m06_schema_identity_digest_missing")
    elif parts.m06_schema_identity_digest != schema_identity_digest():
        blockers.append("m06_schema_identity_digest_mismatch")
    final_delta = record.final_delta_checkpoint_digest
    if not isinstance(final_delta, str) or not final_delta.strip():
        blockers.append("final_delta_checkpoint_evidence_missing")
    if parts.instance_version_id != app_version_id:
        blockers.append("plugin_instance_version_mismatch")
    if writer_generation <= 0:
        blockers.append("writer_epoch_not_positive")
    accepted = tuple(sorted(
        parts.accepted_sources,
        key=lambda item: (item.source_kind, item.source_id, item.schema_version, item.source_digest),
    ))
    if not accepted:
        blockers.append("m06_accepted_source_inventory_missing")
    if blockers:
        raise MigrationEvidenceBlocked(blockers)
    return AcceptedPartitionEvidence(
        tenant_id=tenant_id,
        app_definition_id=app_definition_id,
        app_version_id=app_version_id,
        plugin_instance_id=plugin_instance_id,
        writer_epoch=writer_generation,
        m04_scope_digest=m04_scope_digest,
        m04_rls_digest=parts.m04_rls_evidence.digest,
        m04e_definition_version_digest=marker.canonical_digest,
        m05_instance_digest=m05_instance_digest,
        m06_schema_identity_digest=parts.m06_schema_identity_digest,
        m04_scope_evidence_id=parts.scope_registry_id,
        m04_rls_evidence_id=parts.m04_rls_evidence.review_id,
        m04e_definition_version_evidence_id=parts.m04e_marker_row_id,
        m05_instance_runtime_evidence_id=parts.instance_row_id,
        atomic_evidence_id=record.record_id,
        m06_schema_evidence_id=parts.m06_row_id,
        final_delta_checkpoint=final_delta,
        inventory_digest=inventory_digest(accepted),
        accepted_sources=accepted,
    )


class SqlPartitionHandoffReadStore:
    """`PartitionHandoffReadPort` over accepted, partition-bound upstream evidence.

    One read transaction, tenant-bound first, reads: the latest M05 promotion record for
    the exact partition, the exact plugin-instance row (version/scope/lifecycle), the
    M04E handoff marker that the M05 record cites, the accepted M06 grant-source
    inventory for the exact partition, the M06 promotion record for the exact four-part
    partition, and the live M04 RLS policy state verified against the reviewed manifest.
    Every artefact is re-verified against the requested keys; a missing or stale one
    yields a named refusal, never a partial object, and a partition outside the bound
    tenant is refused before any query.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        scope: TrustedEvidenceScope,
        reviewed_m04_rls: ReviewedM04RlsEvidence | None = None,
    ) -> None:
        self._boundaries = _EvidenceSessionBoundaries(session_factory, scope)
        self._reviewed = reviewed_m04_rls

    @property
    def schema_blockers(self) -> tuple[str, ...]:
        return SCHEMA_BLOCKERS

    async def load_partition(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        plugin_instance_id: str,
        writer_generation: int,
    ) -> AcceptedPartitionEvidence | None:
        result = await self.read_partition(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            plugin_instance_id=plugin_instance_id,
            writer_generation=writer_generation,
        )
        return result.evidence

    async def read_partition(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        plugin_instance_id: str,
        writer_generation: int,
    ) -> PartitionHandoffRead:
        request_blockers = self._validate_request(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            plugin_instance_id=plugin_instance_id,
            writer_generation=writer_generation,
        )
        if request_blockers:
            return PartitionHandoffRead(None, request_blockers)
        async with self._boundaries.read() as session:
            blockers: list[str] = []
            record = await self._load_m05_record(session, tenant_id, app_definition_id)
            if record is None:
                return PartitionHandoffRead(None, ("m05_promotion_evidence_missing",))
            if record.writer_generation != writer_generation:
                blockers.append("m05_promotion_stale_writer_generation")
            gates = {gate.name: gate for gate in record.gate_evidence}
            m04e_gate_digest = _gate_digest(gates, "M04E_definition_version")
            marker_row = None
            if m04e_gate_digest is None:
                blockers.append("m04e_gate_evidence_missing")
            else:
                marker_row = await self._load_marker_row(
                    session, tenant_id, app_definition_id, m04e_gate_digest
                )
                if marker_row is None:
                    blockers.append("m04e_handoff_marker_missing")
            instance = await self._load_instance(
                session, tenant_id, app_definition_id, app_version_id, plugin_instance_id
            )
            if instance is None:
                blockers.append("plugin_instance_evidence_missing")
            elif instance.lifecycle != "published":
                blockers.append("plugin_instance_not_published")
            elif not (instance.scope_registry_id or "").strip():
                blockers.append("typed_scope_anchor_missing")
            inventory = await self._load_inventory(
                session, tenant_id, app_definition_id, app_version_id, plugin_instance_id
            )
            if not inventory:
                blockers.append("m06_accepted_source_inventory_missing")
            m06_row = await self._load_m06_row(
                session, tenant_id, app_definition_id, plugin_instance_id
            )
            if m06_row is None:
                blockers.append("m06_promotion_evidence_missing")
            else:
                if int(m06_row.writer_epoch or 0) != writer_generation:
                    blockers.append("m06_writer_epoch_mismatch")
                if m06_row.outcome not in {"ready", "promotable"}:
                    blockers.append("m06_promotion_not_ready")
            rls_blockers, m04_rls = await self._verify_m04_rls(session)
            blockers.extend(rls_blockers)
            blockers.extend(DECLARED_READ_BLOCKERS)
            if blockers:
                return PartitionHandoffRead(None, tuple(blockers))
            assert marker_row is not None and instance is not None and m06_row is not None
            assert m04_rls is not None
            evidence = assemble_partition_evidence(
                tenant_id=tenant_id,
                app_definition_id=app_definition_id,
                app_version_id=app_version_id,
                plugin_instance_id=plugin_instance_id,
                writer_generation=writer_generation,
                parts=_PartitionParts(
                    m05_record=record,
                    m04e_marker=_require_marker(marker_row),
                    m04e_marker_row_id=marker_row.id,
                    instance_row_id=instance.id,
                    scope_registry_id=instance.scope_registry_id or "",
                    instance_version_id=instance.version_id,
                    accepted_sources=inventory,
                    m06_row_id=m06_row.id,
                    m06_schema_identity_digest=_m06_schema_digest(m06_row),
                    m04_rls_evidence=m04_rls,
                ),
            )
            return PartitionHandoffRead(evidence, ())

    # -- internals ---------------------------------------------------------

    def _validate_request(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        plugin_instance_id: str,
        writer_generation: int,
    ) -> tuple[str, ...]:
        blockers: list[str] = []
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            blockers.append("partition_tenant_missing")
        elif tenant_id != self._boundaries.scope.tenant_id:
            blockers.append("partition_tenant_outside_trusted_scope")
        for name, value in (
            ("app_definition_id", app_definition_id),
            ("app_version_id", app_version_id),
            ("plugin_instance_id", plugin_instance_id),
        ):
            if not isinstance(value, str) or not value.strip():
                blockers.append(f"partition_{name}_missing")
        if not isinstance(writer_generation, int) or isinstance(writer_generation, bool) or writer_generation <= 0:
            blockers.append("writer_epoch_not_positive")
        return tuple(blockers)

    async def _load_m05_record(
        self, session: AsyncSession, tenant_id: str, app_definition_id: str
    ) -> PromotionRecord | None:
        partition_id = m05_partition_id(tenant_id, app_definition_id)
        row = await session.scalar(
            select(M05PluginInstancePromotionEvidenceRow)
            .where(
                M05PluginInstancePromotionEvidenceRow.tenant_id == tenant_id,
                M05PluginInstancePromotionEvidenceRow.definition_id == app_definition_id,
                M05PluginInstancePromotionEvidenceRow.partition_id == partition_id,
                M05PluginInstancePromotionEvidenceRow.gate_evidence["schema"].astext
                == M05_PROMOTION_SCHEMA,
            )
            .order_by(
                M05PluginInstancePromotionEvidenceRow.recorded_at.desc(),
                M05PluginInstancePromotionEvidenceRow.id.desc(),
            )
            .limit(1)
        )
        return decode_promotion_row(_row_view(row))

    async def _load_marker_row(
        self, session: AsyncSession, tenant_id: str, app_definition_id: str, digest_value: str
    ) -> M04EDefinitionVersionPromotionEvidenceRow | None:
        return await session.scalar(
            select(M04EDefinitionVersionPromotionEvidenceRow)
            .where(
                M04EDefinitionVersionPromotionEvidenceRow.tenant_id == tenant_id,
                M04EDefinitionVersionPromotionEvidenceRow.definition_id == app_definition_id,
                M04EDefinitionVersionPromotionEvidenceRow.target_digest == digest_value,
                M04EDefinitionVersionPromotionEvidenceRow.gate_evidence["schema"].astext
                == M04E_HANDOFF_SCHEMA,
            )
            .order_by(
                M04EDefinitionVersionPromotionEvidenceRow.recorded_at,
                M04EDefinitionVersionPromotionEvidenceRow.id,
            )
            .limit(1)
        )

    async def _load_instance(
        self,
        session: AsyncSession,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        plugin_instance_id: str,
    ) -> PluginInstanceRegistryRow | None:
        return await session.scalar(
            select(PluginInstanceRegistryRow).where(
                PluginInstanceRegistryRow.id == plugin_instance_id,
                PluginInstanceRegistryRow.tenant_id == tenant_id,
                PluginInstanceRegistryRow.definition_id == app_definition_id,
                PluginInstanceRegistryRow.version_id == app_version_id,
            )
        )

    async def _load_inventory(
        self,
        session: AsyncSession,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        plugin_instance_id: str,
    ) -> tuple[AcceptedSourceKey, ...]:
        """Accepted M06 grant-source inventory for the exact partition.

        ``AcceptedPartitionEvidence.admits`` compares a ``LegacyGrantSource`` against
        this inventory, so it must be the mapped grant sources of this exact partition,
        not the M05 instance sources. ``dynamic_app_capability_grant_mappings`` carries
        the source identity/digest but no tenant column: the tenant and the exact
        definition/version/instance anchors are proven through its target grant.
        """
        rows = (
            await session.execute(
                select(CanonicalGrantMappingRow)
                .join(
                    CanonicalGrantRow,
                    CanonicalGrantRow.id == CanonicalGrantMappingRow.target_grant_id,
                )
                .where(
                    CanonicalGrantRow.tenant_id == tenant_id,
                    CanonicalGrantRow.app_definition_id == app_definition_id,
                    CanonicalGrantRow.app_version_id == app_version_id,
                    CanonicalGrantRow.plugin_instance_id == plugin_instance_id,
                    CanonicalGrantRow.revoked_at.is_(None),
                    CanonicalGrantMappingRow.status == "mapped",
                    CanonicalGrantMappingRow.target_grant_id.is_not(None),
                )
                .order_by(
                    CanonicalGrantMappingRow.source_kind,
                    CanonicalGrantMappingRow.source_id,
                    CanonicalGrantMappingRow.canonical_schema_version,
                )
            )
        ).scalars()
        keys: list[AcceptedSourceKey] = []
        identities: set[tuple[str, str, str]] = set()
        for row in rows:
            if not is_sha256(row.source_payload_digest):
                continue
            identity = (row.source_kind, row.source_id, row.canonical_schema_version)
            if identity in identities:
                raise MigrationEvidenceCorrupt(("m06_mapping_inventory_duplicate_identity",))
            identities.add(identity)
            keys.append(AcceptedSourceKey(
                source_kind=row.source_kind,
                source_id=row.source_id,
                schema_version=row.canonical_schema_version,
                source_digest=row.source_payload_digest,
            ))
        return tuple(keys)

    async def _load_m06_row(
        self,
        session: AsyncSession,
        tenant_id: str,
        app_definition_id: str,
        plugin_instance_id: str,
    ) -> M06CanonicalGrantPromotionEvidenceRow | None:
        partition_id = m06_partition_id(tenant_id, app_definition_id, plugin_instance_id)
        return await session.scalar(
            select(M06CanonicalGrantPromotionEvidenceRow)
            .where(
                M06CanonicalGrantPromotionEvidenceRow.tenant_id == tenant_id,
                M06CanonicalGrantPromotionEvidenceRow.definition_id == app_definition_id,
                M06CanonicalGrantPromotionEvidenceRow.partition_id == partition_id,
            )
            .order_by(
                M06CanonicalGrantPromotionEvidenceRow.recorded_at.desc(),
                M06CanonicalGrantPromotionEvidenceRow.id.desc(),
            )
            .limit(1)
        )

    async def _verify_m04_rls(
        self, session: AsyncSession
    ) -> tuple[tuple[str, ...], ReviewedM04RlsEvidence | None]:
        if self._reviewed is None:
            return ("m04_rls_review_manifest_missing",), None
        role = await session.scalar(text("SELECT current_user"))
        if not isinstance(role, str) or role != self._reviewed.database_role:
            return ("m04_rls_database_role_mismatch",), None
        digests: dict[str, str] = {}
        blockers: list[str] = []
        for table_name in M04_OWNER_TABLES:
            relation = (
                await session.execute(
                    text(
                        "SELECT n.nspname AS schema_name, c.relrowsecurity AS rls_enabled, "
                        "c.relforcerowsecurity AS rls_forced "
                        "FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE c.oid = to_regclass(:relation_name) AND c.relkind IN ('r', 'p')"
                    ),
                    {"relation_name": table_name},
                )
            ).mappings().one_or_none()
            if relation is None:
                blockers.append(f"m04_rls_relation_missing:{table_name}")
                continue
            if not relation["rls_enabled"]:
                blockers.append(f"m04_rls_disabled:{table_name}")
            if not relation["rls_forced"]:
                blockers.append(f"m04_rls_not_forced:{table_name}")
            policies = (
                await session.execute(
                    text(
                        "SELECT policyname, permissive, roles, cmd, qual, with_check "
                        "FROM pg_policies WHERE schemaname = :schema_name "
                        "AND tablename = :table_name ORDER BY policyname"
                    ),
                    {"schema_name": relation["schema_name"], "table_name": table_name},
                )
            ).mappings().all()
            if not policies:
                blockers.append(f"m04_rls_policy_missing:{table_name}")
                continue
            if any(not policy_shape_is_restrictive(row) for row in policies):
                blockers.append(f"m04_rls_policy_incomplete:{table_name}")
            current = policy_digest(policies)
            if self._reviewed.digest_for(table_name) != current:
                blockers.append(f"m04_rls_review_mismatch:{table_name}")
            digests[table_name] = current
        if blockers:
            return tuple(blockers), None
        return (), self._reviewed


def _require_marker(row: M04EDefinitionVersionPromotionEvidenceRow) -> M05HandoffMarker:
    marker = decode_marker_row(_row_view(row))
    if marker is None:
        raise MigrationEvidenceCorrupt(("m04e_handoff_marker_row_unreadable",))
    return marker


def _m06_schema_digest(row: M06CanonicalGrantPromotionEvidenceRow) -> str:
    dependencies = dict(row.dependency_digests or {})
    for key in M06_DEPENDENCY_KEYS:
        value = dependencies.get(key)
        if is_sha256(value):
            return value
    return ""


# ---------------------------------------------------------------------------
# Envelope codecs
# ---------------------------------------------------------------------------

_RECORD_FIELDS = (
    "source_kind", "source_id", "schema_version", "source_digest", "canonical_digest",
    "canonical_identity", "checkpoint", "status", "reason_code", "correlation_id",
    "tenant_id", "app_definition_id", "phase", "source_snapshot_digest", "proof_evidence",
    "artifact_uri", "artifact_digest", "verified_artifact_digest", "current_artifact_digest",
    "transaction_id", "writer_generation", "observed_source_digest", "observed_artifact_digest",
    "blocker_evidence",
)


@dataclass(frozen=True, slots=True)
class _RowView:
    """Minimal read-only view of a promotion row, so decoders stay pure."""

    id: str
    tenant_id: str
    definition_id: str
    revision_id: str
    outcome: str
    source_snapshot_id: str
    source_digest: str
    target_digest: str
    gate_evidence: Mapping[str, Any]


def _row_view(row: Any | None) -> _RowView | None:
    if row is None:
        return None
    if isinstance(row, _RowView):
        return row
    return _RowView(
        id=row.id,
        tenant_id=row.tenant_id,
        definition_id=row.definition_id,
        revision_id=row.revision_id,
        outcome=row.outcome,
        source_snapshot_id=row.source_snapshot_id,
        source_digest=row.source_digest,
        target_digest=row.target_digest,
        gate_evidence=row.gate_evidence,
    )


def _envelope(view: Any) -> Mapping[str, Any]:
    if view is None:
        raise MigrationEvidenceCorrupt(("evidence_row_missing",))
    envelope = view.gate_evidence
    if not isinstance(envelope, Mapping):
        raise MigrationEvidenceCorrupt(("evidence_envelope_invalid",))
    return envelope


def _checkpoint_payload(checkpoint: BackfillCheckpoint) -> dict[str, Any]:
    return {
        "partition": list(checkpoint.partition),
        "phase": checkpoint.phase,
        "after_source_id": checkpoint.after_source_id,
        "sequence": checkpoint.sequence,
        "source_snapshot_id": checkpoint.source_snapshot_id,
        "source_schema_version": checkpoint.source_schema_version,
        "source_snapshot_digest": checkpoint.source_snapshot_digest,
        "canonical_schema_version": checkpoint.canonical_schema_version,
        "writer_generation": checkpoint.writer_generation,
        "phase_complete": checkpoint.phase_complete,
    }


def _checkpoint(payload: Any) -> BackfillCheckpoint:
    if not isinstance(payload, Mapping):
        raise MigrationEvidenceCorrupt(("backfill_checkpoint_payload_invalid",))
    return BackfillCheckpoint(
        partition=_partition(payload.get("partition")),
        phase=_text(payload, "phase"),
        after_source_id=_optional_text(payload, "after_source_id"),
        sequence=_count(payload, "sequence"),
        source_snapshot_id=_text(payload, "source_snapshot_id"),
        source_schema_version=_text(payload, "source_schema_version"),
        source_snapshot_digest=_text(payload, "source_snapshot_digest"),
        canonical_schema_version=_text(payload, "canonical_schema_version"),
        writer_generation=_optional_count(payload, "writer_generation"),
        phase_complete=bool(payload.get("phase_complete", False)),
    )


def _import_checkpoint(payload: Any) -> ImportCheckpoint:
    if not isinstance(payload, Mapping):
        raise MigrationEvidenceCorrupt(("import_checkpoint_payload_invalid",))
    return ImportCheckpoint(
        partition=_partition(payload.get("partition")),
        source_snapshot_id=_text(payload, "source_snapshot_id"),
        source_schema_version=_text(payload, "source_schema_version"),
        source_snapshot_digest=_text(payload, "source_snapshot_digest"),
        after_source_id=_optional_text(payload, "after_source_id"),
        sequence=_count(payload, "sequence"),
    )


def _gates(payload: Any) -> tuple[HandoffGate, ...]:
    if not isinstance(payload, list):
        raise MigrationEvidenceCorrupt(("handoff_gate_evidence_invalid",))
    return tuple(
        HandoffGate(
            name=str(item["name"]),
            passed=bool(item["passed"]),
            evidence_digest=item.get("evidence_digest"),
            reason=item.get("reason"),
        )
        for item in payload
    )


def _enum_value(value: Any) -> str:
    return getattr(value, "value", str(value))


def _handoff_status(value: Any) -> M04EHandoffStatus:
    try:
        return M04EHandoffStatus(str(value))
    except ValueError as exc:
        raise MigrationEvidenceCorrupt(("handoff_status_invalid",)) from exc


def _m05_handoff_status(value: Any) -> M05HandoffStatus:
    try:
        return M05HandoffStatus(str(value))
    except ValueError as exc:
        raise MigrationEvidenceCorrupt(("m05_handoff_status_invalid",)) from exc


def _evidence_status(value: Any) -> EvidenceStatus:
    try:
        return EvidenceStatus(str(value))
    except ValueError as exc:
        raise MigrationEvidenceCorrupt(("evidence_status_invalid",)) from exc


def _backfill_outcome(value: Any) -> BackfillOutcome:
    try:
        return BackfillOutcome(str(value))
    except ValueError as exc:
        raise MigrationEvidenceCorrupt(("backfill_outcome_invalid",)) from exc


def _partition(value: Any) -> tuple[str, str]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise MigrationEvidenceCorrupt(("partition_payload_invalid",))
    return (str(value[0]), str(value[1]))


def _text(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise MigrationEvidenceCorrupt((f"evidence_field_missing:{key}",))
    return value


def _optional_text(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise MigrationEvidenceCorrupt((f"evidence_field_invalid:{key}",))
    return value


def _count(payload: Mapping[str, Any], key: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MigrationEvidenceCorrupt((f"evidence_count_invalid:{key}",))
    return value


def _optional_count(payload: Mapping[str, Any], key: str) -> int | None:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise MigrationEvidenceCorrupt((f"evidence_count_invalid:{key}",))
    return value


__all__ = [
    "DECLARED_READ_BLOCKERS",
    "M04E_BACKFILL_SCHEMA",
    "M04E_HANDOFF_SCHEMA",
    "M04_OWNER_TABLES",
    "M05_PROMOTION_SCHEMA",
    "M06_DEPENDENCY_KEYS",
    "MarkerAppendPlan",
    "MigrationEvidenceBlocked",
    "MigrationEvidenceConflict",
    "MigrationEvidenceCorrupt",
    "PartitionHandoffRead",
    "REDACTION",
    "ReviewedM04RlsEvidence",
    "SCHEMA_BLOCKERS",
    "SqlM04EBackfillEvidenceStore",
    "SqlM05HandoffEvidenceStore",
    "SqlM05PromotionEvidenceStore",
    "SqlPartitionHandoffReadStore",
    "TrustedEvidenceScope",
    "assemble_partition_evidence",
    "backfill_checkpoint_digest",
    "backfill_observation_key",
    "backfill_source_key",
    "bind_evidence_transaction",
    "decode_marker_row",
    "decode_promotion_row",
    "digest",
    "handoff_marker_row_values",
    "is_sha256",
    "m05_partition_id",
    "m06_partition_id",
    "marker_ledger_key",
    "marker_payload",
    "next_checkpoint",
    "page_ledger_key",
    "plan_marker_append",
    "plan_promotion_append",
    "policy_digest",
    "policy_shape_is_restrictive",
    "promotion_payload",
    "promotion_record_from_payload",
    "promotion_row_values",
    "record_from_payload",
    "record_payload",
]
