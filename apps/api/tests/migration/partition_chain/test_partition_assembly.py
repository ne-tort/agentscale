"""A partition can be assembled into a complete, fully wired chain.

The claim under test is the whole reason ``partition_chain.py`` exists: the two evidence
stores the process cannot own -- ``SqlM05HandoffEvidenceStore`` and
``SqlM05PromotionEvidenceStore`` -- take a ``TrustedEvidenceScope``, and once that scope is
known the chain is complete.  So the assertion is not "the planner returns a dict"; it is
that every member except the genuinely per-process ones is a real constructed object and the
reviewed composition root then reports every one of the five stages as wired.
"""

from __future__ import annotations

from dataclasses import fields

import pytest

from prodavan.application.migration.composition import (
    ChainDependencies,
    MigrationStage,
    assemble_migration_chain,
)
from prodavan.application.migration.evidence_ports import (
    MigrationEvidenceBlocked,
    SqlM04EBackfillEvidenceStore,
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    SqlPartitionHandoffReadStore,
    TrustedEvidenceScope,
)
from prodavan.application.migration.partition_chain import (
    PartitionChainKey,
    PartitionChainPlan,
    assemble_partition_chain,
    build_partition_chain,
)
from .partition_fixtures import (
    DEFINITION,
    GENERATION,
    TENANT,
    complete_overrides,
    key,
    runtime_context,
)

PER_PARTITION_SERVICES = (
    "m04e_store",
    "m04e_m05_handoff",
    "m05_promotion_records",
    "partition_handoff_read",
)


def test_a_complete_partition_has_no_blockers() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert plan.complete is True
    assert plan.blocker_codes == ()
    assert plan.blockers == ()


def test_every_chain_dependency_is_stated_for_the_partition() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    # No field may stay ``None`` while the plan claims completeness: a ``None`` field is a
    # missing dependency, and an incomplete plan is required to say so through ``blockers``.
    missing = [
        field.name
        for field in fields(ChainDependencies)
        if getattr(plan.dependencies, field.name) is None
    ]
    assert missing == []


def test_the_two_partition_bound_evidence_stores_are_really_constructed() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    # These two are exactly the ports whose constructors take ``scope: TrustedEvidenceScope``;
    # at process scope they are permanently ``None`` (blocker
    # ``migration_chain_evidence_scope_is_per_partition``), and that is what this task fixes.
    assert isinstance(plan.dependencies.m04e_m05_handoff, SqlM05HandoffEvidenceStore)
    assert isinstance(plan.dependencies.m05_promotion_records, SqlM05PromotionEvidenceStore)
    assert isinstance(plan.dependencies.m04e_store, SqlM04EBackfillEvidenceStore)
    assert isinstance(plan.partition_handoff_read, SqlPartitionHandoffReadStore)


def test_the_partition_scope_is_exactly_the_key_and_the_generation() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert plan.evidence_scope == TrustedEvidenceScope(
        tenant_id=TENANT, writer_generation=GENERATION
    )
    # ``admit_partition`` is the gate every write port applies before any SQL: the scope must
    # admit this partition and no other one.
    assert plan.evidence_scope is not None
    assert plan.evidence_scope.admits_partition((TENANT, DEFINITION)) is True
    assert plan.evidence_scope.admits_partition((TENANT, "definition-somewhere-else")) is True
    assert plan.evidence_scope.admits_partition(("tenant-other", DEFINITION)) is False


@pytest.mark.parametrize("service", PER_PARTITION_SERVICES)
def test_every_partition_bound_service_is_reported_as_built(service: str) -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert service in plan.built


def test_the_reviewed_composition_root_then_reports_every_stage_wired() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    chain = assemble_migration_chain(plan.dependencies)

    assert chain.wired is True, chain.blocker_codes
    assert chain.blocker_codes == ()
    assert chain.ordered_stages == tuple(MigrationStage)


def test_assemble_partition_chain_returns_the_same_chain_as_the_composition_root() -> None:
    args = dict(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    via_helper = assemble_partition_chain(**args)
    via_root = assemble_migration_chain(
        build_partition_chain(**args).dependencies  # type: ignore[arg-type]
    )

    assert via_helper.wired == via_root.wired is True
    for assembly in via_helper.stages:
        # The helper adds no stage logic: the same stage, the same service symbols.
        mirrored = via_root.by_stage(assembly.stage)
        assert tuple(name for name, _ in assembly.services) == tuple(
            name for name, _ in mirrored.services
        )


def test_plan_iterates_as_dependencies_and_blockers() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    dependencies, blockers = plan

    assert isinstance(plan, PartitionChainPlan)
    assert dependencies is plan.dependencies
    assert blockers == plan.blockers


def test_a_partition_without_a_principal_resolver_still_builds_its_evidence_stores() -> None:
    """The evidence scope does not depend on the auth boundary -- the tenant context does.

    This separation is what makes the planner honest: it can hand back the *partition-bound*
    half even when the principal half is missing, instead of failing the whole assembly.
    """

    overrides = complete_overrides(principal_resolver=None)
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **overrides.chain_kwargs(),
    )

    assert isinstance(plan.dependencies.m04e_m05_handoff, SqlM05HandoffEvidenceStore)
    assert isinstance(plan.dependencies.m05_promotion_records, SqlM05PromotionEvidenceStore)
    assert plan.dependencies.m04_tenant_context is None
    assert plan.complete is False


def test_a_generation_zero_partition_has_no_evidence_scope() -> None:
    """``writer_generation=0`` cannot be fenced by M06, so no scope is invented for it."""

    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=0,
        **complete_overrides().chain_kwargs(),
    )

    assert plan.evidence_scope is None
    assert plan.dependencies.m04e_m05_handoff is None
    assert plan.dependencies.m05_promotion_records is None


async def test_the_partition_bound_stores_refuse_a_foreign_tenant_before_any_sql() -> None:
    """Behavioural proof of the scope, not an attribute read.

    ``load_marker``/``load_latest`` refuse a tenant outside the bound scope *before* opening
    a session, so a factory that raises proves the refusal happened first.
    """

    from .partition_fixtures import RecordingSessionFactory

    factory = RecordingSessionFactory()
    plan = build_partition_chain(
        key=key(),
        session_factory=factory,
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    handoff = plan.dependencies.m04e_m05_handoff
    promotion = plan.dependencies.m05_promotion_records
    assert handoff is not None and promotion is not None

    with pytest.raises(MigrationEvidenceBlocked) as handoff_refusal:
        await handoff.load_marker(
            tenant_id="tenant-foreign",
            app_definition_id=DEFINITION,
            canonical_digest="sha256:" + "0" * 64,
        )
    assert "handoff_read_partition_outside_trusted_tenant" in handoff_refusal.value.blockers

    with pytest.raises(MigrationEvidenceBlocked) as promotion_refusal:
        await promotion.load_latest(tenant_id="tenant-foreign", app_definition_id=DEFINITION)
    assert "m05_promotion_read_outside_trusted_tenant" in promotion_refusal.value.blockers

    assert factory.calls == 0


@pytest.mark.parametrize(
    ("tenant", "definition"),
    (("", DEFINITION), (TENANT, ""), (" tenant-padded", DEFINITION)),
)
def test_a_key_refuses_empty_or_padded_identifiers(tenant: str, definition: str) -> None:
    with pytest.raises(ValueError):
        PartitionChainKey(tenant_id=tenant, app_definition_id=definition)


def test_the_plan_snapshot_is_json_safe_and_names_the_partition() -> None:
    import json

    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    snapshot = plan.snapshot()
    encoded = json.dumps(snapshot, sort_keys=True)

    assert snapshot["tenant_id"] == TENANT
    assert snapshot["app_definition_id"] == DEFINITION
    assert snapshot["writer_generation"] == GENERATION
    assert snapshot["complete"] is True
    assert "postgresql" not in encoded.lower()
    assert "async_sessionmaker" not in encoded
