"""Fail-closed content port for exact M04E immutable version identities.

The legacy artifact URI has no reviewed mapping to a persisted content blob
version. This adapter can resolve the URI from the exact M04E version row, but
will not guess a storage key or read a caller-selected asset.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.migration.m04e_definition_version.artifact import ArtifactContentPort
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    DynamicAppDefinitionVersionFoundationRow,
)

ARTIFACT_CONTENT_MAPPING_BLOCKER = (
    "m04e_artifact_content_mapping_unproven: m04e_dynamic_app_versions.artifact_uri "
    "has no trusted FK or writer-proven mapping to content_assets/content_blob_versions; "
    "no storage_key may be derived"
)


class ArtifactVersionIdentityMismatch(LookupError):
    """No unique persisted version row matches the supplied identity tuple."""

    reason_code = "artifact_version_identity_mismatch"


class ArtifactUriMismatch(ValueError):
    """The supplied URI differs from the URI stored on the exact version row."""

    reason_code = "artifact_uri_mismatch"


class ArtifactContentMappingBlocked(RuntimeError):
    """A byte read is blocked until the URI-to-storage-key gate is proven."""

    reason_code = "artifact_content_mapping_unproven"

    def __init__(self) -> None:
        super().__init__(ARTIFACT_CONTENT_MAPPING_BLOCKER)


class M04EArtifactContentAdapter(ArtifactContentPort):
    """Resolve persisted URI by tuple identity and refuse unproven blob reads.

    No storage key is accepted from the caller. The FileStore is intentionally
    not consulted until a trusted URI-to-blob identity exists.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve_artifact_uri(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
    ) -> str | None:
        row = await self._version_row(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
        )
        if row is None or not isinstance(row.artifact_uri, str) or not row.artifact_uri.strip():
            return None
        return row.artifact_uri

    async def read_artifact(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
        artifact_uri: str,
        max_bytes: int,
    ) -> bytes | None:
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise ValueError("max_bytes must be a positive integer")
        row = await self._version_row(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
        )
        if row is None:
            raise ArtifactVersionIdentityMismatch(
                "no persisted M04E version matches the supplied identity tuple"
            )
        if not isinstance(row.artifact_uri, str) or row.artifact_uri != artifact_uri:
            raise ArtifactUriMismatch(
                "supplied URI does not equal the URI on the exact persisted version row"
            )
        raise ArtifactContentMappingBlocked()

    async def _version_row(
        self,
        *,
        tenant_id: str,
        app_definition_id: str,
        app_version_id: str,
    ) -> DynamicAppDefinitionVersionFoundationRow | None:
        result = await self._session.execute(
            select(DynamicAppDefinitionVersionFoundationRow).where(
                DynamicAppDefinitionVersionFoundationRow.tenant_id == tenant_id,
                DynamicAppDefinitionVersionFoundationRow.definition_id == app_definition_id,
                DynamicAppDefinitionVersionFoundationRow.id == app_version_id,
            )
        )
        rows = tuple(result.scalars().all())
        if not rows:
            return None
        if len(rows) != 1:
            raise ArtifactVersionIdentityMismatch(
                "persisted M04E version identity is ambiguous"
            )
        row = rows[0]
        if (
            row.tenant_id != tenant_id
            or row.definition_id != app_definition_id
            or row.id != app_version_id
        ):
            raise ArtifactVersionIdentityMismatch(
                "database result does not match the supplied identity tuple"
            )
        return row


__all__ = [
    "ARTIFACT_CONTENT_MAPPING_BLOCKER",
    "ArtifactContentMappingBlocked",
    "ArtifactUriMismatch",
    "ArtifactVersionIdentityMismatch",
    "M04EArtifactContentAdapter",
]