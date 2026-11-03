"""Durable, fail-closed M04 scope persistence over the seven owner-registry tables.

Scope of this module
--------------------
It is the concrete implementation of :class:`ResolveScopePort`,
:class:`ScopeCheckpointPort` and :class:`TrustedScopeTransactionPort` against the
tables created by revision ``2026110301`` (``scope_owner_tenant_anchors``,
``scope_owner_cabinet_anchors``, ``scope_owner_project_anchors``,
``scope_registry``, ``scope_owner_mappings``, ``scope_owner_quarantine``,
``scope_owner_audit``).  No DDL, ORM model or Alembic revision is added here:
every column written below already exists in that revision.

Layer note: MIG-M04-BACKFILL-02 owns ``application/migration/m04_scope/**`` only,
so the SQLAlchemy adapter lives here rather than under
``infrastructure/persistence/migration_scope/``.  This follows the existing
precedent of ``application/migration/runtime_materialization/sqlalchemy_repository.py``.
Relocating it later must not change behaviour.

Transaction and tenant context
------------------------------
Every operation owns exactly one PostgreSQL transaction
(:meth:`SqlAlchemyScopeBackfillAdapter.page_transaction`); a whole bounded page of
source rows commits or rolls back atomically.  The transaction-local tenant
context is **injected**, never written here: :class:`TenantScopeContextPort` is
implemented by ``infrastructure/persistence/rls/**`` (revision ``2026110307``),
which a parallel agent owns and which this module deliberately does not import.
Construction without the port is refused — there is no session-level ``SET``, no
in-memory checkpoint and no read-through fallback.

Durable cursor (checkpoint)
---------------------------
Revision ``2026110301`` has no checkpoint table, so the cursor lives in a reserved
control row of ``scope_owner_mappings`` (a row whose ``source_kind`` is the
reserved ``m04.scope.stream`` namespace, which no source table ever uses):

* ``source_id``  — ``c1:<56 hex>``, sha256 of the canonical stream key (tenant,
  stream kind, snapshot id, source schema, canonical schema, writer generation);
* ``schema_version`` — ``checkpoint.v1``;
* ``source_payload_digest`` — the stored ``source_snapshot_digest``, so a changed
  snapshot is detected as a lineage change instead of silently resetting;
* ``canonical_payload_digest`` — the exact ``after_source_id`` cursor;
* ``checkpoint`` — ``c1:<sequence>:<cursor fingerprint>``.

The row is read ``FOR UPDATE`` and advanced by compare-and-swap on that token, so
two runners of the same lineage can never both advance it.  A dedicated
``scope_backfill_checkpoints`` table is the correct long-term carrier; that DDL is
outside this task zone and is recorded as a blocker.

Source mappings are append-only: the first mapping for
``(source_kind, source_id, schema_version)`` is never updated.  A changed digest
appends redacted quarantine + audit evidence and advances the cursor while the
original mapping and registry identity stay untouched.

Redaction
---------
Nothing here writes a source payload, secret, credential or free-form text.  Only
identities, reason codes, sha256 digests, checkpoint tokens, correlation ids and
the actor id reach audit/quarantine rows.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import AsyncContextManager, AsyncIterator, Mapping, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    BackfillOutcome,
    ExistingScopeMapping,
    MappingReplayDecision,
    MigrationEvidenceContext,
    ScopeActorMembershipEvidence,
    ScopeMigrationCoordinator,
    ScopeMigrationResult,
    ScopeParentEvidence,
    ScopeResolutionEvidence,
    ScopeRlsEvidence,
    ScopeSourceEvidence,
    ScopeSourceQuarantineEvidence,
    ScopeStreamIdentity,
    assess_mapping_replay,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    QuarantineRecord,
    ScopeAnchors,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Reserved ``scope_owner_mappings.source_kind`` for durable stream control rows.
CONTROL_SOURCE_KIND = "m04.scope.stream"
CONTROL_SCHEMA_VERSION = "checkpoint.v1"
#: Prefix of every checkpoint token; also the prefix of a control-row ``source_id``.
CHECKPOINT_TOKEN_PREFIX = "c1"
#: Prefix of the packed canonical evidence stored in ``canonical_payload_digest``.
MAPPING_CANONICAL_PREFIX = "v1"
#: Marker for "no canonical identity exists" inside the packed evidence.
MAPPING_CANONICAL_NONE = "-"

OWNER_TENANT_TABLE = "scope_owner_tenant_anchors"
OWNER_CABINET_TABLE = "scope_owner_cabinet_anchors"
OWNER_PROJECT_TABLE = "scope_owner_project_anchors"
REGISTRY_TABLE = "scope_registry"
MAPPING_TABLE = "scope_owner_mappings"
QUARANTINE_TABLE = "scope_owner_quarantine"
AUDIT_TABLE = "scope_owner_audit"

TENANT_TYPE = "company"
ACTIVE_OWNER_STATE = "active"
MAPPING_STATE_MAPPED = "mapped"
MAPPING_STATE_QUARANTINED = "quarantined"
MAPPING_STATE_ARCHIVED = "archived"

#: ``projects.status`` values accepted by the typed project anchor and registry shape.
PROJECT_STATUS_VALUES = ("draft", "active", "paused", "error", "completed", "deleted")
#: Project statuses that may never back an active scope row.
_NON_ACTIVE_PROJECT_STATUSES = frozenset({"completed", "deleted"})

#: Detailed parent-chain failures collapsed onto the coordinator's closed reason set.
_PARENT_REASON_CLASSES: Mapping[str, str] = {
    "tenant_parent_missing": "parent_cardinality_or_lifecycle_invalid",
    "tenant_parent_ambiguous": "parent_cardinality_or_lifecycle_invalid",
    "tenant_parent_deleted": "parent_cardinality_or_lifecycle_invalid",
    "tenant_parent_archived": "parent_cardinality_or_lifecycle_invalid",
    "cabinet_parent_missing": "parent_cardinality_or_lifecycle_invalid",
    "cabinet_parent_ambiguous": "parent_cardinality_or_lifecycle_invalid",
    "cabinet_parent_deleted": "parent_cardinality_or_lifecycle_invalid",
    "cabinet_parent_archived": "parent_cardinality_or_lifecycle_invalid",
    "cabinet_membership_missing": "parent_cardinality_or_lifecycle_invalid",
    "cabinet_membership_ambiguous": "parent_cardinality_or_lifecycle_invalid",
    "project_parent_missing": "parent_cardinality_or_lifecycle_invalid",
    "project_parent_ambiguous": "parent_cardinality_or_lifecycle_invalid",
    "project_parent_deleted": "parent_cardinality_or_lifecycle_invalid",
    "project_parent_archived": "parent_cardinality_or_lifecycle_invalid",
    "cross_tenant_parent": "cross_tenant_assertion",
    "cross_tenant_cabinet_membership": "cross_tenant_assertion",
    "cross_tenant_project_parent": "cross_tenant_assertion",
    "cabinet_parent_mismatch": "parent_compatibility_unproven",
    "cabinet_membership_kind_unproven": "parent_compatibility_unproven",
    "cabinet_membership_mismatch": "parent_compatibility_unproven",
    "project_parent_mismatch": "parent_compatibility_unproven",
    "project_cabinet_mismatch": "parent_compatibility_unproven",
    "project_status_unrepresentable": "parent_compatibility_unproven",
    "active_scope_missing": "duplicate_or_ambiguous_scope",
    "active_scope_ambiguous": "duplicate_or_ambiguous_scope",
    "active_scope_anchor_mismatch": "duplicate_or_ambiguous_scope",
    "plugin_instance_scope_denied": "plugin_instance_deferred_m05",
}


class TenantScopeMode(StrEnum):
    """Which RLS capability a transaction needs.

    ``TENANT_SCOPED`` is the ordinary request path (tenant policy only).
    ``MIGRATION_TRUSTED`` is the migration writer path: it additionally needs the
    trusted service capability, because unresolved mapping / quarantine / audit
    evidence has no ``scope_registry`` parent and is therefore invisible to the
    tenant policy by design (see ``persistence/rls/policies.py``).
    """

    TENANT_SCOPED = "tenant_scoped"
    MIGRATION_TRUSTED = "migration_trusted"


class ScopeBackfillPersistenceBlocked(RuntimeError):
    """A durable M04 invariant is absent, unproven or contradicted by database evidence."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(reason_code if not detail else f"{reason_code}: {detail}")


# ---------------------------------------------------------------------------
# Token and evidence codecs
# ---------------------------------------------------------------------------


def parent_reason_class(detailed_reason: str) -> str:
    """Collapse a detailed parent failure onto the coordinator's closed reason set."""

    return _PARENT_REASON_CLASSES.get(detailed_reason, "parent_cardinality_or_lifecycle_invalid")


def _sha256_hex(material: str) -> str:
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def cursor_fingerprint(after_source_id: str | None) -> str:
    """Stable 16-hex fingerprint of a cursor value (``None`` has its own fingerprint)."""

    return _sha256_hex("cursor:" + (after_source_id or ""))[:16]


def encode_checkpoint_token(sequence: int, after_source_id: str | None) -> str:
    """Encode a checkpoint position as ``c1:<sequence>:<cursor fingerprint>``."""

    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise ValueError("checkpoint sequence must be a non-negative integer")
    if after_source_id is not None:
        if (
            not isinstance(after_source_id, str)
            or not after_source_id.strip()
            or len(after_source_id) > 128
        ):
            raise ValueError("checkpoint cursor exceeds the durable token contract")
        if sequence == 0:
            raise ValueError("a non-empty cursor requires a positive sequence")
    elif sequence != 0:
        raise ValueError("a positive sequence requires a cursor")
    return f"{CHECKPOINT_TOKEN_PREFIX}:{sequence}:{cursor_fingerprint(after_source_id)}"


def decode_checkpoint_token(token: object) -> tuple[int, str]:
    """Return ``(sequence, cursor fingerprint)`` or raise for an unreadable token."""

    if not isinstance(token, str):
        raise ScopeBackfillPersistenceBlocked("checkpoint_row_unreadable", "token is not text")
    parts = token.split(":")
    if len(parts) != 3 or parts[0] != CHECKPOINT_TOKEN_PREFIX:
        raise ScopeBackfillPersistenceBlocked("checkpoint_row_unreadable", "token shape")
    if not parts[1].isdigit() or len(parts[1]) > 12:
        raise ScopeBackfillPersistenceBlocked("checkpoint_row_unreadable", "token sequence")
    if len(parts[2]) != 16:
        raise ScopeBackfillPersistenceBlocked("checkpoint_row_unreadable", "token fingerprint")
    return int(parts[1]), parts[2]


def stream_storage_id(stream: ScopeStreamIdentity) -> str:
    """Deterministic control-row key for one exact checkpoint lineage."""

    material = json.dumps(
        {
            "tenant_id": stream.tenant_id,
            "stream_kind": stream.stream_kind,
            "source_snapshot_id": stream.source_snapshot_id,
            "source_schema_version": stream.source_schema_version,
            "canonical_schema_version": stream.canonical_schema_version,
            "writer_generation": stream.writer_generation,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return f"{CHECKPOINT_TOKEN_PREFIX}:{_sha256_hex(material)[:56]}"


def encode_mapping_canonical_evidence(
    canonical_schema_version: str, canonical_digest: str | None
) -> str:
    """``v1|<canonical schema>|<canonical digest|->`` packed into one 128-char column.

    ``scope_owner_mappings`` has no ``canonical_schema_version`` column (the
    recorded DDL gap ``canonical_schema_version_not_persisted_on_source_mapping``),
    so the canonical lineage of the *first* write is preserved inside
    ``canonical_payload_digest`` instead of being guessed on a later replay.
    ``-`` means "this mapping never held a canonical active identity".
    """

    if not isinstance(canonical_schema_version, str) or not canonical_schema_version.strip():
        raise ValueError("canonical schema version is required")
    if len(canonical_schema_version) > 32:
        raise ValueError("canonical schema version exceeds mapping evidence width")
    digest = MAPPING_CANONICAL_NONE
    if canonical_digest is not None:
        if not canonical_digest.startswith("sha256:") or len(canonical_digest) != 71:
            raise ValueError("canonical digest is not a sha256 digest")
        digest = canonical_digest
    packed = f"{MAPPING_CANONICAL_PREFIX}|{canonical_schema_version}|{digest}"
    if len(packed) > 128:
        raise ValueError("canonical evidence exceeds the mapping column width")
    return packed


def decode_mapping_canonical_evidence(value: object) -> tuple[str, str | None]:
    """Inverse of :func:`encode_mapping_canonical_evidence`."""

    if not isinstance(value, str):
        raise ScopeBackfillPersistenceBlocked(
            "mapping_evidence_unreadable", "canonical evidence is missing"
        )
    parts = value.split("|")
    if len(parts) != 3 or parts[0] != MAPPING_CANONICAL_PREFIX or not parts[1].strip():
        raise ScopeBackfillPersistenceBlocked(
            "mapping_evidence_unreadable", "canonical evidence shape"
        )
    digest = None if parts[2] == MAPPING_CANONICAL_NONE else parts[2]
    if digest is not None and (not digest.startswith("sha256:") or len(digest) != 71):
        raise ScopeBackfillPersistenceBlocked(
            "mapping_evidence_unreadable", "canonical evidence digest"
        )
    return parts[1], digest


def new_owner_row_id() -> str:
    """Row id matching ``owner_registry._id`` (``scope_`` + 24 hex, 40-char column)."""

    return f"scope_{uuid.uuid4().hex[:24]}"


# ---------------------------------------------------------------------------
# Injected tenant-context port
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RlsBindingEvidence:
    """Same-transaction facts a trusted tenant-context binder must attest.

    Every field is a *verified observation* made inside the open transaction: the
    values were applied with ``set_config(..., true)``, read back, and the
    database role plus policy state were inspected.  Only the binder produces
    this structure; a request cannot supply it.
    """

    application_transaction_id: str
    database_transaction_id: str
    tenant_id: str
    cabinet_id: str | None
    project_id: str | None
    database_role: str
    security_review_id: str
    policy_names: tuple[str, ...]
    trusted_service: bool
    row_security_enabled: bool
    row_security_forced: bool
    tenant_policy_enforced: bool
    using_policy_enforced: bool
    with_check_policy_enforced: bool
    missing_context_denies: bool
    role_not_bypass_rls: bool
    typed_parent_policy_enforced: bool

    def __post_init__(self) -> None:
        if not isinstance(self.application_transaction_id, str) or not self.application_transaction_id.strip():
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "application transaction id is required"
            )
        if not isinstance(self.database_transaction_id, str) or not self.database_transaction_id.strip():
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "database transaction id is required"
            )
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip() or len(self.tenant_id) > 40:
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "tenant id is required"
            )
        if self.cabinet_id is not None and (not self.cabinet_id.strip() or len(self.cabinet_id) > 40):
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "cabinet id is malformed"
            )
        if self.project_id is not None and (not self.project_id.strip() or len(self.project_id) > 40):
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "project id is malformed"
            )
        if self.project_id is not None and self.cabinet_id is None:
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "a project scope requires a cabinet scope"
            )
        if not isinstance(self.database_role, str) or not self.database_role.strip():
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "database role is required"
            )
        if not isinstance(self.security_review_id, str) or not self.security_review_id.strip():
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "the RLS review id is required"
            )
        if not self.policy_names or any(
            not isinstance(name, str) or not name.strip() for name in self.policy_names
        ):
            raise ScopeBackfillPersistenceBlocked(
                "trusted_tenant_context_unproven", "reviewed policy names are required"
            )

    def covers(self, anchors: ScopeAnchors) -> bool:
        """True when this binding pins exactly the requested scope (never wider)."""

        return (
            self.tenant_id == anchors.tenant_id
            and self.cabinet_id == anchors.cabinet_id
            and self.project_id == anchors.project_id
        )

    def all_verified(self) -> bool:
        return bool(
            self.row_security_enabled
            and self.row_security_forced
            and self.tenant_policy_enforced
            and self.using_policy_enforced
            and self.with_check_policy_enforced
            and self.missing_context_denies
            and self.role_not_bypass_rls
            and self.typed_parent_policy_enforced
        )


class TenantScopeContextPort(Protocol):
    """Trusted, transaction-local tenant context; implemented outside this zone.

    ``bind`` runs inside an already-open transaction, applies the GUCs with
    ``set_config(..., true)`` (never a session-level ``SET``), reads them back and
    returns the verified facts.  ``narrow`` may only *narrow* the cabinet/project
    part of an existing binding — it must restore the previous values on exit and
    must never change the tenant.
    """

    async def bind(
        self,
        *,
        session: AsyncSession,
        context: MigrationEvidenceContext,
        mode: TenantScopeMode,
    ) -> RlsBindingEvidence: ...

    def narrow(
        self,
        *,
        session: AsyncSession,
        binding: RlsBindingEvidence,
        cabinet_id: str | None,
        project_id: str | None,
    ) -> AsyncContextManager[RlsBindingEvidence]: ...


# ---------------------------------------------------------------------------
# SQL statements (explicit; the acceptance artifacts assert them literally)
# ---------------------------------------------------------------------------

_SELECT_CHECKPOINT_SQL = (
    "SELECT id, source_payload_digest, canonical_payload_digest, checkpoint "
    f"FROM {MAPPING_TABLE} "
    "WHERE source_kind = :control_kind AND source_id = :stream_id "
    "AND schema_version = :control_schema"
)
_SELECT_CHECKPOINT_FOR_UPDATE_SQL = _SELECT_CHECKPOINT_SQL + " FOR UPDATE"

_INSERT_CHECKPOINT_SQL = (
    f"INSERT INTO {MAPPING_TABLE} ("
    "id, source_kind, source_id, schema_version, source_payload_digest, "
    "canonical_payload_digest, scope_registry_id, mapping_state, reason_code, checkpoint"
    ") VALUES ("
    ":id, :control_kind, :source_id, :control_schema, :source_payload_digest, "
    ":canonical_payload_digest, NULL, :mapping_state, :reason_code, :checkpoint"
    ")"
)

_UPDATE_CHECKPOINT_SQL = (
    f"UPDATE {MAPPING_TABLE} "
    "SET source_payload_digest = :source_payload_digest, "
    "canonical_payload_digest = :canonical_payload_digest, "
    "checkpoint = :checkpoint "
    "WHERE id = :id AND checkpoint = :expected_checkpoint"
)

_SELECT_MAPPING_SQL = (
    "SELECT m.id AS mapping_id, m.source_kind, m.source_id, m.schema_version, "
    "m.source_payload_digest, m.canonical_payload_digest, m.scope_registry_id, "
    "m.mapping_state, m.reason_code, "
    "r.scope_kind AS registry_scope_kind, r.scope_id AS registry_scope_id, "
    "r.tenant_id AS registry_tenant_id, r.cabinet_id AS registry_cabinet_id, "
    "r.project_id AS registry_project_id, r.plugin_instance_id AS registry_plugin_instance_id, "
    "r.owner_state AS registry_owner_state "
    f"FROM {MAPPING_TABLE} AS m "
    f"LEFT JOIN {REGISTRY_TABLE} AS r ON r.id = m.scope_registry_id "
    "WHERE m.source_kind = :source_kind AND m.source_id = :source_id "
    "AND m.schema_version = :schema_version"
)

_INSERT_MAPPING_SQL = (
    f"INSERT INTO {MAPPING_TABLE} ("
    "id, source_kind, source_id, schema_version, source_payload_digest, "
    "canonical_payload_digest, scope_registry_id, mapping_state, reason_code, checkpoint"
    ") VALUES ("
    ":id, :source_kind, :source_id, :schema_version, :source_payload_digest, "
    ":canonical_payload_digest, :scope_registry_id, :mapping_state, :reason_code, :checkpoint"
    ") ON CONFLICT ON CONSTRAINT uq_scope_owner_mapping_source DO NOTHING "
    "RETURNING id"
)

_SELECT_TENANT_SQL = "SELECT id, status, deleted_at FROM companies WHERE id = :tenant_id"

_SELECT_CABINET_SQL = "SELECT id, status FROM cabinet_instances WHERE id = :cabinet_id"

_SELECT_GRANTS_SQL = (
    "SELECT company_id, cabinet_id, status FROM cabinet_company_grants "
    "WHERE cabinet_id = :cabinet_id ORDER BY company_id"
)

_SELECT_PROJECT_SQL = (
    "SELECT id, company_id, cabinet_id, status FROM projects WHERE id = :project_id"
)

_SELECT_ACTIVE_REGISTRY_SQL = (
    "SELECT id, scope_kind, scope_id, tenant_id, cabinet_id, project_id, project_status, "
    "plugin_instance_id, owner_state, source_kind, source_id, schema_version, payload_digest "
    f"FROM {REGISTRY_TABLE} "
    "WHERE tenant_id = :tenant_id AND scope_kind = :scope_kind AND scope_id = :scope_id "
    "AND owner_state = 'active' AND cabinet_id IS NOT DISTINCT FROM :cabinet_id "
    "AND project_id IS NOT DISTINCT FROM :project_id AND plugin_instance_id IS NULL"
)

_INSERT_TENANT_ANCHOR_SQL = (
    f"INSERT INTO {OWNER_TENANT_TABLE} ("
    "id, tenant_id, tenant_type, owner_state, source_kind, source_id, schema_version, payload_digest"
    ") VALUES (:id, :tenant_id, :tenant_type, 'active', :source_kind, :source_id, "
    ":schema_version, :payload_digest) "
    "ON CONFLICT ON CONSTRAINT uq_scope_owner_tenant_identity DO NOTHING"
)

_PROMOTE_TENANT_ANCHOR_SQL = (
    f"UPDATE {OWNER_TENANT_TABLE} SET owner_state = 'active' "
    "WHERE tenant_id = :tenant_id AND tenant_type = :tenant_type AND owner_state <> 'active'"
)

_INSERT_CABINET_ANCHOR_SQL = (
    f"INSERT INTO {OWNER_CABINET_TABLE} ("
    "id, cabinet_id, tenant_id, tenant_type, owner_state, source_kind, source_id, "
    "schema_version, payload_digest"
    ") VALUES (:id, :cabinet_id, :tenant_id, :tenant_type, 'active', :source_kind, :source_id, "
    ":schema_version, :payload_digest) "
    "ON CONFLICT ON CONSTRAINT uq_scope_owner_cabinet_anchor_typed_tuple DO NOTHING"
)

_PROMOTE_CABINET_ANCHOR_SQL = (
    f"UPDATE {OWNER_CABINET_TABLE} SET owner_state = 'active' "
    "WHERE cabinet_id = :cabinet_id AND tenant_id = :tenant_id AND tenant_type = :tenant_type "
    "AND owner_state <> 'active'"
)

# ``scope_registry`` references the project anchor through
# (project_id, tenant_id, tenant_type, cabinet_id, project_status, owner_state).
# A project can legitimately move between cabinets with a new status, so the
# previous active anchor for that project is retired first; history is retained.
_RETIRE_OTHER_PROJECT_ANCHORS_SQL = (
    f"UPDATE {OWNER_PROJECT_TABLE} SET owner_state = 'archived' "
    "WHERE project_id = :project_id AND tenant_id = :tenant_id AND tenant_type = :tenant_type "
    "AND owner_state = 'active' "
    "AND (cabinet_id <> :cabinet_id OR project_status <> :project_status)"
)

_INSERT_PROJECT_ANCHOR_SQL = (
    f"INSERT INTO {OWNER_PROJECT_TABLE} ("
    "id, project_id, tenant_id, tenant_type, cabinet_id, project_status, owner_state, "
    "source_kind, source_id, schema_version, payload_digest"
    ") VALUES (:id, :project_id, :tenant_id, :tenant_type, :cabinet_id, :project_status, 'active', "
    ":source_kind, :source_id, :schema_version, :payload_digest) "
    "ON CONFLICT ON CONSTRAINT uq_scope_owner_project_anchor_typed_tuple_state DO NOTHING"
)

_INSERT_REGISTRY_SQL = (
    f"INSERT INTO {REGISTRY_TABLE} ("
    "id, scope_kind, scope_id, tenant_id, tenant_type, cabinet_id, project_id, project_status, "
    "plugin_instance_id, owner_state, source_kind, source_id, schema_version, payload_digest, checkpoint"
    ") VALUES ("
    ":id, :scope_kind, :scope_id, :tenant_id, :tenant_type, :cabinet_id, :project_id, "
    ":project_status, NULL, 'active', :source_kind, :source_id, :schema_version, "
    ":payload_digest, :checkpoint"
    ") ON CONFLICT ON CONSTRAINT uq_scope_registry_kind_id DO NOTHING "
    "RETURNING id"
)

_INSERT_QUARANTINE_SQL = (
    f"INSERT INTO {QUARANTINE_TABLE} ("
    "id, source_kind, source_id, schema_version, source_payload_digest, reason_code, "
    "resolved_tenant_id, resolved_cabinet_id, resolved_project_id, checkpoint, correlation_id"
    ") VALUES ("
    ":id, :source_kind, :source_id, :schema_version, :source_payload_digest, :reason_code, "
    ":resolved_tenant_id, :resolved_cabinet_id, :resolved_project_id, :checkpoint, :correlation_id"
    ") ON CONFLICT ON CONSTRAINT uq_scope_owner_quarantine_observation DO NOTHING "
    "RETURNING id"
)

_SELECT_QUARANTINE_SQL = (
    f"SELECT id FROM {QUARANTINE_TABLE} "
    "WHERE source_kind = :source_kind AND source_id = :source_id "
    "AND schema_version = :schema_version AND source_payload_digest = :source_payload_digest"
)

_INSERT_AUDIT_SQL = (
    f"INSERT INTO {AUDIT_TABLE} ("
    "id, source_kind, source_id, schema_version, source_payload_digest, event_kind, "
    "reason_code, correlation_id, checkpoint, actor_id"
    ") VALUES ("
    ":id, :source_kind, :source_id, :schema_version, :source_payload_digest, :event_kind, "
    ":reason_code, :correlation_id, :checkpoint, :actor_id"
    ") RETURNING id"
)

_SELECT_ACTOR_SQL = (
    "SELECT "
    "(SELECT count(*) FROM memberships AS m "
    " WHERE m.company_id = :tenant_id AND m.employee_id = :user_id) AS membership_matches, "
    "(SELECT count(*) FROM employees AS e WHERE e.id = :user_id AND e.deleted_at IS NULL "
    " AND e.status = 'active') AS active_employee_matches, "
    "(SELECT count(*) FROM companies AS c WHERE c.id = :tenant_id AND c.deleted_at IS NULL "
    " AND c.keycloak_sub IS NOT NULL AND c.keycloak_sub = :user_id) AS principal_matches"
)

_SELECT_DATABASE_TRANSACTION_SQL = "SELECT txid_current()::text AS database_transaction_id"


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _StoredMapping:
    """A prior mapping row plus the identity the coordinator must echo back."""

    mapping: ExistingScopeMapping
    mapping_id: str


class SqlAlchemyScopeBackfillAdapter:
    """One PostgreSQL transaction per page/row; no fallback writer exists."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        tenant_context: TenantScopeContextPort | None,
    ) -> None:
        if session_factory is None:
            raise ValueError("an async session factory is required")
        if tenant_context is None:
            raise ValueError(
                "a trusted tenant-context port is required; missing tenant context never falls "
                "back to a session-level setting or an in-memory checkpoint"
            )
        self._session_factory = session_factory
        self._tenant_context = tenant_context

    # -- entry points -----------------------------------------------------

    @asynccontextmanager
    async def page_transaction(
        self,
        *,
        context: MigrationEvidenceContext,
        mode: TenantScopeMode = TenantScopeMode.MIGRATION_TRUSTED,
    ) -> AsyncIterator["BoundScopePage"]:
        """Open the single transaction that owns one bounded page.

        Everything the caller does through the yielded page (checkpoint read,
        source page read, per-row mapping) commits or rolls back as one unit.
        """

        if not context.usable:
            raise ScopeBackfillPersistenceBlocked(
                "missing_trusted_transaction_context_or_port",
                "a server-derived transaction context is required",
            )
        async with self._session_factory() as session:
            if str(session.get_bind().dialect.name) != "postgresql":
                raise ScopeBackfillPersistenceBlocked(
                    "postgresql_required",
                    "transaction-local tenant context and CAS checkpoints require PostgreSQL",
                )
            async with session.begin():
                binding = await self._tenant_context.bind(
                    session=session, context=context, mode=mode
                )
                self._verify_binding(context=context, binding=binding, mode=mode)
                database_transaction_id = str(
                    (await session.execute(text(_SELECT_DATABASE_TRANSACTION_SQL))).scalar()
                )
                if database_transaction_id != binding.database_transaction_id:
                    raise ScopeBackfillPersistenceBlocked(
                        "database_transaction_identity_mismatch",
                        "the tenant context is not bound to this database transaction",
                    )
                yield BoundScopePage(
                    adapter=self,
                    session=session,
                    context=context,
                    binding=binding,
                    mode=mode,
                )

    async def load_checkpoint(
        self, *, context: MigrationEvidenceContext, stream: ScopeStreamIdentity
    ) -> BackfillCheckpoint | None:
        async with self.page_transaction(context=context) as page:
            return await page.load_checkpoint(context=context, stream=stream)

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
        async with self.page_transaction(context=context) as page:
            return await page.classify_and_append(
                context=context,
                candidate=candidate,
                source_mapping_identity=source_mapping_identity,
                expected_checkpoint=expected_checkpoint,
                next_checkpoint=next_checkpoint,
                proposed_entry=proposed_entry,
                proposed_quarantine=proposed_quarantine,
                reason_code=reason_code,
            )

    async def resolve_active(
        self, *, context: MigrationEvidenceContext, anchors: ScopeAnchors
    ) -> ScopeResolutionEvidence | None:
        async with self.page_transaction(
            context=context, mode=TenantScopeMode.TENANT_SCOPED
        ) as page:
            return await page.resolve_active(context=context, anchors=anchors)

    # -- shared helpers ---------------------------------------------------

    @staticmethod
    def _verify_binding(
        *,
        context: MigrationEvidenceContext,
        binding: RlsBindingEvidence,
        mode: TenantScopeMode,
    ) -> None:
        if binding.application_transaction_id != context.transaction_id:
            raise ScopeBackfillPersistenceBlocked(
                "application_database_transaction_identity_mismatch",
                "the tenant context was applied to a different transaction",
            )
        if binding.tenant_id != context.tenant_id:
            raise ScopeBackfillPersistenceBlocked(
                "scope_context_mismatch", "the tenant context was applied for another tenant"
            )
        if context.rls_context is not None:
            if binding.cabinet_id != context.rls_context.cabinet_id:
                raise ScopeBackfillPersistenceBlocked(
                    "scope_context_mismatch", "the cabinet context differs from the resolved context"
                )
            if binding.project_id != context.rls_context.project_id:
                raise ScopeBackfillPersistenceBlocked(
                    "scope_context_mismatch", "the project context differs from the resolved context"
                )
        if not binding.role_not_bypass_rls:
            raise ScopeBackfillPersistenceBlocked(
                "database_role_bypasses_rls", "RLS would not apply to this database role"
            )
        if mode is TenantScopeMode.MIGRATION_TRUSTED and not binding.trusted_service:
            raise ScopeBackfillPersistenceBlocked(
                "trusted_service_capability_missing",
                "unresolved mapping and quarantine evidence requires the trusted service capability",
            )
        if mode is TenantScopeMode.TENANT_SCOPED and binding.trusted_service:
            raise ScopeBackfillPersistenceBlocked(
                "trusted_service_capability_unexpected",
                "a request-scoped resolution must not run with a cross-tenant capability",
            )


class BoundScopePage:
    """The three trusted ports, bound to one open transaction."""

    def __init__(
        self,
        *,
        adapter: SqlAlchemyScopeBackfillAdapter,
        session: AsyncSession,
        context: MigrationEvidenceContext,
        binding: RlsBindingEvidence,
        mode: TenantScopeMode,
    ) -> None:
        self._adapter = adapter
        self.session = session
        self.context = context
        self.binding = binding
        self.mode = mode

    # -- checkpoint -------------------------------------------------------

    async def load_checkpoint(
        self, *, context: MigrationEvidenceContext, stream: ScopeStreamIdentity
    ) -> BackfillCheckpoint | None:
        self._require_context(context)
        return await self._read_checkpoint(stream=stream)

    async def _read_checkpoint(
        self, *, stream: ScopeStreamIdentity, for_update: bool = False
    ) -> BackfillCheckpoint | None:
        if stream.tenant_id != self.binding.tenant_id:
            raise ScopeBackfillPersistenceBlocked("checkpoint_tenant_mismatch")
        statement = _SELECT_CHECKPOINT_FOR_UPDATE_SQL if for_update else _SELECT_CHECKPOINT_SQL
        row = (
            await self.session.execute(
                text(statement),
                {
                    "control_kind": CONTROL_SOURCE_KIND,
                    "stream_id": stream_storage_id(stream),
                    "control_schema": CONTROL_SCHEMA_VERSION,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        sequence, fingerprint = decode_checkpoint_token(row["checkpoint"])
        cursor = row["canonical_payload_digest"]
        if cursor is not None and not isinstance(cursor, str):
            raise ScopeBackfillPersistenceBlocked("checkpoint_row_unreadable", "cursor is not text")
        if cursor is not None and len(cursor) > 128:
            raise ScopeBackfillPersistenceBlocked("checkpoint_row_unreadable", "cursor is too wide")
        if fingerprint != cursor_fingerprint(cursor):
            raise ScopeBackfillPersistenceBlocked(
                "checkpoint_row_inconsistent", "the cursor fingerprint does not match the token"
            )
        if sequence == 0 and cursor is not None:
            raise ScopeBackfillPersistenceBlocked(
                "checkpoint_cursor_invalid", "initial sequence with a cursor"
            )
        if sequence > 0 and cursor is None:
            raise ScopeBackfillPersistenceBlocked(
                "checkpoint_cursor_invalid", "advanced sequence without a cursor"
            )
        stored_digest = row["source_payload_digest"]
        if not isinstance(stored_digest, str) or not stored_digest.startswith("sha256:"):
            raise ScopeBackfillPersistenceBlocked(
                "checkpoint_row_unreadable", "the stored snapshot digest is not a sha256 digest"
            )
        return BackfillCheckpoint(
            source_snapshot_id=stream.source_snapshot_id,
            source_schema_version=stream.source_schema_version,
            after_source_id=cursor,
            sequence=sequence,
            source_snapshot_digest=stored_digest,
            tenant_id=stream.tenant_id,
            writer_generation=stream.writer_generation,
            stream_kind=stream.stream_kind,
            canonical_schema_version=stream.canonical_schema_version,
        )

    async def _load_checkpoint_unreadable_is_blocking(
        self, *, stream: ScopeStreamIdentity
    ) -> BackfillCheckpoint | None:
        return await self._read_checkpoint(stream=stream, for_update=True)

    async def _advance_checkpoint(
        self,
        *,
        stream: ScopeStreamIdentity,
        expected: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
    ) -> str | None:
        """Compare-and-swap the durable cursor; returns a conflict reason or ``None``."""

        expected_token = encode_checkpoint_token(expected.sequence, expected.after_source_id)
        next_token = encode_checkpoint_token(
            next_checkpoint.sequence, next_checkpoint.after_source_id
        )
        row = (
            await self.session.execute(
                text(_SELECT_CHECKPOINT_FOR_UPDATE_SQL),
                {
                    "control_kind": CONTROL_SOURCE_KIND,
                    "stream_id": stream_storage_id(stream),
                    "control_schema": CONTROL_SCHEMA_VERSION,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            if expected.sequence != 0 or expected.after_source_id is not None:
                return "checkpoint_compare_and_swap_conflict"
            await self.session.execute(
                text(_INSERT_CHECKPOINT_SQL),
                {
                    "id": new_owner_row_id(),
                    "control_kind": CONTROL_SOURCE_KIND,
                    "source_id": stream_storage_id(stream),
                    "control_schema": CONTROL_SCHEMA_VERSION,
                    "source_payload_digest": next_checkpoint.source_snapshot_digest,
                    "canonical_payload_digest": next_checkpoint.after_source_id,
                    "mapping_state": MAPPING_STATE_MAPPED,
                    "reason_code": "scope_backfill_checkpoint",
                    "checkpoint": next_token,
                },
            )
            return None
        if row["source_payload_digest"] != expected.source_snapshot_digest:
            return "checkpoint_lineage_changed"
        if row["checkpoint"] != expected_token:
            return "checkpoint_compare_and_swap_conflict"
        updated = await self.session.execute(
            text(_UPDATE_CHECKPOINT_SQL),
            {
                "id": row["id"],
                "expected_checkpoint": expected_token,
                "source_payload_digest": next_checkpoint.source_snapshot_digest,
                "canonical_payload_digest": next_checkpoint.after_source_id,
                "checkpoint": next_token,
            },
        )
        if updated.rowcount != 1:
            return "checkpoint_compare_and_swap_conflict"
        return None

    # -- mapping ----------------------------------------------------------

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
        self._require_context(context)
        self._validate_command(
            context=context,
            candidate=candidate,
            source_mapping_identity=source_mapping_identity,
            expected_checkpoint=expected_checkpoint,
            next_checkpoint=next_checkpoint,
            proposed_entry=proposed_entry,
            proposed_quarantine=proposed_quarantine,
        )
        stream = ScopeStreamIdentity(
            source_snapshot_id=expected_checkpoint.source_snapshot_id,
            source_schema_version=expected_checkpoint.source_schema_version,
            source_snapshot_digest=expected_checkpoint.source_snapshot_digest,
            tenant_id=expected_checkpoint.tenant_id,
            writer_generation=expected_checkpoint.writer_generation,
            stream_kind=expected_checkpoint.stream_kind,
            canonical_schema_version=expected_checkpoint.canonical_schema_version,
        )
        anchors = candidate.anchors
        if anchors is not None and anchors.tenant_id != self.binding.tenant_id:
            return self._blocked(
                context=context,
                candidate=candidate,
                checkpoint=expected_checkpoint,
                reason="scope_context_mismatch",
            )
        stored_checkpoint = await self._read_checkpoint(stream=stream, for_update=True)
        if (
            stored_checkpoint is not None
            and stored_checkpoint.source_snapshot_digest
            != expected_checkpoint.source_snapshot_digest
        ):
            return self._conflict(
                context=context,
                candidate=candidate,
                checkpoint=expected_checkpoint,
                reason="checkpoint_lineage_changed",
            )
        if stored_checkpoint is None:
            cursor_matches = expected_checkpoint.sequence == 0
        else:
            cursor_matches = (
                encode_checkpoint_token(
                    stored_checkpoint.sequence, stored_checkpoint.after_source_id
                )
                == encode_checkpoint_token(
                    expected_checkpoint.sequence, expected_checkpoint.after_source_id
                )
            )
        if not cursor_matches:
            return self._conflict(
                context=context,
                candidate=candidate,
                checkpoint=expected_checkpoint,
                reason="checkpoint_compare_and_swap_conflict",
            )

        stored_mapping = await self._read_existing_mapping(candidate=candidate)
        assessment = assess_mapping_replay(
            _ReplayProbe(
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                source_digest=candidate.source_digest,
                canonical_schema_version=candidate.canonical_schema_version,
                anchors=anchors,
            ),  # type: ignore[arg-type]
            None if stored_mapping is None else stored_mapping.mapping,
        )

        if assessment.decision is MappingReplayDecision.REPLAY and stored_mapping is not None:
            return await self._replay(
                context=context,
                candidate=candidate,
                stream=stream,
                stored_checkpoint=stored_checkpoint,
                stored_mapping=stored_mapping,
                expected_checkpoint=expected_checkpoint,
                next_checkpoint=next_checkpoint,
                reason=assessment.reason_code,
            )

        if assessment.decision is MappingReplayDecision.CONFLICT and stored_mapping is not None:
            return await self._append_conflict(
                context=context,
                candidate=candidate,
                stream=stream,
                stored_mapping=stored_mapping,
                expected_checkpoint=expected_checkpoint,
                next_checkpoint=next_checkpoint,
                reason=assessment.reason_code or "source_digest_changed",
            )

        return await self._append_new_mapping(
            context=context,
            candidate=candidate,
            stream=stream,
            expected_checkpoint=expected_checkpoint,
            next_checkpoint=next_checkpoint,
            reason_code=reason_code,
        )

    async def _append_conflict(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        stream: ScopeStreamIdentity,
        stored_mapping: _StoredMapping,
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        reason: str,
    ) -> ScopeMigrationResult:
        """Preserve the first mapping; append redacted conflict evidence; resume past it."""

        next_token = encode_checkpoint_token(
            next_checkpoint.sequence, next_checkpoint.after_source_id
        )
        quarantine_id = await self._append_quarantine(
            context=context,
            candidate=candidate,
            reason_code=reason,
            checkpoint=next_token,
        )
        audit_id = await self._append_audit(
            context=context,
            candidate=candidate,
            event_kind="scope_mapping_conflict",
            reason_code=reason,
            checkpoint=next_token,
        )
        conflict = await self._advance_checkpoint(
            stream=stream, expected=expected_checkpoint, next_checkpoint=next_checkpoint
        )
        if conflict is not None:
            return self._conflict(
                context=context,
                candidate=candidate,
                checkpoint=expected_checkpoint,
                reason=conflict,
            )
        anchors = candidate.anchors
        return ScopeMigrationResult(
            outcome=BackfillOutcome.CONFLICT,
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            source_digest=candidate.source_digest,
            canonical_schema_version=candidate.canonical_schema_version,
            canonical_digest=None,
            checkpoint=next_checkpoint,
            reason_code=reason,
            scope_kind=anchors.scope_kind if anchors else None,
            scope_id=anchors.scope_id if anchors else None,
            correlation_id=context.correlation_id,
            writer_generation=context.writer_generation,
            mapping_id=stored_mapping.mapping_id,
            audit_event_id=audit_id,
            quarantine_id=quarantine_id,
            original_outcome=stored_mapping.mapping.outcome,
            canonical_positive_writes=False,
        )

    async def _replay(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        stream: ScopeStreamIdentity,
        stored_checkpoint: BackfillCheckpoint | None,
        stored_mapping: _StoredMapping,
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        reason: str | None,
    ) -> ScopeMigrationResult:
        """Return the original mapping and a redacted replay audit.

        The cursor advances so a resumable run keeps moving (the coordinator only
        requires the replay checkpoint to share the lineage and never regress).
        Re-processing a row whose successor position is already committed returns
        that position unchanged, so the replay itself stays idempotent.
        """

        if stored_checkpoint is None:
            raise ScopeBackfillPersistenceBlocked(
                "checkpoint_row_missing", "a mapped source row requires its stream checkpoint"
            )
        next_token = encode_checkpoint_token(
            next_checkpoint.sequence, next_checkpoint.after_source_id
        )
        if encode_checkpoint_token(
            stored_checkpoint.sequence, stored_checkpoint.after_source_id
        ) == encode_checkpoint_token(expected_checkpoint.sequence, expected_checkpoint.after_source_id):
            conflict = await self._advance_checkpoint(
                stream=stream, expected=expected_checkpoint, next_checkpoint=next_checkpoint
            )
            if conflict is not None:
                raise ScopeBackfillPersistenceBlocked(conflict)
            checkpoint = next_checkpoint
        else:
            checkpoint = stored_checkpoint
        token = encode_checkpoint_token(checkpoint.sequence, checkpoint.after_source_id)
        audit_id = await self._append_audit(
            context=context,
            candidate=candidate,
            event_kind="scope_mapping_replay_observed",
            reason_code=reason or "source_mapping_replayed",
            checkpoint=token,
        )
        original_identity = stored_mapping.mapping.canonical_identity
        if stored_mapping.mapping.outcome is BackfillOutcome.ACCEPTED:
            expected_identity = (
                self._canonical_identity(candidate.anchors) if candidate.anchors is not None else None
            )
            if original_identity != expected_identity:
                raise ScopeBackfillPersistenceBlocked(
                    "replay_canonical_identity_diverged",
                    "the stored mapping resolves to a different scope",
                )
        quarantine_id = None
        if stored_mapping.mapping.outcome in {
            BackfillOutcome.CONFLICT,
            BackfillOutcome.QUARANTINED,
            BackfillOutcome.DEFERRED,
        }:
            quarantine_id = await self._find_quarantine(context=context, candidate=candidate)
            if quarantine_id is None and stored_mapping.mapping.outcome is not BackfillOutcome.DEFERRED:
                raise ScopeBackfillPersistenceBlocked(
                    "replay_quarantine_missing",
                    "a replay must retain the original redacted observation",
                )
        anchors = candidate.anchors
        return ScopeMigrationResult(
            outcome=BackfillOutcome.REPLAY,
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
            correlation_id=context.correlation_id,
            writer_generation=context.writer_generation,
            mapping_id=stored_mapping.mapping_id,
            audit_event_id=audit_id,
            quarantine_id=quarantine_id,
            original_outcome=stored_mapping.mapping.outcome,
            canonical_identity=(
                original_identity
                if stored_mapping.mapping.outcome is BackfillOutcome.ACCEPTED
                else None
            ),
            canonical_positive_writes=False,
        )

    async def _append_new_mapping(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        stream: ScopeStreamIdentity,
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        reason_code: str | None,
    ) -> ScopeMigrationResult:
        anchors = candidate.anchors
        detailed_reason = reason_code
        parent_evidence: ScopeParentEvidence | None = None
        project_status: str | None = None

        if detailed_reason is None and anchors is not None:
            async with self._narrow(anchors=anchors) as row_binding:
                parent_evidence = await self._parent_evidence(anchors=anchors)
                project_status = await self._project_status(anchors=anchors)
                detailed_reason = self._ancestor_reason(
                    anchors=anchors, evidence=parent_evidence, project_status=project_status
                )
                if detailed_reason is not None or not row_binding.covers(anchors):
                    pass
        if detailed_reason is None and anchors is not None:
            if not self.binding.covers(anchors) and not self._narrowed_covers(anchors):
                return self._blocked(
                    context=context,
                    candidate=candidate,
                    checkpoint=expected_checkpoint,
                    reason="scope_context_mismatch",
                )

        if detailed_reason is not None:
            return await self._append_quarantined_row(
                context=context,
                candidate=candidate,
                stream=stream,
                expected_checkpoint=expected_checkpoint,
                next_checkpoint=next_checkpoint,
                detailed_reason=detailed_reason,
                parent_evidence=parent_evidence,
            )
        return await self._append_accepted_row(
            context=context,
            candidate=candidate,
            stream=stream,
            expected_checkpoint=expected_checkpoint,
            next_checkpoint=next_checkpoint,
            anchors=anchors,
            project_status=project_status,
        )

    async def _append_quarantined_row(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        stream: ScopeStreamIdentity,
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        detailed_reason: str,
        parent_evidence: ScopeParentEvidence | None,
    ) -> ScopeMigrationResult:
        anchors = candidate.anchors
        class_reason = (
            detailed_reason
            if detailed_reason in _COORDINATOR_QUARANTINE_REASONS
            else parent_reason_class(detailed_reason)
        )
        outcome = (
            BackfillOutcome.DEFERRED
            if class_reason == "plugin_instance_deferred_m05"
            else BackfillOutcome.QUARANTINED
        )
        mapping_state = (
            MAPPING_STATE_ARCHIVED
            if class_reason in {"source_row_archived", "source_row_deleted"}
            else MAPPING_STATE_QUARANTINED
        )
        next_token = encode_checkpoint_token(
            next_checkpoint.sequence, next_checkpoint.after_source_id
        )
        quarantine_id = await self._append_quarantine(
            context=context,
            candidate=candidate,
            reason_code=detailed_reason,
            checkpoint=next_token,
        )
        mapping_id = await self._append_mapping(
            context=context,
            candidate=candidate,
            mapping_state=mapping_state,
            reason_code=class_reason,
            checkpoint=next_token,
            scope_registry_id=None,
            canonical_digest=None,
        )
        audit_id = await self._append_audit(
            context=context,
            candidate=candidate,
            event_kind=(
                "scope_scope_deferred"
                if outcome is BackfillOutcome.DEFERRED
                else "scope_scope_quarantined"
            ),
            reason_code=class_reason,
            checkpoint=next_token,
        )
        conflict = await self._advance_checkpoint(
            stream=stream, expected=expected_checkpoint, next_checkpoint=next_checkpoint
        )
        if conflict is not None:
            raise ScopeBackfillPersistenceBlocked(conflict)
        return ScopeMigrationResult(
            outcome=outcome,
            source_kind=candidate.source_kind,
            source_id=candidate.source_id,
            schema_version=candidate.schema_version,
            source_digest=candidate.source_digest,
            canonical_schema_version=candidate.canonical_schema_version,
            canonical_digest=None,
            checkpoint=next_checkpoint,
            reason_code=class_reason,
            scope_kind=anchors.scope_kind if anchors else None,
            scope_id=anchors.scope_id if anchors else None,
            correlation_id=context.correlation_id,
            writer_generation=context.writer_generation,
            parent_evidence=parent_evidence,
            mapping_id=mapping_id,
            audit_event_id=audit_id,
            quarantine_id=quarantine_id,
            canonical_positive_writes=False,
        )

    async def _append_accepted_row(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        stream: ScopeStreamIdentity,
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        anchors: ScopeAnchors | None,
        project_status: str | None,
    ) -> ScopeMigrationResult:
        if anchors is None:
            raise ScopeBackfillPersistenceBlocked("canonical_identity_missing")
        next_token = encode_checkpoint_token(
            next_checkpoint.sequence, next_checkpoint.after_source_id
        )
        async with self._narrow(anchors=anchors) as row_binding:
            if not row_binding.covers(anchors):
                return self._blocked(
                    context=context,
                    candidate=candidate,
                    checkpoint=expected_checkpoint,
                    reason="scope_context_mismatch",
                )
            await self._write_active_anchors(
                context=context, candidate=candidate, anchors=anchors, project_status=project_status
            )
            registry_id, duplicate = await self._insert_active_registry(
                context=context,
                candidate=candidate,
                anchors=anchors,
                project_status=project_status,
                checkpoint=next_token,
            )
            if duplicate or registry_id is None:
                return await self._append_duplicate_scope(
                    context=context,
                    candidate=candidate,
                    stream=stream,
                    expected_checkpoint=expected_checkpoint,
                    next_checkpoint=next_checkpoint,
                    anchors=anchors,
                )
            entry = await self._read_exact_active_entry(
                context=context, candidate=candidate, anchors=anchors
            )
            exact_evidence = await self._parent_evidence(anchors=anchors)
            if not exact_evidence.valid_parent_for(anchors):
                raise ScopeBackfillPersistenceBlocked(
                    "post_write_parent_evidence_invalid",
                    exact_evidence.invalid_reason_for(anchors) or "unknown",
                )
            if not row_binding.covers(anchors):
                raise ScopeBackfillPersistenceBlocked("post_write_scope_context_mismatch")
            canonical_digest = ScopeMigrationCoordinator._canonical_digest(
                entry, candidate.canonical_schema_version
            )
            mapping_id = await self._append_mapping(
                context=context,
                candidate=candidate,
                mapping_state=MAPPING_STATE_MAPPED,
                reason_code=None,
                checkpoint=next_token,
                scope_registry_id=registry_id,
                canonical_digest=canonical_digest,
            )
            audit_id = await self._append_audit(
                context=context,
                candidate=candidate,
                event_kind="scope_scope_accepted",
                reason_code="scope_accepted",
                checkpoint=next_token,
            )
            conflict = await self._advance_checkpoint(
                stream=stream, expected=expected_checkpoint, next_checkpoint=next_checkpoint
            )
            if conflict is not None:
                raise ScopeBackfillPersistenceBlocked(conflict)
            return ScopeMigrationResult(
                outcome=BackfillOutcome.ACCEPTED,
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                schema_version=candidate.schema_version,
                source_digest=candidate.source_digest,
                canonical_schema_version=candidate.canonical_schema_version,
                canonical_digest=canonical_digest,
                checkpoint=next_checkpoint,
                reason_code=None,
                scope_kind=anchors.scope_kind,
                scope_id=anchors.scope_id,
                correlation_id=context.correlation_id,
                writer_generation=context.writer_generation,
                parent_compatibility_proof=self._parent_proof(
                    evidence=exact_evidence, project_status=project_status
                ),
                parent_evidence=exact_evidence,
                parent_compatibility_transaction_id=self.binding.application_transaction_id,
                canonical_identity=self._canonical_identity(anchors),
                canonical_positive_writes=False,
                mapping_id=mapping_id,
                audit_event_id=audit_id,
                rls_evidence=self._rls_evidence(anchors=anchors, binding=row_binding),
                actor_membership_evidence=await self._actor_membership(anchors=anchors),
                resolved_entry=entry,
            )

    async def _append_duplicate_scope(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        stream: ScopeStreamIdentity,
        expected_checkpoint: BackfillCheckpoint,
        next_checkpoint: BackfillCheckpoint,
        anchors: ScopeAnchors,
    ) -> ScopeMigrationResult:
        return await self._append_quarantined_row(
            context=context,
            candidate=candidate,
            stream=stream,
            expected_checkpoint=expected_checkpoint,
            next_checkpoint=next_checkpoint,
            detailed_reason="duplicate_or_ambiguous_scope",
            parent_evidence=None,
        )

    # -- resolution -------------------------------------------------------

    async def resolve_active(
        self, *, context: MigrationEvidenceContext, anchors: ScopeAnchors
    ) -> ScopeResolutionEvidence | None:
        self._require_context(context)
        if anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE:
            return None
        # A cross-tenant capability must never answer a request-scoped resolution,
        # and a binding for another tenant/cabinet/project must never be widened.
        if self.binding.trusted_service or not self.binding.covers(anchors):
            return None
        rows = await self._select_active_registry(anchors=anchors)
        if len(rows) != 1:
            return None
        entry = self._entry_from_row(row=rows[0])
        if entry is None or entry.anchors != anchors or entry.owner_state is not OwnerState.ACTIVE:
            return None
        parent_evidence = await self._parent_evidence(anchors=anchors)
        return ScopeResolutionEvidence(
            entry=entry,
            parent_proof=self._parent_proof(
                evidence=parent_evidence,
                project_status=await self._project_status(anchors=anchors),
            ),
            parent_compatibility_transaction_id=self.binding.application_transaction_id,
            parent_evidence=parent_evidence,
            rls_evidence=self._rls_evidence(anchors=anchors, binding=self.binding),
            actor_membership_evidence=self._actor_membership_evidence(anchors=anchors),
        )

    # -- evidence queries -------------------------------------------------

    async def _select_active_registry(
        self, *, anchors: ScopeAnchors
    ) -> list[Mapping[str, object]]:
        result = await self.session.execute(
            text(_SELECT_ACTIVE_REGISTRY_SQL),
            {
                "tenant_id": self.binding.tenant_id,
                "scope_kind": anchors.scope_kind.value,
                "scope_id": anchors.scope_id,
                "cabinet_id": anchors.cabinet_id,
                "project_id": anchors.project_id,
            },
        )
        return list(result.mappings().all())

    async def _read_exact_active_entry(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        anchors: ScopeAnchors,
    ) -> ScopeRegistryEntry:
        rows = await self._select_active_registry(anchors=anchors)
        if len(rows) != 1:
            raise ScopeBackfillPersistenceBlocked(
                "active_scope_not_unique_after_write", f"matched {len(rows)} rows"
            )
        entry = self._entry_from_row(row=rows[0])
        if entry is None:
            raise ScopeBackfillPersistenceBlocked("active_scope_row_unreadable")
        if entry.anchors != anchors:
            raise ScopeBackfillPersistenceBlocked("active_scope_anchor_mismatch")
        if (
            entry.provenance.source_kind != candidate.source_kind
            or entry.provenance.source_id != candidate.source_id
            or entry.provenance.schema_version != candidate.schema_version
            or entry.provenance.payload_digest != candidate.source_digest
        ):
            raise ScopeBackfillPersistenceBlocked(
                "active_scope_provenance_mismatch",
                "the persisted row does not carry this exact source identity",
            )
        return entry

    @staticmethod
    def _entry_from_row(*, row: Mapping[str, object]) -> ScopeRegistryEntry | None:
        try:
            anchors = ScopeAnchors(
                scope_kind=ScopeKind(str(row["scope_kind"])),
                scope_id=str(row["scope_id"]),
                tenant_id=str(row["tenant_id"]),
                cabinet_id=row["cabinet_id"],  # type: ignore[arg-type]
                project_id=row["project_id"],  # type: ignore[arg-type]
                plugin_instance_id=row["plugin_instance_id"],  # type: ignore[arg-type]
            )
            return ScopeRegistryEntry(
                anchors=anchors,
                owner_state=OwnerState(str(row["owner_state"])),
                provenance=ScopeProvenance(
                    source_kind=str(row["source_kind"]),
                    source_id=str(row["source_id"]),
                    schema_version=str(row["schema_version"]),
                    payload_digest=str(row["payload_digest"]),
                ),
            )
        except (TypeError, ValueError, KeyError):
            return None

    async def _project_status(self, *, anchors: ScopeAnchors) -> str | None:
        if anchors.scope_kind is not ScopeKind.PROJECT or anchors.project_id is None:
            return None
        row = (
            await self.session.execute(
                text(_SELECT_PROJECT_SQL), {"project_id": anchors.project_id}
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        status = row["status"]
        return str(status) if isinstance(status, str) else None

    async def _parent_evidence(self, *, anchors: ScopeAnchors) -> ScopeParentEvidence:
        tenant = self.binding.tenant_id
        tenant_row = (
            await self.session.execute(text(_SELECT_TENANT_SQL), {"tenant_id": tenant})
        ).mappings().one_or_none()
        tenant_matches = 0 if tenant_row is None else 1
        tenant_deleted = bool(tenant_row is not None and tenant_row["deleted_at"] is not None)
        tenant_archived = bool(
            tenant_row is not None and str(tenant_row["status"] or "") != ACTIVE_OWNER_STATE
        )

        cabinet_matches = 0
        cabinet_deleted = False
        cabinet_archived = False
        active_memberships: list[Mapping[str, object]] = []
        membership_kind: str | None = None
        membership_tenant: str | None = None
        membership_cabinet: str | None = None
        if anchors.cabinet_id is not None:
            cabinet_row = (
                await self.session.execute(
                    text(_SELECT_CABINET_SQL), {"cabinet_id": anchors.cabinet_id}
                )
            ).mappings().one_or_none()
            cabinet_matches = 0 if cabinet_row is None else 1
            cabinet_status = str(cabinet_row["status"] or "") if cabinet_row is not None else ""
            cabinet_deleted = cabinet_status == "deleted"
            cabinet_archived = bool(
                cabinet_row is not None and cabinet_status not in {"active", "deleted"}
            )
            grants = (
                await self.session.execute(
                    text(_SELECT_GRANTS_SQL), {"cabinet_id": anchors.cabinet_id}
                )
            ).mappings().all()
            active_memberships = [g for g in grants if str(g["status"] or "") == ACTIVE_OWNER_STATE]
            if len(active_memberships) == 1:
                membership_kind = "cabinet_company_grant"
                membership_tenant = str(active_memberships[0]["company_id"])
                membership_cabinet = str(active_memberships[0]["cabinet_id"])

        project_matches = 0
        project_deleted = False
        project_archived = False
        project_tenant: str | None = None
        project_cabinet: str | None = None
        project_id: str | None = None
        if anchors.scope_kind is ScopeKind.PROJECT:
            project_row = (
                await self.session.execute(
                    text(_SELECT_PROJECT_SQL), {"project_id": anchors.project_id}
                )
            ).mappings().one_or_none()
            project_matches = 0 if project_row is None else 1
            if project_row is not None:
                status = str(project_row["status"] or "")
                project_deleted = status == "deleted"
                project_archived = status == "completed"
                project_tenant = str(project_row["company_id"])
                project_cabinet = str(project_row["cabinet_id"])
                project_id = str(project_row["id"])

        registry_rows = await self._select_active_registry(anchors=anchors)
        active_registry_anchors: ScopeAnchors | None = None
        if len(registry_rows) == 1:
            active_entry = self._entry_from_row(row=registry_rows[0])
            active_registry_anchors = active_entry.anchors if active_entry is not None else None

        return ScopeParentEvidence(
            tenant_matches=tenant_matches,
            cabinet_matches=cabinet_matches,
            active_cabinet_memberships=len(active_memberships),
            project_matches=project_matches,
            active_registry_matches=len(registry_rows),
            transaction_id=self.binding.application_transaction_id,
            tenant_deleted=tenant_deleted,
            cabinet_deleted=cabinet_deleted,
            project_deleted=project_deleted,
            tenant_archived=tenant_archived,
            cabinet_archived=cabinet_archived,
            project_archived=project_archived,
            tenant_id=str(tenant_row["id"]) if tenant_row is not None else None,
            cabinet_id=anchors.cabinet_id if cabinet_matches == 1 else None,
            cabinet_membership_kind=membership_kind,
            membership_tenant_id=membership_tenant,
            membership_cabinet_id=membership_cabinet,
            project_id=project_id,
            project_tenant_id=project_tenant,
            project_cabinet_id=project_cabinet,
            active_registry_anchors=active_registry_anchors,
        )

    def _actor_membership_evidence(self, *, anchors: ScopeAnchors) -> ScopeActorMembershipEvidence:
        """Actor proof is read inside the same transaction before use."""

        raise ScopeBackfillPersistenceBlocked(
            "actor_membership_read_required",
            "actor membership must be read inside the transaction (use the async reader)",
        )

    async def _actor_membership(
        self, *, anchors: ScopeAnchors
    ) -> ScopeActorMembershipEvidence:
        row = (
            await self.session.execute(
                text(_SELECT_ACTOR_SQL),
                {"tenant_id": anchors.tenant_id, "user_id": self.context.user_id},
            )
        ).mappings().one_or_none()
        membership_matches = int(row["membership_matches"] or 0) if row is not None else 0
        active_employee_matches = int(row["active_employee_matches"] or 0) if row is not None else 0
        principal_matches = int(row["principal_matches"] or 0) if row is not None else 0
        employee_kind = membership_matches == 1 and active_employee_matches == 1
        principal_kind = principal_matches == 1
        relationship_matches = int(employee_kind) + int(principal_kind)
        if relationship_matches == 1:
            return ScopeActorMembershipEvidence(
                transaction_id=self.binding.application_transaction_id,
                actor_id=self.context.user_id,
                tenant_id=anchors.tenant_id,
                relationship_kind=(
                    "employee_membership" if employee_kind else "company_principal_binding"
                ),
                relationship_matches=1,
                active=True,
            )
        # No relationship, a disabled employee, or two relationship kinds at once:
        # ambiguous evidence is never a positive proof.
        return ScopeActorMembershipEvidence(
            transaction_id=self.binding.application_transaction_id,
            actor_id=self.context.user_id,
            tenant_id=anchors.tenant_id,
            relationship_kind=("employee_membership" if employee_kind else None),
            relationship_matches=relationship_matches,
            active=False,
        )

    # -- writes -----------------------------------------------------------

    @asynccontextmanager
    async def _narrow(self, *, anchors: ScopeAnchors) -> AsyncIterator[RlsBindingEvidence]:
        """Narrow the transaction context to exactly the row being written."""

        if anchors.tenant_id != self.binding.tenant_id:
            raise ScopeBackfillPersistenceBlocked("scope_context_mismatch")
        if anchors.cabinet_id == self.binding.cabinet_id and anchors.project_id == self.binding.project_id:
            yield self.binding
            return
        async with self._adapter._tenant_context.narrow(
            session=self.session,
            binding=self.binding,
            cabinet_id=anchors.cabinet_id,
            project_id=anchors.project_id,
        ) as narrowed:
            if narrowed.tenant_id != self.binding.tenant_id:
                raise ScopeBackfillPersistenceBlocked(
                    "tenant_scope_widened", "narrowing must never change the tenant"
                )
            yield narrowed

    def _narrowed_covers(self, anchors: ScopeAnchors) -> bool:
        """A tenant-only binding still covers a cabinet row when the cabinet is pinned."""

        return (
            self.binding.tenant_id == anchors.tenant_id
            and (anchors.cabinet_id is None or self.binding.cabinet_id == anchors.cabinet_id)
            and (anchors.project_id is None or self.binding.project_id == anchors.project_id)
        )

    @staticmethod
    def _ancestor_reason(
        *,
        anchors: ScopeAnchors,
        evidence: ScopeParentEvidence,
        project_status: str | None,
    ) -> str | None:
        """``ScopeParentEvidence.invalid_reason_for`` without the post-write registry leg.

        Active-registry cardinality cannot be satisfied before the row exists, so it
        is re-verified (by the unchanged contract method) after the insert.  Every
        other rule — including the exact anchor comparisons — is identical.
        """

        if evidence.tenant_matches != 1:
            return "tenant_parent_missing" if evidence.tenant_matches == 0 else "tenant_parent_ambiguous"
        if evidence.tenant_deleted:
            return "tenant_parent_deleted"
        if evidence.tenant_archived:
            return "tenant_parent_archived"
        if evidence.tenant_id != anchors.tenant_id:
            return "cross_tenant_parent"
        kind = anchors.scope_kind
        if kind is ScopeKind.TENANT:
            return None
        if evidence.cabinet_matches != 1:
            return "cabinet_parent_missing" if evidence.cabinet_matches == 0 else "cabinet_parent_ambiguous"
        if evidence.cabinet_deleted:
            return "cabinet_parent_deleted"
        if evidence.cabinet_archived:
            return "cabinet_parent_archived"
        if evidence.cabinet_id != anchors.cabinet_id:
            return "cabinet_parent_mismatch"
        if evidence.active_cabinet_memberships != 1:
            return (
                "cabinet_membership_missing"
                if evidence.active_cabinet_memberships == 0
                else "cabinet_membership_ambiguous"
            )
        if evidence.cabinet_membership_kind != "cabinet_company_grant":
            return "cabinet_membership_kind_unproven"
        if evidence.membership_tenant_id != anchors.tenant_id:
            return "cross_tenant_cabinet_membership"
        if evidence.membership_cabinet_id != anchors.cabinet_id:
            return "cabinet_membership_mismatch"
        if kind is ScopeKind.CABINET:
            return None
        if kind is ScopeKind.PROJECT:
            if evidence.project_matches != 1:
                return "project_parent_missing" if evidence.project_matches == 0 else "project_parent_ambiguous"
            if evidence.project_deleted:
                return "project_parent_deleted"
            if evidence.project_archived:
                return "project_parent_archived"
            if evidence.project_id != anchors.project_id:
                return "project_parent_mismatch"
            if evidence.project_tenant_id != anchors.tenant_id:
                return "cross_tenant_project_parent"
            if evidence.project_cabinet_id != anchors.cabinet_id:
                return "project_cabinet_mismatch"
            if project_status is None or project_status not in PROJECT_STATUS_VALUES:
                return "project_status_unrepresentable"
            if project_status in _NON_ACTIVE_PROJECT_STATUSES:
                return (
                    "project_parent_deleted"
                    if project_status == "deleted"
                    else "project_parent_archived"
                )
            return None
        return "plugin_instance_scope_denied"

    async def _write_active_anchors(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        anchors: ScopeAnchors,
        project_status: str | None,
    ) -> None:
        payload = {
            "source_kind": candidate.source_kind,
            "source_id": candidate.source_id,
            "schema_version": candidate.schema_version,
            "payload_digest": candidate.source_digest,
        }
        await self.session.execute(
            text(_INSERT_TENANT_ANCHOR_SQL),
            {
                "id": new_owner_row_id(),
                "tenant_id": anchors.tenant_id,
                "tenant_type": TENANT_TYPE,
                **payload,
            },
        )
        await self.session.execute(
            text(_PROMOTE_TENANT_ANCHOR_SQL),
            {"tenant_id": anchors.tenant_id, "tenant_type": TENANT_TYPE},
        )
        if anchors.cabinet_id is not None:
            await self.session.execute(
                text(_INSERT_CABINET_ANCHOR_SQL),
                {
                    "id": new_owner_row_id(),
                    "cabinet_id": anchors.cabinet_id,
                    "tenant_id": anchors.tenant_id,
                    "tenant_type": TENANT_TYPE,
                    **payload,
                },
            )
            await self.session.execute(
                text(_PROMOTE_CABINET_ANCHOR_SQL),
                {
                    "cabinet_id": anchors.cabinet_id,
                    "tenant_id": anchors.tenant_id,
                    "tenant_type": TENANT_TYPE,
                },
            )
        if anchors.scope_kind is ScopeKind.PROJECT and anchors.project_id is not None:
            if project_status is None:
                raise ScopeBackfillPersistenceBlocked("project_status_unrepresentable")
            await self.session.execute(
                text(_RETIRE_OTHER_PROJECT_ANCHORS_SQL),
                {
                    "project_id": anchors.project_id,
                    "tenant_id": anchors.tenant_id,
                    "tenant_type": TENANT_TYPE,
                    "cabinet_id": anchors.cabinet_id,
                    "project_status": project_status,
                },
            )
            await self.session.execute(
                text(_INSERT_PROJECT_ANCHOR_SQL),
                {
                    "id": new_owner_row_id(),
                    "project_id": anchors.project_id,
                    "tenant_id": anchors.tenant_id,
                    "tenant_type": TENANT_TYPE,
                    "cabinet_id": anchors.cabinet_id,
                    "project_status": project_status,
                    **payload,
                },
            )

    async def _insert_active_registry(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        anchors: ScopeAnchors,
        project_status: str | None,
        checkpoint: str,
    ) -> tuple[str | None, bool]:
        """Insert the typed active row; ``(id, duplicate)`` — a duplicate is never overwritten."""

        inserted = (
            await self.session.execute(
                text(_INSERT_REGISTRY_SQL),
                {
                    "id": new_owner_row_id(),
                    "scope_kind": anchors.scope_kind.value,
                    "scope_id": anchors.scope_id,
                    "tenant_id": anchors.tenant_id,
                    "tenant_type": TENANT_TYPE,
                    "cabinet_id": anchors.cabinet_id,
                    "project_id": anchors.project_id,
                    "project_status": project_status,
                    "source_kind": candidate.source_kind,
                    "source_id": candidate.source_id,
                    "schema_version": candidate.schema_version,
                    "payload_digest": candidate.source_digest,
                    "checkpoint": checkpoint,
                },
            )
        ).scalar()
        if inserted is not None:
            return str(inserted), False
        existing = await self._select_active_registry(anchors=anchors)
        if (
            len(existing) == 1
            and existing[0]["source_kind"] == candidate.source_kind
            and existing[0]["source_id"] == candidate.source_id
            and existing[0]["schema_version"] == candidate.schema_version
            and existing[0]["payload_digest"] == candidate.source_digest
        ):
            return str(existing[0]["id"]), False
        return None, True

    async def _append_mapping(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        mapping_state: str,
        reason_code: str | None,
        checkpoint: str,
        scope_registry_id: str | None,
        canonical_digest: str | None,
    ) -> str:
        row_id = (
            await self.session.execute(
                text(_INSERT_MAPPING_SQL),
                {
                    "id": new_owner_row_id(),
                    "source_kind": candidate.source_kind,
                    "source_id": candidate.source_id,
                    "schema_version": candidate.schema_version,
                    "source_payload_digest": candidate.source_digest,
                    "canonical_payload_digest": encode_mapping_canonical_evidence(
                        candidate.canonical_schema_version, canonical_digest
                    ),
                    "scope_registry_id": scope_registry_id,
                    "mapping_state": mapping_state,
                    "reason_code": reason_code or "scope_accepted",
                    "checkpoint": checkpoint,
                },
            )
        ).scalar()
        if row_id is None:
            raise ScopeBackfillPersistenceBlocked(
                "mapping_append_conflict",
                "the first mapping for this source identity already exists",
            )
        return str(row_id)

    async def _append_quarantine(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        reason_code: str,
        checkpoint: str,
    ) -> str:
        anchors = candidate.anchors
        row_id = (
            await self.session.execute(
                text(_INSERT_QUARANTINE_SQL),
                {
                    "id": new_owner_row_id(),
                    "source_kind": candidate.source_kind,
                    "source_id": candidate.source_id,
                    "schema_version": candidate.schema_version,
                    "source_payload_digest": candidate.source_digest,
                    "reason_code": reason_code[:64],
                    "resolved_tenant_id": anchors.tenant_id if anchors else None,
                    "resolved_cabinet_id": anchors.cabinet_id if anchors else None,
                    "resolved_project_id": anchors.project_id if anchors else None,
                    "checkpoint": checkpoint,
                    "correlation_id": context.correlation_id,
                },
            )
        ).scalar()
        if row_id is not None:
            return str(row_id)
        existing = await self._find_quarantine(context=context, candidate=candidate)
        if existing is None:
            raise ScopeBackfillPersistenceBlocked("quarantine_append_conflict")
        return existing

    async def _find_quarantine(
        self, *, context: MigrationEvidenceContext, candidate: ScopeSourceEvidence
    ) -> str | None:
        row = (
            await self.session.execute(
                text(_SELECT_QUARANTINE_SQL),
                {
                    "source_kind": candidate.source_kind,
                    "source_id": candidate.source_id,
                    "schema_version": candidate.schema_version,
                    "source_payload_digest": candidate.source_digest,
                },
            )
        ).mappings().one_or_none()
        return None if row is None else str(row["id"])

    async def _append_audit(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        event_kind: str,
        reason_code: str,
        checkpoint: str,
    ) -> str:
        row_id = (
            await self.session.execute(
                text(_INSERT_AUDIT_SQL),
                {
                    "id": new_owner_row_id(),
                    "source_kind": candidate.source_kind,
                    "source_id": candidate.source_id,
                    "schema_version": candidate.schema_version,
                    "source_payload_digest": candidate.source_digest,
                    "event_kind": event_kind[:64],
                    "reason_code": reason_code[:64],
                    "correlation_id": (context.correlation_id or "missing-correlation")[:128],
                    "checkpoint": checkpoint,
                    "actor_id": context.user_id,
                },
            )
        ).scalar()
        if row_id is None:
            raise ScopeBackfillPersistenceBlocked("append_only_audit_unavailable")
        return str(row_id)

    # -- evidence assembly ------------------------------------------------

    async def _read_existing_mapping(
        self, *, candidate: ScopeSourceEvidence
    ) -> _StoredMapping | None:
        row = (
            await self.session.execute(
                text(_SELECT_MAPPING_SQL),
                {
                    "source_kind": candidate.source_kind,
                    "source_id": candidate.source_id,
                    "schema_version": candidate.schema_version,
                },
            )
        ).mappings().one_or_none()
        if row is None:
            return None
        canonical_schema_version, _canonical_digest = decode_mapping_canonical_evidence(
            row["canonical_payload_digest"]
        )
        canonical_identity: tuple[str, str, str, str | None, str | None, str | None] | None = None
        if (
            row["scope_registry_id"] is not None
            and str(row["registry_owner_state"] or "") == ACTIVE_OWNER_STATE
        ):
            canonical_identity = self._canonical_identity_from_row(row=row)
        return _StoredMapping(
            mapping=ExistingScopeMapping(
                mapping_identity=(
                    str(row["source_kind"]),
                    str(row["source_id"]),
                    str(row["schema_version"]),
                ),
                source_digest=str(row["source_payload_digest"]),
                canonical_schema_version=canonical_schema_version,
                canonical_identity=canonical_identity,
                outcome=self._outcome_from_state(
                    state=str(row["mapping_state"]), reason=row["reason_code"]
                ),
                reason_code=None if row["reason_code"] is None else str(row["reason_code"]),
            ),
            mapping_id=str(row["mapping_id"]),
        )

    @staticmethod
    def _canonical_identity_from_row(
        *, row: Mapping[str, object]
    ) -> tuple[str, str, str, str | None, str | None, str | None] | None:
        scope_kind = row["registry_scope_kind"]
        if scope_kind is None:
            return None
        return (
            str(scope_kind),
            str(row["registry_scope_id"]),
            str(row["registry_tenant_id"]),
            None if row["registry_cabinet_id"] is None else str(row["registry_cabinet_id"]),
            None if row["registry_project_id"] is None else str(row["registry_project_id"]),
            None if row["registry_plugin_instance_id"] is None else str(row["registry_plugin_instance_id"]),
        )

    @staticmethod
    def _outcome_from_state(*, state: str, reason: object) -> BackfillOutcome:
        reason_code = str(reason or "")
        if state == MAPPING_STATE_MAPPED:
            return BackfillOutcome.ACCEPTED
        if state == MAPPING_STATE_QUARANTINED and reason_code == "plugin_instance_deferred_m05":
            return BackfillOutcome.DEFERRED
        if state in {MAPPING_STATE_QUARANTINED, MAPPING_STATE_ARCHIVED}:
            return BackfillOutcome.QUARANTINED
        raise ScopeBackfillPersistenceBlocked("mapping_state_unreadable", state)

    @staticmethod
    def _rls_evidence(
        *, anchors: ScopeAnchors, binding: RlsBindingEvidence
    ) -> ScopeRlsEvidence:
        return ScopeRlsEvidence(
            transaction_id=binding.application_transaction_id,
            tenant_id=binding.tenant_id,
            cabinet_id=binding.cabinet_id,
            project_id=binding.project_id,
            row_security_enabled=binding.row_security_enabled,
            row_security_forced=binding.row_security_forced,
            database_role=binding.database_role,
            security_review_id=binding.security_review_id,
            policy_names=tuple(binding.policy_names),
            tenant_policy_enforced=binding.tenant_policy_enforced,
            using_policy_enforced=binding.using_policy_enforced,
            with_check_policy_enforced=binding.with_check_policy_enforced,
            missing_context_denies=binding.missing_context_denies,
            role_not_bypass_rls=binding.role_not_bypass_rls,
            typed_parent_policy_enforced=binding.typed_parent_policy_enforced,
        )

    @staticmethod
    def _parent_proof(
        *, evidence: ScopeParentEvidence, project_status: str | None
    ) -> ParentCompatibilityProof:
        tenant_ok = evidence.tenant_matches == 1
        cabinet_ok = evidence.cabinet_matches == 1 and not evidence.cabinet_deleted
        membership_ok = (
            evidence.active_cabinet_memberships == 1
            and evidence.cabinet_membership_kind == "cabinet_company_grant"
            and evidence.membership_tenant_id == evidence.tenant_id
            and evidence.membership_cabinet_id == evidence.cabinet_id
        )
        project_row_ok = (
            evidence.project_matches == 1
            and not evidence.project_deleted
            and evidence.project_tenant_id == evidence.tenant_id
            and evidence.project_cabinet_id == evidence.cabinet_id
            and project_status in PROJECT_STATUS_VALUES
        )
        return ParentCompatibilityProof(
            tenant_exists=tenant_ok,
            tenant_retained=tenant_ok
            and not evidence.tenant_deleted
            and not evidence.tenant_archived,
            cabinet_exists=cabinet_ok,
            cabinet_tenant_compatible=bool(
                cabinet_ok
                and not evidence.cabinet_archived
                and evidence.membership_tenant_id == evidence.tenant_id
            ),
            cabinet_membership_active=membership_ok,
            project_exists=evidence.project_matches == 1 and not evidence.project_deleted,
            project_tenant_compatible=evidence.project_tenant_id == evidence.tenant_id,
            project_cabinet_compatible=evidence.project_cabinet_id == evidence.cabinet_id,
            project_policy_eligible=bool(
                project_row_ok and project_status not in _NON_ACTIVE_PROJECT_STATUSES
            ),
        )

    @staticmethod
    def _canonical_identity(
        anchors: ScopeAnchors | None,
    ) -> tuple[str, str, str, str | None, str | None, str | None]:
        if anchors is None:
            raise ScopeBackfillPersistenceBlocked("canonical_identity_missing")
        return (
            anchors.scope_kind.value,
            anchors.scope_id,
            anchors.tenant_id,
            anchors.cabinet_id,
            anchors.project_id,
            anchors.plugin_instance_id,
        )

    # -- guards -----------------------------------------------------------

    def _require_context(self, context: MigrationEvidenceContext) -> None:
        if not context.usable:
            raise ScopeBackfillPersistenceBlocked(
                "missing_trusted_transaction_context_or_port"
            )
        if context.transaction_id != self.binding.application_transaction_id:
            raise ScopeBackfillPersistenceBlocked(
                "application_database_transaction_identity_mismatch"
            )
        if context.correlation_id != self.context.correlation_id:
            raise ScopeBackfillPersistenceBlocked("scope_context_mismatch")

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
            raise ValueError("expected checkpoint tenant does not match the trusted transaction")
        if expected_checkpoint.writer_generation != context.writer_generation:
            raise ValueError("expected checkpoint writer generation does not match the transaction")
        if candidate.anchors is not None and candidate.anchors.tenant_id != context.tenant_id:
            raise ValueError("source candidate tenant does not match the trusted transaction")
        if candidate.schema_version != expected_checkpoint.source_schema_version:
            raise ValueError("source candidate schema does not match the checkpoint lineage")
        if candidate.canonical_schema_version != expected_checkpoint.canonical_schema_version:
            raise ValueError("canonical schema does not match the checkpoint lineage")
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
                raise ValueError(
                    "proposed registry evidence does not match the exact active source candidate"
                )
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
        if (
            candidate.anchors is not None
            and candidate.anchors.scope_kind is ScopeKind.PLUGIN_INSTANCE
            and proposed_entry is not None
        ):
            raise ValueError("plugin-instance scope cannot propose a registry row")

    def _blocked(
        self,
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
            correlation_id=context.correlation_id,
            writer_generation=context.writer_generation,
            canonical_positive_writes=False,
        )

    def _conflict(
        self,
        *,
        context: MigrationEvidenceContext,
        candidate: ScopeSourceEvidence,
        checkpoint: BackfillCheckpoint,
        reason: str,
    ) -> ScopeMigrationResult:
        anchors = candidate.anchors
        return ScopeMigrationResult(
            outcome=BackfillOutcome.CONFLICT,
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
            correlation_id=context.correlation_id,
            writer_generation=context.writer_generation,
            canonical_positive_writes=False,
        )


@dataclass(frozen=True, slots=True)
class _ReplayProbe:
    """Structural stand-in carrying the four fields ``assess_mapping_replay`` reads.

    The trusted transaction receives :class:`ScopeSourceEvidence` (redacted) while
    the replay classifier is written against :class:`ScopeCandidate`.  Both expose
    the same immutable source identity, so the classifier is reused rather than
    re-implemented — there is exactly one replay rule set.
    """

    source_kind: str
    source_id: str
    schema_version: str
    source_digest: str
    canonical_schema_version: str
    anchors: ScopeAnchors | None

    @property
    def mapping_identity(self) -> tuple[str, str, str]:
        return (self.source_kind, self.source_id, self.schema_version)


#: Reason codes the coordinator accepts verbatim for a quarantined/deferred row.
_COORDINATOR_QUARANTINE_REASONS = frozenset(
    {
        "parent_cardinality_or_lifecycle_invalid",
        "duplicate_or_ambiguous_scope",
        "parent_compatibility_unproven",
        "source_row_deleted",
        "source_row_archived",
        "source_row_quarantined",
        "source_row_lifecycle_unknown",
        "unresolved_typed_scope",
        "plugin_instance_deferred_m05",
        "cross_tenant_assertion",
        "cross_tenant_source_assertion",
        "cabinet_source_assertion_mismatch",
        "project_source_assertion_mismatch",
        "source_id_exceeds_typed_provenance",
    }
)


__all__ = [
    "ACTIVE_OWNER_STATE",
    "BoundScopePage",
    "CHECKPOINT_TOKEN_PREFIX",
    "CONTROL_SCHEMA_VERSION",
    "CONTROL_SOURCE_KIND",
    "MAPPING_CANONICAL_NONE",
    "MAPPING_CANONICAL_PREFIX",
    "MAPPING_STATE_ARCHIVED",
    "MAPPING_STATE_MAPPED",
    "MAPPING_STATE_QUARANTINED",
    "PROJECT_STATUS_VALUES",
    "RlsBindingEvidence",
    "ScopeBackfillPersistenceBlocked",
    "SqlAlchemyScopeBackfillAdapter",
    "TenantScopeContextPort",
    "TenantScopeMode",
    "cursor_fingerprint",
    "decode_checkpoint_token",
    "decode_mapping_canonical_evidence",
    "encode_checkpoint_token",
    "encode_mapping_canonical_evidence",
    "new_owner_row_id",
    "parent_reason_class",
    "stream_storage_id",
]
