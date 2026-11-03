"""Version upload/publish acceptance: immutable snapshots and forward-only lifecycle.

Authored for the migration freeze; not executed yet.
"""

from __future__ import annotations

import pytest

from prodavan.application.dynamic_apps_definition import (
    CreateDefinitionRequest,
    CreateVersionRequest,
    DynamicAppOperation,
    PublishVersionRequest,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus

from .fakes import DIGEST_1, DIGEST_2, TENANT_A, context, manifest, service


async def _draft_app(svc, *, app_id: str = "equipment-review"):
    created = await svc.create_definition(
        context=context(key=f"create-{app_id}"),
        request=CreateDefinitionRequest(app_id=app_id, display_name="Equipment review"),
    )
    return created.data["id"]


async def _upload(svc, definition_id: str, *, version: str = "1.0.0", digest: str = DIGEST_1, key=None):
    return await svc.create_version(
        context=context(
            operation=DynamicAppOperation.CREATE_VERSION,
            key=key or f"version-{version}",
        ),
        definition_id=definition_id,
        request=CreateVersionRequest(
            manifest=manifest(version=version), artifact_digest=digest
        ),
    )


async def test_upload_creates_immutable_draft_version():
    svc, repository, audit, _ = service()
    definition_id = await _draft_app(svc)

    receipt = await _upload(svc, definition_id)

    assert receipt.data["lifecycle"] == "draft"
    assert receipt.data["manifest_version"] == "1.0.0"
    assert receipt.data["artifact_digest"] == DIGEST_1
    assert receipt.data["published_at"] is None
    assert receipt.data["requested_capabilities"] == [
        {"name": "ui.render", "reason": "ui.render is needed for the review screen",
         "constraints": None, "optional": False},
        {"name": "platform.queries", "reason": "platform.queries is needed for the review screen",
         "constraints": None, "optional": False},
    ]
    stored = repository.versions[receipt.data["id"]]
    assert stored.status is AppLifecycleStatus.DRAFT
    # The audit payload never carries the raw manifest.
    created_audit = [item for item in audit.appended if item.event_type.endswith("version.created")]
    assert len(created_audit) == 1
    assert "manifest" not in created_audit[0].result


async def test_publish_sets_serving_pointer_and_published_at():
    svc, repository, audit, _ = service()
    definition_id = await _draft_app(svc)
    version = await _upload(svc, definition_id)

    receipt = await svc.publish_version(
        context=context(operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-1"),
        request=PublishVersionRequest(
            definition_id=definition_id, version_id=version.data["id"]
        ),
    )

    assert receipt.data["version"]["lifecycle"] == "published"
    assert receipt.data["version"]["published_at"] is not None
    assert receipt.data["definition"]["lifecycle"] == "published"
    assert receipt.data["definition"]["current_version_id"] == version.data["id"]
    assert repository.versions[version.data["id"]].status is AppLifecycleStatus.PUBLISHED
    assert repository.definitions[definition_id].status is AppLifecycleStatus.PUBLISHED
    events = [item.event_type for item in audit.appended]
    assert events.count("dynamic_app.version.published") == 1


async def test_publishing_an_already_published_version_is_rejected():
    svc, _, _, _ = service()
    definition_id = await _draft_app(svc)
    version = await _upload(svc, definition_id)
    await svc.publish_version(
        context=context(operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-1"),
        request=PublishVersionRequest(
            definition_id=definition_id, version_id=version.data["id"]
        ),
    )

    with pytest.raises(Exception) as error:
        await svc.publish_version(
            context=context(
                operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-2"
            ),
            request=PublishVersionRequest(
                definition_id=definition_id, version_id=version.data["id"]
            ),
        )

    assert error.value.code == "VERSION_ALREADY_PUBLISHED"
    assert error.value.status == 409


async def test_published_manifest_cannot_be_edited_in_place():
    """A published snapshot is immutable: re-uploading it is a conflict, not an update."""
    svc, repository, _, _ = service()
    definition_id = await _draft_app(svc)
    version = await _upload(svc, definition_id)
    await svc.publish_version(
        context=context(operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-1"),
        request=PublishVersionRequest(
            definition_id=definition_id, version_id=version.data["id"]
        ),
    )

    with pytest.raises(Exception) as error:
        await svc.create_version(
            context=context(
                operation=DynamicAppOperation.CREATE_VERSION, key="version-1-edited"
            ),
            definition_id=definition_id,
            request=CreateVersionRequest(
                manifest=manifest(version="1.0.0", display_name="Edited title"),
                artifact_digest=DIGEST_2,
            ),
        )

    assert error.value.code == "VERSION_EXISTS"
    assert error.value.status == 409
    stored = repository.versions[version.data["id"]]
    assert stored.manifest["displayName"] == "Equipment review"
    assert stored.artifact_digest == DIGEST_1
    assert stored.status is AppLifecycleStatus.PUBLISHED


async def test_second_published_version_moves_pointer_and_keeps_history_immutable():
    svc, repository, _, _ = service()
    definition_id = await _draft_app(svc)
    first = await _upload(svc, definition_id, version="1.0.0", digest=DIGEST_1)
    await svc.publish_version(
        context=context(operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-1"),
        request=PublishVersionRequest(
            definition_id=definition_id, version_id=first.data["id"]
        ),
    )
    second = await _upload(svc, definition_id, version="1.1.0", digest=DIGEST_2)

    receipt = await svc.publish_version(
        context=context(operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-2"),
        request=PublishVersionRequest(
            definition_id=definition_id,
            version_id=second.data["id"],
            expected_current_version_id=first.data["id"],
        ),
    )

    assert receipt.data["definition"]["current_version_id"] == second.data["id"]
    # The superseded snapshot keeps its bytes, digest and published timestamp.
    assert repository.versions[first.data["id"]].artifact_digest == DIGEST_1
    assert repository.versions[first.data["id"]].status is AppLifecycleStatus.PUBLISHED
    assert repository.versions[first.data["id"]].published_at is not None


async def test_stale_expected_current_version_conflicts():
    svc, _, _, _ = service()
    definition_id = await _draft_app(svc)
    version = await _upload(svc, definition_id)

    with pytest.raises(Exception) as error:
        await svc.publish_version(
            context=context(
                operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-1"
            ),
            request=PublishVersionRequest(
                definition_id=definition_id,
                version_id=version.data["id"],
                expected_current_version_id="aver_stale",
            ),
        )

    assert error.value.code == "STATE_CHANGED"
    assert error.value.status == 409


async def test_publish_missing_version_is_not_found():
    svc, _, _, _ = service()
    definition_id = await _draft_app(svc)

    with pytest.raises(Exception) as error:
        await svc.publish_version(
            context=context(
                operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-1"
            ),
            request=PublishVersionRequest(
                definition_id=definition_id, version_id="aver_does_not_exist"
            ),
        )

    assert error.value.code == "NOT_FOUND"
    assert error.value.status == 404


async def test_publish_version_from_another_definition_is_not_found():
    svc, _, _, _ = service()
    definition_id = await _draft_app(svc, app_id="equipment-review")
    other_id = await _draft_app(svc, app_id="offers-console")
    other_version = await _upload(svc, other_id, key="version-other")

    with pytest.raises(Exception) as error:
        await svc.publish_version(
            context=context(
                operation=DynamicAppOperation.PUBLISH_VERSION, key="publish-cross"
            ),
            request=PublishVersionRequest(
                definition_id=definition_id, version_id=other_version.data["id"]
            ),
        )

    assert error.value.code == "NOT_FOUND"
    assert error.value.status == 404


async def test_manifest_identity_mismatch_is_rejected():
    svc, repository, _, _ = service()
    definition_id = await _draft_app(svc, app_id="equipment-review")

    with pytest.raises(Exception) as error:
        await svc.create_version(
            context=context(
                operation=DynamicAppOperation.CREATE_VERSION, key="version-mismatch"
            ),
            definition_id=definition_id,
            request=CreateVersionRequest(
                manifest=manifest(app_id="another-app"), artifact_digest=DIGEST_1
            ),
        )

    assert error.value.code == "VALIDATION_FAILED"
    assert error.value.status == 422
    assert repository.versions == {}


@pytest.mark.parametrize(
    "broken",
    [
        {"protocol": "2.0"},
        {"version": "1.0"},
        {"entrypoints": {}},
        {"requestedCapabilities": "ui.render"},
        {"displayName": "  "},
    ],
)
async def test_malformed_manifest_is_rejected_before_write(broken):
    svc, repository, audit, _ = service()
    definition_id = await _draft_app(svc)
    payload = {**manifest(), **broken}

    with pytest.raises(Exception) as error:
        await svc.create_version(
            context=context(
                operation=DynamicAppOperation.CREATE_VERSION, key="version-bad"
            ),
            definition_id=definition_id,
            request=CreateVersionRequest(manifest=payload, artifact_digest=DIGEST_1),
        )

    assert error.value.code == "VALIDATION_FAILED"
    assert error.value.status == 422
    assert repository.versions == {}
    assert all("version." not in item.event_type for item in audit.appended)
