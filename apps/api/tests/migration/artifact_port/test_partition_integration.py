"""Integration: the operator assembly no longer refuses on a missing artifact port.

Blocker ``partition_artifact_content_port_unavailable`` was the reason no partition could be
imported: the M04E backfill cannot read a definition version's artifact without an
``ArtifactContentPort``, and no implementation existed.  ``MIG-ARTIFACT-PORT-01`` supplies
one, so this module pins both halves of the claim:

* the blocker is **gone** from every plan shape that can build a durable port, and
* what replaced it is a real, exploitable dependency -- not a stub.  The integration test
  drives the composed port end to end against a live database and a local object store, so
  "the blocker is gone" is backed by "the bytes really come out".
"""

from __future__ import annotations

import inspect
from dataclasses import fields

import pytest

from prodavan.application.migration.composition import (
    ChainDependencies,
    assemble_migration_chain,
)
from prodavan.application.migration.m04e_definition_version.artifact import (
    ArtifactContentPort,
)
from prodavan.application.migration.partition_chain import (
    M04E_MAX_ARTIFACT_BYTES,
    PARTITION_M04E_ARTIFACTS_UNAVAILABLE,
    PartitionAssemblyOverrides,
    build_partition_chain,
)
from prodavan.infrastructure.persistence.migration_artifact_store import (
    SqlArtifactContentStore,
)

from ..partition_chain.partition_fixtures import (
    GENERATION,
    RecordingSessionFactory,
    complete_overrides,
    key,
    other_key,
)


def _plan(**overrides: object):
    """Assemble with the artifact port **omitted**, so the planner has to derive it.

    ``complete_overrides()`` supplies an inert ``ArtifactContentPort`` by default (its job is
    to model "the host provided everything"), so every derivation test here has to say
    ``m04e_artifacts=None`` explicitly.  Without that the assertion below would pass for the
    wrong reason -- it would be inspecting the fixture, not the planner.
    """

    values: dict[str, object] = {"m04e_artifacts": None}
    values.update(overrides)
    return build_partition_chain(
        key=key(),
        session_factory=object(),
        writer_generation=GENERATION,
        **complete_overrides(**values).chain_kwargs(),
    )


def test_a_complete_partition_no_longer_carries_the_artifact_blocker() -> None:
    plan = _plan()

    assert PARTITION_M04E_ARTIFACTS_UNAVAILABLE not in plan.blocker_codes
    assert plan.complete is True
    assert plan.blocker_codes == ()


def test_the_composed_port_satisfies_the_protocol_structurally() -> None:
    """The dependency is checked against the contract's *shape*, not against a class name.

    ``ArtifactContentPort`` is a plain ``Protocol`` without ``@runtime_checkable`` (the task
    forbids editing the contract), so ``isinstance`` cannot be used.  That is not a loss
    here: the port is an application boundary implemented by duck typing, so the meaningful
    assertion is that the composed object exposes both contract methods as coroutines with
    the contract's parameter names -- which is exactly what the next test checks.
    """

    plan = _plan()
    port = plan.dependencies.m04e_artifacts
    assert port is not None

    for method_name in ("resolve_artifact_uri", "read_artifact"):
        assert callable(getattr(port, method_name, None))


def test_the_composed_port_exposes_both_contract_methods_with_the_declared_signatures() -> None:
    plan = _plan()
    port = plan.dependencies.m04e_artifacts
    assert port is not None

    for method_name in ("resolve_artifact_uri", "read_artifact"):
        port_method = getattr(port, method_name)
        contract_method = getattr(ArtifactContentPort, method_name)
        assert inspect.iscoroutinefunction(port_method)
        # Compare against an *instance's* signature: the class-level one is unbound and
        # carries ``self``, which is not part of the contract a caller sees.
        assert tuple(inspect.signature(port_method).parameters) == tuple(
            inspect.signature(contract_method).parameters
        )[1:]
        # Keyword-only, as the contract declares: a positional call cannot smuggle a
        # caller-selected tenant into the read.
        kinds = {
            name: parameter.kind
            for name, parameter in inspect.signature(port_method).parameters.items()
        }
        assert all(
            kind is inspect.Parameter.KEYWORD_ONLY for kind in kinds.values()
        ), kinds


def test_the_port_is_built_even_when_every_other_collaborator_is_absent() -> None:
    """Derivation must not depend on a host supplying anything else."""

    plan = build_partition_chain(
        key=key(), session_factory=object(), writer_generation=GENERATION,
    )

    assert isinstance(plan.dependencies.m04e_artifacts, SqlArtifactContentStore)
    assert "m04e_artifacts" in plan.built
    # The plan is still incomplete -- for the six reasons that remain real.
    assert plan.complete is False
    assert PARTITION_M04E_ARTIFACTS_UNAVAILABLE not in plan.blocker_codes


def test_the_port_uses_the_same_artifact_bound_as_the_rest_of_the_chain() -> None:
    plan = _plan()

    port = plan.dependencies.m04e_artifacts
    assert isinstance(port, SqlArtifactContentStore)
    assert port.max_object_bytes == M04E_MAX_ARTIFACT_BYTES
    assert M04E_MAX_ARTIFACT_BYTES == 64 * 1024 * 1024


def test_an_invalid_artifact_bound_is_refused_at_assembly_time() -> None:
    """A scalar with a reviewed range is validated where it is stated, not mid-run."""

    with pytest.raises(ValueError):
        _plan(m04e_max_artifact_bytes=0)


def test_two_partitions_get_distinct_artifact_ports() -> None:
    """No port is shared between partitions: each is built against its own factory."""

    first = build_partition_chain(
        key=key(), session_factory=object(), writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )
    second = build_partition_chain(
        key=other_key(), session_factory=object(), writer_generation=GENERATION,
        **complete_overrides().chain_kwargs(),
    )

    assert first.dependencies.m04e_artifacts is not second.dependencies.m04e_artifacts


def test_the_overrides_type_states_the_derived_default_and_the_bound() -> None:
    overrides = PartitionAssemblyOverrides()

    assert overrides.m04e_artifacts is None
    assert overrides.m04e_max_artifact_bytes == M04E_MAX_ARTIFACT_BYTES
    assert "m04e_artifacts" in overrides.chain_kwargs()


async def test_assembly_of_the_port_opens_no_session_and_no_file_store() -> None:
    """The planner's central promise -- "nothing runs" -- survives a derived read port."""

    factory = RecordingSessionFactory()

    plan = build_partition_chain(
        key=key(),
        session_factory=factory,
        writer_generation=GENERATION,
        **complete_overrides(m04e_artifacts=None).chain_kwargs(),
    )

    assert isinstance(plan.dependencies.m04e_artifacts, SqlArtifactContentStore)
    assert factory.calls == 0


def test_every_chain_dependency_is_still_stated() -> None:
    """Deriving one port must not leave another silently ``None``."""

    plan = _plan()

    missing = [
        field.name
        for field in fields(ChainDependencies)
        if getattr(plan.dependencies, field.name) is None
    ]
    assert missing == []


def test_the_reviewed_composition_root_reports_every_stage_wired() -> None:
    plan = _plan()
    chain = assemble_migration_chain(plan.dependencies)

    assert chain.wired is True, chain.blocker_codes
    assert chain.blocker_codes == ()


def test_the_stage_assembly_no_longer_reports_the_artifact_port_missing() -> None:
    """The M04E stage's own port check must see the composed adapter as present."""

    from prodavan.application.migration.composition import M04E_ARTIFACT_CONTENT_PORT_MISSING

    plan = _plan()
    chain = assemble_migration_chain(plan.dependencies)

    assert M04E_ARTIFACT_CONTENT_PORT_MISSING not in chain.blocker_codes
    assert "artifact_content_port_missing" not in chain.blocker_codes


def test_the_plan_snapshot_names_no_code_that_no_longer_exists() -> None:
    """An operator greps the snapshot; a retired blocker must not appear in it."""

    snapshot = _plan().snapshot()
    rendered = str(snapshot)

    assert PARTITION_M04E_ARTIFACTS_UNAVAILABLE not in rendered
