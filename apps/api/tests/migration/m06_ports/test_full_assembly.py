"""Acceptance tests for the complete ``ShadowGatewayPorts`` assembly.

Task ``MIG-M06-PORTS-01``.  The statement under test is narrow: sixteen fields,
each either constructed from a real adapter or named as absent, and — the point
of the whole exercise — ``build_shadow_gateway`` really builds a shadow facade
out of the result.

What is proven here, in order of strength:

* the assembled set is **accepted by the reviewed builder**, and the facade it
  produces keeps ``DECISION_OWNER``/``CANONICAL_READ_OWNER == "legacy"`` with
  canonical positive writes off;
* every derived field is a **production class** (not a fake), because a port set
  made of doubles would prove nothing about the tree;
* assembly **opens no session**.
"""

from __future__ import annotations

from dataclasses import fields

from prodavan.application.migration.m06_grants_gateway.composition import (
    ShadowGatewayPorts,
    build_shadow_gateway,
    caller_assertion_report,
)
from prodavan.application.migration.m06_grants_gateway.ports_assembly import (
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

# ---------------------------------------------------------------------------
# The main proof: the facade constructs
# ---------------------------------------------------------------------------


def test_full_assembly_produces_a_port_set_the_reviewed_builder_accepts() -> None:
    """The decisive test: ``build_shadow_gateway`` builds from the assembled ports."""

    plan = full_ports()

    assert plan.complete is True
    assert plan.blockers == ()
    ports = plan.ports
    assert isinstance(ports, ShadowGatewayPorts)

    # The caller must be able to construct the facade from *these* ports.
    gateway = build_shadow_gateway(ports)
    assert gateway.DECISION_OWNER == "legacy"
    assert gateway.CANONICAL_READ_OWNER == "legacy"
    assert gateway.CANONICAL_POSITIVE_WRITES is False


def test_the_shadow_ownership_invariants_are_stated_by_the_composition_itself() -> None:
    """``caller_assertion_report`` refuses a graph that claims canonical ownership."""

    gateway = build_shadow_gateway(full_ports().ports)  # type: ignore[arg-type]
    report = caller_assertion_report(gateway=gateway)

    assert report["decision_owner"] == "legacy"
    assert report["canonical_read_owner"] == "legacy"
    assert report["canonical_positive_writes"] == "false"


def test_every_declared_field_is_populated() -> None:
    """Every required field carries a real object.

    ``protocol`` is the one optional field of the dataclass: its absence is the
    documented exact-version comparison, and ``test_protocol_is_optional_and_its_
    absence_is_not_a_blocker`` pins that separately.
    """

    ports = full_ports().ports
    assert ports is not None
    for field in fields(ShadowGatewayPorts):
        if field.name in {"protocol"}:
            assert getattr(ports, field.name) is None
            continue
        assert getattr(ports, field.name) is not None, field.name


# ---------------------------------------------------------------------------
# Derived ports are real production adapters
# ---------------------------------------------------------------------------


def test_derived_ports_are_the_production_sql_adapters() -> None:
    """A port set made of doubles would prove nothing about the tree."""

    from prodavan.application.migration.evidence_ports import (
        SqlPartitionHandoffReadStore,
    )
    from prodavan.application.migration.m05_instance_runtime.runtime_service import (
        FailClosedResolvePluginInstance,
    )
    from prodavan.application.migration.m06_grants_gateway.evidence_store import (
        SqlCanonicalEvidenceStore,
    )
    from prodavan.infrastructure.persistence.canonical_grants_read import (
        SqlAuthenticatedCompanyPort,
        SqlCanonicalGrantReadPort,
    )
    from prodavan.infrastructure.persistence.dynamic_app_gateway import (
        SqlCapabilityScopePort,
        SqlDynamicPluginRepository,
    )

    ports = full_ports().ports
    assert ports is not None
    assert isinstance(ports.legacy_repository, SqlDynamicPluginRepository)
    assert isinstance(ports.legacy_scope, SqlCapabilityScopePort)
    assert isinstance(ports.company, SqlAuthenticatedCompanyPort)
    assert isinstance(ports.canonical_grants, SqlCanonicalGrantReadPort)
    assert isinstance(ports.evidence_read, SqlCanonicalEvidenceStore)
    assert isinstance(ports.evidence_transaction, SqlCanonicalEvidenceStore)
    # The evidence store owns both halves; sharing one instance keeps the
    # transaction boundary identical between read and commit.
    assert ports.evidence_read is ports.evidence_transaction
    assert isinstance(ports.handoff_partitions, SqlPartitionHandoffReadStore)
    assert isinstance(ports.m05_instances, FailClosedResolvePluginInstance)
    # The legacy repository offers the facade's protocol surface.  A bare
    # ``isinstance`` would raise here: the ports are plain ``Protocol``s, not
    # ``@runtime_checkable`` ones, and making them runtime-checkable to satisfy a
    # test would weaken the production contract.
    assert callable(getattr(ports.legacy_repository, "resolve", None))
    assert callable(getattr(ports.legacy_repository, "get_grants", None))
    assert callable(getattr(ports.legacy_repository, "get_instance", None))


def test_a_supplied_legacy_bundle_is_used_verbatim() -> None:
    """``legacy_ports`` is taken from the object the host already composed."""

    from prodavan.infrastructure.persistence.dynamic_app_gateway import (
        build_legacy_gateway_ports,
    )

    factory = RefusingSessionFactory()
    bundle = build_legacy_gateway_ports(session_factory=factory)  # type: ignore[arg-type]
    plan = build_shadow_ports(
        session_factory=factory,
        partition=PARTITION,
        writer_generation=WRITER_GENERATION,
        m04_tenant_context=Inert(),
        m05_tenant_context=Inert(),
        m04e_definition_versions=definition_repository(),
        m05_upstream=Inert(),
        legacy_ports=bundle,
        legacy_quota=Inert(),
        method_mappings=method_mappings(),
        method_servers=method_configuration()[0],
        method_versions=method_configuration()[1],
    )

    assert plan.complete is True
    ports = plan.ports
    assert ports is not None
    assert ports.legacy_repository is bundle.repository
    assert ports.legacy_scope is bundle.scope


# ---------------------------------------------------------------------------
# Assembly runs nothing
# ---------------------------------------------------------------------------


def test_assembly_opens_no_session() -> None:
    factory = RefusingSessionFactory()
    plan = full_ports(session_factory=factory)

    assert plan.complete is True
    assert factory.opened == 0


def test_the_handoff_reader_is_bound_to_the_stated_partition() -> None:
    """``SqlPartitionHandoffReadStore`` refuses a partition outside its bound tenant.

    That refusal happens *before any query*, so reading it back proves the store was
    constructed with this partition's tenant and writer generation.
    """

    plan = full_ports()
    assert plan.ports is not None
    handoff = plan.ports.handoff_partitions

    assert handoff.read_partition is not None
    scope = handoff._boundaries.scope  # noqa: SLF001 - the binding is the assertion
    assert scope.tenant_id == PARTITION[0]
    assert scope.writer_generation == WRITER_GENERATION


def test_protocol_is_optional_and_its_absence_is_not_a_blocker() -> None:
    """``ShadowGatewayPorts.protocol`` has a documented default behaviour."""

    plan = full_ports(protocol=None)

    assert plan.complete is True
    assert plan.ports is not None
    assert plan.ports.protocol is None
    assert "protocol" not in plan.built


def test_a_stated_protocol_port_is_recorded_as_built() -> None:
    plan = full_ports(protocol=Inert())

    assert plan.complete is True
    assert plan.ports is not None
    assert plan.ports.protocol is not None
    assert "protocol" in plan.built


# ---------------------------------------------------------------------------
# Bookkeeping
# ---------------------------------------------------------------------------


def test_built_names_every_required_field_it_constructed() -> None:
    plan = full_ports()

    assert set(plan.built) == {name for name, _code in FIELD_BLOCKERS}
    assert plan.snapshot() == {
        "complete": True,
        "built": list(plan.built),
        "blockers": [],
    }


def test_snapshot_is_json_safe() -> None:
    import json

    encoded = json.dumps(full_ports().snapshot(), sort_keys=True)

    assert "Inert" not in encoded
    assert "object at 0x" not in encoded
    assert "RefusingSessionFactory" not in encoded
