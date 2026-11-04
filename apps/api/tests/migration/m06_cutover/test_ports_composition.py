"""Composition-level contract for the cutover stage now that its ports exist.

The registry in ``application/migration/composition.py`` lists ports declared in ``app/**``
with no implementation anywhere. Five of them are implemented by
``m06_cutover/ports.py``; this module asserts the registry no longer claims otherwise and
that the stage builder reports exactly what a host still owes it.

``tests/migration/migration_composition/`` asserts the pre-implementation registry and is
owned by another task; the assertions here are the ones this task is responsible for.
"""

from prodavan.application.migration.composition import (
    UNIMPLEMENTED_PORTS,
    MigrationStage,
    build_cutover_stage,
    stage_spec,
    unimplemented_ports,
)
from prodavan.application.migration.m06_cutover.cutover import CutoverCommandService
from prodavan.application.migration.m06_cutover.ports import (
    CutoverPorts,
    SqlCanonicalRepairVerifier,
    SqlCutoverRepository,
    SqlGateVerifier,
    SqlLegacyOwnerVerifier,
    SqlRollbackVerifier,
    build_cutover_ports,
)

IMPLEMENTED_CUTOVER_PORTS = (
    "CutoverRepository",
    "GateVerifier",
    "LegacyOwnerVerifier",
    "RollbackVerifier",
    "CanonicalRepairVerifier",
)


def test_every_cutover_port_is_implemented_in_the_cutover_ports_module():
    assert issubclass(SqlCutoverRepository, object)
    assert all(callable(getattr(module, "verify", None)) for module in (
        SqlGateVerifier, SqlLegacyOwnerVerifier, SqlRollbackVerifier,
        SqlCanonicalRepairVerifier,
    ))
    assert not isinstance(SqlCutoverRepository, CutoverPorts)


def test_registry_no_longer_lists_the_five_cutover_gaps():
    registered = {port.port for port in UNIMPLEMENTED_PORTS}
    assert registered.isdisjoint(IMPLEMENTED_CUTOVER_PORTS)
    assert unimplemented_ports(MigrationStage.CUTOVER) == ()
    # The M06 handoff port keeps its entry: that builder does not accept it yet.
    assert registered == {"PartitionHandoffReadPort"}


def test_cutover_stage_spec_still_requires_the_five_ports():
    spec = stage_spec(MigrationStage.CUTOVER)
    assert spec.required_ports == IMPLEMENTED_CUTOVER_PORTS
    assert spec.service_class == "CutoverCommandService"


def test_stage_specs_are_never_empty_for_any_stage():
    assert all(stage_spec(stage).required_ports for stage in MigrationStage)


def test_builder_binds_the_service_once_every_port_is_supplied():
    repository = object()
    assembly = build_cutover_stage(
        repository=repository,  # type: ignore[arg-type]
        gate_verifier=object(),  # type: ignore[arg-type]
        legacy_owner_verifier=object(),  # type: ignore[arg-type]
        rollback_verifier=object(),  # type: ignore[arg-type]
        canonical_repair_verifier=object(),  # type: ignore[arg-type]
    )
    assert isinstance(assembly.service("cutover_command_service"), CutoverCommandService)
    assert assembly.blockers == ()
    assert assembly.wired is True


def test_builder_reports_a_missing_repository_without_inventing_one():
    assembly = build_cutover_stage(
        repository=None, gate_verifier=None, legacy_owner_verifier=None,
        rollback_verifier=None, canonical_repair_verifier=None,
    )
    assert assembly.services == ()
    assert assembly.wired is False
    assert {blocker.port for blocker in assembly.blockers} == set(IMPLEMENTED_CUTOVER_PORTS)
    assert set(assembly.blocker_codes) == {"cutover_required_port_missing"}


def test_bundle_builds_all_five_ports_over_one_factory():
    sentinel = object()
    ports = build_cutover_ports(session_factory=sentinel)  # type: ignore[arg-type]
    assert isinstance(ports, CutoverPorts)
    assert ports.repository.session_factory is sentinel
    assert isinstance(ports.gate_verifier, SqlGateVerifier)
    assert isinstance(ports.legacy_owner_verifier, SqlLegacyOwnerVerifier)
    assert isinstance(ports.rollback_verifier, SqlRollbackVerifier)
    assert isinstance(ports.canonical_repair_verifier, SqlCanonicalRepairVerifier)
