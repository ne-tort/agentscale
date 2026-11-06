"""MIG-M06-02 acceptance behavior; intentionally unexecuted while migration freeze holds."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from prodavan.application.canonical_grants.contracts import CanonicalGrant
from prodavan.application.capability_method_mapping.contracts import (
    CapabilityMethodMapping,
    MethodIdentity,
    ResourceDomainConstraint,
)
from prodavan.application.migration.m06_grants_gateway.adapter import (
    METHOD_MAPPING_VERSION,
    AdapterOutcome,
    CapabilityGatewayService,
    GrantSetAdapter,
    IdentityResolutionError,
    LegacyGrantSource,
    MappingError,
    MappingOutcome,
    ResolvedGrantIdentity,
    ShadowDecision,
    SourceMappingRecord,
    binding_digest,
    digest,
    request_identity_digest,
    schema_identity,
    schema_identity_digest,
    source_payload_digest,
)
from prodavan.application.migration.m06_grants_gateway.canonical_decision_adapter import CanonicalGrantShadowAdapter
from prodavan.application.migration.m06_grants_gateway.handoff import ServerResolvedHandoffResolver
from prodavan.application.migration.m06_grants_gateway.method_mapping import DeclaredMappingReadPort
from prodavan.application.migration.m06_grants_gateway.parity import bounded_partition_parity
from prodavan.application.migration.m06_grants_gateway.parity_runner import (
    BoundedParityRunner,
    ObservationBatch,
    ParityEvidenceSummary,
    ParityStatus,
    ReportOutcome,
)
from prodavan.domain.dynamic_apps import (
    AppGrant,
    AppScope,
    AppScopeKind,
    CapabilityDecision,
    CapabilityGrant,
    CapabilityName,
    GrantActor,
)

from .acceptance_fixtures import (
    CASES,
    CONTEXT,
    GOOD_GATES,
    GRANTED,
    IDENTITY,
    MAPPING_READ_PORT,
    M04_SCOPE_DIGEST,
    M06_SCHEMA_DIGEST,
    NOW,
    PARTITION,
    RLS,
    SOURCE,
    method_mapping,
)


class _Handoffs:
    """Trusted handoff double: re-derives the gates from the supplied partition."""

    def __init__(self, partition=PARTITION):
        self.partition = partition

    async def resolve_gates(self, *, context, source, source_digest, now):
        class _Partitions:
            def __init__(self, evidence):
                self.evidence = evidence

            async def load_partition(self, **kwargs):
                return self.evidence

        return await ServerResolvedHandoffResolver(
            partitions=_Partitions(self.partition)).resolve_gates(
            context=context, source=source, source_digest=source_digest, now=now)


class _Read:
    def __init__(self, mapping=None):
        self.mapping = mapping
        self.calls = 0

    async def find_mapping(self, *, context, source_kind, source_id, schema_version):
        self.calls += 1
        return self.mapping


class _Store:
    def __init__(self, outcome=MappingOutcome.RECORDED):
        self.outcome = outcome
        self.transactions = []

    async def commit_evidence(self, *, context, transaction):
        self.transactions.append((context, transaction))
        return self.outcome


class _Identity:
    def __init__(self, identity: ResolvedGrantIdentity | None = IDENTITY,
                 error: str | None = None):
        self.identity = identity
        self.error = error

    async def resolve(self, **kwargs):
        if self.error is not None:
            raise IdentityResolutionError(self.error)
        if self.identity is None:
            raise IdentityResolutionError("instance_missing_or_ambiguous")
        return self.identity


class _Legacy:
    def __init__(self, decision=None):
        self.decision = decision

    async def decide(self, *, identity, **kwargs):
        if self.decision is not None:
            return self.decision
        if kwargs.get("method") == "root":
            return ShadowDecision(False, "method_unknown", binding_digest(identity))
        return ShadowDecision(True, "allowed", binding_digest(identity))


class _Canonical:
    def __init__(self, decision=None):
        self.decision = decision

    async def decide_shadow(self, *, identity, **kwargs):
        return self.decision or ShadowDecision(True, "allowed", binding_digest(identity))


def _service(*, identity: ResolvedGrantIdentity | None = IDENTITY, legacy=None, canonical=None,
             read=None, store=None, partition=PARTITION, mapping=None):
    store = store or _Store()
    service = CapabilityGatewayService(
        identity=_Identity(identity), legacy=legacy or _Legacy(),
        canonical_shadow=canonical or _Canonical(),
        handoffs=_Handoffs(partition), evidence_read=read or _Read(), evidence_transaction=store,
        method_mapping=mapping or method_mapping(),
    )
    return service, store


def _observe(service, source=SOURCE, context=CONTEXT, *, capability="storage.files",
             method="read", operation="read", resource="workspace", domain="files", now=NOW):
    return service.observe(source=source, context=context, capability=capability,
                           operation=operation, resource=resource, domain=domain,
                           method=method, checkpoint="checkpoint-1", now=now)


def test_behavior_matrix_names_requested_m06_acceptance_cases():
    expected = ("unknown capability", "unsupported methods", "missing or ambiguous instance",
                "stale runtime or writer generation", "legacy allow versus canonical deny",
                "changed source digest", "cross-tenant replay", "expired and revoked canonical grants",
                "exact source schema and digest replay", "incomplete partition",
                "never authorization")
    assert all(any(term in case for case in CASES) for term in expected)


def test_actual_legacy_capability_grant_shape_maps_without_invented_methods():
    mapped = GrantSetAdapter().adapt(GRANTED)
    assert mapped.operations == ("read",)
    assert mapped.constraints == {}


def test_source_snapshot_uses_the_live_app_grant_and_capability_grant_models():
    grant = CapabilityGrant(
        name=CapabilityName.STORAGE_FILES, decision=CapabilityDecision.GRANTED,
        constraints=GRANTED["constraints"], granted_by=GrantActor.ADMINISTRATOR,
    )
    app_grant = AppGrant(
        id="grant-live", app_definition_id="definition-a", app_version_id="version-a",
        scope=AppScope(kind=AppScopeKind.PROJECT, id="project-a"), grants=(grant,),
        issued_at=NOW, expires_at=NOW + timedelta(days=1),
    )
    source = LegacyGrantSource.from_legacy_app_grant(
        app_grant, app_id="inventory", manifest_version="1.2.0")
    assert source.source_id == "grant-live"
    assert source.app_definition_id == "definition-a"
    assert source.grants[0]["name"] == "storage.files"
    assert GrantSetAdapter().adapt(source.grants[0]).operations == ("read",)


def test_unknown_capability_and_unsupported_method_constraint_are_quarantined():
    adapter = GrantSetAdapter()
    with pytest.raises(MappingError, match="capability_unknown"):
        adapter.adapt({**GRANTED, "name": "storage.future"})
    with pytest.raises(MappingError, match="operation_unapproved"):
        adapter.adapt({**GRANTED, "constraints": {**GRANTED["constraints"], "operations": ["root"]}})
    with pytest.raises(MappingError, match="constraint_incomplete"):
        adapter.adapt({**GRANTED, "constraints": {"resources": ["workspace"], "domains": ["files"]}})


@pytest.mark.asyncio
async def test_unapproved_method_is_quarantined_before_any_decision():
    service, store = _service()
    assert await _observe(service, method="root") is None
    assert store.transactions[-1][1].mapping.outcome is AdapterOutcome.QUARANTINED
    assert store.transactions[-1][1].mapping.reason_code == "method_unapproved"
    assert store.transactions[-1][1].shadow is None


@pytest.mark.asyncio
async def test_method_mapping_rejects_unknown_foreign_and_broadening_lookups():
    adapter = method_mapping()
    unknown = await adapter.resolve(identity=IDENTITY, method="fetch", capability="storage.files",
                                    operation="read", resource="workspace", domain="files")
    assert not unknown.approved and unknown.reason_code == "method_unapproved"
    other_capability = await adapter.resolve(identity=IDENTITY, method="read",
                                             capability="storage.kv", operation="read",
                                             resource="workspace", domain="files")
    assert not other_capability.approved and other_capability.reason_code == "operation_unapproved"
    outside_constraint = await adapter.resolve(identity=IDENTITY, method="read",
                                               capability="storage.files", operation="read",
                                               resource="workspace", domain="network")
    assert not outside_constraint.approved
    wildcard = await adapter.resolve(identity=IDENTITY, method="read",
                                     capability="storage.files", operation="read",
                                     resource="*", domain="files")
    assert not wildcard.approved and wildcard.reason_code.startswith("wildcard")


def test_method_mapping_registry_rejects_a_duplicated_identity():
    duplicate = CapabilityMethodMapping(
        identity=MethodIdentity(server="files", tool="read", version="1.0.0"),
        capability_key="storage.files", operation="read",
        constraints=ResourceDomainConstraint(resources=("workspace",), domains=("files",)),
        supported_protocol_version="mcp-1", tool_version_compatibility=("1.0.0",),
    )
    with pytest.raises(ValueError, match="must not repeat"):
        DeclaredMappingReadPort((duplicate, duplicate))


def test_approved_mapping_digest_is_versioned_and_non_wildcard():
    from prodavan.application.migration.m06_grants_gateway.method_mapping import (
        declared_method_mapping,
    )

    approved = declared_method_mapping(MAPPING_READ_PORT.configured()[0])
    other = declared_method_mapping(MAPPING_READ_PORT.configured()[1])
    assert approved.mapping_digest.startswith("sha256:")
    assert approved.mapping_digest != other.mapping_digest
    assert METHOD_MAPPING_VERSION == "capability-method-mapping-v1"


@pytest.mark.asyncio
async def test_missing_instance_resolution_does_not_create_positive_shadow():
    service, store = _service(identity=None)
    assert await _observe(service) is None
    assert store.transactions == []
    missing_context = replace(CONTEXT, plugin_instance_id=None, runtime_generation=None,
                              writer_generation=None, app_definition_id=None)
    service, store = _service()
    assert await _observe(service, context=missing_context) is None
    assert store.transactions == []
    service = CapabilityGatewayService(
        identity=_Identity(error="instance_ambiguous"), legacy=_Legacy(),
        canonical_shadow=_Canonical(), handoffs=_Handoffs(), evidence_read=_Read(),
        evidence_transaction=store, method_mapping=method_mapping(),
    )
    assert await _observe(service) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_unknown_capability_is_quarantined_and_denied_method_is_shadow_compared():
    service, store = _service()
    assert await _observe(service, capability="storage.future") is None
    assert store.transactions[-1][1].mapping.outcome is AdapterOutcome.QUARANTINED
    assert store.transactions[-1][1].mapping.reason_code == "capability_unknown"
    denied = ShadowDecision(False, "method_unknown", binding_digest(IDENTITY))
    service, store = _service(legacy=_Legacy(denied))
    observation = await _observe(service)
    assert observation is not None and not observation.legacy_decision.allowed
    assert observation.mismatch
    assert observation.mismatch_explained is False
    assert observation.operation_mapping_digest


@pytest.mark.asyncio
async def test_stale_runtime_generation_is_rejected_before_identity_resolution():
    stale_context = replace(CONTEXT, runtime_generation=4)
    service, store = _service()
    assert await _observe(service, context=stale_context) is None
    assert store.transactions == []
    stale_partition = replace(PARTITION, writer_epoch=6)
    service, store = _service(partition=stale_partition)
    assert await _observe(service) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_allow_deny_mismatch_is_recorded_deterministically():
    denied = ShadowDecision(False, "grant_missing", binding_digest(IDENTITY))
    service, store = _service(canonical=_Canonical(denied))
    observation = await _observe(service)
    assert observation is not None and observation.mismatch
    transaction = store.transactions[-1][1]
    assert transaction.shadow is observation
    assert transaction.audit["event"] == "shadow_mismatch"
    assert transaction.mapping.outcome is AdapterOutcome.MAPPED_EVIDENCE
    assert observation.mismatch_explained is True
    assert observation.mismatch_explanation == "canonical_grant_not_yet_backfilled"


@pytest.mark.asyncio
async def test_reason_class_parity_treats_deny_variants_as_equal():
    class _DenyVariant:
        async def decide_shadow(self, *, identity, **kwargs):
            return ShadowDecision(False, "grant_denied", binding_digest(identity))

    service, store = _service(legacy=_Legacy(ShadowDecision(False, "protocol_denied",
                                                           binding_digest(IDENTITY))),
                              canonical=_DenyVariant())
    observation = await _observe(service)
    assert observation is not None and not observation.mismatch


@pytest.mark.asyncio
async def test_changed_digest_and_cross_tenant_replay_preserve_prior_mapping():
    from .acceptance_fixtures import ACCEPTED_SOURCES
    from prodavan.application.migration.m06_grants_gateway.handoff import inventory_digest

    source_digest = source_payload_digest(SOURCE)
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_digest, "sha256:" + "b" * 64, binding_digest(IDENTITY),
        AdapterOutcome.MAPPED_EVIDENCE, None, "old-checkpoint", "old-correlation")
    altered = replace(SOURCE, grants=({**GRANTED, "granted_by": "user"},))
    # A changed source digest is only reachable once the trusted partition inventory has
    # been re-accepted for the new digest; otherwise the handoff resolver refuses the
    # source outright and nothing about the immutable first mapping is ever tested. The
    # re-accepted inventory is the realistic path, and the mapping must still conflict.
    reaccepted = replace(
        ACCEPTED_SOURCES[0], source_digest=source_payload_digest(altered))
    service, store = _service(
        read=_Read(prior),
        partition=replace(PARTITION, accepted_sources=(reaccepted,),
                          inventory_digest=inventory_digest((reaccepted,))))
    assert await _observe(service, source=altered) is None
    assert store.transactions[-1][1].conflict_mapping.reason_code == "source_digest_changed"
    assert store.transactions[-1][1].mapping is None
    other_context = replace(CONTEXT, tenant_id="tenant-b", company_id="company-b",
                            rls_context=RLS.model_copy(update={"tenant_id": "tenant-b"}))
    other_identity = replace(IDENTITY, tenant_id="tenant-b", company_id="company-b")
    other_partition = replace(PARTITION, tenant_id="tenant-b")
    service, store = _service(identity=other_identity, read=_Read(prior), partition=other_partition)
    assert await _observe(service, context=other_context) is None
    assert store.transactions[-1][1].conflict_mapping.reason_code == "cross_tenant_replay"


@pytest.mark.asyncio
async def test_cross_tenant_source_is_rejected_before_any_identity_resolution():
    other_context = replace(CONTEXT, tenant_id="tenant-b", company_id="company-b",
                            rls_context=RLS.model_copy(update={"tenant_id": "tenant-b"}))
    assertions = replace(SOURCE, asserted_company_id="company-b")
    service, store = _service()
    assert await _observe(service, source=assertions, context=other_context) is None
    assert store.transactions == []
    conflicting = replace(SOURCE, asserted_company_id="company-b")
    service, store = _service()
    assert await _observe(service, source=conflicting, context=CONTEXT) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_exact_replay_does_not_duplicate_source_mapping():
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_payload_digest(SOURCE), _canonical_digest(), binding_digest(IDENTITY),
        AdapterOutcome.MAPPED_EVIDENCE, None, "checkpoint-1", "corr-a")
    service, store = _service(read=_Read(prior), store=_Store(MappingOutcome.REPLAY))
    observation = await _observe(service)
    assert observation is not None
    transaction = store.transactions[-1][1]
    assert transaction.mapping is None
    assert transaction.shadow is observation
    assert store.transactions[-1][0] == CONTEXT


def _canonical_digest() -> str:
    from prodavan.application.migration.m06_grants_gateway.method_mapping import (
        declared_method_mapping,
    )

    mapping = declared_method_mapping(MAPPING_READ_PORT.configured()[0])
    base = digest((("storage.files", GrantSetAdapter().adapt(GRANTED).payload_digest),))
    return digest({"mapping": base, "method_mapping": mapping.mapping_digest,
                   "schema_identity": dict(schema_identity())})


@pytest.mark.asyncio
async def test_quarantined_mapping_is_repaired_forward_not_duplicated():
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_payload_digest(SOURCE), None, binding_digest(IDENTITY),
        AdapterOutcome.QUARANTINED, "method_unapproved", "checkpoint-0", "corr-0")
    service, store = _service(read=_Read(prior), store=_Store(MappingOutcome.RECORDED))
    observation = await _observe(service)
    assert observation is not None
    assert store.transactions[-1][1].mapping is not None
    assert store.transactions[-1][1].mapping.outcome is AdapterOutcome.MAPPED_EVIDENCE


@pytest.mark.asyncio
async def test_absent_accepted_handoff_blocks_reads_and_all_evidence_writes():
    # "Absent" is modelled by the trusted handoff source returning no accepted partition
    # for the live request. It cannot be modelled by constructing an
    # AcceptedPartitionEvidence with a malformed digest: that record refuses to exist at
    # all, which is the stronger invariant asserted first below.
    with pytest.raises(ValueError, match="must be sha256"):
        replace(PARTITION, m04_scope_digest="not-a-digest")
    service, store = _service(partition=None)
    assert await _observe(service) is None
    assert store.transactions == []
    service, store = _service(partition=replace(PARTITION, m06_schema_identity_digest=M04_SCOPE_DIGEST))
    assert await _observe(service) is None
    assert store.transactions == []
    missing_rls = replace(CONTEXT, rls_context=None)
    service, store = _service()
    assert await _observe(service, context=missing_rls) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_source_outside_the_accepted_inventory_is_never_mapped():
    from .acceptance_fixtures import ACCEPTED_SOURCES
    from prodavan.application.migration.m06_grants_gateway.handoff import (
        AcceptedPartitionEvidence,
        inventory_digest,
    )

    narrow = (ACCEPTED_SOURCES[0],)
    partition = AcceptedPartitionEvidence(
        tenant_id="tenant-a", app_definition_id="definition-a", app_version_id="version-a",
        plugin_instance_id="instance-a", writer_epoch=7, m04_scope_digest=M04_SCOPE_DIGEST,
        m04_rls_digest=PARTITION.m04_rls_digest,
        m04e_definition_version_digest=PARTITION.m04e_definition_version_digest,
        m05_instance_digest=PARTITION.m05_instance_digest,
        m06_schema_identity_digest=M06_SCHEMA_DIGEST,
        m04_scope_evidence_id="m04-scope-proof", m04_rls_evidence_id="m04-rls-proof",
        m04e_definition_version_evidence_id="m04e-proof",
        m05_instance_runtime_evidence_id="m05-proof", atomic_evidence_id="txn-proof",
        m06_schema_evidence_id="m06-schema-proof", final_delta_checkpoint="final-delta-1",
        inventory_digest=inventory_digest(narrow), accepted_sources=narrow,
    )
    service, store = _service(partition=partition)
    assert await _observe(service, source=replace(SOURCE, source_id="grant-b")) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_canonical_shadow_denies_expired_and_revoked_grants():
    class GrantRead:
        def __init__(self, grant):
            self.grant = grant

        async def list_grants(self, **kwargs):
            return (self.grant,)

    def grant(*, expires_at=None, revoked_at=None):
        return CanonicalGrant(
            grant_id="grant-1", tenant_id="tenant-a", app_definition_id="definition-a",
            app_id="inventory", app_version_id="version-a", app_artifact_digest=IDENTITY.artifact_digest,
            scope_kind="plugin_instance", scope_id="instance-a", plugin_instance_id="instance-a",
            subject_id="subject-a", session_id="session-a", capability_key="storage.files",
            operations=frozenset({"read"}), resources=frozenset({"workspace"}),
            domains=frozenset({"files"}), constraints={}, issued_at=NOW - timedelta(days=2),
            expires_at=expires_at, revoked_at=revoked_at, issued_by="admin")

    expired = await CanonicalGrantShadowAdapter(grants=GrantRead(grant(expires_at=NOW))).decide_shadow(
        identity=IDENTITY, capability="storage.files", operation="read", resource="workspace",
        domain="files", constraints={}, now=NOW)
    revoked = await CanonicalGrantShadowAdapter(grants=GrantRead(grant(revoked_at=NOW))).decide_shadow(
        identity=IDENTITY, capability="storage.files", operation="read", resource="workspace",
        domain="files", constraints={}, now=NOW)
    assert not expired.allowed and expired.reason_code == "expired"
    assert not revoked.allowed and revoked.reason_code == "revoked"


def test_lifecycle_mismatch_classes_are_explicitly_reconcilable():
    from prodavan.application.migration.m06_grants_gateway.adapter import ShadowObservation

    binding = binding_digest(IDENTITY)
    observation = ShadowObservation(
        tenant_id="tenant-a", app_definition_id="definition-a", source_id="grant-a",
        capability_key="storage.files", source_digest=source_payload_digest(SOURCE),
        canonical_digest=None, binding_digest=binding,
        legacy_decision=ShadowDecision(True, "allowed", binding),
        canonical_decision=ShadowDecision(False, "expired", binding),
        mismatch=True, mismatch_explained=True, checkpoint="checkpoint-1",
        correlation_id="corr-a", observed_at=NOW.isoformat(),
        operation_mapping_digest=digest("mapping"),
        mismatch_explanation="legacy_and_canonical_lifecycle_disagree",
    )
    assert observation.mismatch_explained and observation.mismatch_explanation
    with pytest.raises(ValueError, match="explanation"):
        ShadowObservation(
            tenant_id="tenant-a", app_definition_id="definition-a", source_id="grant-a",
            capability_key="storage.files", source_digest=source_payload_digest(SOURCE),
            canonical_digest=None, binding_digest=binding,
            legacy_decision=ShadowDecision(True, "allowed", binding),
            canonical_decision=ShadowDecision(False, "expired", binding),
            mismatch=True, mismatch_explained=True, checkpoint="checkpoint-1",
            correlation_id="corr-a", observed_at=NOW.isoformat(),
            operation_mapping_digest=digest("mapping"),
        )


def test_parity_stays_blocked_without_accepted_migration_handoffs():
    report = bounded_partition_parity(
        tenant_id="tenant-a", app_definition_id="definition-a", observations=(),
        source_snapshot_digest="sha256:" + "a" * 64, final_delta_checkpoint="final-delta-1",
        observation_window_start=(NOW - timedelta(minutes=1)).isoformat(),
        observation_window_end=(NOW + timedelta(minutes=1)).isoformat(), writer_epoch=7,
        unresolved_positive_bindings=0, unknown_mapping_count=0, stale_generation_acceptances=0,
        audit_complete=True, dependency_gates=None,
    )
    assert report.status == ParityStatus.BLOCKED.value
    assert report.canonical_positive_writes is False
    assert "accepted_upstream_and_atomic_evidence_handoffs" in report.blocked_reasons


def test_parity_ready_for_review_requires_reconciled_evidence():
    from prodavan.application.migration.m06_grants_gateway.adapter import ShadowObservation

    binding = binding_digest(IDENTITY)
    observation = ShadowObservation(
        tenant_id="tenant-a", app_definition_id="definition-a", source_id="grant-a",
        capability_key="storage.files", source_digest=source_payload_digest(SOURCE),
        canonical_digest=digest("canonical"), binding_digest=binding,
        legacy_decision=ShadowDecision(True, "allowed", binding),
        canonical_decision=ShadowDecision(True, "allowed", binding),
        mismatch=False, mismatch_explained=False, checkpoint="checkpoint-1",
        correlation_id="corr-a", observed_at=NOW.isoformat(),
        operation_mapping_digest=digest("mapping"),
    )
    report = bounded_partition_parity(
        tenant_id="tenant-a", app_definition_id="definition-a", observations=(observation,),
        source_snapshot_digest="sha256:" + "a" * 64, final_delta_checkpoint="final-delta-1",
        observation_window_start=(NOW - timedelta(minutes=1)).isoformat(),
        observation_window_end=(NOW + timedelta(minutes=1)).isoformat(), writer_epoch=7,
        unresolved_positive_bindings=0, unknown_mapping_count=0, stale_generation_acceptances=0,
        audit_complete=True, dependency_gates=GOOD_GATES,
    )
    assert report.status == ParityStatus.READY_FOR_REVIEW.value
    assert report.blocked_reasons == ()
    exported = report.export()
    assert '"canonical_positive_writes": false' in exported
    assert report.to_json()["digests"]["binding"] == report.binding_digest


@pytest.mark.asyncio
async def test_parity_runner_reports_incomplete_partition_at_the_budget():
    observations = [_observation(index) for index in range(6)]

    class Store:
        async def load_observations(self, *, context, tenant_id, app_definition_id, window_start,
                                    window_end, after_cursor=None, limit=500):
            start = 0 if after_cursor is None else int(after_cursor)
            page = observations[start:start + limit]
            next_cursor = str(start + len(page)) if page else after_cursor
            return ObservationBatch(tuple(page), next_cursor,
                                    start + limit < len(observations))

    class Summary:
        async def load_summary(self, *, context, partition, window_start, window_end):
            return ParityEvidenceSummary(
                partition=partition, source_snapshot_digest="sha256:" + "a" * 64,
                final_delta_checkpoint="final-delta-1", writer_epoch=7, unknown_mapping_count=0,
                unresolved_positive_bindings=0, stale_generation_acceptances=0, audit_complete=True)

    from .acceptance_fixtures import PARITY_REQUEST

    request = replace(PARITY_REQUEST, max_observations=4, page_size=2)
    runner = BoundedParityRunner(observations=Store(), summaries=Summary())
    report = await runner.run(request=request, context=CONTEXT, dependency_gates=GOOD_GATES)
    assert report.status == ParityStatus.INCOMPLETE.value
    assert report.observation_count == 4
    assert report.checkpoint.complete_window is False
    assert "partition_coverage_incomplete" in report.blocked_reasons
    assert report.canonical_positive_writes is False


@pytest.mark.asyncio
async def test_parity_runner_is_idempotent_for_an_identical_run_and_conflicts_on_change():
    from prodavan.application.migration.m06_grants_gateway.evidence_store import (
        InMemoryParityReportStore,
    )
    from .acceptance_fixtures import PARITY_REQUEST

    observations = [_observation(index) for index in range(3)]

    class Store:
        async def load_observations(self, *, context, tenant_id, app_definition_id, window_start,
                                    window_end, after_cursor=None, limit=500):
            start = 0 if after_cursor is None else int(after_cursor)
            page = observations[start:start + limit]
            return ObservationBatch(tuple(page), str(start + len(page)),
                                    start + len(page) < len(observations))

    class Summary:
        def __init__(self, digest_value="sha256:" + "a" * 64):
            self.digest_value = digest_value

        async def load_summary(self, *, context, partition, window_start, window_end):
            return ParityEvidenceSummary(
                partition=partition, source_snapshot_digest=self.digest_value,
                final_delta_checkpoint="final-delta-1", writer_epoch=7, unknown_mapping_count=0,
                unresolved_positive_bindings=0, stale_generation_acceptances=0, audit_complete=True)

    reports = InMemoryParityReportStore()
    runner = BoundedParityRunner(observations=Store(), summaries=Summary(), reports=reports)
    first, first_outcome = await runner.run_and_record(
        request=PARITY_REQUEST, context=CONTEXT, dependency_gates=GOOD_GATES)
    second, second_outcome = await runner.run_and_record(
        request=PARITY_REQUEST, context=CONTEXT, dependency_gates=GOOD_GATES)
    assert first_outcome is ReportOutcome.RECORDED
    assert second_outcome is ReportOutcome.REPLAY
    assert first.report_digest == second.report_digest
    assert first.export() == second.export()
    changed = BoundedParityRunner(observations=Store(),
                                  summaries=Summary("sha256:" + "c" * 64), reports=reports)
    _, third_outcome = await changed.run_and_record(
        request=PARITY_REQUEST, context=CONTEXT, dependency_gates=GOOD_GATES)
    assert third_outcome is ReportOutcome.CONFLICT


@pytest.mark.asyncio
async def test_parity_runner_rejects_a_foreign_partition_context():
    from .acceptance_fixtures import PARITY_REQUEST

    class Store:
        async def load_observations(self, **kwargs):
            raise AssertionError("foreign partition must be rejected before any read")

    class Summary:
        async def load_summary(self, **kwargs):
            raise AssertionError("foreign partition must be rejected before any read")

    runner = BoundedParityRunner(observations=Store(), summaries=Summary())
    with pytest.raises(ValueError, match="tenant does not match"):
        await runner.run(request=PARITY_REQUEST,
                         context=replace(CONTEXT, tenant_id="tenant-b"), dependency_gates=GOOD_GATES)


def _observation(index: int):
    from prodavan.application.migration.m06_grants_gateway.adapter import ShadowObservation

    binding = binding_digest(IDENTITY)
    return ShadowObservation(
        tenant_id="tenant-a", app_definition_id="definition-a", source_id=f"grant-{index:03d}",
        capability_key="storage.files", source_digest=digest(["source", index]),
        canonical_digest=digest(["canonical", index]), binding_digest=binding,
        legacy_decision=ShadowDecision(True, "allowed", binding),
        canonical_decision=ShadowDecision(True, "allowed", binding),
        mismatch=False, mismatch_explained=False, checkpoint="checkpoint-1",
        correlation_id=f"corr-{index:03d}", observed_at=(NOW + timedelta(minutes=index)).isoformat(),
        operation_mapping_digest=digest("mapping"),
    )


def test_caller_assertions_never_become_authorization():
    from prodavan.application.migration.m06_grants_gateway.composition import caller_assertion_report

    service, _ = _service()
    report = caller_assertion_report(gateway=service)
    assert report["decision_owner"] == "legacy"
    assert report["canonical_read_owner"] == "legacy"
    assert report["canonical_positive_writes"] == "false"
    # A manifest-requested capability is not authorization: the identity snapshot may
    # carry it, yet the capability grant set still decides, and the request identity
    # digest (which excludes requested capabilities) does not change.
    requested_only = replace(IDENTITY, requested_capabilities=frozenset({"storage.files",
                                                                         "secrets.read"}))
    assert requested_only.requested_capabilities != IDENTITY.requested_capabilities
    assert request_identity_digest(requested_only) == request_identity_digest(IDENTITY)
    assert schema_identity_digest() != M04_SCOPE_DIGEST
