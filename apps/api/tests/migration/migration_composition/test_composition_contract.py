"""MIG-ASSEMBLY-01 — the M04 -> M04E -> M05 -> M06 -> cutover chain as a constructible process.

Authored during the migration **verification freeze**: nothing in this module was executed.

Companion review: ``docs/08-migration/integration-seams-review.md`` §1-§3 and §7. The review
established that the chain was never assembled: every service constructor existed only in
``tests/`` and ``core/wiring.py`` did not mention migration at all. These artifacts pin the
composition root that closes that gap.

Three layers of acceptance:

**static (AST / text, no import of the code under test for the invariants that must hold on a
clean checkout)**
    the composition imports no test module, never imports the application adapter as a scope
    port, has no dependency defaulted to ``None``, and its published sequence can be read
    straight from the source;

**assembly (real constructors, inert doubles, no database, no transaction)**
    each stage is built from its stated dependencies, refuses with a *named* blocker when a
    mandatory port is absent, and never reports itself wired while a port declared in
    ``app/**`` has no implementation in ``src/prodavan``;

**registry (the review §3 gaps)**
    every declared-but-unimplemented port is listed with a live ``file:line`` anchor, and the
    chain reports exactly those blocker codes — so closing a gap *and* forgetting to update
    the registry fails here rather than at cutover time.

Deliberately out of scope: exercising any stage method. The composition only binds objects;
running a backfill page or a cutover transition needs a database and belongs to the
post-freeze verification wave.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from prodavan.application.migration.composition import (
    MIGRATION_SEQUENCE,
    STAGE_BUILDERS,
    UNIMPLEMENTED_PORTS,
    AssemblyBlocker,
    ChainDependencies,
    MigrationChain,
    MigrationStage,
    StageAssembly,
    StageSpec,
    assemble_migration_chain,
    build_cutover_stage,
    build_m04_scope_stage,
    build_m04e_definition_version_stage,
    build_m05_instance_runtime_stage,
    build_m06_grants_gateway_stage,
    build_m06_durable_evidence_ports,
    migration_sequence,
    stage_spec,
    unimplemented_ports,
    validate_migration_sequence,
)
from prodavan.application.migration.m04_scope.scope_persistence import (
    SqlAlchemyScopeBackfillAdapter,
)
from prodavan.application.migration.m04e_definition_version.backfill import (
    DefinitionVersionEvidenceBackfill,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    InstanceImportService,
    M05PromotionService,
)
from prodavan.application.migration.m06_cutover.cutover import CutoverCommandService
from prodavan.application.migration.m06_grants_gateway.adapter import CapabilityGatewayService
from prodavan.application.migration.m06_grants_gateway.parity_runner import BoundedParityRunner
from prodavan.application.migration.scope_registry_adapter.adapter import (
    ScopeRegistryApplicationAdapter,
)

from . import composition_fixtures as fx

# apps/api/tests/migration/migration_composition/test_composition_contract.py -> apps/api
API_ROOT = Path(__file__).resolve().parents[3]
SRC = API_ROOT / "src" / "prodavan"
COMPOSITION = SRC / "application" / "migration" / "composition.py"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _composition_tree() -> ast.Module:
    return ast.parse(COMPOSITION.read_text(encoding="utf-8"), filename=str(COMPOSITION))


def _imported_modules(tree: ast.Module) -> tuple[str, ...]:
    """Every dotted module named by an ``import``/``from ... import`` statement."""

    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            modules.append(("." * node.level) + (node.module or ""))
    return tuple(modules)


def _imported_symbols(tree: ast.Module) -> tuple[tuple[str, str], ...]:
    """``(module, symbol)`` for every name pulled in by ``from module import symbol``."""

    pairs: list[tuple[str, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            pairs.extend((node.module or "", alias.name) for alias in node.names)
    return tuple(pairs)


def _public_builders(tree: ast.Module) -> tuple[ast.FunctionDef, ...]:
    return tuple(
        node for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and (node.name.startswith("build_") or node.name == "assemble_migration_chain")
    )


def _stage_order_from_source(tree: ast.Module) -> tuple[str, ...]:
    """Read the published sequence straight out of the source, without importing it.

    Each ``StageSpec(stage=MigrationStage.X, ...)`` in the module-level ``MIGRATION_SEQUENCE``
    tuple contributes ``X``; this is what makes the ordering reviewable by reading the file.
    """

    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = [target for target in node.targets if isinstance(target, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target]
            value = node.value
        else:
            continue
        if not any(target.id == "MIGRATION_SEQUENCE" for target in targets):
            continue
        if not isinstance(value, ast.Tuple):
            raise AssertionError("MIGRATION_SEQUENCE must be a literal tuple of StageSpec calls")
        stages: list[str] = []
        for element in value.elts:
            if not isinstance(element, ast.Call):
                raise AssertionError("MIGRATION_SEQUENCE entries must be StageSpec(...) calls")
            stage_keyword = next(
                (keyword for keyword in element.keywords if keyword.arg == "stage"), None,
            )
            if stage_keyword is None or not isinstance(stage_keyword.value, ast.Attribute):
                raise AssertionError("every StageSpec must name its stage explicitly")
            stages.append(stage_keyword.value.attr)
        return tuple(stages)
    raise AssertionError("composition.py must publish a module-level MIGRATION_SEQUENCE tuple")


def _codes(assembly: StageAssembly) -> set[str]:
    return set(assembly.blocker_codes)


def _registry_codes(stage: MigrationStage) -> set[str]:
    return {port.blocker_code for port in unimplemented_ports(stage)}


def _folded(name: str) -> str:
    """Case- and separator-insensitive form, so ``GateVerifier`` matches ``gate_verifier``."""

    return "".join(character for character in name if character.isalnum()).lower()


# ---------------------------------------------------------------------------
# 1. static contract: what the composition may and may not depend on
# ---------------------------------------------------------------------------


def test_composition_imports_no_test_module() -> None:
    """A production composition root that reaches into ``tests/**`` is not production."""

    tree = _composition_tree()
    offenders = [
        module for module in _imported_modules(tree)
        if "tests" in module.split(".") or module.startswith("..") or "conftest" in module
    ]
    assert offenders == [], f"composition must not import test modules: {offenders}"


def test_composition_never_wires_the_application_adapter_as_scope_port() -> None:
    """``ScopeRegistryApplicationAdapter`` returns ``ScopeAdapterResult``; it is not a port.

    Review §5 I1: its ``resolve_active`` signature differs from ``ResolveScopePort``. It may be
    named in the module docstring (to warn the next reader) but must not be imported, so it can
    never be passed as ``m04_scope``: that would make M06 identity fail with
    ``m04_scope_evidence_missing`` on every request.
    """

    tree = _composition_tree()
    imported = {symbol for _module, symbol in _imported_symbols(tree)}
    assert "ScopeRegistryApplicationAdapter" not in imported
    assert "scope_registry_adapter" not in {
        module.rsplit(".", 1)[-1] for module in _imported_modules(tree)
    }
    # The warning itself must stay in the module: it is the only thing preventing a repeat.
    text = COMPOSITION.read_text(encoding="utf-8")
    assert "ScopeRegistryApplicationAdapter" in text


def test_no_builder_dependency_is_defaulted() -> None:
    """Every dependency is stated by the host; a default can only ever be a bounded scalar.

    The review's §7 recommendation is explicit: no ``=None`` arguments, because
    ``build_sql_resolve_plugin_instance_port(upstream=None)`` silently builds a deny-everything
    port. Scalars (``page_size``, ``max_command_bytes``) may carry bounded defaults; ports may
    not be defaulted at all, so passing ``None`` is always a deliberate act.
    """

    tree = _composition_tree()
    builders = _public_builders(tree)
    assert len(builders) >= 6, "the five stage builders plus the chain assembler must be present"
    for function in builders:
        arguments = function.args
        # Positional defaults would hide a dependency behind call ordering; builders take
        # their dependencies keyword-only for exactly that reason.
        assert arguments.defaults == [], (
            f"{function.name} must not default a positional dependency"
        )
        parameters = [*arguments.posonlyargs, *arguments.args, *arguments.kwonlyargs]
        assert parameters, f"{function.name} declares no dependencies"
        # ``kw_defaults`` is aligned with ``kwonlyargs``; ``None`` means "no default at all".
        assert len(arguments.kw_defaults) == len(arguments.kwonlyargs), function.name
        for argument, default in zip(arguments.kwonlyargs, arguments.kw_defaults):
            if default is None:
                continue
            assert not (isinstance(default, ast.Constant) and default.value is None), (
                f"{function.name}({argument.arg}=None): a dependency defaulted to None would let "
                "a deny-everything object graph look wired"
            )
            assert isinstance(default, ast.Constant) and isinstance(default.value, int), (
                f"{function.name}({argument.arg}) may only default to a bounded integer"
            )


def test_no_live_builder_signature_defaults_a_port() -> None:
    """Cross-check the AST rule against the live signatures: defaults are scalars only."""

    import inspect

    scalars = {"page_size", "max_command_bytes"}
    for builder in STAGE_BUILDERS.values():
        signature = inspect.signature(builder)
        for name, parameter in signature.parameters.items():
            if parameter.default is inspect.Parameter.empty:
                continue
            assert name in scalars, (
                f"{builder.__name__}.{name} has a default but is not a bounded scalar"
            )
            assert isinstance(parameter.default, int) and not isinstance(
                parameter.default, bool
            )


def test_sequence_order_is_readable_from_source() -> None:
    """The published order is a literal tuple, reviewable without executing the module."""

    declared = _stage_order_from_source(_composition_tree())
    assert declared == (
        "M04_SCOPE", "M04E_DEFINITION_VERSION", "M05_INSTANCE_RUNTIME",
        "M06_GRANTS_GATEWAY", "CUTOVER",
    )
    assert declared == tuple(stage.name for stage in MigrationStage)


# ---------------------------------------------------------------------------
# 2. the published sequence (pure data, starts nothing)
# ---------------------------------------------------------------------------


def test_sequence_covers_every_stage_once_in_reviewed_order() -> None:
    specs = migration_sequence()
    assert tuple(spec.stage for spec in specs) == tuple(MigrationStage)
    assert len(specs) == 5


def test_sequence_names_the_reviewed_class_for_each_stage() -> None:
    """stage -> class is not free-form: these are the classes the chain actually assembles."""

    expected = {
        MigrationStage.M04_SCOPE: "SqlAlchemyScopeBackfillAdapter",
        MigrationStage.M04E_DEFINITION_VERSION: "DefinitionVersionEvidenceBackfill",
        MigrationStage.M05_INSTANCE_RUNTIME: "InstanceImportService",
        MigrationStage.M06_GRANTS_GATEWAY: "CapabilityGatewayService",
        MigrationStage.CUTOVER: "CutoverCommandService",
    }
    assert {spec.stage: spec.service_class for spec in migration_sequence()} == expected


def test_every_stage_states_ports_and_evidence() -> None:
    for spec in migration_sequence():
        assert spec.required_ports, f"{spec.stage.value} must state required ports"
        assert spec.produces, f"{spec.stage.value} must state what it produces"
        assert spec.precondition.requires_evidence, (
            f"{spec.stage.value} must state the evidence it needs"
        )


def test_preconditions_only_reference_earlier_stages() -> None:
    order = [spec.stage for spec in MIGRATION_SEQUENCE]
    for index, spec in enumerate(MIGRATION_SEQUENCE):
        for required in spec.precondition.requires_stages:
            assert order.index(required) < index

    assert stage_spec(MigrationStage.M04_SCOPE).precondition.requires_stages == ()
    assert stage_spec(MigrationStage.M05_INSTANCE_RUNTIME).precondition.requires_stages == (
        MigrationStage.M04E_DEFINITION_VERSION,
    )
    assert stage_spec(MigrationStage.CUTOVER).precondition.requires_stages == (
        MigrationStage.M04_SCOPE,
        MigrationStage.M04E_DEFINITION_VERSION,
        MigrationStage.M05_INSTANCE_RUNTIME,
        MigrationStage.M06_GRANTS_GATEWAY,
    )


def test_handoff_gate_names_match_the_six_m04e_m05_gates() -> None:
    """The M05 stage precondition quotes the exact gate vocabulary M05 re-checks."""

    assert stage_spec(MigrationStage.M05_INSTANCE_RUNTIME).precondition.gate_names == (
        "MIG-M04-01", "MIG-M04-02", "MIG-M04E-01",
        "active_typed_parent_scope", "same_definition_version", "immutable_artifact",
    )
    assert stage_spec(MigrationStage.CUTOVER).precondition.gate_names == ("M04", "M04E", "M05")


def test_sequence_validation_rejects_a_broken_order() -> None:
    reversed_specs = tuple(reversed(MIGRATION_SEQUENCE))
    with pytest.raises(ValueError, match="reviewed order"):
        validate_migration_sequence(reversed_specs)
    with pytest.raises(ValueError, match="reviewed order"):
        validate_migration_sequence(MIGRATION_SEQUENCE[:4])


def test_every_stage_has_a_builder() -> None:
    assert set(STAGE_BUILDERS) == set(MigrationStage)
    for stage, builder in STAGE_BUILDERS.items():
        assert callable(builder), f"{stage.value} has no callable builder"


# ---------------------------------------------------------------------------
# 3. the registry of unimplemented ports (review §3)
# ---------------------------------------------------------------------------


EXPECTED_GAP_CODES = {
    # G3 — the M06 stage builder does not accept the evidence ports yet
    # (``ShadowGatewayPorts.handoff_partitions`` carries it, but no builder parameter does),
    # so it stays registered. Everything else has an implementation and left this table.
    "m06_partition_handoff_read_port_unimplemented",
}

#: Ports that left the registry once a reviewed implementation landed, each pinned to the
#: module and class that implements it. The tests below import every target, so a stale
#: "implemented" claim fails here instead of quietly weakening the chain:
#:
#: * G7 tenant-context binders -> ``application/migration/tenant_context.py``
#: * G1/G2/G4 evidence ports   -> ``application/migration/evidence_ports.py``
#: * G6 M04E gates/proofs/reader -> ``application/migration/m04e_ports.py``
#: * G5 cutover repository + four verifiers -> ``application/migration/m06_cutover/ports.py``
IMPLEMENTED_PORTS: dict[str, str] = {
    "TenantScopeContextPort":
        "prodavan.application.migration.tenant_context:RlsTenantScopeContextAdapter",
    "TrustedTenantContextPort":
        "prodavan.application.migration.tenant_context:RlsTrustedTenantContextAdapter",
    "EvidenceStore":
        "prodavan.application.migration.evidence_ports:SqlM04EBackfillEvidenceStore",
    "M05HandoffPort":
        "prodavan.application.migration.evidence_ports:SqlM05HandoffEvidenceStore",
    "M05PromotionRecordPort":
        "prodavan.application.migration.evidence_ports:SqlM05PromotionEvidenceStore",
    "UpstreamGatePort": "prodavan.application.migration.m04e_ports:SqlM04EUpstreamGate",
    "CandidateProofPort": "prodavan.application.migration.m04e_ports:SqlM04ECandidateProof",
    "CandidateReader": "prodavan.application.migration.m04e_ports:SqlM04ECandidateReader",
    "CutoverRepository":
        "prodavan.application.migration.m06_cutover.ports:SqlCutoverRepository",
    "GateVerifier": "prodavan.application.migration.m06_cutover.ports:SqlGateVerifier",
    "LegacyOwnerVerifier":
        "prodavan.application.migration.m06_cutover.ports:SqlLegacyOwnerVerifier",
    "RollbackVerifier":
        "prodavan.application.migration.m06_cutover.ports:SqlRollbackVerifier",
    "CanonicalRepairVerifier":
        "prodavan.application.migration.m06_cutover.ports:SqlCanonicalRepairVerifier",
}


def _imported_target(path: str) -> object:
    """``module:Class`` -> the class, imported for real."""

    import importlib

    module_name, _, symbol = path.partition(":")
    module = importlib.import_module(module_name)
    return getattr(module, symbol)


def test_registry_lists_exactly_the_reviewed_gaps() -> None:
    assert {port.blocker_code for port in UNIMPLEMENTED_PORTS} == EXPECTED_GAP_CODES
    assert {port.review_ref for port in UNIMPLEMENTED_PORTS} == {"G3"}


@pytest.mark.parametrize("port", sorted(IMPLEMENTED_PORTS), ids=str)
def test_every_implemented_port_really_exists_and_left_the_registry(port: str) -> None:
    """A port is removed from the registry only because a real implementation exists.

    Two independent facts, both required: the implementing class is importable, and the port
    is no longer registered as unimplemented. Either one alone would let the registry drift.
    """

    assert _imported_target(IMPLEMENTED_PORTS[port]) is not None
    assert port not in {entry.port for entry in UNIMPLEMENTED_PORTS}


def test_the_one_registered_port_still_has_an_implementation_but_no_builder_parameter() -> None:
    """``PartitionHandoffReadPort``: implemented, yet still registered — and that is the truth.

    ``SqlPartitionHandoffReadStore`` exists, but ``build_m06_grants_gateway_stage`` takes
    ``ShadowGatewayPorts`` (which carries the port) rather than the port itself, so the stage
    builder cannot be handed one. The registry entry therefore still describes reality; it is
    removed in the same change that adds the builder parameter, not by this test.
    """

    target = _imported_target(
        "prodavan.application.migration.evidence_ports:SqlPartitionHandoffReadStore"
    )
    assert target is not None
    assert "PartitionHandoffReadPort" in {entry.port for entry in UNIMPLEMENTED_PORTS}
    import inspect

    parameters = set(inspect.signature(build_m06_grants_gateway_stage).parameters)
    assert "handoff_partitions" not in parameters


def test_registry_never_names_a_port_a_stage_builder_accepts() -> None:
    """A registered port must not also be a builder parameter.

    Names differ in case (``GateVerifier`` class vs ``gate_verifier`` argument), so both sides
    are compared case- and separator-folded. If a builder starts accepting a registered port,
    the entry is stale: the host can now supply it, so the stage is wireable.
    """

    tree = _composition_tree()
    accepted = {
        _folded(argument.arg)
        for function in _public_builders(tree)
        for argument in [*function.args.args, *function.args.kwonlyargs]
    }
    stale = [entry.port for entry in UNIMPLEMENTED_PORTS if _folded(entry.port) in accepted]
    assert stale == [], f"registered ports a builder already accepts: {stale}"


def test_registry_has_one_entry_per_port() -> None:
    names = [entry.port for entry in UNIMPLEMENTED_PORTS]
    assert len(names) == len(set(names))


@pytest.mark.parametrize("port", UNIMPLEMENTED_PORTS, ids=lambda port: port.blocker_code)
def test_every_gap_points_at_a_live_declaration(port) -> None:
    """``declared_in`` is ``path:line``; a moved declaration invalidates the pointer."""

    path_text, _, line_text = port.declared_in.rpartition(":")
    declaration = SRC / path_text
    assert declaration.is_file(), f"{port.port} points at a missing file: {port.declared_in}"
    lines = declaration.read_text(encoding="utf-8").splitlines()
    line = int(line_text)
    assert 1 <= line <= len(lines), f"{port.port} points past the end of {path_text}"
    assert f"class {port.port}" in lines[line - 1], (
        f"{port.port} is not declared on {port.declared_in}: {lines[line - 1]!r}"
    )


def test_registry_is_partitioned_by_stage() -> None:
    for stage in MigrationStage:
        assert {port.stage for port in unimplemented_ports(stage)} <= {stage}
    # Every registered port is owned by exactly one stage.
    assert len({port.port for port in UNIMPLEMENTED_PORTS}) == len(UNIMPLEMENTED_PORTS)
    # The one remaining gap belongs to M06, and only to M06.
    assert {port.stage for port in UNIMPLEMENTED_PORTS} == {MigrationStage.M06_GRANTS_GATEWAY}
    # Every other stage is fully implemented: its ports exist, so a supplied graph is wireable.
    assert {
        stage for stage in MigrationStage if not unimplemented_ports(stage)
    } == set(MigrationStage) - {MigrationStage.M06_GRANTS_GATEWAY}


# ---------------------------------------------------------------------------
# 4. M04 assembly
# ---------------------------------------------------------------------------


def test_m04_stage_assembles_the_durable_scope_adapter() -> None:
    assembly = build_m04_scope_stage(
        session_factory=fx.session_factory(), tenant_context=fx.tenant_scope_context(),
    )
    assert assembly.stage is MigrationStage.M04_SCOPE
    # The tenant binder has an implementation, so a supplied graph is a complete binding.
    assert assembly.blocker_codes == ()
    assert assembly.wired is True

    adapter = assembly.service("resolve_scope_port")
    assert isinstance(adapter, SqlAlchemyScopeBackfillAdapter)
    # Same object satisfies the backfill transaction port: one adapter, two contracts.
    assert assembly.service("trusted_scope_transaction_port") is adapter
    assert assembly.service("scope_migration_coordinator") is not None
    assert hasattr(adapter, "resolve_active") and hasattr(adapter, "load_checkpoint")


def test_m04_stage_is_not_wired_without_a_tenant_context() -> None:
    """The binder is mandatory: a session factory alone would read without RLS context."""

    assembly = build_m04_scope_stage(
        session_factory=fx.session_factory(), tenant_context=None,
    )
    assert assembly.wired is False
    assert assembly.services == ()
    assert _codes(assembly) == {"m04_required_port_missing"}
    assert {blocker.port for blocker in assembly.blockers} == {"TenantScopeContextPort"}


def test_m04_scope_port_is_not_the_application_adapter() -> None:
    """Guard the exact mistake review §5 I1 describes."""

    assembly = build_m04_scope_stage(
        session_factory=fx.session_factory(), tenant_context=fx.tenant_scope_context(),
    )
    assert not isinstance(assembly.service("resolve_scope_port"), ScopeRegistryApplicationAdapter)


def test_m04_stage_refuses_without_its_dependencies() -> None:
    assembly = build_m04_scope_stage(session_factory=None, tenant_context=None)
    assert assembly.services == ()
    assert assembly.wired is False
    assert _codes(assembly) == {
        "m04_session_factory_missing", "m04_required_port_missing",
    }
    assert {blocker.port for blocker in assembly.blockers} == {
        "async_sessionmaker", "TenantScopeContextPort",
    }


def test_m04_stage_refuses_a_partial_wiring() -> None:
    """Tenant context alone is not enough: RLS without a session factory is not evidence."""

    assembly = build_m04_scope_stage(
        session_factory=None, tenant_context=fx.tenant_scope_context(),
    )
    assert assembly.services == ()
    assert "m04_session_factory_missing" in _codes(assembly)


# ---------------------------------------------------------------------------
# 5. M04E assembly
# ---------------------------------------------------------------------------


def _m04e_complete(**overrides: object) -> StageAssembly:
    arguments: dict[str, object] = {
        "reader": fx.InertPort("candidate-reader"),
        "store": fx.InertPort("evidence-store"),
        "gates": fx.InertPort("upstream-gates"),
        "proofs": fx.InertPort("candidate-proofs"),
        "artifacts": fx.InertPort("artifact-content"),
        "server_repository": fx.server_definition_repository(),
        "m05_handoff": fx.InertPort("m05-handoff"),
    }
    arguments.update(overrides)
    return build_m04e_definition_version_stage(**arguments)  # type: ignore[arg-type]


def test_m04e_stage_builds_the_backfill_and_is_wireable_when_supplied() -> None:
    assembly = _m04e_complete()
    assert isinstance(assembly.service("evidence_backfill"), DefinitionVersionEvidenceBackfill)
    assert assembly.service("server_definition_version_repository") is not None
    assert assembly.service("m05_handoff_port") is not None

    # All five trusted ports plus artifacts and the server facade: a complete binding.
    assert assembly.wired is True
    assert assembly.blocker_codes == ()


def test_m04e_stage_names_every_absent_port() -> None:
    """Each trusted port the host omits is one blocker naming it, never a silent skip."""

    for argument, port in (
        ("reader", "CandidateReader"),
        ("store", "EvidenceStore"),
        ("gates", "UpstreamGatePort"),
        ("proofs", "CandidateProofPort"),
        ("m05_handoff", "M05HandoffPort"),
    ):
        assembly = _m04e_complete(**{argument: None})
        assert assembly.wired is False, port
        assert _codes(assembly) == {"m04e_required_port_missing"}, port
        assert {blocker.port for blocker in assembly.blockers} == {port}
    assert _m04e_complete(store=None).service("evidence_backfill") is None


def test_m04e_stage_refuses_without_artifact_content_port() -> None:
    assembly = _m04e_complete(artifacts=None)
    assert assembly.service("evidence_backfill") is None
    assert "m04e_artifact_content_port_missing" in _codes(assembly)


def test_m04e_stage_refuses_without_a_server_repository() -> None:
    """M04E's persistence adapter needs a caller-owned SERIALIZABLE session, so it cannot be
    built from a session factory here — the host must supply the facade or the stage blocks."""

    assembly = _m04e_complete(server_repository=None)
    assert assembly.service("server_definition_version_repository") is None
    assert "m04e_server_definition_repository_missing" in _codes(assembly)


def test_m04e_stage_refuses_an_unbounded_page_size() -> None:
    assembly = _m04e_complete(page_size=2000)
    assert assembly.service("evidence_backfill") is None
    assert "m04e_page_size_invalid" in _codes(assembly)


def test_m04e_stage_reports_each_absent_port_separately() -> None:
    assembly = build_m04e_definition_version_stage(
        reader=None, store=None, gates=None, proofs=None, artifacts=None,
        server_repository=None, m05_handoff=None,
    )
    assert assembly.services == ()
    codes = _codes(assembly)
    assert codes == {
        "m04e_required_port_missing",
        "m04e_artifact_content_port_missing",
        "m04e_server_definition_repository_missing",
    }
    # One blocker per omitted trusted port: nothing collapses into a single generic refusal.
    assert {blocker.port for blocker in assembly.blockers if blocker.port} == {
        "CandidateReader", "EvidenceStore", "UpstreamGatePort", "CandidateProofPort",
        "M05HandoffPort", "ArtifactContentPort", "ServerDefinitionVersionRepository",
    }


# ---------------------------------------------------------------------------
# 6. M05 assembly
# ---------------------------------------------------------------------------


def _m05_complete(**overrides: object) -> StageAssembly:
    arguments: dict[str, object] = {
        "session_factory": fx.session_factory(),
        "tenant_context": fx.trusted_tenant_context(),
        "runtime_context": fx.runtime_context(),
        "upstream_evidence": fx.upstream_evidence(),
        "promotion_records": fx.InertPort("m05-promotion-records"),
        "identity_sessions": fx.InertPort("runtime-session-ownership"),
    }
    arguments.update(overrides)
    return build_m05_instance_runtime_stage(**arguments)  # type: ignore[arg-type]


def test_m05_stage_assembles_import_and_exact_resolution() -> None:
    assembly = _m05_complete()
    assert isinstance(assembly.service("import_service"), InstanceImportService)
    assert assembly.service("resolve_plugin_instance_port") is not None
    assert isinstance(assembly.service("promotion_service"), M05PromotionService)
    assert assembly.service("m05_unit_of_work") is not None
    assert assembly.service("identity_evidence") is not None
    assert assembly.service("audit_sink") is not None

    assert assembly.wired is True
    assert assembly.blocker_codes == ()


def test_m05_stage_refuses_without_upstream_evidence() -> None:
    """``build_sql_resolve_plugin_instance_port(upstream=None)`` would deny everything silently."""

    assembly = _m05_complete(upstream_evidence=None)
    assert "m05_upstream_evidence_missing" in _codes(assembly)
    assert assembly.wired is False


def test_m05_stage_refuses_an_unusable_runtime_context() -> None:
    assembly = _m05_complete(runtime_context=fx.runtime_context(usable=False))
    assert "m05_runtime_context_missing" in _codes(assembly)
    assert assembly.service("import_service") is None
    assert assembly.service("resolve_plugin_instance_port") is None
    # The unit of work is still assembled: it is the run context, not the factory, that is absent.
    assert assembly.service("m05_unit_of_work") is not None


def test_m05_stage_refuses_without_tenant_context() -> None:
    assembly = _m05_complete(tenant_context=None)
    assert "m05_required_port_missing" in _codes(assembly)
    assert {blocker.port for blocker in assembly.blockers
            if blocker.code == "m05_required_port_missing"} == {"TrustedTenantContextPort"}
    assert assembly.service("m05_unit_of_work") is None
    assert assembly.service("import_service") is None
    assert assembly.wired is False


def test_m05_stage_refuses_without_a_promotion_record_port() -> None:
    """No promotion-record writer means no ``AcceptedPartitionEvidence`` can reach M06."""

    assembly = _m05_complete(promotion_records=None)
    assert assembly.service("promotion_service") is None
    assert {blocker.port for blocker in assembly.blockers
            if blocker.code == "m05_required_port_missing"} == {"M05PromotionRecordPort"}
    assert assembly.wired is False


def test_m05_stage_refuses_an_unbounded_page_size() -> None:
    assembly = _m05_complete(page_size=0)
    assert "m05_page_size_invalid" in _codes(assembly)
    assert assembly.service("import_service") is None


def test_m05_upstream_fixture_is_fail_closed() -> None:
    """The M05 gate cannot open while the M04E marker is absent (review §3 G1)."""

    evidence = fx.upstream_evidence()
    assert evidence.ready_for_import(fx.PARTITION, fx.SNAPSHOT_DIGEST) is False
    assert evidence.ready_for_positive_transition(fx.PARTITION) is False


# ---------------------------------------------------------------------------
# 7. M06 assembly
# ---------------------------------------------------------------------------


def _m06_complete(**overrides: object) -> StageAssembly:
    arguments: dict[str, object] = {
        "ports": fx.shadow_gateway_ports(),
        "parity_observations": fx.InertPort("parity-observations"),
        "parity_summaries": fx.InertPort("parity-summaries"),
        "parity_reports": fx.InertPort("parity-reports"),
    }
    arguments.update(overrides)
    return build_m06_grants_gateway_stage(**arguments)  # type: ignore[arg-type]


def test_m06_stage_builds_a_shadow_only_gateway() -> None:
    assembly = _m06_complete()
    gateway = assembly.service("shadow_gateway")
    assert isinstance(gateway, CapabilityGatewayService)
    # Shadow invariants are non-negotiable, whatever the host supplies.
    assert gateway.DECISION_OWNER == "legacy"
    assert gateway.CANONICAL_READ_OWNER == "legacy"
    assert gateway.CANONICAL_POSITIVE_WRITES is False
    assert isinstance(assembly.service("parity_runner"), BoundedParityRunner)
    assert assembly.wired is False
    assert _codes(assembly) == {"m06_partition_handoff_read_port_unimplemented"}


def test_m06_stage_converts_the_legacy_port_refusal_into_a_blocker() -> None:
    """``build_shadow_gateway`` raises on ``legacy_scope=None``; the chain must name it."""

    assembly = _m06_complete(ports=fx.shadow_gateway_ports(legacy_scope=None))
    assert assembly.service("shadow_gateway") is None
    assert "m06_legacy_scope_and_quota_ports_required" in _codes(assembly)


def test_m06_stage_refuses_without_ports() -> None:
    assembly = _m06_complete(ports=None)
    assert assembly.service("shadow_gateway") is None
    codes = _codes(assembly)
    assert "m06_shadow_gateway_ports_missing" in codes
    assert "m06_partition_handoff_read_port_unimplemented" in codes
    # Parity can still be assembled (it does not need the facade), but nothing consumes it
    # while the gateway is missing — the blockers keep the stage unwired.
    assert assembly.wired is False


def test_m06_stage_refuses_without_a_report_store_but_keeps_the_runner() -> None:
    assembly = _m06_complete(parity_reports=None)
    assert isinstance(assembly.service("parity_runner"), BoundedParityRunner)
    assert "m06_parity_report_store_missing" in _codes(assembly)


def test_m06_stage_refuses_without_parity_observations() -> None:
    assembly = _m06_complete(parity_observations=None)
    assert assembly.service("parity_runner") is None
    assert "m06_parity_observations_port_missing" in _codes(assembly)


def test_durable_evidence_ports_need_a_session_factory() -> None:
    ports, blockers = build_m06_durable_evidence_ports(session_factory=None)
    assert ports is None
    assert [blocker.code for blocker in blockers] == ["m06_session_factory_missing"]


def test_durable_evidence_ports_bind_one_store_to_both_contracts() -> None:
    ports, blockers = build_m06_durable_evidence_ports(session_factory=fx.session_factory())
    assert blockers == ()
    assert ports is not None
    assert ports.evidence_read is ports.evidence_transaction
    assert ports.parity_observations is ports.evidence_read
    assert ports.parity_summaries is not ports.parity_reports


# ---------------------------------------------------------------------------
# 8. cutover assembly
# ---------------------------------------------------------------------------


def test_cutover_stage_is_wired_when_every_port_with_an_implementation_is_supplied() -> None:
    assembly = build_cutover_stage(
        repository=fx.InertPort("cutover-repository"),
        gate_verifier=fx.InertPort("gate-verifier"),
        legacy_owner_verifier=fx.InertPort("legacy-owner-verifier"),
        rollback_verifier=fx.InertPort("rollback-verifier"),
        canonical_repair_verifier=fx.InertPort("repair-verifier"),
    )
    assert isinstance(assembly.service("cutover_command_service"), CutoverCommandService)
    assert assembly.blocker_codes == ()
    assert assembly.wired is True


@pytest.mark.parametrize("missing", [
    "repository", "gate_verifier", "legacy_owner_verifier", "rollback_verifier",
    "canonical_repair_verifier",
])
def test_cutover_stage_names_each_omitted_port(missing: str) -> None:
    """A partial graph is never "merely not yet": the omitted port is named."""

    arguments = {
        "repository": fx.InertPort("cutover-repository"),
        "gate_verifier": fx.InertPort("gate-verifier"),
        "legacy_owner_verifier": fx.InertPort("legacy-owner-verifier"),
        "rollback_verifier": fx.InertPort("rollback-verifier"),
        "canonical_repair_verifier": fx.InertPort("repair-verifier"),
    }
    arguments[missing] = None
    assembly = build_cutover_stage(**arguments)  # type: ignore[arg-type]
    assert assembly.wired is False
    assert _codes(assembly) == {"cutover_required_port_missing"}
    assert len(assembly.blockers) == 1
    assert assembly.blockers[0].port == {
        "repository": "CutoverRepository",
        "gate_verifier": "GateVerifier",
        "legacy_owner_verifier": "LegacyOwnerVerifier",
        "rollback_verifier": "RollbackVerifier",
        "canonical_repair_verifier": "CanonicalRepairVerifier",
    }[missing]
    # With a repository the service is constructible even when a verifier is absent: the port
    # exists, so its fail-closed refusals stay reachable and testable.
    if missing != "repository":
        assert isinstance(assembly.service("cutover_command_service"), CutoverCommandService)
    else:
        assert assembly.service("cutover_command_service") is None


def test_cutover_stage_refuses_without_a_repository() -> None:
    assembly = build_cutover_stage(
        repository=None, gate_verifier=None, legacy_owner_verifier=None,
        rollback_verifier=None, canonical_repair_verifier=None,
    )
    assert assembly.services == ()
    assert assembly.wired is False
    assert _codes(assembly) == {"cutover_required_port_missing"}
    assert {blocker.port for blocker in assembly.blockers} == {
        "CutoverRepository", "GateVerifier", "LegacyOwnerVerifier", "RollbackVerifier",
        "CanonicalRepairVerifier",
    }


def test_cutover_stage_refuses_an_unbounded_command_size() -> None:
    assembly = build_cutover_stage(
        repository=fx.InertPort("cutover-repository"),
        gate_verifier=None, legacy_owner_verifier=None, rollback_verifier=None,
        canonical_repair_verifier=None, max_command_bytes=0,
    )
    assert assembly.service("cutover_command_service") is None
    assert "cutover_max_command_bytes_invalid" in _codes(assembly)
    assert assembly.wired is False


# ---------------------------------------------------------------------------
# 9. whole chain
# ---------------------------------------------------------------------------


def _chain_dependencies(**overrides: object) -> ChainDependencies:
    arguments: dict[str, object] = {
        "session_factory": fx.session_factory(),
        "m04_tenant_context": fx.tenant_scope_context(),
        "m04e_reader": fx.InertPort("candidate-reader"),
        "m04e_store": fx.InertPort("evidence-store"),
        "m04e_gates": fx.InertPort("upstream-gates"),
        "m04e_proofs": fx.InertPort("candidate-proofs"),
        "m04e_artifacts": fx.InertPort("artifact-content"),
        "m04e_server_repository": fx.server_definition_repository(),
        "m04e_m05_handoff": fx.InertPort("m05-handoff"),
        "m04e_page_size": 100,
        "m05_tenant_context": fx.trusted_tenant_context(),
        "m05_runtime_context": fx.runtime_context(),
        "m05_upstream_evidence": fx.upstream_evidence(),
        "m05_promotion_records": fx.InertPort("m05-promotion-records"),
        "m05_identity_sessions": fx.InertPort("runtime-session-ownership"),
        "m05_page_size": 100,
        "m06_ports": fx.shadow_gateway_ports(),
        "m06_parity_observations": fx.InertPort("parity-observations"),
        "m06_parity_summaries": fx.InertPort("parity-summaries"),
        "m06_parity_reports": fx.InertPort("parity-reports"),
        "cutover_repository": fx.InertPort("cutover-repository"),
        "cutover_gate_verifier": fx.InertPort("gate-verifier"),
        "cutover_legacy_owner_verifier": fx.InertPort("legacy-owner-verifier"),
        "cutover_rollback_verifier": fx.InertPort("rollback-verifier"),
        "cutover_canonical_repair_verifier": fx.InertPort("repair-verifier"),
    }
    arguments.update(overrides)
    return ChainDependencies(**arguments)  # type: ignore[arg-type]


def test_chain_keeps_every_stage_in_order() -> None:
    chain = assemble_migration_chain(_chain_dependencies())
    assert isinstance(chain, MigrationChain)
    assert chain.ordered_stages == tuple(MigrationStage)
    assert [assembly.stage for assembly in chain.stages] == list(MigrationStage)


def test_chain_is_wired_except_for_the_one_remaining_gap() -> None:
    """A fully supplied graph is a complete binding for four stages, never for M06.

    M06 keeps ``PartitionHandoffReadPort`` in the registry because its builder does not take
    the evidence ports yet, so ``DependencyGates`` still resolves to ``None`` there. Every
    other stage is wired, which is exactly what closing G1/G2/G4/G5/G6/G7 means.
    """

    chain = assemble_migration_chain(_chain_dependencies())
    assert chain.wired is False
    assert set(chain.blocker_codes) == EXPECTED_GAP_CODES
    first = chain.first_blocker
    assert isinstance(first, AssemblyBlocker)
    assert first.stage is MigrationStage.M06_GRANTS_GATEWAY
    assert first.code == "m06_partition_handoff_read_port_unimplemented"
    blocked = {assembly.stage for assembly in chain.stages if not assembly.wired}
    assert blocked == {MigrationStage.M06_GRANTS_GATEWAY}
    assert all(
        assembly.wired for assembly in chain.stages
        if assembly.stage is not MigrationStage.M06_GRANTS_GATEWAY
    )


def test_chain_with_nothing_supplied_still_reports_every_stage() -> None:
    """An empty host configuration must fail loudly, stage by stage, not collapse."""

    empty = ChainDependencies(
        session_factory=None,
        m04_tenant_context=None,
        m04e_reader=None,
        m04e_store=None,
        m04e_gates=None,
        m04e_proofs=None,
        m04e_artifacts=None,
        m04e_server_repository=None,
        m04e_m05_handoff=None,
        m04e_page_size=100,
        m05_tenant_context=None,
        m05_runtime_context=None,
        m05_upstream_evidence=None,
        m05_promotion_records=None,
        m05_identity_sessions=None,
        m05_page_size=100,
        m06_ports=None,
        m06_parity_observations=None,
        m06_parity_summaries=None,
        m06_parity_reports=None,
        cutover_repository=None,
        cutover_gate_verifier=None,
        cutover_legacy_owner_verifier=None,
        cutover_rollback_verifier=None,
        cutover_canonical_repair_verifier=None,
    )
    chain = assemble_migration_chain(empty)
    assert chain.ordered_stages == tuple(MigrationStage)
    assert all(assembly.services == () for assembly in chain.stages)
    assert chain.wired is False
    codes = set(chain.blocker_codes)
    assert EXPECTED_GAP_CODES <= codes
    # Each stage names its own missing dependencies instead of one anonymous refusal.
    assert {
        "m04_session_factory_missing",
        "m04_required_port_missing",
        "m04e_required_port_missing",
        "m05_upstream_evidence_missing",
        "m05_required_port_missing",
        "m06_shadow_gateway_ports_missing",
        "cutover_required_port_missing",
    } <= codes
    # Every stage refuses all of its services, and no stage silently reports itself wired.
    assert all(not assembly.wired for assembly in chain.stages)


def test_chain_rejects_a_gap_in_the_stage_list() -> None:
    chain = assemble_migration_chain(_chain_dependencies())
    with pytest.raises(ValueError, match="reviewed order"):
        MigrationChain(stages=chain.stages[:-1])


def test_chain_lookup_by_stage() -> None:
    chain = assemble_migration_chain(_chain_dependencies())
    assert chain.by_stage(MigrationStage.M04_SCOPE).stage is MigrationStage.M04_SCOPE
    with pytest.raises(KeyError):
        chain.by_stage("nope")  # type: ignore[arg-type]


def test_every_stage_assembly_reports_its_own_stage() -> None:
    chain = assemble_migration_chain(_chain_dependencies())
    for assembly in chain.stages:
        assert isinstance(assembly, StageAssembly)
        assert all(blocker.stage is assembly.stage for blocker in assembly.blockers)
        assert isinstance(stage_spec(assembly.stage), StageSpec)
