"""Acceptance fixtures for MIG-RES-01; intentionally not executed during migration freeze."""
from __future__ import annotations

import pytest

from prodavan.application.migration.resources.resources import (
    Binding, BindingState, CompatibilityCandidate, ImportOutcome, Resource,
    ResourceKind, ResourceLifecycle, ResourceScope, ResourceVersion, ScopeKind,
    ResourceSnapshot, ScopeProof, SecretReference, canonical_digest, candidate_canonical_digest,
    import_compatibility_candidate, resolve_resource,
)

TENANT = "tenant-a"
SCOPE = ResourceScope(ScopeKind.TENANT, TENANT, TENANT)
PAYLOAD = {"endpoint": "https://service.invalid", "auth": {"secret_ref": "vault://team/credential"}}
VERSION = ResourceVersion("v1", 1, "1", canonical_digest(PAYLOAD), PAYLOAD,
                          (SecretReference("vault", "team/credential", "version-1"),))
RESOURCE = Resource("resource-a", ResourceKind.CONNECTION, SCOPE, ResourceLifecycle.ACTIVE, (VERSION,))
BINDING = Binding("binding-a", "resource-a", "v1", SCOPE, BindingState.ACTIVE)
CANDIDATE = CompatibilityCandidate("legacy_connection", "legacy-a", "1", "",
                                   RESOURCE, BINDING, {"legacy": "payload"})
CANDIDATE = CompatibilityCandidate(CANDIDATE.source_kind, CANDIDATE.source_id,
                                   CANDIDATE.schema_version, candidate_canonical_digest(CANDIDATE),
                                   CANDIDATE.resource, CANDIDATE.binding, CANDIDATE.source_envelope)


class Store:
    def __init__(self, *, resource=RESOURCE, binding=BINDING, prior=None, snapshot=None):
        self.resource, self.binding, self.prior = resource, binding, prior
        self.snapshot = snapshot
        self.imports, self.commits = [], []

    async def resolve_snapshot(self, **identity):
        return self.snapshot

    async def import_if_absent_or_record_conflict(self, *, identity, digest, candidate):
        self.commits.append((identity, digest, candidate))
        return self.prior


class AuthorizedScope:
    async def is_authorized(self, **context):
        return True


@pytest.mark.asyncio
async def test_missing_m04_scope_resolver_fails_closed_without_store_reads():
    store = Store()
    result = await resolve_resource(store=store, scope_resolver=None, m04_scope_ready=False,
        tenant_id=TENANT, subject_id="subject-a", session_id=None, project_id=None,
        binding_id="binding-a", resource_id="resource-a", version_id="v1")
    assert not result.resolved and result.reason == "m04_scope_resolver_unavailable"


@pytest.mark.asyncio
async def test_explicit_tenant_binding_resolves_only_from_exact_immutable_snapshot():
    proof = ScopeProof(TENANT, "subject-a", ScopeKind.TENANT, TENANT,
                       None, None, "proof-a", 1)
    snapshot = ResourceSnapshot(BINDING, RESOURCE, VERSION, proof, generation=1)
    result = await resolve_resource(store=Store(snapshot=snapshot), scope_resolver=AuthorizedScope(),
        m04_scope_ready=True, tenant_id=TENANT, subject_id="subject-a", session_id=None,
        project_id=None, binding_id="binding-a", resource_id="resource-a", version_id="v1")
    assert result.resolved and result.snapshot is snapshot


@pytest.mark.asyncio
async def test_missing_snapshot_denies_even_when_separate_scope_object_allows():
    result = await resolve_resource(store=Store(), scope_resolver=AuthorizedScope(),
        m04_scope_ready=True, tenant_id=TENANT, subject_id="subject-a", session_id=None,
        project_id=None, binding_id="binding-a", resource_id="resource-a", version_id="v1")
    assert not result.resolved and result.reason == "immutable_snapshot_unavailable_or_denied"


@pytest.mark.asyncio
async def test_snapshot_identity_mismatch_denies_before_application():
    proof = ScopeProof(TENANT, "subject-a", ScopeKind.TENANT, TENANT,
                       None, None, "proof-a", 1)
    snapshot = ResourceSnapshot(BINDING, RESOURCE, VERSION, proof, generation=1)
    result = await resolve_resource(store=Store(snapshot=snapshot), scope_resolver=AuthorizedScope(),
        m04_scope_ready=True, tenant_id=TENANT, subject_id="subject-a", session_id=None,
        project_id=None, binding_id="binding-a", resource_id="resource-a", version_id="v2")
    assert not result.resolved and result.reason == "immutable_snapshot_identity_or_lifecycle_mismatch"


@pytest.mark.asyncio
async def test_compatibility_import_blocked_until_dependencies_ready():
    store = Store()
    record = await import_compatibility_candidate(store=store, candidate=CANDIDATE)
    assert record.outcome is ImportOutcome.BLOCKED and store.commits == []


@pytest.mark.asyncio
async def test_missing_trusted_atomic_import_port_fails_closed():
    class UntrustedStore:
        pass
    record = await import_compatibility_candidate(
        store=UntrustedStore(), candidate=CANDIDATE, dependencies_ready=True)
    assert record.outcome is ImportOutcome.BLOCKED
    assert record.reason == "trusted_atomic_import_port_unavailable"


def test_snapshot_rejects_generation_mismatch():
    proof = ScopeProof(TENANT, "subject-a", ScopeKind.TENANT, TENANT,
                       None, None, "proof-a", 1)
    with pytest.raises(ValueError):
        ResourceSnapshot(BINDING, RESOURCE, VERSION, proof, generation=2)


@pytest.mark.asyncio
async def test_canonical_digest_mismatch_is_quarantined_before_port_call():
    changed = CompatibilityCandidate(CANDIDATE.source_kind, CANDIDATE.source_id,
        CANDIDATE.schema_version, "sha256:" + "0" * 64, CANDIDATE.resource,
        CANDIDATE.binding, {"legacy": "tampered"})
    store = Store()
    record = await import_compatibility_candidate(store=store, candidate=changed,
                                                  dependencies_ready=True)
    assert record.outcome is ImportOutcome.QUARANTINED
    assert record.reason == "canonical_digest_mismatch" and store.commits == []


@pytest.mark.asyncio
async def test_import_port_owns_atomic_identity_digest_cas_and_conflict_preservation():
    class AtomicStore:
        async def import_if_absent_or_record_conflict(self, *, identity, digest, candidate):
            assert identity == (candidate.source_kind, candidate.source_id, candidate.schema_version)
            assert digest == candidate_canonical_digest(candidate)
            return type("Record", (), {"outcome": ImportOutcome.CONFLICT,
                "reason": "source_digest_changed", "digest": digest})()
    record = await import_compatibility_candidate(store=AtomicStore(), candidate=CANDIDATE,
                                                  dependencies_ready=True)
    assert record.outcome is ImportOutcome.CONFLICT
    assert record.reason == "source_digest_changed"


def test_payload_rejects_inline_secret_material():
    with pytest.raises(ValueError):
        ResourceVersion("v2", 2, "1", "sha256:" + "0" * 64, {"api_token": "raw-value"})


def test_versions_are_append_ordered_unique_and_immutable():
    with pytest.raises(ValueError):
        Resource("bad", ResourceKind.CONFIGURATION, SCOPE, ResourceLifecycle.ACTIVE,
                 (VERSION, ResourceVersion("v0", 1, "1", VERSION.content_digest, PAYLOAD)))
