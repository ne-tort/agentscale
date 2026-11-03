"""Behavioral M04 SQLAlchemy persistence gates; intentionally not executed."""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import replace
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.migration.m04_scope.scope_migration import (
    BackfillOutcome,
    MigrationEvidenceContext,
    ScopeSourceEvidence,
    ScopeSourceQuarantineEvidence,
    SourceRowState,
)
from prodavan.infrastructure.persistence.migration_scope.adapter import (
    ReviewedScopeRlsManifest,
    ScopePersistenceGateBlocked,
    SqlAlchemyTrustedScopeTransactionPort,
    _OWNER_TABLES,
    _declared_schema_blockers,
    _policy_digest,
    _policy_shape_is_restrictive,
)
from acceptance_fixtures import candidate, checkpoint, trusted_context


class _Rows:
    def __init__(self, row):
        self._row = row

    def one(self):
        return self._row


class _Result:
    def __init__(self, row=None, scalar=None):
        self._row = row
        self._scalar = scalar

    def mappings(self):
        return _Rows(self._row)

    def scalar_one(self):
        return self._scalar


class _Transaction:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return False


class _FakeSession:
    """Tiny SQLAlchemy-session stand-in to exercise transaction setup/cleanup."""

    def __init__(self, context: MigrationEvidenceContext):
        self.context = context
        self.statements: list[str] = []
        self._gucs = {}
        self._bind = SimpleNamespace(dialect=SimpleNamespace(name="postgresql"))

    def get_bind(self):
        return self._bind

    def begin(self):
        return _Transaction()

    async def execute(self, statement, params: dict[str, str] | None = None):
        sql = str(statement)
        self.statements.append(sql)
        if "txid_current()" in sql:
            return _Result({"txid": "7081"})
        if "set_config('app.tenant_id', '', true)" in sql:
            self._gucs = {key: "" for key in (
                "tenant_id", "cabinet_id", "project_id", "user_id", "role", "writer_generation"
            )}
            return _Result(self._gucs.copy())
        if "set_config('app.tenant_id', :tenant_id, true)" in sql:
            assert params is not None
            self._gucs = {
                "tenant_id": params["tenant_id"],
                "cabinet_id": params["cabinet_id"],
                "project_id": params["project_id"],
                "user_id": params["user_id"],
                "role": params["role"],
                "writer_generation": params["writer_generation"],
            }
            return _Result(self._gucs.copy())
        if "current_setting('app.tenant_id', true)" in sql:
            return _Result({**self._gucs, "row_security": "on", "database_role": self.context.role})
        raise AssertionError(f"unexpected query in gate-only test: {sql}")


class _SessionFactory:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False

    def __call__(self):
        return self


class _GateOnlyPort(SqlAlchemyTrustedScopeTransactionPort):
    def __init__(self, session, blockers):
        super().__init__(session_factory=_SessionFactory(session))
        self.blockers = tuple(blockers)

    async def _gate_blockers(
        self, session: AsyncSession, context: MigrationEvidenceContext
    ) -> tuple[str, ...]:
        return self.blockers


@asynccontextmanager
async def _bound_transaction(session, context):
    adapter = SqlAlchemyTrustedScopeTransactionPort(session_factory=_SessionFactory(session))
    async with adapter._bound_transaction(context) as bound:
        yield bound


@pytest.mark.asyncio
async def test_transaction_context_is_local_read_back_and_cleared_on_exit():
    context = replace(trusted_context(), transaction_id="7081")
    session = _FakeSession(context)

    async with _bound_transaction(session, context) as (_bound_session, binding):
        assert binding.application_transaction_id == context.transaction_id
        assert binding.database_transaction_id == "7081"
        assert binding.tenant_id == context.tenant_id
        assert any("set_config('app.tenant_id', :tenant_id, true)" in sql for sql in session.statements)
        assert any("current_setting('app.tenant_id', true)" in sql for sql in session.statements)

    assert any("set_config('app.tenant_id', '', true)" in sql for sql in session.statements)
    assert all(value == "" for value in session._gucs.values())


@pytest.mark.asyncio
async def test_application_transaction_id_must_match_database_transaction():
    context = trusted_context()  # The fixture's opaque ID is not a PostgreSQL txid.
    session = _FakeSession(context)
    adapter = SqlAlchemyTrustedScopeTransactionPort(session_factory=_SessionFactory(session))

    with pytest.raises(ScopePersistenceGateBlocked, match="application_database_transaction_identity_mismatch"):
        async with adapter._bound_transaction(context):
            raise AssertionError("transaction with an unbound application ID must not proceed")

    assert not any("set_config('app.tenant_id', :tenant_id, true)" in sql for sql in session.statements)


@pytest.mark.asyncio
async def test_missing_checkpoint_schema_raises_instead_of_inventing_initial_cursor():
    context = replace(trusted_context(), transaction_id="7081")
    session = _FakeSession(context)
    port = _GateOnlyPort(session, ("m04_checkpoint_model_and_key_contract_missing",))

    with pytest.raises(ScopePersistenceGateBlocked, match="m04_checkpoint_model_and_key_contract_missing"):
        await port.load_checkpoint(context=context, stream=checkpoint_stream())

    assert not any("INSERT" in sql.upper() or "UPDATE" in sql.upper() for sql in session.statements)


@pytest.mark.asyncio
@pytest.mark.parametrize("payload", [{"scope": "src-1", "state": "active"}, {"scope": "changed", "state": "active"}])
async def test_same_source_key_replay_or_digest_change_stays_blocked_until_database_contract(payload):
    context = replace(trusted_context(), transaction_id="7081")
    item = candidate(payload=payload)
    session = _FakeSession(context)
    port = _GateOnlyPort(session, ("canonical_schema_version_not_persisted_on_source_mapping",))
    expected = checkpoint()
    next_cursor = replace(expected, sequence=expected.sequence + 1, after_source_id=item.source_id)

    result = await port.classify_and_append(
        context=context,
        candidate=_source_evidence(item),
        source_mapping_identity=item.mapping_identity,
        expected_checkpoint=expected,
        next_checkpoint=next_cursor,
        proposed_entry=None,
        proposed_quarantine=None,
        reason_code=None,
    )

    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.checkpoint == expected
    assert result.canonical_positive_writes is False
    assert not any(token in sql.upper() for sql in session.statements for token in ("INSERT", "UPDATE", "DELETE"))


@pytest.mark.asyncio
async def test_missing_rls_and_append_only_review_blocks_mapping_without_writes():
    context = replace(trusted_context(), transaction_id="7081")
    item = candidate()
    source = ScopeSourceEvidence(
        source_kind=item.source_kind,
        source_id=item.source_id,
        schema_version=item.schema_version,
        canonical_schema_version=item.canonical_schema_version,
        source_digest=item.source_digest,
        source_state=item.source_state,
        anchors=item.anchors,
        asserted_tenant_id=item.asserted_tenant_id,
        asserted_cabinet_id=item.asserted_cabinet_id,
        asserted_project_id=item.asserted_project_id,
    )
    expected = checkpoint()
    next_cursor = replace(expected, after_source_id=item.source_id, sequence=expected.sequence + 1)
    session = _FakeSession(context)
    port = _GateOnlyPort(session, ("m04_rls_and_append_only_security_review_required",))

    result = await port.classify_and_append(
        context=context,
        candidate=source,
        source_mapping_identity=item.mapping_identity,
        expected_checkpoint=expected,
        next_checkpoint=next_cursor,
        proposed_entry=None,
        proposed_quarantine=None,
        reason_code="unresolved_typed_scope",
    )

    assert result.outcome is BackfillOutcome.BLOCKED
    assert result.reason_code == "append_only_audit_unavailable"
    assert result.checkpoint == expected
    assert result.canonical_positive_writes is False
    assert not any(token in sql.upper() for sql in session.statements for token in ("INSERT", "UPDATE", "DELETE"))


@pytest.mark.asyncio
async def test_active_resolution_returns_no_entry_without_reviewed_rls_and_parent_proof():
    context = replace(trusted_context(), transaction_id="7081")
    session = _FakeSession(context)
    port = _GateOnlyPort(session, ("m04_rls_and_append_only_security_review_required",))

    resolved = await port.resolve_active(context=context, anchors=item_anchors())

    assert resolved is None
    assert not any("FROM scope_registry" in sql for sql in session.statements)
    assert not any(token in sql.upper() for sql in session.statements for token in ("INSERT", "UPDATE", "DELETE"))


def test_source_identity_and_checkpoint_cas_are_validated_before_database_work():
    item = candidate()
    expected = checkpoint()
    context = trusted_context()
    source = _source_evidence(item)
    assert item.anchors is not None
    successor = replace(expected, sequence=1, after_source_id=item.source_id)

    with pytest.raises(ValueError, match="source candidate tenant"):
        SqlAlchemyTrustedScopeTransactionPort._validate_command(
            context=context,
            candidate=replace(source, anchors=replace(item.anchors, tenant_id="tenant-other")),
            source_mapping_identity=item.mapping_identity,
            expected_checkpoint=expected,
            next_checkpoint=successor,
            proposed_entry=None,
            proposed_quarantine=None,
        )
    with pytest.raises(ValueError, match="writer generation"):
        SqlAlchemyTrustedScopeTransactionPort._validate_command(
            context=context,
            candidate=source,
            source_mapping_identity=item.mapping_identity,
            expected_checkpoint=replace(expected, writer_generation=2),
            next_checkpoint=replace(successor, writer_generation=2),
            proposed_entry=None,
            proposed_quarantine=None,
        )
    bad_next = replace(expected, sequence=expected.sequence + 2, after_source_id=item.source_id)
    with pytest.raises(ValueError, match="sequence-CAS successor"):
        SqlAlchemyTrustedScopeTransactionPort._validate_command(
            context=context,
            candidate=source,
            source_mapping_identity=item.mapping_identity,
            expected_checkpoint=expected,
            next_checkpoint=bad_next,
            proposed_entry=None,
            proposed_quarantine=None,
        )
    with pytest.raises(ValueError, match="source mapping identity"):
        SqlAlchemyTrustedScopeTransactionPort._validate_command(
            context=context,
            candidate=source,
            source_mapping_identity=(item.source_kind, item.source_id, "different-schema"),
            expected_checkpoint=expected,
            next_checkpoint=successor,
            proposed_entry=None,
            proposed_quarantine=None,
        )


def test_quarantine_digest_must_match_exact_source_observation():
    item = candidate()
    expected = checkpoint()
    context = trusted_context()
    wrong_observation = ScopeSourceQuarantineEvidence(
        source_kind=item.source_kind,
        source_id=item.source_id,
        schema_version=item.schema_version,
        canonical_schema_version=item.canonical_schema_version,
        source_digest="sha256:" + "e" * 64,
    )

    with pytest.raises(ValueError, match="exact source key and digest"):
        SqlAlchemyTrustedScopeTransactionPort._validate_command(
            context=context,
            candidate=_source_evidence(item),
            source_mapping_identity=item.mapping_identity,
            expected_checkpoint=expected,
            next_checkpoint=replace(expected, sequence=1, after_source_id=item.source_id),
            proposed_entry=None,
            proposed_quarantine=wrong_observation,
        )


def test_declared_schema_gaps_keep_all_persistence_paths_blocked():
    blockers = _declared_schema_blockers()
    assert "canonical_schema_version_not_persisted_on_source_mapping" in blockers
    assert "m04_checkpoint_model_and_key_contract_missing" in blockers
    assert "membership_row_has_no_active_or_deleted_lifecycle" in blockers
    assert "m04_rls_and_append_only_security_review_required" in blockers
    assert "m04_scope_promotion_gate_evidence_missing" in blockers


def test_rls_manifest_requires_every_owner_table_and_exact_policy_digest_format():
    with pytest.raises(ValueError, match="every M04 owner-registry table"):
        ReviewedScopeRlsManifest("review", "migration", (("scope_registry", "sha256:" + "a" * 64),))
    manifest = ReviewedScopeRlsManifest(
        "review-1",
        "migration",
        tuple((table, "sha256:" + "a" * 64) for table in _OWNER_TABLES),
    )
    assert manifest.digest_for("scope_owner_audit") == "sha256:" + "a" * 64


def test_policy_review_requires_command_appropriate_using_and_with_check():
    assert _policy_shape_is_restrictive({"cmd": "SELECT", "permissive": "RESTRICTIVE", "qual": "tenant predicate", "with_check": None})
    assert not _policy_shape_is_restrictive({"cmd": "SELECT", "permissive": "PERMISSIVE", "qual": "tenant predicate", "with_check": None})
    assert not _policy_shape_is_restrictive({"cmd": "SELECT", "permissive": "RESTRICTIVE", "qual": None, "with_check": None})
    assert _policy_shape_is_restrictive({"cmd": "INSERT", "permissive": "RESTRICTIVE", "qual": None, "with_check": "tenant predicate"})
    assert not _policy_shape_is_restrictive({"cmd": "INSERT", "permissive": "RESTRICTIVE", "qual": None, "with_check": None})
    assert _policy_digest([]).startswith("sha256:")


def checkpoint_stream():
    from acceptance_fixtures import scope_stream

    return scope_stream()


def item_anchors():
    from acceptance_fixtures import cabinet_anchors

    return cabinet_anchors()


def _source_evidence(item):
    return ScopeSourceEvidence(
        source_kind=item.source_kind,
        source_id=item.source_id,
        schema_version=item.schema_version,
        canonical_schema_version=item.canonical_schema_version,
        source_digest=item.source_digest,
        source_state=SourceRowState.ACTIVE,
        anchors=item.anchors,
        asserted_tenant_id=item.asserted_tenant_id,
        asserted_cabinet_id=item.asserted_cabinet_id,
        asserted_project_id=item.asserted_project_id,
    )
