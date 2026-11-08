"""Тесты ``objects_adapter``: policy/quota → bucket_policy/session_policy, drop → delete.

Покрытие:
* ``ensure_bucket`` транслируется в ``PluginObjectStore.grant`` (позиционный)
  и реальный ``BucketAdminPort.ensure_bucket`` получает ``policy``/``quota_bytes``;
* ``drop_prefix`` → ``delete_prefix(bucket, prefix)`` СТРОГО своей пары;
* ``drop_bucket_if_empty`` → ``delete_bucket`` и ``False`` при чужих префиксах;
* чужая компания не подменяется;
* fail-closed: нет ``delete_prefix``/``list_prefix`` → явный отказ.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.adapters.objects_adapter import ObjectStoreAdapter
from prodavan.application.plugins.objects import policy as object_policy
from prodavan.application.plugins.objects.errors import BucketUnavailable
from prodavan.application.plugins.objects.ports import ObjectInfo
from prodavan.application.plugins.objects.service import PluginObjectStore

from .fakes import (
    COMPANY,
    COMPANY_OTHER,
    PLUGIN,
    FakeBucketAdmin,
    FakeObjectAudit,
)


def _adapter(buckets: FakeBucketAdmin | None = None) -> tuple[ObjectStoreAdapter, FakeBucketAdmin]:
    admin = buckets or FakeBucketAdmin()
    store = PluginObjectStore(
        buckets=admin,
        audit=FakeObjectAudit(),
        quota_bytes=1024 * 1024 * 1024,
    )
    return ObjectStoreAdapter(store=store, buckets=admin), admin


# --------------------------------------------------------------------------- ensure


async def test_ensure_bucket_translates_to_grant_with_policy_and_quota() -> None:
    adapter, admin = _adapter()

    binding = await adapter.ensure_bucket(company_id=COMPANY, plugin_id=PLUGIN)

    assert binding.company_id == COMPANY
    assert binding.plugin_id == PLUGIN
    assert binding.bucket == object_policy.bucket_name(COMPANY)
    assert binding.prefix.startswith(object_policy.module_prefix(PLUGIN).rstrip("/"))

    ensure_calls = [c for c in admin.calls if c.method == "ensure_bucket"]
    assert len(ensure_calls) == 1
    call = ensure_calls[0]
    assert call.args == (binding.bucket,)  # позиционный bucket
    assert call.kwargs["quota_bytes"] == 1024 * 1024 * 1024
    # Политика собрана одним генератором: session_policy ⊂ bucket_policy.
    parent = object_policy.bucket_policy(COMPANY, PLUGIN)
    session = object_policy.session_policy(COMPANY, PLUGIN)
    assert call.kwargs["policy"] == parent
    assert call.kwargs["policy"]["Statement"] == session["Statement"]
    assert "agentscale/*" not in str(call.kwargs["policy"])
    # session_policy ⊂ bucket_policy: один генератор на обе политики.


async def test_ensure_bucket_never_addresses_other_company_bucket() -> None:
    adapter, admin = _adapter()
    await adapter.ensure_bucket(company_id=COMPANY, plugin_id=PLUGIN)

    foreign = object_policy.bucket_name(COMPANY_OTHER)
    assert all(c.args[0] != foreign for c in admin.calls if c.args)


# --------------------------------------------------------------------------- drop_prefix


async def test_drop_prefix_maps_to_delete_prefix_with_own_scope() -> None:
    adapter, admin = _adapter()

    assert await adapter.drop_prefix(company_id=COMPANY, plugin_id=PLUGIN) is True

    calls = [c for c in admin.calls if c.method == "delete_prefix"]
    assert len(calls) == 1
    assert calls[0].args == (
        object_policy.bucket_name(COMPANY),
        object_policy.module_prefix(PLUGIN),
    )
    assert calls[0].args[0] != object_policy.bucket_name(COMPANY_OTHER)


async def test_drop_prefix_rejects_bad_count() -> None:
    class BadBuckets(FakeBucketAdmin):
        async def delete_prefix(self, bucket: str, prefix: str) -> int:
            return -1

    adapter, _admin = _adapter(BadBuckets())
    with pytest.raises(BucketUnavailable, match="некорректное число"):
        await adapter.drop_prefix(company_id=COMPANY, plugin_id=PLUGIN)


async def test_drop_prefix_requires_delete_prefix() -> None:
    class NoDeletePrefix(FakeBucketAdmin):
        delete_prefix = None  # type: ignore[assignment]

    adapter, _admin = _adapter(NoDeletePrefix())
    with pytest.raises(BucketUnavailable, match="delete_prefix"):
        await adapter.drop_prefix(company_id=COMPANY, plugin_id=PLUGIN)


# --------------------------------------------------------------------------- drop_bucket


async def test_drop_bucket_if_empty_deletes_only_own_bucket() -> None:
    adapter, admin = _adapter()

    assert await adapter.drop_bucket_if_empty(company_id=COMPANY) is True
    assert admin.deleted_buckets == [object_policy.bucket_name(COMPANY)]
    assert object_policy.bucket_name(COMPANY_OTHER) not in admin.deleted_buckets


async def test_drop_bucket_if_empty_returns_false_when_other_plugins_remain() -> None:
    admin = FakeBucketAdmin()
    admin.objects[object_policy.bucket_name(COMPANY)] = [
        ObjectInfo(key=object_policy.module_prefix(PLUGIN) + "file.txt", size=1),
    ]
    adapter, _admin = _adapter(admin)

    assert await adapter.drop_bucket_if_empty(company_id=COMPANY) is False
    assert admin.deleted_buckets == []


async def test_drop_bucket_if_empty_requires_list_prefix() -> None:
    """Нельзя «предположить пустоту»: без листинга удаление запрещено."""

    class NoList(FakeBucketAdmin):
        list_prefix = None  # type: ignore[assignment]

    adapter, _admin = _adapter(NoList())
    with pytest.raises(BucketUnavailable, match="list_prefix"):
        await adapter.drop_bucket_if_empty(company_id=COMPANY)


async def test_drop_bucket_if_empty_propagates_delete_failure() -> None:
    admin = FakeBucketAdmin(delete_fails=True)
    adapter, _admin = _adapter(admin)
    with pytest.raises(BucketUnavailable):
        await adapter.drop_bucket_if_empty(company_id=COMPANY)


# --------------------------------------------------------------------------- fail-closed


def test_adapter_requires_both_implementations() -> None:
    with pytest.raises((ValueError, TypeError), match="object_store|ожидался"):
        ObjectStoreAdapter(store=None, buckets=FakeBucketAdmin())  # type: ignore[arg-type]
    with pytest.raises((ValueError, TypeError), match="object_store|ожидался"):
        ObjectStoreAdapter(
            store=PluginObjectStore(
                buckets=FakeBucketAdmin(), audit=FakeObjectAudit()
            ),
            buckets=None,  # type: ignore[arg-type]
        )
