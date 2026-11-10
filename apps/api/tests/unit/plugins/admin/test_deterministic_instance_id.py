"""Deterministic initial ids and legacy instance preservation; not run in freeze."""

from __future__ import annotations

from dataclasses import replace

import pytest

from prodavan.application.plugins.admin.ports import webhook_secret_key
from prodavan.domain.plugins.helpers import plugin_instance_id_for

from .fakes import COMPANY_A, PLUGIN_X, Harness


@pytest.mark.asyncio
async def test_initial_grant_and_revoke_regrant_keep_precomputable_id(
    harness: Harness,
    grant_args: dict,
) -> None:
    service = harness.service()
    expected = plugin_instance_id_for(COMPANY_A, PLUGIN_X)

    first = await service.grant(**grant_args)
    assert first.plugin_instance_id == expected
    assert first.grant.plugin_instance_id == expected
    stored_instance = harness.instance_store.records[(COMPANY_A, PLUGIN_X)]
    assert stored_instance.plugin_instance_id == expected

    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    regrant = await service.grant(**grant_args)

    assert regrant.plugin_instance_id == expected
    assert regrant.grant.plugin_instance_id == expected
    assert regrant.grant.grant_id != first.grant.grant_id


@pytest.mark.asyncio
async def test_active_legacy_random_id_replay_is_not_rewritten(
    harness: Harness,
    grant_args: dict,
) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    legacy_id = "inst_legacy_random_0123456789abcdef"
    assert legacy_id != plugin_instance_id_for(COMPANY_A, PLUGIN_X)

    key = (COMPANY_A, PLUGIN_X)
    harness.grant_store.grants[key] = replace(
        first.grant,
        plugin_instance_id=legacy_id,
    )
    # Exercise repair of a missing instance projection from the authoritative
    # legacy grant, rather than replay of an already deterministic instance.
    harness.instance_store.records.clear()
    old_secret_key = webhook_secret_key(
        company_id=COMPANY_A,
        plugin_id=PLUGIN_X,
        plugin_instance_id=first.plugin_instance_id,
    )
    legacy_secret_key = webhook_secret_key(
        company_id=COMPANY_A,
        plugin_id=PLUGIN_X,
        plugin_instance_id=legacy_id,
    )
    harness.webhook_secrets.records[legacy_secret_key] = (
        harness.webhook_secrets.records.pop(old_secret_key)
    )
    harness.publication_approvals.records.clear()

    replay = await service.grant(**grant_args)

    assert replay.replayed is True
    assert replay.plugin_instance_id == legacy_id
    assert replay.grant.plugin_instance_id == legacy_id
    assert replay.grant.grant_id == first.grant.grant_id
    assert harness.instance_store.records[key].plugin_instance_id == legacy_id
