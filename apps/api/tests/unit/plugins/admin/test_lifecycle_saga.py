"""Lifecycle saga regressions: failure injection, retry, replay, forced revoke."""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.plugins.admin.contracts import (
    REGRANT_ID_CONFLICT_CODE,
    GrantStatus,
    PluginInstanceStatus,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.admin.service import PluginAdminService
from prodavan.domain.errors import AppError

from .fakes import COMPANY_A, PLUGIN_X, Harness


async def test_failed_grant_resumes_from_persistent_instance_without_rotation(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    original = harness.activation.ensure_deployment
    first = True

    async def fail_once(**kwargs):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("deployment unavailable")
        return await original(**kwargs)

    harness.activation.ensure_deployment = fail_once  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="deployment unavailable"):
        await service.grant(**grant_args)

    instance = await harness.instance_store.get(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert instance is not None
    assert instance.status is PluginInstanceStatus.INSTALLED
    assert harness.grant_store.grants == {}

    receipt = await service.grant(**grant_args)
    assert receipt.plugin_instance_id == instance.plugin_instance_id
    assert receipt.grant.status is GrantStatus.ACTIVE
    assert len(harness.instance_store.records) == 1
    assert not any(c.method == "rotate_plugin_schema" for c in harness.tenant_db.calls)


async def test_grant_is_created_only_after_deployment_and_installed_instance(
    harness: Harness, grant_args: dict
) -> None:
    original = harness.grant_store.create
    observed: list[PluginInstanceStatus] = []

    async def create_with_assertion(**kwargs):
        key = (COMPANY_A, PLUGIN_X)
        assert key in harness.activation.deployments
        instance = await harness.instance_store.get(company_id=COMPANY_A, plugin_id=PLUGIN_X)
        assert instance is not None
        observed.append(instance.status)
        return await original(**kwargs)

    harness.grant_store.create = create_with_assertion  # type: ignore[method-assign]
    await harness.service().grant(**grant_args)
    assert observed == [PluginInstanceStatus.INSTALLED]


async def test_active_replay_reensures_deployment_and_routes_without_password_rotation(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    harness.activation.deployments.clear()
    harness.routes.registered.clear()

    replay = await service.grant(**grant_args)

    assert replay.replayed is True
    assert replay.grant.grant_id == first.grant.grant_id
    assert len(harness.activation.deployments) == 1
    assert len(harness.routes.registered) == 1
    assert sum(c.method == "ensure_deployment" for c in harness.activation.calls) == 2
    assert not any(c.method == "rotate_plugin_schema" for c in harness.tenant_db.calls)
    assert sum(c.method == "create" for c in harness.grant_store.calls) == 1


async def test_regrant_after_revoke_rotates_once_and_reuses_instance(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)

    second = await service.grant(**grant_args)

    assert second.grant.grant_id != first.grant.grant_id
    assert second.grant.plugin_instance_id == first.grant.plugin_instance_id
    assert second.grant.status is GrantStatus.ACTIVE
    assert first.grant.grant_id in harness.revocation.revoked
    assert sum(c.method == "rotate_plugin_schema" for c in harness.tenant_db.calls) == 1
    assert sum(c.method == "regrant" for c in harness.grant_store.calls) == 1


async def test_regrant_same_id_from_id_factory_conflicts_before_any_saga_mutation(
    harness: Harness, grant_args: dict
) -> None:
    service = PluginAdminService(harness.ports(), id_factory=lambda: "repeated")
    first = await service.grant(**grant_args)
    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    key = (COMPANY_A, PLUGIN_X)
    revoked_before = harness.grant_store.grants[key]
    assert revoked_before.status is GrantStatus.REVOKED
    assert revoked_before.grant_id == first.grant.grant_id == "grant_repeated"

    mutation_counts_before = {
        "webhook_put": len(harness.webhook_secrets.put_keys),
        "webhook_rotate": len(harness.webhook_secrets.rotate_keys),
        "webhook_delete": len(harness.webhook_secrets.deleted_refs),
        "tenant_db": len(harness.tenant_db.calls),
        "objects": len(harness.object_store.calls),
        "activation": len(harness.activation.calls),
        "routes": len(harness.routes.calls),
        "mint": len(harness.mint.calls),
        "audit": len(harness.audit.events),
        "revocation": len(harness.revocation.calls),
        "grant_writes": sum(
            call.method in {"create", "regrant", "deactivate", "delete"}
            for call in harness.grant_store.calls
        ),
        "instance_writes": sum(
            call.method in {"create", "update_status", "delete"}
            for call in harness.instance_store.calls
        ),
        "approval_consumption": sum(
            call.method == "mark_consumed"
            for call in harness.publication_approvals.calls
        ),
    }

    with pytest.raises(AppError) as caught:
        await service.grant(**grant_args)

    assert caught.value.code == REGRANT_ID_CONFLICT_CODE
    assert caught.value.status == 409
    assert caught.value.extra["previous_grant_id"] == first.grant.grant_id
    assert caught.value.extra["candidate_grant_id"] == first.grant.grant_id
    mutation_counts_after = {
        "webhook_put": len(harness.webhook_secrets.put_keys),
        "webhook_rotate": len(harness.webhook_secrets.rotate_keys),
        "webhook_delete": len(harness.webhook_secrets.deleted_refs),
        "tenant_db": len(harness.tenant_db.calls),
        "objects": len(harness.object_store.calls),
        "activation": len(harness.activation.calls),
        "routes": len(harness.routes.calls),
        "mint": len(harness.mint.calls),
        "audit": len(harness.audit.events),
        "revocation": len(harness.revocation.calls),
        "grant_writes": sum(
            call.method in {"create", "regrant", "deactivate", "delete"}
            for call in harness.grant_store.calls
        ),
        "instance_writes": sum(
            call.method in {"create", "update_status", "delete"}
            for call in harness.instance_store.calls
        ),
        "approval_consumption": sum(
            call.method == "mark_consumed"
            for call in harness.publication_approvals.calls
        ),
    }
    assert mutation_counts_after == mutation_counts_before
    assert harness.grant_store.grants[key] == revoked_before
    assert first.grant.grant_id in harness.revocation.revoked
    assert not any(
        call.method == "regrant" for call in harness.grant_store.calls
    )


async def test_regrant_rejects_returned_old_id_without_accepting_resurrection(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    key = (COMPANY_A, PLUGIN_X)
    revoked_before = harness.grant_store.grants[key]
    instance_writes_before = sum(
        call.method == "update_status" for call in harness.instance_store.calls
    )
    routes_before = len(harness.routes.calls)
    mint_before = len(harness.mint.calls)

    async def return_old_id(**kwargs):
        return replace(
            kwargs["grant"], grant_id=kwargs["previous_grant_id"]
        )

    harness.grant_store.regrant = return_old_id  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="returned mismatched grant binding"):
        await service.grant(**grant_args)

    assert harness.grant_store.grants[key] == revoked_before
    assert first.grant.grant_id in harness.revocation.revoked
    # Re-grant rotation сначала оставляет durable repair marker, затем возвращает
    # INSTALLED; ACTIVE не записывается после подменённого ответа regrant store.
    assert sum(
        call.method == "update_status" for call in harness.instance_store.calls
    ) == instance_writes_before + 2
    assert harness.instance_store.records[key].status is PluginInstanceStatus.INSTALLED
    assert len(harness.routes.calls) == routes_before
    assert len(harness.mint.calls) == mint_before


async def test_failed_rotation_leaves_quarantined_marker_and_retries_rotation(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    await service.grant(**grant_args)
    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    original = harness.tenant_db.rotate_plugin_schema
    first = True

    async def fail_once(**kwargs):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("failure after password alter before secret rotate")
        return await original(**kwargs)

    harness.tenant_db.rotate_plugin_schema = fail_once  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="secret rotate"):
        await service.grant(**grant_args)
    assert harness.instance_store.records[(COMPANY_A, PLUGIN_X)].status is (
        PluginInstanceStatus.QUARANTINED
    )

    repaired = await service.grant(**grant_args)
    assert repaired.grant.status is GrantStatus.ACTIVE
    assert sum(c.method == "rotate_plugin_schema" for c in harness.tenant_db.calls) == 1


async def test_revoke_retry_after_grant_already_revoked_finishes_steps(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    await service.grant(**grant_args)
    original = harness.tenant_db.revoke_plugin
    first = True

    async def fail_once(**kwargs):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("tenant db unavailable")
        return await original(**kwargs)

    harness.tenant_db.revoke_plugin = fail_once  # type: ignore[method-assign]
    with pytest.raises(PluginAdminError):
        await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert harness.grant_store.grants[(COMPANY_A, PLUGIN_X)].status is GrantStatus.REVOKED

    replay = await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert replay.replayed is True
    assert sum(c.method == "revoke_plugin" for c in harness.tenant_db.calls) == 1
    assert sum(c.method == "suspend" for c in harness.activation.calls) == 2
    assert harness.instance_store.records[(COMPANY_A, PLUGIN_X)].status is PluginInstanceStatus.REVOKED


async def test_revoke_does_not_accept_unconfirmed_forced_suspend(
    harness: Harness, grant_args: dict
) -> None:
    await harness.service().grant(**grant_args)

    async def not_scaled(**kwargs) -> bool:
        return False

    harness.activation.suspend = not_scaled  # type: ignore[method-assign]
    with pytest.raises(PluginAdminError) as caught:
        await harness.service().revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert "forced_suspend" in caught.value.detail


async def test_uninstall_retry_keeps_lifecycle_records_until_cleanup(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    await service.grant(**grant_args)
    original = harness.object_store.drop_prefix
    first = True

    async def fail_once(**kwargs):
        nonlocal first
        if first:
            first = False
            raise RuntimeError("object store unavailable")
        return await original(**kwargs)

    harness.object_store.drop_prefix = fail_once  # type: ignore[method-assign]
    with pytest.raises(PluginAdminError):
        await service.uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert (COMPANY_A, PLUGIN_X) in harness.grant_store.grants
    assert (COMPANY_A, PLUGIN_X) in harness.instance_store.records

    finished = await service.uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert finished.deleted is True
    assert finished.already_absent is False
    assert harness.grant_store.grants == {}
    assert harness.instance_store.records == {}

    repeated = await service.uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert repeated.already_absent is True


async def test_uninstall_cleans_instance_left_by_failed_grant(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()

    async def fail_deployment(**kwargs) -> str:
        raise RuntimeError("deployment unavailable")

    harness.activation.ensure_deployment = fail_deployment  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        await service.grant(**grant_args)
    assert harness.grant_store.grants == {}
    assert len(harness.instance_store.records) == 1

    result = await service.uninstall(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert result.deleted is True
    assert result.already_absent is False
    assert harness.instance_store.records == {}
    assert harness.artifact_registry.unlinked
