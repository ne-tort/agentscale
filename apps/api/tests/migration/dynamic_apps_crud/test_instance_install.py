"""Instance installation acceptance: tenant-scoped installs onto published versions.

Authored for the migration freeze; not executed yet.
"""

from __future__ import annotations

import pytest

from prodavan.application.dynamic_apps_definition import (
    ArchiveDefinitionRequest,
    CreateDefinitionRequest,
    CreateVersionRequest,
    DynamicAppOperation,
    InstallInstanceRequest,
    PublishVersionRequest,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus, AppScopeKind

from .fakes import (
    CABINET_A,
    CABINET_B,
    DIGEST_1,
    DIGEST_2,
    PROJECT_A,
    TENANT_A,
    TENANT_B,
    context,
    manifest,
    service,
)


async def _serving_app(svc, *, app_id: str = "equipment-review", tenant: str = TENANT_A):
    created = await svc.create_definition(
        context=context(tenant_id=tenant, key=f"create-{app_id}"),
        request=CreateDefinitionRequest(app_id=app_id, display_name="Equipment review"),
    )
    definition_id = created.data["id"]
    version = await svc.create_version(
        context=context(
            tenant_id=tenant,
            operation=DynamicAppOperation.CREATE_VERSION,
            key=f"version-{app_id}",
        ),
        definition_id=definition_id,
        request=CreateVersionRequest(
            manifest=manifest(app_id=app_id), artifact_digest=DIGEST_1
        ),
    )
    published = await svc.publish_version(
        context=context(
            tenant_id=tenant,
            operation=DynamicAppOperation.PUBLISH_VERSION,
            key=f"publish-{app_id}",
        ),
        request=PublishVersionRequest(
            definition_id=definition_id, version_id=version.data["id"]
        ),
    )
    return definition_id, published.data["version"]["id"]


async def test_install_into_cabinet_uses_current_published_version():
    svc, repository, audit, _ = service()
    definition_id, version_id = await _serving_app(svc)

    receipt = await svc.install_instance(
        context=context(operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-1"),
        request=InstallInstanceRequest(
            definition_id=definition_id,
            scope_kind=AppScopeKind.CABINET,
            scope_id=CABINET_A,
            runtime_ref="pod:review-1",
        ),
    )

    assert receipt.data["app_version_id"] == version_id
    assert receipt.data["scope"] == {"kind": "cabinet", "id": CABINET_A}
    assert receipt.data["lifecycle"] == "published"
    assert receipt.data["runtime_ref"] == "pod:review-1"
    assert len(repository.instances) == 1
    installed = [item for item in audit.appended if "instance" in item.event_type]
    assert len(installed) == 1
    assert installed[0].cabinet_id == CABINET_A


async def test_install_into_project_records_cabinet_and_project():
    svc, _, audit, _ = service()
    definition_id, _ = await _serving_app(svc)

    receipt = await svc.install_instance(
        context=context(operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-1"),
        request=InstallInstanceRequest(
            definition_id=definition_id,
            scope_kind=AppScopeKind.PROJECT,
            scope_id=PROJECT_A,
        ),
    )

    assert receipt.data["scope"] == {"kind": "project", "id": PROJECT_A}
    installed = [item for item in audit.appended if "instance" in item.event_type]
    assert installed[0].cabinet_id == CABINET_A
    assert installed[0].project_id == PROJECT_A


async def test_install_into_foreign_cabinet_is_not_found():
    svc, repository, _, _ = service()
    definition_id, _ = await _serving_app(svc)

    with pytest.raises(Exception) as error:
        await svc.install_instance(
            context=context(
                operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-foreign"
            ),
            request=InstallInstanceRequest(
                definition_id=definition_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_B,
            ),
        )

    assert error.value.code == "NOT_FOUND"
    assert error.value.status == 404
    assert repository.instances == {}


async def test_install_into_archived_definition_is_rejected():
    svc, repository, _, _ = service()
    definition_id, _ = await _serving_app(svc)
    await svc.archive_definition(
        context=context(
            operation=DynamicAppOperation.ARCHIVE_DEFINITION, key="archive-1"
        ),
        request=ArchiveDefinitionRequest(definition_id=definition_id),
    )

    with pytest.raises(Exception) as error:
        await svc.install_instance(
            context=context(
                operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-1"
            ),
            request=InstallInstanceRequest(
                definition_id=definition_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
            ),
        )

    assert error.value.code == "APP_ARCHIVED"
    assert error.value.status == 409
    assert repository.instances == {}


async def test_install_before_publication_is_rejected():
    svc, repository, _, _ = service()
    created = await svc.create_definition(
        context=context(key="create-1"),
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
    )

    with pytest.raises(Exception) as error:
        await svc.install_instance(
            context=context(
                operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-1"
            ),
            request=InstallInstanceRequest(
                definition_id=created.data["id"],
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
            ),
        )

    assert error.value.code == "APP_NOT_PUBLISHED"
    assert error.value.status == 409
    assert repository.instances == {}


async def test_install_is_unique_per_definition_and_scope():
    svc, _, _, _ = service()
    definition_id, _ = await _serving_app(svc)
    request = InstallInstanceRequest(
        definition_id=definition_id,
        scope_kind=AppScopeKind.CABINET,
        scope_id=CABINET_A,
    )
    await svc.install_instance(
        context=context(operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-1"),
        request=request,
    )

    with pytest.raises(Exception) as error:
        await svc.install_instance(
            context=context(
                operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-2"
            ),
            request=request,
        )

    assert error.value.code == "INSTANCE_EXISTS"
    assert error.value.status == 409


async def test_install_of_superseded_version_is_rejected():
    svc, _, _, _ = service()
    definition_id, first_version_id = await _serving_app(svc)
    second = await svc.create_version(
        context=context(
            operation=DynamicAppOperation.CREATE_VERSION, key="version-2"
        ),
        definition_id=definition_id,
        request=CreateVersionRequest(
            manifest=manifest(version="1.1.0"), artifact_digest=DIGEST_2
        ),
    )
    await svc.publish_version(
        context=context(operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-2"),
        request=PublishVersionRequest(
            definition_id=definition_id,
            version_id=second.data["id"],
            expected_current_version_id=first_version_id,
        ),
    )

    with pytest.raises(Exception) as error:
        await svc.install_instance(
            context=context(
                operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-old"
            ),
            request=InstallInstanceRequest(
                definition_id=definition_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
                version_id=first_version_id,
            ),
        )

    assert error.value.code == "VERSION_NOT_PUBLISHED"
    assert error.value.status == 409


async def test_install_of_missing_version_is_not_found():
    svc, _, _, _ = service()
    definition_id, _ = await _serving_app(svc)

    with pytest.raises(Exception) as error:
        await svc.install_instance(
            context=context(
                operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-ghost"
            ),
            request=InstallInstanceRequest(
                definition_id=definition_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
                version_id="aver_missing",
            ),
        )

    assert error.value.code == "NOT_FOUND"
    assert error.value.status == 404


async def test_install_rejects_tenant_scope_instances():
    with pytest.raises(ValueError):
        InstallInstanceRequest(
            definition_id="app_x",
            scope_kind=AppScopeKind.TENANT,
            scope_id=TENANT_A,
        )


async def test_instances_are_listed_only_for_the_resolved_tenant():
    svc, _, _, _ = service()
    definition_id, _ = await _serving_app(svc)
    await svc.install_instance(
        context=context(operation=DynamicAppOperation.INSTALL_INSTANCE, key="install-1"),
        request=InstallInstanceRequest(
            definition_id=definition_id,
            scope_kind=AppScopeKind.CABINET,
            scope_id=CABINET_A,
        ),
    )

    page = await svc.list_instances(
        identity_tenant_id=TENANT_A, definition_id=definition_id
    )
    assert len(page["items"]) == 1
    assert page["items"][0]["scope"] == {"kind": "cabinet", "id": CABINET_A}

    with pytest.raises(Exception) as error:
        await svc.list_instances(
            identity_tenant_id=TENANT_B, definition_id=definition_id
        )
    assert error.value.code == "NOT_FOUND"
