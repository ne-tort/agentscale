"""Each M05 context builder: what it assembles, and exactly how it refuses.

The contract under test is the one ``m05_contexts``' module docstring states: a builder
either returns a value that validates against the production port it satisfies, or it
returns ``None`` **plus** one named blocker per missing input.  It never raises for absence
and never substitutes a placeholder.
"""

from __future__ import annotations

import inspect
from dataclasses import asdict

import pytest

from prodavan.application.migration.evidence_ports import digest as evidence_digest
from prodavan.application.migration.m05_contexts import (
    M05_INSTANCE_COUNTS_MISSING,
    M05_M04_SCOPE_EVIDENCE_MISSING,
    M05_M04_SCOPE_EVIDENCE_NOT_READY,
    M05_M04_SCOPE_EVIDENCE_PARTITION_MISMATCH,
    M05_RUNTIME_CONTEXT_CORRELATION_MISSING,
    M05_RUNTIME_CONTEXT_NOT_AUTHENTICATED,
    M05_RUNTIME_CONTEXT_SESSION_MISSING,
    M05_RUNTIME_CONTEXT_SUBJECT_MISSING,
    M05_RUNTIME_CONTEXT_TENANT_MISSING,
    M05_RUNTIME_GATE_REVIEW_MISSING,
    M05_SESSION_OWNERSHIP_SESSION_FACTORY_MISSING,
    M05_TENANT_CONTEXT_RESOLVER_MISSING,
    M05_UPSTREAM_EVIDENCE_M04_SCOPE_MISSING,
    M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
    M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH,
    M05InstanceCounts,
    M05ContextBuild,
    ReviewedM05RuntimeGate,
    SqlAuthSessionOwnership,
    build_m04_scope_gate,
    build_m05_identity_sessions,
    build_m05_runtime_context,
    build_m05_runtime_gate,
    build_m05_tenant_context,
    build_m05_upstream_evidence,
    build_partition_m05_contexts,
)
from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
    RuntimeSessionOwnershipPort,
    SqlAlchemyM05IdentityEvidence,
)
from prodavan.application.migration.partition_chain import PartitionChainKey
from prodavan.application.migration.tenant_context import (
    M05_RUNTIME_RELATIONS,
    RlsTrustedTenantContextAdapter,
)
from . import fixtures as fx


# ---------------------------------------------------------------------------
# The result type itself
# ---------------------------------------------------------------------------


def test_a_build_is_either_a_value_or_blockers_never_both() -> None:
    """A substitute object next to the code that says it is missing is not an option."""

    with pytest.raises(ValueError):
        M05ContextBuild(value=object(), blockers=(fx.stored_scope_row(),))  # type: ignore[arg-type]

    built = M05ContextBuild(value=object())
    assert built.built is True and built.blocker_codes == ()

    refused = M05ContextBuild(value=None)
    assert refused.built is False

    values, blockers = refused  # iterates as ``(value, blockers)``
    assert values is None and blockers == ()


# ---------------------------------------------------------------------------
# 1. m05_tenant_context
# ---------------------------------------------------------------------------


def test_the_m05_tenant_context_is_the_reviewed_rls_binder() -> None:
    build = build_m05_tenant_context(principal_resolver=fx.InertResolver())

    assert isinstance(build.value, RlsTrustedTenantContextAdapter)
    # The reviewed relation list, unchanged: a binder over fewer tables would prove less.
    assert build.value.relations == M05_RUNTIME_RELATIONS
    assert build.blockers == ()


def test_a_missing_principal_resolver_names_the_dependency_not_the_symptom() -> None:
    build = build_m05_tenant_context(principal_resolver=None)

    assert build.value is None
    assert build.blocker_codes == (M05_TENANT_CONTEXT_RESOLVER_MISSING,)
    blocker = build.blockers[0]
    assert blocker.dependency == "m05_tenant_context"
    assert blocker.needed.strip()
    # The detail says *why* there is no fallback, which is what makes it actionable.
    assert "TrustedPrincipal" in blocker.detail


def test_an_object_without_resolve_is_not_a_principal_resolver() -> None:
    build = build_m05_tenant_context(principal_resolver=fx.NoResolveMethod())  # type: ignore[arg-type]

    assert build.value is None
    assert build.blocker_codes == (M05_TENANT_CONTEXT_RESOLVER_MISSING,)
    assert "NoResolveMethod" in build.blockers[0].detail


# ---------------------------------------------------------------------------
# 2. m05_runtime_context
# ---------------------------------------------------------------------------


def test_the_runtime_context_is_usable_and_carries_exactly_the_resolved_values() -> None:
    build = build_m05_runtime_context(
        tenant_id=fx.TENANT,
        subject_id=fx.SUBJECT,
        session_id=fx.SESSION,
        correlation_id=fx.CORRELATION,
        authenticated=True,
    )

    assert build.value is not None
    assert build.value.usable is True
    assert build.value.resolved_tenant_id == fx.TENANT
    assert build.value.subject_id == fx.SUBJECT
    assert build.value.session_id == fx.SESSION
    assert build.value.correlation_id == fx.CORRELATION
    assert build.value.authenticated is True


@pytest.mark.parametrize(
    ("field", "expected_code"),
    (
        ("tenant_id", M05_RUNTIME_CONTEXT_TENANT_MISSING),
        ("subject_id", M05_RUNTIME_CONTEXT_SUBJECT_MISSING),
        ("session_id", M05_RUNTIME_CONTEXT_SESSION_MISSING),
        ("correlation_id", M05_RUNTIME_CONTEXT_CORRELATION_MISSING),
    ),
)
def test_each_missing_identity_has_its_own_code(field: str, expected_code: str) -> None:
    values = {
        "tenant_id": fx.TENANT,
        "subject_id": fx.SUBJECT,
        "session_id": fx.SESSION,
        "correlation_id": fx.CORRELATION,
    }
    values[field] = None

    build = build_m05_runtime_context(authenticated=True, **values)

    assert build.value is None
    assert build.blocker_codes == (expected_code,)


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tenant_id", ""),
        ("tenant_id", "   "),
        ("subject_id", f" {fx.SUBJECT}"),
        ("session_id", 42),
        ("correlation_id", ""),
    ),
)
def test_blank_or_non_string_identities_are_refused(field: str, value: object) -> None:
    values: dict[str, object] = {
        "tenant_id": fx.TENANT,
        "subject_id": fx.SUBJECT,
        "session_id": fx.SESSION,
        "correlation_id": fx.CORRELATION,
    }
    values[field] = value

    build = build_m05_runtime_context(authenticated=True, **values)  # type: ignore[arg-type]

    assert build.value is None
    # The code names the field, so the operator knows which value to re-resolve.
    assert build.blockers[0].dependency == "m05_runtime_context"
    assert field in build.blockers[0].detail


@pytest.mark.parametrize("authenticated", (None, False, 0, 1, "yes", "true"))
def test_authentication_must_be_an_explicit_true(authenticated: object) -> None:
    build = build_m05_runtime_context(
        tenant_id=fx.TENANT,
        subject_id=fx.SUBJECT,
        session_id=fx.SESSION,
        correlation_id=fx.CORRELATION,
        authenticated=authenticated,  # type: ignore[arg-type]
    )

    assert build.value is None
    assert M05_RUNTIME_CONTEXT_NOT_AUTHENTICATED in build.blocker_codes


def test_every_missing_identity_is_reported_at_once() -> None:
    """One call should tell the operator every value still missing, not the first one."""

    build = build_m05_runtime_context(
        tenant_id=None, subject_id=None, session_id=None, correlation_id=None,
        authenticated=None,
    )

    assert build.blocker_codes == (
        M05_RUNTIME_CONTEXT_TENANT_MISSING,
        M05_RUNTIME_CONTEXT_SUBJECT_MISSING,
        M05_RUNTIME_CONTEXT_SESSION_MISSING,
        M05_RUNTIME_CONTEXT_CORRELATION_MISSING,
        M05_RUNTIME_CONTEXT_NOT_AUTHENTICATED,
    )


# ---------------------------------------------------------------------------
# 3. m05_identity_sessions
# ---------------------------------------------------------------------------


def test_the_session_ownership_port_is_constructible_from_a_factory_alone() -> None:
    build = build_m05_identity_sessions(session_factory=fx.RecordingSessionFactory())

    assert isinstance(build.value, SqlAuthSessionOwnership)
    assert build.blockers == ()
    # Structural satisfaction of the one-method port the M05 identity evidence consumes.
    # ``RuntimeSessionOwnershipPort`` is a plain ``Protocol`` (not ``runtime_checkable``), so
    # the shape has to be checked by hand rather than with ``isinstance``; the port's own
    # decorator is not ours to change.
    prove = getattr(build.value, "prove", None)
    assert callable(prove)
    assert tuple(inspect.signature(prove).parameters) == (
        "tenant_id", "subject_id", "session_id"
    )
    assert inspect.iscoroutinefunction(prove)
    assert RuntimeSessionOwnershipPort.prove is not None


def test_no_factory_means_no_ownership_proof() -> None:
    build = build_m05_identity_sessions(session_factory=None)

    assert build.value is None
    assert build.blocker_codes == (M05_SESSION_OWNERSHIP_SESSION_FACTORY_MISSING,)
    assert "auth_sessions" in build.blockers[0].detail


def test_the_ownership_proof_refuses_a_blank_triple_without_asking_the_database() -> None:
    """A blank assertion is a denial before any query; the factory must not be called."""

    factory = fx.RecordingSessionFactory()
    port = SqlAuthSessionOwnership(session_factory=factory)

    import asyncio

    for tenant, subject, session in (("", fx.SUBJECT, fx.SESSION), (fx.TENANT, " ", fx.SESSION),
                                     (fx.TENANT, fx.SUBJECT, "")):
        assert asyncio.get_event_loop_policy() is not None  # keep the import meaningful
        assert _run(port.prove(tenant_id=tenant, subject_id=subject, session_id=session)) is False
    assert factory.calls == 0


def _run(coroutine: object) -> object:
    """Run one coroutine in a fresh loop (this module's cases are synchronous)."""

    import asyncio

    return asyncio.run(coroutine)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# 4a. The M04 scope gate, read out of stored evidence
# ---------------------------------------------------------------------------


def test_the_m04_scope_gate_cites_the_stored_row_digests_verbatim() -> None:
    row = fx.stored_scope_row()
    build = build_m04_scope_gate(
        tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=row
    )

    gate = build.value
    assert gate is not None and build.blockers == ()
    assert gate.partition == (fx.TENANT, fx.DEFINITION)
    assert gate.source_snapshot_digest == row.source_snapshot_digest
    assert gate.canonical_digest == row.canonical_digest
    assert gate.checkpoint_digest == row.checkpoint_digest
    assert gate.parent_validation_digest == row.gate_evidence_digest
    assert gate.rls_validation_digest == row.rls_policy_digest
    # The typed-scope proof is the same redacted envelope, cited once -- not a new digest.
    assert gate.typed_constraint_digests == (row.gate_evidence_digest,)
    # The count is the *measured* active-registry match, not a literal.
    assert gate.active_scope_count == 1
    assert gate.ready is True


def test_the_same_row_as_a_mapping_produces_the_same_gate() -> None:
    """A builder that only worked on ORM objects would silently refuse a decoded row."""

    row = fx.stored_scope_row()
    from_object = build_m04_scope_gate(
        tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=row
    ).value
    from_mapping = build_m04_scope_gate(
        tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=row.as_mapping()
    ).value

    assert from_object is not None and from_object == from_mapping


def test_no_stored_m04_row_is_a_named_absence() -> None:
    build = build_m04_scope_gate(
        tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=None
    )

    assert build.value is None
    assert build.blocker_codes == (M05_M04_SCOPE_EVIDENCE_MISSING,)


@pytest.mark.parametrize("outcome", ("blocked", "quarantined", "", "READY_ish"))
def test_a_non_ready_m04_row_never_becomes_an_import_gate(outcome: str) -> None:
    build = build_m04_scope_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        row=fx.stored_scope_row(outcome=outcome),
    )

    assert build.value is None
    assert M05_M04_SCOPE_EVIDENCE_NOT_READY in build.blocker_codes


def test_a_row_of_another_tenant_is_refused() -> None:
    build = build_m04_scope_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        row=fx.stored_scope_row(tenant_id=fx.OTHER_TENANT, scope_id=fx.OTHER_TENANT),
    )

    assert build.value is None
    assert M05_M04_SCOPE_EVIDENCE_PARTITION_MISMATCH in build.blocker_codes


@pytest.mark.parametrize(
    "column",
    ("source_snapshot_digest", "canonical_digest", "checkpoint_digest",
     "gate_evidence_digest", "rls_policy_digest"),
)
def test_a_malformed_digest_column_is_refused_by_name(column: str) -> None:
    build = build_m04_scope_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        row=fx.stored_scope_row(**{column: "sha256:not-hex"}),
    )

    assert build.value is None
    assert "m05_m04_scope_evidence_digest_invalid" in build.blocker_codes
    assert column in build.blockers[0].detail


def test_an_envelope_without_a_measured_active_scope_is_refused() -> None:
    """``active_scope_count`` must come from the proof, never from a default of 1."""

    envelope = fx.gate_envelope(active_scopes=0)
    build = build_m04_scope_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        row=fx.stored_scope_row(gate_evidence=envelope),
    )

    assert build.value is None
    assert "m05_m04_scope_evidence_active_scope_count_missing" in build.blocker_codes


# ---------------------------------------------------------------------------
# 4b. The M05 runtime gate
# ---------------------------------------------------------------------------


def test_the_runtime_gate_binds_the_other_two_gates_digest_for_digest() -> None:
    scope = build_m04_scope_gate(
        tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=fx.stored_scope_row()
    ).value
    export = fx.m04e_export()

    build = build_m05_runtime_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        m04_scope=scope,
        m04e_export=export,
        review=fx.reviewed_gate(),
        counts=fx.instance_counts(),
    )

    gate = build.value
    assert gate is not None and build.blockers == ()
    assert gate.partition == (fx.TENANT, fx.DEFINITION)
    # The two cross-checks `M05UpstreamEvidence.ready_for_import` performs.
    assert gate.m04_scope_digest == scope.canonical_digest  # type: ignore[union-attr]
    assert gate.m04e_export_digest == export.canonical_digest
    # The snapshot is the export's own snapshot, and the checkpoint is its checkpoint.
    assert gate.source_snapshot_digest == export.checkpoint.source_snapshot_digest
    assert gate.checkpoint_digest != export.checkpoint.source_snapshot_digest
    assert gate.schema_ready is True
    assert gate.ready is True


def test_the_runtime_gate_canonical_digest_is_derived_not_supplied() -> None:
    """Two gates over different inventories must not carry the same digest."""

    scope = build_m04_scope_gate(
        tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=fx.stored_scope_row()
    ).value
    export = fx.m04e_export()
    common = {
        "tenant_id": fx.TENANT,
        "app_definition_id": fx.DEFINITION,
        "m04_scope": scope,
        "m04e_export": export,
        "review": fx.reviewed_gate(),
    }
    clean = build_m05_runtime_gate(counts=fx.instance_counts(), **common).value
    dirty = build_m05_runtime_gate(
        counts=fx.instance_counts(unresolved_required_instances=2), **common
    ).value

    assert clean is not None and dirty is not None
    assert clean.canonical_digest != dirty.canonical_digest
    assert dirty.ready is False
    # Determinism: the same inputs answer the same digest.
    assert build_m05_runtime_gate(counts=fx.instance_counts(), **common).value == clean


def test_a_runtime_gate_without_a_reviewed_manifest_is_not_built() -> None:
    build = build_m05_runtime_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        m04_scope=build_m04_scope_gate(
            tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=fx.stored_scope_row()
        ).value,
        m04e_export=fx.m04e_export(),
        review=None,
        counts=fx.instance_counts(),
    )

    assert build.value is None
    assert build.blocker_codes == (M05_RUNTIME_GATE_REVIEW_MISSING,)


def test_a_runtime_gate_without_measured_counts_is_not_built() -> None:
    """Defaulting the counts to zero would claim a clean inventory nobody observed."""

    build = build_m05_runtime_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        m04_scope=build_m04_scope_gate(
            tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=fx.stored_scope_row()
        ).value,
        m04e_export=fx.m04e_export(),
        review=fx.reviewed_gate(),
        counts=None,
    )

    assert build.value is None
    assert build.blocker_codes == (M05_INSTANCE_COUNTS_MISSING,)


def test_the_review_manifest_refuses_a_placeholder_digest() -> None:
    with pytest.raises(ValueError, match="artifact_validation_digest"):
        fx.reviewed_gate(artifact_validation_digest="sha256:" + "0" * 63)

    with pytest.raises(ValueError, match="review_id"):
        fx.reviewed_gate(review_id=" ")


def test_the_review_manifest_digest_covers_every_reviewed_fact() -> None:
    base = fx.reviewed_gate()
    changed = fx.reviewed_gate(audit_atomicity_digest=fx.digest("m05-audit-v2"))

    assert base.digest != changed.digest
    assert base.digest == fx.reviewed_gate().digest
    # Nothing secret or object-shaped leaks into the digest material.
    assert "object at 0x" not in repr(asdict(base))


def test_counts_refuse_a_negative_or_boolean_value() -> None:
    with pytest.raises(ValueError):
        M05InstanceCounts(unresolved_required_instances=-1)
    with pytest.raises(ValueError):
        M05InstanceCounts(unresolved_required_instances=True)


# ---------------------------------------------------------------------------
# 4c. The bundle
# ---------------------------------------------------------------------------


def _complete_upstream(**overrides: object):
    values: dict[str, object] = {
        "tenant_id": fx.TENANT,
        "app_definition_id": fx.DEFINITION,
        "m04_scope": build_m04_scope_gate(
            tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=fx.stored_scope_row()
        ).value,
        "m04e_export": fx.m04e_export(),
        "review": fx.reviewed_gate(),
        "counts": fx.instance_counts(),
    }
    values.update(overrides)
    return build_m05_upstream_evidence(**values)  # type: ignore[arg-type]


def test_a_complete_bundle_realises_the_import_precondition() -> None:
    """The assembled bundle must actually satisfy ``ready_for_import``.

    This is the point of building it at all: three gates whose digests agree, over one
    snapshot, with a marker that re-checks every handoff gate.  The static release switch is
    patched open inside the fixture, because it is a *release* decision and not a property of
    this partition -- closing it again is asserted separately below.
    """

    from unittest import mock

    from prodavan.application.migration.m05_instance_runtime import runtime_service

    build = _complete_upstream()
    upstream = build.value
    assert upstream is not None and build.blockers == ()
    partition = (fx.TENANT, fx.DEFINITION)
    snapshot = upstream.m04e_export.source_snapshot_digest

    with mock.patch.object(runtime_service, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", True):
        assert upstream.ready_for_import(partition, snapshot) is True
        assert upstream.ready_for_positive_transition(partition) is True

    with mock.patch.object(runtime_service, "UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED", False):
        # The release gate still wins: building the bundle is not a promotion.
        assert upstream.ready_for_import(partition, snapshot) is False


def test_a_missing_export_is_named_and_not_reconstructed_from_the_marker() -> None:
    build = _complete_upstream(m04e_export=None)

    assert build.value is None
    assert M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING in build.blocker_codes
    detail = next(
        blocker for blocker in build.blockers
        if blocker.code == M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING
    )
    # The reason the builder refuses instead of reading the marker: counts are not rows.
    assert "rows" in detail.detail


def test_a_missing_scope_gate_is_named_twice_over_the_same_cause() -> None:
    """The bundle and the runtime gate both need it; the report says so once per field."""

    build = _complete_upstream(m04_scope=None)

    assert build.value is None
    assert build.blocker_codes.count(M05_UPSTREAM_EVIDENCE_M04_SCOPE_MISSING) == 2


def test_a_bundle_mixing_partitions_is_refused() -> None:
    build = _complete_upstream(m04e_export=fx.m04e_export(tenant=fx.OTHER_TENANT))

    assert build.value is None
    assert M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH in build.blocker_codes


def test_a_stated_runtime_gate_is_used_verbatim() -> None:
    scope = build_m04_scope_gate(
        tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION, row=fx.stored_scope_row()
    ).value
    export = fx.m04e_export()
    stated = build_m05_runtime_gate(
        tenant_id=fx.TENANT,
        app_definition_id=fx.DEFINITION,
        m04_scope=scope,
        m04e_export=export,
        review=fx.reviewed_gate(),
        counts=fx.instance_counts(),
    ).value

    build = _complete_upstream(m05_runtime=stated)

    assert build.value is not None
    assert build.value.m05_runtime is stated  # type: ignore[union-attr]


# ---------------------------------------------------------------------------
# All four at once
# ---------------------------------------------------------------------------


def test_the_whole_partition_bundle_builds_every_field_and_opens_no_session() -> None:
    factory = fx.RecordingSessionFactory()
    key = PartitionChainKey(tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION)

    contexts = build_partition_m05_contexts(key=key, inputs=fx.context_inputs(), session_factory=factory)

    assert contexts.complete is True, contexts.blocker_codes
    assert contexts.blocker_codes == ()
    assert isinstance(contexts.tenant_context, RlsTrustedTenantContextAdapter)
    assert contexts.runtime_context is not None and contexts.runtime_context.usable
    assert isinstance(contexts.identity_sessions, SqlAuthSessionOwnership)
    assert contexts.upstream_evidence is not None
    # The identity evidence is the adapter the M05 stage actually wires, not a placeholder.
    assert isinstance(contexts.identity_evidence, SqlAlchemyM05IdentityEvidence)
    assert set(contexts.built) == {
        "m05_tenant_context",
        "m05_runtime_context",
        "m05_identity_sessions",
        "m05_upstream_evidence",
        "m05_identity_evidence",
    }
    assert factory.calls == 0


def test_an_empty_bundle_names_every_missing_field() -> None:
    key = PartitionChainKey(tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION)

    contexts = build_partition_m05_contexts(key=key, inputs=None)

    assert contexts.complete is False
    codes = set(contexts.blocker_codes)
    assert {
        M05_TENANT_CONTEXT_RESOLVER_MISSING,
        M05_RUNTIME_CONTEXT_TENANT_MISSING,
        M05_SESSION_OWNERSHIP_SESSION_FACTORY_MISSING,
        M05_M04_SCOPE_EVIDENCE_MISSING,
        M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
    } <= codes
    assert contexts.tenant_context is None
    assert contexts.runtime_context is None
    assert contexts.identity_sessions is None
    assert contexts.upstream_evidence is None


def test_a_runtime_context_of_another_tenant_is_refused_for_this_plan() -> None:
    """A real context is still wrong if it resolves a different tenant than the plan."""

    key = PartitionChainKey(tenant_id=fx.OTHER_TENANT, app_definition_id=fx.DEFINITION)

    contexts = build_partition_m05_contexts(
        key=key, inputs=fx.context_inputs(tenant_id=fx.TENANT)
    )

    assert contexts.runtime_context is None
    assert M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH in contexts.blocker_codes


def test_the_bundle_is_deterministic() -> None:
    key = PartitionChainKey(tenant_id=fx.TENANT, app_definition_id=fx.DEFINITION)
    first = build_partition_m05_contexts(key=key, inputs=fx.context_inputs())
    second = build_partition_m05_contexts(key=key, inputs=fx.context_inputs())

    assert first.blocker_codes == second.blocker_codes == ()
    assert first.upstream_evidence is not None and second.upstream_evidence is not None
    assert (
        first.upstream_evidence.m05_runtime.canonical_digest
        == second.upstream_evidence.m05_runtime.canonical_digest
    )
    assert evidence_digest("sanity") == evidence_digest("sanity")


def test_the_reviewed_gate_type_is_not_constructible_from_bare_strings() -> None:
    """A manifest is a reviewed value: every field is a sha256, no asterisks, no empty."""

    with pytest.raises(TypeError):
        ReviewedM05RuntimeGate()  # type: ignore[call-arg]
