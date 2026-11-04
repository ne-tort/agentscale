"""Approved mapping, source replay and caller-assertion acceptance for the M06 adapter."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

import pytest

from prodavan.application.capability_method_mapping.contracts import (
    CapabilityMethodMapping,
    MethodIdentity,
    ResourceDomainConstraint,
)
from prodavan.application.migration.m06_grants_gateway.adapter import (
    AdapterOutcome,
    CapabilityGatewayService,
    GrantSetAdapter,
    IdentityResolutionError,
    MappingError,
    MappingOutcome,
    MethodOperationResolution,
    ShadowDecision,
    SourceMappingRecord,
    binding_digest,
    digest,
    request_identity_digest,
    schema_identity_digest,
    source_payload_digest,
)
from prodavan.application.migration.m06_grants_gateway.handoff import (
    AcceptedPartitionEvidence,
    AcceptedSourceKey,
    ServerResolvedHandoffResolver,
    inventory_digest,
)
from prodavan.application.migration.m06_grants_gateway.method_mapping import (
    ApprovedMethodOperationAdapter,
    DeclaredMappingReadPort,
    declared_method_mapping,
    module_capability_operations,
)

from .acceptance_fixtures import (
    APPROVED_MAPPINGS,
    CONTEXT,
    GOOD_GATES,
    GRANTED,
    IDENTITY,
    MAPPING_READ_PORT,
    M06_SCHEMA_DIGEST,
    NOW,
    PARTITION,
    RLS,
    SOURCE,
    method_mapping,
)


class _FixedGates:
    """Trusted handoff double bound to the live source digest of the call."""

    def __init__(self, gates=GOOD_GATES):
        self.gates = gates

    async def resolve_gates(self, *, context, source, source_digest, now):
        if self.gates.tenant_id != context.tenant_id:
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
    def __init__(self, identity=IDENTITY, error=None):
        self.identity = identity
        self.error = error

    async def resolve(self, **kwargs):
        if self.error is not None:
            raise IdentityResolutionError(self.error)
        return self.identity


class _Legacy:
    def __init__(self, decision=None):
        self.decision = decision
        self.calls = 0

    async def decide(self, *, identity, **kwargs):
        self.calls += 1
        return self.decision or ShadowDecision(True, "allowed", binding_digest(identity))


class _Canonical:
    def __init__(self, decision=None):
        self.decision = decision

    async def decide_shadow(self, *, identity, **kwargs):
        return self.decision or ShadowDecision(True, "allowed", binding_digest(identity))


def _service(*, identity=IDENTITY, read=None, store=None, gates=None, mapping=None,
             legacy=None, canonical=None):
    store = store or _Store()
    service = CapabilityGatewayService(
        identity=_Identity(identity), legacy=legacy or _Legacy(),
        canonical_shadow=canonical or _Canonical(), handoffs=_FixedGates(gates or GOOD_GATES),
        evidence_read=read or _Read(), evidence_transaction=store,
        method_mapping=mapping or method_mapping(),
    )
    return service, store


async def _observe(service, *, source=SOURCE, context=CONTEXT, capability="storage.files",
                   operation="read", resource="workspace", domain="files", method="read", now=NOW):
    return await service.observe(
        source=source, context=context, capability=capability, operation=operation,
        resource=resource, domain=domain, method=method, checkpoint="checkpoint-1", now=now)


@pytest.mark.asyncio
async def test_unknown_capability_quarantines_without_asking_either_owner():
    legacy = _Legacy()
    service, store = _service(legacy=legacy)
    assert await _observe(service, capability="storage.future") is None
    transaction = store.transactions[-1][1]
    assert transaction.mapping.reason_code == "capability_unknown"
    assert transaction.shadow is None
    assert legacy.calls == 0


@pytest.mark.asyncio
async def test_unsupported_method_quarantines_and_the_capability_is_not_broadened():
    service, store = _service()
    assert await _observe(service, method="shell") is None
    mapping = store.transactions[-1][1].mapping
    assert mapping.outcome is AdapterOutcome.QUARANTINED
    assert mapping.reason_code == "method_unapproved"
    assert mapping.canonical_digest is not None
    assert store.transactions[-1][1].shadow is None


@pytest.mark.asyncio
async def test_manifest_requested_capability_is_never_authorization():
    # The identity requests storage.files only; asking for secrets.read is an
    # unmapped capability even though it is in the global approved set.
    service, store = _service()
    assert await _observe(service, capability="secrets.read", operation="read",
                          resource="workspace", domain="files", method="read") is None
    assert store.transactions[-1][1].mapping.reason_code == "capability_unknown"
    assert store.transactions[-1][1].shadow is None


@pytest.mark.asyncio
async def test_missing_gates_write_no_evidence_at_all():
    absent = replace(GOOD_GATES, atomic_evidence_accepted=False, atomic_evidence_id=None)
    service, store = _service(gates=absent)
    assert await _observe(service) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_gates_for_another_partition_instance_are_rejected():
    other = replace(GOOD_GATES, scope_id="instance-b", plugin_instance_id="instance-b")
    service, store = _service(gates=other)
    assert await _observe(service) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_stale_writer_generation_in_gates_blocks_evidence():
    stale = replace(GOOD_GATES, writer_generation=6, trusted_writer_epoch=6)
    service, store = _service(gates=stale)
    assert await _observe(service) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_request_identity_mismatch_between_gates_and_resolution_blocks():
    mismatched = replace(IDENTITY, runtime_generation=9)
    service, store = _service(identity=mismatched)
    assert await _observe(service) is None
    assert store.transactions == []
    assert request_identity_digest(mismatched) != GOOD_GATES.request_identity_digest


@pytest.mark.asyncio
async def test_changed_source_digest_is_a_conflict_that_preserves_the_first_mapping():
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_payload_digest(SOURCE), digest("canonical"), binding_digest(IDENTITY),
        AdapterOutcome.MAPPED_EVIDENCE, None, "checkpoint-0", "corr-0")
    service, store = _service(read=_Read(prior))
    altered = replace(SOURCE, grants=({**GRANTED, "granted_by": "user"},))
    assert await _observe(service, source=altered) is None
    transaction = store.transactions[-1][1]
    assert transaction.conflict_mapping.reason_code == "source_digest_changed"
    assert transaction.mapping is None and transaction.shadow is None
    assert transaction.conflict_quarantine and transaction.conflict_audit


@pytest.mark.asyncio
async def test_cross_tenant_replay_is_detected_from_the_global_source_identity():
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_payload_digest(SOURCE), digest("canonical"), binding_digest(IDENTITY),
        AdapterOutcome.MAPPED_EVIDENCE, None, "checkpoint-0", "corr-0")
    other_context = replace(CONTEXT, tenant_id="tenant-b", company_id="company-b",
                            rls_context=RLS.model_copy(update={"tenant_id": "tenant-b"}))
    other_identity = replace(IDENTITY, tenant_id="tenant-b", company_id="company-b")
    other_partition = replace(PARTITION, tenant_id="tenant-b")
    # The trusted handoff double must attest the *other* tenant completely: the gates
    # carry company_id, so leaving it at the original company would make
    # ``gates_request_identity_digest`` disagree with the attested digest and the call
    # would be refused as an identity mismatch before the cross-tenant replay is even
    # visible. A cross-tenant replay is one the server resolves consistently.
    gates = replace(GOOD_GATES, tenant_id="tenant-b", company_id="company-b",
                    request_identity_digest=request_identity_digest(other_identity))
    service, store = _service(identity=other_identity, read=_Read(prior), gates=gates)
    assert await _observe(service, context=other_context) is None
    assert store.transactions[-1][1].conflict_mapping.reason_code == "cross_tenant_replay"
    assert other_partition.tenant_id != prior.tenant_id


@pytest.mark.asyncio
async def test_expired_and_revoked_sources_are_known_deny_not_positive_evidence():
    expired_source = replace(SOURCE, issued_at=NOW - timedelta(hours=2),
                             expires_at=NOW - timedelta(hours=1))
    service, store = _service()
    observation = await _observe(service, source=expired_source, now=NOW)
    assert observation is not None
    mapping = store.transactions[-1][1].mapping
    assert mapping.outcome is AdapterOutcome.KNOWN_DENY
    assert mapping.reason_code == "source_grant_expired"
    denied_entry = {"name": "storage.files", "decision": "denied", "granted_by": "administrator",
                    "constraints": {"operations": ["read"], "resources": ["workspace"],
                                    "domains": ["files"]}}
    denied_source = replace(SOURCE, grants=(denied_entry,))
    service, store = _service()
    observation = await _observe(service, source=denied_source)
    assert observation is not None
    assert store.transactions[-1][1].mapping.outcome is AdapterOutcome.KNOWN_DENY
    assert store.transactions[-1][1].mapping.reason_code == "protocol_denied"


@pytest.mark.asyncio
async def test_future_dated_source_is_quarantined_not_mapped():
    future = replace(SOURCE, issued_at=NOW + timedelta(minutes=5))
    service, store = _service()
    assert await _observe(service, source=future) is None
    assert store.transactions[-1][1].mapping.reason_code == "source_grant_future"


@pytest.mark.asyncio
async def test_exact_replay_appends_shadow_only_and_never_rewrites_mapping():
    from .acceptance_fixtures import APPROVED_MAPPINGS as registry
    from prodavan.application.migration.m06_grants_gateway.method_mapping import (
        declared_method_mapping,
    )

    base = digest((("storage.files", GrantSetAdapter().adapt(GRANTED).payload_digest),))
    canonical = digest({"mapping": base,
                        "method_mapping": declared_method_mapping(registry[0]).mapping_digest,
                        "schema_identity": dict(schema_identity_of())})
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_payload_digest(SOURCE), canonical, binding_digest(IDENTITY),
        AdapterOutcome.MAPPED_EVIDENCE, None, "checkpoint-0", "corr-0")
    store = _Store(MappingOutcome.REPLAY)
    service, store = _service(read=_Read(prior), store=store)
    observation = await _observe(service)
    assert observation is not None
    transaction = store.transactions[-1][1]
    assert transaction.mapping is None
    assert transaction.shadow is observation
    assert transaction.audit["event"] == "shadow_replayed"


def schema_identity_of():
    from prodavan.application.migration.m06_grants_gateway.adapter import schema_identity

    return schema_identity()


@pytest.mark.asyncio
async def test_quarantined_prior_mapping_is_repaired_forward():
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_payload_digest(SOURCE), None, binding_digest(IDENTITY),
        AdapterOutcome.QUARANTINED, "method_unapproved", "checkpoint-0", "corr-0")
    service, store = _service(read=_Read(prior), store=_Store(MappingOutcome.RECORDED))
    observation = await _observe(service)
    assert observation is not None
    assert store.transactions[-1][1].mapping.outcome is AdapterOutcome.MAPPED_EVIDENCE


@pytest.mark.asyncio
async def test_conflicted_prior_mapping_is_not_silently_replayed():
    prior = SourceMappingRecord(
        SOURCE.source_kind, SOURCE.source_id, "m06-grant-mapping-v4", "tenant-a",
        source_payload_digest(SOURCE), None, binding_digest(IDENTITY),
        AdapterOutcome.CONFLICT, "source_digest_changed", "checkpoint-0", "corr-0")
    service, store = _service(read=_Read(prior))
    assert await _observe(service) is None
    transaction = store.transactions[-1][1]
    assert transaction.mapping is None and transaction.shadow is None
    assert transaction.audit["event"] == "non_promotable_source_replay"


@pytest.mark.asyncio
async def test_canonical_digest_change_after_mapping_is_a_conflict():
    # Two registry generations of the *same* method identity: the approved mapping was
    # revised (a second resource is now declared), so the canonical digest the adapter
    # derives today differs from the one recorded with the first mapping. A revised
    # declaration is a new row, never an in-place mutation of a frozen dataclass.
    revised = CapabilityMethodMapping(
        identity=MethodIdentity(server="files", tool="read", version="1.0.0"),
        capability_key="storage.files", operation="read",
        constraints=ResourceDomainConstraint(resources=("workspace", "archive"),
                                             domains=("files",)),
        supported_protocol_version="mcp-1", tool_version_compatibility=("1.0.0",),
    )
    service, store = _service()
    first = await _observe(service)
    assert first is not None
    recorded = store.transactions[-1][1].mapping
    assert recorded.canonical_digest is not None
    changed = declared_method_mapping(revised).mapping_digest
    assert changed != declared_method_mapping(MAPPING_READ_PORT.configured()[0]).mapping_digest
    service, store = _service(
        read=_Read(recorded), mapping=ApprovedMethodOperationAdapter(
            mappings=DeclaredMappingReadPort((revised, APPROVED_MAPPINGS[1])),
            method_servers={"read": "files", "delete": "files"},
            method_versions={"read": "1.0.0", "delete": "1.0.0"}, now=NOW))
    assert await _observe(service) is None
    assert store.transactions[-1][1].conflict_mapping.reason_code == "mapping_digest_changed"
    assert recorded.canonical_digest is not None


@pytest.mark.asyncio
async def test_evidence_store_failure_never_becomes_an_allow_or_a_silent_success():
    class _Broken:
        async def commit_evidence(self, *, context, transaction):
            raise RuntimeError("durable store unavailable")

    store = _Broken()
    service = CapabilityGatewayService(
        identity=_Identity(), legacy=_Legacy(), canonical_shadow=_Canonical(),
        handoffs=_FixedGates(), evidence_read=_Read(), evidence_transaction=store,
        method_mapping=method_mapping())
    assert await _observe(service) is None


@pytest.mark.asyncio
async def test_mapping_read_failure_writes_nothing_and_returns_no_observation():
    class _Broken:
        async def find_mapping(self, *, context, source_kind, source_id, schema_version):
            raise RuntimeError("source key lookup failed")

    service, store = _service(read=_Broken())
    assert await _observe(service) is None
    assert store.transactions == []


@pytest.mark.asyncio
async def test_handoff_resolver_requires_the_exact_accepted_inventory():
    class _Partitions:
        def __init__(self, evidence):
            self.evidence = evidence

        async def load_partition(self, *, tenant_id, app_definition_id, app_version_id,
                                 plugin_instance_id, writer_generation):
            return self.evidence

    resolver = ServerResolvedHandoffResolver(partitions=_Partitions(PARTITION))
    gates = await resolver.resolve_gates(context=CONTEXT, source=SOURCE,
                                         source_digest=source_payload_digest(SOURCE), now=NOW)
    assert gates is not None and gates.positive_projection_ready
    outsider = await resolver.resolve_gates(
        context=CONTEXT, source=replace(SOURCE, source_id="grant-z"),
        source_digest=source_payload_digest(replace(SOURCE, source_id="grant-z")), now=NOW)
    assert outsider is None


@pytest.mark.asyncio
async def test_handoff_resolver_rejects_schema_identity_drift_and_stale_epoch():
    class _Partitions:
        def __init__(self, evidence):
            self.evidence = evidence

        async def load_partition(self, **kwargs):
            return self.evidence

    drifted = replace(PARTITION, m06_schema_identity_digest="sha256:" + "9" * 64)
    resolver = ServerResolvedHandoffResolver(partitions=_Partitions(drifted))
    assert await resolver.resolve_gates(context=CONTEXT, source=SOURCE,
                                        source_digest=source_payload_digest(SOURCE),
                                        now=NOW) is None
    stale = replace(PARTITION, writer_epoch=6)
    resolver = ServerResolvedHandoffResolver(partitions=_Partitions(stale))
    assert await resolver.resolve_gates(context=CONTEXT, source=SOURCE,
                                        source_digest=source_payload_digest(SOURCE),
                                        now=NOW) is None


def test_accepted_partition_inventory_must_be_canonical_and_digest_bound():
    keys = (AcceptedSourceKey("legacy_app_grant", "b", "grantset-v1", "sha256:" + "b" * 64),
            AcceptedSourceKey("legacy_app_grant", "a", "grantset-v1", "sha256:" + "a" * 64))
    with pytest.raises(ValueError, match="canonically ordered"):
        AcceptedPartitionEvidence(
            tenant_id="tenant-a", app_definition_id="definition-a", app_version_id="version-a",
            plugin_instance_id="instance-a", writer_epoch=7, m04_scope_digest="sha256:" + "1" * 64,
            m04_rls_digest="sha256:" + "2" * 64, m04e_definition_version_digest="sha256:" + "3" * 64,
            m05_instance_digest="sha256:" + "4" * 64,
            m06_schema_identity_digest=M06_SCHEMA_DIGEST, m04_scope_evidence_id="a",
            m04_rls_evidence_id="b", m04e_definition_version_evidence_id="c",
            m05_instance_runtime_evidence_id="d", atomic_evidence_id="e",
            m06_schema_evidence_id="f", final_delta_checkpoint="final-delta-1",
            inventory_digest=inventory_digest(keys), accepted_sources=keys)


def test_grant_set_grammar_rejects_broadening_and_ambiguity():
    adapter = GrantSetAdapter()
    with pytest.raises(MappingError, match="wildcard_grant_unrepresentable"):
        adapter.adapt({**GRANTED, "constraints": {**GRANTED["constraints"],
                                                  "resources": ["*"]}})
    with pytest.raises(MappingError, match="grant_issuer_missing_or_ambiguous"):
        adapter.adapt({"name": "storage.files", "decision": "granted",
                       "constraints": GRANTED["constraints"]})
    with pytest.raises(MappingError, match="constraint_duplicate"):
        adapter.adapt({**GRANTED, "constraints": {**GRANTED["constraints"],
                                                  "operations": ["read", "read"]}})
    with pytest.raises(MappingError, match="constraint_unsupported"):
        adapter.adapt({**GRANTED, "constraints": {**GRANTED["constraints"],
                                                  "eventTypes": ["project.sync"]}})


def test_module_action_kinds_only_map_to_approved_operations():
    assert module_capability_operations("data.select_row") == frozenset({"read"})
    assert module_capability_operations("data.delete_row") == frozenset({"delete"})
    assert module_capability_operations("equipment.pipeline") is None
    # ``create`` is not an operation of the closed approved vocabulary: the canonical
    # grammar expresses row creation as ``storage.*:write``, and ``ui.render`` owns
    # ``render``. The gate must therefore refuse to hand back an operation no canonical
    # decision can enforce instead of translating the action onto a neighbouring one —
    # so an unmapped action kind quarantines rather than broadening the request.
    assert module_capability_operations("data.create_row") is None
    assert module_capability_operations("documents.convert") is None
    assert module_capability_operations("documents.fill_template") == frozenset({"render"})
