"""Publish one exact verified test artifact through production OCI/cosign/SQL code."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import PluginPublicationApproval, QuotaSet
from prodavan.application.plugins.artifacts.oci import OciArtifactRef, parse_oci_ref
from prodavan.application.plugins.artifacts.publication import (
    HttpxOciArtifactLoader,
    _canonical_manifest_bytes,
    _validate_bundle_manifest,
)
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PluginArtifactRegistry,
    PublicationMetadata,
)
from prodavan.application.plugins.artifacts.verification import (
    PLATFORM_VERSION,
    ArtifactVerifier,
    CompatibilityGate,
)
from prodavan.application.plugins.jobs.declarations import (
    PluginJobDeclaration,
    published_operations,
)
from prodavan.domain.plugins.validation import validate_manifest
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import (
    SqlArtifactRegistryStore,
)
from prodavan.infrastructure.plugins.runtime.cosign_adapter import CliCosignPort

from .config import AcceptanceConfig


@dataclass(frozen=True, slots=True)
class VerifiedTestArtifact:
    record: PluginArtifactRecord
    declaration: PluginJobDeclaration

    @property
    def declarations_json(self) -> str:
        return json.dumps([{
            "plugin_id": self.declaration.plugin_id,
            "version": self.declaration.version,
            "artifact_digest": self.declaration.artifact_digest,
        }], sort_keys=True, separators=(",", ":"))


async def publish_verified_test_artifact(
    *,
    config: AcceptanceConfig,
    session_factory: Any,
) -> VerifiedTestArtifact:
    """Load digest-pinned OCI data, really verify it, then persist through SQL.

    The approval below is test metadata for the exact disposable artifact, not an
    ACTIVE grant. Verification evidence is produced only by ArtifactVerifier;
    this harness never constructs VerificationEvidence or marks a record verified.
    """
    bundle_ref = parse_oci_ref(config.bundle_ref)
    backend_ref = parse_oci_ref(config.backend_ref)
    if not isinstance(bundle_ref, OciArtifactRef) or not isinstance(backend_ref, OciArtifactRef):
        raise RuntimeError("digest-pinned OciArtifactRef values are required")
    if bundle_ref.digest == backend_ref.digest or bundle_ref.image_ref() == backend_ref.image_ref():
        raise RuntimeError("test bundle and backend must be distinct artifacts")
    if bundle_ref.registry.lower() != backend_ref.registry.lower():
        raise RuntimeError("test bundle and backend must use the same explicit OCI registry")

    loader = HttpxOciArtifactLoader(allowed_registries=(bundle_ref.registry.lower(),))
    remote = await loader.load_manifest(bundle_ref)
    submitted = json.loads(remote.data.decode("utf-8"))
    if not isinstance(submitted, dict):
        raise RuntimeError("test OCI manifest must be a JSON object")
    bundle = _validate_bundle_manifest(
        submitted,
        bundle_ref=bundle_ref,
        backend_ref=backend_ref,
        backend_digest=backend_ref.digest,
        plugin_id=config.plugin_id,
        version=config.version,
        source=SimpleNamespace(full_name=config.source_repository),
        commit_sha=config.commit_sha,
    )
    canonical = await loader.load_blob(
        bundle_ref, digest=bundle.config_digest, expected_size=bundle.config_size
    )
    manifest = json.loads(canonical.data.decode("utf-8"))
    if not isinstance(manifest, dict) or _canonical_manifest_bytes(manifest) != canonical.data:
        raise RuntimeError("test canonical manifest is not the signed canonical JSON object")
    validate_manifest(dict(manifest))
    if (
        manifest.get("plugin_id") != config.plugin_id
        or manifest.get("version") != config.version
        or tuple(manifest.get("capabilities", ())) != bundle.capabilities
        or manifest.get("min_platform_version") != bundle.min_platform_version
    ):
        raise RuntimeError("test artifact identity/capability/platform binding mismatch")

    approval = PluginPublicationApproval(
        approval_id=f"ppr_{config.source_id}",
        company_id=config.company_id,
        plugin_id=config.plugin_id,
        manifest_version=config.version,
        artifact_digest=bundle_ref.digest,
        capabilities=tuple(manifest["capabilities"]),
        quotas=QuotaSet(),
        created_by="entrypoint-disposable-test-ci",
        created_at=datetime.now(UTC),
    )
    cosign = CliCosignPort(
        binary=config.cosign_binary,
        timeout_seconds=config.operation_timeout_seconds,
        kms_key_ref=config.cosign_kms_key_ref,
    )
    verifier = ArtifactVerifier(
        cosign,
        identity=config.cosign_identity,
        issuer=config.cosign_issuer,
        compatibility=CompatibilityGate(platform_version=PLATFORM_VERSION),
    )
    outcome = await asyncio.to_thread(
        verifier.verify,
        bundle_ref,
        manifest,
        grant=approval,
        scan_verified=True,
        csp_verified=True,
    )
    if outcome.outcome is not RegistryOutcome.APPLIED:
        raise RuntimeError("disposable test artifact did not pass real cosign/compatibility verification")
    registry = PluginArtifactRegistry(
        store=SqlArtifactRegistryStore(session_factory=session_factory),
        verifier=verifier,
    )
    result = await registry.publish_async(
        bundle_ref,
        manifest,
        outcome,
        actor="entrypoint-disposable-test-ci",
        grant=approval,
        scan_verified=True,
        csp_verified=True,
        metadata=PublicationMetadata(
            source_id=config.source_id,
            commit_sha=config.commit_sha,
            backend_digest=backend_ref.digest,
            backend_image_ref=backend_ref.image_ref(),
            run_url=config.run_url,
        ),
    )
    if result.outcome is not RegistryOutcome.APPLIED:
        raise RuntimeError("verified test artifact was not published through the SQL registry")
    record = await registry.get_async(config.plugin_id, config.version)
    if record is None:
        raise RuntimeError("published test artifact cannot be re-read from PostgreSQL")
    declaration = PluginJobDeclaration(
        plugin_id=config.plugin_id,
        version=config.version,
        artifact_digest=bundle_ref.digest,
    )
    operations = published_operations((record,), (declaration,))
    if not operations or record.publication is None:
        raise RuntimeError("verified test artifact has no signed job operations/publication binding")
    return VerifiedTestArtifact(record=record, declaration=declaration)
