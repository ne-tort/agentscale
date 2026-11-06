"""MIG-EVIDENCE-M04READ-01: the M04-02 scope link of ``read_partition``.

Before this task ``SqlPartitionHandoffReadStore.read_partition`` appended
``DECLARED_READ_BLOCKERS`` **unconditionally**, so every partition refused with
``m04_scope_promotion_evidence_missing`` even when all other links were healthy. Revision
``2026110313`` had already created ``m04_scope_promotion_evidence`` (RLS policy from
``2026110314``), so the refusal was stale.

These cases pin the replacement, which is a *conditional* read:

* a ``ready`` row attests the partition and adds **no** blocker;
* no row -> ``m04_scope_promotion_evidence_missing``;
* ``blocked``/``quarantined`` -> ``m04_scope_evidence_not_ready``;
* a digest that disagrees with the M05 ``M04_scope`` gate -> ``m04_scope_evidence_digest_
  mismatch``;
* a row of another tenant/scope/registry is not consumable, and the emitted statement
  never widens past the bound tenant;
* the trusted tenant context is mandatory and proved before any SQL.

None of these cases touches a database: the freeze allows reasoning about contracts, so
the statement shape is inspected on the compiled SQL and the row decisions are exercised
through the pure reviewer.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from prodavan.application.migration.evidence_ports import (
    DECLARED_READ_BLOCKERS,
    M04_SCOPE_EVIDENCE_DIGEST_MISMATCH,
    M04_SCOPE_EVIDENCE_MISSING,
    M04_SCOPE_EVIDENCE_NOT_READY,
    M04_SCOPE_EVIDENCE_DIGEST_COLUMNS,
    M04_OWNER_TABLES,
    SCHEMA_BLOCKERS,
    MigrationEvidenceBlocked,
    PartitionParts,
    ReviewedM04RlsEvidence,
    SqlPartitionHandoffReadStore,
    TrustedEvidenceScope,
    assemble_partition_evidence,
    m04_scope_anchor,
    review_m04_scope_evidence,
)
from prodavan.application.migration.m06_grants_gateway.adapter import schema_identity_digest
from prodavan.application.migration.m06_grants_gateway.handoff import AcceptedSourceKey

from .acceptance_fixtures import (
    DEFINITION,
    INSTANCE,
    M04_SCOPE_DIGEST,
    M04_SCOPE_REGISTRY_ID,
    M04_SCOPE_ROW_ID,
    TENANT,
    VERSION,
    ExplodingSessionFactory,
    RecordingStatementSession,
    marker,
    promotion_record,
    scope_evidence_row,
)

SCOPE = TrustedEvidenceScope(tenant_id=TENANT, writer_generation=3)
OTHER_TENANT = "tenant-other"


def _store(factory=None) -> SqlPartitionHandoffReadStore:
    return SqlPartitionHandoffReadStore(
        session_factory=factory or ExplodingSessionFactory(), scope=SCOPE
    )


def _reviewed_rls() -> ReviewedM04RlsEvidence:
    return ReviewedM04RlsEvidence(
        review_id="m04-rls-review-1",
        database_role="prodavan_service",
        policy_digests=tuple(
            (table, "sha256:" + format(index, "x") * 64)
            for index, table in enumerate(M04_OWNER_TABLES, start=1)
        ),
    )


def _tenant_anchor():
    return m04_scope_anchor(tenant_id=TENANT, cabinet_id=None, project_id=None)


def _review(**overrides):
    arguments = {
        "row": scope_evidence_row(),
        "tenant_id": TENANT,
        "anchor": _tenant_anchor(),
        "scope_registry_id": M04_SCOPE_REGISTRY_ID,
        "expected_digest": M04_SCOPE_DIGEST,
    }
    arguments.update(overrides)
    return review_m04_scope_evidence(**arguments)


# ---------------------------------------------------------------------------
# A ready row is consumed; every other row is a named refusal
# ---------------------------------------------------------------------------


def test_a_ready_row_is_consumed_and_adds_no_blocker():
    blockers, row_id, digest = _review()
    assert blockers == ()
    assert row_id == M04_SCOPE_ROW_ID
    assert digest == M04_SCOPE_DIGEST


def test_an_absent_row_is_the_missing_evidence_refusal():
    assert _review(row=None) == ((M04_SCOPE_EVIDENCE_MISSING,), "", "")


@pytest.mark.parametrize("outcome", ["blocked", "quarantined"])
def test_a_non_ready_row_records_a_refusal_and_is_not_accepted(outcome):
    """Only ``ready`` is acceptance: the other stored outcomes are a recorded refusal."""
    blockers, row_id, digest = _review(row=scope_evidence_row(outcome=outcome))
    assert blockers == (M04_SCOPE_EVIDENCE_NOT_READY,)
    assert row_id == "" and digest == ""


def test_an_outcome_outside_the_check_constraint_is_not_ready_either():
    """A row that somehow escaped the check constraint never becomes acceptance."""
    assert _review(row=scope_evidence_row(outcome="promotable"))[0] == (
        M04_SCOPE_EVIDENCE_NOT_READY,
    )


def test_a_row_digest_that_differs_from_the_m05_gate_is_a_mismatch():
    blockers, _, _ = _review(expected_digest="sha256:" + "0" * 64)
    assert blockers == (M04_SCOPE_EVIDENCE_DIGEST_MISMATCH,)


@pytest.mark.parametrize("column", M04_SCOPE_EVIDENCE_DIGEST_COLUMNS)
def test_a_malformed_digest_column_is_a_mismatch(column):
    blockers, _, _ = _review(row=scope_evidence_row(**{column: "not-a-digest"}))
    assert blockers == (M04_SCOPE_EVIDENCE_DIGEST_MISMATCH,)


def test_an_absent_expected_digest_does_not_invent_a_second_refusal():
    """``m04_scope_evidence_digest_missing`` already refuses upstream; no duplicate."""
    blockers, row_id, _ = _review(expected_digest=None)
    assert blockers == ()
    assert row_id == M04_SCOPE_ROW_ID


# ---------------------------------------------------------------------------
# A foreign partition is not consumable
# ---------------------------------------------------------------------------


def test_a_row_of_another_tenant_is_refused():
    blockers, row_id, _ = _review(row=scope_evidence_row(tenant_id=OTHER_TENANT))
    assert blockers == ("m04_scope_evidence_tenant_mismatch",)
    assert row_id == ""


@pytest.mark.parametrize(
    "overrides",
    [
        {"scope_kind": "cabinet", "scope_id": "cabinet-1", "cabinet_id": "cabinet-1"},
        {"scope_kind": "project", "scope_id": "project-1", "cabinet_id": "cabinet-1",
         "project_id": "project-1"},
        {"scope_id": "tenant-other"},
    ],
)
def test_a_row_of_another_scope_is_refused(overrides):
    blockers, row_id, _ = _review(row=scope_evidence_row(**overrides))
    assert blockers == ("m04_scope_evidence_scope_shape_mismatch",)
    assert row_id == ""


def test_a_row_bound_to_another_registry_row_is_refused():
    blockers, row_id, _ = _review(scope_registry_id="scope-registry-other")
    assert blockers == ("m04_scope_evidence_registry_mismatch",)
    assert row_id == ""


def test_a_partition_without_a_typed_owner_anchor_is_refused():
    """A plugin-instance-only anchor cannot own registry evidence (M04-02 refuses it)."""
    blockers, row_id, _ = _review(anchor=None)
    assert blockers == ("m04_scope_evidence_scope_shape_mismatch",)
    assert row_id == ""


@pytest.mark.parametrize(
    "overrides,expected",
    [
        ({"cabinet_id": None, "project_id": None}, ("tenant", TENANT, None, None)),
        ({"cabinet_id": "cabinet-1", "project_id": None},
         ("cabinet", "cabinet-1", "cabinet-1", None)),
        ({"cabinet_id": "cabinet-1", "project_id": "project-1"},
         ("project", "project-1", "cabinet-1", "project-1")),
        ({"cabinet_id": None, "project_id": "project-1"}, None),
        ({"cabinet_id": "  ", "project_id": None}, None),
    ],
)
def test_the_owner_anchor_is_the_typed_scope_the_instance_is_installed_at(overrides, expected):
    assert m04_scope_anchor(tenant_id=TENANT, **overrides) == expected


def test_a_blank_tenant_has_no_anchor():
    assert m04_scope_anchor(tenant_id="  ", cabinet_id=None, project_id=None) is None


# ---------------------------------------------------------------------------
# The emitted statement stays inside the bound tenant
# ---------------------------------------------------------------------------


async def test_the_m04_scope_statement_is_tenant_and_scope_bound_and_takes_the_latest():
    session = RecordingStatementSession(row=scope_evidence_row())
    row = await _store()._load_m04_scope_evidence_row(  # noqa: SLF001 - statement shape
        session, TENANT, scope_kind="tenant", scope_id=TENANT
    )
    assert row is not None
    compiled = session.compiled()
    assert "m04_scope_promotion_evidence" in compiled
    for predicate in (
        "m04_scope_promotion_evidence.tenant_id = ",
        "m04_scope_promotion_evidence.scope_kind = ",
        "m04_scope_promotion_evidence.scope_id = ",
    ):
        assert predicate in compiled, predicate
    assert "ORDER BY m04_scope_promotion_evidence.recorded_at DESC" in compiled
    assert "m04_scope_promotion_evidence.revision_id DESC" in compiled
    assert "LIMIT" in compiled.upper()


async def test_the_m04_scope_read_returns_none_when_the_bound_tenant_has_no_row():
    session = RecordingStatementSession(row=None)
    assert await _store()._load_m04_scope_evidence_row(  # noqa: SLF001
        session, TENANT, scope_kind="tenant", scope_id=TENANT
    ) is None


# ---------------------------------------------------------------------------
# The unconditional blocker is gone; the conditional wiring is in read_partition
# ---------------------------------------------------------------------------


def test_read_partition_no_longer_appends_the_declared_blockers_unconditionally():
    source = inspect.getsource(SqlPartitionHandoffReadStore.read_partition)
    assert "DECLARED_READ_BLOCKERS" not in source
    assert "blockers.extend(DECLARED_READ_BLOCKERS)" not in source
    module_source = inspect.getsource(
        inspect.getmodule(SqlPartitionHandoffReadStore)
    )
    assert "blockers.extend(DECLARED_READ_BLOCKERS)" not in module_source


def test_read_partition_reads_and_reviews_the_m04_scope_row():
    source = inspect.getsource(SqlPartitionHandoffReadStore.read_partition)
    assert "_load_m04_scope_evidence_row" in source
    assert "m04_scope_anchor" in source
    assert "review_m04_scope_evidence" in source
    assert "m04_scope_evidence_row_id=scope_row_id" in source
    assert "m04_scope_evidence_digest=scope_digest" in source
    # The scope read happens before the single refusal return, so its blockers refuse.
    assert source.index("review_m04_scope_evidence") < source.index(
        "return PartitionHandoffRead(None, tuple(blockers))"
    )


def test_the_other_four_reads_are_unchanged():
    """M05 record, M04E marker, instance and M06 row keep their own checks."""
    source = inspect.getsource(SqlPartitionHandoffReadStore)
    for blocker in (
        "m05_promotion_evidence_missing",
        "m05_promotion_stale_writer_generation",
        "m04e_gate_evidence_missing",
        "m04e_handoff_marker_missing",
        "plugin_instance_evidence_missing",
        "plugin_instance_not_published",
        "typed_scope_anchor_missing",
        "m06_promotion_evidence_missing",
        "m06_writer_epoch_mismatch",
        "m06_promotion_not_ready",
    ):
        assert blocker in source, blocker


def test_declared_blockers_are_the_three_conditional_m04_refusals():
    joined = " ".join(DECLARED_READ_BLOCKERS)
    for expected in (
        M04_SCOPE_EVIDENCE_MISSING,
        M04_SCOPE_EVIDENCE_NOT_READY,
        M04_SCOPE_EVIDENCE_DIGEST_MISMATCH,
    ):
        assert any(item.startswith(expected) for item in DECLARED_READ_BLOCKERS), expected
        assert expected in joined
    assert len(DECLARED_READ_BLOCKERS) == 3
    # And every one of them is actually raised by the reviewer, not just declared.
    reviewer_source = inspect.getsource(review_m04_scope_evidence)
    for name in (
        "M04_SCOPE_EVIDENCE_MISSING",
        "M04_SCOPE_EVIDENCE_NOT_READY",
        "M04_SCOPE_EVIDENCE_DIGEST_MISMATCH",
    ):
        assert name in reviewer_source, name


def test_schema_blockers_no_longer_claim_the_table_is_missing():
    joined = " ".join(SCHEMA_BLOCKERS)
    assert "no registered table stores the accepted M04" not in joined
    for open_gap in (
        "promotion_outcome_cannot_express_ready",
        "m04_scope_evidence_writer_absent",
        "m05_promotion_record_carries_no_source_inventory",
        "m05_writer_generation_zero_has_no_m06_epoch",
        "m04e_backfill_has_no_checkpoint_or_source_mapping_table",
    ):
        assert open_gap in joined, open_gap


def test_the_consumed_table_is_a_registered_model():
    from prodavan.infrastructure.persistence.models.base import Base

    assert "m04_scope_promotion_evidence" in set(Base.metadata.tables)
    module = inspect.getmodule(SqlPartitionHandoffReadStore)
    assert module is not None
    tree = ast.parse(inspect.getsource(module))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert "M04ScopePromotionEvidenceRow" in imported


# ---------------------------------------------------------------------------
# Trusted tenant context is mandatory for the new read as for the others
# ---------------------------------------------------------------------------


def test_the_store_refuses_a_blank_trusted_tenant():
    with pytest.raises(ValueError, match="resolved tenant"):
        TrustedEvidenceScope(tenant_id="   ")


async def test_a_foreign_tenant_never_reaches_the_m04_scope_read():
    factory = ExplodingSessionFactory()
    result = await _store(factory).read_partition(
        tenant_id=OTHER_TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        plugin_instance_id=INSTANCE,
        writer_generation=3,
    )
    assert result.accepted is False
    assert result.blockers == ("partition_tenant_outside_trusted_scope",)
    # No blocker mentions M04 scope evidence: the request was refused for its tenant, and
    # the stale unconditional blocker is not part of the vocabulary any more.
    assert not any("m04_scope_promotion_evidence_missing" in item for item in result.blockers)
    assert factory.calls == 0


async def test_a_missing_writer_epoch_refuses_before_any_sql():
    factory = ExplodingSessionFactory()
    result = await _store(factory).read_partition(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        plugin_instance_id=INSTANCE,
        writer_generation=0,
    )
    assert result.blockers == ("writer_epoch_not_positive",)
    assert factory.calls == 0


# ---------------------------------------------------------------------------
# Assembly consumes the stored M04-02 row, not a placeholder
# ---------------------------------------------------------------------------


def _parts(**overrides) -> PartitionParts:
    values = {
        "m05_record": promotion_record(),
        "m04e_marker": marker(),
        "m04e_marker_row_id": "sha256:" + "1" * 64,
        "instance_row_id": INSTANCE,
        "scope_registry_id": M04_SCOPE_REGISTRY_ID,
        "instance_version_id": VERSION,
        "accepted_sources": (
            AcceptedSourceKey("legacy_app_grant", "grant-a", "grantset-v1", M04_SCOPE_DIGEST),
        ),
        "m06_row_id": "m06promo-evidence-1",
        "m06_schema_identity_digest": schema_identity_digest(),
        "m04_rls_evidence": _reviewed_rls(),
        "m04_scope_evidence_row_id": M04_SCOPE_ROW_ID,
        "m04_scope_evidence_digest": M04_SCOPE_DIGEST,
    }
    values.update(overrides)
    return PartitionParts(**values)


def _assemble(**overrides):
    return assemble_partition_evidence(
        tenant_id=TENANT,
        app_definition_id=DEFINITION,
        app_version_id=VERSION,
        plugin_instance_id=INSTANCE,
        writer_generation=3,
        parts=_parts(**overrides),
    )


def test_a_complete_partition_assembles_from_the_stored_scope_row():
    evidence = _assemble()
    assert evidence.m04_scope_digest == M04_SCOPE_DIGEST
    assert evidence.m04_scope_evidence_id == M04_SCOPE_REGISTRY_ID
    assert evidence.accepted_sources


@pytest.mark.parametrize(
    "overrides,blocker",
    [
        ({"m04_scope_evidence_row_id": ""}, M04_SCOPE_EVIDENCE_MISSING),
        ({"m04_scope_evidence_row_id": "   "}, M04_SCOPE_EVIDENCE_MISSING),
        ({"m04_scope_evidence_digest": ""}, M04_SCOPE_EVIDENCE_DIGEST_MISMATCH),
        ({"m04_scope_evidence_digest": "sha256:" + "0" * 64},
         M04_SCOPE_EVIDENCE_DIGEST_MISMATCH),
    ],
)
def test_assembly_refuses_a_partition_without_a_consumable_scope_row(overrides, blocker):
    with pytest.raises(MigrationEvidenceBlocked) as raised:
        _assemble(**overrides)
    assert blocker in raised.value.blockers
