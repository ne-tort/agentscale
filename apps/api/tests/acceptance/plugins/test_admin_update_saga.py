"""Stateful no-I/O acceptance coverage for owner-aware plugin artifact updates.

The real ``PluginAdminService`` drives the existing stateful admin doubles. No
Kubernetes, database, network, live or production evidence is claimed here.
Tests are written during the verification freeze and are intentionally not run.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from prodavan.application.plugins.admin.contracts import (
    ArtifactPublicationStatus,
    CapabilitySet,
    PluginInstanceStatus,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.application.plugins.admin.service import PluginAdminService
from tests.unit.plugins.admin.fakes import (
    COMPANY_A,
    DIGEST_A,
    DIGEST_B,
    PLUGIN_X,
    Harness,
)

pytestmark = pytest.mark.asyncio

DIGEST_C = "sha256:" + "c" * 64
KEY = (COMPANY_A, PLUGIN_X)


def _approved_request(
    harness: Harness,
    capabilities: CapabilitySet,
    *,
    manifest_version: str,
    artifact_digest: str,
) -> dict[str, Any]:
    """Seed the exact published artifact and its independent approval."""
    harness.artifact_registry.seed(
        plugin_id=PLUGIN_X,
        manifest_version=manifest_version,
        artifact_digest=artifact_digest,
        status=ArtifactPublicationStatus.PUBLISHED,
    )
    harness.publication_approvals.seed(
        company_id=COMPANY_A,
        plugin_id=PLUGIN_X,
        manifest_version=manifest_version,
        artifact_digest=artifact_digest,
        capabilities=capabilities,
    )
    return {
        "company_id": COMPANY_A,
        "plugin_id": PLUGIN_X,
        "manifest_version": manifest_version,
        "artifact_digest": artifact_digest,
        "capabilities": capabilities,
    }


def _update_calls(harness: Harness) -> list[dict[str, Any]]:
    return [
        call.kwargs
        for call in harness.activation.calls
        if call.method == "update_deployment"
    ]


async def test_update_success_same_owner_retry_and_lock_release() -> None:
    harness = Harness()
    service = harness.service()
    capabilities = CapabilitySet(capabilities=("cache", "objects"))
    initial = _approved_request(
        harness, capabilities, manifest_version="1.2.0", artifact_digest=DIGEST_A
    )
    first = await service.grant(**initial)
    update = _approved_request(
        harness, capabilities, manifest_version="1.3.0", artifact_digest=DIGEST_B
    )

    updated = await service.grant(**update)

    assert updated.grant.grant_id == first.grant.grant_id
    assert updated.grant.plugin_instance_id == first.grant.plugin_instance_id
    assert updated.grant.artifact_digest == DIGEST_B
    assert updated.grant.webhook_secret_ref == first.grant.webhook_secret_ref
    assert harness.grant_store.grants[KEY].artifact_digest == DIGEST_B
    assert harness.instance_store.records[KEY].artifact_digest == DIGEST_B
    assert harness.instance_store.records[KEY].status is PluginInstanceStatus.ACTIVE
    binding = await harness.activation.runtime_binding(
        company_id=COMPANY_A, plugin_id=PLUGIN_X
    )
    assert binding.plugin_instance_id == first.grant.plugin_instance_id
    assert binding.artifact_digest == DIGEST_B
    assert binding.update_owner_token == "update-owner-1"
    assert [lease.owner_token for lease in harness.update_lock.released] == [
        "update-owner-1"
    ]
    assert harness.update_lock.held == {}
    assert harness.webhook_secrets.rotate_keys == []

    # Retry the exact service-produced PATCH after an uncertain caller response.
    # The stateful ActivationPort double recognizes the same digest+owner and
    # performs no second mutation or ownership transfer.
    patch = _update_calls(harness)[0]
    await harness.activation.update_deployment(**patch)
    retried = await harness.activation.runtime_binding(
        company_id=COMPANY_A, plugin_id=PLUGIN_X
    )
    assert retried == binding
    assert len(_update_calls(harness)) == 2
    assert len(harness.update_lock.released) == 1
    assert harness.instance_store.records[KEY].status is PluginInstanceStatus.ACTIVE

    # Initial installation and update each consumed only their exact approval.
    for version, digest in (("1.2.0", DIGEST_A), ("1.3.0", DIGEST_B)):
        approval = await harness.publication_approvals.get(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            manifest_version=version,
            artifact_digest=digest,
        )
        assert approval is not None and approval.consumed_at is not None


async def test_foreign_owner_conflict_skips_compensation_and_quarantine() -> None:
    harness = Harness()
    service = harness.service()
    capabilities = CapabilitySet(capabilities=("cache", "objects"))
    initial = _approved_request(
        harness, capabilities, manifest_version="1.2.0", artifact_digest=DIGEST_A
    )
    first = await service.grant(**initial)
    update = _approved_request(
        harness, capabilities, manifest_version="1.3.0", artifact_digest=DIGEST_B
    )

    async def inject_foreign_cas_winner(**_kwargs: Any) -> Any:
        old_grant = harness.grant_store.grants[KEY]
        old_instance = harness.instance_store.records[KEY]
        harness.grant_store.grants[KEY] = replace(
            old_grant, manifest_version="1.4.0", artifact_digest=DIGEST_C
        )
        harness.instance_store.records[KEY] = replace(
            old_instance,
            manifest_version="1.4.0",
            artifact_digest=DIGEST_C,
            status=PluginInstanceStatus.ACTIVE,
        )
        harness.activation.artifact_pins[KEY] = DIGEST_C
        harness.activation.update_owner_pins[KEY] = "foreign-update-owner"
        raise RuntimeError("injected old-state CAS loser")

    harness.grant_store.update_artifact_pin = inject_foreign_cas_winner  # type: ignore[method-assign]
    with pytest.raises(PluginAdminError) as caught:
        await service.grant(**update)

    assert caught.value.code == "PLUGIN_UPDATE_CONFLICT"
    # Only our forward B patch occurred. Compensation must not restore A over
    # the foreign C winner and must not quarantine that winner.
    assert [call["artifact_digest"] for call in _update_calls(harness)] == [DIGEST_B]
    assert harness.grant_store.grants[KEY].artifact_digest == DIGEST_C
    assert harness.instance_store.records[KEY].artifact_digest == DIGEST_C
    assert harness.instance_store.records[KEY].status is PluginInstanceStatus.ACTIVE
    foreign = await harness.activation.runtime_binding(
        company_id=COMPANY_A, plugin_id=PLUGIN_X
    )
    assert foreign.plugin_instance_id == first.grant.plugin_instance_id
    assert foreign.artifact_digest == DIGEST_C
    assert foreign.update_owner_token == "foreign-update-owner"
    assert len(harness.update_lock.released) == 1
    assert harness.update_lock.held == {}


async def test_first_update_rollback_restores_exact_old_binding_and_releases_lock() -> None:
    harness = Harness()
    service = harness.service()
    capabilities = CapabilitySet(capabilities=("cache", "objects"))
    initial = _approved_request(
        harness, capabilities, manifest_version="1.2.0", artifact_digest=DIGEST_A
    )
    first = await service.grant(**initial)
    update = _approved_request(
        harness, capabilities, manifest_version="1.3.0", artifact_digest=DIGEST_B
    )
    harness.grant_store.update_failures.append(RuntimeError("CAS unavailable"))

    with pytest.raises(PluginAdminError):
        await service.grant(**update)

    restored = await harness.activation.runtime_binding(
        company_id=COMPANY_A, plugin_id=PLUGIN_X
    )
    assert restored.plugin_instance_id == first.grant.plugin_instance_id
    assert restored.artifact_digest == DIGEST_A
    assert restored.update_owner_token is None
    assert harness.grant_store.grants[KEY].artifact_digest == DIGEST_A
    assert harness.instance_store.records[KEY].artifact_digest == DIGEST_A
    assert harness.instance_store.records[KEY].status is PluginInstanceStatus.ACTIVE
    patches = _update_calls(harness)
    assert [call["artifact_digest"] for call in patches] == [DIGEST_B, DIGEST_A]
    assert patches[1]["expected_current_artifact_digest"] == DIGEST_B
    assert patches[1]["update_owner_token"] is None
    assert patches[1]["expected_current_update_owner_token"] == "update-owner-1"
    assert [lease.owner_token for lease in harness.update_lock.released] == [
        "update-owner-1"
    ]
    assert harness.update_lock.held == {}


async def test_update_without_lock_fails_closed_before_runtime_or_pin_mutation() -> None:
    harness = Harness()
    capabilities = CapabilitySet(capabilities=("cache", "objects"))
    initial = _approved_request(
        harness, capabilities, manifest_version="1.2.0", artifact_digest=DIGEST_A
    )
    first = await harness.service().grant(**initial)
    update = _approved_request(
        harness, capabilities, manifest_version="1.3.0", artifact_digest=DIGEST_B
    )
    service = PluginAdminService(replace(harness.ports(), update_lock=None))

    with pytest.raises(RuntimeError, match="update lock is not wired"):
        await service.grant(**update)

    assert _update_calls(harness) == []
    assert not any(
        call.method == "update_artifact_pin" for call in harness.grant_store.calls
    )
    assert harness.grant_store.grants[KEY].grant_id == first.grant.grant_id
    assert harness.grant_store.grants[KEY].artifact_digest == DIGEST_A
    assert harness.instance_store.records[KEY].artifact_digest == DIGEST_A
    assert harness.activation.artifact_pins[KEY] == DIGEST_A
    assert harness.activation.update_owner_pins[KEY] is None
    assert harness.instance_store.records[KEY].status is PluginInstanceStatus.ACTIVE
