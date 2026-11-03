"""Fail-closed server resolver facade and trusted proof/audit ports."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Protocol

from prodavan.application.dynamic_apps_definition import (
    DefinitionResolution,
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from .artifact import ArtifactContentPort
from .readiness import UPSTREAM_BLOCKERS, UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED

_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class MigrationEvidenceContext:
    """Server-derived identity; request tenant assertions are never authority."""

    resolved_tenant_id: str | None
    authenticated: bool
    transaction_id: str | None
    correlation_id: str | None

    @property
    def usable(self) -> bool:
        return bool(
            self.authenticated and self.resolved_tenant_id
            and self.transaction_id and self.correlation_id
        )


@dataclass(frozen=True, slots=True)
class ActiveTypedScopeProof:
    tenant_id: str
    app_id: str
    app_definition_id: str
    scope_kind: str
    scope_id: str
    transaction_id: str
    correlation_id: str
    proof_digest: str
    m04_gate_digest: str
    m04e_gate_digest: str
    active: bool


@dataclass(frozen=True, slots=True)
class DefinitionVersionProof:
    tenant_id: str
    app_id: str
    app_definition_id: str
    app_version_id: str
    manifest_version: str
    transaction_id: str
    correlation_id: str
    definition_owner_digest: str
    same_definition_digest: str
    immutable_artifact_digest: str
    artifact_digest: str
    artifact_uri: str
    artifact_content_digest: str
    current_version_id: str | None
    current_version_definition_id: str | None
    current_version_manifest_version: str | None
    current_version_artifact_digest: str | None
    current_version_artifact_uri: str | None
    current_version_lifecycle: str | None
    current_version_match_count: int | None
    current_version_same_definition_digest: str | None
    current_version_immutable_artifact_digest: str | None
    current_version_proof_digest: str | None
    definition_active: bool
    version_active: bool
    audit_digest: str


class DefinitionResolver(Protocol):
    async def resolve(self, query: DefinitionVersionQuery) -> DefinitionResolution: ...


class ActiveTypedScopePort(Protocol):
    async def prove_active_scope(
        self, *, context: MigrationEvidenceContext, tenant_id: str, app_id: str,
    ) -> ActiveTypedScopeProof | None:
        """Resolve one active typed parent/scope in the trusted transaction."""
        ...


class DefinitionVersionEvidencePort(Protocol):
    async def prove_and_record(
        self, *, context: MigrationEvidenceContext, query: DefinitionVersionQuery,
        resolution: DefinitionResolution, scope: ActiveTypedScopeProof,
        artifact_uri: str, artifact_content_digest: str,
    ) -> DefinitionVersionProof | None:
        """Atomically re-prove owner/key/current/artifact invariants and append evidence."""
        ...

    async def record_denial(
        self, *, context: MigrationEvidenceContext, query: DefinitionVersionQuery | None,
        reason: DefinitionResolutionReason,
    ) -> str | None:
        """Append correlated redacted denial evidence and return its digest."""
        ...


class ServerDefinitionVersionRepository:
    """Tenant/app/manifest resolver; no version-ID-only lookup or write API."""

    def __init__(
        self, resolver: DefinitionResolver, *,
        scopes: ActiveTypedScopePort | None = None,
        evidence: DefinitionVersionEvidencePort | None = None,
        artifacts: ArtifactContentPort | None = None,
    ) -> None:
        self._resolver = resolver
        self._scopes = scopes
        self._evidence = evidence
        self._artifacts = artifacts

    async def resolve(
        self, *, authenticated_context: MigrationEvidenceContext,
        asserted_tenant_id: str | None, app_id: str, manifest_version: str,
        expected_artifact_digest: str | None = None,
    ) -> DefinitionResolution:
        tenant_id = authenticated_context.resolved_tenant_id
        if (
            not authenticated_context.usable or not tenant_id
            or not authenticated_context.transaction_id or not authenticated_context.correlation_id
            or not app_id.strip() or not manifest_version.strip()
            or self._scopes is None or self._evidence is None or self._artifacts is None
            or not UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED
        ):
            return await self._deny(
                authenticated_context, None, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY,
            )
        if asserted_tenant_id is not None and asserted_tenant_id != tenant_id:
            query = DefinitionVersionQuery(tenant_id, app_id, manifest_version)
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.CROSS_TENANT_DEFINITION,
            )
        if expected_artifact_digest is not None and not _DIGEST.fullmatch(expected_artifact_digest):
            query = DefinitionVersionQuery(tenant_id, app_id, manifest_version)
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT,
            )

        query = DefinitionVersionQuery(
            tenant_id=tenant_id, app_id=app_id, manifest_version=manifest_version,
            expected_artifact_digest=expected_artifact_digest,
        )
        scope = await self._scopes.prove_active_scope(
            context=authenticated_context, tenant_id=tenant_id, app_id=app_id,
        )
        if not self._valid_scope(scope, authenticated_context, tenant_id, app_id):
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY,
            )
        assert scope is not None

        result = await self._resolver.resolve(query)
        if result.outcome is not DefinitionResolutionOutcome.RESOLVED:
            return await self._deny(authenticated_context, query, result.reason)
        if result.definition is None or result.version is None or result.reference is None:
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.INVALID_VERSION_HISTORY,
            )
        definition, version = result.definition, result.version
        if (
            definition.status not in {AppLifecycleStatus.DRAFT, AppLifecycleStatus.PUBLISHED}
            or version.status not in {AppLifecycleStatus.DRAFT, AppLifecycleStatus.PUBLISHED}
            or not scope.active
        ):
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.DISALLOWED_VERSION_STATUS,
            )
        if (
            scope.app_definition_id != definition.id or definition.tenant_id != tenant_id
            or definition.app_id != app_id or version.app_definition_id != definition.id
            or definition.scope.kind.value != scope.scope_kind
            or definition.scope.id != scope.scope_id
            or version.manifest_version != manifest_version
            or (expected_artifact_digest is not None and version.artifact_digest != expected_artifact_digest)
        ):
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.CROSS_DEFINITION_VERSION,
            )
        if definition.status is AppLifecycleStatus.PUBLISHED and (
            definition.current_version_id != version.id or version.status is not AppLifecycleStatus.PUBLISHED
        ):
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH,
            )
        if version.status is AppLifecycleStatus.PUBLISHED and (
            definition.status is not AppLifecycleStatus.PUBLISHED
            or definition.current_version_id != version.id
        ):
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH,
            )
        if definition.current_version_id is not None and definition.current_version_id != version.id:
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH,
            )
        if definition.current_version_id is None and definition.status is not AppLifecycleStatus.DRAFT:
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY,
            )
        artifact_uri = await self._artifacts.resolve_artifact_uri(
            tenant_id=tenant_id, app_definition_id=definition.id, app_version_id=version.id,
        )
        if not artifact_uri:
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.INVALID_VERSION_HISTORY,
            )
        artifact_bytes = await self._artifacts.read_artifact(
            tenant_id=tenant_id, app_definition_id=definition.id, app_version_id=version.id,
            artifact_uri=artifact_uri, max_bytes=64 * 1024 * 1024,
        )
        if not isinstance(artifact_bytes, bytes) or len(artifact_bytes) > 64 * 1024 * 1024:
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.INVALID_VERSION_HISTORY,
            )
        artifact_content_digest = "sha256:" + hashlib.sha256(artifact_bytes).hexdigest()
        if artifact_content_digest != version.artifact_digest:
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT,
            )

        proof = await self._evidence.prove_and_record(
            context=authenticated_context, query=query, resolution=result, scope=scope,
            artifact_uri=artifact_uri, artifact_content_digest=artifact_content_digest,
        )
        if not self._valid_resolution_proof(
            proof, authenticated_context, query, result, scope,
            artifact_uri=artifact_uri, artifact_content_digest=artifact_content_digest,
        ):
            return await self._deny(
                authenticated_context, query, DefinitionResolutionReason.INVALID_DEFINITION_HISTORY,
            )
        assert proof is not None and scope is not None
        return DefinitionResolution(
            outcome=result.outcome, definition=definition, version=version, reference=result.reference,
            evidence={
                "correlation_id": authenticated_context.correlation_id or "",
                "scope_digest": scope.proof_digest, "resolution_digest": proof.audit_digest,
                "owner_digest": proof.definition_owner_digest,
                "same_definition_digest": proof.same_definition_digest,
                "artifact_immutability_digest": proof.immutable_artifact_digest,
                "current_version_digest": proof.current_version_proof_digest or "",
            },
        )

    async def _deny(
        self, context: MigrationEvidenceContext, query: DefinitionVersionQuery | None,
        reason: DefinitionResolutionReason | None,
    ) -> DefinitionResolution:
        deny_reason = reason or DefinitionResolutionReason.INVALID_DEFINITION_HISTORY
        evidence: dict[str, str] = {}
        if not UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED:
            evidence["blockers"] = " | ".join(UPSTREAM_BLOCKERS)
        if self._evidence is not None and context.usable:
            digest = await self._evidence.record_denial(context=context, query=query, reason=deny_reason)
            if digest and _DIGEST.fullmatch(digest):
                evidence["denial_digest"] = digest
        if context.correlation_id:
            evidence["correlation_id"] = context.correlation_id
        return DefinitionResolution(
            outcome=DefinitionResolutionOutcome.QUARANTINED,
            reason=deny_reason, evidence=evidence,
        )

    @staticmethod
    def _valid_scope(
        proof: ActiveTypedScopeProof | None, context: MigrationEvidenceContext,
        tenant_id: str, app_id: str,
    ) -> bool:
        return bool(
            proof and proof.active and proof.tenant_id == tenant_id and proof.app_id == app_id
            and proof.scope_id.strip() and proof.scope_kind.strip()
            and proof.transaction_id == context.transaction_id
            and proof.correlation_id == context.correlation_id
            and all(_DIGEST.fullmatch(value) for value in (
                proof.proof_digest, proof.m04_gate_digest, proof.m04e_gate_digest,
            ))
        )

    @staticmethod
    def _valid_resolution_proof(
        proof: DefinitionVersionProof | None, context: MigrationEvidenceContext,
        query: DefinitionVersionQuery, result: DefinitionResolution,
        scope: ActiveTypedScopeProof | None, *, artifact_uri: str,
        artifact_content_digest: str,
    ) -> bool:
        if proof is None or scope is None or result.definition is None or result.version is None or result.reference is None:
            return False
        definition, version = result.definition, result.version
        valid = (
            proof.tenant_id == query.tenant_id == definition.tenant_id
            and proof.app_id == query.app_id == definition.app_id
            and proof.app_definition_id == definition.id == scope.app_definition_id
            and proof.app_version_id == version.id == result.reference.app_version_id
            and proof.manifest_version == query.manifest_version == version.manifest_version
            and proof.transaction_id == context.transaction_id == scope.transaction_id
            and proof.correlation_id == context.correlation_id == scope.correlation_id
            and proof.artifact_digest == version.artifact_digest == result.reference.artifact_digest
            and proof.artifact_uri == artifact_uri
            and proof.artifact_content_digest == artifact_content_digest == version.artifact_digest
            and proof.current_version_id == definition.current_version_id
            and proof.current_version_definition_id == (
                definition.id if definition.current_version_id is not None else None
            )
            and proof.current_version_match_count == (
                1 if definition.current_version_id is not None else 0
            )
            and proof.definition_active
            and proof.version_active
            and all(_DIGEST.fullmatch(value) for value in (
                proof.definition_owner_digest, proof.same_definition_digest,
                proof.immutable_artifact_digest, proof.audit_digest,
            ))
        )
        if definition.current_version_id is not None:
            valid = valid and (
                proof.current_version_definition_id == definition.id
                and proof.current_version_manifest_version == version.manifest_version
                and proof.current_version_artifact_digest == version.artifact_digest
                and proof.current_version_artifact_uri == artifact_uri
                and proof.current_version_lifecycle == "published"
                and proof.current_version_match_count == 1
                and proof.current_version_same_definition_digest == proof.same_definition_digest
                and proof.current_version_immutable_artifact_digest == proof.immutable_artifact_digest
                and proof.current_version_same_definition_digest is not None
                and bool(_DIGEST.fullmatch(proof.current_version_same_definition_digest))
                and proof.current_version_immutable_artifact_digest is not None
                and bool(_DIGEST.fullmatch(proof.current_version_immutable_artifact_digest))
                and proof.current_version_proof_digest is not None
                and bool(_DIGEST.fullmatch(proof.current_version_proof_digest))
            )
        else:
            valid = valid and not any((
                proof.current_version_definition_id,
                proof.current_version_manifest_version,
                proof.current_version_artifact_digest,
                proof.current_version_artifact_uri,
                proof.current_version_lifecycle,
                proof.current_version_same_definition_digest,
                proof.current_version_immutable_artifact_digest,
                proof.current_version_proof_digest,
            ))
        if definition.status is AppLifecycleStatus.PUBLISHED:
            valid = valid and definition.current_version_id == version.id
        if version.status is AppLifecycleStatus.PUBLISHED:
            valid = valid and (
                definition.status is AppLifecycleStatus.PUBLISHED
                and definition.current_version_id == version.id
                and proof.current_version_id == version.id
            )
        return bool(valid)


__all__ = [
    "ActiveTypedScopePort", "ActiveTypedScopeProof", "ArtifactContentPort", "DefinitionResolver",
    "DefinitionVersionEvidencePort", "DefinitionVersionProof", "MigrationEvidenceContext",
    "ServerDefinitionVersionRepository",
]