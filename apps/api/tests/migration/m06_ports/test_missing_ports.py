"""Refusal when a required port is absent: one named code per hole.

Task ``MIG-M06-PORTS-01``.

The rule the whole migration keeps: an incomplete set is **data**, never an
exception and never a substitute.  Each test below removes exactly one
collaborator and asserts three things:

1. the plan is incomplete and carries that field's own code;
2. ``ports`` is ``None`` — no partial ``ShadowGatewayPorts`` is ever handed out;
3. nothing was quietly filled in — the field is not listed in ``built``.
"""

from __future__ import annotations

import pytest

from prodavan.application.migration.m06_grants_gateway.ports_assembly import (
    BLOCKER_CANONICAL_GRANTS_UNAVAILABLE,
    BLOCKER_COMPANY_UNAVAILABLE,
    BLOCKER_EVIDENCE_READ_UNAVAILABLE,
    BLOCKER_EVIDENCE_TRANSACTION_UNAVAILABLE,
    BLOCKER_HANDOFF_PARTITIONS_MISSING,
    BLOCKER_LEGACY_QUOTA_UNAVAILABLE,
    BLOCKER_LEGACY_SCOPE_UNAVAILABLE,
    BLOCKER_M04E_DEFINITION_VERSIONS_MISSING,
    BLOCKER_M04_SCOPE_UNAVAILABLE,
    BLOCKER_M05_INSTANCES_UNAVAILABLE,
    BLOCKER_M05_UPSTREAM_MISSING,
    BLOCKER_METHOD_CONFIGURATION_INCONSISTENT,
    BLOCKER_METHOD_CONFIGURATION_MISSING,
    BLOCKER_METHOD_MAPPINGS_MISSING,
    BLOCKER_PARTITION_INVALID,
    BLOCKER_SESSION_FACTORY_MISSING,
    FIELD_BLOCKERS,
    build_shadow_ports,
)

from .acceptance_fixtures import (
    PARTITION,
    WRITER_GENERATION,
    Inert,
    RefusingSessionFactory,
    definition_repository,
    full_ports,
    method_configuration,
    method_mappings,
)


def _kwargs(**overrides):
    """The stated half of ``build_shadow_ports`` with one field overridden."""

    servers, versions = method_configuration()
    values = {
        "session_factory": RefusingSessionFactory(),
        "partition": PARTITION,
        "writer_generation": WRITER_GENERATION,
        "m04_tenant_context": Inert(),
        "m05_tenant_context": Inert(),
        "m04e_definition_versions": definition_repository(),
        "m05_upstream": Inert(),
        "legacy_quota": Inert(),
        "method_mappings": method_mappings(),
        "method_servers": servers,
        "method_versions": versions,
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# One missing field at a time
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keyword", "code", "field_name"),
    (
        # Derived from the factory or the partition; ablated at the source.
        ("session_factory", BLOCKER_SESSION_FACTORY_MISSING, None),
        ("m04_tenant_context", BLOCKER_M04_SCOPE_UNAVAILABLE, "m04_scope"),
        ("m05_tenant_context", BLOCKER_M05_INSTANCES_UNAVAILABLE, "m05_instances"),
        ("m05_upstream", BLOCKER_M05_UPSTREAM_MISSING, "m05_upstream"),
        ("m04e_definition_versions", BLOCKER_M04E_DEFINITION_VERSIONS_MISSING,
         "m04e_definition_versions"),
        ("legacy_quota", BLOCKER_LEGACY_QUOTA_UNAVAILABLE, "legacy_quota"),
        ("method_mappings", BLOCKER_METHOD_MAPPINGS_MISSING, "method_mappings"),
        # Stated absence of a port that would otherwise be derived.
        ("company", BLOCKER_COMPANY_UNAVAILABLE, "company"),
        ("canonical_grants", BLOCKER_CANONICAL_GRANTS_UNAVAILABLE, "canonical_grants"),
        ("evidence_read", BLOCKER_EVIDENCE_READ_UNAVAILABLE, "evidence_read"),
        ("evidence_transaction", BLOCKER_EVIDENCE_TRANSACTION_UNAVAILABLE,
         "evidence_transaction"),
        ("partition", BLOCKER_HANDOFF_PARTITIONS_MISSING, "handoff_partitions"),
    ),
)
def test_a_missing_required_port_is_a_named_blocker_and_no_port_set(
    keyword: str, code: str, field_name: str | None
) -> None:
    plan = build_shadow_ports(**_kwargs(**{keyword: None}))

    assert plan.ports is None, "a partial port set must never be handed out"
    assert plan.complete is False
    assert code in plan.blockers
    if field_name is not None:
        assert field_name not in plan.built, f"{field_name} was filled in silently"


def test_a_missing_legacy_scope_is_named_not_substituted() -> None:
    """The legacy scope port is only derivable from the factory, so ablate that."""

    plan = build_shadow_ports(**_kwargs(session_factory=None, legacy_ports=None))

    assert plan.ports is None
    assert BLOCKER_LEGACY_SCOPE_UNAVAILABLE in plan.blockers


def test_a_bundle_without_its_scope_reports_the_scope_hole() -> None:
    """A host that states a bundle is trusted for what the bundle actually holds."""

    class HalfBundle:
        repository = Inert()
        scope = None

    plan = build_shadow_ports(**_kwargs(legacy_ports=HalfBundle(), session_factory=None))

    assert plan.ports is None
    assert BLOCKER_LEGACY_SCOPE_UNAVAILABLE in plan.blockers


# ---------------------------------------------------------------------------
# Method configuration is one decision, not three
# ---------------------------------------------------------------------------


def test_missing_method_configuration_is_its_own_code() -> None:
    plan = build_shadow_ports(**_kwargs(method_servers=None, method_versions=None))

    assert plan.ports is None
    assert BLOCKER_METHOD_CONFIGURATION_MISSING in plan.blockers


def test_empty_method_configuration_is_refused_rather_than_denying_every_method() -> None:
    """An empty pair would make every method unapproved — a policy answer, not a hole."""

    plan = build_shadow_ports(**_kwargs(method_servers={}, method_versions={}))

    assert plan.ports is None
    assert BLOCKER_METHOD_CONFIGURATION_MISSING in plan.blockers


def test_lopsided_method_configuration_is_inconsistent() -> None:
    """``ApprovedMethodOperationAdapter`` requires one server *and* one version."""

    plan = build_shadow_ports(**_kwargs(method_versions={}))

    assert plan.ports is None
    assert BLOCKER_METHOD_CONFIGURATION_INCONSISTENT in plan.blockers


def test_blank_method_configuration_entry_is_inconsistent() -> None:
    plan = build_shadow_ports(**_kwargs(method_servers={"kv.put": "  "}))

    assert plan.ports is None
    assert BLOCKER_METHOD_CONFIGURATION_INCONSISTENT in plan.blockers


# ---------------------------------------------------------------------------
# Partition
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "partition",
    (
        ("", "definition"),
        ("tenant", ""),
        (" tenant", "definition"),
        ("only-one",),
        "not-a-pair",
    ),
)
def test_a_malformed_partition_is_a_code_not_an_exception(partition: object) -> None:
    plan = build_shadow_ports(**_kwargs(partition=partition))

    assert plan.ports is None
    assert BLOCKER_PARTITION_INVALID in plan.blockers
    assert BLOCKER_HANDOFF_PARTITIONS_MISSING in plan.blockers


@pytest.mark.parametrize("generation", (0, -1, None, True, 1.5, "3"))
def test_a_writer_generation_that_cannot_form_a_scope_is_a_code(generation: object) -> None:
    plan = build_shadow_ports(**_kwargs(writer_generation=generation))

    assert plan.ports is None
    assert BLOCKER_PARTITION_INVALID in plan.blockers


def test_no_partition_at_all_is_reported_as_the_handoff_hole() -> None:
    """A process has no partition; that is exactly why it cannot own this port."""

    plan = build_shadow_ports(**_kwargs(partition=None))

    assert plan.ports is None
    assert BLOCKER_HANDOFF_PARTITIONS_MISSING in plan.blockers
    # The partition itself is not "invalid" — it was never stated.
    assert BLOCKER_PARTITION_INVALID not in plan.blockers


# ---------------------------------------------------------------------------
# No exception, no stub, no silently satisfied field
# ---------------------------------------------------------------------------


def test_nothing_raises_and_no_blocker_repeats() -> None:
    plan = build_shadow_ports()

    assert plan.ports is None
    assert len(plan.blockers) == len(set(plan.blockers))
    assert all(" " not in code for code in plan.blockers), "blockers are codes, not prose"


def test_every_required_field_has_its_own_code() -> None:
    """The vocabulary is exhaustive: no required field is unrepresented.

    ``method_servers``/``method_versions`` are the one deliberate pair: they are two
    dataclass fields but **one** decision, because
    ``ApprovedMethodOperationAdapter`` refuses a mapping without exactly one server
    *and* one tool version.  Splitting them into two codes would invite a host to
    "fix" one half and leave the set denying every method, so one
    configuration code names the pair.
    """

    declared = {name for name, _code in FIELD_BLOCKERS}
    from dataclasses import fields as dataclass_fields

    from prodavan.application.migration.m06_grants_gateway.composition import (
        ShadowGatewayPorts,
    )

    all_fields = {field.name for field in dataclass_fields(ShadowGatewayPorts)}
    assert declared == all_fields - {"protocol", "method_servers", "method_versions"}, (
        "every non-optional ShadowGatewayPorts field must map to one blocker code"
    )


def test_the_empty_call_reports_every_structural_hole_at_once() -> None:
    """A bare process must learn *all* of what it lacks, not the first thing."""

    plan = build_shadow_ports()

    # The durable half is absent because there is no factory…
    assert BLOCKER_SESSION_FACTORY_MISSING in plan.blockers
    for _name, code in FIELD_BLOCKERS:
        if code in {
            BLOCKER_LEGACY_QUOTA_UNAVAILABLE,
            BLOCKER_HANDOFF_PARTITIONS_MISSING,
            BLOCKER_M05_UPSTREAM_MISSING,
            BLOCKER_M04E_DEFINITION_VERSIONS_MISSING,
            BLOCKER_METHOD_MAPPINGS_MISSING,
            BLOCKER_METHOD_CONFIGURATION_MISSING,
        }:
            # …including the ones no factory could ever have supplied.
            assert code in plan.blockers, code


def test_an_explicit_none_is_absence_and_a_full_assembly_never_contains_one() -> None:
    """``None`` never means "defaulted"; it means "stated absent"."""

    plan = full_ports(company=None)

    assert plan.ports is None
    assert BLOCKER_COMPANY_UNAVAILABLE in plan.blockers
