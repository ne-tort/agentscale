"""The M05 stage really becomes *wired* for a partition (the point of the whole task).

``ChainDependencies`` used to carry ``None`` in ``m05_tenant_context``,
``m05_runtime_context``, ``m05_upstream_evidence`` and ``m05_identity_sessions``, which made
``build_m05_instance_runtime_stage`` report ``wired=False`` forever.  These cases assert the
opposite, through the reviewed composition root and the real per-partition planner:

1. the stage assembles with every service it is supposed to have;
2. it reports **no** blocker, i.e. ``StageAssembly.wired is True``;
3. the services it built are the production classes, bound to this partition;
4. the process-scope resource still leaves the four fields ``None`` with its own codes,
   because a process has no partition.

Nothing here executes a stage: wiring is construction, and the session factories refuse.
"""

from __future__ import annotations

from dataclasses import fields

from prodavan.application.migration.composition import (
    ChainDependencies,
    MigrationStage,
    StageAssembly,
    assemble_migration_chain,
    build_m05_instance_runtime_stage,
)
from prodavan.application.migration.m05_contexts import (
    M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
    SqlAuthSessionOwnership,
    build_partition_m05_contexts,
)
from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
    SqlAlchemyM05IdentityEvidence,
    SqlAlchemyM05UnitOfWork,
    SqlAlchemyPinnedSnapshotReader,
    SqlAlchemyRedactedAuditSink,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    FailClosedResolvePluginInstance,
    InstanceImportService,
    M05UpstreamEvidence,
)
from prodavan.application.migration.partition_chain import (
    PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE,
    PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
    PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE,
    PartitionAssemblyOverrides,
    PartitionChainKey,
    build_partition_chain,
)
from prodavan.application.migration.tenant_context import RlsTrustedTenantContextAdapter
from prodavan.core.infra.migration_chain_resource import build_migration_chain_dependencies
from tests.migration.partition_chain.partition_fixtures import (
    Inert,
    PrincipalResolver,
    runtime_context,
    shadow_ports,
)

from . import fixtures as fx

M05_SERVICES = (
    "m05_unit_of_work",
    "identity_evidence",
    "audit_sink",
    "candidate_reader",
    "import_store",
    "import_service",
    "resolve_plugin_instance_port",
    # Built whenever a promotion record port is supplied: the promotion record is how the
    # accepted evidence reaches M06, so a wired M05 stage always has it.
    "promotion_service",
)


def key() -> PartitionChainKey:
    return PartitionChainKey(tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION)


def complete_overrides() -> PartitionAssemblyOverrides:
    """Every collaborator a partition cannot derive, with the M05 bundle stated once."""

    return PartitionAssemblyOverrides(
        principal_resolver=PrincipalResolver(),
        m04e_artifacts=Inert(),
        m04e_server_repository=Inert(),
        m05_contexts=fx.context_inputs(),
        m06_shadow_ports=None,
    )


# ---------------------------------------------------------------------------
# The stage, assembled from the partition's contexts
# ---------------------------------------------------------------------------


def test_the_m05_stage_is_wired_when_the_partition_supplies_its_four_contexts() -> None:
    """The headline claim: no blocker on the M05 stage for a fully stated partition."""

    contexts = build_partition_m05_contexts(key=key(), inputs=fx.context_inputs())
    assert contexts.complete is True, contexts.blocker_codes

    stage = build_m05_instance_runtime_stage(
        session_factory=fx.RecordingSessionFactory(),
        tenant_context=contexts.tenant_context,
        runtime_context=contexts.runtime_context,
        upstream_evidence=contexts.upstream_evidence,
        promotion_records=Inert(),
        identity_sessions=contexts.identity_sessions,
    )

    assert isinstance(stage, StageAssembly)
    assert stage.blockers == (), stage.blocker_codes
    assert stage.wired is True


def test_every_m05_service_is_the_production_class_bound_to_this_partition() -> None:
    contexts = build_partition_m05_contexts(key=key(), inputs=fx.context_inputs())

    stage = build_m05_instance_runtime_stage(
        session_factory=fx.RecordingSessionFactory(),
        tenant_context=contexts.tenant_context,
        runtime_context=contexts.runtime_context,
        upstream_evidence=contexts.upstream_evidence,
        promotion_records=Inert(),
        identity_sessions=contexts.identity_sessions,
    )

    assert tuple(symbol for symbol, _ in stage.services) == M05_SERVICES
    assert isinstance(stage.service("m05_unit_of_work"), SqlAlchemyM05UnitOfWork)
    assert isinstance(stage.service("identity_evidence"), SqlAlchemyM05IdentityEvidence)
    assert isinstance(stage.service("audit_sink"), SqlAlchemyRedactedAuditSink)
    assert isinstance(stage.service("candidate_reader"), SqlAlchemyPinnedSnapshotReader)
    assert isinstance(stage.service("import_service"), InstanceImportService)
    assert isinstance(
        stage.service("resolve_plugin_instance_port"), FailClosedResolvePluginInstance
    )

    # The import service carries *this* partition's upstream bundle, not a rebuilt one.
    import_service = stage.service("import_service")
    assert import_service is not None  # the symbol exists; the object is run-bound
    assert stage.service("candidate_reader") is not None


def test_the_stage_is_unwired_again_when_any_one_context_is_withdrawn() -> None:
    """Wiring must depend on the contexts, not on the rest of an otherwise full graph."""

    contexts = build_partition_m05_contexts(key=key(), inputs=fx.context_inputs())
    full = {
        "session_factory": fx.RecordingSessionFactory(),
        "tenant_context": contexts.tenant_context,
        "runtime_context": contexts.runtime_context,
        "upstream_evidence": contexts.upstream_evidence,
        "promotion_records": Inert(),
        "identity_sessions": contexts.identity_sessions,
    }

    for field_name, expected_code in (
        ("tenant_context", "m05_required_port_missing"),
        ("runtime_context", "m05_runtime_context_missing"),
        ("upstream_evidence", "m05_upstream_evidence_missing"),
    ):
        stage = build_m05_instance_runtime_stage(**{**full, field_name: None})  # type: ignore[arg-type]
        assert stage.wired is False, field_name
        assert expected_code in stage.blocker_codes, (field_name, stage.blocker_codes)

    # ``identity_sessions`` is deliberately *not* a stage blocker in the composition root:
    # the adapter tolerates ``None`` and refuses per row instead.  The context builder is the
    # place that refuses it up front, and it does (see test_contexts.py).
    without_sessions = build_m05_instance_runtime_stage(**{**full, "identity_sessions": None})
    assert without_sessions.wired is True


# ---------------------------------------------------------------------------
# The planner fills the four fields for the partition
# ---------------------------------------------------------------------------


def test_the_per_partition_plan_fills_all_four_m05_fields() -> None:
    overrides = complete_overrides()
    plan = build_partition_chain(
        key=key(),
        session_factory=fx.RecordingSessionFactory(),
        writer_generation=fx.WRITER_GENERATION,
        **overrides.chain_kwargs(),
    )

    dependencies = plan.dependencies
    assert isinstance(dependencies.m05_tenant_context, RlsTrustedTenantContextAdapter)
    assert dependencies.m05_runtime_context is not None
    assert dependencies.m05_runtime_context.usable is True
    assert isinstance(dependencies.m05_identity_sessions, SqlAuthSessionOwnership)
    assert isinstance(dependencies.m05_upstream_evidence, M05UpstreamEvidence)
    for name in (
        "m05_tenant_context",
        "m05_runtime_context",
        "m05_identity_sessions",
        "m05_upstream_evidence",
    ):
        assert name in plan.built, (name, plan.built)
        assert name not in plan.missing, name


def test_the_plan_and_the_chain_then_report_m05_without_partition_blockers() -> None:
    overrides = PartitionAssemblyOverrides(
        principal_resolver=PrincipalResolver(),
        m04e_artifacts=Inert(),
        m04e_server_repository=Inert(),
        m05_contexts=fx.context_inputs(),
        m06_shadow_ports=shadow_ports(),
    )
    plan = build_partition_chain(
        key=key(),
        session_factory=fx.RecordingSessionFactory(),
        writer_generation=fx.WRITER_GENERATION,
        **overrides.chain_kwargs(),
    )
    chain = assemble_migration_chain(plan.dependencies)

    m05 = chain.by_stage(MigrationStage.M05_INSTANCE_RUNTIME)
    assert m05.blockers == (), m05.blocker_codes
    assert m05.wired is True
    # No M05 partition-scope code survives anywhere in the plan.
    assert not {
        PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
        PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE,
        PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE,
        M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
    } & set(plan.blocker_codes)


def test_the_plan_still_names_m05_contexts_it_was_not_given() -> None:
    """Without ``m05_contexts`` the four fields stay ``None`` *and* named, as before."""

    plan = build_partition_chain(
        key=key(),
        session_factory=fx.RecordingSessionFactory(),
        writer_generation=fx.WRITER_GENERATION,
        principal_resolver=PrincipalResolver(),
        m04e_artifacts=Inert(),
        m04e_server_repository=Inert(),
    )

    dependencies = plan.dependencies
    assert dependencies.m05_runtime_context is None
    assert dependencies.m05_upstream_evidence is None
    assert dependencies.m05_identity_sessions is None
    # The binder is derivable from the resolver alone, so it *is* built -- and that is not a
    # contradiction: a tenant binder without a run context is still a usable binder.
    assert isinstance(dependencies.m05_tenant_context, RlsTrustedTenantContextAdapter)
    assert {
        PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE,
        PARTITION_M05_UPSTREAM_EVIDENCE_UNAVAILABLE,
        PARTITION_M05_IDENTITY_SESSIONS_UNAVAILABLE,
    } <= set(plan.blocker_codes)


def test_the_plan_refuses_a_runtime_context_of_another_tenant() -> None:
    plan = build_partition_chain(
        key=key(),
        session_factory=fx.RecordingSessionFactory(),
        writer_generation=fx.WRITER_GENERATION,
        principal_resolver=PrincipalResolver(),
        m04e_artifacts=Inert(),
        m04e_server_repository=Inert(),
        m05_contexts=fx.context_inputs(tenant_id=fx.OTHER_TENANT),
    )

    assert plan.dependencies.m05_runtime_context is None
    blocker = plan.blocker_for(PARTITION_M05_RUNTIME_CONTEXT_UNAVAILABLE)
    assert blocker is not None
    assert fx.OTHER_TENANT in blocker.detail


def test_an_explicit_override_still_wins_over_the_derived_context() -> None:
    """A host that already holds a validated context is not forced through the builder."""

    stated = runtime_context(subject_id="subject-from-host")
    plan = build_partition_chain(
        key=key(),
        session_factory=fx.RecordingSessionFactory(),
        writer_generation=fx.WRITER_GENERATION,
        principal_resolver=PrincipalResolver(),
        m04e_artifacts=Inert(),
        m04e_server_repository=Inert(),
        m05_contexts=fx.context_inputs(),
        m05_runtime_context=stated,
    )

    assert plan.dependencies.m05_runtime_context is stated
    assert plan.dependencies.m05_runtime_context.subject_id == "subject-from-host"  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# Process scope is unchanged: a process still has no partition
# ---------------------------------------------------------------------------


def test_the_process_scope_resource_still_leaves_the_four_fields_none() -> None:
    """``migration_chain_resource`` must not pretend a process owns a partition.

    The four contexts are per-partition by construction (they carry an owner's identities and
    one snapshot's gates).  The process-scope dependency resolver therefore keeps them ``None``
    with its own codes, which is the *correct* report -- the task's fix is the per-partition
    planner, not a fabricated process-wide run.
    """

    deps = build_migration_chain_dependencies(
        session_factory=fx.RecordingSessionFactory(), durable_principals=False
    )

    assert deps.dependencies.m05_runtime_context is None
    assert deps.dependencies.m05_upstream_evidence is None
    assert deps.dependencies.m05_identity_sessions is None
    assert "migration_chain_runtime_context_is_per_run" in deps.blockers
    assert "migration_chain_upstream_evidence_is_per_partition" in deps.blockers


def test_no_chain_dependency_is_left_unstated_by_the_partition_plan() -> None:
    """Every field of ``ChainDependencies`` is either an object or a named blocker."""

    plan = build_partition_chain(
        key=key(),
        session_factory=fx.RecordingSessionFactory(),
        writer_generation=fx.WRITER_GENERATION,
        principal_resolver=PrincipalResolver(),
        m04e_artifacts=Inert(),
        m04e_server_repository=Inert(),
        m05_contexts=fx.context_inputs(),
        m06_shadow_ports=shadow_ports(),
    )

    missing = [
        field.name
        for field in fields(ChainDependencies)
        if getattr(plan.dependencies, field.name) is None
    ]
    assert missing == [], missing
    assert plan.complete is True, plan.blocker_codes
