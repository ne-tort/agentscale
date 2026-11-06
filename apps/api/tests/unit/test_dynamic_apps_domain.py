"""Source-level contracts for dynamic app registry foundations."""

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
    CapabilityDecision,
    CapabilityGrant,
    CapabilityName,
    GrantActor,
    RequestedCapability,
    RollbackMetadata,
)


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def test_manifest_contract_preserves_immutable_artifact_and_requested_capabilities() -> None:
    version = AppVersion(
        id="version-1",
        app_definition_id="definition-1",
        manifest_version="1.0.0",
        artifact_digest="sha256:" + "a" * 64,
        manifest={
            "protocol": "1.0",
            "appId": "demo",
            "version": "1.0.0",
            "displayName": "Demo",
            "entrypoints": {"ui": {"kind": "declarative-ui", "root": {"type": "text", "id": "root"}}},
            "requestedCapabilities": [{"name": "storage.kv", "reason": "persist preferences"}],
        },
        requested_capabilities=(
            RequestedCapability(name=CapabilityName.STORAGE_KV, reason="persist preferences"),
        ),
        created_at=NOW,
    )

    assert version.artifact_digest == "sha256:" + "a" * 64
    assert version.requested_capabilities[0].name == "storage.kv"
    with pytest.raises(ValidationError):
        version.artifact_digest = "sha256:" + "b" * 64  # type: ignore[misc]


def test_published_definition_requires_current_version() -> None:
    with pytest.raises(ValidationError, match="current_version_id"):
        AppDefinition(
            id="definition-1",
            tenant_id="tenant-1",
            app_id="demo",
            display_name="Demo",
            scope=AppScope(kind=AppScopeKind.TENANT, id="tenant-1"),
            status=AppLifecycleStatus.PUBLISHED,
            created_at=NOW,
            updated_at=NOW,
        )


def test_manifest_contract_matches_declarative_app_v1() -> None:
    # Манифест валиден по всем полям, кроме проверяемого: иначе более ранний
    # валидатор (appId/displayName) срабатывает первым и тест перестаёт
    # проверять рассогласование `version` и `manifest_version`.
    with pytest.raises(ValidationError, match="manifest version"):
        AppVersion(
            id="version-1",
            app_definition_id="definition-1",
            manifest_version="1.0.0",
            artifact_digest="sha256:" + "a" * 64,
            manifest={
                "protocol": "1.0",
                "appId": "demo",
                "displayName": "Demo",
                "version": "2.0.0",
                "requestedCapabilities": [],
                "entrypoints": {"ui": {}},
            },
            created_at=NOW,
        )

    with pytest.raises(ValidationError, match="artifact sha256"):
        AppVersion(
            id="version-1",
            app_definition_id="definition-1",
            manifest_version="1.0.0",
            artifact_digest="sha256:" + "a" * 64,
            manifest={
                "protocol": "1.0", "appId": "demo", "displayName": "Demo", "version": "1.0.0",
                "entrypoints": {"plugin": {"artifact": {"sha256": "b" * 64}}},
                "requestedCapabilities": [],
            },
            created_at=NOW,
        )


def test_published_version_requires_publication_timestamp() -> None:
    # Валидный по манифесту версия-объект: единственное нарушение — статус
    # PUBLISHED без published_at. Пустой manifest проверял бы protocol, а не
    # требование публикационной метки.
    with pytest.raises(ValidationError, match="published_at"):
        AppVersion(
            id="version-1",
            app_definition_id="definition-1",
            manifest_version="1.0.0",
            artifact_digest="sha256:" + "a" * 64,
            manifest={
                "protocol": "1.0",
                "appId": "demo",
                "displayName": "Demo",
                "version": "1.0.0",
                "requestedCapabilities": [],
                "entrypoints": {"ui": {}},
            },
            status=AppLifecycleStatus.PUBLISHED,
            created_at=NOW,
        )


def test_grant_expiry_and_scope_are_explicit() -> None:
    grant = AppGrant(
        id="grant-1",
        app_definition_id="definition-1",
        app_version_id="version-1",
        scope=AppScope(kind=AppScopeKind.PROJECT, id="project-1"),
        grants=(
            CapabilityGrant(
                name=CapabilityName.NETWORK_FETCH,
                decision=CapabilityDecision.GRANTED,
                granted_by=GrantActor.ADMINISTRATOR,
            ),
        ),
        issued_at=NOW,
    )
    assert grant.scope.kind is AppScopeKind.PROJECT
    assert grant.grants[0].decision is CapabilityDecision.GRANTED

    with pytest.raises(ValidationError, match="after issued_at"):
        AppGrant(
            id="grant-2",
            app_definition_id="definition-1",
            app_version_id="version-1",
            scope=AppScope(kind=AppScopeKind.TENANT, id="tenant-1"),
            issued_at=NOW,
            expires_at=NOW,
        )


def test_rollback_metadata_requires_a_complete_version_pair() -> None:
    with pytest.raises(ValidationError, match="both source and target"):
        RollbackMetadata(from_version_id="version-2")

    instance = AppInstance(
        id="instance-1",
        app_definition_id="definition-1",
        app_version_id="version-1",
        scope=AppScope(kind=AppScopeKind.CABINET, id="cabinet-1"),
        rollback=RollbackMetadata(
            from_version_id="version-2",
            to_version_id="version-1",
            reason="failed rollout",
            occurred_at=NOW,
        ),
        created_at=NOW,
        updated_at=NOW,
    )
    assert instance.rollback.to_version_id == "version-1"
