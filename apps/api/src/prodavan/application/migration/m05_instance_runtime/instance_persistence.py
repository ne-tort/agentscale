"""M05 durable plugin-instance persistence: exact resolution, generation CAS, bounded import
cursor, immutable quarantine history and redacted audit.

Scope
-----
This module is the concrete PostgreSQL implementation of the M05 application ports declared in
:mod:`runtime_service`: ``InstanceImportStore``, ``InstanceImportCandidateReader``,
``InstanceQuarantineRetryPort``, ``RuntimeInstanceLifecycle``, ``RedactedRuntimeAuditPort`` and
the durable half of ``ResolvePluginInstancePort``.  It adds **no** DDL, ORM model or Alembic
revision and it never publishes, grants or cuts over.

Relations already created by the migration chain (used here)
    ``m05_plugin_instances``, ``m05_plugin_instance_registry_links`` (revision ``2026110303``),
    ``m04e_dynamic_app_definitions``, ``m04e_dynamic_app_versions`` (``2026110302``),
    ``scope_registry`` (``2026110301``), ``employees`` and ``memberships``.

Relations this adapter requires and revision ``2026110303`` does **not** create
    ``m05_plugin_instance_source_snapshot`` — immutable pinned source page rows;
    ``m05_plugin_instance_import_checkpoints`` — the durable page cursor;
    ``m05_plugin_instance_import_quarantine`` — original quarantine / conflict / blocked
    evidence;
    ``m05_plugin_instance_quarantine_attempts`` — append-only contiguous retry attempts;
    ``m05_plugin_instance_runtime_audit`` — redacted audit rows.

Their exact shape is frozen in ``evidence-schema.md`` next to this module and asserted by
:func:`verify_evidence_relations`, which fails closed when a relation or column is missing.
A cursor, quarantine history or audit trail cannot be faked in memory on the production path,
so a missing relation is a *blocked* migration state, never a silent fallback.

Transaction ownership
---------------------
Every entry point owns exactly one PostgreSQL transaction.  The transaction-local tenant
context is **injected** (:class:`TrustedTenantContextPort`, implemented by
``infrastructure/persistence/rls``) and verified through the database transaction id: there is
no session-level ``SET``, no in-memory cursor and no read-through fallback.  A positive mapping,
its cursor advance, its conflict/quarantine evidence and its audit row commit or roll back
together.

Redaction
---------
Audit rows carry only ``event``, ``outcome``, ``reason_code``, ``correlation_id`` and the two
numeric generations.  No payload, token, credential, subject/session value, tenant/scope label
or full instance identity is written to the audit relation.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, replace
from typing import AsyncIterator, Mapping, Protocol, Sequence

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.dynamic_apps_definition.contracts import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionVersionRef,
)
from prodavan.application.migration.m04_scope.scope_migration import (
    ScopeResolution,
    ScopeResolutionResult,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    FailClosedResolvePluginInstance,
    ImportCheckpoint,
    ImportOutcome,
    InstanceBindingEvidence,
    InstanceIdentity,
    InstanceImportCandidate,
    InstanceImportCandidateReader,
    InstanceImportRecord,
    InstanceImportStore,
    InstanceLifecycleState,
    InstanceQuarantineRetryPort,
    InstanceResolveQuery,
    InstanceSourceRuntime,
    M05IdentityEvidencePort,
    QuarantineRetryAttempt,
    QuarantineRetryState,
    RedactedRuntimeAuditPort,
    RuntimeAuditRecord,
    RuntimeAvailability,
    RuntimeInstanceLifecycle,
    RuntimeSnapshot,
    SubjectSessionBindingEvidence,
    TrustedRuntimeContext,
    WriterCasResult,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ScopeAnchors,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)
from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
    RequestedCapability,
)


# ---------------------------------------------------------------------------
# Relations and column contract
# ---------------------------------------------------------------------------

INSTANCE_TABLE = "m05_plugin_instances"
LINK_TABLE = "m05_plugin_instance_registry_links"
DEFINITION_TABLE = "m04e_dynamic_app_definitions"
VERSION_TABLE = "m04e_dynamic_app_versions"
REGISTRY_TABLE = "scope_registry"
SNAPSHOT_TABLE = "m05_plugin_instance_source_snapshot"
CHECKPOINT_TABLE = "m05_plugin_instance_import_checkpoints"
QUARANTINE_TABLE = "m05_plugin_instance_import_quarantine"
ATTEMPT_TABLE = "m05_plugin_instance_quarantine_attempts"
AUDIT_TABLE = "m05_plugin_instance_runtime_audit"

#: Relations created by the current migration chain.
EXISTING_RELATIONS: tuple[str, ...] = (
    INSTANCE_TABLE,
    LINK_TABLE,
    DEFINITION_TABLE,
    VERSION_TABLE,
    REGISTRY_TABLE,
)

#: Relations this adapter requires and the current chain does not create.
DECLARED_RELATIONS: tuple[str, ...] = (
    SNAPSHOT_TABLE,
    CHECKPOINT_TABLE,
    QUARANTINE_TABLE,
    ATTEMPT_TABLE,
    AUDIT_TABLE,
)

#: Required columns per relation; :func:`verify_evidence_relations` proves each one exists.
REQUIRED_COLUMNS: Mapping[str, tuple[str, ...]] = {
    SNAPSHOT_TABLE: (
        "tenant_id", "app_definition_id", "source_snapshot_id", "source_schema_version",
        "source_snapshot_digest", "page_position", "source_kind", "source_id", "payload",
        "payload_digest", "plugin_instance_id", "version_id", "app_id", "manifest_version",
        "scope_id", "cabinet_id", "project_id", "artifact_digest", "subject_id", "session_id",
        "lifecycle", "availability", "runtime_generation", "writer_generation",
    ),
    CHECKPOINT_TABLE: (
        "tenant_id", "definition_id", "source_snapshot_id", "source_schema_version",
        "source_snapshot_digest", "after_source_id", "sequence",
    ),
    QUARANTINE_TABLE: (
        "id", "tenant_id", "definition_id", "source_kind", "source_id",
        "source_schema_version", "source_digest", "original_digest", "outcome", "reason_code",
        "correlation_id", "checkpoint", "cursor_checkpoint", "source_snapshot_id",
        "source_schema_version_snapshot", "source_snapshot_digest", "after_source_id",
        "sequence", "attempt_count",
    ),
    ATTEMPT_TABLE: (
        "quarantine_id", "attempt_number", "outcome", "reason_code", "correlation_id",
        "attempt_digest", "source_digest",
    ),
    AUDIT_TABLE: (
        "event", "outcome", "reason_code", "correlation_id", "runtime_generation",
        "writer_generation",
    ),
}

#: Closed reason set used by this module; every value is a stable redacted code.
BLOCKED_MISSING_CONTEXT = "authenticated_runtime_context_missing"
BLOCKED_TENANT_CONTEXT = "trusted_tenant_context_unproven"
BLOCKED_POSTGRES_ONLY = "postgresql_required"
BLOCKED_SCHEMA = "m05_evidence_schema_incomplete"
BLOCKED_CURSOR = "checkpoint_compare_and_set_conflict"
BLOCKED_CONFLICT = "immutable_source_identity_conflict"
BLOCKED_STALE = "stale_generation_zero_rows"

_IMPORT_EVENT = "plugin_instance.import"

_TERMINAL_LIFECYCLES = frozenset(
    {InstanceLifecycleState.REVOKED.value, InstanceLifecycleState.QUARANTINED.value}
)


class M05PersistenceBlocked(RuntimeError):
    """Fail-closed persistence precondition; nothing was committed."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(reason_code if not detail else f"{reason_code}: {detail}")
        self.reason_code = reason_code


def _digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    """Registered ``String(40)`` identifier; never longer than the column."""
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _row_digest(values: Mapping[str, object]) -> str:
    return _digest({key: values[key] for key in sorted(values)})


# ---------------------------------------------------------------------------
# Trusted tenant context
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class M05RlsBinding:
    """Evidence that the injected context really applies to this transaction."""

    tenant_id: str
    correlation_id: str
    database_transaction_id: str
    row_security_enabled: bool
    row_security_forced: bool
    tenant_policy_enforced: bool
    using_policy_enforced: bool
    with_check_policy_enforced: bool
    missing_context_denies: bool
    role_not_bypass_rls: bool
    trusted_service: bool

    def all_verified(self) -> bool:
        return bool(
            self.row_security_enabled
            and self.row_security_forced
            and self.tenant_policy_enforced
            and self.using_policy_enforced
            and self.with_check_policy_enforced
            and self.missing_context_denies
            and self.role_not_bypass_rls
        )


class TrustedTenantContextPort(Protocol):
    """Transaction-local tenant context; implemented outside this zone.

    ``bind`` runs inside an already open transaction, applies the GUCs with
    ``set_config(..., true)`` (never a session-level ``SET``), reads them back and returns the
    verified facts, including the database transaction id the context is bound to.
    """

    async def bind(
        self,
        *,
        session: AsyncSession,
        tenant_id: str,
        subject_id: str,
        session_id: str,
        correlation_id: str,
        trusted: bool,
    ) -> M05RlsBinding: ...


_SCHEMA_PROBE_SQL = (
    "SELECT to_regclass(:relation) IS NOT NULL AS present"
)

_COLUMN_PROBE_SQL = (
    "SELECT count(*) AS present FROM information_schema.columns "
    "WHERE table_schema = current_schema() AND table_name = :relation "
    "AND column_name = ANY(:columns)"
)

_DATABASE_TRANSACTION_SQL = "SELECT txid_current()::text AS database_transaction_id"


async def verify_evidence_relations(session: AsyncSession) -> None:
    """Read-only proof that every required M05 evidence relation and column exists.

    The probe never creates, alters or repairs anything.  A missing relation blocks the
    partition before a candidate read, a mapping write or a positive resolution.
    """

    missing: list[str] = []
    for relation in (*EXISTING_RELATIONS, *DECLARED_RELATIONS):
        present = (
            await session.execute(text(_SCHEMA_PROBE_SQL), {"relation": relation})
        ).scalar()
        if not present:
            missing.append(relation)
            continue
        required = REQUIRED_COLUMNS.get(relation)
        if required is None:
            continue
        found = (
            await session.execute(
                text(_COLUMN_PROBE_SQL),
                {"relation": relation, "columns": list(required)},
            )
        ).scalar()
        if int(found or 0) != len(required):
            missing.append(f"{relation}(columns)")
    if missing:
        raise M05PersistenceBlocked(BLOCKED_SCHEMA, ",".join(sorted(missing)))


# ---------------------------------------------------------------------------
# SQL statements (explicit; the acceptance artifacts assert them literally)
# ---------------------------------------------------------------------------

_INSTANCE_SELECT = (
    "SELECT i.id, i.plugin_instance_id, i.definition_id, i.version_id, i.tenant_id, "
    "i.scope_kind, i.scope_id, i.cabinet_id, i.project_id, i.scope_registry_id, "
    "i.artifact_digest, i.subject_id, i.session_id, i.lifecycle, i.runtime_generation, "
    "i.writer_generation, i.source_kind, i.source_id, i.source_schema_version, "
    "i.source_digest, i.checkpoint, v.app_id, v.manifest_version, "
    "d.display_name AS definition_display_name, d.description AS definition_description, "
    "d.scope_kind AS definition_scope_kind, d.scope_id AS definition_scope_id, "
    "d.lifecycle AS definition_lifecycle, d.current_version_id, "
    "d.created_at AS definition_created_at, d.updated_at AS definition_updated_at, "
    "v.manifest, v.requested_capabilities, v.lifecycle AS version_lifecycle, "
    "v.created_at AS version_created_at, v.published_at AS version_published_at "
    f"FROM {INSTANCE_TABLE} AS i "
    f"JOIN {VERSION_TABLE} AS v "
    "ON v.id = i.version_id AND v.definition_id = i.definition_id "
    "AND v.tenant_id = i.tenant_id AND v.artifact_digest = i.artifact_digest "
    f"JOIN {DEFINITION_TABLE} AS d "
    "ON d.id = i.definition_id AND d.tenant_id = i.tenant_id "
)

#: Exact resolver read: complete identity, published lifecycle, exact generations, exact
#: subject/session and an active reciprocal registry link, all in one statement.
_SELECT_EXACT_INSTANCE_SQL = (
    _INSTANCE_SELECT
    + "WHERE i.id = :plugin_instance_id AND i.plugin_instance_id = :plugin_instance_id "
    "AND i.definition_id = :definition_id AND i.version_id = :version_id "
    "AND i.tenant_id = :tenant_id AND i.scope_kind = :scope_kind "
    "AND i.scope_id = :scope_id AND i.artifact_digest = :artifact_digest "
    "AND i.subject_id = :subject_id AND i.session_id = :session_id "
    "AND i.cabinet_id IS NOT DISTINCT FROM :cabinet_id "
    "AND i.project_id IS NOT DISTINCT FROM :project_id "
    "AND v.app_id = :app_id AND v.manifest_version = :manifest_version "
    "AND i.lifecycle = 'published' "
    "AND i.runtime_generation = :runtime_generation "
    "AND i.writer_generation = :writer_generation "
    "AND EXISTS (SELECT 1 FROM " + LINK_TABLE + " AS l "
    "WHERE l.plugin_instance_id = i.plugin_instance_id AND l.tenant_id = i.tenant_id "
    "AND l.scope_kind = 'plugin_instance' AND l.scope_id = i.plugin_instance_id "
    "AND l.owner_state = 'active' "
    "AND l.cabinet_id IS NOT DISTINCT FROM i.cabinet_id "
    "AND l.project_id IS NOT DISTINCT FROM i.project_id)"
)

#: Diagnostic read used only to classify a zero-row resolve result.  It never authorizes.
_SELECT_INSTANCE_STATE_SQL = (
    "SELECT i.lifecycle, i.runtime_generation, i.writer_generation, i.artifact_digest, "
    "i.subject_id, i.session_id, i.plugin_instance_id, i.definition_id, i.version_id, "
    f"(SELECT count(*) FROM {LINK_TABLE} AS l "
    "WHERE l.plugin_instance_id = i.plugin_instance_id AND l.tenant_id = i.tenant_id "
    "AND l.owner_state = 'active') AS active_links "
    f"FROM {INSTANCE_TABLE} AS i "
    "WHERE i.tenant_id = :tenant_id AND i.definition_id = :definition_id "
    "AND i.plugin_instance_id = :plugin_instance_id"
)

_SELECT_INSTANCE_BY_SOURCE_SQL = (
    _INSTANCE_SELECT
    + "WHERE i.tenant_id = :tenant_id AND i.definition_id = :definition_id "
    "AND i.source_kind = :source_kind AND i.source_id = :source_id "
    "AND i.source_schema_version = :source_schema_version"
)

_SELECT_INSTANCE_BY_PLUGIN_ID_SQL = (
    _INSTANCE_SELECT
    + "WHERE i.tenant_id = :tenant_id AND i.definition_id = :definition_id "
    "AND i.plugin_instance_id = :plugin_instance_id"
)

_SELECT_SNAPSHOT_POSITION_SQL = (
    "SELECT page_position, source_snapshot_id, source_schema_version, source_snapshot_digest, "
    "payload_digest "
    f"FROM {SNAPSHOT_TABLE} "
    "WHERE tenant_id = :tenant_id AND app_definition_id = :definition_id "
    "AND source_kind = :source_kind AND source_id = :source_id "
    "AND source_schema_version = :source_schema_version"
)

_INSERT_INSTANCE_SQL = (
    f"INSERT INTO {INSTANCE_TABLE} ("
    "id, plugin_instance_id, definition_id, version_id, tenant_id, scope_kind, scope_id, "
    "cabinet_id, project_id, scope_registry_id, artifact_digest, subject_id, session_id, "
    "lifecycle, runtime_generation, writer_generation, source_kind, source_id, "
    "source_schema_version, source_digest, checkpoint"
    ") VALUES ("
    ":id, :plugin_instance_id, :definition_id, :version_id, :tenant_id, 'plugin_instance', "
    ":scope_id, :cabinet_id, :project_id, :scope_registry_id, :artifact_digest, :subject_id, "
    ":session_id, 'published', :runtime_generation, :writer_generation, :source_kind, "
    ":source_id, :source_schema_version, :source_digest, :checkpoint"
    ") ON CONFLICT ON CONSTRAINT uq_m05_instance_source_identity DO NOTHING "
    "RETURNING id"
)

_INSERT_LINK_SQL = (
    f"INSERT INTO {LINK_TABLE} ("
    "id, plugin_instance_id, tenant_id, scope_kind, scope_id, cabinet_id, project_id, owner_state"
    ") VALUES ("
    ":id, :plugin_instance_id, :tenant_id, 'plugin_instance', :plugin_instance_id, "
    ":cabinet_id, :project_id, 'active'"
    ") ON CONFLICT ON CONSTRAINT uq_m05_registry_link_instance DO NOTHING "
    "RETURNING id"
)

_SELECT_SCOPE_REGISTRY_ID_SQL = (
    f"SELECT id FROM {REGISTRY_TABLE} "
    "WHERE tenant_id = :tenant_id AND scope_kind = :scope_kind AND scope_id = :scope_id "
    "AND plugin_instance_id IS NOT DISTINCT FROM :plugin_instance_id "
    "AND cabinet_id IS NOT DISTINCT FROM :cabinet_id "
    "AND project_id IS NOT DISTINCT FROM :project_id "
    "AND owner_state = 'active'"
)

_SELECT_REGISTRY_ENTRY_SQL = (
    "SELECT scope_kind, scope_id, tenant_id, cabinet_id, project_id, plugin_instance_id, "
    "owner_state, source_kind, source_id, schema_version, payload_digest "
    f"FROM {REGISTRY_TABLE} "
    "WHERE tenant_id = :tenant_id AND scope_kind = :scope_kind AND scope_id = :scope_id "
    "AND cabinet_id IS NOT DISTINCT FROM :cabinet_id "
    "AND project_id IS NOT DISTINCT FROM :project_id "
    "AND plugin_instance_id IS NOT DISTINCT FROM :plugin_instance_id"
)

_SELECT_LINK_STATE_SQL = (
    f"SELECT owner_state, cabinet_id, project_id FROM {LINK_TABLE} "
    "WHERE plugin_instance_id = :plugin_instance_id AND tenant_id = :tenant_id"
)

_SELECT_CHECKPOINT_SQL = (
    "SELECT source_snapshot_id, source_schema_version, source_snapshot_digest, "
    "after_source_id, sequence "
    f"FROM {CHECKPOINT_TABLE} "
    "WHERE tenant_id = :tenant_id AND definition_id = :definition_id "
    "AND source_snapshot_id = :source_snapshot_id "
    "AND source_schema_version = :source_schema_version"
)

_SELECT_CHECKPOINT_FOR_UPDATE_SQL = _SELECT_CHECKPOINT_SQL + " FOR UPDATE"

_INSERT_CHECKPOINT_SQL = (
    f"INSERT INTO {CHECKPOINT_TABLE} ("
    "tenant_id, definition_id, source_snapshot_id, source_schema_version, "
    "source_snapshot_digest, after_source_id, sequence"
    ") VALUES ("
    ":tenant_id, :definition_id, :source_snapshot_id, :source_schema_version, "
    ":source_snapshot_digest, :after_source_id, :sequence"
    ")"
)

#: Zero-row stale-cursor barrier: a second runner of the same page changes nothing.
_ADVANCE_CHECKPOINT_SQL = (
    f"UPDATE {CHECKPOINT_TABLE} "
    "SET after_source_id = :after_source_id, sequence = :sequence, "
    "source_snapshot_digest = :source_snapshot_digest "
    "WHERE tenant_id = :tenant_id AND definition_id = :definition_id "
    "AND source_snapshot_id = :source_snapshot_id "
    "AND source_schema_version = :source_schema_version "
    "AND source_snapshot_digest = :expected_digest "
    "AND sequence = :expected_sequence "
    "AND after_source_id IS NOT DISTINCT FROM :expected_after_source_id"
)

_INSERT_NEGATIVE_SQL = (
    f"INSERT INTO {QUARANTINE_TABLE} ("
    "id, tenant_id, definition_id, source_kind, source_id, source_schema_version, "
    "source_digest, original_digest, outcome, reason_code, correlation_id, checkpoint, "
    "cursor_checkpoint, source_snapshot_id, source_schema_version_snapshot, "
    "source_snapshot_digest, after_source_id, sequence, attempt_count"
    ") VALUES ("
    ":id, :tenant_id, :definition_id, :source_kind, :source_id, :source_schema_version, "
    ":source_digest, :original_digest, :outcome, :reason_code, :correlation_id, :checkpoint, "
    ":cursor_checkpoint, :source_snapshot_id, :source_schema_version_snapshot, "
    ":source_snapshot_digest, :after_source_id, :sequence, 0"
    ") ON CONFLICT ON CONSTRAINT uq_m05_import_negative_evidence DO NOTHING "
    "RETURNING id"
)

_SELECT_NEGATIVE_SQL = (
    "SELECT id, tenant_id, definition_id, source_kind, source_id, source_schema_version, "
    "source_digest, original_digest, outcome, reason_code, correlation_id, checkpoint, "
    "cursor_checkpoint, source_snapshot_id, source_schema_version_snapshot, "
    "source_snapshot_digest, after_source_id, sequence, attempt_count "
    f"FROM {QUARANTINE_TABLE} "
    "WHERE tenant_id = :tenant_id AND definition_id = :definition_id "
    "AND source_kind = :source_kind AND source_id = :source_id "
    "AND source_schema_version = :source_schema_version "
    "ORDER BY CASE outcome WHEN 'quarantined' THEN 0 WHEN 'blocked' THEN 1 ELSE 2 END, id "
    "LIMIT 1"
)

_SELECT_QUARANTINE_FOR_UPDATE_SQL = (
    "SELECT id, tenant_id, definition_id, source_kind, source_id, source_schema_version, "
    "source_digest, original_digest, outcome, reason_code, correlation_id, checkpoint, "
    "cursor_checkpoint, source_snapshot_id, source_schema_version_snapshot, "
    "source_snapshot_digest, after_source_id, sequence, attempt_count "
    f"FROM {QUARANTINE_TABLE} "
    "WHERE tenant_id = :tenant_id AND definition_id = :definition_id "
    "AND source_kind = :source_kind AND source_id = :source_id "
    "AND source_schema_version = :source_schema_version "
    "AND outcome = 'quarantined' "
    "ORDER BY id LIMIT 1 FOR UPDATE"
)

#: Attempt-count CAS: a stale attempt counter changes zero rows and never replaces evidence.
_ADVANCE_ATTEMPT_COUNT_SQL = (
    f"UPDATE {QUARANTINE_TABLE} SET attempt_count = attempt_count + 1 "
    "WHERE id = :id AND attempt_count = :expected_attempt_count AND outcome = 'quarantined' "
    "RETURNING attempt_count"
)

_INSERT_ATTEMPT_SQL = (
    f"INSERT INTO {ATTEMPT_TABLE} ("
    "quarantine_id, attempt_number, outcome, reason_code, correlation_id, attempt_digest, "
    "source_digest"
    ") VALUES ("
    ":quarantine_id, :attempt_number, :outcome, :reason_code, :correlation_id, "
    ":attempt_digest, :source_digest"
    ")"
)

_SELECT_ATTEMPTS_SQL = (
    "SELECT attempt_number, outcome, reason_code, correlation_id, attempt_digest, source_digest "
    f"FROM {ATTEMPT_TABLE} "
    "WHERE quarantine_id = :quarantine_id ORDER BY attempt_number"
)

_INSERT_AUDIT_SQL = (
    f"INSERT INTO {AUDIT_TABLE} ("
    "event, outcome, reason_code, correlation_id, runtime_generation, writer_generation"
    ") VALUES ("
    ":event, :outcome, :reason_code, :correlation_id, :runtime_generation, :writer_generation"
    ")"
)

_CAS_WRITER_SQL = (
    f"UPDATE {INSTANCE_TABLE} AS i SET writer_generation = i.writer_generation + 1 "
    "WHERE i.id = :plugin_instance_id AND i.plugin_instance_id = :plugin_instance_id "
    "AND i.definition_id = :definition_id AND i.version_id = :version_id "
    "AND i.tenant_id = :tenant_id AND i.scope_kind = :scope_kind "
    "AND i.scope_id = :scope_id AND i.artifact_digest = :artifact_digest "
    "AND i.subject_id = :subject_id AND i.session_id = :session_id "
    "AND i.cabinet_id IS NOT DISTINCT FROM :cabinet_id "
    "AND i.project_id IS NOT DISTINCT FROM :project_id "
    "AND i.lifecycle = 'published' "
    "AND i.runtime_generation = :expected_runtime_generation "
    "AND i.writer_generation = :expected_writer_generation "
    "AND EXISTS (SELECT 1 FROM " + LINK_TABLE + " AS l "
    "WHERE l.plugin_instance_id = i.plugin_instance_id AND l.tenant_id = i.tenant_id "
    "AND l.owner_state = 'active') "
    "RETURNING i.id"
)

#: Runtime restart advances both epochs exactly one; a stale epoch changes zero rows.
_CAS_RUNTIME_SQL = (
    f"UPDATE {INSTANCE_TABLE} AS i "
    "SET runtime_generation = i.runtime_generation + 1, "
    "writer_generation = i.writer_generation + 1 "
    "WHERE i.id = :plugin_instance_id AND i.plugin_instance_id = :plugin_instance_id "
    "AND i.definition_id = :definition_id AND i.version_id = :version_id "
    "AND i.tenant_id = :tenant_id AND i.scope_kind = :scope_kind "
    "AND i.scope_id = :scope_id AND i.artifact_digest = :artifact_digest "
    "AND i.subject_id = :subject_id AND i.session_id = :session_id "
    "AND i.cabinet_id IS NOT DISTINCT FROM :cabinet_id "
    "AND i.project_id IS NOT DISTINCT FROM :project_id "
    "AND i.lifecycle = 'published' "
    "AND i.runtime_generation = :expected_runtime_generation "
    "AND i.writer_generation = :expected_writer_generation "
    "AND EXISTS (SELECT 1 FROM " + LINK_TABLE + " AS l "
    "WHERE l.plugin_instance_id = i.plugin_instance_id AND l.tenant_id = i.tenant_id "
    "AND l.owner_state = 'active') "
    "RETURNING i.id"
)

#: Terminal ``revoked``/``quarantined`` rows are unreachable for every transition.
_CAS_LIFECYCLE_SQL = (
    f"UPDATE {INSTANCE_TABLE} AS i "
    "SET lifecycle = :next_lifecycle, writer_generation = i.writer_generation + 1 "
    "WHERE i.id = :plugin_instance_id AND i.plugin_instance_id = :plugin_instance_id "
    "AND i.definition_id = :definition_id AND i.version_id = :version_id "
    "AND i.tenant_id = :tenant_id AND i.scope_kind = :scope_kind "
    "AND i.scope_id = :scope_id AND i.artifact_digest = :artifact_digest "
    "AND i.subject_id = :subject_id AND i.session_id = :session_id "
    "AND i.cabinet_id IS NOT DISTINCT FROM :cabinet_id "
    "AND i.project_id IS NOT DISTINCT FROM :project_id "
    "AND i.lifecycle = :expected_lifecycle "
    "AND i.lifecycle NOT IN ('revoked', 'quarantined') "
    "AND i.runtime_generation = :expected_runtime_generation "
    "AND i.writer_generation = :expected_writer_generation "
    "AND EXISTS (SELECT 1 FROM " + LINK_TABLE + " AS l "
    "WHERE l.plugin_instance_id = i.plugin_instance_id AND l.tenant_id = i.tenant_id "
    "AND l.owner_state = 'active') "
    "RETURNING i.id"
)

_SELECT_SNAPSHOT_PAGE_SQL = (
    "SELECT tenant_id, app_definition_id, source_snapshot_id, source_schema_version, "
    "source_snapshot_digest, page_position, source_kind, source_id, payload, payload_digest, "
    "plugin_instance_id, version_id, app_id, manifest_version, scope_id, cabinet_id, "
    "project_id, artifact_digest, subject_id, session_id, lifecycle, availability, "
    "runtime_generation, writer_generation "
    f"FROM {SNAPSHOT_TABLE} "
    "WHERE tenant_id = :tenant_id AND app_definition_id = :definition_id "
    "AND source_snapshot_id = :source_snapshot_id "
    "AND source_schema_version = :source_schema_version "
    "AND source_snapshot_digest = :source_snapshot_digest "
    "AND (:after_source_id IS NULL OR source_id > :after_source_id) "
    "ORDER BY source_id LIMIT :limit"
)

_SELECT_DEFINITION_SQL = (
    "SELECT id, tenant_id, app_id, display_name, description, scope_kind, scope_id, lifecycle, "
    f"current_version_id, created_at, updated_at FROM {DEFINITION_TABLE} "
    "WHERE id = :definition_id AND tenant_id = :tenant_id AND app_id = :app_id"
)

_SELECT_VERSION_SQL = (
    "SELECT id, definition_id, tenant_id, app_id, manifest_version, artifact_digest, manifest, "
    f"requested_capabilities, lifecycle, created_at, published_at FROM {VERSION_TABLE} "
    "WHERE id = :version_id AND definition_id = :definition_id AND tenant_id = :tenant_id"
)

_SELECT_ACTIVE_SUBJECT_SQL = (
    "SELECT count(*) AS matches FROM employees AS e "
    "JOIN memberships AS m ON m.employee_id = e.id AND m.company_id = :tenant_id "
    "WHERE e.id = :subject_id AND e.deleted_at IS NULL AND e.status = 'active'"
)


# ---------------------------------------------------------------------------
# Bound transaction
# ---------------------------------------------------------------------------


class SqlAlchemyM05Transaction:
    """Port operations bound to one open, tenant-scoped transaction."""

    def __init__(
        self,
        session: AsyncSession,
        binding: M05RlsBinding,
        context: TrustedRuntimeContext,
    ) -> None:
        self.session = session
        self.binding = binding
        self.context = context

    # -- helpers ----------------------------------------------------------

    @property
    def tenant_id(self) -> str:
        return self.binding.tenant_id

    def _params(self, identity: InstanceIdentity) -> dict[str, object]:
        return {
            "plugin_instance_id": identity.plugin_instance_id,
            "definition_id": identity.definition_id,
            "version_id": identity.version_id,
            "tenant_id": identity.tenant_id,
            "scope_kind": identity.scope_kind.value,
            "scope_id": identity.scope_id,
            "artifact_digest": identity.artifact_digest,
            "subject_id": identity.subject_id,
            "session_id": identity.session_id,
            "cabinet_id": identity.installation_scope.cabinet_id,
            "project_id": identity.installation_scope.project_id,
            "app_id": identity.app_id,
            "manifest_version": identity.manifest_version,
        }

    def require_tenant(self, tenant_id: str) -> None:
        if tenant_id != self.binding.tenant_id:
            raise M05PersistenceBlocked(BLOCKED_TENANT_CONTEXT, "partition tenant mismatch")

    # -- instance reads ---------------------------------------------------

    async def select_exact_instance(
        self, *, query: InstanceResolveQuery
    ) -> list[Mapping[str, object]]:
        identity = query.identity
        self.require_tenant(identity.tenant_id)
        params = self._params(identity) | {
            "runtime_generation": query.runtime_generation,
            "writer_generation": query.writer_generation,
        }
        rows = (await self.session.execute(text(_SELECT_EXACT_INSTANCE_SQL), params)).mappings().all()
        return [dict(row) for row in rows]

    async def select_instance_state(
        self, *, tenant_id: str, definition_id: str, plugin_instance_id: str
    ) -> dict[str, object] | None:
        self.require_tenant(tenant_id)
        row = (
            await self.session.execute(
                text(_SELECT_INSTANCE_STATE_SQL),
                {
                    "tenant_id": tenant_id,
                    "definition_id": definition_id,
                    "plugin_instance_id": plugin_instance_id,
                },
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def select_instance_by_source(
        self, *, partition: tuple[str, str], source_kind: str, source_id: str, schema_version: str
    ) -> dict[str, object] | None:
        self.require_tenant(partition[0])
        row = (
            await self.session.execute(
                text(_SELECT_INSTANCE_BY_SOURCE_SQL),
                {
                    "tenant_id": partition[0],
                    "definition_id": partition[1],
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "source_schema_version": schema_version,
                },
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def select_instance_by_plugin_id(
        self, *, partition: tuple[str, str], plugin_instance_id: str
    ) -> dict[str, object] | None:
        self.require_tenant(partition[0])
        row = (
            await self.session.execute(
                text(_SELECT_INSTANCE_BY_PLUGIN_ID_SQL),
                {
                    "tenant_id": partition[0],
                    "definition_id": partition[1],
                    "plugin_instance_id": plugin_instance_id,
                },
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    # -- snapshot / registry reads ---------------------------------------

    async def select_snapshot_position(
        self, *, partition: tuple[str, str], source_kind: str, source_id: str, schema_version: str
    ) -> dict[str, object] | None:
        self.require_tenant(partition[0])
        row = (
            await self.session.execute(
                text(_SELECT_SNAPSHOT_POSITION_SQL),
                {
                    "tenant_id": partition[0],
                    "definition_id": partition[1],
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "source_schema_version": schema_version,
                },
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def select_scope_registry_id(
        self, *, tenant_id: str, anchors: ScopeAnchors, plugin_instance_id: str | None
    ) -> str | None:
        self.require_tenant(tenant_id)
        rows = (
            await self.session.execute(
                text(_SELECT_SCOPE_REGISTRY_ID_SQL),
                {
                    "tenant_id": tenant_id,
                    "scope_kind": anchors.scope_kind.value,
                    "scope_id": anchors.scope_id,
                    "cabinet_id": anchors.cabinet_id,
                    "project_id": anchors.project_id,
                    "plugin_instance_id": plugin_instance_id,
                },
            )
        ).scalars().all()
        if len(rows) != 1:
            return None
        return str(rows[0])

    async def select_registry_entry(
        self, *, anchors: ScopeAnchors, plugin_instance_id: str | None
    ) -> ScopeRegistryEntry | None:
        self.require_tenant(anchors.tenant_id)
        row = (
            await self.session.execute(
                text(_SELECT_REGISTRY_ENTRY_SQL),
                {
                    "tenant_id": anchors.tenant_id,
                    "scope_kind": anchors.scope_kind.value,
                    "scope_id": anchors.scope_id,
                    "cabinet_id": anchors.cabinet_id,
                    "project_id": anchors.project_id,
                    "plugin_instance_id": plugin_instance_id,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        try:
            entry_anchors = ScopeAnchors(
                scope_kind=ScopeKind(row["scope_kind"]),
                scope_id=row["scope_id"],
                tenant_id=row["tenant_id"],
                cabinet_id=row["cabinet_id"],
                project_id=row["project_id"],
                plugin_instance_id=row["plugin_instance_id"],
            )
            return ScopeRegistryEntry(
                anchors=entry_anchors,
                owner_state=OwnerState(row["owner_state"]),
                provenance=ScopeProvenance(
                    source_kind=row["source_kind"],
                    source_id=row["source_id"],
                    schema_version=row["schema_version"],
                    payload_digest=row["payload_digest"],
                ),
            )
        except ValueError as error:
            raise M05PersistenceBlocked(BLOCKED_TENANT_CONTEXT, "registry row unreadable") from error

    async def select_link_state(
        self, *, tenant_id: str, plugin_instance_id: str
    ) -> dict[str, object] | None:
        self.require_tenant(tenant_id)
        row = (
            await self.session.execute(
                text(_SELECT_LINK_STATE_SQL),
                {"tenant_id": tenant_id, "plugin_instance_id": plugin_instance_id},
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    # -- checkpoint -------------------------------------------------------

    async def select_checkpoint(
        self, *, checkpoint: ImportCheckpoint, for_update: bool = False
    ) -> ImportCheckpoint | None:
        self.require_tenant(checkpoint.partition[0])
        statement = _SELECT_CHECKPOINT_FOR_UPDATE_SQL if for_update else _SELECT_CHECKPOINT_SQL
        row = (
            await self.session.execute(
                text(statement),
                {
                    "tenant_id": checkpoint.partition[0],
                    "definition_id": checkpoint.partition[1],
                    "source_snapshot_id": checkpoint.source_snapshot_id,
                    "source_schema_version": checkpoint.source_schema_version,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        return ImportCheckpoint(
            checkpoint.partition,
            row["source_snapshot_id"],
            row["source_schema_version"],
            row["source_snapshot_digest"],
            row["after_source_id"],
            int(row["sequence"]),
        )

    async def insert_checkpoint(self, checkpoint: ImportCheckpoint) -> None:
        self.require_tenant(checkpoint.partition[0])
        try:
            await self.session.execute(
                text(_INSERT_CHECKPOINT_SQL),
                {
                    "tenant_id": checkpoint.partition[0],
                    "definition_id": checkpoint.partition[1],
                    "source_snapshot_id": checkpoint.source_snapshot_id,
                    "source_schema_version": checkpoint.source_schema_version,
                    "source_snapshot_digest": checkpoint.source_snapshot_digest,
                    "after_source_id": checkpoint.after_source_id,
                    "sequence": checkpoint.sequence,
                },
            )
        except IntegrityError as error:
            raise M05PersistenceBlocked(BLOCKED_CURSOR, "cursor row already exists") from error

    async def advance_checkpoint(
        self, *, expected: ImportCheckpoint, next_checkpoint: ImportCheckpoint
    ) -> None:
        self.require_tenant(expected.partition[0])
        result = await self.session.execute(
            text(_ADVANCE_CHECKPOINT_SQL),
            {
                "tenant_id": expected.partition[0],
                "definition_id": expected.partition[1],
                "source_snapshot_id": expected.source_snapshot_id,
                "source_schema_version": expected.source_schema_version,
                "source_snapshot_digest": next_checkpoint.source_snapshot_digest,
                "expected_digest": expected.source_snapshot_digest,
                "expected_sequence": expected.sequence,
                "expected_after_source_id": expected.after_source_id,
                "after_source_id": next_checkpoint.after_source_id,
                "sequence": next_checkpoint.sequence,
            },
        )
        if result.rowcount != 1:
            raise M05PersistenceBlocked(BLOCKED_CURSOR, "durable cursor compare-and-set")

    # -- evidence writes --------------------------------------------------

    async def insert_negative_evidence(
        self,
        *,
        record: InstanceImportRecord,
        original_digest: str | None,
        mapping_checkpoint: ImportCheckpoint,
        cursor_checkpoint: ImportCheckpoint,
    ) -> str | None:
        self.require_tenant(record.checkpoint.partition[0])
        evidence_id = _new_id("m05q")
        result = await self.session.execute(
            text(_INSERT_NEGATIVE_SQL),
            {
                "id": evidence_id,
                "tenant_id": record.checkpoint.partition[0],
                "definition_id": record.checkpoint.partition[1],
                "source_kind": record.source_kind,
                "source_id": record.source_id,
                "source_schema_version": record.schema_version,
                "source_digest": record.source_digest,
                "original_digest": original_digest,
                "outcome": record.outcome.value,
                "reason_code": record.reason_code or record.outcome.value,
                "correlation_id": record.correlation_id,
                "checkpoint": mapping_checkpoint.digest,
                "cursor_checkpoint": cursor_checkpoint.digest,
                "source_snapshot_id": mapping_checkpoint.source_snapshot_id,
                "source_schema_version_snapshot": mapping_checkpoint.source_schema_version,
                "source_snapshot_digest": mapping_checkpoint.source_snapshot_digest,
                "after_source_id": mapping_checkpoint.after_source_id,
                "sequence": mapping_checkpoint.sequence,
            },
        )
        row = result.first()
        # An identical re-observation is idempotent: the first evidence row stands and is
        # never overwritten.  A different digest/checkpoint produces a different row.
        return None if row is None else str(row[0])

    async def select_negative_evidence(
        self, *, partition: tuple[str, str], source_kind: str, source_id: str, schema_version: str
    ) -> dict[str, object] | None:
        self.require_tenant(partition[0])
        row = (
            await self.session.execute(
                text(_SELECT_NEGATIVE_SQL),
                {
                    "tenant_id": partition[0],
                    "definition_id": partition[1],
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "source_schema_version": schema_version,
                },
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def lock_quarantine(
        self, *, partition: tuple[str, str], source_kind: str, source_id: str, schema_version: str
    ) -> dict[str, object] | None:
        self.require_tenant(partition[0])
        row = (
            await self.session.execute(
                text(_SELECT_QUARANTINE_FOR_UPDATE_SQL),
                {
                    "tenant_id": partition[0],
                    "definition_id": partition[1],
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "source_schema_version": schema_version,
                },
            )
        ).mappings().one_or_none()
        return dict(row) if row is not None else None

    async def advance_attempt_count(self, *, quarantine_id: str, expected: int) -> int:
        result = await self.session.execute(
            text(_ADVANCE_ATTEMPT_COUNT_SQL),
            {"id": quarantine_id, "expected_attempt_count": expected},
        )
        row = result.first()
        if result.rowcount != 1 or row is None:
            raise M05PersistenceBlocked(
                "quarantine_attempt_count_cas_conflict", "stale attempt counter"
            )
        return int(row[0])

    async def insert_attempt(
        self, *, quarantine_id: str, attempt: QuarantineRetryAttempt
    ) -> None:
        self.require_tenant(attempt.checkpoint.partition[0])
        try:
            await self.session.execute(
                text(_INSERT_ATTEMPT_SQL),
                {
                    "quarantine_id": quarantine_id,
                    "attempt_number": attempt.attempt_number,
                    "outcome": attempt.outcome.value,
                    "reason_code": attempt.reason_code,
                    "correlation_id": attempt.correlation_id,
                    "attempt_digest": attempt_digest(attempt),
                    "source_digest": attempt.source_digest,
                },
            )
        except IntegrityError as error:
            raise M05PersistenceBlocked("duplicate_quarantine_attempt", "attempt number reused") from error

    async def select_attempts(self, *, quarantine_id: str) -> list[dict[str, object]]:
        rows = (
            await self.session.execute(
                text(_SELECT_ATTEMPTS_SQL), {"quarantine_id": quarantine_id}
            )
        ).mappings().all()
        return [dict(row) for row in rows]

    async def insert_instance(
        self,
        *,
        binding: InstanceBindingEvidence,
        checkpoint: ImportCheckpoint,
        source_kind: str,
        source_id: str,
        source_schema_version: str,
        source_digest: str,
    ) -> None:
        """Insert one positive, published mapping plus its reciprocal registry link.

        The source identity is supplied by the caller from the immutable
        :class:`InstanceImportRecord`; it is never inferred from the instance identity.  The
        composite key ``uq_m05_instance_source_identity`` is the last line of defence against
        a second positive mapping for the same source row.
        """

        identity = binding.identity
        self.require_tenant(identity.tenant_id)
        registry_id = await self.select_scope_registry_id(
            tenant_id=identity.tenant_id,
            anchors=identity.plugin_instance_scope,
            plugin_instance_id=identity.plugin_instance_id,
        )
        if registry_id is None:
            raise M05PersistenceBlocked(
                "plugin_instance_registry_relation_unproven", "no active reciprocal registry row"
            )
        await self.session.execute(
            text(_INSERT_LINK_SQL),
            {
                "id": _new_id("m05l"),
                "plugin_instance_id": identity.plugin_instance_id,
                "tenant_id": identity.tenant_id,
                "cabinet_id": identity.installation_scope.cabinet_id,
                "project_id": identity.installation_scope.project_id,
            },
        )
        try:
            result = await self.session.execute(
                text(_INSERT_INSTANCE_SQL),
                {
                    "id": identity.plugin_instance_id,
                    "plugin_instance_id": identity.plugin_instance_id,
                    "definition_id": identity.definition_id,
                    "version_id": identity.version_id,
                    "tenant_id": identity.tenant_id,
                    "scope_id": identity.scope_id,
                    "cabinet_id": identity.installation_scope.cabinet_id,
                    "project_id": identity.installation_scope.project_id,
                    "scope_registry_id": registry_id,
                    "artifact_digest": identity.artifact_digest,
                    "subject_id": identity.subject_id,
                    "session_id": identity.session_id,
                    "runtime_generation": binding.source_runtime.runtime_generation,
                    "writer_generation": binding.source_runtime.writer_generation,
                    "source_kind": source_kind,
                    "source_id": source_id,
                    "source_schema_version": source_schema_version,
                    "source_digest": source_digest,
                    "checkpoint": checkpoint.digest,
                },
            )
        except IntegrityError as error:
            raise M05PersistenceBlocked(
                "duplicate_plugin_instance_identity", "exact instance already mapped"
            ) from error
        if result.first() is None:
            raise M05PersistenceBlocked(BLOCKED_CONFLICT, "source identity already mapped")

    async def append_audit(self, record: RuntimeAuditRecord) -> None:
        try:
            await self.session.execute(
                text(_INSERT_AUDIT_SQL),
                {
                    "event": record.event,
                    "outcome": record.outcome,
                    "reason_code": record.reason_code,
                    "correlation_id": record.correlation_id,
                    "runtime_generation": record.runtime_generation,
                    "writer_generation": record.writer_generation,
                },
            )
        except SQLAlchemyError as error:
            raise M05PersistenceBlocked("audit_append_failed", "redacted audit sink unavailable") from error


# ---------------------------------------------------------------------------
# Row mapping helpers
# ---------------------------------------------------------------------------

_AVAILABILITY_BY_LIFECYCLE: Mapping[InstanceLifecycleState, RuntimeAvailability] = {
    InstanceLifecycleState.DRAFT: RuntimeAvailability.STOPPED,
    InstanceLifecycleState.PUBLISHED: RuntimeAvailability.ACTIVE,
    InstanceLifecycleState.DISABLED: RuntimeAvailability.STOPPED,
    InstanceLifecycleState.REVOKED: RuntimeAvailability.STOPPED,
    InstanceLifecycleState.QUARANTINED: RuntimeAvailability.QUARANTINED,
}


def installation_anchors_from_row(row: Mapping[str, object]) -> ScopeAnchors:
    """Rebuild the typed installation scope from the immutable instance columns."""

    cabinet_id = row["cabinet_id"]
    project_id = row["project_id"]
    if project_id is not None:
        kind, scope_id = ScopeKind.PROJECT, project_id
    elif cabinet_id is not None:
        kind, scope_id = ScopeKind.CABINET, cabinet_id
    else:
        kind, scope_id = ScopeKind.TENANT, row["tenant_id"]
    return ScopeAnchors(
        scope_kind=kind,
        scope_id=str(scope_id),
        tenant_id=str(row["tenant_id"]),
        cabinet_id=None if cabinet_id is None else str(cabinet_id),
        project_id=None if project_id is None else str(project_id),
    )


def plugin_instance_anchors_from_row(row: Mapping[str, object]) -> ScopeAnchors:
    instance_id = str(row["plugin_instance_id"])
    return ScopeAnchors(
        scope_kind=ScopeKind.PLUGIN_INSTANCE,
        scope_id=instance_id,
        tenant_id=str(row["tenant_id"]),
        cabinet_id=None if row["cabinet_id"] is None else str(row["cabinet_id"]),
        project_id=None if row["project_id"] is None else str(row["project_id"]),
        plugin_instance_id=instance_id,
    )


def identity_from_row(row: Mapping[str, object]) -> InstanceIdentity:
    return InstanceIdentity(
        plugin_instance_id=str(row["plugin_instance_id"]),
        definition_id=str(row["definition_id"]),
        version_id=str(row["version_id"]),
        app_id=str(row["app_id"]),
        manifest_version=str(row["manifest_version"]),
        tenant_id=str(row["tenant_id"]),
        scope_kind=ScopeKind(str(row["scope_kind"])),
        scope_id=str(row["scope_id"]),
        installation_scope=installation_anchors_from_row(row),
        plugin_instance_scope=plugin_instance_anchors_from_row(row),
        artifact_digest=str(row["artifact_digest"]),
        subject_id=str(row["subject_id"]),
        session_id=str(row["session_id"]),
    )


def source_runtime_from_row(row: Mapping[str, object]) -> InstanceSourceRuntime:
    lifecycle = InstanceLifecycleState(str(row["lifecycle"]))
    return InstanceSourceRuntime(
        lifecycle=lifecycle,
        runtime_generation=int(row["runtime_generation"]),
        writer_generation=int(row["writer_generation"]),
        availability=_AVAILABILITY_BY_LIFECYCLE[lifecycle],
    )


def snapshot_from_row(row: Mapping[str, object]) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        identity=identity_from_row(row),
        lifecycle=InstanceLifecycleState(str(row["lifecycle"])),
        availability=_AVAILABILITY_BY_LIFECYCLE[InstanceLifecycleState(str(row["lifecycle"]))],
        runtime_generation=int(row["runtime_generation"]),
        writer_generation=int(row["writer_generation"]),
    )


def definition_resolution_from_row(row: Mapping[str, object]) -> DefinitionResolution:
    """Rebuild the immutable definition/version/artifact proof for a stored mapping."""

    manifest = row["manifest"]
    requested = row["requested_capabilities"]
    if not isinstance(manifest, Mapping) or not isinstance(requested, (list, tuple)):
        raise M05PersistenceBlocked("stored_binding_unreadable", "definition payload malformed")
    try:
        definition = AppDefinition(
            id=str(row["definition_id"]),
            tenant_id=str(row["tenant_id"]),
            app_id=str(row["app_id"]),
            display_name=str(row["definition_display_name"]),
            description=row["definition_description"],
            scope=AppScope(
                kind=AppScopeKind(str(row["definition_scope_kind"])),
                id=str(row["definition_scope_id"]),
            ),
            status=AppLifecycleStatus(str(row["definition_lifecycle"])),
            current_version_id=row["current_version_id"],
            created_at=row["definition_created_at"],
            updated_at=row["definition_updated_at"],
        )
        version = AppVersion(
            id=str(row["version_id"]),
            app_definition_id=str(row["definition_id"]),
            manifest_version=str(row["manifest_version"]),
            artifact_digest=str(row["artifact_digest"]),
            manifest=dict(manifest),
            requested_capabilities=tuple(RequestedCapability(**item) for item in requested),
            status=AppLifecycleStatus(str(row["version_lifecycle"])),
            created_at=row["version_created_at"],
            published_at=row["version_published_at"],
        )
    except (TypeError, ValueError) as error:
        raise M05PersistenceBlocked("stored_binding_unreadable", "definition row malformed") from error
    return DefinitionResolution(
        DefinitionResolutionOutcome.RESOLVED,
        reference=DefinitionVersionRef(
            tenant_id=str(row["tenant_id"]),
            app_id=str(row["app_id"]),
            app_definition_id=str(row["definition_id"]),
            app_version_id=str(row["version_id"]),
            manifest_version=str(row["manifest_version"]),
            artifact_digest=str(row["artifact_digest"]),
        ),
        definition=definition,
        version=version,
        evidence={"source_kind": "m05.instance", "source_id": str(row["plugin_instance_id"])},
    )


def attempt_digest(attempt: QuarantineRetryAttempt) -> str:
    """Deterministic digest of one immutable retry attempt."""

    return _digest(
        {
            "source_kind": attempt.source_kind,
            "source_id": attempt.source_id,
            "schema_version": attempt.schema_version,
            "source_digest": attempt.source_digest,
            "attempt_number": attempt.attempt_number,
            "outcome": attempt.outcome.value,
            "reason_code": attempt.reason_code,
            "correlation_id": attempt.correlation_id,
            "checkpoint": attempt.checkpoint.digest,
            "binding": None if attempt.binding_evidence is None
            else attempt.binding_evidence.identity.digest,
        }
    )


# ---------------------------------------------------------------------------
# Unit of work
# ---------------------------------------------------------------------------


class SqlAlchemyM05UnitOfWork:
    """Open one tenant-scoped transaction per durable M05 operation."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        tenant_context: TrustedTenantContextPort | None,
        trusted_capability: bool = True,
    ) -> None:
        if session_factory is None:
            raise ValueError("an async session factory is required")
        if tenant_context is None:
            raise ValueError(
                "a trusted tenant-context port is required; missing context never falls back "
                "to a session-level setting or an in-memory cursor"
            )
        self._session_factory = session_factory
        self._tenant_context = tenant_context
        self._trusted_capability = trusted_capability

    def with_trusted_capability(self, trusted: bool) -> "SqlAlchemyM05UnitOfWork":
        return SqlAlchemyM05UnitOfWork(
            session_factory=self._session_factory,
            tenant_context=self._tenant_context,
            trusted_capability=trusted,
        )

    @asynccontextmanager
    async def transaction(
        self, *, context: TrustedRuntimeContext
    ) -> AsyncIterator[SqlAlchemyM05Transaction]:
        if not context.usable:
            raise M05PersistenceBlocked(BLOCKED_MISSING_CONTEXT)
        async with self._session_factory() as session:
            if str(session.get_bind().dialect.name) != "postgresql":
                raise M05PersistenceBlocked(BLOCKED_POSTGRES_ONLY)
            async with session.begin():
                binding = await self._tenant_context.bind(
                    session=session,
                    tenant_id=str(context.resolved_tenant_id),
                    subject_id=str(context.subject_id),
                    session_id=str(context.session_id),
                    correlation_id=context.correlation_id,
                    trusted=self._trusted_capability,
                )
                self._verify_binding(context, binding)
                database_transaction_id = str(
                    (await session.execute(text(_DATABASE_TRANSACTION_SQL))).scalar()
                )
                if database_transaction_id != binding.database_transaction_id:
                    raise M05PersistenceBlocked(
                        BLOCKED_TENANT_CONTEXT,
                        "the tenant context is not bound to this database transaction",
                    )
                yield SqlAlchemyM05Transaction(session, binding, context)

    def _verify_binding(self, context: TrustedRuntimeContext, binding: M05RlsBinding) -> None:
        if binding.tenant_id != context.resolved_tenant_id:
            raise M05PersistenceBlocked(BLOCKED_TENANT_CONTEXT, "tenant mismatch")
        if binding.correlation_id != context.correlation_id:
            raise M05PersistenceBlocked(BLOCKED_TENANT_CONTEXT, "correlation mismatch")
        if not binding.all_verified():
            raise M05PersistenceBlocked(
                BLOCKED_TENANT_CONTEXT, "RLS USING/WITH CHECK or role guarantees missing"
            )
        if binding.trusted_service is not self._trusted_capability:
            raise M05PersistenceBlocked(
                BLOCKED_TENANT_CONTEXT, "trusted capability differs from the operation mode"
            )


async def _load_binding(
    tx: SqlAlchemyM05Transaction,
    *,
    row: Mapping[str, object],
    evidence: M05IdentityEvidencePort,
    context: TrustedRuntimeContext,
) -> InstanceBindingEvidence:
    """Rebuild the immutable original binding of a stored positive mapping.

    The identity, registry entries and definition/version/artifact proof come from immutable
    rows.  Subject/session activeness is *re-proved* by the trusted evidence port for the
    current correlation: a stale or revoked binding is a blocked partition, never a replay.
    """

    identity = identity_from_row(row)
    installation = await tx.select_registry_entry(
        anchors=identity.installation_scope, plugin_instance_id=None
    )
    if installation is None or installation.owner_state is not OwnerState.ACTIVE:
        raise M05PersistenceBlocked(
            "stored_registry_relation_unproven", "installation scope is not active"
        )
    plugin_scope = await tx.select_registry_entry(
        anchors=identity.plugin_instance_scope, plugin_instance_id=identity.plugin_instance_id
    )
    if plugin_scope is None or plugin_scope.owner_state is not OwnerState.ACTIVE:
        raise M05PersistenceBlocked(
            "stored_registry_relation_unproven", "plugin-instance scope is not active"
        )
    link = await tx.select_link_state(
        tenant_id=identity.tenant_id, plugin_instance_id=identity.plugin_instance_id
    )
    if link is None or link["owner_state"] != OwnerState.ACTIVE.value:
        raise M05PersistenceBlocked("stored_registry_relation_unproven", "reciprocal link inactive")
    subject_session = await evidence.prove_subject_session(context=context, identity=identity)
    if subject_session is None:
        raise M05PersistenceBlocked(
            "stored_subject_session_unproven", "subject/session binding is not active"
        )
    return InstanceBindingEvidence(
        identity=identity,
        source_runtime=source_runtime_from_row(row),
        installation_scope=installation,
        plugin_instance_scope=plugin_scope,
        definition_version=definition_resolution_from_row(row),
        subject_session=subject_session,
    )


def _mapping_checkpoint(
    partition: tuple[str, str], source_id: str, snapshot: Mapping[str, object]
) -> ImportCheckpoint:
    return ImportCheckpoint(
        partition,
        str(snapshot["source_snapshot_id"]),
        str(snapshot["source_schema_version"]),
        str(snapshot["source_snapshot_digest"]),
        source_id,
        int(snapshot["page_position"]),
    )


# ---------------------------------------------------------------------------
# Import store
# ---------------------------------------------------------------------------


class SqlAlchemyInstanceImportStore(InstanceImportStore):
    """Atomic mapping + cursor + evidence + audit store for one trusted run.

    The store is bound to the run's trusted context so that a replayed binding can re-prove
    its subject/session for the *current* correlation without mutating the stored identity.
    """

    def __init__(
        self,
        uow: SqlAlchemyM05UnitOfWork,
        evidence: M05IdentityEvidencePort,
        context: TrustedRuntimeContext,
    ) -> None:
        if uow is None or evidence is None:
            raise ValueError("M05 import store requires a unit of work and identity evidence")
        if not context.usable:
            raise ValueError("M05 import store requires a trusted runtime context")
        self._uow = uow
        self._evidence = evidence
        self._context = context

    # -- reads ------------------------------------------------------------

    async def _record_from_instance(
        self, tx: SqlAlchemyM05Transaction, row: Mapping[str, object]
    ) -> InstanceImportRecord:
        partition = (str(row["tenant_id"]), str(row["definition_id"]))
        snapshot = await tx.select_snapshot_position(
            partition=partition,
            source_kind=str(row["source_kind"]),
            source_id=str(row["source_id"]),
            schema_version=str(row["source_schema_version"]),
        )
        if snapshot is None:
            raise M05PersistenceBlocked(
                "source_snapshot_row_missing", "stored mapping has no pinned source row"
            )
        checkpoint = _mapping_checkpoint(partition, str(row["source_id"]), snapshot)
        if str(row["checkpoint"]) != checkpoint.digest:
            raise M05PersistenceBlocked(
                "stored_mapping_checkpoint_mismatch", "stored checkpoint token disagrees"
            )
        try:
            binding = await _load_binding(
                tx, row=row, evidence=self._evidence, context=self._context
            )
        except M05PersistenceBlocked:
            # A stored mapping whose registry/subject proof no longer holds is never
            # refreshed into a replay.  It stays untouched and is reported as unresolved
            # evidence, so the partition is blocked instead of silently re-accepted.
            binding = None
        if binding is None:
            return InstanceImportRecord(
                str(row["source_kind"]),
                str(row["source_id"]),
                str(row["source_schema_version"]),
                str(row["source_digest"]),
                checkpoint,
                ImportOutcome.QUARANTINED,
                "stored_binding_unproven",
                self._context.correlation_id,
                None,
                checkpoint,
            )
        return InstanceImportRecord(
            str(row["source_kind"]),
            str(row["source_id"]),
            str(row["source_schema_version"]),
            str(row["source_digest"]),
            checkpoint,
            ImportOutcome.IMPORTED,
            None,
            self._context.correlation_id,
            binding,
            checkpoint,
        )

    def _record_from_negative(self, row: Mapping[str, object]) -> InstanceImportRecord:
        partition = (str(row["tenant_id"]), str(row["definition_id"]))
        checkpoint = ImportCheckpoint(
            partition,
            str(row["source_snapshot_id"]),
            str(row["source_schema_version_snapshot"]),
            str(row["source_snapshot_digest"]),
            str(row["after_source_id"]),
            int(row["sequence"]),
        )
        outcome = ImportOutcome(str(row["outcome"]))
        if outcome is ImportOutcome.IMPORTED or outcome is ImportOutcome.REPLAY:
            raise M05PersistenceBlocked("negative_evidence_outcome_invalid", "positive outcome")
        return InstanceImportRecord(
            str(row["source_kind"]),
            str(row["source_id"]),
            str(row["source_schema_version"]),
            str(row["source_digest"]),
            checkpoint,
            outcome,
            str(row["reason_code"]),
            str(row["correlation_id"]),
            None,
            checkpoint,
        )

    async def find(
        self, *, partition: tuple[str, str], source_kind: str, source_id: str, schema_version: str
    ) -> InstanceImportRecord | None:
        async with self._uow.transaction(context=self._context) as tx:
            row = await tx.select_instance_by_source(
                partition=partition,
                source_kind=source_kind,
                source_id=source_id,
                schema_version=schema_version,
            )
            if row is not None:
                return await self._record_from_instance(tx, row)
            negative = await tx.select_negative_evidence(
                partition=partition,
                source_kind=source_kind,
                source_id=source_id,
                schema_version=schema_version,
            )
            return None if negative is None else self._record_from_negative(negative)

    async def load_checkpoint(
        self,
        *,
        partition: tuple[str, str],
        source_snapshot_id: str,
        source_schema_version: str,
        source_snapshot_digest: str,
    ) -> ImportCheckpoint | None:
        async with self._uow.transaction(context=self._context) as tx:
            return await tx.select_checkpoint(
                checkpoint=ImportCheckpoint(
                    partition,
                    source_snapshot_id,
                    source_schema_version,
                    source_snapshot_digest,
                    None,
                    0,
                )
            )

    async def find_instance(
        self, *, partition: tuple[str, str], plugin_instance_id: str
    ) -> InstanceImportRecord | None:
        async with self._uow.transaction(context=self._context) as tx:
            row = await tx.select_instance_by_plugin_id(
                partition=partition, plugin_instance_id=plugin_instance_id
            )
            return None if row is None else await self._record_from_instance(tx, row)

    # -- write ------------------------------------------------------------

    async def commit_record_and_checkpoint(
        self,
        *,
        context: TrustedRuntimeContext,
        expected_checkpoint: ImportCheckpoint | None,
        next_checkpoint: ImportCheckpoint,
        record: InstanceImportRecord,
        audit_record: RuntimeAuditRecord,
    ) -> None:
        if context.correlation_id != self._context.correlation_id or (
            context.resolved_tenant_id != self._context.resolved_tenant_id
        ):
            raise M05PersistenceBlocked(BLOCKED_TENANT_CONTEXT, "store bound to another run")
        partition = record.checkpoint.partition
        if record.cursor_checkpoint != next_checkpoint:
            raise M05PersistenceBlocked("page_cursor_mismatch", "record cursor differs from page")
        if next_checkpoint.after_source_id != record.source_id:
            raise M05PersistenceBlocked("page_cursor_mismatch", "cursor must end on the record")
        lineage = (partition, next_checkpoint.source_snapshot_id,
                   next_checkpoint.source_schema_version, next_checkpoint.source_snapshot_digest)
        if (record.checkpoint.partition, record.checkpoint.source_snapshot_id,
                record.checkpoint.source_schema_version,
                record.checkpoint.source_snapshot_digest) != lineage:
            raise M05PersistenceBlocked("page_cursor_mismatch", "mapping lineage differs")
        if expected_checkpoint is not None:
            expected_lineage = (expected_checkpoint.partition, expected_checkpoint.source_snapshot_id,
                                expected_checkpoint.source_schema_version,
                                expected_checkpoint.source_snapshot_digest)
            if expected_lineage != lineage:
                raise M05PersistenceBlocked("page_cursor_mismatch", "expected lineage differs")
            if next_checkpoint.sequence != expected_checkpoint.sequence + 1:
                raise M05PersistenceBlocked("page_cursor_mismatch", "cursor must advance one row")
        elif next_checkpoint.sequence != 1:
            raise M05PersistenceBlocked("page_cursor_mismatch", "first page must start at one")

        async with self._uow.transaction(context=context) as tx:
            tx.require_tenant(partition[0])
            durable = await tx.select_checkpoint(checkpoint=next_checkpoint, for_update=True)
            if expected_checkpoint is None:
                if durable is not None:
                    raise M05PersistenceBlocked(BLOCKED_CURSOR, "cursor already initialised")
            elif durable is None:
                raise M05PersistenceBlocked(BLOCKED_CURSOR, "durable cursor missing")
            elif durable != expected_checkpoint:
                raise M05PersistenceBlocked(BLOCKED_CURSOR, "durable cursor differs from caller")

            stored = await tx.select_instance_by_source(
                partition=partition,
                source_kind=record.source_kind,
                source_id=record.source_id,
                schema_version=record.schema_version,
            )
            if record.outcome is ImportOutcome.IMPORTED:
                if record.binding_evidence is None:
                    raise M05PersistenceBlocked("binding_evidence_missing", "positive outcome")
                if stored is not None:
                    raise M05PersistenceBlocked(BLOCKED_CONFLICT, "source identity already mapped")
                await tx.insert_instance(
                    binding=record.binding_evidence,
                    checkpoint=record.checkpoint,
                    source_kind=record.source_kind,
                    source_id=record.source_id,
                    source_schema_version=record.schema_version,
                    source_digest=record.source_digest,
                )
            elif record.outcome is ImportOutcome.REPLAY:
                if stored is None:
                    raise M05PersistenceBlocked("replay_without_mapping", "no stored mapping")
                if str(stored["source_digest"]) != record.source_digest:
                    raise M05PersistenceBlocked(BLOCKED_CONFLICT, "replay digest changed")
                if str(stored["checkpoint"]) != record.checkpoint.digest:
                    raise M05PersistenceBlocked(
                        "replay_checkpoint_changed", "replay must keep the original checkpoint"
                    )
            else:
                await tx.insert_negative_evidence(
                    record=record,
                    original_digest=None if stored is None else str(stored["source_digest"]),
                    mapping_checkpoint=record.checkpoint,
                    cursor_checkpoint=next_checkpoint,
                )

            if expected_checkpoint is None:
                await tx.insert_checkpoint(next_checkpoint)
            else:
                await tx.advance_checkpoint(
                    expected=expected_checkpoint, next_checkpoint=next_checkpoint
                )
            await tx.append_audit(audit_record)


# ---------------------------------------------------------------------------
# Pinned snapshot reader
# ---------------------------------------------------------------------------


class SqlAlchemyPinnedSnapshotReader(InstanceImportCandidateReader):
    """Read bounded pages from the immutable pinned source snapshot only."""

    def __init__(
        self,
        uow: SqlAlchemyM05UnitOfWork,
        context: TrustedRuntimeContext,
        *,
        max_page_size: int = 1000,
    ) -> None:
        if not 1 <= max_page_size <= 1000:
            raise ValueError("max_page_size must be between 1 and 1000")
        if not context.usable:
            raise ValueError("snapshot reader requires a trusted runtime context")
        self._uow = uow
        self._context = context
        self._max_page_size = max_page_size

    async def read_page(
        self,
        *,
        partition: tuple[str, str],
        source_snapshot_id: str,
        source_schema_version: str,
        source_snapshot_digest: str,
        after_source_id: str | None,
        limit: int,
    ) -> Sequence[InstanceImportCandidate]:
        if not 1 <= limit <= self._max_page_size:
            raise M05PersistenceBlocked("page_size_invalid", "bounded page size required")
        if partition[0] != self._context.resolved_tenant_id:
            raise M05PersistenceBlocked(BLOCKED_TENANT_CONTEXT, "partition tenant mismatch")
        async with self._uow.transaction(context=self._context) as tx:
            tx.require_tenant(partition[0])
            rows = (
                await tx.session.execute(
                    text(_SELECT_SNAPSHOT_PAGE_SQL),
                    {
                        "tenant_id": partition[0],
                        "definition_id": partition[1],
                        "source_snapshot_id": source_snapshot_id,
                        "source_schema_version": source_schema_version,
                        "source_snapshot_digest": source_snapshot_digest,
                        "after_source_id": after_source_id,
                        "limit": limit,
                    },
                )
            ).mappings().all()
        if len(rows) > limit:
            raise M05PersistenceBlocked("page_size_exceeded", "reader returned more than the bound")
        candidates = [self._candidate(dict(row)) for row in rows]
        self._verify_page(candidates, after_source_id=after_source_id)
        return tuple(candidates)

    @staticmethod
    def _candidate(row: Mapping[str, object]) -> InstanceImportCandidate:
        payload = row["payload"]
        if not isinstance(payload, Mapping):
            raise M05PersistenceBlocked("snapshot_payload_unreadable", "payload is not an object")
        if _digest(dict(payload)) != str(row["payload_digest"]):
            raise M05PersistenceBlocked("snapshot_payload_digest_mismatch", "payload was altered")
        identity = None
        runtime = None
        required = ("plugin_instance_id", "version_id", "app_id", "manifest_version",
                    "scope_id", "artifact_digest", "subject_id", "session_id")
        if all(row[name] is not None for name in required) and row["lifecycle"] is not None:
            try:
                identity = InstanceIdentity(
                    plugin_instance_id=str(row["plugin_instance_id"]),
                    definition_id=str(row["app_definition_id"]),
                    version_id=str(row["version_id"]),
                    app_id=str(row["app_id"]),
                    manifest_version=str(row["manifest_version"]),
                    tenant_id=str(row["tenant_id"]),
                    scope_kind=ScopeKind.PLUGIN_INSTANCE,
                    scope_id=str(row["scope_id"]),
                    installation_scope=installation_anchors_from_row(row),
                    plugin_instance_scope=plugin_instance_anchors_from_row(row),
                    artifact_digest=str(row["artifact_digest"]),
                    subject_id=str(row["subject_id"]),
                    session_id=str(row["session_id"]),
                )
                lifecycle = InstanceLifecycleState(str(row["lifecycle"]))
                availability = RuntimeAvailability(str(row["availability"]))
                runtime = InstanceSourceRuntime(
                    lifecycle=lifecycle,
                    runtime_generation=int(row["runtime_generation"]),
                    writer_generation=int(row["writer_generation"]),
                    availability=availability,
                )
            except (TypeError, ValueError):
                identity, runtime = None, None
        return InstanceImportCandidate(
            source_kind=str(row["source_kind"]),
            source_id=str(row["source_id"]),
            schema_version=str(row["source_schema_version"]),
            payload=dict(payload),
            identity=identity,
            source_runtime=runtime,
        )

    @staticmethod
    def _verify_page(
        candidates: Sequence[InstanceImportCandidate], *, after_source_id: str | None
    ) -> None:
        seen: set[str] = set()
        previous: str | None = after_source_id
        for candidate in candidates:
            if candidate.source_id in seen:
                raise M05PersistenceBlocked("page_duplicate_source_id", "duplicate page row")
            seen.add(candidate.source_id)
            if previous is not None and candidate.source_id <= previous:
                raise M05PersistenceBlocked("page_not_monotonic", "page order is not increasing")
            previous = candidate.source_id


# ---------------------------------------------------------------------------
# Quarantine retry store
# ---------------------------------------------------------------------------


class SqlAlchemyInstanceQuarantineStore(InstanceQuarantineRetryPort):
    """Append-only contiguous retry history for one original quarantine."""

    def __init__(
        self,
        uow: SqlAlchemyM05UnitOfWork,
        evidence: M05IdentityEvidencePort,
        context: TrustedRuntimeContext,
    ) -> None:
        if not context.usable:
            raise ValueError("quarantine store requires a trusted runtime context")
        self._uow = uow
        self._evidence = evidence
        self._context = context

    @staticmethod
    def _checkpoint_from_row(row: Mapping[str, object]) -> ImportCheckpoint:
        partition = (str(row["tenant_id"]), str(row["definition_id"]))
        return ImportCheckpoint(
            partition,
            str(row["source_snapshot_id"]),
            str(row["source_schema_version_snapshot"]),
            str(row["source_snapshot_digest"]),
            str(row["after_source_id"]),
            int(row["sequence"]),
        )

    @classmethod
    def _original_record(cls, row: Mapping[str, object]) -> InstanceImportRecord:
        checkpoint = cls._checkpoint_from_row(row)
        return InstanceImportRecord(
            str(row["source_kind"]),
            str(row["source_id"]),
            str(row["source_schema_version"]),
            str(row["source_digest"]),
            checkpoint,
            ImportOutcome(str(row["outcome"])),
            str(row["reason_code"]),
            str(row["correlation_id"]),
            None,
            checkpoint,
        )

    async def find_quarantine(
        self, *, partition: tuple[str, str], source_kind: str, source_id: str, schema_version: str
    ) -> InstanceImportRecord | None:
        async with self._uow.transaction(context=self._context) as tx:
            row = await tx.select_negative_evidence(
                partition=partition,
                source_kind=source_kind,
                source_id=source_id,
                schema_version=schema_version,
            )
        if row is None or str(row["outcome"]) != ImportOutcome.QUARANTINED.value:
            return None
        return self._original_record(row)

    async def load_retry_state(
        self, *, partition: tuple[str, str], source_kind: str, source_id: str, schema_version: str
    ) -> QuarantineRetryState:
        async with self._uow.transaction(context=self._context) as tx:
            original = await tx.select_negative_evidence(
                partition=partition,
                source_kind=source_kind,
                source_id=source_id,
                schema_version=schema_version,
            )
            if original is None or str(original["outcome"]) != ImportOutcome.QUARANTINED.value:
                return QuarantineRetryState()
            rows = await tx.select_attempts(quarantine_id=str(original["id"]))
            checkpoint = self._checkpoint_from_row(original)
            attempts: list[QuarantineRetryAttempt] = []
            for row in rows:
                outcome = ImportOutcome(str(row["outcome"]))
                binding = None
                if outcome is ImportOutcome.IMPORTED:
                    stored = await tx.select_instance_by_source(
                        partition=partition,
                        source_kind=source_kind,
                        source_id=source_id,
                        schema_version=schema_version,
                    )
                    if stored is None:
                        raise M05PersistenceBlocked(
                            "retry_history_unreadable", "accepted attempt has no mapping"
                        )
                    binding = await _load_binding(
                        tx, row=stored, evidence=self._evidence, context=self._context
                    )
                attempts.append(
                    QuarantineRetryAttempt(
                        source_kind=source_kind,
                        source_id=source_id,
                        schema_version=schema_version,
                        source_digest=str(row["source_digest"]),
                        attempt_number=int(row["attempt_number"]),
                        outcome=outcome,
                        reason_code=row["reason_code"],
                        correlation_id=str(row["correlation_id"]),
                        checkpoint=checkpoint,
                        binding_evidence=binding,
                    )
                )
        numbers = [attempt.attempt_number for attempt in attempts]
        if numbers != list(range(1, len(attempts) + 1)):
            raise M05PersistenceBlocked(
                "retry_history_not_contiguous", "attempt numbers are not contiguous"
            )
        return QuarantineRetryState(tuple(attempts))

    async def find_instance(
        self, *, partition: tuple[str, str], plugin_instance_id: str
    ) -> InstanceImportRecord | None:
        async with self._uow.transaction(context=self._context) as tx:
            row = await tx.select_instance_by_plugin_id(
                partition=partition, plugin_instance_id=plugin_instance_id
            )
            if row is None:
                return None
            snapshot = await tx.select_snapshot_position(
                partition=partition,
                source_kind=str(row["source_kind"]),
                source_id=str(row["source_id"]),
                schema_version=str(row["source_schema_version"]),
            )
            if snapshot is None:
                raise M05PersistenceBlocked("source_snapshot_row_missing", "no pinned source row")
            checkpoint = _mapping_checkpoint(partition, str(row["source_id"]), snapshot)
            binding = await _load_binding(
                tx, row=row, evidence=self._evidence, context=self._context
            )
            return InstanceImportRecord(
                str(row["source_kind"]),
                str(row["source_id"]),
                str(row["source_schema_version"]),
                str(row["source_digest"]),
                checkpoint,
                ImportOutcome.IMPORTED,
                None,
                self._context.correlation_id,
                binding,
                checkpoint,
            )

    async def commit_retry_attempt(
        self,
        *,
        context: TrustedRuntimeContext,
        original: InstanceImportRecord,
        expected_attempt_count: int,
        attempt: QuarantineRetryAttempt,
        audit_record: RuntimeAuditRecord,
    ) -> QuarantineRetryAttempt:
        if context.correlation_id != self._context.correlation_id:
            raise M05PersistenceBlocked(BLOCKED_TENANT_CONTEXT, "store bound to another run")
        if expected_attempt_count < 0:
            raise M05PersistenceBlocked("attempt_count_invalid", "negative attempt count")
        if (attempt.attempt_number != expected_attempt_count + 1
                or attempt.source_digest != original.source_digest
                or attempt.checkpoint != original.checkpoint
                or (attempt.source_kind, attempt.source_id, attempt.schema_version)
                != (original.source_kind, original.source_id, original.schema_version)):
            raise M05PersistenceBlocked(
                "retry_not_bound_to_original", "attempt differs from the original quarantine"
            )
        partition = original.checkpoint.partition
        async with self._uow.transaction(context=context) as tx:
            tx.require_tenant(partition[0])
            row = await tx.lock_quarantine(
                partition=partition,
                source_kind=original.source_kind,
                source_id=original.source_id,
                schema_version=original.schema_version,
            )
            if row is None:
                raise M05PersistenceBlocked(
                    "quarantine_record_missing_or_not_retryable", "no locked quarantine row"
                )
            if (str(row["source_digest"]) != original.source_digest
                    or self._checkpoint_from_row(row) != original.checkpoint
                    or int(row["attempt_count"]) != expected_attempt_count):
                raise M05PersistenceBlocked(
                    "stale_quarantine_attempt_count", "quarantine row changed"
                )
            await tx.advance_attempt_count(
                quarantine_id=str(row["id"]), expected=expected_attempt_count
            )
            await tx.insert_attempt(quarantine_id=str(row["id"]), attempt=attempt)
            if attempt.outcome is ImportOutcome.IMPORTED:
                if attempt.binding_evidence is None:
                    raise M05PersistenceBlocked("binding_evidence_missing", "accepted retry")
                stored = await tx.select_instance_by_source(
                    partition=partition,
                    source_kind=attempt.source_kind,
                    source_id=attempt.source_id,
                    schema_version=attempt.schema_version,
                )
                if stored is not None:
                    raise M05PersistenceBlocked(BLOCKED_CONFLICT, "exact instance already mapped")
                await tx.insert_instance(
                    binding=attempt.binding_evidence,
                    checkpoint=attempt.checkpoint,
                    source_kind=attempt.source_kind,
                    source_id=attempt.source_id,
                    source_schema_version=attempt.schema_version,
                    source_digest=attempt.source_digest,
                )
            await tx.append_audit(audit_record)
        return attempt


# ---------------------------------------------------------------------------
# Redacted audit sink
# ---------------------------------------------------------------------------


class SqlAlchemyRedactedAuditSink(RedactedRuntimeAuditPort):
    """Append one redacted audit row in its own transaction.

    The row carries only event/outcome/reason/correlation and numeric generations, so no
    raw payload, token, subject/session value or tenant/scope label can leak into the sink.
    """

    def __init__(self, *, session_factory: async_sessionmaker[AsyncSession]) -> None:
        if session_factory is None:
            raise ValueError("an async session factory is required")
        self._session_factory = session_factory

    async def append(self, record: RuntimeAuditRecord) -> None:
        if not record.correlation_id.strip():
            raise M05PersistenceBlocked("audit_correlation_missing")
        async with self._session_factory() as session:
            async with session.begin():
                try:
                    await session.execute(
                        text(_INSERT_AUDIT_SQL),
                        {
                            "event": record.event,
                            "outcome": record.outcome,
                            "reason_code": record.reason_code,
                            "correlation_id": record.correlation_id,
                            "runtime_generation": record.runtime_generation,
                            "writer_generation": record.writer_generation,
                        },
                    )
                except SQLAlchemyError as error:
                    raise M05PersistenceBlocked(
                        "audit_append_failed", "redacted audit sink unavailable"
                    ) from error


# ---------------------------------------------------------------------------
# Runtime lifecycle CAS
# ---------------------------------------------------------------------------


class SqlAlchemyRuntimeInstanceLifecycle(RuntimeInstanceLifecycle):
    """Zero-row CAS resolution and generation/lifecycle fencing over ``m05_plugin_instances``."""

    def __init__(self, uow: SqlAlchemyM05UnitOfWork) -> None:
        self._uow = uow

    async def resolve_exact(
        self, *, query: InstanceResolveQuery, context: TrustedRuntimeContext
    ) -> RuntimeSnapshot | None:
        identity = query.identity
        if identity.partition[0] != context.resolved_tenant_id:
            return None
        async with self._uow.transaction(context=context) as tx:
            rows = await tx.select_exact_instance(query=query)
            if len(rows) != 1:
                return None
            snapshot = snapshot_from_row(rows[0])
        if (snapshot.identity != identity
                or snapshot.runtime_generation != query.runtime_generation
                or snapshot.writer_generation != query.writer_generation
                or snapshot.lifecycle is not InstanceLifecycleState.PUBLISHED
                or snapshot.availability is not RuntimeAvailability.ACTIVE):
            return None
        return snapshot

    async def compare_and_set_writer(
        self,
        *,
        identity: InstanceIdentity,
        context: TrustedRuntimeContext,
        expected_runtime_generation: int,
        expected_writer_generation: int,
        next_writer_generation: int,
        audit_record: RuntimeAuditRecord,
    ) -> WriterCasResult:
        if next_writer_generation != expected_writer_generation + 1:
            raise M05PersistenceBlocked("non_monotonic_generation_request", "writer must advance one")
        return await self._cas(
            statement=_CAS_WRITER_SQL,
            identity=identity,
            context=context,
            expected_runtime_generation=expected_runtime_generation,
            expected_writer_generation=expected_writer_generation,
            next_writer_generation=next_writer_generation,
            expected_lifecycle=None,
            audit_record=audit_record,
            expected_runtime_after=expected_runtime_generation,
            expected_lifecycle_after=InstanceLifecycleState.PUBLISHED,
        )

    async def compare_and_set_runtime(
        self,
        *,
        identity: InstanceIdentity,
        context: TrustedRuntimeContext,
        expected_runtime_generation: int,
        expected_writer_generation: int,
        next_runtime_generation: int,
        next_writer_generation: int,
        audit_record: RuntimeAuditRecord,
    ) -> WriterCasResult:
        if (next_runtime_generation != expected_runtime_generation + 1
                or next_writer_generation != expected_writer_generation + 1):
            raise M05PersistenceBlocked(
                "non_monotonic_generation_request", "runtime restart must advance both epochs"
            )
        return await self._cas(
            statement=_CAS_RUNTIME_SQL,
            identity=identity,
            context=context,
            expected_runtime_generation=expected_runtime_generation,
            expected_writer_generation=expected_writer_generation,
            next_writer_generation=next_writer_generation,
            expected_lifecycle=None,
            audit_record=audit_record,
            expected_runtime_after=next_runtime_generation,
            expected_lifecycle_after=InstanceLifecycleState.PUBLISHED,
        )

    async def compare_and_set_lifecycle(
        self,
        *,
        identity: InstanceIdentity,
        context: TrustedRuntimeContext,
        expected_lifecycle: InstanceLifecycleState,
        next_lifecycle: InstanceLifecycleState,
        expected_runtime_generation: int,
        expected_writer_generation: int,
        next_writer_generation: int,
        audit_record: RuntimeAuditRecord,
    ) -> WriterCasResult:
        if next_writer_generation != expected_writer_generation + 1:
            raise M05PersistenceBlocked("non_monotonic_generation_request", "writer must advance one")
        if expected_lifecycle.value in _TERMINAL_LIFECYCLES:
            return WriterCasResult(False, None, "terminal_lifecycle")
        return await self._cas(
            statement=_CAS_LIFECYCLE_SQL,
            identity=identity,
            context=context,
            expected_runtime_generation=expected_runtime_generation,
            expected_writer_generation=expected_writer_generation,
            next_writer_generation=next_writer_generation,
            expected_lifecycle=expected_lifecycle,
            next_lifecycle=next_lifecycle,
            audit_record=audit_record,
            expected_runtime_after=expected_runtime_generation,
            expected_lifecycle_after=next_lifecycle,
        )

    async def _cas(
        self,
        *,
        statement: str,
        identity: InstanceIdentity,
        context: TrustedRuntimeContext,
        expected_runtime_generation: int,
        expected_writer_generation: int,
        next_writer_generation: int,
        expected_lifecycle: InstanceLifecycleState | None,
        audit_record: RuntimeAuditRecord,
        expected_runtime_after: int,
        expected_lifecycle_after: InstanceLifecycleState,
        next_lifecycle: InstanceLifecycleState | None = None,
    ) -> WriterCasResult:
        if (identity.tenant_id != context.resolved_tenant_id
                or identity.subject_id != context.subject_id
                or identity.session_id != context.session_id):
            return WriterCasResult(False, None, "authenticated_binding_mismatch")
        params = {
            "plugin_instance_id": identity.plugin_instance_id,
            "definition_id": identity.definition_id,
            "version_id": identity.version_id,
            "tenant_id": identity.tenant_id,
            "scope_kind": identity.scope_kind.value,
            "scope_id": identity.scope_id,
            "artifact_digest": identity.artifact_digest,
            "subject_id": identity.subject_id,
            "session_id": identity.session_id,
            "cabinet_id": identity.installation_scope.cabinet_id,
            "project_id": identity.installation_scope.project_id,
            "expected_runtime_generation": expected_runtime_generation,
            "expected_writer_generation": expected_writer_generation,
        }
        if expected_lifecycle is not None:
            params["expected_lifecycle"] = expected_lifecycle.value
        if next_lifecycle is not None:
            params["next_lifecycle"] = next_lifecycle.value
        async with self._uow.transaction(context=context) as tx:
            result = await tx.session.execute(text(statement), params)
            if result.rowcount != 1 or result.first() is None:
                await tx.append_audit(
                    replace(
                        audit_record,
                        outcome="stale-generation",
                        reason_code=BLOCKED_STALE,
                    )
                )
                return WriterCasResult(False, None, BLOCKED_STALE)
            row = await tx.select_instance_by_plugin_id(
                partition=identity.partition, plugin_instance_id=identity.plugin_instance_id
            )
            if row is None:
                raise M05PersistenceBlocked("cas_readback_missing", "mutated row disappeared")
            snapshot = snapshot_from_row(row)
            if (snapshot.identity != identity
                    or snapshot.runtime_generation != expected_runtime_after
                    or snapshot.writer_generation != next_writer_generation
                    or snapshot.lifecycle is not expected_lifecycle_after):
                # The transaction must roll back a malformed post-write row.
                raise M05PersistenceBlocked(
                    "cas_result_mismatch", "returned row differs from the request"
                )
            await tx.append_audit(audit_record)
            return WriterCasResult(True, snapshot, "cas_updated_one_row")


# ---------------------------------------------------------------------------
# Server-side identity evidence
# ---------------------------------------------------------------------------


class RuntimeSessionOwnershipPort(Protocol):
    """Exact server-side proof that an authenticated session belongs to the subject.

    The current baseline has no authentication-session relation (only ``agent_sessions``,
    which carries a project but no employee).  The runtime session owner must therefore
    supply this port; it must compare tenant, subject and session against a server-side
    record and return ``True`` only for an active exact match.  Returning ``True`` for a
    caller-supplied tuple is not proof.
    """

    async def prove(self, *, tenant_id: str, subject_id: str, session_id: str) -> bool: ...


class SqlAlchemyM05IdentityEvidence(M05IdentityEvidencePort):
    """Durable M04/M04E/subject evidence used by import, retry and exact resolution."""

    def __init__(
        self,
        uow: SqlAlchemyM05UnitOfWork,
        *,
        sessions: RuntimeSessionOwnershipPort | None = None,
    ) -> None:
        self._uow = uow
        self._sessions = sessions

    async def resolve_installation_scope(
        self, *, context: TrustedRuntimeContext, anchors: ScopeAnchors
    ) -> ScopeResolutionResult:
        if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "installation_scope_kind_invalid", context.correlation_id
            )
        if anchors.tenant_id != context.resolved_tenant_id:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "cross_tenant_assertion", context.correlation_id
            )
        async with self._uow.transaction(context=context) as tx:
            entry = await tx.select_registry_entry(anchors=anchors, plugin_instance_id=None)
        if entry is None:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "active_scope_missing", context.correlation_id
            )
        if entry.owner_state is not OwnerState.ACTIVE or entry.anchors != anchors:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "active_scope_anchor_mismatch", context.correlation_id
            )
        return ScopeResolutionResult(
            ScopeResolution.ACTIVE, entry, "active", context.correlation_id
        )

    async def resolve_plugin_instance_scope(
        self, *, context: TrustedRuntimeContext, identity: InstanceIdentity
    ) -> ScopeResolutionResult:
        if identity.tenant_id != context.resolved_tenant_id:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "cross_tenant_assertion", context.correlation_id
            )
        async with self._uow.transaction(context=context) as tx:
            entry = await tx.select_registry_entry(
                anchors=identity.plugin_instance_scope,
                plugin_instance_id=identity.plugin_instance_id,
            )
            link = await tx.select_link_state(
                tenant_id=identity.tenant_id,
                plugin_instance_id=identity.plugin_instance_id,
            ) if entry is not None else None
        if entry is None:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "plugin_instance_scope_missing", context.correlation_id
            )
        if (entry.owner_state is not OwnerState.ACTIVE
                or entry.anchors != identity.plugin_instance_scope
                or entry.anchors.scope_kind is not ScopeKind.PLUGIN_INSTANCE
                or entry.anchors.scope_id != identity.plugin_instance_id
                or entry.anchors.plugin_instance_id != identity.plugin_instance_id):
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "plugin_instance_scope_mismatch", context.correlation_id
            )
        if link is None or link["owner_state"] != OwnerState.ACTIVE.value:
            return ScopeResolutionResult(
                ScopeResolution.DENY, None, "registry_link_not_active", context.correlation_id
            )
        return ScopeResolutionResult(
            ScopeResolution.ACTIVE, entry, "active", context.correlation_id
        )

    async def resolve_definition_version(
        self,
        *,
        context: TrustedRuntimeContext,
        tenant_id: str,
        app_id: str,
        manifest_version: str,
        expected_definition_id: str,
        expected_version_id: str,
        artifact_digest: str,
    ) -> DefinitionResolution:
        if tenant_id != context.resolved_tenant_id:
            return DefinitionResolution(
                DefinitionResolutionOutcome.MISSING, reason="cross_tenant_definition"
            )
        async with self._uow.transaction(context=context) as tx:
            definition = (
                await tx.session.execute(
                    text(_SELECT_DEFINITION_SQL),
                    {
                        "definition_id": expected_definition_id,
                        "tenant_id": tenant_id,
                        "app_id": app_id,
                    },
                )
            ).mappings().one_or_none()
            if definition is None:
                return DefinitionResolution(
                    DefinitionResolutionOutcome.MISSING, reason="missing_definition"
                )
            version = (
                await tx.session.execute(
                    text(_SELECT_VERSION_SQL),
                    {
                        "version_id": expected_version_id,
                        "definition_id": expected_definition_id,
                        "tenant_id": tenant_id,
                    },
                )
            ).mappings().one_or_none()
        if version is None:
            return DefinitionResolution(
                DefinitionResolutionOutcome.MISSING, reason="missing_version"
            )
        if (version["artifact_digest"] != artifact_digest
                or version["manifest_version"] != manifest_version
                or version["app_id"] != app_id):
            return DefinitionResolution(
                DefinitionResolutionOutcome.CONFLICT, reason="artifact_digest_conflict"
            )
        row = {
            "tenant_id": tenant_id,
            "app_id": app_id,
            "definition_id": expected_definition_id,
            "version_id": expected_version_id,
            "manifest_version": version["manifest_version"],
            "artifact_digest": version["artifact_digest"],
            "manifest": version["manifest"],
            "requested_capabilities": version["requested_capabilities"],
            "version_lifecycle": version["lifecycle"],
            "version_created_at": version["created_at"],
            "version_published_at": version["published_at"],
            "definition_display_name": definition["display_name"],
            "definition_description": definition["description"],
            "definition_scope_kind": definition["scope_kind"],
            "definition_scope_id": definition["scope_id"],
            "definition_lifecycle": definition["lifecycle"],
            "current_version_id": definition["current_version_id"],
            "definition_created_at": definition["created_at"],
            "definition_updated_at": definition["updated_at"],
            "plugin_instance_id": "",
        }
        try:
            resolution = definition_resolution_from_row(row)
        except M05PersistenceBlocked:
            return DefinitionResolution(
                DefinitionResolutionOutcome.QUARANTINED, reason="invalid_version_history"
            )
        return replace(resolution, evidence={"correlation_id": context.correlation_id})

    async def prove_subject_session(
        self, *, context: TrustedRuntimeContext, identity: InstanceIdentity
    ) -> SubjectSessionBindingEvidence | None:
        if not context.usable:
            return None
        if (identity.tenant_id != context.resolved_tenant_id
                or identity.subject_id != context.subject_id
                or identity.session_id != context.session_id):
            return None
        if self._sessions is None:
            return None
        async with self._uow.transaction(context=context) as tx:
            matches = (
                await tx.session.execute(
                    text(_SELECT_ACTIVE_SUBJECT_SQL),
                    {"tenant_id": identity.tenant_id, "subject_id": identity.subject_id},
                )
            ).scalar()
        if int(matches or 0) != 1:
            return None
        session_active = await self._sessions.prove(
            tenant_id=identity.tenant_id,
            subject_id=identity.subject_id,
            session_id=identity.session_id,
        )
        if not session_active:
            return None
        return SubjectSessionBindingEvidence(
            tenant_id=identity.tenant_id,
            subject_id=identity.subject_id,
            session_id=identity.session_id,
            session_tenant_id=identity.tenant_id,
            session_subject_id=identity.subject_id,
            subject_active=True,
            session_active=True,
            correlation_id=context.correlation_id,
            evidence_digest=_digest(
                {
                    "tenant_id": identity.tenant_id,
                    "subject_id": identity.subject_id,
                    "session_id": identity.session_id,
                    "correlation_id": context.correlation_id,
                }
            ),
        )


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------


def build_sql_resolve_plugin_instance_port(
    *,
    uow: SqlAlchemyM05UnitOfWork,
    identity_evidence: M05IdentityEvidencePort,
    audit: RedactedRuntimeAuditPort,
    upstream=None,
):
    """Compose the durable ``ResolvePluginInstancePort``.

    The exact SQL reader is :class:`SqlAlchemyRuntimeInstanceLifecycle`; the fail-closed gate
    (context, upstream, M04/M04E identity proofs, redacted audit) is
    :class:`FailClosedResolvePluginInstance`, which wraps it.  No other resolution path exists.
    """

    if uow is None or identity_evidence is None or audit is None:
        raise ValueError("durable resolution requires a unit of work, identity evidence and audit")
    return FailClosedResolvePluginInstance(
        delegate=SqlAlchemyRuntimeInstanceLifecycle(uow),
        audit=audit,
        upstream=upstream,
        identity_evidence=identity_evidence,
    )


__all__ = [
    "ATTEMPT_TABLE",
    "AUDIT_TABLE",
    "BLOCKED_CONFLICT",
    "BLOCKED_CURSOR",
    "BLOCKED_SCHEMA",
    "BLOCKED_STALE",
    "CHECKPOINT_TABLE",
    "DECLARED_RELATIONS",
    "EXISTING_RELATIONS",
    "INSTANCE_TABLE",
    "M05PersistenceBlocked",
    "M05RlsBinding",
    "REQUIRED_COLUMNS",
    "RuntimeSessionOwnershipPort",
    "SNAPSHOT_TABLE",
    "SqlAlchemyInstanceImportStore",
    "SqlAlchemyInstanceQuarantineStore",
    "SqlAlchemyM05IdentityEvidence",
    "SqlAlchemyM05Transaction",
    "SqlAlchemyM05UnitOfWork",
    "SqlAlchemyPinnedSnapshotReader",
    "SqlAlchemyRedactedAuditSink",
    "SqlAlchemyRuntimeInstanceLifecycle",
    "TrustedTenantContextPort",
    "attempt_digest",
    "build_sql_resolve_plugin_instance_port",
    "definition_resolution_from_row",
    "identity_from_row",
    "installation_anchors_from_row",
    "plugin_instance_anchors_from_row",
    "snapshot_from_row",
    "source_runtime_from_row",
    "verify_evidence_relations",
]
