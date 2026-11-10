"""Test-only fixtures for real PostgreSQL plugin acceptance.

No SQLite dialect, metadata create_all, in-memory registry, or store replacement
is used. HookedSessionFactory returns a proxy around a real AsyncSession solely
to order races and inject I/O failures; all SQL and transaction semantics remain
SQLAlchemy/asyncpg/PostgreSQL.
"""

from __future__ import annotations

import asyncio
import hashlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome, RollbackCommand
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord, PublicationMetadata
from prodavan.application.plugins.artifacts.verification import VerificationEvidence, manifest_fingerprint
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.infrastructure.persistence.models.artifact_lifecycle import PluginArtifactLifecycleRow
from prodavan.infrastructure.persistence.models.plugin_platform import (
    PluginArtifactRow,
    build_app_version_projection,
)
from prodavan.infrastructure.persistence.repositories.plugins.artifacts import SqlArtifactRegistryStore

from ..postgres_integration._support import sha256_of_text

NOW = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)
DIGEST_A = sha256_of_text("plugin-acceptance-A")
DIGEST_B = sha256_of_text("plugin-acceptance-B")
BACKEND_DIGEST = sha256_of_text("plugin-acceptance-backend")
PLUGIN_ID = "mod_equipment"
SOURCE_ID = "src_acceptance"


class InjectedSessionFailure(RuntimeError):
    """A test-controlled session I/O failure, never a database substitute."""


def evidence_for(record: PluginArtifactRecord) -> VerificationEvidence:
    assert record.manifest_fingerprint == manifest_fingerprint(record.manifest)
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
        granted_capabilities=("equipment.read",),
    )


def make_record(
    version: str = "1.0.0",
    digest: str = DIGEST_A,
    *,
    manifest_override: dict[str, Any] | None = None,
) -> PluginArtifactRecord:
    manifest: dict[str, Any] = manifest_override or {
        "plugin_id": PLUGIN_ID,
        "version": version,
        "entrypoint": "plugin.main:App",
    }
    publication = PublicationMetadata(
        source_id=SOURCE_ID,
        commit_sha="a" * 40,
        backend_digest=BACKEND_DIGEST,
        backend_image_ref="registry.example/backend@" + BACKEND_DIGEST,
        run_url="https://github.com/agentscale/plugins/actions/runs/42",
    )
    bare = PluginArtifactRecord(
        plugin_id=PLUGIN_ID,
        version=version,
        digest=digest,
        image_ref="registry.example/plugin@" + digest,
        manifest=manifest,
        manifest_fingerprint=manifest_fingerprint(manifest),
        status=AppLifecycleStatus.PUBLISHED,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        published_at=NOW,
        created_at=NOW,
        verification=None,
        publication=publication,
        lifecycle_version=None,
    )
    return replace(bare, verification=evidence_for(bare))


def make_draft(record: PluginArtifactRecord) -> PluginArtifactRecord:
    return replace(
        record,
        status=AppLifecycleStatus.DRAFT,
        outcome=RegistryOutcome.QUARANTINED,
        quarantine=None,
        published_at=None,
        verification=None,
        publication=None,
        lifecycle_version=None,
    )


def safe_evidence(record: PluginArtifactRecord) -> dict[str, Any]:
    evidence = record.verification
    assert evidence is not None
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
        "observed_at": evidence.observed_at.astimezone(UTC).isoformat(),
        "granted_capabilities": list(evidence.granted_capabilities),
    }


def expected_projection(record: PluginArtifactRecord) -> dict[str, Any]:
    projection = build_app_version_projection(record.manifest)
    projection["verification_evidence"] = safe_evidence(record)
    return projection


def store_for(session_factory, *, clock=NOW) -> SqlArtifactRegistryStore:
    """The actual production store over a real AsyncSession factory."""
    return SqlArtifactRegistryStore(session_factory=session_factory, clock=lambda: clock)


class HookedSession:
    """Delegate to one real AsyncSession, adding deterministic test hooks."""

    def __init__(self, session: AsyncSession, factory: "HookedSessionFactory") -> None:
        self._session = session
        self._factory = factory

    def __getattr__(self, name: str) -> Any:
        return getattr(self._session, name)

    async def __aenter__(self) -> "HookedSession":
        await self._session.__aenter__()
        return self

    async def __aexit__(self, *exc_info: Any) -> Any:
        return await self._session.__aexit__(*exc_info)

    async def flush(self, *args: Any, **kwargs: Any) -> Any:
        factory = self._factory
        new = tuple(self._session.new)
        if factory.first_insert_barrier is not None and any(
            isinstance(item, PluginArtifactRow) for item in new
        ):
            await factory.first_insert_barrier.wait()
        lifecycle = next(
            (item for item in new if isinstance(item, PluginArtifactLifecycleRow)),
            None,
        )
        if (
            lifecycle is not None
            and factory.fail_lifecycle_actor == lifecycle.actor
            and factory.fail_lifecycle_operation == lifecycle.operation
        ):
            factory.fail_lifecycle_actor = None
            factory.fail_lifecycle_operation = None
            raise InjectedSessionFailure(
                f"injected before-flush {lifecycle.operation} lifecycle failure"
            )
        return await self._session.flush(*args, **kwargs)

    async def commit(self) -> None:
        await self._session.commit()
        if self._factory.fail_after_commit:
            self._factory.fail_after_commit = False
            raise InjectedSessionFailure("injected lost response after durable COMMIT")

    async def rollback(self) -> None:
        await self._session.rollback()


class HookedSessionFactory:
    """A factory whose every product wraps an independent real AsyncSession."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._maker = async_sessionmaker(engine, expire_on_commit=False)
        self.first_insert_barrier: asyncio.Barrier | None = None
        self.fail_lifecycle_actor: str | None = None
        self.fail_lifecycle_operation: str | None = None
        self.fail_after_commit = False
        self.sessions: list[AsyncSession] = []

    def __call__(self) -> HookedSession:
        session = self._maker()
        self.sessions.append(session)
        return HookedSession(session, self)

    def fail_lifecycle(self, *, actor: str, operation: str) -> None:
        self.fail_lifecycle_actor = actor
        self.fail_lifecycle_operation = operation

    def lose_next_commit_response(self) -> None:
        self.fail_after_commit = True


async def stored_state(engine: AsyncEngine) -> dict[str, list[dict[str, Any]]]:
    """Complete raw stored rows, not a count or a domain-only projection."""
    async with engine.connect() as conn:
        artifacts = (
            await conn.execute(text("SELECT * FROM plugin_artifacts ORDER BY id"))
        ).mappings().all()
        lifecycle = (
            await conn.execute(text("SELECT * FROM plugin_artifact_lifecycle ORDER BY id"))
        ).mappings().all()
    return {
        "artifacts": [deepcopy(dict(row)) for row in artifacts],
        "lifecycle": [deepcopy(dict(row)) for row in lifecycle],
    }


def artifact_by_version(state: dict[str, Any], version: str) -> dict[str, Any]:
    return next(row for row in state["artifacts"] if row["version"] == version)


def assert_full_artifact_row(
    row: dict[str, Any],
    record: PluginArtifactRecord,
    *,
    current: bool,
    generation: int,
) -> None:
    """Assert every column of the real stored artifact record."""
    assert isinstance(row["id"], str) and row["id"]
    publication = record.publication
    verification = record.verification
    assert publication is not None and verification is not None
    assert row == {
        "id": row["id"],
        "plugin_id": record.plugin_id,
        "version": record.version,
        "oci_digest": record.digest,
        "image_ref": record.image_ref,
        "manifest": dict(record.manifest),
        "app_version_projection": expected_projection(record),
        "cosign_verified": True,
        "sbom_digest": None,
        "status": "published",
        "quarantine_reason": None,
        "lifecycle_version": generation,
        "source_id": publication.source_id,
        "commit_sha": publication.commit_sha,
        "backend_digest": publication.backend_digest,
        "backend_image_ref": publication.backend_image_ref,
        "signer_identity": verification.identity,
        "issuer": verification.issuer,
        "run_url": publication.run_url,
        "verification_evidence": safe_evidence(record),
        "is_current": current,
        "published_at": record.published_at,
        "created_at": record.created_at,
    }


def assert_full_domain_record(
    stored: PluginArtifactRecord,
    expected: PluginArtifactRecord,
    *,
    generation: int,
) -> None:
    assert expected.verification is not None
    assert stored == replace(
        expected,
        verification=replace(expected.verification, seal=None),
        lifecycle_version=generation,
    )


async def seed_source(db) -> None:
    """Seed the real module-source parent required by artifact provenance FK."""
    await db.execute(
        """
        INSERT INTO plugin_module_sources (
            id, company_id, plugin_id, provider, repo_id, full_name, private,
            default_branch, installation_credential_ref, trusted_workflow_ref,
            status
        ) VALUES (
            :id, :company_id, :plugin_id, 'github_app', :repo_id, :full_name, true,
            'main', :credential_ref, :workflow_ref, 'ready'
        )
        """,
        {
            "id": SOURCE_ID,
            "company_id": "co_a",
            "plugin_id": PLUGIN_ID,
            "repo_id": 900001,
            "full_name": "agentscale/plugins",
            "credential_ref": "secret_ref://ci/plugin",
            "workflow_ref": "agentscale/plugins/.github/workflows/publish.yml@" + "a" * 40,
        },
    )


def rollback_command(
    *,
    target: PluginArtifactRecord,
    current: PluginArtifactRecord,
    reason: str,
) -> RollbackCommand:
    assert current.lifecycle_version is not None
    return RollbackCommand(
        definition_id=target.plugin_id,
        target_version_id=target.version,
        actor="admin",
        reason=reason,
        expected_current_version_id=current.version,
        expected_artifact_digest=target.digest,
        expected_current_lifecycle_version=current.lifecycle_version,
    )


def rollback_token(artifact_id: str, source_version: str, source_generation: int) -> str:
    identity = f"{artifact_id}|{source_version}|{source_generation}".encode("utf-8")
    return "rollback:" + hashlib.sha256(identity).hexdigest()


async def insert_legacy_rollback(
    db,
    *,
    row_id: str,
    artifact_id: str,
    source_version: str,
    source_generation: int,
    actor: str,
    detail: str,
) -> None:
    """Insert a 0907-shaped row; command_fingerprint deliberately does not exist."""
    await db.execute(
        """
        INSERT INTO plugin_artifact_lifecycle (
            id, artifact_id, operation, actor, detail, operation_token
        ) VALUES (
            :id, :artifact_id, 'rollback', :actor, :detail, :operation_token
        )
        """,
        {
            "id": row_id,
            "artifact_id": artifact_id,
            "actor": actor,
            "detail": detail,
            "operation_token": rollback_token(artifact_id, source_version, source_generation),
        },
    )


__all__ = [
    "BACKEND_DIGEST",
    "DIGEST_A",
    "DIGEST_B",
    "HookedSessionFactory",
    "InjectedSessionFailure",
    "NOW",
    "PLUGIN_ID",
    "SOURCE_ID",
    "artifact_by_version",
    "assert_full_artifact_row",
    "assert_full_domain_record",
    "evidence_for",
    "insert_legacy_rollback",
    "make_draft",
    "make_record",
    "rollback_command",
    "rollback_token",
    "safe_evidence",
    "seed_source",
    "stored_state",
    "store_for",
]
