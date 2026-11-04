"""MIG-M04-WRITER-01: every refusal, and every one of them before any SQL.

The acceptance list of the task names four refusals that must happen without writing a row:
a plugin-instance scope, a ready row without an actor, a non-ready row without a reason code
and a partition outside the trusted tenant. Each is checked twice: the writer raises a named
:class:`MigrationEvidenceBlocked` (or subclass) and the model constraint that would otherwise
reject the row is proven to exist, so the writer's refusal is the *first* line and the
database is the second.

All cases inject ``ExplodingSessionFactory`` except where the assertion is about the row a
refusal did *not* produce, so "refused before SQL" is a fact of the test, not a claim in a
docstring.
"""

from __future__ import annotations

import pytest
from sqlalchemy import CheckConstraint

from prodavan.application.migration.evidence_ports import (
    MigrationEvidenceBlocked,
    MigrationEvidenceCorrupt,
)
from prodavan.application.migration.m04_scope.scope_migration import (
    ScopeAnchors,
    ScopeParentEvidence,
    ScopeResolution,
)
from prodavan.application.migration.m04_scope_evidence_writer import (
    M04_SCOPE_EVIDENCE_CANONICAL_DIGEST_DISAGREES,
    M04_SCOPE_EVIDENCE_CHECKPOINT_GENERATION_MISMATCH,
    M04_SCOPE_EVIDENCE_NON_READY_REQUIRES_REASON,
    M04_SCOPE_EVIDENCE_PARTITION_OUTSIDE_TRUSTED_TENANT,
    M04_SCOPE_EVIDENCE_PREVIOUS_REVISION_MISSING,
    M04_SCOPE_EVIDENCE_READBACK_MISMATCH,
    M04_SCOPE_EVIDENCE_READY_ACTOR_MISMATCH,
    M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTIVE_OWNER,
    M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR,
    M04_SCOPE_EVIDENCE_READY_REQUIRES_EVIDENCE,
    M04_SCOPE_EVIDENCE_READY_REQUIRES_PARENT,
    M04_SCOPE_EVIDENCE_READY_REQUIRES_RLS,
    M04_SCOPE_EVIDENCE_READY_REQUIRES_TRANSACTION,
    M04_SCOPE_EVIDENCE_REGISTRY_MISSING,
    M04_SCOPE_EVIDENCE_REVISION_CYCLE,
    M04_SCOPE_EVIDENCE_WRITER_GENERATION_MISSING,
    M04_SCOPE_EVIDENCE_WRITER_GENERATION_NOT_TRUSTED,
    PLUGIN_INSTANCE_SCOPE_DENIED,
    SqlM04ScopeEvidenceWriter,
    scope_evidence_row_values,
)
from prodavan.application.scope_registry.contracts import OwnerState, ScopeKind
from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
    M04ScopePromotionEvidenceRow,
)

from . import acceptance_fixtures as fixtures
from .acceptance_fixtures import ExplodingSessionFactory, OTHER_TENANT


def _check_sqltexts() -> dict[str, str]:
    """``constraint name -> SQL text`` for every named check of the evidence table.

    Only :class:`CheckConstraint` objects carry ``sqltext``: the table also declares named
    foreign keys (``*_fkey``), which are constraints but not checks and have no SQL predicate
    to assert on, so they are filtered out by type rather than assumed to expose the
    attribute.
    """
    return {
        str(constraint.name): str(constraint.sqltext)
        for constraint in M04ScopePromotionEvidenceRow.__table__.constraints
        if isinstance(constraint, CheckConstraint) and constraint.name
    }


# ---------------------------------------------------------------------------
# plugin_instance: refused, and unrepresentable in the table
# ---------------------------------------------------------------------------


async def test_a_plugin_instance_scope_is_refused_and_no_row_is_written():
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(
            fixtures.write(fixtures.plugin_instance_anchors())
        )
    assert raised.value.blockers == (PLUGIN_INSTANCE_SCOPE_DENIED,)
    assert factory.calls == 0, "a denied scope must be refused before any SQL"


def test_plugin_instance_is_not_a_storable_scope_kind():
    scope_kind = _check_sqltexts()["ck_m04_scope_promotion_scope_kind"]
    assert "tenant" in scope_kind and "cabinet" in scope_kind and "project" in scope_kind
    assert "plugin_instance" not in scope_kind


def test_the_m04_02_contract_refuses_the_same_scope():
    """The writer and the resolver use one refusal vocabulary for the same denial.

    The kind is checked *first*, before any tenant or parent leg, exactly as
    ``ScopeResolver.resolve`` and ``evaluate_active_scope_evidence`` deny it before their
    context checks. This evidence is deliberately left incomplete — ``tenant_id`` is unset
    and the membership cabinet does not match — so that a contract that answered
    ``cross_tenant_parent`` (a reason a caller could "fix" by resending corrected parents)
    for a scope kind M04-02 cannot store at all would be caught here.
    """
    parent = ScopeParentEvidence(
        tenant_matches=1,
        cabinet_matches=1,
        active_cabinet_memberships=1,
        project_matches=0,
        active_registry_matches=1,
        cabinet_membership_kind="cabinet_company_grant",
        membership_tenant_id=fixtures.TENANT,
        membership_cabinet_id=None,
    )
    assert (
        parent.invalid_reason_for(fixtures.plugin_instance_anchors())
        == PLUGIN_INSTANCE_SCOPE_DENIED
    )


# ---------------------------------------------------------------------------
# ready requires an actor (db check + writer refusal)
# ---------------------------------------------------------------------------


async def test_a_ready_row_without_an_actor_is_refused():
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(fixtures.write(actor_id=None))
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR,)
    assert factory.calls == 0


async def test_a_ready_row_whose_actor_is_not_the_proven_actor_is_refused():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(ExplodingSessionFactory()).append(
            fixtures.write(actor_id=fixtures.OTHER_ACTOR)
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_READY_ACTOR_MISMATCH,)


def test_the_ready_actor_requirement_is_also_a_database_check():
    actor_check = _check_sqltexts()["ck_m04_scope_promotion_ready_requires_actor"]
    assert "ready" in actor_check and "actor_id" in actor_check


@pytest.mark.parametrize(
    "actor_kwargs,blocker",
    [
        ({"active": False}, M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR),
        ({"relationship_matches": 2}, M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR),
        ({"relationship_kind": "contractor"}, M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTOR),
        # A membership proven in another transaction is not same-transaction proof.
        ({"transaction_id": fixtures.OTHER_TRANSACTION},
         M04_SCOPE_EVIDENCE_READY_REQUIRES_TRANSACTION),
    ],
)
async def test_a_ready_row_requires_a_proven_active_membership(actor_kwargs, blocker):
    anchors = fixtures.tenant_anchors()
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(fixtures.write(
            resolution=fixtures.active_resolution(anchors),
            evidence=fixtures.resolution_evidence(
                anchors, actor=fixtures.actor_evidence(anchors, **actor_kwargs)
            ),
            actor_id=fixtures.ACTOR,
        ))
    assert blocker in raised.value.blockers
    assert factory.calls == 0


# ---------------------------------------------------------------------------
# non-ready requires a reason code (db check + writer refusal)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("outcome", [ScopeResolution.DENY, ScopeResolution.DEFERRED,
                                     ScopeResolution.QUARANTINED])
async def test_a_non_ready_row_without_a_reason_code_is_refused(outcome):
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(
            fixtures.refused_write(outcome=outcome, reason_code="   ")
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_NON_READY_REQUIRES_REASON,)
    assert factory.calls == 0


def test_the_non_ready_reason_requirement_is_also_a_database_check():
    reason_check = _check_sqltexts()["ck_m04_scope_promotion_non_ready_requires_reason"]
    assert "ready" in reason_check and "reason_code" in reason_check


# ---------------------------------------------------------------------------
# foreign partition / untrusted context: refused before any SQL
# ---------------------------------------------------------------------------


def _foreign_anchors() -> ScopeAnchors:
    return ScopeAnchors(
        scope_kind=ScopeKind.TENANT, scope_id=OTHER_TENANT, tenant_id=OTHER_TENANT
    )


async def test_a_partition_outside_the_trusted_tenant_is_refused():
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(fixtures.write(_foreign_anchors()))
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_PARTITION_OUTSIDE_TRUSTED_TENANT,)
    assert factory.calls == 0, "a foreign partition must be refused before any SQL"


async def test_a_checkpoint_of_another_tenant_is_refused():
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(fixtures.write(
            checkpoint=fixtures.checkpoint(tenant_id=OTHER_TENANT)
        ))
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_PARTITION_OUTSIDE_TRUSTED_TENANT,)
    assert factory.calls == 0


async def test_an_absent_trusted_writer_generation_is_refused():
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory, scope=fixtures.trusted_scope(writer_generation=None)).append(
            fixtures.write()
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_WRITER_GENERATION_MISSING,)
    assert factory.calls == 0


async def test_an_untrusted_writer_generation_is_refused():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(ExplodingSessionFactory()).append(
            fixtures.write(writer_generation=4)
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_WRITER_GENERATION_NOT_TRUSTED,)


async def test_a_checkpoint_of_another_writer_generation_is_refused():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(ExplodingSessionFactory()).append(
            fixtures.write(checkpoint=fixtures.checkpoint(writer_generation=2))
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_CHECKPOINT_GENERATION_MISMATCH,)


# ---------------------------------------------------------------------------
# An ACTIVE verdict whose bundle does not prove acceptance is a contradiction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "overrides,blocker",
    [
        ({"evidence": None}, M04_SCOPE_EVIDENCE_READY_REQUIRES_EVIDENCE),
        (
            {
                "resolution": fixtures.active_resolution(
                    fixtures.tenant_anchors(),
                    entry=fixtures.registry_entry(
                        fixtures.tenant_anchors(), owner_state=OwnerState.QUARANTINED
                    ),
                ),
                "evidence": fixtures.resolution_evidence(
                    fixtures.tenant_anchors(),
                    entry=fixtures.registry_entry(
                        fixtures.tenant_anchors(), owner_state=OwnerState.QUARANTINED
                    ),
                ),
            },
            M04_SCOPE_EVIDENCE_READY_REQUIRES_ACTIVE_OWNER,
        ),
        (
            {"evidence": fixtures.resolution_evidence(
                fixtures.tenant_anchors(), proof=fixtures.parent_proof(valid=False)
            )},
            M04_SCOPE_EVIDENCE_READY_REQUIRES_PARENT,
        ),
        (
            # A cabinet scope without the cabinet half of the parent chain is not acceptance.
            {
                "anchors": fixtures.cabinet_anchors(),
                "resolution": fixtures.active_resolution(fixtures.cabinet_anchors()),
                "evidence": fixtures.resolution_evidence(
                    fixtures.cabinet_anchors(),
                    proof=fixtures.parent_proof(fixtures.tenant_anchors()),
                ),
            },
            M04_SCOPE_EVIDENCE_READY_REQUIRES_PARENT,
        ),
        (
            {"evidence": fixtures.resolution_evidence(
                fixtures.tenant_anchors(),
                rls=fixtures.rls_evidence(fixtures.tenant_anchors(), fully_enforced=False),
            )},
            M04_SCOPE_EVIDENCE_READY_REQUIRES_RLS,
        ),
        (
            {"evidence": fixtures.resolution_evidence(
                fixtures.tenant_anchors(),
                parent_transaction_id=fixtures.OTHER_TRANSACTION,
                parent=fixtures.parent_evidence(
                    fixtures.tenant_anchors(), transaction_id=fixtures.OTHER_TRANSACTION
                ),
            )},
            M04_SCOPE_EVIDENCE_READY_REQUIRES_TRANSACTION,
        ),
    ],
)
async def test_an_active_verdict_without_complete_proof_writes_nothing(overrides, blocker):
    factory = ExplodingSessionFactory()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(fixtures.write(**overrides))
    assert blocker in raised.value.blockers
    assert factory.calls == 0


async def test_a_ready_row_whose_registry_identity_is_absent_is_refused():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(ExplodingSessionFactory()).append(
            fixtures.write(scope_registry_id="  ")
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_REGISTRY_MISSING,)


# ---------------------------------------------------------------------------
# Digest / revision integrity
# ---------------------------------------------------------------------------


async def test_an_expected_canonical_digest_that_disagrees_is_refused():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(ExplodingSessionFactory()).append(
            fixtures.write(expected_canonical_digest="sha256:" + "0" * 64)
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_CANONICAL_DIGEST_DISAGREES,)


async def test_the_expected_canonical_digest_is_accepted_when_it_matches():
    fake, factory = fixtures.session()
    expected = scope_evidence_row_values(
        fixtures.write(), scope=fixtures.trusted_scope()
    )["canonical_digest"]
    appended = await fixtures.writer(factory).append(
        fixtures.write(expected_canonical_digest=expected)
    )
    assert appended.inserted is True
    assert fake.stored[0].canonical_digest == expected


async def test_a_previous_revision_that_does_not_exist_is_refused():
    fake, factory = fixtures.session()
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(
            fixtures.write(previous_revision_id="sha256:" + "7" * 64)
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_PREVIOUS_REVISION_MISSING,)
    assert fake.flush_calls == 0, "nothing may be written for an unknown predecessor"


async def test_a_previous_revision_that_points_at_itself_is_refused():
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(ExplodingSessionFactory()).append(
            fixtures.write(revision_id="rev-fixed", previous_revision_id="rev-fixed")
        )
    assert raised.value.blockers == (M04_SCOPE_EVIDENCE_REVISION_CYCLE,)


async def test_a_previous_revision_that_exists_is_accepted():
    first = fixtures.stored_row()
    fake, factory = fixtures.session(first)
    appended = await fixtures.writer(factory).append(
        fixtures.write(previous_revision_id=first.revision_id)
    )
    assert appended.inserted is True
    assert appended.revision_id != first.revision_id
    assert {row.revision_id for row in fake.stored} == {first.revision_id, appended.revision_id}


# ---------------------------------------------------------------------------
# Readback inside the same transaction
# ---------------------------------------------------------------------------


async def test_a_transaction_context_that_does_not_read_back_is_refused_before_the_insert():
    fake, factory = fixtures.session(readback_drift=OTHER_TENANT)
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        await fixtures.writer(factory).append(fixtures.write())
    assert "transaction_local_context_readback_mismatch" in raised.value.blockers
    assert fake.flush_calls == 0
    assert fake.stored == ()


async def test_a_readback_that_differs_from_the_written_row_is_corruption():
    class _DriftingSession(fixtures.FakeSession):
        """Records a different actor than the one the writer sent."""

        async def flush(self) -> None:
            await super().flush()
            for row in self.rows.values():
                row.actor_id = fixtures.OTHER_ACTOR

    fake = _DriftingSession()
    with pytest.raises(MigrationEvidenceCorrupt) as raised:
        await fixtures.writer(fixtures.FakeSessionFactory(fake)).append(fixtures.write())
    assert M04_SCOPE_EVIDENCE_READBACK_MISMATCH in raised.value.blockers
    assert f"{M04_SCOPE_EVIDENCE_READBACK_MISMATCH}:actor_id" in raised.value.blockers


def test_the_writer_refuses_to_be_constructed_without_a_trusted_scope():
    with pytest.raises(ValueError, match="resolved trusted evidence scope"):
        SqlM04ScopeEvidenceWriter(
            session_factory=ExplodingSessionFactory(),
            scope=object(),
        )


def test_a_trusted_scope_cannot_be_unusable():
    with pytest.raises(ValueError, match="resolved tenant"):
        fixtures.trusted_scope(tenant_id="   ")


def test_the_pure_builder_refuses_a_scope_that_is_not_trusted_evidence_context():
    """The builder is importable on its own; it must not accept a look-alike scope."""
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        scope_evidence_row_values(fixtures.write(), scope=object())
    assert raised.value.blockers == ("m04_scope_evidence_tenant_missing",)


def test_scoped_helper_values_match_the_writer_path():
    """The pure builder the tests inspect is the one ``append`` actually calls."""
    import inspect

    source = inspect.getsource(SqlM04ScopeEvidenceWriter.append)
    assert "scope_evidence_row_values" in source
    assert source.index("scope_evidence_row_values") < source.index("self._boundaries.write()")
