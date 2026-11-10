"""Regression tests for administrative publication approval and grant consumption."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.admin.contracts import CapabilitySet
from prodavan.domain.errors import AppError

from .fakes import COMPANY_A, DIGEST_A, PLUGIN_X, Harness


def _approval_args(capabilities: CapabilitySet) -> dict:
    return {
        "actor_id": "platform-admin",
        "company_id": COMPANY_A,
        "plugin_id": PLUGIN_X,
        "manifest_version": "1.2.0",
        "artifact_digest": DIGEST_A,
        "capabilities": capabilities,
    }


async def test_preapproval_needs_no_artifact_and_creates_no_grant_or_deployment() -> None:
    harness = Harness()
    capabilities = CapabilitySet(capabilities=("cache", "objects"))

    first = await harness.service().approve_publication(**_approval_args(capabilities))
    replay = await harness.service().approve_publication(**_approval_args(capabilities))

    assert first.created is True
    assert first.replayed is False
    assert first.approval.consumed_at is None
    assert first.approval.capability_set == capabilities
    assert not hasattr(first.approval, "status")
    assert replay.created is False
    assert replay.replayed is True
    assert replay.approval.approval_id == first.approval.approval_id
    assert len(harness.publication_approvals.records) == 1
    assert harness.artifact_registry.records == {}
    assert harness.grant_store.grants == {}
    assert harness.activation.deployments == {}


async def test_preapproval_exact_binding_mismatch_is_conflict() -> None:
    harness = Harness()
    service = harness.service()
    await service.approve_publication(
        **_approval_args(CapabilitySet(capabilities=("cache",)))
    )

    with pytest.raises(AppError) as caught:
        await service.approve_publication(
            **_approval_args(CapabilitySet(capabilities=("objects",)))
        )

    assert caught.value.code == "PUBLICATION_APPROVAL_BINDING_MISMATCH"
    assert caught.value.status == 409
    assert len(harness.publication_approvals.records) == 1


async def test_grant_before_published_is_denied_even_with_approval(
    harness: Harness, grant_args: dict
) -> None:
    harness.artifact_registry.records.clear()

    with pytest.raises(AppError) as caught:
        await harness.service().grant(**grant_args)

    assert caught.value.code == "ARTIFACT_NOT_PUBLISHED"
    assert harness.grant_store.grants == {}
    assert harness.activation.deployments == {}


async def test_grant_without_approval_is_denied(
    harness: Harness, grant_args: dict
) -> None:
    harness.publication_approvals.records.clear()

    with pytest.raises(AppError) as caught:
        await harness.service().grant(**grant_args)

    assert caught.value.code == "PUBLICATION_APPROVAL_REQUIRED"
    assert harness.grant_store.grants == {}
    assert harness.activation.deployments == {}


async def test_grant_with_stale_capabilities_is_denied(
    harness: Harness, grant_args: dict
) -> None:
    stale = dict(
        grant_args,
        capabilities=CapabilitySet(capabilities=("network",)),
    )

    with pytest.raises(AppError) as caught:
        await harness.service().grant(**stale)

    assert caught.value.code == "PUBLICATION_APPROVAL_BINDING_MISMATCH"
    assert harness.grant_store.grants == {}
    assert harness.activation.deployments == {}


async def test_published_grant_consumes_once_and_active_replay_does_not_duplicate(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    key = (COMPANY_A, PLUGIN_X, "1.2.0", DIGEST_A)
    consumed = harness.publication_approvals.records[key].consumed_at

    assert first.grant.is_active
    assert consumed is not None
    replay = await service.grant(**grant_args)

    assert replay.replayed is True
    assert replay.grant.grant_id == first.grant.grant_id
    assert len(harness.grant_store.grants) == 1
    assert harness.publication_approvals.records[key].consumed_at == consumed
    # Direct duplicate consumption is also idempotent and preserves first timestamp.
    stored = await harness.publication_approvals.mark_consumed(
        approval_id=harness.publication_approvals.records[key].approval_id,
        consumed_at=datetime(2030, 1, 1, tzinfo=UTC),
    )
    assert stored is not None
    assert stored.consumed_at == consumed


async def test_consumed_approval_cannot_create_another_initial_grant(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    await service.grant(**grant_args)
    harness.grant_store.grants.clear()

    with pytest.raises(AppError) as caught:
        await service.grant(**grant_args)

    assert caught.value.code == "PUBLICATION_APPROVAL_CONSUMED"
    assert harness.grant_store.grants == {}


async def test_legacy_active_grant_replay_does_not_require_retroactive_approval(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    harness.publication_approvals.records.clear()

    replay = await service.grant(**grant_args)

    assert replay.replayed is True
    assert replay.grant.grant_id == first.grant.grant_id
    assert len(harness.grant_store.grants) == 1
