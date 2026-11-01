"""Source contracts for the exclusive dynamic app registry lifecycle zone."""

from datetime import UTC, datetime

from prodavan.application.dynamic_app_registry import (
    DraftCommand,
    DynamicAppRegistryService,
    LifecycleCommand,
    PublishCommand,
    RegistryOutcome,
    RegistryReason,
    RollbackCommand,
)
from prodavan.domain.dynamic_apps import AppDefinition, AppLifecycleStatus, AppScope, AppScopeKind, AppVersion

NOW = datetime(2026, 1, 1, tzinfo=UTC)
DIGEST = "sha256:" + "a" * 64


def definition(status=AppLifecycleStatus.DRAFT, current_version_id=None):
    return AppDefinition(id="definition-1", tenant_id="tenant-1", app_id="demo-app", display_name="Demo", scope=AppScope(kind=AppScopeKind.TENANT, id="tenant-1"), status=status, current_version_id=current_version_id, created_at=NOW, updated_at=NOW)


def version(status=AppLifecycleStatus.DRAFT, version_id="version-1", digest=DIGEST):
    return AppVersion(id=version_id, app_definition_id="definition-1", manifest_version="1.0.0" if version_id == "version-1" else "2.0.0", artifact_digest=digest, manifest={"protocol": "1.0", "appId": "demo-app", "version": "1.0.0" if version_id == "version-1" else "2.0.0", "displayName": "Demo", "entrypoints": {"ui": {"type": "text"}}, "requestedCapabilities": []}, status=status, created_at=NOW, published_at=NOW if status is AppLifecycleStatus.PUBLISHED else None)


class Repository:
    def __init__(self, definition_row, version_rows):
        self.definition_row = definition_row
        self.version_rows = {row.id: row for row in version_rows}
        self.calls = []

    async def get_definition(self, *, definition_id):
        return self.definition_row if self.definition_row.id == definition_id else None

    async def get_version(self, *, definition_id, version_id):
        row = self.version_rows.get(version_id)
        return row if row and row.app_definition_id == definition_id else None

    async def list_versions(self, *, definition_id):
        return tuple(self.version_rows.values())

    async def append_draft(self, **kwargs):
        self.calls.append(("draft", kwargs))

    async def transition_and_project(self, **kwargs):
        self.calls.append(("transition_and_project", kwargs))


class Quarantine:
    def __init__(self):
        self.records = []

    async def append(self, evidence):
        self.records.append(evidence)


async def test_draft_uses_repository_port_only():
    repo = Repository(definition(), [version()])
    result = await DynamicAppRegistryService(repo).draft(DraftCommand(definition=definition(), version=version(), actor="admin"))
    assert result.outcome is RegistryOutcome.APPLIED
    assert repo.calls[0][0] == "draft"


async def test_publish_requires_expected_current_version_and_projects_after_validation():
    repo = Repository(definition(), [version()])
    result = await DynamicAppRegistryService(repo).publish(PublishCommand(definition_id="definition-1", version_id="version-1", actor="admin", expected_current_version_id="stale"))
    assert (result.outcome, result.reason) == (RegistryOutcome.CONFLICT, RegistryReason.CURRENT_VERSION_CONFLICT)
    assert repo.calls == []


async def test_digest_conflict_is_quarantined_and_never_projected():
    repo = Repository(definition(), [version()])
    quarantine = Quarantine()
    result = await DynamicAppRegistryService(repo, quarantine=quarantine).publish(PublishCommand(definition_id="definition-1", version_id="version-1", actor="admin", expected_artifact_digest="sha256:" + "b" * 64))
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert result.reason is RegistryReason.ARTIFACT_DIGEST_CONFLICT
    assert repo.calls == []
    assert quarantine.records[0].source_id == "version-1"


async def test_publish_quarantines_manifest_identity_drift_before_positive_write():
    malformed = version()
    malformed = malformed.model_copy(update={"manifest": {**malformed.manifest, "appId": "other-app"}})
    repo = Repository(definition(), [malformed])
    quarantine = Quarantine()
    result = await DynamicAppRegistryService(repo, quarantine=quarantine).publish(PublishCommand(definition_id="definition-1", version_id="version-1", actor="admin"))
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert result.reason is RegistryReason.VERSION_DEFINITION_MISMATCH
    assert repo.calls == []


async def test_publish_with_grants_uses_repository_owned_atomic_projection():
    from prodavan.domain.dynamic_apps import AppGrant

    grant = AppGrant(id="grant-1", app_definition_id="definition-1", app_version_id="version-1", scope=AppScope(kind=AppScopeKind.TENANT, id="tenant-1"), issued_at=NOW)
    repo = Repository(definition(), [version()])
    quarantine = Quarantine()
    result = await DynamicAppRegistryService(repo, quarantine=quarantine).publish(PublishCommand(definition_id="definition-1", version_id="version-1", actor="admin", grants=(grant,)))
    assert result.outcome is RegistryOutcome.APPLIED
    assert repo.calls[0][0] == "transition_and_project"
    assert repo.calls[0][1]["grants"] == (grant,)


async def test_rollback_digest_conflict_is_quarantined_without_transition():
    current = version(AppLifecycleStatus.PUBLISHED)
    target = version(AppLifecycleStatus.PUBLISHED, "version-2", "sha256:" + "b" * 64)
    repo = Repository(definition(AppLifecycleStatus.PUBLISHED, "version-1"), [current, target])
    quarantine = Quarantine()
    result = await DynamicAppRegistryService(repo, quarantine=quarantine).rollback(RollbackCommand(definition_id="definition-1", target_version_id="version-2", actor="admin", reason="failed rollout", expected_artifact_digest=DIGEST))
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert result.reason is RegistryReason.ARTIFACT_DIGEST_CONFLICT
    assert repo.calls == []


async def test_disable_rejects_already_disabled_version():
    repo = Repository(definition(AppLifecycleStatus.PUBLISHED, "version-1"), [version(AppLifecycleStatus.DISABLED)])
    result = await DynamicAppRegistryService(repo).disable(LifecycleCommand(definition_id="definition-1", version_id="version-1", actor="admin"))
    assert (result.outcome, result.reason) == (RegistryOutcome.CONFLICT, RegistryReason.INVALID_TRANSITION)
    assert repo.calls == []


async def test_disable_and_revoke_are_explicit_transitions():
    repo = Repository(definition(AppLifecycleStatus.PUBLISHED, "version-1"), [version(AppLifecycleStatus.PUBLISHED)])
    service = DynamicAppRegistryService(repo)
    disabled = await service.disable(LifecycleCommand(definition_id="definition-1", version_id="version-1", actor="admin", expected_current_version_id="version-1"))
    assert disabled.outcome is RegistryOutcome.APPLIED
    repo.version_rows["version-1"] = version(AppLifecycleStatus.DISABLED)
    revoked = await service.revoke(LifecycleCommand(definition_id="definition-1", version_id="version-1", actor="admin"))
    assert revoked.outcome is RegistryOutcome.APPLIED
    assert [call[0] for call in repo.calls] == ["transition_and_project", "transition_and_project"]
    assert [call[1]["status"] for call in repo.calls] == ["disabled", "revoked"]


async def test_rollback_selects_another_immutable_published_version():
    current = version(AppLifecycleStatus.PUBLISHED)
    target = version(AppLifecycleStatus.PUBLISHED, "version-2", "sha256:" + "b" * 64)
    repo = Repository(definition(AppLifecycleStatus.PUBLISHED, "version-1"), [current, target])
    result = await DynamicAppRegistryService(repo).rollback(RollbackCommand(definition_id="definition-1", target_version_id="version-2", actor="admin", reason="failed rollout", expected_current_version_id="version-1"))
    assert result.outcome is RegistryOutcome.APPLIED
    assert result.version is not None
    assert result.version.rollback.from_version_id == "version-1"
    assert repo.calls[0][0] == "transition_and_project"
    assert repo.calls[0][1]["current_version_id"] == "version-2"
    assert repo.calls[0][1]["expected_current_version_id"] == "version-1"
    assert repo.calls[0][1]["expected_artifact_digest"] is None
