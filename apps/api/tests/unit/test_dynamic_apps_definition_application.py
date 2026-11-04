"""Source-level acceptance checks for the M04.e definition/version adapter."""

from datetime import datetime, timezone

import pytest

from prodavan.application.dynamic_apps_definition import (
    DefinitionResolutionOutcome,
    DefinitionResolutionReason,
    DefinitionVersionQuery,
    DynamicAppDefinitionService,
)
from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
)

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
DIGEST = "sha256:" + "a" * 64

#: Distinguishes "caller said nothing" from an explicit ``published_at=None``.
_UNSET = object()


def _definition(*, status=AppLifecycleStatus.PUBLISHED, current_version_id="version-1", tenant_id="tenant-1", app_id="demo-app", scope=None):
    return AppDefinition(
        id="definition-1",
        tenant_id=tenant_id,
        app_id=app_id,
        display_name="Demo",
        scope=scope or AppScope(kind=AppScopeKind.TENANT, id=tenant_id),
        status=status,
        current_version_id=current_version_id,
        created_at=NOW,
        updated_at=NOW,
    )


def _version(
    *,
    status=AppLifecycleStatus.PUBLISHED,
    definition_id="definition-1",
    version_id="version-1",
    digest=DIGEST,
    app_id="demo-app",
    manifest_version="1.0.0",
    published_at=_UNSET,
):
    """Build an ``AppVersion``.

    ``published_at`` is a real field of ``AppVersion`` (``types.AppVersion``) and
    ``DynamicAppDefinitionService.resolve`` compares it between the selected
    version and its history row, so the factory must be able to set it. It stays
    derived from ``status`` when the caller says nothing — a published version
    without a publication timestamp is rejected by the model itself, and a
    non-published one must not carry a timestamp. An explicit ``None`` is
    therefore preserved rather than overridden, so a test can still model the
    malformed row the service is required to quarantine.
    """

    if published_at is _UNSET:
        published_at = NOW if status is AppLifecycleStatus.PUBLISHED else None
    return AppVersion(
        id=version_id,
        app_definition_id=definition_id,
        manifest_version=manifest_version,
        artifact_digest=digest,
        manifest={
            "protocol": "1.0",
            "appId": app_id,
            "version": manifest_version,
            "displayName": "Demo",
            "entrypoints": {"ui": {"type": "text"}},
            "requestedCapabilities": [],
        },
        status=status,
        created_at=NOW,
        published_at=published_at,
    )


class FakeRepository:
    def __init__(self, definition, versions, history=None):
        self.definition = definition
        self.versions = versions
        self.history = history if history is not None else versions
        self.calls = []

    async def list_definitions(self, *, tenant_id, app_id):
        self.calls.append(("definitions", tenant_id, app_id))
        return [self.definition] if self.definition is not None else []

    async def list_versions(self, *, app_definition_id, manifest_version):
        self.calls.append(("versions", app_definition_id, manifest_version))
        return self.versions

    async def list_definition_versions(self, *, app_definition_id):
        self.calls.append(("history", app_definition_id))
        return self.history


@pytest.mark.parametrize(
    ("definition", "versions", "expected"),
    [
        (None, [], (DefinitionResolutionOutcome.MISSING, DefinitionResolutionReason.MISSING_DEFINITION)),
        (_definition(), [], (DefinitionResolutionOutcome.MISSING, DefinitionResolutionReason.MISSING_VERSION)),
        (_definition(current_version_id="other"), [_version()], (DefinitionResolutionOutcome.CONFLICT, DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)),
        (_definition(status=AppLifecycleStatus.DRAFT), [_version()], (DefinitionResolutionOutcome.CONFLICT, DefinitionResolutionReason.PUBLISHED_VERSION_MISMATCH)),
        (_definition(), [_version(definition_id="other")], (DefinitionResolutionOutcome.QUARANTINED, DefinitionResolutionReason.CROSS_DEFINITION_VERSION)),
    ],
)
async def test_resolution_rejects_missing_or_invalid_history(definition, versions, expected):
    repository = FakeRepository(definition, versions)
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )
    assert (result.outcome, result.reason) == expected
    assert result.reference is None


async def test_cross_tenant_definition_is_quarantined_without_version_lookup():
    repository = FakeRepository(_definition(tenant_id="tenant-2"), [_version()])
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )
    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.CROSS_TENANT_DEFINITION
    assert repository.calls == [("definitions", "tenant-1", "demo-app")]


@pytest.mark.parametrize(
    "definition",
    [
        _definition(app_id="other-app"),
        _definition(scope=AppScope(kind=AppScopeKind.TENANT, id="tenant-2")),
    ],
)
async def test_definition_owner_anchor_mismatch_is_quarantined_without_version_lookup(definition):
    repository = FakeRepository(definition, [_version()])
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )
    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason in {
        DefinitionResolutionReason.CROSS_APP_DEFINITION,
        DefinitionResolutionReason.INVALID_DEFINITION_HISTORY,
    }
    assert repository.calls == [("definitions", "tenant-1", "demo-app")]


async def test_empty_or_unrelated_history_cannot_prove_selected_version():
    for history in ([], [_version(version_id="other-version")]):
        repository = FakeRepository(_definition(), [_version()], history=history)
        result = await DynamicAppDefinitionService(repository).resolve(
            DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
        )
        assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
        assert result.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY
        assert result.reference is None


async def test_duplicate_history_is_quarantined_before_positive_resolution():
    duplicate = _version(version_id="version-2", digest="sha256:" + "b" * 64)
    repository = FakeRepository(_definition(), [_version()], history=[_version(), duplicate])
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )
    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY
    assert result.reference is None


async def test_history_manifest_identity_and_version_are_quarantined():
    repository = FakeRepository(
        _definition(),
        [_version()],
        history=[_version(), _version(version_id="version-2", app_id="other-app", digest="sha256:" + "b" * 64)],
    )
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )
    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY


@pytest.mark.parametrize(
    "history_version",
    [
        _version(status=AppLifecycleStatus.DRAFT, digest=DIGEST),
        _version(published_at=NOW.replace(second=1)),
    ],
)
async def test_selected_snapshot_must_match_history_status_and_publication(history_version):
    repository = FakeRepository(_definition(), [_version()], history=[history_version])
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )
    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.INVALID_VERSION_HISTORY
    assert result.reference is None


async def test_cross_definition_history_is_quarantined_without_positive_result():
    repository = FakeRepository(_definition(), [_version()], history=[_version(), _version(version_id="other", definition_id="other")])
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(tenant_id="tenant-1", app_id="demo-app", manifest_version="1.0.0")
    )
    assert result.outcome is DefinitionResolutionOutcome.QUARANTINED
    assert result.reason is DefinitionResolutionReason.CROSS_DEFINITION_VERSION
    assert result.reference is None


async def test_resolution_is_scoped_and_returns_canonical_version_id():
    repository = FakeRepository(_definition(), [_version()])
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(
            tenant_id="tenant-1",
            app_id="demo-app",
            manifest_version="1.0.0",
            expected_artifact_digest=DIGEST,
        )
    )
    assert result.outcome is DefinitionResolutionOutcome.RESOLVED
    assert result.reference is not None
    assert result.reference.app_version_id == "version-1"
    assert repository.calls == [
        ("definitions", "tenant-1", "demo-app"),
        ("versions", "definition-1", "1.0.0"),
        ("history", "definition-1"),
    ]


async def test_digest_conflict_has_no_positive_result():
    repository = FakeRepository(_definition(), [_version()])
    result = await DynamicAppDefinitionService(repository).resolve(
        DefinitionVersionQuery(
            tenant_id="tenant-1",
            app_id="demo-app",
            manifest_version="1.0.0",
            expected_artifact_digest="sha256:" + "b" * 64,
        )
    )
    assert result.outcome is DefinitionResolutionOutcome.CONFLICT
    assert result.reason is DefinitionResolutionReason.ARTIFACT_DIGEST_CONFLICT
    assert result.definition is None
    assert not any(name.startswith("save") for name, *_ in repository.calls)


def test_adapter_zone_exposes_no_positive_write_method():
    assert not any(name.startswith(("save", "create", "update", "delete")) for name in dir(DynamicAppDefinitionService))
