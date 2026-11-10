"""CI publication service tests using only in-memory ports and fake OCI bytes."""

from __future__ import annotations

import copy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
import hashlib
import json
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    CapabilityGrantRecord,
    EgressPolicy,
    GrantStatus,
    PluginInstanceRecord,
    PluginInstanceStatus,
    PluginPublicationApproval,
    QuotaSet,
)
from prodavan.application.plugins.artifacts.oidc import GitHubActionsClaims
from prodavan.application.plugins.artifacts.publication import ArtifactPublicationCommand
from prodavan.application.plugins.artifacts.publication import (
    DurablePluginInstanceScopeResolver,
)
from prodavan.application.plugins.artifacts.publication import LoadedOciObject
from prodavan.application.plugins.artifacts.publication import PluginCiPublicationService
from prodavan.application.plugins.artifacts.publication import PublicationErrorCode
from prodavan.application.plugins.artifacts.publication import PublicationEvidence
from prodavan.application.plugins.artifacts.publication import _LoaderError
from prodavan.application.plugins.artifacts.registry import InMemoryRegistryStore
from prodavan.application.plugins.artifacts.registry import PluginArtifactRegistry
from prodavan.application.plugins.artifacts.verification import ArtifactVerifier, CompatibilityGate
from prodavan.application.plugins.sources.types import ModuleSource, ModuleSourceStatus
from prodavan.domain.plugins.validation import validate_manifest
from .conftest import FakeCosign, valid_manifest

COMMIT = "c" * 40
WORKFLOW = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
FIXED_TIME = datetime(2026, 11, 9, tzinfo=UTC)


def _canonical_bytes(manifest: dict[str, Any]) -> bytes:
    return (
        json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")


def _source() -> ModuleSource:
    return ModuleSource(
        id="psrc_0123456789abcdef",
        company_id="company_1",
        plugin_id="hello_plugin",
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


def _grant(
    instance_id: str,
    *,
    status: GrantStatus = GrantStatus.ACTIVE,
) -> CapabilityGrantRecord:
    return CapabilityGrantRecord(
        grant_id=f"grant_{instance_id}",
        company_id="company_1",
        plugin_id="hello_plugin",
        plugin_instance_id=instance_id,
        manifest_version="1.2.0",
        artifact_digest="sha256:" + "a" * 64,
        capabilities=("cache",),
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        status=status,
        created_at=FIXED_TIME,
        revoked_at=FIXED_TIME if status is GrantStatus.REVOKED else None,
    )


def _instance(
    instance_id: str,
    *,
    status: PluginInstanceStatus = PluginInstanceStatus.ACTIVE,
) -> PluginInstanceRecord:
    return PluginInstanceRecord(
        plugin_instance_id=instance_id,
        company_id="company_1",
        plugin_id="hello_plugin",
        manifest_version="1.2.0",
        artifact_digest="sha256:" + "a" * 64,
        status=status,
        tenant_db_name="tenant_company_1",
        bucket_name="tenant-company-1",
        created_at=FIXED_TIME,
        updated_at=FIXED_TIME,
    )


def _scope_overrides(instance_id: str) -> dict[str, Any]:
    secret_ref = (
        "vault://ai_keys/plugins/capabilities/secrets/"
        f"company_1/hello_plugin/{instance_id}/api-key"
    )
    return {
        "pods": {
            "allowlist": [
                {
                    "plugin_instance_id": instance_id,
                    "project_id": "project_1",
                    "job_id": "job_1",
                }
            ]
        },
        "secret_delivery": {
            "mode": "k8s_secret",
            "allowlist": [secret_ref],
        },
    }


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
        self.calls: list[str] = []

    async def get_by_id(self, source_id: str) -> ModuleSource | None:
        self.calls.append(source_id)
        return self.source


class FakeOidc:
    def __init__(self) -> None:
        self.calls = 0

    async def validate_for_source(self, token: str, **kwargs: Any) -> GitHubActionsClaims:
        self.calls += 1
        assert token == "fake-oidc-token"
        return _claims()


class FakeLoader:
    def __init__(self, remote: bytes, canonical: bytes) -> None:
        self.remote = remote
        self.canonical = canonical
        self.blob_error: Exception | None = None
        self.manifest_calls = 0
        self.blob_calls = 0

    async def load_manifest(self, ref: object) -> LoadedOciObject:
        self.manifest_calls += 1
        return LoadedOciObject(data=self.remote, digest=ref.digest, size=len(self.remote))

    async def load_blob(
        self, ref: object, *, digest: str, expected_size: int | None = None
    ) -> LoadedOciObject:
        self.blob_calls += 1
        if self.blob_error is not None:
            raise self.blob_error
        return LoadedOciObject(data=self.canonical, digest=digest, size=len(self.canonical))


class FakeScopeStore:
    def __init__(self, record: object | None) -> None:
        self.record = record
        self.error: Exception | None = None

    async def get(self, *, company_id: str, plugin_id: str) -> object | None:
        if self.error is not None:
            raise self.error
        return self.record


class FakeApprovalReader:
    def __init__(self, record: PluginPublicationApproval | None) -> None:
        self.record = record
        self.create_calls = 0
        self.get_calls = 0
        self.consume_calls = 0

    async def get(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> PluginPublicationApproval | None:
        self.get_calls += 1
        assert (company_id, plugin_id) == ("company_1", "hello_plugin")
        assert manifest_version == "1.2.0"
        assert artifact_digest.startswith("sha256:")
        return self.record

    async def create(self, **kwargs: Any) -> None:
        self.create_calls += 1
        raise AssertionError("CI publication must never create approval or grant")

    async def mark_consumed(self, **kwargs: Any) -> None:
        self.consume_calls += 1
        raise AssertionError("CI publication must not consume approval before scope validation")


@dataclass
class Fixture:
    source: ModuleSource
    command: ArtifactPublicationCommand
    service: PluginCiPublicationService
    store: InMemoryRegistryStore
    approval_reader: FakeApprovalReader
    loader: FakeLoader
    oidc: FakeOidc
    cosign: FakeCosign
    canonical: dict[str, Any]
    oci: dict[str, Any]


def _fixture(
    *,
    capabilities: tuple[str, ...] = ("cache",),
    manifest_overrides: dict[str, Any] | None = None,
    grant_record: CapabilityGrantRecord | None = None,
    instance_record: PluginInstanceRecord | None = None,
    scope_error: Exception | None = None,
) -> Fixture:
    source = _source()
    canonical = valid_manifest(
        plugin_id="hello_plugin",
        version="1.2.0",
        capabilities=["cache"],
    )
    canonical.update(manifest_overrides or {})
    canonical_data = _canonical_bytes(canonical)
    config_digest = "sha256:" + hashlib.sha256(canonical_data).hexdigest()
    backend_digest = "sha256:" + "b" * 64
    backend_ref = "ghcr.io/ne-tort/plugins/hello/backend@" + backend_digest
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
            "plugin_id": "hello_plugin",
            "manifest_version": "1.2.0",
            "protocol": "plugin-rpc/1",
            "requestedCapabilities": "cache",
            "min_platform_version": "4.0.0",
            "backend_digest": backend_digest,
            "com.agentscale.plugin.backend_image_ref": backend_ref,
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
        plugin_id="hello_plugin",
        version="1.2.0",
        bundle_ref=bundle_ref,
        bundle_digest=bundle_digest,
        backend_ref=backend_ref,
        backend_digest=backend_digest,
        manifest=oci,
        commit_sha=COMMIT,
        ref="refs/heads/main",
        run_url="https://github.com/ne-tort/example/actions/runs/123/attempts/2",
        evidence=PublicationEvidence(scan_verified=True, csp_verified=True),
    )
    loader = FakeLoader(remote, canonical_data)
    approval = PluginPublicationApproval(
        approval_id="ppr_test",
        company_id=source.company_id,
        plugin_id=source.plugin_id,
        manifest_version=command.version,
        artifact_digest=bundle_digest,
        capabilities=capabilities,
        egress_policy=EgressPolicy(),
        quotas=QuotaSet(),
        created_by="platform-admin",
        created_at=FIXED_TIME,
    )
    approval_reader = FakeApprovalReader(approval)
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
    scope_grants = FakeScopeStore(grant_record)
    scope_instances = FakeScopeStore(instance_record)
    scope_grants.error = scope_error
    scope_instances.error = scope_error
    scope_resolver = DurablePluginInstanceScopeResolver(
        grant_store=scope_grants,  # type: ignore[arg-type]
        instance_store=scope_instances,  # type: ignore[arg-type]
    )
    service = PluginCiPublicationService(
        source_repository=FakeSourceRepository(source),
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
        store=store,
        approval_reader=approval_reader,
        loader=loader,
        oidc=oidc,
        cosign=cosign,
        canonical=canonical,
        oci=oci,
    )


@pytest.mark.asyncio
async def test_valid_ci_publication_binds_metadata_and_publishes_without_install() -> None:
    fixture = _fixture()
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "PUBLISHED"
    record = fixture.store.current("hello_plugin")
    assert record is not None
    assert record.digest == fixture.command.bundle_digest
    assert record.publication is not None
    assert record.publication.source_id == fixture.source.id
    assert record.publication.commit_sha == COMMIT
    assert record.publication.backend_digest == fixture.command.backend_digest
    assert record.publication.run_url == fixture.command.run_url
    assert fixture.oidc.calls == 1
    assert fixture.loader.manifest_calls == 1
    assert fixture.loader.blob_calls == 1
    assert fixture.approval_reader.create_calls == 0


@pytest.mark.asyncio
async def test_valid_deterministic_pods_and_secret_scopes_publish() -> None:
    instance_id = _source().plugin_instance_id
    secret_ref = (
        "vault://ai_keys/plugins/capabilities/secrets/"
        f"company_1/hello_plugin/{instance_id}/api-key"
    )
    fixture = _fixture(manifest_overrides={
        "pods": {"allowlist": [{
            "plugin_instance_id": instance_id,
            "project_id": "project_1",
            "job_id": "job_1",
        }]},
        "secret_delivery": {"mode": "k8s_secret", "allowlist": [secret_ref]},
    })
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "PUBLISHED"
    assert fixture.store.current("hello_plugin") is not None
    assert fixture.approval_reader.get_calls == 1


@pytest.mark.asyncio
async def test_legacy_random_update_scopes_publish_against_persisted_instance() -> None:
    legacy_id = "inst_legacy_random_0123456789abcdef"
    fixture = _fixture(
        manifest_overrides=_scope_overrides(legacy_id),
        grant_record=_grant(legacy_id),
        instance_record=_instance(legacy_id),
    )

    result = await fixture.service.publish(
        fixture.command,
        oidc_token="fake-oidc-token",
    )

    assert result.status == "PUBLISHED"
    assert fixture.approval_reader.get_calls == 1
    assert fixture.store.current("hello_plugin") is not None


@pytest.mark.asyncio
async def test_revoked_legacy_regrant_scope_remains_persisted_not_deterministic() -> None:
    legacy_id = "inst_revoked_legacy_0123456789abcdef"
    fixture = _fixture(
        manifest_overrides=_scope_overrides(legacy_id),
        grant_record=_grant(legacy_id, status=GrantStatus.REVOKED),
        instance_record=_instance(
            legacy_id,
            status=PluginInstanceStatus.REVOKED,
        ),
    )

    result = await fixture.service.publish(
        fixture.command,
        oidc_token="fake-oidc-token",
    )

    # CI scope resolution is identity-only: no ACTIVE grant is required for the
    # later admin re-grant, and the legacy scope is not rewritten.
    assert result.status == "PUBLISHED"
    assert fixture.approval_reader.get_calls == 1


@pytest.mark.asyncio
async def test_grant_instance_mismatch_quarantines_before_approval_or_write() -> None:
    fixture = _fixture(
        grant_record=_grant("inst_legacy_a"),
        instance_record=_instance("inst_legacy_b"),
    )

    result = await fixture.service.publish(
        fixture.command,
        oidc_token="fake-oidc-token",
    )

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SCOPE_MISMATCH.value
    assert fixture.approval_reader.get_calls == 0
    assert fixture.store.current("hello_plugin") is None
    assert fixture.store.lifecycle_log == ()


def _assert_scope_gate_has_no_downstream_effects(fixture: Fixture) -> None:
    assert fixture.approval_reader.get_calls == 0
    assert fixture.approval_reader.consume_calls == 0
    assert fixture.approval_reader.create_calls == 0
    assert fixture.cosign.calls == []
    assert fixture.store.versions("hello_plugin") == ()
    assert fixture.store.current("hello_plugin") is None
    assert fixture.store.quarantine_log == ()
    assert fixture.store.lifecycle_log == ()


@pytest.mark.asyncio
async def test_grant_without_instance_quarantines_before_downstream_effects() -> None:
    # Without declared scopes, any erroneous deterministic/persisted fallback
    # would pass validation and expose downstream calls instead of failing here.
    fixture = _fixture(grant_record=_grant("inst_partial_legacy"))

    result = await fixture.service.publish(
        fixture.command,
        oidc_token="fake-oidc-token",
    )

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SCOPE_MISMATCH.value
    _assert_scope_gate_has_no_downstream_effects(fixture)


@pytest.mark.asyncio
async def test_instance_without_grant_quarantines_before_downstream_effects() -> None:
    fixture = _fixture(instance_record=_instance("inst_partial_legacy"))

    result = await fixture.service.publish(
        fixture.command,
        oidc_token="fake-oidc-token",
    )

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SCOPE_MISMATCH.value
    _assert_scope_gate_has_no_downstream_effects(fixture)


@pytest.mark.asyncio
async def test_scope_resolver_failure_quarantines_before_approval_or_write() -> None:
    fixture = _fixture(scope_error=RuntimeError("durable read unavailable"))

    result = await fixture.service.publish(
        fixture.command,
        oidc_token="fake-oidc-token",
    )

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SCOPE_RESOLUTION_UNAVAILABLE.value
    assert fixture.approval_reader.get_calls == 0
    assert fixture.store.current("hello_plugin") is None
    assert fixture.store.lifecycle_log == ()


@pytest.mark.asyncio
async def test_wildcard_scope_is_denied_before_approval_or_write() -> None:
    fixture = _fixture(
        manifest_overrides={
            "pods": {
                "allowlist": [
                    {
                        "plugin_instance_id": "*",
                        "project_id": "project_1",
                        "job_id": "job_1",
                    }
                ]
            }
        }
    )

    result = await fixture.service.publish(
        fixture.command,
        oidc_token="fake-oidc-token",
    )

    assert result.status == "QUARANTINED"
    assert fixture.approval_reader.get_calls == 0
    assert fixture.store.current("hello_plugin") is None
    assert fixture.store.lifecycle_log == ()


@pytest.mark.asyncio
async def test_foreign_pods_instance_is_rejected_before_approval_or_registry_write() -> None:
    fixture = _fixture(manifest_overrides={
        "pods": {"allowlist": [{
            "plugin_instance_id": "inst_" + "0" * 32,
            "project_id": "project_1",
            "job_id": "job_1",
        }]},
    })
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SCOPE_MISMATCH.value
    assert fixture.approval_reader.get_calls == 0
    assert fixture.store.current("hello_plugin") is None
    assert fixture.store.lifecycle_log == ()


@pytest.mark.asyncio
async def test_secret_ref_from_other_instance_is_rejected_before_any_write() -> None:
    fixture = _fixture(manifest_overrides={
        "secret_delivery": {
            "mode": "k8s_secret",
            "allowlist": [
                "vault://ai_keys/plugins/capabilities/secrets/"
                "company_1/hello_plugin/inst_" + "0" * 32 + "/api-key"
            ],
        },
    })
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SCOPE_MISMATCH.value
    assert fixture.approval_reader.get_calls == 0
    assert fixture.store.current("hello_plugin") is None
    assert fixture.store.lifecycle_log == ()


@pytest.mark.asyncio
async def test_non_typed_validator_result_is_canonical_failure_before_scope_or_write() -> None:
    fixture = _fixture()
    fixture.service._manifest_validator = lambda data: None

    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.CANONICAL_MANIFEST_INVALID.value
    assert fixture.approval_reader.get_calls == 0
    assert fixture.store.current("hello_plugin") is None


@pytest.mark.asyncio
async def test_unknown_source_is_fail_closed_before_oidc_or_loader() -> None:
    fixture = _fixture()
    fixture.service._sources = FakeSourceRepository(None)
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SOURCE_UNKNOWN.value
    assert fixture.oidc.calls == 0
    assert fixture.loader.manifest_calls == 0


@pytest.mark.asyncio
async def test_source_plugin_mismatch_is_denied_before_oidc() -> None:
    fixture = _fixture()
    command = replace(fixture.command, plugin_id="other_plugin")
    result = await fixture.service.publish(command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.SOURCE_PLUGIN_MISMATCH.value
    assert fixture.oidc.calls == 0


@pytest.mark.asyncio
async def test_backend_and_bundle_must_be_distinct() -> None:
    fixture = _fixture()
    command = replace(
        fixture.command,
        backend_digest=fixture.command.bundle_digest,
        backend_ref=fixture.command.bundle_ref,
    )
    result = await fixture.service.publish(command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.BACKEND_BUNDLE_COLLISION.value
    assert fixture.loader.manifest_calls == 0


@pytest.mark.asyncio
async def test_wrong_manifest_blob_digest_is_quarantined() -> None:
    fixture = _fixture()
    fixture.loader.blob_error = _LoaderError(
        "wrong canonical blob digest",
        PublicationErrorCode.CANONICAL_MANIFEST_INVALID,
    )
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.CANONICAL_MANIFEST_INVALID.value
    assert fixture.store.current("hello_plugin") is None


@pytest.mark.asyncio
async def test_submitted_manifest_is_compared_with_remote_oci_manifest() -> None:
    fixture = _fixture()
    command = replace(fixture.command, manifest={**fixture.oci, "artifactType": "forged"})
    result = await fixture.service.publish(command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.OCI_MANIFEST_MISMATCH.value
    assert fixture.store.current("hello_plugin") is None


@pytest.mark.asyncio
async def test_capability_mismatch_is_quarantined_without_grant() -> None:
    fixture = _fixture(capabilities=("objects",))
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert fixture.store.current("hello_plugin") is None
    assert fixture.approval_reader.get_calls == 1
    assert fixture.approval_reader.create_calls == 0


@pytest.mark.asyncio
async def test_canonical_manifest_plugin_id_must_match_request() -> None:
    fixture = _fixture()
    changed = copy.deepcopy(fixture.canonical)
    changed["plugin_id"] = "other_plugin"
    fixture.loader.canonical = _canonical_bytes(changed)
    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.CANONICAL_MANIFEST_INVALID.value
    assert fixture.store.current("hello_plugin") is None


@pytest.mark.asyncio
async def test_first_publication_without_approval_is_quarantined() -> None:
    fixture = _fixture()
    fixture.approval_reader.record = None

    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_REQUIRED.value
    assert fixture.store.current("hello_plugin") is None
    assert fixture.approval_reader.create_calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("manifest_version", "1.2.1"),
        ("artifact_digest", "sha256:" + "f" * 64),
        ("capabilities", ("objects",)),
    ],
)
async def test_publication_rejects_stale_approval_binding(
    field: str, value: object
) -> None:
    fixture = _fixture()
    fixture.approval_reader.record = replace(
        fixture.approval_reader.record, **{field: value}
    )

    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_BINDING_MISMATCH.value
    assert fixture.store.current("hello_plugin") is None


@pytest.mark.asyncio
async def test_consumed_approval_cannot_publish_again() -> None:
    fixture = _fixture()
    fixture.approval_reader.record = replace(
        fixture.approval_reader.record,
        consumed_at=datetime(2026, 11, 9, 1, tzinfo=UTC),
    )

    result = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")

    assert result.status == "QUARANTINED"
    assert result.code == PublicationErrorCode.APPROVAL_CONSUMED.value
    assert fixture.store.current("hello_plugin") is None


@pytest.mark.asyncio
async def test_repeated_publication_does_not_duplicate_artifact_or_create_grant() -> None:
    fixture = _fixture()
    first = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")
    second = await fixture.service.publish(fixture.command, oidc_token="fake-oidc-token")

    assert first.status == "PUBLISHED"
    assert second.status == "PUBLISHED"
    assert second.code == "PUBLISHED"
    assert fixture.store.current("hello_plugin").digest == fixture.command.bundle_digest
    assert fixture.approval_reader.get_calls == 2
    assert fixture.approval_reader.create_calls == 0
    assert not hasattr(fixture.approval_reader.record, "status")
