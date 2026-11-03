"""Definition CRUD acceptance: create, list, inspect, archive.

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
    IssueGrantRequest,
    PublishVersionRequest,
    GrantRequest,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus, AppScopeKind

from .fakes import (
    CABINET_A,
    DIGEST_1,
    TENANT_A,
    TENANT_B,
    context,
    manifest,
    service,
)


async def _published_app(svc, *, app_id: str = "equipment-review", tenant: str = TENANT_A):
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
    return definition_id, published


async def test_create_definition_is_draft_with_server_resolved_tenant():
    svc, repository, audit, _ = service()

    receipt = await svc.create_definition(
        context=context(tenant_id=TENANT_A, key="create-1"),
        request=CreateDefinitionRequest(
            app_id="equipment-review",
            display_name="Equipment review",
            description="Review offers per request line",
        ),
    )

    assert receipt.replayed is False
    assert receipt.data["lifecycle"] == "draft"
    assert receipt.data["tenant_id"] == TENANT_A
    assert receipt.data["scope"] == {"kind": "tenant", "id": TENANT_A}
    assert receipt.data["current_version_id"] is None
    assert repository.commits == 1
    stored = repository.definitions[receipt.data["id"]]
    assert stored.status is AppLifecycleStatus.DRAFT
    # Exactly one audit record, written in the same transaction as the definition.
    assert len(audit.appended) == 1
    assert audit.appended[0].event_type == "dynamic_app.definition.created"
    assert audit.appended[0].tenant_id == TENANT_A
    assert audit.appended[0].actor_id == "emp_admin"


async def test_duplicate_app_id_within_tenant_conflicts():
    svc, _, _, _ = service()
    request = CreateDefinitionRequest(app_id="equipment-review", display_name="One")
    await svc.create_definition(
        context=context(key="create-a"), request=request
    )

    with pytest.raises(Exception) as error:
        await svc.create_definition(context=context(key="create-b"), request=request)

    assert error.value.code == "APP_ID_TAKEN"
    assert error.value.status == 409


async def test_invalid_app_id_is_rejected_before_any_write():
    svc, repository, audit, _ = service()
    for bad in ("Equipment", "e", "equip_ment", "1equipment"):
        with pytest.raises(Exception) as error:
            await svc.create_definition(
                context=context(key=f"bad-{bad}"),
                request=CreateDefinitionRequest(app_id=bad, display_name="Bad"),
            )
        assert error.value.code == "VALIDATION_FAILED"
        assert error.value.status == 422
    assert repository.definitions == {}
    assert audit.appended == []


async def test_list_and_inspect_scope_to_resolved_tenant():
    svc, _, _, _ = service()
    definition_id, published = await _published_app(svc)
    await svc.create_definition(
        context=context(tenant_id=TENANT_B, key="create-beta"),
        request=CreateDefinitionRequest(app_id="beta-app", display_name="Beta"),
    )

    page = await svc.list_definitions(identity_tenant_id=TENANT_A)
    assert [item["app_id"] for item in page["items"]] == ["equipment-review"]
    assert page["has_more"] is False

    inspected = await svc.get_definition(
        identity_tenant_id=TENANT_A, app_id="equipment-review"
    )
    assert inspected["id"] == definition_id
    assert inspected["lifecycle"] == "published"
    assert inspected["current_version_id"] == published.data["version"]["id"]
    assert [item["manifest_version"] for item in inspected["versions"]] == ["1.0.0"]


async def test_unknown_app_is_not_found():
    svc, _, _, _ = service()
    with pytest.raises(Exception) as error:
        await svc.get_definition(identity_tenant_id=TENANT_A, app_id="ghost-app")
    assert error.value.code == "NOT_FOUND"
    assert error.value.status == 404


async def test_draft_definition_cannot_be_archived():
    """Only a published definition may archive; draft -> archived is not an edge."""
    svc, repository, _, _ = service()
    created = await svc.create_definition(
        context=context(key="create-1"),
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
    )

    with pytest.raises(Exception) as error:
        await svc.archive_definition(
            context=context(
                operation=DynamicAppOperation.ARCHIVE_DEFINITION, key="archive-1"
            ),
            request=ArchiveDefinitionRequest(definition_id=created.data["id"]),
        )

    assert error.value.code == "INVALID_LIFECYCLE_TRANSITION"
    assert error.value.status == 409
    assert repository.definitions[created.data["id"]].status is AppLifecycleStatus.DRAFT


async def test_archive_published_definition_maps_to_persisted_disabled():
    svc, repository, audit, _ = service()
    definition_id, _ = await _published_app(svc)

    receipt = await svc.archive_definition(
        context=context(
            operation=DynamicAppOperation.ARCHIVE_DEFINITION, key="archive-1"
        ),
        request=ArchiveDefinitionRequest(definition_id=definition_id, reason="retired"),
    )

    assert receipt.data["lifecycle"] == "archived"
    # The persisted enumeration has no ``archived``; ``disabled`` is its terminal stand-in.
    assert repository.definitions[definition_id].status is AppLifecycleStatus.DISABLED
    archived_audit = [item for item in audit.appended if "archived" in item.event_type]
    assert len(archived_audit) == 1
    assert archived_audit[0].reason == "retired"
    assert archived_audit[0].markers == {}


async def test_archive_is_terminal_and_cannot_reopen():
    svc, repository, _, _ = service()
    definition_id, published = await _published_app(svc)
    await svc.archive_definition(
        context=context(
            operation=DynamicAppOperation.ARCHIVE_DEFINITION, key="archive-1"
        ),
        request=ArchiveDefinitionRequest(definition_id=definition_id),
    )

    # Second archive attempt with a fresh key is an explicit conflict, not a no-op.
    with pytest.raises(Exception) as error:
        await svc.archive_definition(
            context=context(
                operation=DynamicAppOperation.ARCHIVE_DEFINITION, key="archive-2"
            ),
            request=ArchiveDefinitionRequest(definition_id=definition_id),
        )
    assert error.value.code == "ALREADY_ARCHIVED"

    # archived -> published must not exist: there is no un-archive operation, and a new
    # version cannot be published onto an archived definition.
    version_id = published.data["version"]["id"]
    with pytest.raises(Exception) as publish_error:
        await svc.publish_version(
            context=context(
                operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-again"
            ),
            request=PublishVersionRequest(
                definition_id=definition_id, version_id=version_id
            ),
        )
    assert publish_error.value.code in {
        "INVALID_LIFECYCLE_TRANSITION",
        "VERSION_ALREADY_PUBLISHED",
    }
    assert repository.definitions[definition_id].status is AppLifecycleStatus.DISABLED


async def test_archived_definition_refuses_new_instances_and_grants():
    svc, _, _, _ = service()
    definition_id, published = await _published_app(svc)
    version_id = published.data["version"]["id"]
    await svc.archive_definition(
        context=context(
            operation=DynamicAppOperation.ARCHIVE_DEFINITION, key="archive-1"
        ),
        request=ArchiveDefinitionRequest(definition_id=definition_id),
    )

    with pytest.raises(Exception) as install_error:
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
    assert install_error.value.code == "APP_ARCHIVED"
    assert install_error.value.status == 409

    with pytest.raises(Exception) as grant_error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
            request=IssueGrantRequest(
                definition_id=definition_id,
                version_id=version_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
                capabilities=(GrantRequest(name="ui.render"),),
            ),
        )
    assert grant_error.value.code == "APP_ARCHIVED"


async def test_archive_audit_is_written_only_once_per_key():
    svc, _, audit, _ = service()
    definition_id, _ = await _published_app(svc)
    ctx = context(operation=DynamicAppOperation.ARCHIVE_DEFINITION, key="archive-1")
    request = ArchiveDefinitionRequest(definition_id=definition_id)

    first = await svc.archive_definition(context=ctx, request=request)
    second = await svc.archive_definition(context=ctx, request=request)

    assert first.replayed is False
    assert second.replayed is True
    assert second.data == first.data
    archived = [item for item in audit.appended if "archived" in item.event_type]
    assert len(archived) == 1
