from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest

from prodavan.application.plugins.admin.contracts import PluginInstanceStatus
from prodavan.application.plugins.admin.ports import webhook_secret_key
from prodavan.application.plugins.wiring.install import _webhook_secret_resolver

from .fakes import COMPANY_A, PLUGIN_X


def _full_resolver(harness):
    return _webhook_secret_resolver(
        SimpleNamespace(
            grant_store=harness.grant_store,
            instance_store=harness.instance_store,
            publication_approvals=harness.publication_approvals,
            artifact_registry=harness.artifact_registry,
        ),
        harness.webhook_secrets,
    )


async def _revoked_regrant_projection(harness, grant_args):
    service = harness.service()
    await service.grant(**grant_args)
    await service.revoke(company_id=COMPANY_A, plugin_id=PLUGIN_X)
    record = harness.grant_store.grants[(COMPANY_A, PLUGIN_X)]
    instance = harness.instance_store.records[(COMPANY_A, PLUGIN_X)]
    approval = await harness.publication_approvals.get(
        company_id=COMPANY_A,
        plugin_id=PLUGIN_X,
        manifest_version=instance.manifest_version,
        artifact_digest=instance.artifact_digest,
    )
    assert approval is not None
    secret_ref = await harness.webhook_secrets.put(
        key=webhook_secret_key(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=instance.plugin_instance_id,
        ),
        value="regrant-secret-test-only",
    )
    return record, instance, approval, secret_ref, _full_resolver(harness)


@pytest.mark.asyncio
async def test_regrant_projection_allows_revoked_records_with_exact_consumed_approval(
    harness, grant_args
):
    record, instance, approval, secret_ref, resolver = (
        await _revoked_regrant_projection(harness, grant_args)
    )
    assert record.is_active is False
    assert record.revoked_at is not None
    assert instance.status.value == "revoked"
    assert approval.consumed_at is not None
    assert (
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=instance.plugin_instance_id,
            secret_ref=secret_ref,
        )
        == "regrant-secret-test-only"
    )


@pytest.mark.asyncio
async def test_regrant_projection_allows_post_rotation_installed_instance(
    harness, grant_args
):
    _, instance, _, secret_ref, resolver = await _revoked_regrant_projection(
        harness, grant_args
    )
    instance = replace(instance, status=PluginInstanceStatus.INSTALLED)
    harness.instance_store.records[(COMPANY_A, PLUGIN_X)] = instance
    assert (
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=instance.plugin_instance_id,
            secret_ref=secret_ref,
        )
        == "regrant-secret-test-only"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status",
    [
        PluginInstanceStatus.QUARANTINED,
        PluginInstanceStatus.ACTIVE,
        PluginInstanceStatus.SUSPENDED,
    ],
)
async def test_regrant_projection_denies_non_regrant_instance_status(
    harness, grant_args, status
):
    _, instance, _, secret_ref, resolver = await _revoked_regrant_projection(
        harness, grant_args
    )
    harness.instance_store.records[(COMPANY_A, PLUGIN_X)] = replace(
        instance, status=status
    )
    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=instance.plugin_instance_id,
            secret_ref=secret_ref,
        )


@pytest.mark.asyncio
async def test_regrant_projection_denies_unconsumed_approval(harness, grant_args):
    _, instance, approval, secret_ref, resolver = await _revoked_regrant_projection(
        harness, grant_args
    )
    key = (
        COMPANY_A,
        PLUGIN_X,
        instance.manifest_version,
        instance.artifact_digest,
    )
    harness.publication_approvals.records[key] = replace(approval, consumed_at=None)
    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=instance.plugin_instance_id,
            secret_ref=secret_ref,
        )


@pytest.mark.asyncio
async def test_revoked_projection_without_consumed_approval_is_denied(
    harness, grant_args
):
    _, instance, _, secret_ref, resolver = await _revoked_regrant_projection(
        harness, grant_args
    )
    harness.publication_approvals.records.clear()
    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=instance.plugin_instance_id,
            secret_ref=secret_ref,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "foreign",
    [
        "grant_company",
        "grant_plugin",
        "grant_instance",
        "instance_company",
        "instance_plugin",
        "instance_instance",
        "secret_ref",
    ],
)
async def test_regrant_projection_denies_foreign_scope_or_secret(
    harness, grant_args, foreign: str
):
    record, instance, _, secret_ref, resolver = await _revoked_regrant_projection(
        harness, grant_args
    )
    if foreign.startswith("grant_"):
        field = foreign.removeprefix("grant_")
        field_name = "plugin_instance_id" if field == "instance" else f"{field}_id"
        value = "inst_foreign" if field == "instance" else f"{field}_foreign"
        record = replace(record, **{field_name: value})
        harness.grant_store.grants[(COMPANY_A, PLUGIN_X)] = record
    elif foreign.startswith("instance_"):
        field = foreign.removeprefix("instance_")
        field_name = "plugin_instance_id" if field == "instance" else f"{field}_id"
        value = "inst_foreign" if field == "instance" else f"{field}_foreign"
        instance = replace(instance, **{field_name: value})
        harness.instance_store.records[(COMPANY_A, PLUGIN_X)] = instance
    else:
        secret_ref = "file://ai_keys/foreign.secret"
    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=record.plugin_instance_id,
            secret_ref=secret_ref,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "mismatched_value"),
    [
        ("manifest_version", "0.0.0-foreign"),
        ("artifact_digest", "sha256:" + "f" * 64),
    ],
)
async def test_regrant_projection_denies_instance_artifact_binding_mismatch(
    harness, grant_args, field: str, mismatched_value: str
):
    """Instance pin must match the consumed approval, not merely its presence."""
    record, instance, approval, secret_ref, resolver = await _revoked_regrant_projection(
        harness, grant_args
    )
    assert approval.consumed_at is not None
    instance = replace(instance, **{field: mismatched_value})
    harness.instance_store.records[(COMPANY_A, PLUGIN_X)] = instance

    # The original approval is consumed, but no approval matches the foreign pin.
    with pytest.raises(LookupError):
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=record.plugin_instance_id,
            secret_ref=secret_ref,
        )


@pytest.mark.asyncio
async def test_active_projection_path_does_not_use_regrant_fallback(
    harness, grant_args
):
    first = await harness.service().grant(**grant_args)
    resolver = _full_resolver(harness)
    assert (
        await resolver.resolve(
            company_id=COMPANY_A,
            plugin_id=PLUGIN_X,
            plugin_instance_id=first.grant.plugin_instance_id,
            secret_ref=first.grant.webhook_secret_ref,
        )
        == harness.webhook_secrets.value_for(first.grant)
    )
