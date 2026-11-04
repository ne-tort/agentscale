"""Two partitions never share state; repeated calls never change their verdict.

Both properties are structural, not stylistic:

* every partition-bound evidence store is constructed with **its own**
  ``TrustedEvidenceScope(tenant, writer_generation)``, and that scope is what decides which
  rows a store can see or write -- so identity of the scope object is the isolation;
* the outcome digest is a pure function of the verdict, so an identical repeat is
  byte-identical and a changed generation cannot masquerade as one.
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.composition import MigrationStage
from prodavan.application.migration.evidence_ports import (
    MigrationEvidenceBlocked,
    SqlM05HandoffEvidenceStore,
    SqlM05PromotionEvidenceStore,
    SqlPartitionHandoffReadStore,
    TrustedEvidenceScope,
)
from prodavan.application.migration.partition_chain import (
    PartitionChainKey,
    build_partition_chain,
    partition_key,
)
from prodavan.application.migration.partition_runner import (
    run_partition_stage,
    stage_execution_digest,
)
from .partition_fixtures import (
    DEFINITION,
    GENERATION,
    OTHER_DEFINITION,
    OTHER_TENANT,
    TENANT,
    RecordingEvidenceSource,
    RecordingSessionFactory,
    complete_overrides,
    key,
    other_key,
    promotion_record,
    ready_marker,
)


def test_two_partitions_get_different_trusted_evidence_scopes() -> None:
    alpha = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    beta = build_partition_chain(
        key=other_key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert alpha.evidence_scope is not None and beta.evidence_scope is not None
    assert alpha.evidence_scope == TrustedEvidenceScope(
        tenant_id=TENANT, writer_generation=GENERATION
    )
    assert beta.evidence_scope == TrustedEvidenceScope(
        tenant_id=OTHER_TENANT, writer_generation=GENERATION
    )
    assert alpha.evidence_scope != beta.evidence_scope
    # And the scopes really disagree about the partitions they admit:
    assert alpha.evidence_scope.admits_partition((OTHER_TENANT, OTHER_DEFINITION)) is False
    assert beta.evidence_scope.admits_partition((TENANT, DEFINITION)) is False


def test_the_same_tenant_at_another_generation_is_another_partition_scope() -> None:
    """The scope is ``(tenant, generation)``: a re-fenced tenant is not the same partition."""

    old = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    new = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION + 1,
        **complete_overrides().chain_kwargs(),
    )

    assert old.evidence_scope != new.evidence_scope
    assert old.evidence_scope is not None and new.evidence_scope is not None
    assert old.evidence_scope.writer_generation == GENERATION
    assert new.evidence_scope.writer_generation == GENERATION + 1


@pytest.mark.parametrize(
    "attribute",
    ("m04e_m05_handoff", "m05_promotion_records", "partition_handoff_read"),
)
def test_two_partitions_do_not_share_evidence_store_instances(attribute: str) -> None:
    alpha = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    beta = build_partition_chain(
        key=other_key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    alpha_store = plan_store(alpha, attribute)
    beta_store = plan_store(beta, attribute)

    assert alpha_store is not beta_store
    # Each store carries its own scope: a shared store would have to pick one tenant.
    assert alpha_store._boundaries.scope != beta_store._boundaries.scope


def plan_store(plan: object, attribute: str) -> object:
    """The partition-bound store named ``attribute`` (handoff read lives on the plan)."""

    if attribute == "partition_handoff_read":
        store = getattr(plan, "partition_handoff_read")
    else:
        store = getattr(getattr(plan, "dependencies"), attribute)
    assert store is not None
    return store


def test_a_store_built_for_one_partition_refuses_the_other_before_any_sql() -> None:
    """Isolation is enforced by the port itself, not by the caller's discipline.

    The factory records and refuses calls, so a store that opened a session before checking
    the tenant would fail the test instead of silently reading another partition.
    """

    factory = RecordingSessionFactory()
    plan = build_partition_chain(
        key=key(),
        session_factory=factory,
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    promotion = plan.dependencies.m05_promotion_records
    assert isinstance(promotion, SqlM05PromotionEvidenceStore)

    import asyncio

    async def attempt() -> None:
        await promotion.load_latest(
            tenant_id=OTHER_TENANT, app_definition_id=OTHER_DEFINITION
        )

    with pytest.raises(MigrationEvidenceBlocked):
        asyncio.run(attempt())
    assert factory.calls == 0


def test_stores_are_the_documented_implementations_for_each_port() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert isinstance(plan.dependencies.m04e_m05_handoff, SqlM05HandoffEvidenceStore)
    assert isinstance(plan.dependencies.m05_promotion_records, SqlM05PromotionEvidenceStore)
    assert isinstance(plan.partition_handoff_read, SqlPartitionHandoffReadStore)


# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------


async def test_a_dry_run_is_idempotent() -> None:
    """Same key, stage and generation -> the same verdict and the same digest."""

    args = dict(
        key=key(),
        stage=MigrationStage.M04E_DEFINITION_VERSION,
        session_factory=object(),
        writer_generation=GENERATION,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(),
    )
    first = await run_partition_stage(**args)  # type: ignore[arg-type]
    second = await run_partition_stage(**args)  # type: ignore[arg-type]

    assert first.dry_run is True and second.dry_run is True
    assert first.executed is False and second.executed is False
    assert first.snapshot() == second.snapshot()
    assert first.digest == second.digest
    assert stage_execution_digest(first) == stage_execution_digest(second)


async def test_a_dry_run_does_not_write_and_repeats_without_side_effects() -> None:
    factory = RecordingSessionFactory()
    source = RecordingEvidenceSource(marker=ready_marker())
    args = dict(
        key=key(),
        stage=MigrationStage.M05_INSTANCE_RUNTIME,
        session_factory=factory,
        writer_generation=GENERATION,
        overrides=complete_overrides(),
        evidence_source=source,
    )
    for _ in range(3):
        outcome = await run_partition_stage(**args)  # type: ignore[arg-type]
        assert outcome.executed is False

    # A dry run reads evidence, and the factory is never called: three repeats, no session.
    assert factory.calls == 0
    assert source.reads == ["latest_handoff_marker"] * 3


async def test_a_changed_generation_changes_the_verdict_digest() -> None:
    source = RecordingEvidenceSource(promotion=promotion_record())
    base = dict(
        key=key(),
        stage=MigrationStage.M06_GRANTS_GATEWAY,
        session_factory=object(),
        overrides=complete_overrides(),
        evidence_source=source,
    )
    at_three = await run_partition_stage(writer_generation=GENERATION, **base)  # type: ignore[arg-type]
    at_four = await run_partition_stage(writer_generation=GENERATION + 1, **base)  # type: ignore[arg-type]

    assert at_three.executable is True
    assert at_three.digest != at_four.digest
    # Generation 4 sees generation-3 evidence: stale, and it must say so rather than pass.
    assert at_four.blocked("partition_writer_generation_stale")


async def test_a_refused_real_run_repeats_identically_and_writes_nothing() -> None:
    factory = RecordingSessionFactory()
    args = dict(
        key=key(),
        stage=MigrationStage.CUTOVER,
        session_factory=factory,
        writer_generation=GENERATION,
        dry_run=False,
        overrides=complete_overrides(),
        evidence_source=RecordingEvidenceSource(
            m06_state=_absent_m06(),
        ),
    )
    first = await run_partition_stage(**args)  # type: ignore[arg-type]
    second = await run_partition_stage(**args)  # type: ignore[arg-type]

    assert first.executed is False and second.executed is False
    assert first.blocker_codes == second.blocker_codes
    assert first.digest == second.digest
    assert factory.calls == 0


def _absent_m06() -> object:
    from prodavan.application.migration.partition_runner import ScopedEvidenceReadiness

    return ScopedEvidenceReadiness(present=False)


def test_partition_key_coercion_is_total_for_pairs_and_keys() -> None:
    existing = key()

    assert partition_key(existing) is existing
    assert partition_key((TENANT, DEFINITION)) == PartitionChainKey(
        tenant_id=TENANT, app_definition_id=DEFINITION
    )
    with pytest.raises(ValueError):
        partition_key((TENANT,))
    with pytest.raises(ValueError):
        partition_key(object())
