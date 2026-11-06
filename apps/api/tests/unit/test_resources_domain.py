from datetime import UTC, datetime

from prodavan.application.resources import (
    BindingKind,
    ContentResource,
    ResourceBinding,
    ResourceKind,
    ResourceScope,
    ResourceVersion,
    ScopeKind,
    VersionStatus,
)


def test_resource_contract_carries_kind_and_owner_scope() -> None:
    resource = ContentResource(
        id="res_prompt",
        kind=ResourceKind.PROMPT,
        slug="support-agent",
        title="Support agent instructions",
        scope=ResourceScope(ScopeKind.COMPANY, "company_1"),
        metadata={"path": "AGENTS.md"},
    )

    assert resource.kind is ResourceKind.PROMPT
    assert resource.scope.kind is ScopeKind.COMPANY
    assert resource.active_version is None


def test_version_is_explicit_and_immutable_by_contract() -> None:
    version = ResourceVersion(
        id="rver_1",
        resource_id="res_file",
        number=2,
        status=VersionStatus.PUBLISHED,
        payload={"storage_key": "resources/res_file/2"},
        created_at=datetime.now(UTC),
    )

    assert version.number == 2
    assert version.status is VersionStatus.PUBLISHED


def test_binding_exposes_global_policy_and_chat_scope() -> None:
    binding = ResourceBinding(
        id="rbind_1",
        resource_id="res_mcp",
        target=ResourceScope(ScopeKind.PROJECT, "project_1"),
        binding=BindingKind.GLOBAL,
        child_may_edit=False,
        version_number=3,
        project_ids=("project_1",),
        session_id="session_1",
    )

    assert binding.binding is BindingKind.GLOBAL
    assert binding.child_may_edit is False
    assert binding.is_chat_scoped is True
