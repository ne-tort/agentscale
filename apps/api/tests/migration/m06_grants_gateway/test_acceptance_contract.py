"""MIG-M06-02 acceptance behavior; intentionally unexecuted while migration freeze holds."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from prodavan.application.canonical_grants.contracts import CanonicalGrant
from prodavan.domain.dynamic_apps import (
    AppGrant,
    AppScope,
    AppScopeKind,
    CapabilityDecision,
    CapabilityGrant,
    CapabilityName,
    GrantActor,
)
from prodavan.application.migration.m06_grants_gateway.adapter import (
    AdapterOutcome,
    CapabilityGatewayService,
    GrantSetAdapter,
    IdentityResolutionError,
    LegacyGrantSource,
    MappingError,
    MappingOutcome,
    ResolvedGrantIdentity,
    SourceMappingRecord,
    ShadowDecision,
    binding_digest,
    digest,
    source_payload_digest,
)
from prodavan.application.migration.m06_grants_gateway.canonical_decision_adapter import CanonicalGrantShadowAdapter
from prodavan.application.migration.m06_grants_gateway.parity import bounded_partition_parity

from .acceptance_fixtures import CASES, CONTEXT, GOOD_GATES, GRANTED, IDENTITY, NOW, RLS, SOURCE


class _Handoffs:
    def __init__(self, gates=GOOD_GATES):
        self.gates = gates

    async def resolve_gates(self, *, context, source, source_digest, now):
        if source_digest != source_payload_digest(source):
            return None
        return replace(self.gates, source_digest=source_digest)


class _Read:
    def __init__(self, mapping=None):
        self.mapping = mapping

    async def find_mapping(self, *, context, source_kind, source_id, schema_version):
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


def _service(*, identity: ResolvedGrantIdentity | None = IDENTITY, legacy=None, canonical=None, read=None,
             store=None, gates=GOOD_GATES):
    store = store or _Store()
    service = CapabilityGatewayService(
        identity=_Identity(identity), legacy=legacy or _Legacy(), canonical_shadow=canonical or _Canonical(),
        handoffs=_Handoffs(gates), evidence_read=read or _Read(), evidence_transaction=store,
    )
    return service, store


def _observe(service, source=SOURCE, context=CONTEXT, *, capability="storage.files", method="read", now=NOW):
    return service.observe(source=source, context=context, capability=capability,
                           operation="read", resource="workspace", domain="files",
                           method=method, checkpoint="checkpoint-1", now=now)


def test_behavior_matrix_names_requested_m06_acceptance_cases():
    expected = ("unknown capability", "unknown method", "missing or ambiguous instance",
                "stale runtime or writer generation", "legacy allow versus canonical deny",
                "changed source digest", "cross-tenant replay", "expired and revoked canonical grants",
                "exact source schema and digest replay")
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
async def test_missing_instance_resolution_does_not_create_positive_shadow():
    service, store = _service(identity=None)
    result = await _observe(service)
    assert result is None
    assert store.transactions == []
    missing_context = replace(CONTEXT, plugin_instance_id=None, runtime_generation=None,
                             writer_generation=None)
    service, store = _service()
    assert await _observe(service, context=missing_context) is None
    assert store.transactions == []
    service = CapabilityGatewayService(
        identity=_Identity(error="instance_ambiguous"), legacy=_Legacy(), canonical_shadow=_Canonical(),
        handoffs=_Handoffs(), evidence_read=_Read(), evidence_transaction=store,
    )
    assert await _observe(service) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_unknown_capability_is_quarantined_and_unknown_method_is_shadow_denied():
    service, store = _service()
    assert await _observe(service, capability="storage.future") is None
    assert store.transactions[-1][1].mapping.outcome is AdapterOutcome.QUARANTINED
    service, store = _service()
    observation = await _observe(service, method="root")
    assert observation is not None and not observation.legacy_decision.allowed
    assert observation.legacy_decision.reason_code == "method_unknown"
    assert observation.mismatch


@pytest.mark.asyncio
async def test_stale_runtime_generation_is_rejected_before_identity_resolution():
    stale_context = replace(CONTEXT, runtime_generation=4)
    service, store = _service()
    result = await _observe(service, context=stale_context)
    assert result is None
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


@pytest.mark.asyncio
async def test_changed_digest_and_cross_tenant_replay_preserve_prior_mapping():
    source_digest = source_payload_digest(SOURCE)
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v3", "tenant-a",
        source_digest, "sha256:" + "b" * 64, binding_digest(IDENTITY),
        AdapterOutcome.MAPPED_EVIDENCE, None, "old-checkpoint", "old-correlation")
    altered = replace(SOURCE, grants=({**GRANTED, "granted_by": "user"},))
    service, store = _service(read=_Read(prior))
    assert await _observe(service, source=altered) is None
    assert store.transactions[-1][1].conflict_mapping.reason_code == "source_digest_changed"
    assert store.transactions[-1][1].mapping is None
    other_context = replace(CONTEXT, tenant_id="tenant-b", company_id="company-b",
                            rls_context=RLS.model_copy(update={"tenant_id": "tenant-b"}))
    other_identity = replace(IDENTITY, tenant_id="tenant-b", company_id="company-b")
    other_tenant_gates = replace(
        GOOD_GATES, tenant_id="tenant-b", m05_identity_binding_digest=binding_digest(other_identity))
    service, store = _service(identity=other_identity, read=_Read(prior), gates=other_tenant_gates)
    assert await _observe(service, context=other_context) is None
    assert store.transactions[-1][1].conflict_mapping.reason_code == "cross_tenant_replay"


@pytest.mark.asyncio
async def test_exact_replay_does_not_duplicate_source_mapping():
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v3", "tenant-a",
        source_payload_digest(SOURCE), digest((('storage.files', GrantSetAdapter().adapt(GRANTED).payload_digest),)),
        binding_digest(IDENTITY), AdapterOutcome.MAPPED_EVIDENCE, None, "checkpoint-1", "corr-a")
    service, store = _service(read=_Read(prior), store=_Store(MappingOutcome.REPLAY))
    observation = await _observe(service)
    assert observation is not None
    transaction = store.transactions[-1][1]
    assert transaction.mapping is None
    assert transaction.shadow is observation
    assert store.transactions[-1][0] == CONTEXT


@pytest.mark.asyncio
async def test_absent_accepted_handoff_blocks_reads_and_all_evidence_writes():
    service, store = _service(gates=replace(GOOD_GATES, m05_instance_runtime_accepted=False))
    assert await _observe(service) is None
    assert store.transactions == []
    missing_rls = replace(CONTEXT, rls_context=None)
    service, store = _service()
    assert await _observe(service, context=missing_rls) is None
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


def test_parity_stays_blocked_without_accepted_migration_handoffs():
    report = bounded_partition_parity(
        tenant_id="tenant-a", app_definition_id="definition-a", observations=(),
        source_snapshot_digest="sha256:" + "a" * 64, final_delta_checkpoint="none",
        observation_window_start=(NOW - timedelta(minutes=1)).isoformat(),
        observation_window_end=(NOW + timedelta(minutes=1)).isoformat(), writer_epoch=None,
        unresolved_positive_bindings=0, unknown_mapping_count=0, stale_generation_acceptances=0,
        audit_complete=False, dependency_gates=None,
    )
    assert report.status == "blocked"
    assert report.canonical_positive_writes is False
