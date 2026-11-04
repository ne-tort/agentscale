"""Durable SQL implementations of the four remaining M04E backfill ports.

Task ``MIG-M04E-PORTS-01``. The M04E evidence backfill
(:mod:`prodavan.application.migration.m04e_definition_version.backfill`) declares
four trusted ports and refuses to run a page without them
(``trusted_proof_or_source_port_missing``). This module implements them against
tables that already exist — no DDL, no ORM change, no Alembic revision, no
in-memory fallback:

``CandidateReader`` — :class:`SqlM04ECandidateReader` over the legacy
``dynamic_app_definitions`` / ``dynamic_app_versions`` rows.
``UpstreamGatePort`` — :class:`SqlM04EUpstreamGate` over ``companies``,
``scope_owner_*``, ``scope_registry`` and
``m04e_definition_version_promotion_evidence``.
``CandidateProofPort`` — :class:`SqlM04ECandidateProof` over the legacy rows plus
``m04e_dynamic_app_definitions`` / ``m04e_dynamic_app_versions``.
``EvidenceStore`` — :class:`SqlM04EEvidenceStore` **refuses** by name: no
registered M04E per-source ledger exists (see below).

Why the evidence store refuses instead of approximating
=======================================================

``EvidenceStore.classify_and_commit_page`` needs, in **one** transaction: a
per-source first-mapping ledger, quarantine/conflict observations with the
*observed* digest, redacted audit, and a durable cursor advanced by CAS. The
three registered M04E tables (revision ``2026110302``) cannot carry any of it:

* ``m04e_dynamic_app_definitions`` / ``m04e_dynamic_app_versions`` are canonical
  identity rows keyed by ``(id, tenant_id)`` / ``(id, definition_id)``; neither has
  ``source_kind``, ``source_schema_version``, ``observed_*``, ``reason_code``,
  ``correlation_id``, ``checkpoint`` or a sequence column;
* ``m04e_definition_version_promotion_evidence`` is a **per-partition** record
  (``uq_m04e_promotion_revision`` = ``(tenant_id, definition_id, revision_id)``)
  whose ``outcome`` is constrained to ``('blocked', 'quarantined')``
  (``ck_m04e_promotion_outcome``), so it cannot record an accepted mapping, and
  its ``source_digest``/``target_digest``/``current_version_resolution_digest``/
  ``artifact_equality_digest`` are ``NOT NULL`` sha256 gate proofs that a blocked
  page has no truthful value for.

Two tempting workarounds are **rejected** rather than implemented, because both
invent a schema: (a) packing a per-source ledger and a cursor into the
``gate_evidence`` JSONB column of a promotion record, and (b) treating the target
foundation rows as the mapping ledger and synthesising the checkpoint /
correlation fields ``BackfillRecord`` requires. A store that answered
``find_existing`` with ``None`` because it cannot see its own ledger would license
a second append for an already-mapped source identity — the opposite of the
fail-closed rule. :class:`SqlM04EEvidenceStore` therefore raises
:class:`EvidenceStoreUnavailable` carrying
:data:`EVIDENCE_STORE_SCHEMA_GAPS`, and the four port entries stay blockers in
``composition.py`` until a reviewed DDL adds
``m04e_definition_version_mappings`` + ``..._checkpoints`` (mirroring
``scope_owner_mappings`` / ``scope_owner_quarantine`` / ``scope_owner_audit``).

Transaction, tenant and redaction rules shared by every adapter
==============================================================

* One call owns one PostgreSQL transaction over the injected
  ``async_sessionmaker``. Nothing is cached between calls and no adapter holds a
  session, so a page cannot leak a tenant into the next call through a pooled
  connection.
* The transaction-local tenant context is applied with ``set_config(..., true)``
  (never a session-level ``SET``), read back, and proven before any table read —
  the same shape as ``m06_grants_gateway/evidence_store.bind_transaction_context``.
  ``app.migration_role`` is set because the M04E tables are ``FORCE ROW LEVEL
  SECURITY`` with restrictive tenant policies (``rls/policies.py``); a read that
  ran without a bound tenant would return zero rows and be misread as an empty,
  exhausted stream. A readback mismatch raises
  :class:`M04EPortRefused` with ``m04e_backfill_tenant_context_unbound``.
* Every read carries an explicit ``tenant_id`` predicate in addition to RLS, so a
  policy regression cannot widen the partition.
* Only identities, reason codes, sha256 digests, sequence/cursor tokens and the
  correlation id ever appear in a refusal or a digest material. No payload,
  manifest body, description or secret is copied, logged or hashed verbatim
  beyond the source digest the backfill contract already defines.

Determinism
===========

Page order is ``ORDER BY id COLLATE "C"``: byte order, which for UTF-8 equals
Python's ``str`` code-point order, so the keyset cursor the backfill keeps
(``after_source_id``, compared with ``>`` in Python and in SQL) can neither skip
nor re-read a row. Each page is re-validated in Python (strictly ascending,
strictly after the cursor, inside the requested phase/schema lineage); a
violation aborts the page instead of returning a short page that the backfill
would interpret as a drained stream.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import AsyncIterator, Mapping, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillBatch,
    BackfillCheckpoint,
    BackfillContext,
    BackfillRecord,
    CandidateDecision,
    CandidateProof,
    CandidateProofPort,
    CandidateReader,
    EvidenceCandidate,
    EvidenceStatus,
    EvidenceStore,
    SourceRowState,
    UpstreamGateEvidence,
    UpstreamGatePort,
)

# ---------------------------------------------------------------------------
# Tables the adapters read (all registered; nothing is created here)
# ---------------------------------------------------------------------------

LEGACY_DEFINITION_TABLE: Final = "dynamic_app_definitions"
LEGACY_VERSION_TABLE: Final = "dynamic_app_versions"
TARGET_DEFINITION_TABLE: Final = "m04e_dynamic_app_definitions"
TARGET_VERSION_TABLE: Final = "m04e_dynamic_app_versions"
PROMOTION_EVIDENCE_TABLE: Final = "m04e_definition_version_promotion_evidence"
COMPANY_TABLE: Final = "companies"
TENANT_ANCHOR_TABLE: Final = "scope_owner_tenant_anchors"
CABINET_ANCHOR_TABLE: Final = "scope_owner_cabinet_anchors"
PROJECT_ANCHOR_TABLE: Final = "scope_owner_project_anchors"
REGISTRY_TABLE: Final = "scope_registry"
MAPPING_TABLE: Final = "scope_owner_mappings"
CABINET_TABLE: Final = "cabinet_instances"
CABINET_GRANT_TABLE: Final = "cabinet_company_grants"
PROJECT_TABLE: Final = "projects"

#: Source kinds claimed by this reader. The backfill compares
#: ``EvidenceCandidate.schema_version`` with ``BackfillContext.source_schema_version``
#: and ``canonical_schema_version`` with the context's canonical schema, so only the
#: *kind* is fixed here while both schema versions are taken from the trusted context.
LEGACY_DEFINITION_SOURCE_KIND: Final = "m04e.legacy.app_definition"
LEGACY_VERSION_SOURCE_KIND: Final = "m04e.legacy.app_version"
SOURCE_KIND_BY_PHASE: Final[Mapping[str, str]] = {
    "definition": LEGACY_DEFINITION_SOURCE_KIND,
    "version": LEGACY_VERSION_SOURCE_KIND,
}

#: Gate identities reused from the reviewed handoff vocabulary
#: (``composition.MIGRATION_SEQUENCE`` M04E precondition and
#: ``export_m05_evidence``'s required gate set), so a refusal names the same gate a
#: reviewer and the M05 handoff already speak about.
GATE_M04_01_OWNER_REGISTRY: Final = "MIG-M04-01"
GATE_M04_02_TYPED_SCOPE: Final = "MIG-M04-02"
GATE_M04E_01_DEFINITION_FOUNDATION: Final = "MIG-M04E-01"
GATE_ACTIVE_TYPED_PARENT_SCOPE: Final = "active_typed_parent_scope"

#: The backfill refuses a page size above 1000; the reader never reads more.
MAX_PAGE_SIZE: Final = 1000

#: Legacy ``dynamic_app_versions`` has no tenant column: the partition tenant is
#: proven through the owning definition row (a join in SQL, an explicit predicate
#: here) before any version is returned.
VERSION_TABLE_HAS_TENANT_COLUMN: Final = False

_SHA256_PREFIX: Final = "sha256:"
_SHA256_LENGTH: Final = len(_SHA256_PREFIX) + 64

#: Roles recorded in the transaction-local context. The database GRANT/policy gate
#: for these roles is a DDL concern; here they only make the applied context
#: explicit and read back.
CONTEXT_ROLE: Final = "m04e_backfill"


# ---------------------------------------------------------------------------
# Canonical digest (same canonicalisation as the backfill contract)
# ---------------------------------------------------------------------------


def _digest(value: object) -> str:
    """Deterministic ``sha256:`` digest of a JSON-serialisable evidence structure."""

    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return _SHA256_PREFIX + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _is_digest(value: object) -> bool:
    if not isinstance(value, str) or len(value) != _SHA256_LENGTH:
        return False
    if not value.startswith(_SHA256_PREFIX):
        return False
    return all(char in "0123456789abcdef" for char in value[len(_SHA256_PREFIX):])


# ---------------------------------------------------------------------------
# Refusals: typed, named, redacted
# ---------------------------------------------------------------------------


class M04EPortReason(StrEnum):
    """Closed reason vocabulary for every M04E port refusal.

    Partition/context reasons are raised (the caller asked the wrong question and
    must not turn it into per-row quarantine evidence). Gate and proof-absence
    reasons are returned as :class:`PortRefusal` values, which is what the
    ``UpstreamGatePort`` contract expresses as ``None``.
    """

    # -- trusted context / partition ---------------------------------------
    CONTEXT_UNUSABLE = "m04e_backfill_context_unusable"
    PARTITION_MISMATCH = "m04e_backfill_partition_mismatch"
    TENANT_CONTEXT_UNBOUND = "m04e_backfill_tenant_context_unbound"
    ROW_SECURITY_DISABLED = "m04e_row_security_disabled"
    TRANSACTION_ISOLATION_INSUFFICIENT = "m04e_transaction_isolation_insufficient"
    SESSION_FACTORY_MISSING = "m04e_backfill_session_factory_missing"
    # -- reader -------------------------------------------------------------
    PAGE_LIMIT_OUT_OF_RANGE = "m04e_backfill_page_limit_out_of_range"
    PHASE_INVALID = "m04e_backfill_phase_invalid"
    CURSOR_INVALID = "m04e_backfill_cursor_invalid"
    PAGE_ORDER_UNDETERMINED = "m04e_reader_source_order_undetermined"
    PAGE_BOUND_VIOLATED = "m04e_reader_page_bound_violated"
    # -- upstream gates -----------------------------------------------------
    GATE_CONTEXT_INVALID = "m04e_gate_context_invalid"
    M04_01_OWNER_REGISTRY_UNPROVEN = "mig_m04_01_owner_registry_unproven"
    M04_02_TYPED_SCOPE_UNPROVEN = "mig_m04_02_typed_scope_unproven"
    M04E_01_DEFINITION_ABSENT = "mig_m04e_01_definition_absent"
    M04E_01_PROMOTION_ABSENT = "mig_m04e_01_promotion_evidence_absent"
    M04E_01_PROMOTION_BLOCKED = "mig_m04e_01_promotion_evidence_blocked"
    M04E_01_PROMOTION_LINEAGE_MISMATCH = "mig_m04e_01_promotion_evidence_lineage_mismatch"
    M04E_01_POSITIVE_OUTCOME_UNREPRESENTABLE = (
        "mig_m04e_01_positive_promotion_outcome_unrepresentable"
    )
    # -- candidate proof ----------------------------------------------------
    PROOF_CONTEXT_INVALID = "m04e_candidate_proof_context_invalid"
    PROOF_GATES_INVALID = "m04e_candidate_proof_gates_invalid"
    PROOF_CANDIDATE_BINDING_INVALID = "m04e_candidate_proof_binding_invalid"
    PROOF_SOURCE_CHANGED = "m04e_candidate_proof_source_changed"
    PROOF_ACTIVE_SCOPE_MISMATCH = "m04e_candidate_proof_active_scope_mismatch"
    # -- evidence store -----------------------------------------------------
    STORE_MAPPING_LEDGER_MISSING = "m04e_backfill_mapping_ledger_missing"
    STORE_OBSERVATION_LEDGER_MISSING = "m04e_backfill_observation_ledger_missing"
    STORE_CHECKPOINT_LEDGER_MISSING = "m04e_backfill_checkpoint_ledger_missing"
    STORE_BLOCKED_EVIDENCE_UNREPRESENTABLE = "m04e_backfill_blocked_evidence_unrepresentable"
    STORE_ACCEPTED_OUTCOME_UNREPRESENTABLE = "m04e_promotion_accepted_outcome_unrepresentable"


@dataclass(frozen=True, slots=True)
class PortRefusal:
    """One named, redacted reason an M04E port could not return evidence.

    Falsy by construction: ``if refusal:`` reads as "there is evidence", so call
    sites cannot accidentally treat a refusal as success.
    """

    code: M04EPortReason
    detail: str
    partition: tuple[str, str] | None = None
    gate_name: str | None = None
    correlation_id: str | None = None
    blockers: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return False

    def evidence_text(self) -> str:
        """Redacted single-line form suitable for ``record_blocked(blocker_evidence=...)``."""

        gate = f" gate={self.gate_name}" if self.gate_name else ""
        return f"{self.code.value}{gate}: {self.detail}"

    def as_blocker_evidence(self) -> tuple[str, ...]:
        return (self.evidence_text(), *self.blockers)


class M04EPortRefused(RuntimeError):
    """A partition/context-level refusal. Raised; never converted to a row decision.

    A wrong tenant, an unbound RLS context or a candidate whose source row changed
    under the page is *not* evidence about a candidate, so quarantining the row
    would record a false finding. Raising leaves the durable checkpoint untouched
    and lets the operator retry from the same cursor.
    """

    def __init__(self, refusal: PortRefusal) -> None:
        self.refusal = refusal
        super().__init__(refusal.evidence_text())

    @property
    def code(self) -> M04EPortReason:
        return self.refusal.code


class EvidenceStoreUnavailable(M04EPortRefused):
    """The durable M04E evidence boundary has no table to write or read.

    Carries the reviewed schema gap list; see :data:`EVIDENCE_STORE_SCHEMA_GAPS`.
    """


def _refusal(
    code: M04EPortReason,
    detail: str,
    *,
    context: BackfillContext | None = None,
    gate_name: str | None = None,
    blockers: tuple[str, ...] = (),
) -> PortRefusal:
    return PortRefusal(
        code=code,
        detail=detail,
        partition=(
            (context.tenant_id, context.app_definition_id) if context is not None else None
        ),
        gate_name=gate_name,
        correlation_id=context.correlation_id if context is not None else None,
        blockers=blockers,
    )


# ---------------------------------------------------------------------------
# Transaction-local tenant context
# ---------------------------------------------------------------------------

_APPLY_CONTEXT_SQL = text(
    "SELECT "
    "set_config('app.tenant_id', :tenant_id, true) AS tenant_id, "
    "set_config('app.user_id', :user_id, true) AS user_id, "
    "set_config('app.role', :role, true) AS role, "
    "set_config('app.correlation_id', :correlation_id, true) AS correlation_id, "
    "set_config('app.migration_role', 'true', true) AS migration_role"
)

_READBACK_CONTEXT_SQL = text(
    "SELECT "
    "current_setting('app.tenant_id', true) AS tenant_id, "
    "current_setting('app.migration_role', true) AS migration_role, "
    "current_setting('row_security', true) AS row_security, "
    "current_setting('transaction_isolation') AS transaction_isolation"
)


async def bind_m04e_transaction_context(
    session: AsyncSession, context: BackfillContext, *, require_serializable: bool = False,
) -> None:
    """Apply and prove the transaction-local tenant context for one M04E page read.

    Raises :class:`M04EPortRefused` instead of degrading: a page read under an
    unbound tenant would return zero rows for ``FORCE ROW LEVEL SECURITY`` tables,
    which the backfill would interpret as a drained stream.
    """

    values = {
        "tenant_id": context.tenant_id,
        "user_id": context.actor,
        "role": CONTEXT_ROLE,
        "correlation_id": context.correlation_id,
    }
    await session.execute(_APPLY_CONTEXT_SQL, values)
    readback = (await session.execute(_READBACK_CONTEXT_SQL)).mappings().one()
    if str(readback.get("tenant_id") or "") != context.tenant_id:
        raise M04EPortRefused(_refusal(
            M04EPortReason.TENANT_CONTEXT_UNBOUND,
            "the transaction-local tenant setting does not match the backfill partition",
            context=context,
        ))
    if str(readback.get("migration_role") or "") != "true":
        raise M04EPortRefused(_refusal(
            M04EPortReason.TENANT_CONTEXT_UNBOUND,
            "the migration role marker was not applied transaction-locally",
            context=context,
        ))
    if str(readback.get("row_security") or "").lower() != "on":
        raise M04EPortRefused(_refusal(
            M04EPortReason.ROW_SECURITY_DISABLED,
            "row_security is not on; the M04E restrictive policies would be skipped",
            context=context,
        ))
    if require_serializable and str(readback.get("transaction_isolation") or "") != "serializable":
        raise M04EPortRefused(_refusal(
            M04EPortReason.TRANSACTION_ISOLATION_INSUFFICIENT,
            "exact identity proofs require SERIALIZABLE transaction isolation",
            context=context,
        ))


def require_usable_context(context: BackfillContext) -> None:
    """Refuse a context that cannot name its own partition or lineage."""

    if not isinstance(context, BackfillContext) or not context.usable:
        raise M04EPortRefused(_refusal(
            M04EPortReason.CONTEXT_UNUSABLE,
            "tenant, definition, snapshot, schema, transaction, correlation, actor and "
            "decision owner must all be present and the snapshot digest must be sha256",
            context=context if isinstance(context, BackfillContext) else None,
        ))


def require_partition(context: BackfillContext, *, tenant_id: str, app_definition_id: str) -> None:
    """Refuse any read that is not exactly the context's own partition."""

    if (tenant_id, app_definition_id) != (context.tenant_id, context.app_definition_id):
        raise M04EPortRefused(_refusal(
            M04EPortReason.PARTITION_MISMATCH,
            "requested tenant/definition is not the trusted partition of this context",
            context=context,
        ))


# ---------------------------------------------------------------------------
# SQL: legacy source pages (keyset cursor, bounded page)
# ---------------------------------------------------------------------------

_DEFINITION_COLUMNS = (
    "id, tenant_id, app_id, display_name, description, scope_kind, scope_id, "
    "status, current_version_id"
)

_VERSION_COLUMNS = (
    "id, app_definition_id, manifest_version, artifact_digest, artifact_uri, "
    "manifest, requested_capabilities, status"
)

#: One exact definition of the partition (also the tenant proof for versions).
_SELECT_LEGACY_DEFINITION_SQL = text(
    f"SELECT {_DEFINITION_COLUMNS} FROM {LEGACY_DEFINITION_TABLE} "
    "WHERE tenant_id = :tenant_id AND id = :definition_id LIMIT 1"
)

#: Definition phase: keyset page, byte-ordered for a deterministic cursor.
_SELECT_LEGACY_DEFINITION_PAGE_SQL = text(
    f"SELECT {_DEFINITION_COLUMNS} FROM {LEGACY_DEFINITION_TABLE} "
    "WHERE tenant_id = :tenant_id AND id = :definition_id "
    'ORDER BY id COLLATE "C" LIMIT :page_limit'
)

_SELECT_LEGACY_DEFINITION_PAGE_AFTER_SQL = text(
    f"SELECT {_DEFINITION_COLUMNS} FROM {LEGACY_DEFINITION_TABLE} "
    "WHERE tenant_id = :tenant_id AND id = :definition_id "
    'AND id COLLATE "C" > :after_source_id '
    'ORDER BY id COLLATE "C" LIMIT :page_limit'
)

#: Version phase. ``dynamic_app_versions`` has no tenant column, so the partition
#: is bounded by the already-proven owning definition instead of a join that could
#: silently drop an orphan row from the page.
_SELECT_LEGACY_VERSION_PAGE_SQL = text(
    f"SELECT {_VERSION_COLUMNS} FROM {LEGACY_VERSION_TABLE} "
    "WHERE app_definition_id = :definition_id "
    'ORDER BY id COLLATE "C" LIMIT :page_limit'
)

_SELECT_LEGACY_VERSION_PAGE_AFTER_SQL = text(
    f"SELECT {_VERSION_COLUMNS} FROM {LEGACY_VERSION_TABLE} "
    "WHERE app_definition_id = :definition_id "
    'AND id COLLATE "C" > :after_source_id '
    'ORDER BY id COLLATE "C" LIMIT :page_limit'
)

_SELECT_LEGACY_VERSION_SQL = text(
    f"SELECT {_VERSION_COLUMNS} FROM {LEGACY_VERSION_TABLE} "
    "WHERE app_definition_id = :definition_id AND id = :version_id LIMIT 1"
)

#: Same-definition / immutable-artifact evidence: both unique keys must still bind
#: exactly one row inside the definition.
_SELECT_VERSION_KEY_COUNTS_SQL = text(
    "SELECT "
    "COUNT(*) FILTER (WHERE manifest_version = :manifest_version) AS manifest_version_count, "
    "COUNT(*) FILTER (WHERE artifact_digest = :artifact_digest) AS artifact_digest_count "
    f"FROM {LEGACY_VERSION_TABLE} WHERE app_definition_id = :definition_id"
)

#: Target-side identity cross-check: a row that already exists must agree.
_SELECT_TARGET_DEFINITION_SQL = text(
    f"SELECT id, tenant_id, app_id, scope_kind, scope_id, lifecycle, current_version_id, "
    f"source_snapshot_id, source_digest FROM {TARGET_DEFINITION_TABLE} "
    "WHERE tenant_id = :tenant_id AND id = :definition_id LIMIT 1"
)

_SELECT_TARGET_VERSION_SQL = text(
    f"SELECT id, definition_id, tenant_id, app_id, manifest_version, artifact_digest, "
    f"artifact_uri, lifecycle, source_snapshot_id, source_digest FROM {TARGET_VERSION_TABLE} "
    "WHERE tenant_id = :tenant_id AND definition_id = :definition_id AND id = :version_id "
    "LIMIT 1"
)


def _rows(result: Any) -> tuple[Mapping[str, Any], ...]:
    return tuple(result.mappings().all())


def legacy_status_to_state(status: object) -> SourceRowState:
    """Map a legacy lifecycle value onto the backfill's source-row state.

    Anything the legacy schema does not guarantee is ``UNKNOWN`` so the backfill
    quarantines it with ``source_lifecycle_unproven`` instead of this adapter
    guessing an active row.
    """

    if status in {"draft", "published"}:
        return SourceRowState.ACTIVE
    if status in {"disabled", "revoked"}:
        return SourceRowState.ARCHIVED
    return SourceRowState.UNKNOWN


def definition_candidate(
    context: BackfillContext, row: Mapping[str, Any],
) -> EvidenceCandidate:
    """Build the canonical definition candidate for one legacy definition row.

    Timestamps are excluded on purpose: the candidate digest is the immutable
    source identity the store replays on, and a touch that changes only
    ``updated_at`` must not turn an unchanged mapping into a digest conflict. Any
    change to the identity/approval fields below *is* a source change and does
    become a conflict — that is the reviewed contract, not an accident.
    """

    return EvidenceCandidate(
        source_kind=LEGACY_DEFINITION_SOURCE_KIND,
        source_id=str(row["id"]),
        schema_version=context.source_schema_version,
        phase="definition",
        tenant_id=str(row["tenant_id"]),
        app_definition_id=str(row["id"]),
        source_state=legacy_status_to_state(row["status"]),
        canonical_schema_version=context.canonical_schema_version,
        payload={
            "app_id": row["app_id"],
            "display_name": row["display_name"],
            "description": row["description"],
            "scope_kind": row["scope_kind"],
            "scope_id": row["scope_id"],
            "status": row["status"],
            "current_version_id": row["current_version_id"],
        },
    )


def version_candidate(
    context: BackfillContext,
    row: Mapping[str, Any],
    definition: Mapping[str, Any],
) -> EvidenceCandidate:
    """Build the canonical version candidate for one legacy version row.

    ``definition_current_version_id`` is carried **only** for a published version:
    ``DefinitionVersionEvidenceBackfill._valid_candidate_proof`` requires a draft
    version candidate to report no current version, while a published one must
    report the definition's current pointer and have it proven.
    """

    status = row["status"]
    return EvidenceCandidate(
        source_kind=LEGACY_VERSION_SOURCE_KIND,
        source_id=str(row["id"]),
        schema_version=context.source_schema_version,
        phase="version",
        tenant_id=str(definition["tenant_id"]),
        app_definition_id=str(definition["id"]),
        source_state=legacy_status_to_state(status),
        canonical_schema_version=context.canonical_schema_version,
        payload={
            "manifest_version": row["manifest_version"],
            "artifact_digest": row["artifact_digest"],
            "artifact_uri": row["artifact_uri"],
            "manifest": dict(row["manifest"] or {}),
            "requested_capabilities": list(row["requested_capabilities"] or []),
            "status": status,
            "definition_status": definition["status"],
            "definition_current_version_id": (
                definition["current_version_id"] if status == "published" else None
            ),
        },
    )


class _SessionReads:
    """One read transaction with the transaction-local tenant context applied.

    Shared base of the three read adapters. ``session_factory`` is mandatory: a
    read adapter without a database is exactly the in-memory path this task
    forbids.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        require_serializable: bool = False,
    ) -> None:
        if session_factory is None:
            raise ValueError(M04EPortReason.SESSION_FACTORY_MISSING.value)
        self._session_factory = session_factory
        self._require_serializable = require_serializable

    @asynccontextmanager
    async def _read(self, context: BackfillContext) -> AsyncIterator[AsyncSession]:
        require_usable_context(context)
        async with self._session_factory() as session:
            async with session.begin():
                await bind_m04e_transaction_context(
                    session, context, require_serializable=self._require_serializable,
                )
                yield session

    async def _definition_row(
        self, session: AsyncSession, context: BackfillContext,
    ) -> Mapping[str, Any] | None:
        rows = _rows(await session.execute(
            _SELECT_LEGACY_DEFINITION_SQL,
            {"tenant_id": context.tenant_id, "definition_id": context.app_definition_id},
        ))
        if not rows:
            return None
        row = rows[0]
        # Defence in depth: the SQL predicate already bounds the tenant.
        if str(row["tenant_id"]) != context.tenant_id:
            raise M04EPortRefused(_refusal(
                M04EPortReason.PARTITION_MISMATCH,
                "legacy definition row belongs to another tenant than the partition",
                context=context,
            ))
        return row


class SqlM04ECandidateReader(_SessionReads):
    """Bounded, cursor-ordered reader over the legacy M04 definition/version rows.

    Implements :class:`CandidateReader`: one page per call, never "read everything
    and filter here". The reader returns candidates as they are — including
    archived, malformed or unpublished rows — because classification belongs to
    ``DefinitionVersionEvidenceBackfill._candidate_rejection``; what the reader
    refuses is anything that would make a *short* page mean "stream drained":

    * an unusable context, a wrong phase or a cursor that is not a non-empty string;
    * a page limit outside ``1..min(1000, configured limit)``;
    * a transaction whose tenant context/row security could not be proven;
    * a page that is not strictly ascending, not strictly after the cursor, or not
      inside the context's phase/schema lineage.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        page_size_limit: int = MAX_PAGE_SIZE,
        require_serializable: bool = False,
    ) -> None:
        if not isinstance(page_size_limit, int) or not 1 <= page_size_limit <= MAX_PAGE_SIZE:
            raise ValueError(M04EPortReason.PAGE_LIMIT_OUT_OF_RANGE.value)
        super().__init__(
            session_factory=session_factory, require_serializable=require_serializable,
        )
        self._page_size_limit = page_size_limit

    @property
    def page_size_limit(self) -> int:
        return self._page_size_limit

    async def read_page(
        self, *, context: BackfillContext, phase: str,
        after_source_id: str | None, limit: int,
    ) -> Sequence[EvidenceCandidate]:
        if phase not in SOURCE_KIND_BY_PHASE:
            raise M04EPortRefused(_refusal(
                M04EPortReason.PHASE_INVALID,
                "phase must be 'definition' or 'version'",
                context=context,
            ))
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= self._page_size_limit:
            raise M04EPortRefused(_refusal(
                M04EPortReason.PAGE_LIMIT_OUT_OF_RANGE,
                f"page limit must be an integer in 1..{self._page_size_limit}",
                context=context,
            ))
        cursor = self._cursor(after_source_id, context)
        async with self._read(context) as session:
            definition = await self._definition_row(session, context)
            if definition is None:
                # The partition's definition is absent: the stream is empty for this
                # partition. It cannot leak another tenant's rows (tenant predicate)
                # and it cannot become a READY handoff (export_m05_evidence requires
                # accepted rows), and the gate port refuses this partition first.
                return ()
            if phase == "definition":
                rows = await self._definition_page(session, context, cursor, limit)
                candidates = tuple(definition_candidate(context, row) for row in rows)
            else:
                rows = await self._version_page(session, definition, cursor, limit)
                candidates = tuple(
                    version_candidate(context, row, definition) for row in rows
                )
        self._validate_page(candidates, cursor=cursor, limit=limit, phase=phase, context=context)
        return candidates

    @staticmethod
    def _cursor(after_source_id: str | None, context: BackfillContext) -> str | None:
        if after_source_id is None:
            return None
        if not isinstance(after_source_id, str) or not after_source_id.strip():
            raise M04EPortRefused(_refusal(
                M04EPortReason.CURSOR_INVALID,
                "after_source_id must be None or a non-empty source identity",
                context=context,
            ))
        return after_source_id

    async def _definition_page(
        self, session: AsyncSession, context: BackfillContext,
        cursor: str | None, limit: int,
    ) -> tuple[Mapping[str, Any], ...]:
        parameters: dict[str, Any] = {
            "tenant_id": context.tenant_id,
            "definition_id": context.app_definition_id,
            "page_limit": limit,
        }
        statement = _SELECT_LEGACY_DEFINITION_PAGE_SQL
        if cursor is not None:
            statement = _SELECT_LEGACY_DEFINITION_PAGE_AFTER_SQL
            parameters["after_source_id"] = cursor
        return _rows(await session.execute(statement, parameters))

    async def _version_page(
        self, session: AsyncSession, definition: Mapping[str, Any],
        cursor: str | None, limit: int,
    ) -> tuple[Mapping[str, Any], ...]:
        parameters: dict[str, Any] = {
            "definition_id": definition["id"],
            "page_limit": limit,
        }
        statement = _SELECT_LEGACY_VERSION_PAGE_SQL
        if cursor is not None:
            statement = _SELECT_LEGACY_VERSION_PAGE_AFTER_SQL
            parameters["after_source_id"] = cursor
        return _rows(await session.execute(statement, parameters))

    def _validate_page(
        self,
        candidates: tuple[EvidenceCandidate, ...],
        *,
        cursor: str | None,
        limit: int,
        phase: str,
        context: BackfillContext,
    ) -> None:
        if len(candidates) > limit:
            raise M04EPortRefused(_refusal(
                M04EPortReason.PAGE_BOUND_VIOLATED,
                "the source page exceeded the requested bound",
                context=context,
            ))
        previous: str | None = None
        for candidate in candidates:
            if candidate.phase != phase:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PAGE_BOUND_VIOLATED,
                    "the source page returned a row from another phase",
                    context=context,
                ))
            if (
                candidate.schema_version != context.source_schema_version
                or candidate.canonical_schema_version != context.canonical_schema_version
            ):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PAGE_BOUND_VIOLATED,
                    "the source page returned a row outside the checkpoint schema lineage",
                    context=context,
                ))
            if candidate.source_kind != SOURCE_KIND_BY_PHASE[phase]:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PAGE_BOUND_VIOLATED,
                    "the source page returned a row with an unexpected source kind",
                    context=context,
                ))
            if (candidate.tenant_id, candidate.app_definition_id) != (
                context.tenant_id, context.app_definition_id,
            ):
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PARTITION_MISMATCH,
                    "the source page returned a row outside the trusted partition",
                    context=context,
                ))
            # Byte order (COLLATE "C") equals code-point order for UTF-8, so this
            # can only fail on a collation/ordering regression — which must abort
            # the page, not silently shorten it.
            if cursor is not None and candidate.source_id <= cursor:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PAGE_ORDER_UNDETERMINED,
                    "the source page returned an identity at or before the cursor",
                    context=context,
                ))
            if previous is not None and candidate.source_id <= previous:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PAGE_ORDER_UNDETERMINED,
                    "the source page is not strictly ascending by source identity",
                    context=context,
                ))
            previous = candidate.source_id


# ---------------------------------------------------------------------------
# SQL: M04 owner/typed-scope evidence and M04E promotion evidence
# ---------------------------------------------------------------------------

_SELECT_COMPANY_SQL = text(
    f"SELECT id, status, deleted_at FROM {COMPANY_TABLE} WHERE id = :tenant_id LIMIT 1"
)

_SELECT_TENANT_ANCHOR_SQL = text(
    "SELECT id, tenant_id, tenant_type, owner_state, source_kind, source_id, "
    f"schema_version, payload_digest FROM {TENANT_ANCHOR_TABLE} "
    "WHERE tenant_id = :tenant_id AND tenant_type = 'company' AND owner_state = 'active' "
    "ORDER BY id"
)

_SELECT_ACTIVE_REGISTRY_SQL = text(
    "SELECT id, scope_kind, scope_id, tenant_id, cabinet_id, project_id, project_status, "
    "plugin_instance_id, owner_state, source_kind, source_id, schema_version, "
    f"payload_digest, checkpoint FROM {REGISTRY_TABLE} "
    "WHERE tenant_id = :tenant_id AND scope_kind = :scope_kind AND scope_id = :scope_id "
    "AND owner_state = 'active' ORDER BY id"
)

_SELECT_SCOPE_MAPPING_SQL = text(
    "SELECT id, source_kind, source_id, schema_version, source_payload_digest, "
    "canonical_payload_digest, scope_registry_id, mapping_state, reason_code, checkpoint "
    f"FROM {MAPPING_TABLE} "
    "WHERE scope_registry_id = :registry_id AND mapping_state = 'mapped' ORDER BY id"
)

_SELECT_CABINET_ANCHOR_SQL = text(
    "SELECT id, cabinet_id, tenant_id, tenant_type, owner_state, source_kind, source_id, "
    f"schema_version, payload_digest FROM {CABINET_ANCHOR_TABLE} "
    "WHERE cabinet_id = :cabinet_id AND tenant_id = :tenant_id AND tenant_type = 'company' "
    "AND owner_state = 'active' ORDER BY id"
)

_SELECT_CABINET_GRANT_SQL = text(
    "SELECT id, cabinet_id, company_id, mode, status FROM "
    f"{CABINET_GRANT_TABLE} WHERE cabinet_id = :cabinet_id AND company_id = :tenant_id "
    "ORDER BY id"
)

_SELECT_CABINET_ROW_SQL = text(
    f"SELECT id, company_id, owner_scope, status FROM {CABINET_TABLE} WHERE id = :cabinet_id "
    "LIMIT 1"
)

_SELECT_PROJECT_ANCHOR_SQL = text(
    "SELECT id, project_id, tenant_id, tenant_type, cabinet_id, project_status, owner_state, "
    f"source_kind, source_id, schema_version, payload_digest FROM {PROJECT_ANCHOR_TABLE} "
    "WHERE project_id = :project_id AND tenant_id = :tenant_id AND tenant_type = 'company' "
    "AND cabinet_id = :cabinet_id AND project_status = 'active' AND owner_state = 'active' "
    "ORDER BY id"
)

_SELECT_PROJECT_ROW_SQL = text(
    f"SELECT id, company_id, cabinet_id, status FROM {PROJECT_TABLE} "
    "WHERE id = :project_id LIMIT 1"
)

#: Latest durable M04E promotion record for the partition. The table is keyed by
#: ``(tenant_id, definition_id, revision_id)`` and this read is the exact-key read
#: the gate needs; another partition's row is unreachable by construction.
_SELECT_PROMOTION_EVIDENCE_SQL = text(
    "SELECT id, tenant_id, definition_id, revision_id, outcome, source_snapshot_id, "
    "source_digest, target_digest, valid_count, archived_count, quarantined_count, "
    "current_version_resolution_digest, artifact_equality_digest, gate_evidence, "
    "previous_revision_id, recorded_at "
    f"FROM {PROMOTION_EVIDENCE_TABLE} "
    "WHERE tenant_id = :tenant_id AND definition_id = :definition_id "
    "ORDER BY recorded_at DESC, revision_id DESC LIMIT 1"
)

#: Promotion outcomes the registered schema permits today
#: (``ck_m04e_promotion_outcome`` in revision ``2026110302``).
_NEGATIVE_PROMOTION_OUTCOMES: Final[frozenset[str]] = frozenset({"blocked", "quarantined"})
_POSITIVE_PROMOTION_OUTCOME: Final = "accepted"

_PROVENANCE_FIELDS: Final[tuple[str, ...]] = (
    "source_kind", "source_id", "schema_version", "payload_digest",
)


def _row_digest(row: Mapping[str, Any], fields: Sequence[str]) -> str:
    """Digest of the named persisted columns only; never the whole payload."""

    return _digest({field: row.get(field) for field in fields})


def _provenance_ok(row: Mapping[str, Any]) -> bool:
    """A provenance tuple must name its source and carry a sha256 payload digest."""

    return (
        all(isinstance(row.get(field), str) and str(row.get(field)).strip()
            for field in ("source_kind", "source_id", "schema_version"))
        and _is_digest(row.get("payload_digest"))
    )


# ---------------------------------------------------------------------------
# Evidence value objects produced by the gate and the candidate proof
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class OwnerEvidence:
    """``MIG-M04-01``: the retained tenant company and its typed owner anchor."""

    tenant_id: str
    company_digest: str
    tenant_anchor_digest: str

    def digest(self) -> str:
        return _digest({
            "gate": GATE_M04_01_OWNER_REGISTRY,
            "tenant_id": self.tenant_id,
            "company": self.company_digest,
            "tenant_anchor": self.tenant_anchor_digest,
        })


@dataclass(frozen=True, slots=True)
class TypedScopeEvidence:
    """``MIG-M04-02`` plus ``active_typed_parent_scope`` for one typed scope.

    The digest of this value is the ``active_scope_digest`` the backfill compares
    against ``CandidateProof.active_scope_proof_digest``, so the gate and the
    candidate proof must build it through the same helper — otherwise a page would
    pass the gate and then fail every candidate proof.
    """

    tenant_id: str
    scope_kind: str
    scope_id: str
    cabinet_id: str | None
    project_id: str | None
    registry_id: str
    registry_digest: str
    mapping_digest: str
    parent_digests: tuple[tuple[str, str], ...]

    def digest(self) -> str:
        return _digest({
            "gate": GATE_M04_02_TYPED_SCOPE,
            "parent_gate": GATE_ACTIVE_TYPED_PARENT_SCOPE,
            "tenant_id": self.tenant_id,
            "scope_kind": self.scope_kind,
            "scope_id": self.scope_id,
            "cabinet_id": self.cabinet_id,
            "project_id": self.project_id,
            "registry_id": self.registry_id,
            "registry": self.registry_digest,
            "mapping": self.mapping_digest,
            "parents": [list(item) for item in self.parent_digests],
        })


@dataclass(frozen=True, slots=True)
class PromotionEvidence:
    """``MIG-M04E-01``: one exact-key durable promotion record for the partition."""

    tenant_id: str
    definition_id: str
    revision_id: str
    outcome: str
    source_snapshot_id: str
    source_digest: str
    target_digest: str
    gate_evidence_digest: str

    def digest(self) -> str:
        return _digest({
            "gate": GATE_M04E_01_DEFINITION_FOUNDATION,
            "tenant_id": self.tenant_id,
            "definition_id": self.definition_id,
            "revision_digest": _digest({"revision_id": self.revision_id}),
            "outcome": self.outcome,
            "source_snapshot_id": self.source_snapshot_id,
            "source_digest": self.source_digest,
            "target_digest": self.target_digest,
            "gate_evidence": self.gate_evidence_digest,
        })


@dataclass(frozen=True, slots=True)
class GateDecision:
    """Either the proven upstream gate evidence or the named refusal that stopped it."""

    evidence: UpstreamGateEvidence | None
    refusal: PortRefusal | None
    probes: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        return self.evidence is not None

    @property
    def gate_names(self) -> tuple[str, ...]:
        if self.refusal is not None and self.refusal.gate_name:
            return (self.refusal.gate_name,)
        return ()


# ---------------------------------------------------------------------------
# Shared read helpers (one transaction, exact keys, sha256 provenance only)
# ---------------------------------------------------------------------------


async def read_owner_evidence(
    session: AsyncSession, *, context: BackfillContext, tenant_id: str,
) -> OwnerEvidence | PortRefusal:
    """Prove ``MIG-M04-01``: active retained company + exactly one active tenant anchor."""

    company_rows = _rows(await session.execute(_SELECT_COMPANY_SQL, {"tenant_id": tenant_id}))
    if len(company_rows) != 1:
        return _refusal(
            M04EPortReason.M04_01_OWNER_REGISTRY_UNPROVEN,
            "no single retained company row backs the partition tenant",
            context=context, gate_name=GATE_M04_01_OWNER_REGISTRY,
        )
    company = company_rows[0]
    if str(company.get("status") or "") != "active" or company.get("deleted_at") is not None:
        return _refusal(
            M04EPortReason.M04_01_OWNER_REGISTRY_UNPROVEN,
            "the retained company row is not active or is soft-deleted",
            context=context, gate_name=GATE_M04_01_OWNER_REGISTRY,
        )
    anchor_rows = _rows(await session.execute(
        _SELECT_TENANT_ANCHOR_SQL, {"tenant_id": tenant_id},
    ))
    if len(anchor_rows) != 1:
        return _refusal(
            M04EPortReason.M04_01_OWNER_REGISTRY_UNPROVEN,
            "exactly one active canonical tenant anchor is required for the partition tenant",
            context=context, gate_name=GATE_M04_01_OWNER_REGISTRY,
        )
    anchor = anchor_rows[0]
    if not _provenance_ok(anchor) or str(anchor.get("tenant_id")) != tenant_id:
        return _refusal(
            M04EPortReason.M04_01_OWNER_REGISTRY_UNPROVEN,
            "the active tenant anchor has no sha256 source provenance for this tenant",
            context=context, gate_name=GATE_M04_01_OWNER_REGISTRY,
        )
    return OwnerEvidence(
        tenant_id=tenant_id,
        company_digest=_row_digest(company, ("id", "status", "deleted_at")),
        tenant_anchor_digest=_row_digest(anchor, ("id", "tenant_id", "tenant_type",
                                                  "owner_state", *_PROVENANCE_FIELDS)),
    )


async def read_typed_scope_evidence(
    session: AsyncSession, *, context: BackfillContext, definition: Mapping[str, Any],
) -> TypedScopeEvidence | PortRefusal:
    """Prove ``MIG-M04-02`` / ``active_typed_parent_scope`` for the definition's scope.

    Every parent hop is re-read from the live retained tables (company → cabinet →
    grant → project) and must bind exactly one active row each; the registry and its
    mapped source row must agree on source identity, payload digest and checkpoint.
    """

    tenant_id = context.tenant_id
    scope_kind = str(definition.get("scope_kind") or "")
    scope_id = str(definition.get("scope_id") or "")
    if scope_kind not in {"tenant", "cabinet", "project"} or not scope_id.strip():
        return _refusal(
            M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
            "the legacy definition does not name a typed tenant/cabinet/project scope",
            context=context, gate_name=GATE_M04_02_TYPED_SCOPE,
        )

    registry_rows = _rows(await session.execute(
        _SELECT_ACTIVE_REGISTRY_SQL,
        {"tenant_id": tenant_id, "scope_kind": scope_kind, "scope_id": scope_id},
    ))
    if len(registry_rows) != 1:
        return _refusal(
            M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
            "exactly one active typed M04 registry row is required for this scope",
            context=context, gate_name=GATE_M04_02_TYPED_SCOPE,
        )
    registry = registry_rows[0]
    if (
        not _provenance_ok(registry)
        or str(registry.get("tenant_id")) != tenant_id
        or str(registry.get("scope_kind")) != scope_kind
        or str(registry.get("scope_id")) != scope_id
        or registry.get("plugin_instance_id") is not None
        or not str(registry.get("checkpoint") or "").strip()
    ):
        return _refusal(
            M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
            "the active registry row is not a checkpointed typed scope with sha256 provenance",
            context=context, gate_name=GATE_M04_02_TYPED_SCOPE,
        )

    mapping_rows = _rows(await session.execute(
        _SELECT_SCOPE_MAPPING_SQL, {"registry_id": registry["id"]},
    ))
    if len(mapping_rows) != 1:
        return _refusal(
            M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
            "exactly one mapped source row must back the active registry identity",
            context=context, gate_name=GATE_M04_02_TYPED_SCOPE,
        )
    mapping = mapping_rows[0]
    if (
        str(mapping.get("source_kind")) != str(registry.get("source_kind"))
        or str(mapping.get("source_id")) != str(registry.get("source_id"))
        or str(mapping.get("schema_version")) != str(registry.get("schema_version"))
        or str(mapping.get("source_payload_digest")) != str(registry.get("payload_digest"))
        or not _is_digest(mapping.get("source_payload_digest"))
        or not _is_digest(mapping.get("canonical_payload_digest"))
        or not str(mapping.get("checkpoint") or "").strip()
    ):
        return _refusal(
            M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
            "the mapped source row disagrees with the registry identity, digest or checkpoint",
            context=context, gate_name=GATE_M04_02_TYPED_SCOPE,
        )

    parents: list[tuple[str, str]] = []
    cabinet_id = registry.get("cabinet_id")
    project_id = registry.get("project_id")

    if scope_kind == "tenant":
        if cabinet_id is not None or project_id is not None or scope_id != tenant_id:
            return _refusal(
                M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
                "a tenant-scoped registry row must bind the tenant itself and no parent",
                context=context, gate_name=GATE_ACTIVE_TYPED_PARENT_SCOPE,
            )
    else:
        if not isinstance(cabinet_id, str) or not cabinet_id.strip():
            return _refusal(
                M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
                "a cabinet/project scope requires the cabinet parent identity",
                context=context, gate_name=GATE_ACTIVE_TYPED_PARENT_SCOPE,
            )
        cabinet_digest = await _cabinet_parent_digest(
            session, context=context, tenant_id=tenant_id, cabinet_id=cabinet_id,
        )
        if isinstance(cabinet_digest, PortRefusal):
            return cabinet_digest
        parents.append(("cabinet", cabinet_digest))

    if scope_kind == "project":
        if not isinstance(project_id, str) or not project_id.strip():
            return _refusal(
                M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
                "a project scope requires the project parent identity",
                context=context, gate_name=GATE_ACTIVE_TYPED_PARENT_SCOPE,
            )
        project_digest = await _project_parent_digest(
            session, context=context, tenant_id=tenant_id,
            cabinet_id=str(cabinet_id), project_id=project_id,
        )
        if isinstance(project_digest, PortRefusal):
            return project_digest
        parents.append(("project", project_digest))

    return TypedScopeEvidence(
        tenant_id=tenant_id,
        scope_kind=scope_kind,
        scope_id=scope_id,
        cabinet_id=str(cabinet_id) if isinstance(cabinet_id, str) else None,
        project_id=str(project_id) if isinstance(project_id, str) else None,
        registry_id=str(registry["id"]),
        registry_digest=_row_digest(registry, (
            "id", "scope_kind", "scope_id", "tenant_id", "cabinet_id", "project_id",
            "project_status", "plugin_instance_id", "owner_state", "checkpoint",
            *_PROVENANCE_FIELDS,
        )),
        mapping_digest=_row_digest(mapping, (
            "id", "source_kind", "source_id", "schema_version", "source_payload_digest",
            "canonical_payload_digest", "scope_registry_id", "mapping_state", "checkpoint",
        )),
        parent_digests=tuple(parents),
    )


async def _cabinet_parent_digest(
    session: AsyncSession, *, context: BackfillContext, tenant_id: str, cabinet_id: str,
) -> str | PortRefusal:
    anchors = _rows(await session.execute(
        _SELECT_CABINET_ANCHOR_SQL, {"cabinet_id": cabinet_id, "tenant_id": tenant_id},
    ))
    grants = _rows(await session.execute(
        _SELECT_CABINET_GRANT_SQL, {"cabinet_id": cabinet_id, "tenant_id": tenant_id},
    ))
    cabinets = _rows(await session.execute(
        _SELECT_CABINET_ROW_SQL, {"cabinet_id": cabinet_id},
    ))
    active_grants = [row for row in grants if str(row.get("status") or "") == "active"]
    if (
        len(anchors) != 1 or not _provenance_ok(anchors[0])
        or len(active_grants) != 1
        or len(cabinets) != 1
        or str(cabinets[0].get("status") or "") != "active"
        or str(cabinets[0].get("company_id") or "") != tenant_id
    ):
        return _refusal(
            M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
            "the cabinet parent chain requires one active anchor, grant and company cabinet row",
            context=context, gate_name=GATE_ACTIVE_TYPED_PARENT_SCOPE,
        )
    return _digest({
        "hop": "cabinet",
        "anchor": _row_digest(anchors[0], (
            "id", "cabinet_id", "tenant_id", "tenant_type", "owner_state", *_PROVENANCE_FIELDS,
        )),
        "grant": _row_digest(active_grants[0], ("id", "cabinet_id", "company_id", "mode", "status")),
        "cabinet": _row_digest(cabinets[0], ("id", "company_id", "owner_scope", "status")),
    })


async def _project_parent_digest(
    session: AsyncSession, *, context: BackfillContext, tenant_id: str,
    cabinet_id: str, project_id: str,
) -> str | PortRefusal:
    anchors = _rows(await session.execute(
        _SELECT_PROJECT_ANCHOR_SQL,
        {"project_id": project_id, "tenant_id": tenant_id, "cabinet_id": cabinet_id},
    ))
    projects = _rows(await session.execute(
        _SELECT_PROJECT_ROW_SQL, {"project_id": project_id},
    ))
    if (
        len(anchors) != 1 or not _provenance_ok(anchors[0])
        or len(projects) != 1
        or str(projects[0].get("status") or "") != "active"
        or str(projects[0].get("company_id") or "") != tenant_id
        or str(projects[0].get("cabinet_id") or "") != cabinet_id
    ):
        return _refusal(
            M04EPortReason.M04_02_TYPED_SCOPE_UNPROVEN,
            "the project parent chain requires one active typed anchor and one active project row",
            context=context, gate_name=GATE_ACTIVE_TYPED_PARENT_SCOPE,
        )
    return _digest({
        "hop": "project",
        "anchor": _row_digest(anchors[0], (
            "id", "project_id", "tenant_id", "tenant_type", "cabinet_id", "project_status",
            "owner_state", *_PROVENANCE_FIELDS,
        )),
        "project": _row_digest(projects[0], ("id", "company_id", "cabinet_id", "status")),
    })


async def read_promotion_evidence(
    session: AsyncSession, *, context: BackfillContext,
) -> PromotionEvidence | PortRefusal:
    """Read the partition's latest durable M04E promotion record, exact key only.

    Empirical basis for the refusals below (revision ``2026110302`` model
    ``M04EDefinitionVersionPromotionEvidenceRow``):

    * ``uq_m04e_promotion_revision`` is ``(tenant_id, definition_id, revision_id)``,
      so this read can never observe another partition's record;
    * ``ck_m04e_promotion_outcome`` allows ``('blocked', 'quarantined')`` only, so a
      positive ``MIG-M04E-01`` promotion cannot be recorded in this schema. The gate
      therefore refuses by name instead of treating the foundation tables' existence
      as approval — that would make the gate satisfied by the very mapping it gates.
    """

    rows = _rows(await session.execute(
        _SELECT_PROMOTION_EVIDENCE_SQL,
        {"tenant_id": context.tenant_id, "definition_id": context.app_definition_id},
    ))
    if not rows:
        return _refusal(
            M04EPortReason.M04E_01_PROMOTION_ABSENT,
            "no durable M04E promotion record exists for this partition",
            context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
        )
    row = rows[0]
    outcome = str(row.get("outcome") or "")
    if outcome in _NEGATIVE_PROMOTION_OUTCOMES:
        return _refusal(
            M04EPortReason.M04E_01_PROMOTION_BLOCKED,
            "the latest durable M04E promotion record for this partition is negative; "
            "blocked/quarantined is the only outcome this schema can store, so no positive "
            "foundation gate can be proven until it is reviewed and superseded",
            context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            blockers=(
                f"m04e_promotion_outcome={outcome}",
                "m04e_promotion_positive_outcome_unrepresentable: "
                "ck_m04e_promotion_outcome = ('blocked', 'quarantined')",
            ),
        )
    if outcome != _POSITIVE_PROMOTION_OUTCOME:
        return _refusal(
            M04EPortReason.M04E_01_POSITIVE_OUTCOME_UNREPRESENTABLE,
            f"unrecognised durable promotion outcome {outcome!r}; only 'accepted' may prove the gate",
            context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
        )
    if (
        str(row.get("source_snapshot_id") or "") != context.source_snapshot_id
        or str(row.get("source_digest") or "") != context.source_snapshot_digest
    ):
        return _refusal(
            M04EPortReason.M04E_01_PROMOTION_LINEAGE_MISMATCH,
            "the durable promotion record is bound to a different source snapshot than the page",
            context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
        )
    return PromotionEvidence(
        tenant_id=str(row["tenant_id"]),
        definition_id=str(row["definition_id"]),
        revision_id=str(row["revision_id"]),
        outcome=outcome,
        source_snapshot_id=str(row["source_snapshot_id"]),
        source_digest=str(row["source_digest"]),
        target_digest=str(row["target_digest"]),
        gate_evidence_digest=_digest(dict(row.get("gate_evidence") or {})),
    )


# ---------------------------------------------------------------------------
# UpstreamGatePort
# ---------------------------------------------------------------------------


class SqlM04EUpstreamGate(_SessionReads):
    """Re-prove the M04/M04E upstream gates for one exact partition, per page.

    ``prove_partition`` is the port method: it returns
    ``UpstreamGateEvidence | None`` and therefore lets ``run_page`` take its reviewed
    durable-blocked path (``upstream_owner_scope_gate_unproven``) whenever a gate is
    unsatisfied. ``partition_gate`` returns the same decision *plus* the typed
    :class:`PortRefusal` that names the gate, so an operator (or the acceptance
    artifact) can see **which** gate failed without parsing a log line.

    Ordering matters: the definition row is read first because it names the typed
    scope; then ``MIG-M04-01`` (owner), ``MIG-M04-02`` +
    ``active_typed_parent_scope`` (typed scope and every parent hop), then
    ``MIG-M04E-01`` (durable promotion record).
    """

    async def prove_partition(self, *, context: BackfillContext) -> UpstreamGateEvidence | None:
        decision = await self.partition_gate(context=context)
        return decision.evidence

    async def partition_gate(self, *, context: BackfillContext) -> GateDecision:
        async with self._read(context) as session:
            definition = await self._definition_row(session, context)
            if definition is None:
                return GateDecision(None, _refusal(
                    M04EPortReason.M04E_01_DEFINITION_ABSENT,
                    "the partition's legacy definition row is absent for this tenant",
                    context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
                ))
            owner = await read_owner_evidence(
                session, context=context, tenant_id=context.tenant_id,
            )
            if isinstance(owner, PortRefusal):
                return GateDecision(None, owner)
            scope = await read_typed_scope_evidence(
                session, context=context, definition=definition,
            )
            if isinstance(scope, PortRefusal):
                return GateDecision(None, scope)
            promotion = await read_promotion_evidence(session, context=context)
            if isinstance(promotion, PortRefusal):
                return GateDecision(
                    None,
                    promotion,
                    probes=(
                        (GATE_M04_01_OWNER_REGISTRY, owner.digest()),
                        (GATE_M04_02_TYPED_SCOPE, scope.digest()),
                    ),
                )
            evidence = UpstreamGateEvidence(
                tenant_id=context.tenant_id,
                app_definition_id=context.app_definition_id,
                source_snapshot_digest=context.source_snapshot_digest,
                # Echoed from the trusted context: the backfill contract compares
                # these with the context and the requested transaction/correlation
                # binding is proven separately by the transaction-local context.
                transaction_id=context.transaction_id,
                correlation_id=context.correlation_id,
                m04_gate_digest=owner.digest(),
                m04e_gate_digest=promotion.digest(),
                active_scope_digest=scope.digest(),
            )
            if not evidence.valid:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.GATE_CONTEXT_INVALID,
                    "proven gate evidence is not shaped like a valid upstream gate record",
                    context=context,
                ))
            return GateDecision(
                evidence,
                None,
                probes=(
                    (GATE_M04_01_OWNER_REGISTRY, evidence.m04_gate_digest),
                    (GATE_M04_02_TYPED_SCOPE, evidence.active_scope_digest),
                    (GATE_M04E_01_DEFINITION_FOUNDATION, evidence.m04e_gate_digest),
                ),
            )


# ---------------------------------------------------------------------------
# Candidate proof
# ---------------------------------------------------------------------------

#: Domain separators for the per-candidate proof digests. They are plain constants
#: (not gate names) because these proofs are about one persisted row, not a gate.
PROOF_DOMAIN_SAME_DEFINITION: Final = "m04e.same_definition"
PROOF_DOMAIN_IMMUTABLE_ARTIFACT: Final = "m04e.immutable_artifact"
PROOF_DOMAIN_CURRENT_VERSION: Final = "m04e.current_version"
PROOF_DOMAIN_CURRENT_SAME_DEFINITION: Final = "m04e.current_same_definition"
PROOF_DOMAIN_CURRENT_IMMUTABLE_ARTIFACT: Final = "m04e.current_immutable_artifact"

_PROMOTABLE_LIFECYCLES: Final[frozenset[str]] = frozenset({"draft", "published"})


@dataclass(frozen=True, slots=True)
class CandidateProofDecision:
    """Either the per-candidate proof or the named reason it could not be established."""

    proof: CandidateProof | None
    refusal: PortRefusal | None

    def __bool__(self) -> bool:
        return self.proof is not None


class SqlM04ECandidateProof(_SessionReads):
    """Prove one candidate against trusted persistence, or return ``None``.

    ``prove_candidate`` returns ``CandidateProof | None``: ``None`` is the reviewed
    "no proof" answer and makes the backfill quarantine the row
    (``owner_scope_or_definition_artifact_proof_missing``). It is returned **only**
    for facts about the candidate itself (row missing, lifecycle not promotable,
    identity drift against an already-mapped target row, unusable current-version
    link, non-unique artifact keys).

    Partition-level failures raise :class:`M04EPortRefused` instead, because they are
    not evidence about a row: an unusable context, gate evidence from another
    lineage, an owner/typed-scope proof that no longer matches the page's
    ``active_scope_digest``, or a candidate whose source row changed between the
    page read and this call. Quarantining every row of a page because the partition
    moved would record a false finding and lose the reason.

    Everything is re-read from the database: the candidate is rebuilt from the exact
    legacy rows through the same builders the reader uses, and equality of the whole
    candidate (which includes the source digest) is required. A proposed digest is
    therefore never authoritative.
    """

    async def prove_candidate(
        self, *, context: BackfillContext, gates: UpstreamGateEvidence,
        candidate: EvidenceCandidate,
    ) -> CandidateProof | None:
        decision = await self.candidate_proof(
            context=context, gates=gates, candidate=candidate,
        )
        return decision.proof

    async def candidate_proof(
        self, *, context: BackfillContext, gates: UpstreamGateEvidence,
        candidate: EvidenceCandidate,
    ) -> CandidateProofDecision:
        self._require_binding(context, gates, candidate)
        async with self._read(context) as session:
            definition = await self._definition_row(session, context)
            if definition is None:
                return CandidateProofDecision(None, _refusal(
                    M04EPortReason.M04E_01_DEFINITION_ABSENT,
                    "the partition's legacy definition row is absent for this tenant",
                    context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
                ))
            version: Mapping[str, Any] | None = None
            if candidate.phase == "version":
                version = await self._legacy_version(
                    session, definition=definition, version_id=candidate.source_id,
                )
                if version is None:
                    return CandidateProofDecision(None, _refusal(
                        M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                        "the candidate's legacy version row is absent or ambiguous",
                        context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
                    ))
                rebuilt = version_candidate(context, version, definition)
            else:
                rebuilt = definition_candidate(context, definition)

            # The source identity must be exactly what the database still says. A
            # changed payload between page read and proof aborts the page.
            if rebuilt != candidate:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PROOF_SOURCE_CHANGED,
                    "the legacy source row changed between the page read and the proof",
                    context=context,
                ))

            owner = await read_owner_evidence(
                session, context=context, tenant_id=context.tenant_id,
            )
            if isinstance(owner, PortRefusal):
                raise M04EPortRefused(owner)
            scope = await read_typed_scope_evidence(
                session, context=context, definition=definition,
            )
            if isinstance(scope, PortRefusal):
                raise M04EPortRefused(scope)
            if scope.digest() != gates.active_scope_digest:
                raise M04EPortRefused(_refusal(
                    M04EPortReason.PROOF_ACTIVE_SCOPE_MISMATCH,
                    "the active typed scope changed since the page gate was proven",
                    context=context, gate_name=GATE_ACTIVE_TYPED_PARENT_SCOPE,
                ))

            definition_status = str(definition.get("status") or "")
            if definition_status not in _PROMOTABLE_LIFECYCLES:
                return CandidateProofDecision(None, _refusal(
                    M04EPortReason.M04E_01_DEFINITION_ABSENT,
                    "the legacy definition lifecycle is not draft/published",
                    context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
                ))

            # Already-mapped target rows must agree; a disagreement is identity drift
            # inside the partition, which no per-row proof can absorb.
            target_definition = _rows(await session.execute(
                _SELECT_TARGET_DEFINITION_SQL,
                {"tenant_id": context.tenant_id, "definition_id": context.app_definition_id},
            ))
            if target_definition and not self._target_definition_matches(
                target_definition[0], definition=definition, context=context,
            ):
                return CandidateProofDecision(None, _refusal(
                    M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                    "an already-mapped M04E definition row disagrees with the legacy identity",
                    context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
                ))
            if version is not None:
                target_version = _rows(await session.execute(
                    _SELECT_TARGET_VERSION_SQL,
                    {
                        "tenant_id": context.tenant_id,
                        "definition_id": context.app_definition_id,
                        "version_id": version["id"],
                    },
                ))
                if target_version and not self._target_version_matches(
                    target_version[0], version=version, context=context,
                ):
                    return CandidateProofDecision(None, _refusal(
                        M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                        "an already-mapped M04E version row disagrees with the legacy identity",
                        context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
                    ))

            current_proof = await self._current_version_proof(
                session, context=context, definition=definition,
                candidate=candidate, version=version,
            )
            if isinstance(current_proof, PortRefusal):
                return CandidateProofDecision(None, current_proof)

            source_material = {
                "source_kind": candidate.source_kind,
                "source_id": candidate.source_id,
                "source_digest": candidate.source_digest,
                "phase": candidate.phase,
                "tenant_id": context.tenant_id,
                "app_definition_id": context.app_definition_id,
            }
            owner_proof_digest = _digest({
                "gate": GATE_M04_01_OWNER_REGISTRY,
                "owner": owner.digest(),
                "source": source_material,
                "transaction_id": context.transaction_id,
                "correlation_id": context.correlation_id,
            })

            same_definition_digest: str | None = None
            artifact_immutability_digest: str | None = None
            if version is not None:
                key_counts = _rows(await session.execute(
                    _SELECT_VERSION_KEY_COUNTS_SQL,
                    {
                        "definition_id": definition["id"],
                        "manifest_version": version["manifest_version"],
                        "artifact_digest": version["artifact_digest"],
                    },
                ))
                manifest_version_count, artifact_digest_count = self._key_counts(key_counts)
                if manifest_version_count != 1 or artifact_digest_count != 1:
                    return CandidateProofDecision(None, _refusal(
                        M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                        "the version identity does not bind exactly one manifest version and "
                        "one artifact digest inside the definition",
                        context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
                    ))
                manifest = dict(version.get("manifest") or {})
                same_definition_digest = _digest({
                    "domain": PROOF_DOMAIN_SAME_DEFINITION,
                    "definition_id": definition["id"],
                    "definition_app_id": definition["app_id"],
                    "definition_tenant_id": definition["tenant_id"],
                    "version_id": version["id"],
                    "version_definition_id": version["app_definition_id"],
                    "manifest_app_id": manifest.get("appId"),
                    "manifest_version": version["manifest_version"],
                    "manifest_manifest_version": manifest.get("version"),
                })
                artifact_immutability_digest = _digest({
                    "domain": PROOF_DOMAIN_IMMUTABLE_ARTIFACT,
                    "version_id": version["id"],
                    "definition_id": definition["id"],
                    "manifest_version": version["manifest_version"],
                    "artifact_digest": version["artifact_digest"],
                    "artifact_uri": version["artifact_uri"],
                    "manifest_version_key_count": manifest_version_count,
                    "artifact_digest_key_count": artifact_digest_count,
                })
            assert current_proof is not None

            return CandidateProofDecision(CandidateProof(
                tenant_id=context.tenant_id,
                app_definition_id=context.app_definition_id,
                source_kind=candidate.source_kind,
                source_id=candidate.source_id,
                source_digest=candidate.source_digest,
                transaction_id=context.transaction_id,
                correlation_id=context.correlation_id,
                owner_proof_digest=owner_proof_digest,
                # Must equal the page gate's active scope digest exactly; the check
                # above guarantees it, so the two ports cannot drift apart.
                active_scope_proof_digest=scope.digest(),
                canonical_definition_id=context.app_definition_id,
                app_id=str(definition["app_id"]),
                app_version_id=str(version["id"]) if version is not None else None,
                manifest_version=(
                    str(version["manifest_version"]) if version is not None else None
                ),
                artifact_uri=(
                    str(version["artifact_uri"])
                    if version is not None and version.get("artifact_uri") else None
                ),
                same_definition_proof_digest=same_definition_digest,
                artifact_digest=(
                    str(version["artifact_digest"]) if version is not None else None
                ),
                artifact_immutability_proof_digest=artifact_immutability_digest,
                **current_proof,
            ), None)

    # -- helpers ----------------------------------------------------------

    async def _legacy_version(
        self, session: AsyncSession, *, definition: Mapping[str, Any], version_id: str,
    ) -> Mapping[str, Any] | None:
        rows = _rows(await session.execute(
            _SELECT_LEGACY_VERSION_SQL,
            {"definition_id": definition["id"], "version_id": version_id},
        ))
        if len(rows) != 1:
            return None
        row = rows[0]
        if str(row["app_definition_id"]) != str(definition["id"]):
            return None
        return row

    @staticmethod
    def _key_counts(rows: tuple[Mapping[str, Any], ...]) -> tuple[int, int]:
        if not rows:
            return 0, 0
        row = rows[0]
        return int(row.get("manifest_version_count") or 0), int(
            row.get("artifact_digest_count") or 0
        )

    @staticmethod
    def _target_definition_matches(
        target: Mapping[str, Any], *, definition: Mapping[str, Any],
        context: BackfillContext,
    ) -> bool:
        return bool(
            str(target.get("tenant_id")) == context.tenant_id
            and str(target.get("id")) == context.app_definition_id
            and str(target.get("app_id")) == str(definition["app_id"])
            and str(target.get("scope_kind")) == str(definition["scope_kind"])
            and str(target.get("scope_id")) == str(definition["scope_id"])
            and str(target.get("source_snapshot_id")) == context.source_snapshot_id
            and _is_digest(target.get("source_digest"))
            and str(target.get("lifecycle") or "") in _PROMOTABLE_LIFECYCLES
        )

    @staticmethod
    def _target_version_matches(
        target: Mapping[str, Any], *, version: Mapping[str, Any], context: BackfillContext,
    ) -> bool:
        return bool(
            str(target.get("tenant_id")) == context.tenant_id
            and str(target.get("definition_id")) == context.app_definition_id
            and str(target.get("id")) == str(version["id"])
            and str(target.get("manifest_version")) == str(version["manifest_version"])
            and str(target.get("artifact_digest")) == str(version["artifact_digest"])
            and str(target.get("source_snapshot_id")) == context.source_snapshot_id
            and _is_digest(target.get("source_digest"))
            and str(target.get("lifecycle") or "") in _PROMOTABLE_LIFECYCLES
        )

    async def _current_version_proof(
        self, session: AsyncSession, *, context: BackfillContext,
        definition: Mapping[str, Any], candidate: EvidenceCandidate,
        version: Mapping[str, Any] | None,
    ) -> dict[str, Any] | PortRefusal:
        """Prove the definition's current-version link exactly as the contract reads it.

        ``DefinitionVersionEvidenceBackfill._valid_candidate_proof`` requires a
        *draft* version candidate to report **no** current version, and a published
        one to report the definition's current pointer with its lifecycle, artifact
        identity and same-definition/immutability digests.
        """

        if candidate.phase == "version" and str(candidate.payload.get("status")) != "published":
            return {
                "current_version_id": None,
                "current_version_definition_id": None,
                "current_version_manifest_version": None,
                "current_version_artifact_digest": None,
                "current_version_artifact_uri": None,
                "current_version_lifecycle": None,
                "current_version_match_count": 0,
                "current_version_proof_digest": None,
                "current_version_same_definition_digest": None,
                "current_version_immutable_artifact_digest": None,
            }
        current_id = definition.get("current_version_id")
        if current_id is None or (isinstance(current_id, str) and not current_id.strip()):
            if candidate.phase == "definition" and str(candidate.payload.get("status")) != "published":
                return {
                    "current_version_id": None,
                    "current_version_definition_id": None,
                    "current_version_manifest_version": None,
                    "current_version_artifact_digest": None,
                    "current_version_artifact_uri": None,
                    "current_version_lifecycle": None,
                    "current_version_match_count": 0,
                    "current_version_proof_digest": None,
                    "current_version_same_definition_digest": None,
                    "current_version_immutable_artifact_digest": None,
                }
            return _refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "the definition names no current version, so the required current-version "
                "proof cannot be established",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            )

        rows = _rows(await session.execute(
            _SELECT_LEGACY_VERSION_SQL,
            {"definition_id": definition["id"], "version_id": str(current_id)},
        ))
        if len(rows) != 1:
            return _refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "the definition's current version is absent or ambiguous inside the definition",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            )
        current = rows[0]
        current_lifecycle = str(current.get("status") or "")
        artifact_uri = current.get("artifact_uri")
        artifact_digest = current.get("artifact_digest")
        if (
            current_lifecycle not in _PROMOTABLE_LIFECYCLES
            or not isinstance(artifact_uri, str) or not artifact_uri.strip()
            or not _is_digest(artifact_digest)
        ):
            return _refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "the current version has no promotable lifecycle or no bound artifact identity",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            )
        candidate_status = str(candidate.payload.get("status") or "")
        if candidate.phase == "version" and candidate_status == "published" and (
            current_lifecycle != "published"
        ):
            return _refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "a published version candidate requires a published current version",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            )
        manifest = dict(current.get("manifest") or {})
        return {
            "current_version_id": str(current["id"]),
            "current_version_definition_id": str(current["app_definition_id"]),
            "current_version_manifest_version": str(current["manifest_version"]),
            "current_version_artifact_digest": str(artifact_digest),
            "current_version_artifact_uri": str(artifact_uri),
            "current_version_lifecycle": current_lifecycle,
            "current_version_match_count": 1,
            "current_version_proof_digest": _digest({
                "domain": PROOF_DOMAIN_CURRENT_VERSION,
                "definition_id": definition["id"],
                "current_version_id": current["id"],
                "current_version_definition_id": current["app_definition_id"],
                "current_version_manifest_version": current["manifest_version"],
                "current_version_lifecycle": current_lifecycle,
            }),
            "current_version_same_definition_digest": _digest({
                "domain": PROOF_DOMAIN_CURRENT_SAME_DEFINITION,
                "definition_id": definition["id"],
                "definition_app_id": definition["app_id"],
                "current_version_id": current["id"],
                "current_version_definition_id": current["app_definition_id"],
                "manifest_app_id": manifest.get("appId"),
                "manifest_version": current["manifest_version"],
                "manifest_manifest_version": manifest.get("version"),
            }),
            "current_version_immutable_artifact_digest": _digest({
                "domain": PROOF_DOMAIN_CURRENT_IMMUTABLE_ARTIFACT,
                "current_version_id": current["id"],
                "definition_id": definition["id"],
                "manifest_version": current["manifest_version"],
                "artifact_digest": artifact_digest,
                "artifact_uri": artifact_uri,
            }),
        }

    @staticmethod
    def _require_binding(
        context: BackfillContext, gates: UpstreamGateEvidence, candidate: EvidenceCandidate,
    ) -> None:
        if not isinstance(context, BackfillContext) or not context.usable:
            raise M04EPortRefused(_refusal(
                M04EPortReason.PROOF_CONTEXT_INVALID,
                "the candidate proof requires a fully usable backfill context",
            ))
        if not isinstance(candidate, EvidenceCandidate):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "the candidate proof requires an EvidenceCandidate",
                context=context,
            ))
        if (candidate.tenant_id, candidate.app_definition_id) != (
            context.tenant_id, context.app_definition_id,
        ):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "the candidate belongs to another tenant/definition partition",
                context=context,
            ))
        if candidate.source_kind != SOURCE_KIND_BY_PHASE.get(candidate.phase):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "the candidate's phase and source kind disagree",
                context=context,
            ))
        if (
            candidate.schema_version != context.source_schema_version
            or candidate.canonical_schema_version != context.canonical_schema_version
        ):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PROOF_CANDIDATE_BINDING_INVALID,
                "the candidate is outside the context's source/canonical schema lineage",
                context=context,
            ))
        if (
            not isinstance(gates, UpstreamGateEvidence)
            or not gates.valid
            or gates.tenant_id != context.tenant_id
            or gates.app_definition_id != context.app_definition_id
            or gates.source_snapshot_digest != context.source_snapshot_digest
            or gates.transaction_id != context.transaction_id
            or gates.correlation_id != context.correlation_id
        ):
            raise M04EPortRefused(_refusal(
                M04EPortReason.PROOF_GATES_INVALID,
                "the gate evidence is absent, malformed or bound to another lineage",
                context=context, gate_name=GATE_M04E_01_DEFINITION_FOUNDATION,
            ))


# ---------------------------------------------------------------------------
# EvidenceStore boundary — refuses, with the reviewed schema gaps
# ---------------------------------------------------------------------------

EVIDENCE_STORE_SCHEMA_GAPS: Final[tuple[str, ...]] = (
    "m04e_backfill_mapping_ledger_missing: no registered table carries the M04E "
    "per-source mapping key (tenant_id, app_definition_id, source_kind, source_id, "
    "source_schema_version) with the first source and canonical digest; "
    "m04e_dynamic_app_definitions/m04e_dynamic_app_versions have no source_kind, "
    "source_schema_version, reason_code, correlation_id or checkpoint column",
    "m04e_backfill_observation_ledger_missing: no registered table carries an "
    "observed_source_digest / observed_artifact_digest pair, so a changed-digest "
    "conflict cannot be replayed exactly (find_observation has no storage)",
    "m04e_backfill_checkpoint_ledger_missing: no registered table carries the durable "
    "cursor (phase, after_source_id, sequence, phase_complete) or a CAS token for the "
    "M04E definition/version stream, so classify_and_commit_page cannot advance a "
    "cursor atomically with the evidence",
    "m04e_backfill_audit_ledger_missing: no registered table carries redacted per-page "
    "audit evidence (event_kind, reason_code, correlation_id, checkpoint, actor)",
    "m04e_promotion_evidence_is_per_partition: uq_m04e_promotion_revision is "
    "(tenant_id, definition_id, revision_id); it records one promotion revision for a "
    "partition, not one row per mapped source identity",
    "m04e_promotion_accepted_outcome_unrepresentable: ck_m04e_promotion_outcome permits "
    "only ('blocked','quarantined'), so the only registered M04E evidence table cannot "
    "record an ACCEPTED mapping",
    "m04e_promotion_blocked_digests_unrepresentable: source_digest, target_digest, "
    "current_version_resolution_digest and artifact_equality_digest are NOT NULL "
    "sha256 gate proofs; a blocked page has no truthful value for them and inventing "
    "one would fabricate gate evidence",
)

EVIDENCE_STORE_REQUIRED_DDL: Final[tuple[str, ...]] = (
    "m04e_definition_version_mappings (tenant_id, definition_id, source_kind, source_id, "
    "source_schema_version, source_digest, canonical_digest, canonical_identity, phase, "
    "status, reason_code, proof_evidence, artifact_digest, verified_artifact_digest, "
    "checkpoint, correlation_id) with uq (tenant, definition, source_kind, source_id, "
    "source_schema_version)",
    "m04e_definition_version_quarantine (…, observed_source_digest, "
    "observed_artifact_digest, reason_code, blocker_evidence, checkpoint) with "
    "uq (tenant, definition, source_kind, source_id, source_schema_version, "
    "observed_source_digest, observed_artifact_digest)",
    "m04e_definition_version_checkpoints (tenant_id, definition_id, phase, "
    "after_source_id, sequence, phase_complete, source_snapshot_id, "
    "source_schema_version, source_snapshot_digest, canonical_schema_version, "
    "writer_generation, checkpoint_token) read FOR UPDATE and advanced by CAS",
    "m04e_definition_version_audit (append-only redacted event stream, trusted writer "
    "privileges, UPDATE/DELETE denied)",
)


class SqlM04EEvidenceStore(_SessionReads):
    """Fail-closed ``EvidenceStore`` boundary: it reads exact keys and never writes.

    See the module docstring for why no truthful implementation exists over the
    three registered M04E tables. Every mutating/reading method of the port raises
    :class:`EvidenceStoreUnavailable` with the gap that blocks it, so a host that
    wires this object cannot believe a page was committed, and
    ``DefinitionVersionEvidenceBackfill.run_page`` aborts with the partition's
    checkpoint untouched.

    The one exact-key read the schema *does* support —
    :meth:`latest_promotion_evidence` — is implemented: it returns the partition's
    newest durable promotion record, or ``None`` when the partition has none, and it
    can never observe another partition's row.
    """

    IS_FAIL_CLOSED_BOUNDARY: Final = True

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        require_serializable: bool = False,
    ) -> None:
        super().__init__(
            session_factory=session_factory, require_serializable=require_serializable,
        )

    @property
    def blockers(self) -> tuple[str, ...]:
        """Named reasons this boundary cannot be a production ``EvidenceStore``."""

        return EVIDENCE_STORE_SCHEMA_GAPS

    @property
    def required_ddl(self) -> tuple[str, ...]:
        return EVIDENCE_STORE_REQUIRED_DDL

    async def latest_promotion_evidence(
        self, *, context: BackfillContext,
    ) -> PromotionEvidence | None:
        """Exact-key read of the partition's newest durable promotion record."""

        async with self._read(context) as session:
            rows = _rows(await session.execute(
                _SELECT_PROMOTION_EVIDENCE_SQL,
                {
                    "tenant_id": context.tenant_id,
                    "definition_id": context.app_definition_id,
                },
            ))
        if not rows:
            return None
        row = rows[0]
        if (
            str(row.get("tenant_id")) != context.tenant_id
            or str(row.get("definition_id")) != context.app_definition_id
        ):
            # Unreachable through the exact-key predicate; a policy/regression guard.
            raise M04EPortRefused(_refusal(
                M04EPortReason.PARTITION_MISMATCH,
                "durable promotion evidence belongs to another partition",
                context=context,
            ))
        return PromotionEvidence(
            tenant_id=str(row["tenant_id"]),
            definition_id=str(row["definition_id"]),
            revision_id=str(row["revision_id"]),
            outcome=str(row.get("outcome") or ""),
            source_snapshot_id=str(row.get("source_snapshot_id") or ""),
            source_digest=str(row.get("source_digest") or ""),
            target_digest=str(row.get("target_digest") or ""),
            gate_evidence_digest=_digest(dict(row.get("gate_evidence") or {})),
        )

    async def find_existing(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str,
    ) -> BackfillRecord | None:
        raise self._unavailable(
            context,
            M04EPortReason.STORE_MAPPING_LEDGER_MISSING,
            "no M04E per-source mapping ledger exists; returning None would license a "
            "second mapping for an already-mapped source identity",
        )

    async def find_observation(
        self, *, context: BackfillContext, source_kind: str, source_id: str,
        source_schema_version: str, observed_source_digest: str | None,
        observed_artifact_digest: str | None,
    ) -> BackfillRecord | None:
        raise self._unavailable(
            context,
            M04EPortReason.STORE_OBSERVATION_LEDGER_MISSING,
            "no M04E changed-digest observation ledger exists; a conflict replay cannot "
            "be looked up exactly",
        )

    async def record_blocked(
        self, *, context: BackfillContext, checkpoint: BackfillCheckpoint,
        reason_code: str, blocker_evidence: Sequence[str] = (),
    ) -> BackfillRecord:
        raise self._unavailable(
            context,
            M04EPortReason.STORE_BLOCKED_EVIDENCE_UNREPRESENTABLE,
            "the only registered M04E evidence table constrains outcome to "
            "('blocked','quarantined') but requires four NOT NULL sha256 gate digests that "
            "a blocked page cannot truthfully supply",
        )

    async def classify_and_commit_page(
        self, *, context: BackfillContext, gates: UpstreamGateEvidence,
        expected_checkpoint: BackfillCheckpoint, decisions: Sequence[CandidateDecision],
        page_exhausted: bool,
    ) -> BackfillBatch:
        accepted = any(
            isinstance(decision, CandidateDecision)
            and decision.status is EvidenceStatus.ACCEPTED
            for decision in decisions
        )
        raise self._unavailable(
            context,
            M04EPortReason.STORE_ACCEPTED_OUTCOME_UNREPRESENTABLE if accepted else
            M04EPortReason.STORE_CHECKPOINT_LEDGER_MISSING,
            "no durable M04E cursor/CAS or per-source ledger exists, so a page cannot "
            "commit its mappings, quarantine, redacted audit and checkpoint atomically"
            + (
                "; additionally ck_m04e_promotion_outcome cannot record an ACCEPTED mapping"
                if accepted else ""
            ),
        )

    @staticmethod
    def _unavailable(
        context: BackfillContext, code: M04EPortReason, detail: str,
    ) -> EvidenceStoreUnavailable:
        return EvidenceStoreUnavailable(PortRefusal(
            code=code,
            detail=detail,
            partition=(
                (context.tenant_id, context.app_definition_id)
                if isinstance(context, BackfillContext) else None
            ),
            correlation_id=(
                context.correlation_id if isinstance(context, BackfillContext) else None
            ),
            blockers=EVIDENCE_STORE_SCHEMA_GAPS,
        ))


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

#: Blockers a composition root must keep until DDL-backed stores are reviewed.
M04E_PORT_WIRING_BLOCKERS: Final[tuple[str, ...]] = (
    "m04e_evidence_store_port_unimplemented: "
    + "; ".join(EVIDENCE_STORE_SCHEMA_GAPS),
    "m04e_backfill_tenant_context_binding_unreviewed: the adapters apply and read back "
    "the transaction-local tenant GUCs and set app.migration_role='true'; the database "
    "GRANT/append-only privilege review for that role is still open",
    "m04e_positive_promotion_gate_unrepresentable: ck_m04e_promotion_outcome admits only "
    "('blocked','quarantined'), so SqlM04EUpstreamGate refuses by name until a reviewed "
    "revision can record an accepted M04E foundation promotion",
)


@dataclass(frozen=True, slots=True)
class M04ESqlPorts:
    """The three M04E ports that are implemented, ready for the composition root.

    The evidence store is deliberately absent: compose
    :func:`build_m04e_evidence_store` and honour its blocker instead of substituting
    an in-memory store.
    """

    reader: CandidateReader
    gates: UpstreamGatePort
    proofs: CandidateProofPort
    page_size_limit: int
    blockers: tuple[str, ...]


def build_m04e_sql_ports(
    *, session_factory: async_sessionmaker[AsyncSession] | None,
    page_size_limit: int = MAX_PAGE_SIZE, require_serializable: bool = False,
) -> tuple[M04ESqlPorts | None, tuple[str, ...]]:
    """Build the reader/gate/proof adapters, or explain why they cannot be built."""

    if session_factory is None:
        return None, (
            "m04e_backfill_session_factory_missing: the M04E ports read PostgreSQL "
            "through an async_sessionmaker; there is no in-memory candidate source",
        )
    if not isinstance(page_size_limit, int) or not 1 <= page_size_limit <= MAX_PAGE_SIZE:
        return None, (
            f"m04e_page_size_invalid: page_size_limit {page_size_limit!r} is outside 1..{MAX_PAGE_SIZE}",
        )
    return (
        M04ESqlPorts(
            reader=SqlM04ECandidateReader(
                session_factory=session_factory,
                page_size_limit=page_size_limit,
                require_serializable=require_serializable,
            ),
            gates=SqlM04EUpstreamGate(
                session_factory=session_factory,
                require_serializable=require_serializable,
            ),
            proofs=SqlM04ECandidateProof(
                session_factory=session_factory,
                require_serializable=require_serializable,
            ),
            page_size_limit=page_size_limit,
            blockers=M04E_PORT_WIRING_BLOCKERS,
        ),
        (),
    )


def build_m04e_evidence_store(
    *, session_factory: async_sessionmaker[AsyncSession] | None,
) -> tuple[EvidenceStore | None, tuple[str, ...]]:
    """Return the durable evidence store, or ``(None, blockers)``.

    Shaped like ``composition.build_m06_durable_evidence_ports`` so the composition
    root reports a named blocker rather than pretending the stage is wired.
    """

    if session_factory is None:
        return None, (
            "m04e_backfill_session_factory_missing: the evidence boundary reads "
            "PostgreSQL; there is no in-memory store on the production path",
            *EVIDENCE_STORE_SCHEMA_GAPS,
        )
    boundary = SqlM04EEvidenceStore(session_factory=session_factory)
    return None, boundary.blockers


__all__ = [
    "CABINET_ANCHOR_TABLE",
    "CABINET_GRANT_TABLE",
    "CABINET_TABLE",
    "COMPANY_TABLE",
    "CONTEXT_ROLE",
    "CandidateProofDecision",
    "EVIDENCE_STORE_REQUIRED_DDL",
    "EVIDENCE_STORE_SCHEMA_GAPS",
    "EvidenceStoreUnavailable",
    "GATE_ACTIVE_TYPED_PARENT_SCOPE",
    "GATE_M04E_01_DEFINITION_FOUNDATION",
    "GATE_M04_01_OWNER_REGISTRY",
    "GATE_M04_02_TYPED_SCOPE",
    "GateDecision",
    "LEGACY_DEFINITION_SOURCE_KIND",
    "LEGACY_DEFINITION_TABLE",
    "LEGACY_VERSION_SOURCE_KIND",
    "LEGACY_VERSION_TABLE",
    "M04EPortReason",
    "M04EPortRefused",
    "M04ESqlPorts",
    "M04E_PORT_WIRING_BLOCKERS",
    "MAPPING_TABLE",
    "MAX_PAGE_SIZE",
    "OwnerEvidence",
    "PROMOTION_EVIDENCE_TABLE",
    "PROJECT_ANCHOR_TABLE",
    "PROJECT_TABLE",
    "PortRefusal",
    "PromotionEvidence",
    "REGISTRY_TABLE",
    "SOURCE_KIND_BY_PHASE",
    "SqlM04ECandidateProof",
    "SqlM04ECandidateReader",
    "SqlM04EEvidenceStore",
    "SqlM04EUpstreamGate",
    "TARGET_DEFINITION_TABLE",
    "TARGET_VERSION_TABLE",
    "TENANT_ANCHOR_TABLE",
    "TypedScopeEvidence",
    "bind_m04e_transaction_context",
    "build_m04e_evidence_store",
    "build_m04e_sql_ports",
    "definition_candidate",
    "legacy_status_to_state",
    "read_owner_evidence",
    "read_promotion_evidence",
    "read_typed_scope_evidence",
    "require_partition",
    "require_usable_context",
    "version_candidate",
]
