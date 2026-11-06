"""MIG-M04-WRITER-01: the writer produces exactly what the partition reader accepts.

The reader is the contract. ``evidence_ports.review_m04_scope_evidence`` is the only code
that decides whether a stored row attests a partition, so these cases drive the writer and
then hand its row to that reviewer:

* a ready row is accepted, with the row id and canonical digest the reader returns;
* the canonical digest is byte-identical to the digest the *M04-02 coordinator* validates
  an accepted resolution against (``ScopeMigrationCoordinator._canonical_digest``), so the
  M05 promotion record can cite it as its ``M04_scope`` gate without a translation step;
* the typed scope shape is exactly the anchor ``m04_scope_anchor`` reconstructs, for all
  three owner kinds;
* an exact repeat is a replay that inserts nothing, while a changed digest is a typed
  conflict under a fixed revision and a *forward* revision under a derived one.

No database is touched: the append runs against ``FakeSession``, which applies the real
``(tenant_id, scope_kind, scope_id, revision_id)`` uniqueness from the compiled statement.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy.exc import IntegrityError

from prodavan.application.migration.evidence_ports import (
    M04_SCOPE_EVIDENCE_DIGEST_COLUMNS,
    MigrationEvidenceBlocked,
    MigrationEvidenceConflict,
    is_sha256,
    m04_scope_anchor,
    review_m04_scope_evidence,
)
from prodavan.application.migration.m04_scope.scope_migration import ScopeMigrationCoordinator
from prodavan.application.migration.m04_scope_evidence_writer import (
    M04_SCOPE_EVIDENCE_REVISION_CONTENT_CONFLICT,
    m04_scope_canonical_digest,
)

from . import acceptance_fixtures as fixtures
from .acceptance_fixtures import ANCHOR_BUILDERS, CABINET, CANONICAL_SCHEMA, PROJECT, TENANT


def _anchor(shape: str):
    """The owner anchor the reader reconstructs for one instance installation."""
    return {
        "tenant": m04_scope_anchor(tenant_id=TENANT, cabinet_id=None, project_id=None),
        "cabinet": m04_scope_anchor(tenant_id=TENANT, cabinet_id=CABINET, project_id=None),
        "project": m04_scope_anchor(tenant_id=TENANT, cabinet_id=CABINET, project_id=PROJECT),
    }[shape]


def _review(row: Any, shape: str = "tenant"):
    return review_m04_scope_evidence(
        row=row,
        tenant_id=TENANT,
        anchor=_anchor(shape),
        scope_registry_id=fixtures.REGISTRY_ID,
        expected_digest=_coordinator_digest(shape),
    )


def _coordinator_digest(shape: str) -> str:
    """The digest the M04-02 coordinator itself accepts for this scope.

    Computed with the production formatter, never with the writer's helper, so the two
    cannot agree by construction.
    """
    entry = fixtures.registry_entry(ANCHOR_BUILDERS[shape]())
    return ScopeMigrationCoordinator._canonical_digest(  # noqa: SLF001 - production formatter
        entry, CANONICAL_SCHEMA
    )


# ---------------------------------------------------------------------------
# Writer -> reader round trip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("shape", sorted(ANCHOR_BUILDERS))
async def test_a_ready_row_is_appended_and_accepted_by_the_partition_reader(shape):
    fake, factory = fixtures.session()
    appended = await fixtures.writer(factory).append(fixtures.write(ANCHOR_BUILDERS[shape]()))

    assert appended.inserted is True
    assert appended.replayed is False
    assert appended.outcome == "ready"
    assert fake.flush_calls == 1 and len(fake.stored) == 1

    row = fake.stored[0]
    blockers, row_id, canonical_digest = _review(row, shape)
    assert blockers == ()
    assert row_id == row.id == appended.row_id
    assert canonical_digest == row.canonical_digest == appended.canonical_digest


@pytest.mark.parametrize("shape", sorted(ANCHOR_BUILDERS))
def test_the_canonical_digest_is_the_m04_02_accepted_digest(shape):
    values = fixtures.scope_row_values_for(shape)
    entry = fixtures.registry_entry(ANCHOR_BUILDERS[shape]())
    assert values["canonical_digest"] == ScopeMigrationCoordinator._canonical_digest(  # noqa: SLF001
        entry, CANONICAL_SCHEMA
    )


def test_the_writer_helper_agrees_with_the_coordinator_formatter():
    """Same formula, independent implementations: identical bytes for every owner kind."""
    for shape, builder in ANCHOR_BUILDERS.items():
        anchors = builder()
        entry = fixtures.registry_entry(anchors)
        assert m04_scope_canonical_digest(
            canonical_schema_version=CANONICAL_SCHEMA,
            anchors=anchors,
            owner_state=entry.owner_state,
        ) == ScopeMigrationCoordinator._canonical_digest(entry, CANONICAL_SCHEMA)  # noqa: SLF001


@pytest.mark.parametrize("shape", sorted(ANCHOR_BUILDERS))
def test_the_stored_scope_shape_is_the_typed_anchor(shape):
    values = fixtures.scope_row_values_for(shape)
    kind, scope_id, cabinet_id, project_id = _anchor(shape)
    assert (values["scope_kind"], values["scope_id"]) == (kind, scope_id)
    assert values["cabinet_id"] == cabinet_id
    assert values["project_id"] == project_id
    assert values["tenant_id"] == TENANT
    if kind == "tenant":
        assert values["cabinet_id"] is None and values["project_id"] is None
    if kind == "cabinet":
        assert values["scope_id"] == CABINET and values["project_id"] is None
    if kind == "project":
        assert values["scope_id"] == PROJECT and values["cabinet_id"] == CABINET


def test_every_digest_column_is_sha256_and_the_row_never_enables_positive_writes():
    values = fixtures.scope_row_values_for("tenant")
    for column in M04_SCOPE_EVIDENCE_DIGEST_COLUMNS:
        assert is_sha256(values[column]), column
    assert values["canonical_positive_writes"] is False


def test_a_ready_row_names_its_actor_and_carries_no_reason_code():
    values = fixtures.scope_row_values_for("tenant")
    assert values["outcome"] == "ready"
    assert values["actor_id"] == fixtures.ACTOR
    assert values["reason_code"] is None


@pytest.mark.parametrize(
    "outcome,expected",
    [("DENY", "blocked"), ("DEFERRED", "blocked"), ("QUARANTINED", "quarantined")],
)
def test_a_refusal_is_recorded_with_its_redacted_reason_code(outcome, expected):
    from prodavan.application.migration.m04_scope.scope_migration import ScopeResolution

    values = fixtures.scope_row_values_for(
        "tenant",
        resolution=fixtures.refusal_resolution(outcome=ScopeResolution[outcome]),
        evidence=None,
        actor_id=None,
    )
    assert values["outcome"] == expected
    assert values["reason_code"] == "scope_missing_or_not_unique_active"
    assert values["actor_id"] is None
    # A refusal can never carry an acceptance digest: its owner state is unproven.
    assert values["canonical_digest"] == m04_scope_canonical_digest(
        canonical_schema_version=CANONICAL_SCHEMA,
        anchors=fixtures.tenant_anchors(),
        owner_state=None,
    )
    # An unproven RLS proof is stated as absent, never invented as a review identity.
    assert values["rls_security_review_id"].startswith("unproven:")
    assert values["rls_database_role"].startswith("unproven:")


@pytest.mark.parametrize("outcome", ["blocked", "quarantined"])
def test_a_refusal_row_is_recorded_but_never_consumed_as_acceptance(outcome):
    """The reader's second line: a stored refusal is ``not_ready``, not acceptance."""
    from prodavan.application.migration.m04_scope.scope_migration import ScopeResolution

    resolution_outcome = {
        "blocked": ScopeResolution.DENY,
        "quarantined": ScopeResolution.QUARANTINED,
    }[outcome]
    values = fixtures.scope_row_values_for(
        "tenant",
        resolution=fixtures.refusal_resolution(outcome=resolution_outcome),
        evidence=None,
        actor_id=None,
    )
    from prodavan.infrastructure.persistence.models.m04_scope_evidence import (
        M04ScopePromotionEvidenceRow,
    )

    blockers, row_id, canonical = review_m04_scope_evidence(
        row=M04ScopePromotionEvidenceRow(**values),
        tenant_id=TENANT,
        anchor=_anchor("tenant"),
        scope_registry_id=fixtures.REGISTRY_ID,
        expected_digest=None,
    )
    assert blockers == ("m04_scope_evidence_not_ready",)
    assert row_id == "" and canonical == ""


# ---------------------------------------------------------------------------
# Append-only semantics: replay, conflict, forward revision
# ---------------------------------------------------------------------------


async def test_an_exact_repeat_returns_the_recorded_row_and_inserts_nothing():
    fake, factory = fixtures.session()
    writer = fixtures.writer(factory)
    first = await writer.append(fixtures.write())
    second = await writer.append(fixtures.write())

    assert first.inserted is True and second.replayed is True
    assert second.row_id == first.row_id
    assert second.revision_id == first.revision_id
    assert second.outcome == "ready"
    assert fake.flush_calls == 1, "a replay must not write a second row"
    assert len(fake.stored) == 1


async def test_a_changed_digest_under_a_fixed_revision_is_a_typed_conflict():
    fake, factory = fixtures.session()
    writer = fixtures.writer(factory)
    await writer.append(fixtures.write(revision_id="rev-fixed"))
    other_checkpoint = fixtures.checkpoint(source_snapshot_digest=fixtures.digest_of("f"))

    with pytest.raises(MigrationEvidenceConflict) as raised:
        await writer.append(fixtures.write(revision_id="rev-fixed", checkpoint=other_checkpoint))

    blockers = raised.value.blockers
    assert M04_SCOPE_EVIDENCE_REVISION_CONTENT_CONFLICT in blockers
    # The conflict names the immutable fields that disagree, so a reviewer can tell a
    # digest change from an actor change.
    assert f"{M04_SCOPE_EVIDENCE_REVISION_CONTENT_CONFLICT}:checkpoint_digest" in blockers
    assert fake.flush_calls == 1, "a conflict must never write a second row"
    assert len(fake.stored) == 1


async def test_a_changed_digest_under_a_derived_revision_appends_a_forward_revision():
    """Forward evidence is appended, never overwritten: the older revision stays intact."""
    fake, factory = fixtures.session()
    writer = fixtures.writer(factory)
    first = await writer.append(fixtures.write())
    later = await writer.append(fixtures.write(
        checkpoint=fixtures.checkpoint(source_snapshot_digest=fixtures.digest_of("f"))
    ))

    assert first.inserted is True and later.inserted is True
    assert first.revision_id != later.revision_id
    assert first.row_id != later.row_id
    assert len(fake.stored) == 2
    assert {row.revision_id for row in fake.stored} == {first.revision_id, later.revision_id}


async def test_a_concurrent_writer_that_wins_the_insert_race_is_a_typed_conflict():
    fake = _RacingSession()
    with pytest.raises(MigrationEvidenceConflict) as raised:
        await fixtures.writer(fixtures.FakeSessionFactory(fake)).append(fixtures.write())
    assert raised.value.blockers == ("m04_scope_evidence_concurrent_append_conflict",)
    assert fake.flush_calls == 1
    assert fake.rows == {}, "the losing append must not leave a row behind"


@pytest.mark.parametrize(
    "message,expected,is_conflict",
    [
        (
            'duplicate key value violates unique constraint "uq_m04_scope_promotion_revision"',
            "m04_scope_evidence_concurrent_append_conflict",
            True,
        ),
        (
            'insert or update on table "m04_scope_promotion_evidence" violates foreign key '
            'constraint "fk_m04_scope_promotion_registry"',
            "m04_scope_evidence_registry_reference_invalid",
            False,
        ),
        (
            'new row for relation "m04_scope_promotion_evidence" violates check constraint '
            '"ck_m04_scope_promotion_typed_shape"',
            "m04_scope_evidence_constraint_violation",
            False,
        ),
    ],
)
async def test_a_rejected_insert_is_classified_by_its_constraint(message, expected, is_conflict):
    """Only a lost insert race is retryable; a constraint violation is a bug."""
    fake = fixtures.FakeSession()
    fake_insert_error = IntegrityError("INSERT", None, Exception(message))
    fake.flush = _raising_flush(fake, fake_insert_error)
    raised_type = MigrationEvidenceConflict if is_conflict else MigrationEvidenceBlocked
    with pytest.raises(raised_type) as raised:
        await fixtures.writer(fixtures.FakeSessionFactory(fake)).append(fixtures.write())
    assert raised.value.blockers == (expected,)


def _raising_flush(session: fixtures.FakeSession, error: Exception):
    async def flush() -> None:
        session.flush_calls += 1
        raise error

    return flush


class _RacingSession(fixtures.FakeSession):
    """Sees no stored revision, then loses the insert race at ``flush``.

    That is exactly the shape of two writers appending the same revision concurrently: the
    first load is empty for both, and the unique constraint decides the winner.
    """

    async def scalar(self, statement: Any) -> Any:
        return None

    async def flush(self) -> None:
        self.flush_calls += 1
        raise IntegrityError(
            "INSERT INTO m04_scope_promotion_evidence",
            None,
            Exception("duplicate key value violates unique constraint uq_m04_scope_promotion_revision"),
        )
