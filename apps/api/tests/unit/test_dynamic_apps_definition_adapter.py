"""Source-level acceptance checks for the exclusive M04.e ORM adapter."""

from datetime import datetime, timezone

import pytest

from prodavan.application.dynamic_apps_definition import (
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
)
from prodavan.application.dynamic_apps_definition_adapter import DynamicAppDefinitionRepositoryAdapter
from prodavan.domain.dynamic_apps import AppLifecycleStatus, CapabilityName
from prodavan.infrastructure.persistence.models.dynamic_apps import AppDefinitionRow, AppVersionRow

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
DIGEST = "sha256:" + "a" * 64


class _Scalars:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Result:
    def __init__(self, rows):
        self._rows = rows

    def scalars(self):
        return _Scalars(self._rows)


class _Session:
    def __init__(self, rows):
        self.rows = rows
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return _Result(self.rows)


def _definition_row(**overrides):
    values = {
        "id": "definition-1",
        "tenant_id": "tenant-1",
        "app_id": "demo-app",
        "display_name": "Demo",
        "description": None,
        "scope_kind": "tenant",
        "scope_id": "tenant-1",
        "status": "published",
        "current_version_id": "version-1",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(overrides)
    return AppDefinitionRow(**values)


def _version_row(**overrides):
    values = {
        "id": "version-1",
        "app_definition_id": "definition-1",
        "manifest_version": "1.0.0",
        "artifact_digest": DIGEST,
        "artifact_uri": None,
        "manifest": {
            "protocol": "1.0",
            "appId": "demo-app",
            "version": "1.0.0",
            "displayName": "Demo",
            "entrypoints": {"ui": {"type": "text"}},
            "requestedCapabilities": [
                {"name": "storage.kv", "reason": "persist preferences", "optional": True}
            ],
        },
        "requested_capabilities": [
            {"name": "storage.kv", "reason": "persist preferences", "optional": True}
        ],
        "status": "published",
        "rollback_from_version_id": "version-0",
        "rollback_to_version_id": "version-1",
        "rollback_reason": "failed rollout",
        "rollback_at": NOW,
        "created_at": NOW,
        "published_at": NOW,
    }
    values.update(overrides)
    return AppVersionRow(**values)


@pytest.mark.asyncio
async def test_adapter_maps_version_immutable_fields_and_full_rollback():
    adapter = DynamicAppDefinitionRepositoryAdapter(_Session([]))
    version = adapter._version(_version_row())

    assert version.id == "version-1"
    assert version.artifact_digest == DIGEST
    assert version.requested_capabilities[0].name is CapabilityName.STORAGE_KV
    assert version.rollback.from_version_id == "version-0"
    assert version.rollback.to_version_id == "version-1"
    assert version.rollback.reason == "failed rollout"
    assert version.rollback.occurred_at == NOW


@pytest.mark.asyncio
async def test_repository_reads_are_scoped_and_history_is_explicit():
    session = _Session([_version_row()])
    adapter = DynamicAppDefinitionRepositoryAdapter(session)

    versions = await adapter.list_versions(app_definition_id="definition-1", manifest_version="1.0.0")
    history = await adapter.list_definition_versions(app_definition_id="definition-1")

    assert len(versions) == len(history) == 1
    assert "dynamic_app_versions" in str(session.statements[0])
    assert "app_definition_id" in str(session.statements[0])
    assert "manifest_version" in str(session.statements[0])
    assert "manifest_version" not in str(session.statements[1])
    assert "created_at" in str(session.statements[1])
    assert "id" in str(session.statements[1])


@pytest.mark.asyncio
async def test_definition_lookup_has_tenant_and_app_composite_predicates():
    session = _Session([_definition_row()])
    adapter = DynamicAppDefinitionRepositoryAdapter(session)

    definitions = await adapter.list_definitions(tenant_id="tenant-1", app_id="demo-app")

    assert len(definitions) == 1
    statement = str(session.statements[0])
    assert "tenant_id" in statement
    assert "app_id" in statement
    assert "dynamic_app_definitions" in statement


def test_version_mapper_rejects_capability_fallback_or_mismatch():
    with pytest.raises(ValueError, match="disagree"):
        DynamicAppDefinitionRepositoryAdapter._version(
            _version_row(
                requested_capabilities=[],
            )
        )

    with pytest.raises(ValueError, match="arrays"):
        DynamicAppDefinitionRepositoryAdapter._version(
            _version_row(
                requested_capabilities=None,
                manifest={"requestedCapabilities": {}, "protocol": "1.0"},
            )
        )

    with pytest.raises(ValueError, match="object"):
        DynamicAppDefinitionRepositoryAdapter._version(_version_row(manifest=None))


@pytest.mark.asyncio
async def test_resolve_quarantines_malformed_rows_without_positive_evidence():
    session = _Session([_definition_row(scope_kind="not-a-scope")])
    adapter = DynamicAppDefinitionRepositoryAdapter(session)

    result = await adapter.resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )

    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY
    assert result.reference is None
    assert result.definition is None


def test_version_mapper_quarantines_incomplete_rollback_metadata():
    with pytest.raises(ValueError, match="rollback metadata"):
        DynamicAppDefinitionRepositoryAdapter._version(
            _version_row(rollback_to_version_id=None)
        )


def test_adapter_has_no_positive_write_surface():
    names = set(dir(DynamicAppDefinitionRepositoryAdapter))
    assert not names.intersection({"save", "create", "update", "delete", "add", "flush", "commit"})
