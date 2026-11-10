"""SQL artifact publication/evidence regressions (verification freeze)."""
from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
)
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PublicationMetadata,
)
from prodavan.application.plugins.artifacts.verification import (
    VerificationEvidence,
    manifest_fingerprint,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.infrastructure.persistence.models.plugin_platform import PluginArtifactRow
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import (
    ConcurrentPublishError,
    SqlArtifactRegistryStore,
)
from prodavan.infrastructure.persistence.repositories.plugins._schema import (
    PluginRepositoryError,
    PluginRepositoryUnavailable,
)

from .conftest import DIGEST, NOW, OTHER_DIGEST, FakeSession, FakeSessionFactory

pytestmark = pytest.mark.asyncio


def _metadata() -> PublicationMetadata:
    return PublicationMetadata(
        source_id="agentscale/plugins",
        commit_sha="a" * 40,
        backend_digest=OTHER_DIGEST,
        backend_image_ref="registry.example/backend@" + OTHER_DIGEST,
        run_url="https://github.com/agentscale/plugins/actions/runs/42",
    )


def _record() -> PluginArtifactRecord:
    manifest = {"plugin_id": "mod_equipment", "version": "1.0.0"}
    return PluginArtifactRecord(
        plugin_id="mod_equipment",
        version="1.0.0",
        digest=DIGEST,
        image_ref="ghcr.io/example@" + DIGEST,
        manifest=manifest,
        manifest_fingerprint=manifest_fingerprint(manifest),
        status=AppLifecycleStatus.PUBLISHED,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        published_at=NOW,
        created_at=NOW,
    )


def _evidence(record: PluginArtifactRecord) -> VerificationEvidence:
    return VerificationEvidence(
        image_ref=record.image_ref,
        artifact_digest=record.digest,
        manifest_fingerprint=record.manifest_fingerprint,
        identity="ci@agentscale",
        issuer="https://token.actions.githubusercontent.com",
        sbom_verified=True,
        provenance_verified=True,
        scan_verified=True,
        csp_verified=True,
        observed_at=NOW,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        seal=object(),
    )


def _verified_record() -> PluginArtifactRecord:
    record = _record()
    return replace(record, verification=_evidence(record), publication=_metadata())


def _safe_evidence_json(evidence: VerificationEvidence) -> dict[str, object]:
    return {
        "image_ref": evidence.image_ref,
        "artifact_digest": evidence.artifact_digest,
        "manifest_fingerprint": evidence.manifest_fingerprint,
        "identity": evidence.identity,
        "issuer": evidence.issuer,
        "sbom_verified": True,
        "provenance_verified": True,
        "scan_verified": True,
        "csp_verified": True,
        "observed_at": NOW.isoformat(),
        "granted_capabilities": list(evidence.granted_capabilities),
    }


def _artifact_row(**overrides: object) -> PluginArtifactRow:
    record = _verified_record()
    assert record.verification is not None and record.publication is not None
    safe = _safe_evidence_json(record.verification)
    data: dict[str, object] = {
        "id": "part_01",
        "plugin_id": record.plugin_id,
        "version": record.version,
        "oci_digest": record.digest,
        "image_ref": record.image_ref,
        "manifest": dict(record.manifest),
        "app_version_projection": {"protocol": "1.0", "verification_evidence": safe},
        "verification_evidence": safe,
        "cosign_verified": True,
        "source_id": record.publication.source_id,
        "commit_sha": record.publication.commit_sha,
        "backend_digest": record.publication.backend_digest,
        "backend_image_ref": record.publication.backend_image_ref,
        "signer_identity": record.verification.identity,
        "issuer": record.verification.issuer,
        "run_url": record.publication.run_url,
        "status": "published",
        "quarantine_reason": None,
        "lifecycle_version": 7,
        "is_current": False,
        "published_at": NOW,
        "created_at": NOW,
    }
    data.update(overrides)
    return PluginArtifactRow(**data)


def _store(session: FakeSession) -> SqlArtifactRegistryStore:
    return SqlArtifactRegistryStore(
        session_factory=FakeSessionFactory(session), clock=lambda: NOW
    )


async def test_verified_add_then_set_current_persists_evidence_and_metadata() -> None:
    session = FakeSession(rows=[], rowcount=1)
    store = _store(session)
    record = _verified_record()
    assert record.verification is not None and record.publication is not None

    await store.add_async(record)
    inserted = session.added[-1]
    expected_evidence = _safe_evidence_json(record.verification)
    assert inserted.cosign_verified is True
    assert inserted.oci_digest == record.digest
    assert inserted.image_ref == record.image_ref
    assert inserted.signer_identity == record.verification.identity
    assert inserted.issuer == record.verification.issuer
    assert inserted.source_id == record.publication.source_id
    assert inserted.commit_sha == record.publication.commit_sha
    assert inserted.backend_digest == record.publication.backend_digest
    assert inserted.backend_image_ref == record.publication.backend_image_ref
    assert inserted.run_url == record.publication.run_url
    assert inserted.verification_evidence == expected_evidence
    assert inserted.app_version_projection["verification_evidence"] == expected_evidence
    for persisted_evidence in (
        inserted.verification_evidence,
        inserted.app_version_projection["verification_evidence"],
    ):
        assert "seal" not in persisted_evidence
        assert "secret" not in persisted_evidence

    # The same fake session now returns the inserted candidate to SELECT FOR UPDATE.
    session.rows = [inserted]
    await store.set_current_async("mod_equipment", "1.0.0")
    assert session.commits == 2

    restored = await store.get_async("mod_equipment", "1.0.0")
    assert restored is not None
    assert restored.digest == record.digest
    assert restored.image_ref == record.image_ref
    assert restored.publication == record.publication
    assert restored.verification == replace(record.verification, seal=None)
    assert restored.verification is not None
    assert restored.verification.issuer == record.verification.issuer
    assert restored.verification.seal is None


async def test_published_add_without_evidence_is_explicit_error() -> None:
    session = FakeSession(rows=[])
    store = _store(session)
    with pytest.raises(PluginRepositoryError, match="VerificationEvidence"):
        await store.add_async(replace(_record(), publication=_metadata()))
    assert session.added == []


async def test_published_add_without_metadata_is_explicit_error() -> None:
    session = FakeSession(rows=[])
    store = _store(session)
    with pytest.raises(PluginRepositoryError, match="PublicationMetadata"):
        await store.add_async(replace(_verified_record(), publication=None))
    assert session.added == []


async def test_legacy_empty_evidence_read_has_no_active_provenance() -> None:
    provenance_fields = (
        "source_id",
        "commit_sha",
        "backend_digest",
        "backend_image_ref",
        "signer_identity",
        "issuer",
        "run_url",
    )
    row = _artifact_row(
        **{name: None for name in provenance_fields},
        verification_evidence={},
        app_version_projection={"protocol": "1.0"},
    )
    session = FakeSession(rows=[row])
    store = _store(session)

    restored = await store.get_async("mod_equipment", "1.0.0")
    assert restored is not None
    assert restored.verification is None
    assert restored.publication is None
    with pytest.raises(PluginRepositoryError, match="PublicationMetadata"):
        await store.set_current_async("mod_equipment", "1.0.0")

    assert row.status == "published"
    assert row.is_current is False
    assert row.verification_evidence == {}
    assert all(getattr(row, name) is None for name in provenance_fields)
    assert session.commits == 0
    assert len(session.statements) == 2  # Two SELECTs; no provenance/current UPDATE.


@pytest.mark.parametrize(
    "missing_field",
    ("source_id", "commit_sha", "backend_digest", "backend_image_ref", "run_url"),
)
async def test_incomplete_publication_metadata_cannot_activate(missing_field: str) -> None:
    row = _artifact_row(**{missing_field: None})
    session = FakeSession(rows=[row])
    store = _store(session)

    restored = await store.get_async("mod_equipment", "1.0.0")
    assert restored is not None
    assert restored.publication is None
    with pytest.raises(PluginRepositoryError, match="PublicationMetadata"):
        await store.set_current_async("mod_equipment", "1.0.0")
    assert row.status == "published"
    assert row.is_current is False
    assert session.commits == 0
    assert len(session.statements) == 2  # Two SELECTs; no provenance/current UPDATE.


async def test_evidence_falls_back_to_app_version_projection() -> None:
    row = _artifact_row(verification_evidence={})
    store = _store(FakeSession(rows=[row]))

    restored = await store.get_async("mod_equipment", "1.0.0")
    assert restored is not None and restored.verification is not None
    assert restored.verification.identity == "ci@agentscale"
    assert restored.verification.issuer == "https://token.actions.githubusercontent.com"
    assert restored.verification.seal is None
    assert restored.publication is not None


async def test_set_current_cas_conflict_denies_second_publish() -> None:
    session = FakeSession(rows=[_artifact_row()], rowcount=0)
    store = _store(session)
    with pytest.raises(ConcurrentPublishError):
        await store.set_current_async("mod_equipment", "1.0.0")
    assert session.rollbacks == 1
    assert session.commits == 0


async def test_set_current_rejects_unpublished_artifact() -> None:
    store = _store(FakeSession(rows=[_artifact_row(status="quarantined")]))
    with pytest.raises(PluginRepositoryUnavailable):
        await store.set_current_async("mod_equipment", "1.0.0")


async def test_both_async_artifact_reads_return_typed_record() -> None:
    store = _store(FakeSession(rows=[_artifact_row()]))
    by_digest = await store.by_digest_async(DIGEST)
    by_version = await store.get_async("mod_equipment", "1.0.0")
    assert by_digest is not None and by_digest.digest == DIGEST
    assert by_version is not None and by_version.digest == DIGEST
    assert by_version.verification is not None
    assert by_version.publication is not None


async def test_add_duplicate_version_is_concurrent_publish_error() -> None:
    store = _store(FakeSession(rows=[_artifact_row()]))
    record = await store.by_digest_async(DIGEST)
    assert record is not None
    with pytest.raises(ConcurrentPublishError):
        await store.add_async(record)


async def test_current_reads_is_current_and_append_only_logs() -> None:
    store = _store(FakeSession(rows=[_artifact_row(is_current=True)]))
    record = await store.current_async("mod_equipment")
    assert record is not None and record.digest == DIGEST

    evidence = QuarantineEvidence(
        reason=RegistryReason.INVALID_TRANSITION,
        source_kind="test",
        source_id="mod_equipment@1.0.0",
        source_digest=DIGEST,
        observed_at=NOW,
        metadata={},
    )
    await store.append_quarantine_async(evidence)
    await store.append_lifecycle_async(command=None, actor="admin", detail="publish")
    await store.append_lifecycle_async(command=None, actor="admin", detail="rollback")
    assert len(store.quarantine_log) == 1
    assert len(store.lifecycle_log) == 2
