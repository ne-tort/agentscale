"""An incomplete partition set is *named*, never executed and never silently degraded.

The planner's contract is that a partition which cannot be fully assembled reports one
blocker per missing collaborator -- with the exact code an operator greps for, the dependency
at fault, and what would remove it.  Nothing here raises for absence, and nothing returns a
substitute object in place of the missing dependency.
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.partition_chain import (
    PARTITION_M04E_ARTIFACTS_UNAVAILABLE,
    PARTITION_M04E_SERVER_REPOSITORY_SESSION_BOUND,
    PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE,
    PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
    PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE,
    PARTITION_M06_SHADOW_PORTS_UNAVAILABLE,
    PARTITION_PRINCIPAL_RESOLVER_UNAVAILABLE,
    PARTITION_SESSION_FACTORY_MISSING,
    PARTITION_WRITER_GENERATION_INVALID,
    PartitionAssemblyOverrides,
    build_partition_chain,
)
from .partition_fixtures import (
    GENERATION,
    complete_overrides,
    key,
    runtime_context,
)

#: Everything a partition cannot derive.  A plan that omits all of them must name all of
#: them -- this tuple *is* the acceptance list for "the partition is still not runnable".
UNDERIVABLE_CODES = (
    PARTITION_PRINCIPAL_RESOLVER_UNAVAILABLE,
    PARTITION_M04E_ARTIFACTS_UNAVAILABLE,
    PARTITION_M04E_SERVER_REPOSITORY_SESSION_BOUND,
    PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
    PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE,
    PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE,
    PARTITION_M06_SHADOW_PORTS_UNAVAILABLE,
)


def test_a_partition_with_no_host_collaborators_names_all_seven() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        # No overrides at all: the honest shape of "someone asked for a plan".
    )

    assert plan.complete is False
    # Set equality, not "is a subset": a planner that silently dropped a blocker would let a
    # stage look runnable while a collaborator is still absent.
    assert set(plan.blocker_codes) == set(UNDERIVABLE_CODES)
    assert len(plan.blocker_codes) == len(UNDERIVABLE_CODES)


@pytest.mark.parametrize(
    ("field", "expected_code"),
    (
        ("principal_resolver", PARTITION_PRINCIPAL_RESOLVER_UNAVAILABLE),
        ("m04e_artifacts", PARTITION_M04E_ARTIFACTS_UNAVAILABLE),
        ("m04e_server_repository", PARTITION_M04E_SERVER_REPOSITORY_SESSION_BOUND),
        ("m05_runtime_context", PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE),
        ("m05_upstream_evidence", PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE),
        ("m05_identity_sessions", PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE),
        ("m06_shadow_ports", PARTITION_M06_SHADOW_PORTS_UNAVAILABLE),
    ),
)
def test_omitting_one_collaborator_yields_exactly_its_code(field: str, expected_code: str) -> None:
    overrides = complete_overrides(**{field: None})
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **overrides.chain_kwargs(),
    )

    assert plan.blocker_codes == (expected_code,)
    blocker = plan.blockers[0]
    assert blocker.dependency
    assert blocker.detail
    # "What is needed" is what makes the report actionable rather than merely red.
    assert blocker.needed.strip()


@pytest.mark.parametrize(
    ("field", "expected_dependency"),
    (
        ("principal_resolver", {"principal_resolver"}),
        ("m04e_artifacts", {"m04e_artifacts"}),
        ("m04e_server_repository", {"m04e_server_repository"}),
        ("m05_runtime_context", {"m05_runtime_context"}),
        ("m05_upstream_evidence", {"m05_upstream_evidence"}),
        ("m05_identity_sessions", {"m05_identity_sessions"}),
        ("m06_shadow_ports", {"m06_ports"}),
    ),
)
def test_each_blocker_names_the_dependency_at_fault(
    field: str, expected_dependency: set[str]
) -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides(**{field: None}).chain_kwargs(),
    )

    # The principal resolver is one collaborator behind two dependencies (M04 and M05 tenant
    # contexts); the blocker names the collaborator, and its detail names both dependents.
    principal = plan.blockers[0]
    assert set(plan.missing) == expected_dependency
    if field == "principal_resolver":
        assert "m04_tenant_context" in principal.detail
        assert "m05_tenant_context" in principal.detail


def test_no_database_means_no_durable_port_and_one_named_code() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=None,
        writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert plan.blocker_codes == (PARTITION_SESSION_FACTORY_MISSING,)
    assert plan.built == ()
    assert plan.evidence_scope is None
    # No port is fabricated in place of a database: the dependency set is empty, not partial.
    assert plan.dependencies.m04e_m05_handoff is None
    assert plan.dependencies.cutover_repository is None


@pytest.mark.parametrize("generation", (0, -1, 1.5, "3", True, None))
def test_a_partition_without_a_usable_writer_generation_is_refused(
    generation: object,
) -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=generation,  # type: ignore[arg-type]
        **complete_overrides().chain_kwargs(),
    )

    assert PARTITION_WRITER_GENERATION_INVALID in plan.blocker_codes
    # The generation is the partition's fence: without it no scope exists, so the two
    # partition-bound stores must not appear at all.
    assert plan.dependencies.m04e_m05_handoff is None
    assert plan.dependencies.m05_promotion_records is None


def test_an_unusable_runtime_context_is_reported_as_missing() -> None:
    """A ``TrustedRuntimeContext`` that cannot be used is not a context.

    ``runtime_context()`` with ``authenticated=False`` is exactly what a host would produce
    for an anonymous operator; the M05 store would refuse it, so the planner must not treat
    it as supplied.
    """

    plan = build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides(
            m05_runtime_context=runtime_context(authenticated=False)
        ).chain_kwargs(),
    )

    assert plan.blocker_codes == (PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,)


def test_blocker_order_is_stable_and_follows_the_stage_sequence() -> None:
    first = build_partition_chain(
        key=key(), session_factory=object(), writer_generation=GENERATION
    )
    second = build_partition_chain(
        key=key(), session_factory=object(), writer_generation=GENERATION
    )

    assert first.blocker_codes == second.blocker_codes


def test_the_empty_overrides_type_leaves_every_collaborator_absent() -> None:
    overrides = PartitionAssemblyOverrides()

    assert overrides.principal_resolver is None
    assert overrides.m04e_artifacts is None
    assert overrides.m04e_server_repository is None
    assert overrides.m05_runtime_context is None
    assert overrides.m05_upstream_evidence is None
    assert overrides.m05_identity_sessions is None
    assert overrides.m06_shadow_ports is None
    # Page sizes are bounded scalars with reviewed defaults, not collaborators.
    assert 1 <= overrides.m04e_page_size <= 1000
    assert 1 <= overrides.m05_page_size <= 1000


def test_a_blocker_message_says_what_to_do_not_only_what_is_wrong() -> None:
    plan = build_partition_chain(
        key=key(), session_factory=object(), writer_generation=GENERATION
    )
    rendered = str(plan.blockers[0])

    assert plan.blockers[0].code in rendered
    assert "needed:" in rendered
