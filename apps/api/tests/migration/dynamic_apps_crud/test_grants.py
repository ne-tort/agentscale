"""Grant issue/revoke acceptance: requested-vs-granted separation, deny-on-revoke.

Authored for the migration freeze; not executed yet.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.dynamic_apps_definition import (
    CreateDefinitionRequest,
    CreateVersionRequest,
    DynamicAppOperation,
    GrantRequest,
    IssueGrantRequest,
    PublishVersionRequest,
    RevokeGrantRequest,
)
from prodavan.domain.dynamic_apps import AppScopeKind, CapabilityDecision

from .fakes import (
    CABINET_A,
    CABINET_B,
    DIGEST_1,
    TENANT_A,
    context,
    manifest,
    service,
)


async def _serving_app(svc, *, app_id: str = "equipment-review"):
    created = await svc.create_definition(
        context=context(key=f"create-{app_id}"),
        request=CreateDefinitionRequest(app_id=app_id, display_name="Equipment review"),
    )
    definition_id = created.data["id"]
    version = await svc.create_version(
        context=context(
            operation=DynamicAppOperation.CREATE_VERSION, key=f"version-{app_id}"
        ),
        definition_id=definition_id,
        request=CreateVersionRequest(
            manifest=manifest(app_id=app_id), artifact_digest=DIGEST_1
        ),
    )
    published = await svc.publish_version(
        context=context(
            operation=DynamicAppOperation.PUBLISH_VERSION, key=f"publish-{app_id}"
        ),
        request=PublishVersionRequest(
            definition_id=definition_id, version_id=version.data["id"]
        ),
    )
    return definition_id, published.data["version"]["id"]


def _issue(definition_id, version_id, *, capabilities, key="grant-1", **kwargs):
    return IssueGrantRequest(
        definition_id=definition_id,
        version_id=version_id,
        scope_kind=kwargs.pop("scope_kind", AppScopeKind.CABINET),
        scope_id=kwargs.pop("scope_id", CABINET_A),
        capabilities=tuple(GrantRequest(name=name) for name in capabilities),
        **kwargs,
    )


async def test_issue_grant_records_only_requested_capabilities():
    svc, repository, audit, _ = service()
    definition_id, version_id = await _serving_app(svc)

    receipt = await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
        request=_issue(definition_id, version_id, capabilities=["ui.render"]),
    )

    assert receipt.data["scope"] == {"kind": "cabinet", "id": CABINET_A}
    assert [item["name"] for item in receipt.data["capabilities"]] == ["ui.render"]
    assert receipt.data["capabilities"][0]["decision"] == "granted"
    assert len(repository.grants) == 1
    issued = [item for item in audit.appended if "grant.issued" in item.event_type]
    assert len(issued) == 1
    assert issued[0].actor_id == "emp_admin"


async def test_grant_for_unrequested_capability_is_rejected():
    svc, repository, _, _ = service()
    definition_id, version_id = await _serving_app(svc)

    with pytest.raises(Exception) as error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
            request=_issue(definition_id, version_id, capabilities=["secrets.read"]),
        )

    assert error.value.code == "CAPABILITY_NOT_REQUESTED"
    assert error.value.status == 409
    assert repository.grants == {}


async def test_grant_unknown_capability_name_is_validation_error():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)

    with pytest.raises(Exception) as error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
            request=_issue(definition_id, version_id, capabilities=["totally.unknown"]),
        )

    assert error.value.code == "VALIDATION_FAILED"
    assert error.value.status == 422


async def test_grant_on_draft_version_is_rejected():
    svc, _, _, _ = service()
    created = await svc.create_definition(
        context=context(key="create-1"),
        request=CreateDefinitionRequest(app_id="equipment-review", display_name="Eq"),
    )
    definition_id = created.data["id"]
    version = await svc.create_version(
        context=context(operation=DynamicAppOperation.CREATE_VERSION, key="version-1"),
        definition_id=definition_id,
        request=CreateVersionRequest(manifest=manifest(), artifact_digest=DIGEST_1),
    )

    with pytest.raises(Exception) as error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
            request=_issue(definition_id, version.data["id"], capabilities=["ui.render"]),
        )

    assert error.value.code == "APP_NOT_PUBLISHED"
    assert error.value.status == 409


async def test_grant_on_draft_version_after_publication_of_another_version():
    """A superseded draft version cannot become a grant target either."""
    svc, _, _, _ = service()
    definition_id, _ = await _serving_app(svc)
    draft = await svc.create_version(
        context=context(operation=DynamicAppOperation.CREATE_VERSION, key="version-2"),
        definition_id=definition_id,
        request=CreateVersionRequest(
            manifest=manifest(version="1.1.0"),
            artifact_digest="sha256:" + "3" * 64,
        ),
    )

    with pytest.raises(Exception) as error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-draft"),
            request=_issue(definition_id, draft.data["id"], capabilities=["ui.render"]),
        )

    assert error.value.code == "VERSION_NOT_PUBLISHED"
    assert error.value.status == 409


async def test_grant_expiry_in_the_past_is_rejected():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)
    past = datetime.now(UTC) - timedelta(hours=1)

    with pytest.raises(Exception) as error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
            request=IssueGrantRequest(
                definition_id=definition_id,
                version_id=version_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
                capabilities=(GrantRequest(name="ui.render", expires_at=past),),
                expires_at=past,
            ),
        )

    assert error.value.code == "VALIDATION_FAILED"
    assert error.value.status == 422


async def test_grant_for_foreign_scope_is_not_found():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)

    with pytest.raises(Exception) as error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
            request=_issue(
                definition_id,
                version_id,
                capabilities=["ui.render"],
                scope_id=CABINET_B,
            ),
        )

    assert error.value.code == "NOT_FOUND"
    assert error.value.status == 404


async def test_revoke_turns_decisions_into_deny_without_deleting_the_row():
    svc, repository, audit, _ = service()
    definition_id, version_id = await _serving_app(svc)
    await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
        request=_issue(definition_id, version_id, capabilities=["ui.render", "platform.queries"]),
    )

    receipt = await svc.revoke_grant(
        context=context(operation=DynamicAppOperation.REVOKE_GRANT, key="revoke-1"),
        request=RevokeGrantRequest(
            definition_id=definition_id,
            version_id=version_id,
            scope_kind=AppScopeKind.CABINET,
            scope_id=CABINET_A,
            capability="ui.render",
            reason="tenant withdrew consent",
        ),
    )

    decisions = {item["name"]: item["decision"] for item in receipt.data["capabilities"]}
    assert decisions == {"ui.render": "denied", "platform.queries": "granted"}
    assert receipt.data["revoked_capability"] == "ui.render"
    # The grant row is retained as authorization evidence.
    stored = next(iter(repository.grants.values()))
    assert {item.name.value for item in stored.grants} == {"ui.render", "platform.queries"}
    revoked = [item for item in audit.appended if "grant.revoked" in item.event_type]
    assert len(revoked) == 1
    assert revoked[0].reason == "tenant withdrew consent"
    assert revoked[0].markers["revoked"] == "true"


async def test_revoke_twice_conflicts():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)
    await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
        request=_issue(definition_id, version_id, capabilities=["ui.render"]),
    )
    request = RevokeGrantRequest(
        definition_id=definition_id,
        version_id=version_id,
        scope_kind=AppScopeKind.CABINET,
        scope_id=CABINET_A,
        capability="ui.render",
        reason="withdrawn",
    )
    await svc.revoke_grant(
        context=context(operation=DynamicAppOperation.REVOKE_GRANT, key="revoke-1"),
        request=request,
    )

    with pytest.raises(Exception) as error:
        await svc.revoke_grant(
            context=context(operation=DynamicAppOperation.REVOKE_GRANT, key="revoke-2"),
            request=request,
        )

    assert error.value.code == "GRANT_ALREADY_REVOKED"
    assert error.value.status == 409


async def test_revoke_of_never_granted_capability_is_not_found():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)

    with pytest.raises(Exception) as error:
        await svc.revoke_grant(
            context=context(operation=DynamicAppOperation.REVOKE_GRANT, key="revoke-1"),
            request=RevokeGrantRequest(
                definition_id=definition_id,
                version_id=version_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
                capability="ui.render",
                reason="withdrawn",
            ),
        )

    assert error.value.code == "NOT_FOUND"
    assert error.value.status == 404


async def test_revoke_requires_a_reason():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)
    await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
        request=_issue(definition_id, version_id, capabilities=["ui.render"]),
    )

    with pytest.raises(Exception) as error:
        await svc.revoke_grant(
            context=context(operation=DynamicAppOperation.REVOKE_GRANT, key="revoke-1"),
            request=RevokeGrantRequest(
                definition_id=definition_id,
                version_id=version_id,
                scope_kind=AppScopeKind.CABINET,
                scope_id=CABINET_A,
                capability="ui.render",
                reason="   ",
            ),
        )

    assert error.value.code == "VALIDATION_FAILED"
    assert error.value.status == 422


async def test_reissue_of_an_existing_capability_conflicts():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)
    await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
        request=_issue(definition_id, version_id, capabilities=["ui.render"]),
    )

    with pytest.raises(Exception) as error:
        await svc.issue_grant(
            context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-2"),
            request=_issue(definition_id, version_id, capabilities=["ui.render"]),
        )

    assert error.value.code == "STATE_CHANGED"
    assert error.value.status == 409


async def test_additive_grant_keeps_prior_decisions():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)
    await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
        request=_issue(definition_id, version_id, capabilities=["ui.render"]),
    )

    receipt = await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-2"),
        request=_issue(definition_id, version_id, capabilities=["platform.queries"]),
    )

    names = [item["name"] for item in receipt.data["capabilities"]]
    assert names == ["ui.render", "platform.queries"]


async def test_grants_are_listed_per_version_under_the_resolved_tenant():
    svc, _, _, _ = service()
    definition_id, version_id = await _serving_app(svc)
    await svc.issue_grant(
        context=context(operation=DynamicAppOperation.ISSUE_GRANT, key="grant-1"),
        request=_issue(definition_id, version_id, capabilities=["ui.render"]),
    )

    page = await svc.list_grants(
        identity_tenant_id=TENANT_A,
        definition_id=definition_id,
        version_id=version_id,
    )
    assert len(page["items"]) == 1
    assert page["items"][0]["capabilities"][0]["decision"] == CapabilityDecision.GRANTED.value
