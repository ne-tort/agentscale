"""Fixture-free helpers for the M04-02 scope-evidence writer artefacts.

Nothing here touches a database. Two ideas carry the whole package:

``write(...)``
    Builds a real :class:`~prodavan.application.migration.m04_scope_evidence_writer.
    ScopeEvidenceWrite` from the M04-02 contracts (``ScopeAnchors``, the transaction-bound
    ``ScopeResolutionEvidence`` bundle, a ``BackfillCheckpoint``), with per-case overrides.
    The default case is a *complete* accepted tenant-scope resolution, so every negative case
    is a single named override rather than a parallel fake.

``FakeSession`` / ``FakeSessionFactory``
    A statement-level stand-in for the one ORM ``INSERT`` and the two ``select`` loads the
    writer issues. It applies the real ``(tenant_id, scope_kind, scope_id, revision_id)``
    uniqueness, materialises rows on ``flush`` and answers ``bind_evidence_transaction``'s
    ``set_config``/``current_setting`` pair, so ``append`` can be driven end to end without a
    database. It is not a database emulator: the predicates are taken from the compiled
    statement, which is why a test can also assert the writer never widens past the bound
    tenant.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import IntegrityError

from prodavan.application.migration.evidence_ports import TrustedEvidenceScope
from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillCheckpoint,
    ScopeActorMembershipEvidence,
    ScopeParentEvidence,
    ScopeResolution,
    ScopeResolutionEvidence,
    ScopeResolutionResult,
    ScopeRlsEvidence,
)
from prodavan.application.migration.m04_scope_evidence_writer import (
    SqlM04ScopeEvidenceWriter,
    ScopeEvidenceWrite,
    scope_evidence_row_values,
)
from prodavan.application.scope_registry.contracts import (
    OwnerState,
    ParentCompatibilityProof,
    ScopeAnchors,
    ScopeKind,
    ScopeProvenance,
    ScopeRegistryEntry,
)
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
)

TENANT = "tenant-m04-writer"
OTHER_TENANT = "tenant-m04-writer-other"
CABINET = "cabinet-m04-writer"
PROJECT = "project-m04-writer"
PLUGIN_INSTANCE = "instance-m04-writer"
REGISTRY_ID = "scope_registry_writer_1"
ACTOR = "actor-m04-writer"
OTHER_ACTOR = "actor-m04-writer-other"
CORRELATION = "corr-m04-writer"
TRANSACTION = "tx-m04-writer"
OTHER_TRANSACTION = "tx-m04-writer-other"
SNAPSHOT_ID = "snapshot-m04-writer-1"
SOURCE_SCHEMA = "scope-legacy.v1"
CANONICAL_SCHEMA = "scope-registry.v1"
WRITER_GENERATION = 3
NOW_TEXT = "2026-11-04T00:00:00+00:00"

#: Payload-shaped markers. A stored column or envelope key carrying one of these would be a
#: redaction bug, so the artefacts assert their absence rather than trusting the writer.
SECRET_MARKER = "sk-live-SECRET-must-never-be-persisted"
PAYLOAD_MARKER = "raw-source-payload"


def digest_of(byte: str) -> str:
    return "sha256:" + byte * 64


SNAPSHOT_DIGEST = digest_of("a")
SOURCE_DIGEST = digest_of("b")
CHECKPOINT_SNAPSHOT_DIGEST = digest_of("c")


# ---------------------------------------------------------------------------
# Typed scope shapes
# ---------------------------------------------------------------------------


def tenant_anchors() -> ScopeAnchors:
    return ScopeAnchors(scope_kind=ScopeKind.TENANT, scope_id=TENANT, tenant_id=TENANT)


def cabinet_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.CABINET,
        scope_id=CABINET,
        tenant_id=TENANT,
        cabinet_id=CABINET,
    )


def project_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.PROJECT,
        scope_id=PROJECT,
        tenant_id=TENANT,
        cabinet_id=CABINET,
        project_id=PROJECT,
    )


def plugin_instance_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.PLUGIN_INSTANCE,
        scope_id=PLUGIN_INSTANCE,
        tenant_id=TENANT,
        plugin_instance_id=PLUGIN_INSTANCE,
    )


ANCHOR_BUILDERS = {
    "tenant": tenant_anchors,
    "cabinet": cabinet_anchors,
    "project": project_anchors,
}


# ---------------------------------------------------------------------------
# Accepted M04-02 evidence bundle
# ---------------------------------------------------------------------------


def registry_entry(anchors: ScopeAnchors, *, owner_state: OwnerState = OwnerState.ACTIVE) -> ScopeRegistryEntry:
    return ScopeRegistryEntry(
        anchors=anchors,
        owner_state=owner_state,
        provenance=ScopeProvenance(
            source_kind="legacy_scope",
            source_id="legacy-scope-1",
            schema_version=SOURCE_SCHEMA,
            payload_digest=SOURCE_DIGEST,
        ),
    )


def parent_evidence(
    anchors: ScopeAnchors, *, transaction_id: str = TRANSACTION
) -> ScopeParentEvidence:
    """Parent cardinality proof that is exactly valid for ``anchors``' own scope kind."""
    values: dict[str, Any] = {
        "tenant_matches": 1,
        "cabinet_matches": 0,
        "active_cabinet_memberships": 0,
        "project_matches": 0,
        "active_registry_matches": 1,
        "transaction_id": transaction_id,
        "tenant_id": anchors.tenant_id,
        "active_registry_anchors": anchors,
    }
    if anchors.scope_kind in {ScopeKind.CABINET, ScopeKind.PROJECT}:
        values.update(
            cabinet_matches=1,
            active_cabinet_memberships=1,
            cabinet_id=anchors.cabinet_id,
            cabinet_membership_kind="cabinet_company_grant",
            membership_tenant_id=anchors.tenant_id,
            membership_cabinet_id=anchors.cabinet_id,
        )
    if anchors.scope_kind is ScopeKind.PROJECT:
        values.update(
            project_matches=1,
            project_id=anchors.project_id,
            project_tenant_id=anchors.tenant_id,
            project_cabinet_id=anchors.cabinet_id,
        )
    return ScopeParentEvidence(**values)


def parent_proof(
    anchors: ScopeAnchors | None = None, *, valid: bool = True
) -> ParentCompatibilityProof:
    """Compatibility proof that is exactly valid for ``anchors``' own scope kind.

    ``ParentCompatibilityProof.valid_for_scope`` requires the cabinet chain for a cabinet
    scope and the project chain for a project scope, so a tenant-only proof would make a
    cabinet/project acceptance case fail for the wrong reason.
    """
    anchors = anchors or tenant_anchors()
    flags = {
        "tenant_exists": valid,
        "tenant_retained": valid,
        "cabinet_exists": valid and anchors.cabinet_id is not None,
        "cabinet_tenant_compatible": valid and anchors.cabinet_id is not None,
        "cabinet_membership_active": valid and anchors.cabinet_id is not None,
        "project_exists": valid and anchors.project_id is not None,
        "project_tenant_compatible": valid and anchors.project_id is not None,
        "project_cabinet_compatible": valid and anchors.project_id is not None,
        "project_policy_eligible": valid and anchors.project_id is not None,
    }
    return ParentCompatibilityProof(**flags)


def rls_evidence(
    anchors: ScopeAnchors,
    *,
    transaction_id: str = TRANSACTION,
    fully_enforced: bool = True,
) -> ScopeRlsEvidence:
    return ScopeRlsEvidence(
        transaction_id=transaction_id,
        tenant_id=anchors.tenant_id,
        cabinet_id=anchors.cabinet_id,
        project_id=anchors.project_id,
        row_security_enabled=True,
        row_security_forced=True,
        database_role="prodavan_migration_writer",
        security_review_id="rls-registry:2026110307.1",
        policy_names=("scope_registry_tenant_all", "scope_registry_trusted_write"),
        tenant_policy_enforced=fully_enforced,
        using_policy_enforced=fully_enforced,
        with_check_policy_enforced=fully_enforced,
        missing_context_denies=fully_enforced,
        role_not_bypass_rls=fully_enforced,
        typed_parent_policy_enforced=fully_enforced,
    )


def actor_evidence(
    anchors: ScopeAnchors,
    *,
    actor_id: str = ACTOR,
    transaction_id: str = TRANSACTION,
    active: bool = True,
    relationship_kind: str = "employee_membership",
    relationship_matches: int = 1,
) -> ScopeActorMembershipEvidence:
    return ScopeActorMembershipEvidence(
        transaction_id=transaction_id,
        actor_id=actor_id,
        tenant_id=anchors.tenant_id,
        relationship_kind=relationship_kind,
        relationship_matches=relationship_matches,
        active=active,
    )


def resolution_evidence(
    anchors: ScopeAnchors,
    *,
    entry: ScopeRegistryEntry | None = None,
    parent: ScopeParentEvidence | None = None,
    rls: ScopeRlsEvidence | None = None,
    actor: ScopeActorMembershipEvidence | None = None,
    proof: ParentCompatibilityProof | None = None,
    parent_transaction_id: str | None = None,
) -> ScopeResolutionEvidence:
    return ScopeResolutionEvidence(
        entry=entry or registry_entry(anchors),
        parent_proof=proof or parent_proof(anchors),
        parent_compatibility_transaction_id=parent_transaction_id or TRANSACTION,
        parent_evidence=parent or parent_evidence(anchors),
        rls_evidence=rls or rls_evidence(anchors),
        actor_membership_evidence=actor or actor_evidence(anchors),
    )


def active_resolution(
    anchors: ScopeAnchors, *, entry: ScopeRegistryEntry | None = None
) -> ScopeResolutionResult:
    return ScopeResolutionResult(
        outcome=ScopeResolution.ACTIVE,
        entry=entry or registry_entry(anchors),
        reason_code="active_typed_scope",
        correlation_id=CORRELATION,
    )


def refusal_resolution(
    *,
    outcome: ScopeResolution = ScopeResolution.DENY,
    reason_code: str = "scope_missing_or_not_unique_active",
    correlation_id: str = CORRELATION,
    entry: ScopeRegistryEntry | None = None,
) -> ScopeResolutionResult:
    return ScopeResolutionResult(
        outcome=outcome,
        entry=entry,
        reason_code=reason_code,
        correlation_id=correlation_id,
    )


def checkpoint(
    *,
    tenant_id: str = TENANT,
    writer_generation: int = WRITER_GENERATION,
    sequence: int = 7,
    after_source_id: str | None = "legacy-scope-7",
    source_snapshot_digest: str = CHECKPOINT_SNAPSHOT_DIGEST,
) -> BackfillCheckpoint:
    return BackfillCheckpoint(
        source_snapshot_id=SNAPSHOT_ID,
        source_schema_version=SOURCE_SCHEMA,
        after_source_id=after_source_id,
        sequence=sequence,
        source_snapshot_digest=source_snapshot_digest,
        tenant_id=tenant_id,
        writer_generation=writer_generation,
        stream_kind="scope_registry",
        canonical_schema_version=CANONICAL_SCHEMA,
    )


def write(anchors: ScopeAnchors | None = None, **overrides: Any) -> ScopeEvidenceWrite:
    """A complete accepted tenant-scope write; every override is a named negative case."""
    resolved_anchors = anchors or tenant_anchors()
    values: dict[str, Any] = {
        "anchors": resolved_anchors,
        "resolution": active_resolution(resolved_anchors),
        "checkpoint": checkpoint(),
        "scope_registry_id": REGISTRY_ID,
        "writer_generation": WRITER_GENERATION,
        "correlation_id": CORRELATION,
        "evidence": resolution_evidence(resolved_anchors),
        "actor_id": ACTOR,
        "expected_canonical_digest": None,
        "revision_id": None,
        "previous_revision_id": None,
        "recorded_at": None,
    }
    values.update(overrides)
    return ScopeEvidenceWrite(**values)


def refused_write(
    *, outcome: ScopeResolution = ScopeResolution.DENY, reason_code: str = "scope_missing_or_not_unique_active"
) -> ScopeEvidenceWrite:
    return write(
        resolution=refusal_resolution(outcome=outcome, reason_code=reason_code),
        evidence=None,
        actor_id=None,
    )


def trusted_scope(**overrides: Any) -> TrustedEvidenceScope:
    values: dict[str, Any] = {"tenant_id": TENANT, "writer_generation": WRITER_GENERATION}
    values.update(overrides)
    return TrustedEvidenceScope(**values)


def scope_row_values_for(shape: str = "tenant", **overrides: Any) -> dict[str, Any]:
    """Column mapping the pure builder produces, for one typed scope shape."""
    return scope_evidence_row_values(
        write(ANCHOR_BUILDERS[shape](), **overrides), scope=trusted_scope()
    )


# ---------------------------------------------------------------------------
# Session stand-ins
# ---------------------------------------------------------------------------


class ExplodingSessionFactory:
    """A session factory that fails the test if the writer opens a transaction."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("the writer opened a session before refusing the request")


class _Result:
    def __init__(self, rows: tuple[Mapping[str, Any], ...] = ()) -> None:
        self._rows = rows

    def mappings(self) -> "_Result":
        return self

    def one(self) -> Mapping[str, Any]:
        if len(self._rows) != 1:
            raise AssertionError(f"expected exactly one row, got {len(self._rows)}")
        return self._rows[0]

    def one_or_none(self) -> Mapping[str, Any] | None:
        return self._rows[0] if self._rows else None

    def all(self) -> list[Mapping[str, Any]]:
        return list(self._rows)


class _Transaction:
    def __init__(self, session: "FakeSession") -> None:
        self._session = session

    async def __aenter__(self) -> "FakeSession":
        self._session.begin_calls += 1
        return self._session

    async def __aexit__(self, *_args: object) -> bool:
        return False


_PREDICATE = re.compile(r"m04_scope_promotion_evidence\.(\w+) = '([^']*)'")
_PARTITION_KEY = ("tenant_id", "scope_kind", "scope_id", "revision_id")


class FakeSession:
    """Statement-level stand-in for the writer's ``INSERT`` and its two ``select`` loads."""

    def __init__(self, rows: tuple[Any, ...] = (), *, readback_drift: str | None = None) -> None:
        self.rows: dict[tuple[str, ...], Any] = {
            tuple(getattr(row, name) for name in _PARTITION_KEY): row for row in rows
        }
        self.pending: list[Any] = []
        self.applied: dict[str, Any] = {}
        self.readback_drift = readback_drift
        self.statements: list[str] = []
        self.scalar_statements: list[str] = []
        self.begin_calls = 0
        self.flush_calls = 0

    # -- session surface --------------------------------------------------

    def begin(self) -> _Transaction:
        return _Transaction(self)

    def add(self, row: Any) -> None:
        self.pending.append(row)

    async def flush(self) -> None:
        self.flush_calls += 1
        pending, self.pending = self.pending, []
        for row in pending:
            key = tuple(getattr(row, name) for name in _PARTITION_KEY)
            if key in self.rows:
                raise IntegrityError(
                    "INSERT INTO m04_scope_promotion_evidence",
                    None,
                    Exception("duplicate key value violates unique constraint"),
                )
            self.rows[key] = row

    async def execute(self, statement: Any, params: Mapping[str, Any] | None = None) -> _Result:
        sql = str(statement)
        self.statements.append(sql)
        if "set_config(" in sql:
            self.applied = dict(params or {})
            return _Result()
        if "current_setting('app.tenant_id', true)" in sql:
            applied = dict(self.applied)
            return _Result(({
                # Fault injection: report a context the transaction never applied.
                "tenant_id": self.readback_drift or applied.get("tenant_id"),
                "writer_generation": applied.get("writer_generation"),
                "migration_role": applied.get("migration_role"),
            },))
        raise AssertionError(f"unexpected statement: {sql}")

    async def scalar(self, statement: Any) -> Any:
        sql = self._render(statement)
        self.scalar_statements.append(sql)
        filters = dict(_PREDICATE.findall(sql))
        if not filters:
            raise AssertionError(f"the writer's load carries no partition predicates: {sql}")
        for key, row in self.rows.items():
            if all(str(getattr(row, name)) == value for name, value in filters.items()):
                return row
        return None

    # -- inspection -------------------------------------------------------

    @staticmethod
    def _render(statement: Any) -> str:
        return str(statement.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        ))

    @property
    def stored(self) -> tuple[Any, ...]:
        return tuple(self.rows.values())


class FakeSessionFactory:
    """Callable ``async_sessionmaker`` stand-in handing out one recorded session."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.opened = 0

    def __call__(self) -> "_SessionContext":
        self.opened += 1
        return _SessionContext(self.session)


class _SessionContext:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *_args: object) -> bool:
        return False


def session(*rows: Any, readback_drift: str | None = None) -> tuple[FakeSession, FakeSessionFactory]:
    fake = FakeSession(tuple(rows), readback_drift=readback_drift)
    return fake, FakeSessionFactory(fake)


def writer(session_factory: Any = None, *, scope: TrustedEvidenceScope | None = None) -> SqlM04ScopeEvidenceWriter:
    return SqlM04ScopeEvidenceWriter(
        session_factory=session_factory or ExplodingSessionFactory(),
        scope=scope or trusted_scope(),
    )


def stored_row(**values: Any) -> Any:
    """A fully populated row object in ORM *state*, never persisted.

    Used where a case needs a row to already exist (replay/conflict) without going through
    the writer first.
    """
    row_values: dict[str, Any] = {
        "id": "m04scope:" + "0" * 64,
        "tenant_id": TENANT,
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "cabinet_id": None,
        "project_id": None,
        "scope_registry_id": REGISTRY_ID,
        "revision_id": "sha256:" + "1" * 64,
        "previous_revision_id": None,
        "outcome": "ready",
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "canonical_digest": digest_of("d"),
        "canonical_schema_version": CANONICAL_SCHEMA,
        "checkpoint_digest": digest_of("e"),
        "gate_evidence_digest": digest_of("f"),
        "rls_policy_digest": digest_of("9"),
        "rls_security_review_id": "rls-registry:2026110307.1",
        "rls_database_role": "prodavan_migration_writer",
        "gate_evidence": {"schema": "m04-scope-evidence.v1"},
        "reason_code": None,
        "writer_generation": WRITER_GENERATION,
        "correlation_id": CORRELATION,
        "actor_id": ACTOR,
        "canonical_positive_writes": False,
    }
    row_values.update(values)
    return M04ScopePromotionEvidenceRow(**row_values)


__all__ = [
    "ACTOR",
    "ANCHOR_BUILDERS",
    "CABINET",
    "CANONICAL_SCHEMA",
    "CORRELATION",
    "CHECKPOINT_SNAPSHOT_DIGEST",
    "ExplodingSessionFactory",
    "FakeSession",
    "FakeSessionFactory",
    "NOW_TEXT",
    "OTHER_ACTOR",
    "OTHER_TENANT",
    "OTHER_TRANSACTION",
    "PAYLOAD_MARKER",
    "PLUGIN_INSTANCE",
    "PROJECT",
    "REGISTRY_ID",
    "SECRET_MARKER",
    "SNAPSHOT_DIGEST",
    "SNAPSHOT_ID",
    "SOURCE_DIGEST",
    "SOURCE_SCHEMA",
    "TENANT",
    "TRANSACTION",
    "WRITER_GENERATION",
    "active_resolution",
    "actor_evidence",
    "cabinet_anchors",
    "checkpoint",
    "digest_of",
    "parent_evidence",
    "parent_proof",
    "plugin_instance_anchors",
    "project_anchors",
    "refusal_resolution",
    "refused_write",
    "registry_entry",
    "resolution_evidence",
    "rls_evidence",
    "session",
    "scope_row_values_for",
    "stored_row",
    "tenant_anchors",
    "trusted_scope",
    "write",
    "writer",
]
