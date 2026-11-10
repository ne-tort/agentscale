"""Durable per-instance webhook secret lifecycle tests (not run during freeze)."""

from __future__ import annotations

from dataclasses import replace
import logging
from types import SimpleNamespace

import pytest

from prodavan.application.plugins.admin.ports import webhook_secret_key
from prodavan.application.plugins.wiring.install import _webhook_secret_resolver

from .fakes import COMPANY_A, PLUGIN_X, Harness


async def test_grant_provisions_and_replay_resolves_same_ref_without_rotation(
    harness: Harness, grant_args: dict, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    service = harness.service()

    first = await service.grant(**grant_args)
    first_value = harness.webhook_secrets.value_for(first.grant)
    assert first.grant.artifact_digest == grant_args["artifact_digest"]
    assert first.grant.webhook_secret_ref is not None
    assert first.grant.webhook_secret_ref.startswith(("file://", "vault://"))
    assert len(first_value) >= 43  # token_urlsafe(48): 384 bits before encoding
    with pytest.raises(ValueError, match="webhook_secret_ref"):
        replace(first.grant, webhook_secret_ref=first_value)

    replay = await service.grant(**grant_args)

    assert replay.replayed is True
    assert replay.grant.webhook_secret_ref == first.grant.webhook_secret_ref
    assert harness.webhook_secrets.value_for(replay.grant) == first_value
    assert len(harness.webhook_secrets.put_keys) == 1
    assert harness.webhook_secrets.rotate_keys == []

    activation = [c for c in harness.activation.calls if c.method == "ensure_deployment"]
    assert activation
    assert all(c.kwargs["webhook_secret_ref"] == first.grant.webhook_secret_ref for c in activation)
    public_surfaces = [caplog.text, repr(first), repr(replay)]
    public_surfaces.extend(c.kwargs["webhook_path"] for c in activation if "webhook_path" in c.kwargs)
    for surface in public_surfaces:
        assert first_value not in surface


async def test_regrant_rotates_and_revoke_makes_secret_unresolvable(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    first = await service.grant(**grant_args)
    first_value = harness.webhook_secrets.value_for(first.grant)
    resolver = _webhook_secret_resolver(
        SimpleNamespace(grant_store=harness.grant_store), harness.webhook_secrets
    )
    assert await resolver.resolve(
        company_id=COMPANY_A,
        plugin_id=PLUGIN_X,
        plugin_instance_id=first.grant.plugin_instance_id,
        secret_ref=first.grant.webhook_secret_ref,
    ) == first_value

    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert harness.webhook_secrets.records == {}
    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=first.grant.plugin_instance_id,
            secret_ref=first.grant.webhook_secret_ref,
        )

    second = await service.grant(**grant_args)
    second_value = harness.webhook_secrets.value_for(second.grant)
    assert second_value != first_value
    assert len(harness.webhook_secrets.rotate_keys) == 1
    assert await resolver.resolve(
        company_id=COMPANY_A,
        plugin_id=PLUGIN_X,
        plugin_instance_id=second.grant.plugin_instance_id,
        secret_ref=second.grant.webhook_secret_ref,
    ) == second_value


async def test_active_legacy_grant_without_ref_fails_closed_without_fabrication(
    harness: Harness, grant_args: dict
) -> None:
    service = harness.service()
    receipt = await service.grant(**grant_args)
    initial_value = harness.webhook_secrets.value_for(receipt.grant)
    key = next(iter(harness.webhook_secrets.records))
    harness.grant_store.grants[(COMPANY_A, PLUGIN_X)] = replace(
        receipt.grant, webhook_secret_ref=None
    )
    put_count = len(harness.webhook_secrets.put_keys)

    with pytest.raises(RuntimeError, match="webhook_secret_ref"):
        await service.grant(**grant_args)

    assert len(harness.webhook_secrets.put_keys) == put_count
    assert harness.webhook_secrets.rotate_keys == []
    assert harness.webhook_secrets.records[key].value == initial_value
    resolver = _webhook_secret_resolver(
        SimpleNamespace(grant_store=harness.grant_store), harness.webhook_secrets
    )
    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=receipt.grant.plugin_instance_id,
            secret_ref=receipt.grant.webhook_secret_ref,
        )

    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    assert harness.webhook_secrets.records == {}
    assert len(harness.webhook_secrets.put_keys) == put_count
    assert harness.webhook_secrets.rotate_keys == []


async def test_active_grant_with_missing_secret_value_fails_closed(
    harness: Harness, grant_args: dict
) -> None:
    receipt = await harness.service().grant(**grant_args)
    harness.webhook_secrets.records.clear()

    with pytest.raises(RuntimeError, match="missing, foreign, or empty"):
        await harness.service().grant(**grant_args)

    assert receipt.grant.webhook_secret_ref is not None
    assert harness.webhook_secrets.rotate_keys == []


def _full_resolver(harness: Harness):
    return _webhook_secret_resolver(
        SimpleNamespace(
            grant_store=harness.grant_store,
            instance_store=harness.instance_store,
            publication_approvals=harness.publication_approvals,
        ),
        harness.webhook_secrets,
    )


async def test_missing_grant_instance_and_approval_never_fabricates_resolution(
    harness: Harness,
) -> None:
    instance_id = "inst_without_lifecycle_evidence"
    key = webhook_secret_key(
        company_id=COMPANY_A, plugin_id=PLUGIN_X, plugin_instance_id=instance_id
    )
    ref = await harness.webhook_secrets.put(key=key, value="unit-test-unapproved-secret")
    resolver = _full_resolver(harness)

    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=instance_id,
            secret_ref=ref,
        )


async def test_first_ever_grant_projection_uses_unconsumed_approval_without_active_grant(
    harness: Harness, grant_args: dict
) -> None:
    resolver = _full_resolver(harness)
    original = harness.activation.ensure_deployment
    observed: list[str] = []

    async def resolve_before_active_grant(**kwargs):
        assert harness.grant_store.grants == {}
        observed.append(await resolver.resolve(
            company_id=kwargs["company_id"],
            plugin_id=kwargs["plugin_id"],
            plugin_instance_id=kwargs["plugin_instance_id"],
            secret_ref=kwargs["webhook_secret_ref"],
        ))
        return await original(**kwargs)

    harness.activation.ensure_deployment = resolve_before_active_grant  # type: ignore[method-assign]
    receipt = await harness.service().grant(**grant_args)

    assert observed == [harness.webhook_secrets.value_for(receipt.grant)]
    assert receipt.grant.is_active
    assert receipt.grant.artifact_digest == grant_args["artifact_digest"]


@pytest.mark.parametrize("wrong", ("approval", "instance", "ref"))
async def test_first_grant_projection_denies_wrong_approval_instance_or_ref(
    harness: Harness, grant_args: dict, wrong: str
) -> None:
    resolver = _full_resolver(harness)
    original = harness.activation.ensure_deployment

    async def deny_invalid_projection(**kwargs):
        assert harness.grant_store.grants == {}
        if wrong == "approval":
            key = next(iter(harness.publication_approvals.records))
            approval = harness.publication_approvals.records[key]
            harness.publication_approvals.records[key] = SimpleNamespace(
                consumed_at=None,
                company_id=approval.company_id,
                plugin_id=approval.plugin_id,
                manifest_version=approval.manifest_version,
                artifact_digest=approval.artifact_digest,
            )
        elif wrong == "instance":
            key = (COMPANY_A, PLUGIN_X)
            instance = harness.instance_store.records[key]
            harness.instance_store.records[key] = replace(
                instance, plugin_instance_id="inst_wrong"
            )
        await resolver.resolve(
            company_id=kwargs["company_id"],
            plugin_id=kwargs["plugin_id"],
            plugin_instance_id=kwargs["plugin_instance_id"],
            secret_ref=(
                "file://wrong"
                if wrong == "ref"
                else kwargs["webhook_secret_ref"]
            ),
        )
        return await original(**kwargs)

    harness.activation.ensure_deployment = deny_invalid_projection  # type: ignore[method-assign]
    with pytest.raises(LookupError):
        await harness.service().grant(**grant_args)
    assert harness.grant_store.grants == {}
