"""Fail-closed SQLAlchemy boundary for M04 scope persistence.

This adapter binds tenant GUCs to an owned PostgreSQL transaction and checks live
schema/role evidence. It deliberately cannot persist or resolve scopes while the
M04 checkpoint, canonical schema lineage, RLS review, and lifecycle gates are absent.
"""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator, Mapping

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    BackfillOutcome,
    MigrationEvidenceContext,
    ScopeMigrationResult,
    ScopeSourceEvidence,
    ScopeSourceQuarantineEvidence,
    ScopeStreamIdentity,
    TrustedScopeTransactionPort,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    QuarantineRecord,
    ScopeAnchors,
    ScopeKind,
    ScopeRegistryEntry,
)
from prodavan.infrastructure.persistence.models.identity import MembershipRow
from prodavan.infrastructure.persistence.models.owner_registry import ScopeOwnerMappingRow


_OWNER_TABLES = (
    "scope_registry",
    "scope_owner_tenant_anchors",
    "scope_owner_cabinet_anchors",
    "scope_owner_project_anchors",
    "scope_owner_mappings",
    "scope_owner_quarantine",
    "scope_owner_audit",
)
_REQUIRED_UNIQUE_CONSTRAINTS = (
    ("scope_registry", "uq_scope_registry_kind_id"),
    ("scope_owner_mappings", "uq_scope_owner_mapping_source"),
    ("projects", "uq_projects_scope_owner_parent_tuple"),
    ("cabinet_company_grants", "uq_cabinet_company_grant"),
    ("memberships", "uq_membership_company_employee"),
)


@dataclass(frozen=True, slots=True)
class ReviewedScopeRlsManifest:
    """Security-reviewed policy snapshot supplied by trusted deployment wiring.

    Policy digests cover exact policy names, roles, commands, USING and WITH CHECK
    expressions. A caller/request cannot provide this configuration. No approved
    M04 manifest is currently present, so the production default is ``None``.
    """

    review_id: str
    database_role: str
    policy_digests: tuple[tuple[str, str], ...]

    def __post_init__(self) -> None:
        if not self.review_id.strip() or not self.database_role.strip():
            raise ValueError("review ID and database role are required")
        names = tuple(name for name, _ in self.policy_digests)
        if set(names) != set(_OWNER_TABLES) or len(names) != len(_OWNER_TABLES):
            raise ValueError("review must bind every M04 owner-registry table exactly once")
        if any(re.fullmatch(r"sha256:[0-9a-f]{64}", digest) is None for _, digest in self.policy_digests):
            raise ValueError("review policy digests must use sha256 format")

    def digest_for(self, table_name: str) -> str | None:
        return dict(self.policy_digests).get(table_name)


@dataclass(frozen=True, slots=True)
class _TransactionBinding:
    application_transaction_id: str
    database_transaction_id: str
    tenant_id: str
    database_role: str


class ScopePersistenceGateBlocked(RuntimeError):
    """A durable M04 invariant is absent or contradicted by database evidence."""

    def __init__(self, blockers: tuple[str, ...]) -> None:
        self.blockers = blockers
        super().__init__(", ".join(blockers))


class SqlAlchemyTrustedScopeTransactionPort(TrustedScopeTransactionPort):
    """SQLAlchemy implementation that denies unsupported M04 persistence paths.

    Each operation owns one database transaction. Tenant, cabinet, project, actor,
    role, and writer generation are set with PostgreSQL ``set_config(..., true)``;
    the values are read back before any evidence query. Missing evidence never
    falls back to a global/session-level context or an in-memory checkpoint.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        rls_manifest: ReviewedScopeRlsManifest | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._rls_manifest = rls_manifest

    async def load_checkpoint(
        self, *, context: MigrationEvidenceContext, stream: ScopeStreamIdentity
    ) -> BackfillCheckpoint | None:
        if not context.usable or stream.tenant_id != context.tenant_id:
            raise ScopePersistenceGateBlocked(("checkpoint_tenant_or_transaction_context_invalid",))
        async with self._bound_transaction(context) as (session, _binding):
            blockers = await self._gate_blockers(session, context)
            if blockers:
                # Returning None would let the coordinator invent an initial cursor.
                raise ScopePersistenceGateBlocked(blockers)
            # No checkpoint row/model contract exists in M04-01. Do not guess a
            # table name or key layout and do not initialize a cursor in memory.
            raise ScopePersistenceGateBlocked(("m04_checkpoint_persistence_schema_missing",))

    async def classify_and_append(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        source_mapping_identity: tuple[str, str, str],
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        proposed_entry: ScopeRegistryEntry | None,
        proposed_quarantine: QuarantineRecord | ScopeSourceQuarantineEvidence | None,
        reason_code: str | None,
    ) -> ScopeMigrationResult:
        if not context.usable:
            return self._blocked_result(
                context=context,
                candidate=candidate,
                checkpoint=expected_checkpoint,
                reason="append_only_audit_unavailable",
            )
        self._validate_command(
            context=context,
            candidate=candidate,
            source_mapping_identity=source_mapping_identity,
            expected_checkpoint=expected_checkpoint,
            next_checkpoint=next_checkpoint,
            proposed_entry=proposed_entry,
            proposed_quarantine=proposed_quarantine,
        )
        try:
            async with self._bound_transaction(context) as (session, _binding):
                blockers = await self._gate_blockers(session, context)
                if blockers:
                    return self._blocked_result(
                        context=context,
                        candidate=candidate,
                        checkpoint=expected_checkpoint,
                        reason="append_only_audit_unavailable",
                    )
                # Deliberately no write path until the exact checkpoint and mapping
                # schema are registered and independently reviewed.
                return self._blocked_result(
                    context=context,
                    candidate=candidate,
                    checkpoint=expected_checkpoint,
                    reason="append_only_audit_unavailable",
                )
        except Exception:
            # Database/role/catalog failures are not evidence that a row is safe.
            return self._blocked_result(
                context=context,
                candidate=candidate,
                checkpoint=expected_checkpoint,
                reason="append_only_audit_unavailable",
            )

    async def resolve_active(
        self,
        *,
        context: MigrationEvidenceContext,
        anchors: ScopeAnchors,
    ) -> ScopeResolutionEvidence | None:
        if (
            anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE
            or not context.usable
            or not context.matches_scope(anchors)
        ):
            return None
        try:
            async with self._bound_transaction(context) as (session, _binding):
                if await self._gate_blockers(session, context):
                    return None
                # No M04 read can be promoted without a persisted canonical schema
                # lineage/checkpoint gate and reviewed parent-membership policies.
                return None
        except Exception:
            return None

    @asynccontextmanager
    async def _bound_transaction(
        self, context: MigrationEvidenceContext
    ) -> AsyncIterator[tuple[AsyncSession, _TransactionBinding]]:
        if not context.usable or context.rls_context is None:
            raise ScopePersistenceGateBlocked(("trusted_transaction_context_missing",))
        async with self._session_factory() as session:
            if session.get_bind().dialect.name != "postgresql":
                raise ScopePersistenceGateBlocked(("postgresql_rls_backend_required",))
            async with session.begin():
                tx_row = (await session.execute(text("SELECT txid_current()::text AS txid"))).mappings().one()
                database_transaction_id = str(tx_row["txid"])
                if not database_transaction_id.isdigit():
                    raise ScopePersistenceGateBlocked(("database_transaction_identity_missing",))
                if context.transaction_id != database_transaction_id:
                    raise ScopePersistenceGateBlocked(("application_database_transaction_identity_mismatch",))
                rls = context.rls_context
                values = {
                    "tenant_id": rls.tenant_id,
                    "cabinet_id": rls.cabinet_id or "",
                    "project_id": rls.project_id or "",
                    "user_id": rls.user_id,
                    "role": rls.role,
                    "writer_generation": str(context.writer_generation),
                }
                await session.execute(
                    text(
                        "SELECT "
                        "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
                        "set_config('app.cabinet_id', :cabinet_id, true) AS cabinet_id, "
                        "set_config('app.project_id', :project_id, true) AS project_id, "
                        "set_config('app.user_id', :user_id, true) AS user_id, "
                        "set_config('app.role', :role, true) AS role, "
                        "set_config('app.writer_generation', :writer_generation, true) AS writer_generation"
                    ),
                    values,
                )
                readback = (
                    await session.execute(
                        text(
                            "SELECT current_setting('app.tenant_id', true) AS tenant_id, "
                            "current_setting('app.cabinet_id', true) AS cabinet_id, "
                            "current_setting('app.project_id', true) AS project_id, "
                            "current_setting('app.user_id', true) AS user_id, "
                            "current_setting('app.role', true) AS role, "
                            "current_setting('app.writer_generation', true) AS writer_generation, "
                            "current_setting('row_security', true) AS row_security, "
                            "current_user AS database_role"
                        )
                    )
                ).mappings().one()
                expected = {**values, "row_security": "on"}
                if any(readback.get(key) != value for key, value in expected.items()):
                    raise ScopePersistenceGateBlocked(("transaction_local_rls_context_readback_mismatch",))
                binding = _TransactionBinding(
                    application_transaction_id=context.transaction_id or "",
                    database_transaction_id=database_transaction_id,
                    tenant_id=context.tenant_id or "",
                    database_role=str(readback["database_role"]),
                )
                try:
                    yield session, binding
                finally:
                    cleared = (
                        await session.execute(
                            text(
                                "SELECT "
                                "set_config('app.tenant_id', '', true) AS tenant_id, "
                                "set_config('app.cabinet_id', '', true) AS cabinet_id, "
                                "set_config('app.project_id', '', true) AS project_id, "
                                "set_config('app.user_id', '', true) AS user_id, "
                                "set_config('app.role', '', true) AS role, "
                                "set_config('app.writer_generation', '', true) AS writer_generation"
                            )
                        )
                    ).mappings().one()
                    if any(cleared.get(key) != "" for key in values):
                        raise ScopePersistenceGateBlocked(("transaction_local_rls_context_clear_unproven",))

    async def _gate_blockers(
        self, session: AsyncSession, context: MigrationEvidenceContext
    ) -> tuple[str, ...]:
        blockers = list(_declared_schema_blockers())
        role_row = (
            await session.execute(
                text(
                    "SELECT current_user AS role, r.rolbypassrls AS bypass_rls, "
                    "r.rolsuper AS superuser "
                    "FROM pg_roles r WHERE r.rolname = current_user"
                )
            )
        ).mappings().one_or_none()
        if role_row is None:
            blockers.append("database_role_evidence_missing")
        else:
            role = str(role_row["role"])
            if role != context.role:
                blockers.append("database_role_context_mismatch")
            if bool(role_row["bypass_rls"]):
                blockers.append("database_role_bypasses_rls")
            if bool(role_row["superuser"]):
                blockers.append("database_role_is_superuser")

        for table_name in _OWNER_TABLES:
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
                blockers.append(f"database_relation_missing:{table_name}")
                continue
            if not relation["rls_enabled"]:
                blockers.append(f"row_level_security_disabled:{table_name}")
            if not relation["rls_forced"]:
                blockers.append(f"row_level_security_not_forced:{table_name}")
            policy_rows = (
                await session.execute(
                    text(
                        "SELECT policyname, permissive, roles, cmd, qual, with_check "
                        "FROM pg_policies WHERE schemaname = :schema_name AND tablename = :table_name "
                        "ORDER BY policyname"
                    ),
                    {"schema_name": relation["schema_name"], "table_name": table_name},
                )
            ).mappings().all()
            if not policy_rows:
                blockers.append(f"row_level_security_policy_missing:{table_name}")
            elif any(not _policy_shape_is_restrictive(row) for row in policy_rows):
                blockers.append(f"row_level_security_policy_incomplete:{table_name}")
            if self._rls_manifest is None:
                blockers.append("m04_rls_review_manifest_missing")
            elif (
                self._rls_manifest.database_role != context.role
                or self._rls_manifest.digest_for(table_name) != _policy_digest(policy_rows)
            ):
                blockers.append(f"m04_rls_review_mismatch:{table_name}")

        for table_name, constraint_name in _REQUIRED_UNIQUE_CONSTRAINTS:
            found = await session.scalar(
                text(
                    "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                    "WHERE conrelid = to_regclass(:table_name) "
                    "AND conname = :constraint_name AND contype IN ('u', 'p'))"
                ),
                {"table_name": table_name, "constraint_name": constraint_name},
            )
            if not found:
                blockers.append(f"required_unique_constraint_missing:{constraint_name}")

        audit_privileges = (
            await session.execute(
                text(
                    "SELECT has_table_privilege(current_user, to_regclass('scope_owner_audit'), 'INSERT') AS can_insert, "
                    "has_table_privilege(current_user, to_regclass('scope_owner_audit'), 'UPDATE') AS can_update, "
                    "has_table_privilege(current_user, to_regclass('scope_owner_audit'), 'DELETE') AS can_delete"
                )
            )
        ).mappings().one_or_none()
        if (
            audit_privileges is None
            or not audit_privileges["can_insert"]
            or audit_privileges["can_update"]
            or audit_privileges["can_delete"]
        ):
            blockers.append("append_only_audit_privileges_unproven")
        return tuple(dict.fromkeys(blockers))

    @staticmethod
    def _validate_command(
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        source_mapping_identity: tuple[str, str, str],
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        proposed_entry: ScopeRegistryEntry | None,
        proposed_quarantine: QuarantineRecord | ScopeSourceQuarantineEvidence | None,
    ) -> None:
        expected_identity = (candidate.source_kind, candidate.source_id, candidate.schema_version)
        if source_mapping_identity != expected_identity:
            raise ValueError("source mapping identity does not match immutable source evidence")
        if expected_checkpoint.tenant_id != context.tenant_id:
            raise ValueError("expected checkpoint tenant does not match trusted transaction")
        if expected_checkpoint.writer_generation != context.writer_generation:
            raise ValueError("expected checkpoint writer generation does not match trusted transaction")
        if candidate.anchors is not None and candidate.anchors.tenant_id != context.tenant_id:
            raise ValueError("source candidate tenant does not match trusted transaction")
        if candidate.schema_version != expected_checkpoint.source_schema_version:
            raise ValueError("source candidate schema does not match checkpoint lineage")
        if candidate.canonical_schema_version != expected_checkpoint.canonical_schema_version:
            raise ValueError("canonical schema does not match checkpoint lineage")
        if (
            next_checkpoint.stream_key != expected_checkpoint.stream_key
            or next_checkpoint.source_snapshot_digest != expected_checkpoint.source_snapshot_digest
            or next_checkpoint.sequence != expected_checkpoint.sequence + 1
            or next_checkpoint.after_source_id != candidate.source_id
        ):
            raise ValueError("next checkpoint is not the exact sequence-CAS successor")
        if proposed_entry is not None:
            provenance = proposed_entry.provenance
            if proposed_entry.owner_state is not OwnerState.QUARANTINED:
                raise ValueError("M04 persistence accepts only quarantined proposed registry evidence")
            if (
                candidate.anchors is None
                or proposed_entry.anchors != candidate.anchors
                or provenance.source_kind != candidate.source_kind
                or provenance.source_id != candidate.source_id
                or provenance.schema_version != candidate.schema_version
                or provenance.payload_digest != candidate.source_digest
                or candidate.source_state.value != "active"
            ):
                raise ValueError("proposed registry evidence does not match the exact active source candidate")
        if proposed_quarantine is not None:
            quarantine_digest = (
                proposed_quarantine.source_digest
                if isinstance(proposed_quarantine, ScopeSourceQuarantineEvidence)
                else proposed_quarantine.payload_digest
            )
            if (
                proposed_quarantine.source_kind != candidate.source_kind
                or proposed_quarantine.source_id != candidate.source_id
                or proposed_quarantine.schema_version != candidate.schema_version
                or quarantine_digest != candidate.source_digest
            ):
                raise ValueError("quarantine evidence does not match the exact source key and digest")
        if candidate.anchors is not None and candidate.anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            if proposed_entry is not None:
                raise ValueError("plugin-instance scope cannot propose a registry row")

    @staticmethod
    def _blocked_result(
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        checkpoint: BackfillCheckpoint,
        reason: str,
    ) -> ScopeMigrationResult:
        anchors = candidate.anchors
        return ScopeMigrationResult(
            outcome=BackfillOutcome.BLOCKED,
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            source_digest=candidate.source_digest,
            canonical_schema_version=candidate.canonical_schema_version,
            canonical_digest=None,
            checkpoint=checkpoint,
            reason_code=reason,
            scope_kind=anchors.scope_kind if anchors else None,
            scope_id=anchors.scope_id if anchors else None,
            correlation_id=context.correlation_id or "missing-correlation",
            writer_generation=context.writer_generation,
            canonical_positive_writes=False,
        )


def _declared_schema_blockers() -> tuple[str, ...]:
    """Static gaps which live catalog inspection cannot repair or infer."""
    blockers = []
    if "canonical_schema_version" not in ScopeOwnerMappingRow.__table__.c:
        blockers.append("canonical_schema_version_not_persisted_on_source_mapping")
    # No reviewed checkpoint model/key exists in the owned ORM contract.
    blockers.append("m04_checkpoint_model_and_key_contract_missing")
    if "status" not in MembershipRow.__table__.c or "deleted_at" not in MembershipRow.__table__.c:
        blockers.append("membership_row_has_no_active_or_deleted_lifecycle")
    # ORM declarations do not install PostgreSQL RLS, privileges, or append-only
    # audit rules. Runtime checks are still required even if the schema is present.
    blockers.append("m04_rls_and_append_only_security_review_required")
    blockers.append("m04_scope_promotion_gate_evidence_missing")
    return tuple(blockers)


def _policy_shape_is_restrictive(row: Mapping[str, object]) -> bool:
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


def _policy_digest(rows: list[Mapping[str, object]]) -> str:
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


__all__ = [
    "ReviewedScopeRlsManifest",
    "ScopePersistenceGateBlocked",
    "SqlAlchemyTrustedScopeTransactionPort",
]
