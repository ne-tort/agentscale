"""Behavioral tests for the fail-closed M04E artifact content boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy.sql.elements import BinaryExpression

from prodavan.application.dynamic_apps_definition.contracts import (
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
)
from prodavan.infrastructure.persistence.migration_definition_version.content_adapter import (
    ArtifactContentMappingBlocked,
    ArtifactUriMismatch,
    ArtifactVersionIdentityMismatch,
    M04EArtifactContentAdapter,
)
from prodavan.infrastructure.persistence.models.dynamic_app_definition_version import (
    DynamicAppDefinitionVersionFoundationRow,
)
from test_definition_version_adapter import (
    ARTIFACT_BYTES,
    ARTIFACT_DIGEST,
    ARTIFACT_URI,
    DEFINITION_ID,
    TENANT,
    VERSION_ID,
    _ArtifactStore,
    _adapter as _persistence_adapter,
    _definition,
    _query,
    _version,
)


class _Result:
    def __init__(self, rows=()):
        self._rows = tuple(rows)

    def scalars(self):
        return self

    def all(self):
        return list(self._rows)


class _VersionSession:
    def __init__(self, rows):
        self.rows = tuple(rows)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        assert statement.column_descriptions[0]["entity"] is DynamicAppDefinitionVersionFoundationRow
        rows = self.rows
        for criterion in statement.whereclause.clauses:
            if not isinstance(criterion, BinaryExpression):
                continue
            key = criterion.left.key
            value = getattr(criterion.right, "value", None)
            rows = tuple(row for row in rows if getattr(row, key) == value)
        return _Result(rows)


def _version_row(**overrides):
    values = {
        "id": VERSION_ID,
        "definition_id": DEFINITION_ID,
        "tenant_id": TENANT,
        "artifact_uri": ARTIFACT_URI,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _blocked_port(rows=None):
    return M04EArtifactContentAdapter(_VersionSession(rows or (_version_row(),)))


@pytest.mark.asyncio
async def test_uri_is_resolved_only_from_the_exact_persisted_identity_tuple():
    port = _blocked_port()

    resolved = await port.resolve_artifact_uri(
        tenant_id=TENANT,
        app_definition_id=DEFINITION_ID,
        app_version_id=VERSION_ID,
    )
    wrong_tenant = await port.resolve_artifact_uri(
        tenant_id="other-tenant",
        app_definition_id=DEFINITION_ID,
        app_version_id=VERSION_ID,
    )
    wrong_version = await port.resolve_artifact_uri(
        tenant_id=TENANT,
        app_definition_id=DEFINITION_ID,
        app_version_id="other-version",
    )

    assert resolved == ARTIFACT_URI
    assert wrong_tenant is None
    assert wrong_version is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("tenant_id", "app_definition_id", "app_version_id"),
    [
        ("other-tenant", DEFINITION_ID, VERSION_ID),
        (TENANT, "other-definition", VERSION_ID),
        (TENANT, DEFINITION_ID, "other-version"),
    ],
)
async def test_read_rejects_tenant_definition_or_version_mismatch(
    tenant_id, app_definition_id, app_version_id,
):
    port = _blocked_port()

    with pytest.raises(ArtifactVersionIdentityMismatch):
        await port.read_artifact(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            app_version_id=app_version_id,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )


@pytest.mark.asyncio
async def test_read_rejects_uri_that_differs_from_the_persisted_version_uri():
    port = _blocked_port()

    with pytest.raises(ArtifactUriMismatch):
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION_ID,
            app_version_id=VERSION_ID,
            artifact_uri="content://caller-selected/object",
            max_bytes=1024,
        )


@pytest.mark.asyncio
async def test_exact_uri_is_still_blocked_without_trusted_storage_mapping():
    port = _blocked_port()

    with pytest.raises(ArtifactContentMappingBlocked, match="m04e_artifact_content_mapping_unproven"):
        await port.read_artifact(
            tenant_id=TENANT,
            app_definition_id=DEFINITION_ID,
            app_version_id=VERSION_ID,
            artifact_uri=ARTIFACT_URI,
            max_bytes=1024,
        )


@pytest.mark.asyncio
async def test_existing_resolver_accepts_matching_sha256_bytes():
    adapter, _ = _persistence_adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=_ArtifactStore(content=ARTIFACT_BYTES),
    )

    result = await adapter.resolve(_query())

    assert result.outcome is DefinitionResolutionOutcome.RESOLVED
    assert result.reference is not None
    assert result.reference.artifact_digest == ARTIFACT_DIGEST


@pytest.mark.asyncio
async def test_existing_resolver_rejects_mismatched_sha256_bytes():
    adapter, _ = _persistence_adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=_ArtifactStore(content=b"different artifact bytes"),
    )

    result = await adapter.resolve(_query())

    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT


@pytest.mark.asyncio
async def test_existing_resolver_treats_absent_bytes_as_unavailable_content():
    adapter, _ = _persistence_adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=_ArtifactStore(content=None),
    )

    result = await adapter.resolve(_query())

    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY


@pytest.mark.asyncio
async def test_existing_resolver_does_not_convert_backend_errors_to_missing_content():
    class _BackendErrorStore(_ArtifactStore):
        async def read_artifact(self, **kwargs):
            raise RuntimeError("object store unavailable")

    adapter, _ = _persistence_adapter(
        {"definitions": (_definition(),), "versions": (_version(),)},
        artifacts=_BackendErrorStore(),
    )

    with pytest.raises(RuntimeError, match="object store unavailable"):
        await adapter.resolve(_query())