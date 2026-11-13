"""B3 regression: fail-closed idempotent exact replay after consumed approval.

A repeated POST of an already PUBLISHED digest whose approval was consumed by
the grant saga must return the CI success contract (HTTP 201 +
status/code=PUBLISHED, see .github/scripts/plugin_publication.py:1084-1115)
instead of the irreversible 422 APPROVAL_CONSUMED. The replay is served only
for the exact same immutable source binding; every difference in
plugin/version/digest/source/ref/trust/capabilities/approval identity stays a
typed rejection without new registry/lifecycle/approval writes.

These tests are written during the migration verification freeze and are not
executed here. All ports are in-memory fakes: no network, database or cluster.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from typing import Any

import pytest
from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import (
    EgressPolicy,
    PluginPublicationApproval,
    QuotaSet,
)
from prodavan.application.plugins.artifacts.oidc import GitHubActionsClaims
from prodavan.application.plugins.artifacts.publication import (
    ArtifactPublicationCommand,
    DurablePluginInstanceScopeResolver,
    LoadedOciObject,
    PluginCiPublicationService,
    PublicationErrorCode,
    PublicationEvidence,
)
from prodavan.application.plugins.artifacts.registry import InMemoryRegistryStore, PluginArtifactRegistry
from prodavan.application.plugins.artifacts.verification import ArtifactVerifier, CompatibilityGate
from prodavan.application.plugins.sources.types import ModuleSource, ModuleSourceStatus
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.domain.plugins.validation import validate_manifest

from .conftest import FakeCosign, valid_manifest

PLUGIN = "hello_plugin"
VERSION = "1.2.0"
COMMIT = "c" * 40
WORKFLOW = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
FIXED_TIME = datetime(2026, 11, 9, tzinfo=UTC)
CONSUMED_AT = datetime(2026, 11, 9, 1, tzinfo=UTC)
BACKEND_DIGEST = "sha256:" + "b" * 64
BACKEND_REF = "ghcr.io/ne-tort/plugins/hello/backend@" + BACKEND_DIGEST
OIDC_TOKEN = "fake-oidc-token"


def _canonical_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _source() -> ModuleSource:
    return ModuleSource(
        id="psrc_0123456789abcdef",
        company_id="company_1",
        plugin_id=PLUGIN,
        repo_id=123456,
        full_name="ne-tort/example",
        private=True,
        default_branch="main",
        installation_credential_ref="vault://github_app/company_1/hello_plugin/installation",
        trusted_workflow_ref=WORKFLOW,
        status=ModuleSourceStatus.READY,
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )


def _claims() -> GitHubActionsClaims:
    return GitHubActionsClaims(
        repository="ne-tort/example",
        repository_id="123456",
        ref="refs/heads/main",
        sha=COMMIT,
        event_name="push",
        job_workflow_ref=WORKFLOW,
        run_id="123",
        run_attempt="2",
    )


class FakeSourceRepository:
    def __init__(self, source: ModuleSource | None) -> None:
        self.source = source

    async def get_by_id(self, source_id: str) -> ModuleSource | None:
        return self.source


class FakeOidc:
    """Records every validation: a replay must never skip the OIDC gate."""

    def __init__(self) -> None:
        self.claims = _claims()
        self.calls = 0

    async def validate_for_source(self, token: str, **kwargs: Any) -> GitHubActionsClaims:
        self.calls += 1
        assert token == OIDC_TOKEN
        return self.claims


class FakeLoader:
    """Records fetches: a replay still re-fetches and digest-verifies bytes."""

    def __init__(self, remote: bytes, canonical: bytes) -> None:
        self.remote = remote
        self.canonical = canonical
        self.manifest_calls = 0
        self.blob_calls = 0

    async def load_manifest(self, ref: object) -> LoadedOciObject:
        self.manifest_calls += 1
        return LoadedOciObject(data=self.remote, digest=ref.digest, size=len(self.remote))

    async def load_blob(
        self, ref: object, *, digest: str, expected_size: int | None = None
    ) -> LoadedOciObject:
        self.blob_calls += 1
        return LoadedOciObject(data=self.canonical, digest=digest, size=len(self.canonical))


class FakeScopeStore:
    def __init__(self, record: object | None = None) -> None:
        self.record = record

    async def get(self, *, company_id: str, plugin_id: str) -> object | None:
        return self.record


class RecordingApprovalReader:
    """Read-only approval port; any create/consume attempt fails the test."""

    def __init__(self, record: PluginPublicationApproval | None) -> None:
        self.record = record
        self.get_keys: list[tuple[str, str, str, str]] = []
        self.create_calls = 0
        self.consume_calls = 0

    async def get(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> PluginPublicationApproval | None:
        self.get_keys.append((company_id, plugin_id, manifest_version, artifact_digest))
        return self.record

    async def create(self, **kwargs: Any) -> None:
        self.create_calls += 1
        raise AssertionError("CI publication must never create an approval")

    async def mark_consumed(self, **kwargs: Any) -> None:
        self.consume_calls += 1
        raise AssertionError("CI publication must never rewrite consumed_at")


@dataclass
class Fixture:
    source: ModuleSource
    command: ArtifactPublicationCommand
    service: PluginCiPublicationService
    sources: FakeSourceRepository
    store: InMemoryRegistryStore
    approval_reader: RecordingApprovalReader
    loader: FakeLoader
    oidc: FakeOidc
    cosign: FakeCosign
    canonical: dict[str, Any]
    oci: dict[str, Any]


def _fixture() -> Fixture:
    source = _source()
    canonical = valid_manifest(plugin_id=PLUGIN, version=VERSION, capabilities=["cache"])
    canonical_data = _canonical_bytes(canonical)
    config_digest = "sha256:" + hashlib.sha256(canonical_data).hexdigest()
    oci = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "artifactType": "application/vnd.agentscale.plugin.oci",
        "config": {
            "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
            "digest": config_digest,
            "size": len(canonical_data),
        },
        "layers": [
            {
                "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
                "digest": config_digest,
                "size": len(canonical_data),
                "annotations": {
                    "com.agentscale.plugin.layer.role": "canonical-manifest",
                    "org.opencontainers.image.title": "canonical-manifest.json",
                },
            },
            {
                "mediaType": "application/vnd.agentscale.plugin.frontend",
                "digest": "sha256:" + "d" * 64,
                "size": 10,
                "annotations": {"com.agentscale.plugin.layer.role": "frontend-dist"},
            },
            {
                "mediaType": "application/vnd.agentscale.plugin.liquibase+xml",
                "digest": "sha256:" + "e" * 64,
                "size": 10,
                "annotations": {"com.agentscale.plugin.layer.role": "migrations"},
            },
        ],
        "annotations": {
            "plugin_id": PLUGIN,
            "manifest_version": VERSION,
            "protocol": "plugin-rpc/1",
            "requestedCapabilities": "cache",
            "min_platform_version": "4.0.0",
            "backend_digest": BACKEND_DIGEST,
            "com.agentscale.plugin.backend_image_ref": BACKEND_REF,
            "com.agentscale.plugin.source_repository": source.full_name,
            "com.agentscale.plugin.source_sha": COMMIT,
            "com.agentscale.plugin.manifest_digest": config_digest,
        },
    }
    remote = json.dumps(oci, sort_keys=True, separators=(",", ":")).encode("utf-8")
    bundle_digest = "sha256:" + hashlib.sha256(remote).hexdigest()
    bundle_ref = "ghcr.io/ne-tort/plugins/hello/bundle@" + bundle_digest
    command = ArtifactPublicationCommand(
        source_id=source.id,
        plugin_id=PLUGIN,
        version=VERSION,
        bundle_ref=bundle_ref,
        bundle_digest=bundle_digest,
        backend_ref=BACKEND_REF,
        backend_digest=BACKEND_DIGEST,
        manifest=oci,
        commit_sha=COMMIT,
        ref="refs/heads/main",
        run_url="https://github.com/ne-tort/example/actions/runs/123/attempts/2",
        evidence=PublicationEvidence(scan_verified=True, csp_verified=True),
    )
    approval = PluginPublicationApproval(
        approval_id="ppr_replay",
        company_id=source.company_id,
        plugin_id=source.plugin_id,
        manifest_version=VERSION,
        artifact_digest=bundle_digest,
        capabilities=("cache",),
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        created_by="platform-admin",
        created_at=FIXED_TIME,
    )
    sources = FakeSourceRepository(source)
    approval_reader = RecordingApprovalReader(approval)
    loader = FakeLoader(remote, canonical_data)
    cosign = FakeCosign()
    verifier = ArtifactVerifier(
        cosign,
        validator=lambda data: None,
        identity=source.expected_signer_identity,
        issuer="https://token.actions.githubusercontent.com",
        compatibility=CompatibilityGate(platform_version="4.0.0"),
        clock=lambda: FIXED_TIME,
    )
    store = InMemoryRegistryStore()
    registry = PluginArtifactRegistry(store=store, verifier=verifier, clock=lambda: FIXED_TIME)
    oidc = FakeOidc()
    scope_resolver = DurablePluginInstanceScopeResolver(
        grant_store=FakeScopeStore(),  # type: ignore[arg-type]
        instance_store=FakeScopeStore(),  # type: ignore[arg-type]
    )
    service = PluginCiPublicationService(
        source_repository=sources,
        oidc_validator=oidc,
        artifact_loader=loader,
        verifier=verifier,
        artifact_registry=registry,
        approval_reader=approval_reader,
        instance_scope_resolver=scope_resolver,
        manifest_validator=validate_manifest,
    )
    return Fixture(
        source=source,
        command=command,
        service=service,
        sources=sources,
        store=store,
        approval_reader=approval_reader,
        loader=loader,
        oidc=oidc,
        cosign=cosign,
        canonical=canonical,
        oci=oci,
    )


def _assert_ci_published_contract(result: Any, command: ArtifactPublicationCommand) -> None:
    """Exact response contract expected by plugin_publication.py:1102-1114."""

    document = asdict(result)
    assert document == {
        "status": "PUBLISHED",
        "code": "PUBLISHED",
        "plugin_id": command.plugin_id,
        "version": command.version,
        "bundle_digest": command.bundle_digest,
        "backend_digest": command.backend_digest,
        "source_id": command.source_id,
    }


@dataclass(frozen=True)
class _State:
    versions: tuple[Any, ...]
    lifecycle: tuple[Any, ...]
    quarantine: tuple[Any, ...]
    current: Any
    cosign_calls: tuple[Any, ...]


def _state(fixture: Fixture) -> _State:
    return _State(
        versions=tuple(fixture.store.versions(PLUGIN)),
        lifecycle=fixture.store.lifecycle_log,
        quarantine=fixture.store.quarantine_log,
        current=fixture.store.current(PLUGIN),
        cosign_calls=tuple(fixture.cosign.calls),
    )


def _assert_no_new_writes(fixture: Fixture, before: _State) -> None:
    """No second registry row, lifecycle entry, quarantine or re-verification."""

    after = _state(fixture)
    assert after == before
    assert len(after.versions) == len(before.versions)
    assert all(new is old for new, old in zip(after.versions, before.versions))
    assert after.current is before.current
    assert fixture.approval_reader.create_calls == 0
    assert fixture.approval_reader.consume_calls == 0


async def _published_fixture() -> Fixture:
    """First publication succeeds; the grant saga then consumes the approval."""

    fixture = _fixture()
    first = await fixture.service.publish(fixture.command, oidc_token=OIDC_TOKEN)
    _assert_ci_published_contract(first, fixture.command)
    assert fixture.store.current(PLUGIN) is not None
    assert len(fixture.store.lifecycle_log) == 1
    fixture.approval_reader.record = replace(
        fixture.approval_reader.record,
        consumed_at=CONSUMED_AT,
    )
    return fixture


@pytest.mark.asyncio
async def test_consumed_approval_exact_digest_replay_returns_published_contract() -> None:
    fixture = await _published_fixture()
    before = _state(fixture)
    approval_reads = len(fixture.approval_reader.get_keys)
    oidc_calls = fixture.oidc.calls
    manifest_calls = fixture.loader.manifest_calls
    blob_calls = fixture.loader.blob_calls

    replay = await fixture.service.publish(fixture.command, oidc_token=OIDC_TOKEN)

    # B3: the late retry of the same digest is a successful idempotent replay,
    # not the irreversible 422 APPROVAL_CONSUMED seen before the fix.
    _assert_ci_published_contract(replay, fixture.command)
    _assert_no_new_writes(fixture, before)
    # Fail-closed gates are NOT loosened: OIDC and both OCI fetches ran again.
    assert fixture.oidc.calls == oidc_calls + 1
    assert fixture.loader.manifest_calls == manifest_calls + 1
    assert fixture.loader.blob_calls == blob_calls + 1
    # The consumed approval was read exactly once, never rewritten or revived.
    assert len(fixture.approval_reader.get_keys) == approval_reads + 1
    assert fixture.approval_reader.record.consumed_at == CONSUMED_AT
    # No second cosign verification and no second registry/lifecycle write.
    assert len(fixture.store.lifecycle_log) == 1
    stored = fixture.store.get(PLUGIN, VERSION)
    assert stored is not None and stored.published_at == FIXED_TIME


@pytest.mark.asyncio
async def test_exact_replay_rerun_with_new_run_url_still_succeeds() -> None:
    fixture = await _published_fixture()
    before = _state(fixture)
    # A CI rerun has a new run id/attempt; run_url is not an immutable field,
    # while commit_sha and both digest-pinned refs stay bound by the digest.
    rerun = replace(
        fixture.command,
        run_url="https://github.com/ne-tort/example/actions/runs/456/attempts/1",
    )
    fixture.oidc.claims = replace(fixture.oidc.claims, run_id="456", run_attempt="1")

    replay = await fixture.service.publish(rerun, oidc_token=OIDC_TOKEN)

    _assert_ci_published_contract(replay, rerun)
    _assert_no_new_writes(fixture, before)
    assert fixture.approval_reader.record.consumed_at == CONSUMED_AT


@pytest.mark.asyncio
async def test_replay_with_mismatched_digest_is_typed_rejection_without_writes() -> None:
    fixture = await _published_fixture()
    new_digest = "sha256:" + "f" * 64
    command = replace(
        fixture.command,
        bundle_digest=new_digest,
        bundle_ref="ghcr.io/ne-tort/plugins/hello/bundle@" + new_digest,
    )
    # A consumed approval exists for the NEW digest, so the consumed gate fires
    # and the replay guard itself must reject the digest mismatch.
    fixture.approval_reader.record = replace(
        fixture.approval_reader.record,
        artifact_digest=new_digest,
    )
    before = _state(fixture)
    approval_reads = len(fixture.approval_reader.get_keys)

    result = await fixture.service.publish(command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_CONSUMED.value
    assert len(fixture.approval_reader.get_keys) == approval_reads + 1
    _assert_no_new_writes(fixture, before)
    stored = fixture.store.get(PLUGIN, VERSION)
    assert stored is not None and stored.digest == fixture.command.bundle_digest


@pytest.mark.asyncio
async def test_replay_with_mismatched_version_is_rejected_before_any_write() -> None:
    fixture = await _published_fixture()
    before = _state(fixture)
    approval_reads = len(fixture.approval_reader.get_keys)
    # The OCI annotation binds manifest_version=1.2.0, so a version change is
    # already a typed mismatch at bundle validation, long before any write.
    command = replace(fixture.command, version="1.2.1")

    result = await fixture.service.publish(command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.OCI_MANIFEST_MISMATCH.value
    assert len(fixture.approval_reader.get_keys) == approval_reads
    _assert_no_new_writes(fixture, before)


@pytest.mark.asyncio
async def test_replay_with_mismatched_plugin_is_rejected_before_oidc() -> None:
    fixture = await _published_fixture()
    before = _state(fixture)
    oidc_calls = fixture.oidc.calls
    command = replace(fixture.command, plugin_id="other_plugin")

    result = await fixture.service.publish(command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SOURCE_PLUGIN_MISMATCH.value
    assert fixture.oidc.calls == oidc_calls
    _assert_no_new_writes(fixture, before)


@pytest.mark.asyncio
async def test_replay_with_mismatched_source_binding_is_rejected_without_writes() -> None:
    fixture = await _published_fixture()
    # Same company/plugin/repository, but a different durable source row: the
    # approval binding still matches, so the replay guard must compare the
    # persisted publication.source_id instead of trusting the request.
    other_source = replace(fixture.source, id="psrc_fedcba9876543210")
    fixture.sources.source = other_source
    command = replace(fixture.command, source_id=other_source.id)
    before = _state(fixture)
    approval_reads = len(fixture.approval_reader.get_keys)

    result = await fixture.service.publish(command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_CONSUMED.value
    assert len(fixture.approval_reader.get_keys) == approval_reads + 1
    _assert_no_new_writes(fixture, before)


@pytest.mark.asyncio
async def test_replay_with_changed_signer_trust_is_rejected_without_writes() -> None:
    fixture = await _published_fixture()
    # Rebinding the source to another trusted workflow changes the expected
    # signer identity; the persisted verification no longer matches exactly.
    fixture.sources.source = replace(
        fixture.source,
        trusted_workflow_ref=(
            "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "d" * 40
        ),
    )
    before = _state(fixture)
    approval_reads = len(fixture.approval_reader.get_keys)

    result = await fixture.service.publish(fixture.command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_CONSUMED.value
    assert len(fixture.approval_reader.get_keys) == approval_reads + 1
    _assert_no_new_writes(fixture, before)


@pytest.mark.asyncio
async def test_replay_with_divergent_manifest_fingerprint_is_rejected() -> None:
    fixture = await _published_fixture()
    stored = fixture.store.get(PLUGIN, VERSION)
    assert stored is not None
    # Store divergence: same digest key but a different canonical fingerprint
    # (covers capabilities and every signed manifest field) must not replay.
    fixture.store.replace(replace(stored, manifest_fingerprint="sha256:" + "0" * 64))
    before = _state(fixture)

    result = await fixture.service.publish(fixture.command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_CONSUMED.value
    _assert_no_new_writes(fixture, before)


@pytest.mark.asyncio
async def test_non_published_record_is_not_replayed_as_success() -> None:
    fixture = await _published_fixture()
    stored = fixture.store.get(PLUGIN, VERSION)
    assert stored is not None
    fixture.store.replace(
        replace(
            stored,
            status=AppLifecycleStatus.REVOKED,
            outcome=RegistryOutcome.QUARANTINED,
            published_at=None,
        )
    )
    before = _state(fixture)

    result = await fixture.service.publish(fixture.command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_CONSUMED.value
    _assert_no_new_writes(fixture, before)
    current = fixture.store.current(PLUGIN)
    assert current is not None and not current.published


@pytest.mark.asyncio
async def test_consumed_approval_without_published_record_remains_consumed_error() -> None:
    # Guard against over-correction: a consumed approval alone is never a
    # success. Only an already PUBLISHED exact binding can replay.
    fixture = _fixture()
    fixture.approval_reader.record = replace(
        fixture.approval_reader.record,
        consumed_at=CONSUMED_AT,
    )
    before = _state(fixture)

    result = await fixture.service.publish(fixture.command, oidc_token=OIDC_TOKEN)

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_CONSUMED.value
    _assert_no_new_writes(fixture, before)
    assert fixture.store.versions(PLUGIN) == ()
    assert fixture.store.current(PLUGIN) is None


@pytest.mark.asyncio
async def test_replay_registry_read_failure_is_fail_closed_unavailable() -> None:
    fixture = await _published_fixture()
    before = _state(fixture)

    async def _read_down(plugin_id: str, version: str) -> None:
        raise RuntimeError("registry read down")

    fixture.store.get_async = _read_down  # type: ignore[attr-defined]

    result = await fixture.service.publish(fixture.command, oidc_token=OIDC_TOKEN)

    # A failed replay lookup must never become a success or a silent write.
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.REGISTRY_UNAVAILABLE.value
    _assert_no_new_writes(fixture, before)
