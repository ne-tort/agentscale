"""Тесты ``PluginObjectStore``: короткие creds, префикс, TTL, STS fail-closed,
uninstall-изоляция и форма аудита.

Покрывает критерии v4 §11: №19 (STS creds не видят чужой bucket/префикс),
№15 (uninstall не трогает чужие БД/бакеты), №11 (аудит без payload), §5.4,
§5.5, §4.5. Тесты НАПИСАНЫ, НЕ ЗАПУСКАЮТСЯ (verification freeze).
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.objects import (
    AUDIT_EVENT,
    DEFAULT_TTL_SECONDS,
    MAX_TTL_SECONDS,
    MIN_TTL_SECONDS,
    InvalidObjectMethod,
    InvalidTtl,
    ObjectAccessDenied,
    ScopedCredentials,
    StsUnavailable,
    bucket_name,
    bucket_policy,
    module_prefix,
    session_policy,
)
from prodavan.application.plugins.objects.errors import BucketUnavailable, InvalidObjectKey
from prodavan.application.plugins.objects.service import _normalize_key
from tests.unit.plugins.objects.conftest import (  # type: ignore[import-not-found]
    FakeAudit,
    FakeBuckets,
    FakeClock,
    FakeSts,
    build_store,
)

COMPANY = "co_123"
OTHER_COMPANY = "co_other"
PLUGIN = "mod_equipment"


# ---------------------------------------------------------------------------
# Нормализация ключа / отказ в доступе
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_key_traversal_to_other_company_is_denied() -> None:
    """``../../other-company/secret`` → ObjectAccessDenied, URL не подписан."""
    store, _, _, presigner, audit, _ = build_store()
    with pytest.raises(ObjectAccessDenied):
        await store.presign(COMPANY, PLUGIN, "../../other-company/secret", "get")
    assert presigner.calls == []
    assert audit.for_op("presign")[-1]["decision"] == "deny"
    assert audit.for_op("presign")[-1]["code"] in (
        ObjectAccessDenied.CODE,
        InvalidObjectKey.CODE,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_key",
    [
        "../../other-company/secret",
        "../module-mod_other/secret",
        "tenant-co_other/secret",
        "module-mod_other/secret",
        "/etc/passwd",
        "s3://tenant-co_other/secret",
        "a//b",
        "a/../../b",
        "..\\other\\secret",
        "*",
        "",
        "\x00evil",
    ],
)
async def test_foreign_or_traversal_keys_are_all_denied(bad_key: str) -> None:
    store, _, _, presigner, _, _ = build_store()
    with pytest.raises(ObjectAccessDenied):
        await store.presign(COMPANY, PLUGIN, bad_key, "put")
    assert presigner.calls == []


@pytest.mark.asyncio
async def test_plain_foreign_looking_filename_stays_inside_own_prefix() -> None:
    """``other-company/secret`` — это имя файла, а не чужой префикс.

    Ключ всё равно перестраивается в ``module-<plugin>/...``: выхода за пределы
    своей области нет, поэтому отказ здесь был бы ложноположительным.
    """
    store, _, _, presigner, _, _ = build_store()
    await store.presign(COMPANY, PLUGIN, "other-company/secret", "put")
    assert presigner.calls[0]["key"] == "module-mod_equipment/other-company/secret"


@pytest.mark.asyncio
async def test_presign_normalizes_key_into_own_module_prefix() -> None:
    """Даже «голый» ключ перестраивается в ``module-<plugin>/...``."""
    store, _, _, presigner, _, _ = build_store()
    url = await store.presign(COMPANY, PLUGIN, "reports/q1.xlsx", "put")
    call = presigner.calls[0]
    assert call["bucket"] == "tenant-co_123"
    assert call["key"] == "module-mod_equipment/reports/q1.xlsx"
    assert "module-mod_equipment/reports/q1.xlsx" in url
    assert call["bucket"] == f"tenant-{COMPANY}"


@pytest.mark.asyncio
async def test_presign_with_already_correct_prefix_does_not_duplicate_it() -> None:
    store, _, _, presigner, _, _ = build_store()
    await store.presign(COMPANY, PLUGIN, "module-mod_equipment/a.txt", "get")
    assert presigner.calls[0]["key"] == "module-mod_equipment/a.txt"


def test_normalize_key_is_pure_and_scoped() -> None:
    assert (
        _normalize_key(COMPANY, PLUGIN, "a/b.txt", operation="test")
        == "module-mod_equipment/a/b.txt"
    )
    with pytest.raises(ObjectAccessDenied):
        _normalize_key(COMPANY, PLUGIN, "../../other-company/secret", operation="test")
    with pytest.raises(InvalidObjectKey):
        # Невалидный company_id отсекается тем же генератором, что строит ARN.
        _normalize_key("../bad", PLUGIN, "x", operation="test")


# ---------------------------------------------------------------------------
# TTL presigned: 60 <= ttl <= 3600
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [MIN_TTL_SECONDS, 300, DEFAULT_TTL_SECONDS, MAX_TTL_SECONDS])
async def test_presign_accepts_ttl_in_range(ttl: int) -> None:
    store, _, _, presigner, _, _ = build_store()
    await store.presign(COMPANY, PLUGIN, "a.txt", "get", ttl)
    assert presigner.calls[0]["ttl_seconds"] == ttl
    assert MIN_TTL_SECONDS <= presigner.calls[0]["ttl_seconds"] <= MAX_TTL_SECONDS


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [0, 1, 59, -1, 3601, 7200, 10**9])
async def test_presign_rejects_ttl_out_of_range(ttl: int) -> None:
    """Не клампим молча (as-built делал только ``max(60, ...)``), а отказываем."""
    store, _, _, presigner, _, _ = build_store()
    with pytest.raises(InvalidTtl):
        await store.presign(COMPANY, PLUGIN, "a.txt", "get", ttl)
    assert presigner.calls == []


@pytest.mark.asyncio
async def test_presign_rejects_non_numeric_ttl() -> None:
    store, _, _, _, _, _ = build_store()
    with pytest.raises(InvalidTtl):
        await store.presign(COMPANY, PLUGIN, "a.txt", "get", "eternity")  # type: ignore[arg-type]


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["delete", "list", "POST", "", "GET"])
async def test_presign_rejects_unsupported_method(method: str) -> None:
    """Разрешены только get/put; всё остальное — явный отказ до подписи."""
    store, _, _, presigner, audit, _ = build_store()
    with pytest.raises(InvalidObjectMethod):
        await store.presign(COMPANY, PLUGIN, "a.txt", method)
    assert presigner.calls == []
    assert audit.for_op("presign")[-1]["decision"] == "deny"


# ---------------------------------------------------------------------------
# STS: short-lived scoped creds, только свой префикс, fail-closed
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_issue_credentials_returns_scoped_short_lived_creds() -> None:
    store, _, sts, _, _, clock = build_store()
    creds = await store.issue_credentials(COMPANY, PLUGIN, ttl_seconds=900)
    assert isinstance(creds, ScopedCredentials)
    assert creds.bucket == "tenant-co_123"
    assert creds.prefix == "module-mod_equipment/"
    assert creds.access_key != "agentscale"          # не root-user из minio.yaml
    assert creds.access_key != "agentscale-api"      # не service user
    assert creds.session_token                       # обязательно STS, не static keys
    assert creds.expires_at == clock.time() + 900
    assert creds.policy == session_policy(COMPANY, PLUGIN)
    assert sts.calls[0]["ttl_seconds"] == 900
    assert sts.calls[0]["session_policy"] == session_policy(COMPANY, PLUGIN)


@pytest.mark.asyncio
async def test_sts_call_carries_session_policy_subset_of_parent() -> None:
    store, _, sts, _, _, _ = build_store()
    await store.issue_credentials(COMPANY, PLUGIN)
    s = sts.calls[0]["session_policy"]
    parent = bucket_policy(COMPANY, PLUGIN)
    assert s["Version"] == parent["Version"]
    assert s["Statement"] == parent["Statement"]
    assert "agentscale/*" not in str(s)


@pytest.mark.asyncio
async def test_other_company_cannot_get_creds_for_tenant_other() -> None:
    """Критерий 19: creds компании A не могут указывать на ``tenant-<other>``.

    Сервис вычисляет ожидаемый бакет из СВОЕГО ``company_id`` и перепроверяет
    ответ адаптера; подсунутый чужой bucket/prefix → ``StsUnavailable``.
    """
    clock = FakeClock()
    hostile = ScopedCredentials(
        access_key="ASIA_OTHER",
        secret_key="other-secret",
        session_token="other-token",
        bucket=bucket_name(OTHER_COMPANY),
        prefix=module_prefix(PLUGIN),
        expires_at=clock.time() + 900,
        policy=session_policy(OTHER_COMPANY, PLUGIN),
    )
    store, _, _, _, audit, _ = build_store(
        sts=FakeSts(clock=clock, override=hostile), clock=clock
    )
    with pytest.raises(StsUnavailable):
        await store.issue_credentials(COMPANY, PLUGIN)
    assert audit.for_op("issue_credentials")[-1]["decision"] == "deny"


@pytest.mark.asyncio
async def test_creds_with_foreign_prefix_are_rejected() -> None:
    clock = FakeClock()
    hostile = ScopedCredentials(
        access_key="ASIA_X",
        secret_key="s",
        session_token="tok",
        bucket=bucket_name(COMPANY),
        prefix="module-mod_other/",
        expires_at=clock.time() + 900,
        policy=session_policy(COMPANY, "mod_other"),
    )
    store, _, _, _, _, _ = build_store(sts=FakeSts(clock=clock, override=hostile), clock=clock)
    with pytest.raises(StsUnavailable):
        await store.issue_credentials(COMPANY, PLUGIN)


@pytest.mark.asyncio
async def test_creds_without_session_token_or_expired_are_rejected() -> None:
    clock = FakeClock()
    for bad in (
        ScopedCredentials("a", "b", "", bucket_name(COMPANY), module_prefix(PLUGIN), clock.time() + 900, {}),
        ScopedCredentials("a", "b", "t", bucket_name(COMPANY), module_prefix(PLUGIN), clock.time() - 1, {}),
    ):
        store, _, _, _, _, _ = build_store(sts=FakeSts(clock=clock, override=bad), clock=clock)
        with pytest.raises(StsUnavailable):
            await store.issue_credentials(COMPANY, PLUGIN)


@pytest.mark.asyncio
async def test_sts_unavailable_fails_closed_and_never_returns_root_creds() -> None:
    """Главный fail-closed тест: STS упал → ошибка, а НЕ root-креды."""
    store, _, _, _, audit, _ = build_store(
        sts=FakeSts(error=RuntimeError("connection refused"))
    )
    with pytest.raises(StsUnavailable) as exc:
        await store.issue_credentials(COMPANY, PLUGIN)
    assert exc.value.CODE == StsUnavailable.CODE
    assert "root" in str(exc.value).lower()
    denied = audit.for_op("issue_credentials")
    assert denied and denied[0]["decision"] == "deny"
    assert denied[0]["code"] == StsUnavailable.CODE
    # Ни в ответе, ни в аудите нет секретов.
    assert "agentscale123" not in str(denied)


@pytest.mark.asyncio
async def test_sts_not_configured_fails_closed() -> None:
    from prodavan.application.plugins.objects.service import PluginObjectStore

    buckets = FakeBuckets()
    audit = FakeAudit()
    store = PluginObjectStore(buckets=buckets, sts=None, audit=audit, clock=FakeClock())
    with pytest.raises(StsUnavailable):
        await store.issue_credentials(COMPANY, PLUGIN)
    assert audit.for_op("issue_credentials")[-1]["decision"] == "deny"


@pytest.mark.asyncio
async def test_sts_garbage_response_fails_closed() -> None:
    store, _, _, _, _, _ = build_store(sts=FakeSts(return_garbage=True))
    with pytest.raises(StsUnavailable):
        await store.issue_credentials(COMPANY, PLUGIN)


@pytest.mark.asyncio
@pytest.mark.parametrize("ttl", [0, 59, 3601, 99999])
async def test_issue_credentials_rejects_ttl_out_of_range(ttl: int) -> None:
    store, _, sts, _, _, _ = build_store()
    with pytest.raises(InvalidTtl):
        await store.issue_credentials(COMPANY, PLUGIN, ttl_seconds=ttl)
    assert sts.calls == []


# ---------------------------------------------------------------------------
# grant / revoke / uninstall
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_grant_ensures_own_bucket_quota_and_policy() -> None:
    store, buckets, _, _, audit, _ = build_store(quota_bytes=2048)
    result = await store.grant(COMPANY, PLUGIN)
    assert result["bucket"] == "tenant-co_123"
    assert result["prefix"] == "module-mod_equipment/"
    assert buckets.ensured == [("tenant-co_123", bucket_policy(COMPANY, PLUGIN), 2048)]
    assert "agentscale" not in str(buckets.ensured[0][1])
    assert audit.for_op("grant")[0]["decision"] == "allow"


@pytest.mark.asyncio
async def test_grant_bucket_unavailable_fails_closed() -> None:
    store, _, _, _, audit, _ = build_store(
        buckets=FakeBuckets(ensure_error=RuntimeError("minio down"))
    )
    with pytest.raises(BucketUnavailable):
        await store.grant(COMPANY, PLUGIN)
    assert audit.for_op("grant")[0]["decision"] == "deny"


@pytest.mark.asyncio
async def test_revoke_keeps_data_and_bucket() -> None:
    store, buckets, _, _, _, _ = build_store()
    result = await store.revoke(COMPANY, PLUGIN)
    assert result["bucket_deleted"] is False
    assert buckets.deleted_buckets == []
    assert buckets.deleted_prefixes == []


@pytest.mark.asyncio
async def test_uninstall_deletes_only_own_bucket_and_prefix() -> None:
    """Критерий 15: удаляем ровно ``tenant-<company>`` и ``module-<plugin>/``."""
    buckets = FakeBuckets(
        objects={
            "tenant-co_123": [
                _info("module-mod_equipment/a.txt"),
                _info("module-mod_other/b.txt"),
            ],
            "tenant-co_other": [_info("module-mod_equipment/c.txt")],
        }
    )
    store, buckets, _, _, audit, _ = build_store(buckets=buckets)
    result = await store.uninstall(COMPANY, PLUGIN)

    assert result["bucket"] == "tenant-co_123"
    assert result["prefix"] == "module-mod_equipment/"
    assert result["bucket_deleted"] is True
    assert buckets.deleted_buckets == ["tenant-co_123"]
    assert "tenant-co_other" not in buckets.deleted_buckets
    assert buckets.deleted_prefixes == []  # весь СВОЙ бакет удалён целиком
    # Чужая компания не тронута.
    assert [o.key for o in buckets.objects["tenant-co_other"]] == ["module-mod_equipment/c.txt"]
    assert audit.for_op("uninstall")[0]["decision"] == "allow"


@pytest.mark.asyncio
async def test_uninstall_prefix_only_mode_never_deletes_bucket() -> None:
    """v1-компромисс «префикс + STS»: удаляем только СВОЙ префикс."""
    buckets = FakeBuckets(
        owns_bucket=False,
        objects={
            "tenant-co_123": [
                _info("module-mod_equipment/a.txt"),
                _info("module-mod_other/b.txt"),
            ]
        },
    )
    store, buckets, _, _, _, _ = build_store(buckets=buckets)
    result = await store.uninstall(COMPANY, PLUGIN)
    assert result["bucket_deleted"] is False
    assert buckets.deleted_buckets == []
    assert buckets.deleted_prefixes == [("tenant-co_123", "module-mod_equipment/")]
    assert [o.key for o in buckets.objects["tenant-co_123"]] == ["module-mod_other/b.txt"]


@pytest.mark.asyncio
async def test_uninstall_without_delete_prefix_in_shared_mode_fails_closed() -> None:
    """Нельзя «тихо» удалить весь общий бакет вместо своего префикса."""

    class NoDeletePrefixBuckets(FakeBuckets):
        delete_prefix = None  # type: ignore[assignment]

        def __init__(self) -> None:
            super().__init__(owns_bucket=False)

    store, buckets, _, _, _, _ = build_store(buckets=NoDeletePrefixBuckets())
    with pytest.raises(BucketUnavailable):
        await store.uninstall(COMPANY, PLUGIN)
    assert buckets.deleted_buckets == []


def _info(key: str):
    from prodavan.application.plugins.objects.ports import ObjectInfo

    return ObjectInfo(key=key, size=10)


# ---------------------------------------------------------------------------
# list — только свой префикс
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_returns_only_own_prefix_metadata() -> None:
    from prodavan.application.plugins.objects.ports import ObjectInfo

    buckets = FakeBuckets(
        objects={
            "tenant-co_123": [
                ObjectInfo("module-mod_equipment/a.txt", 5),
                ObjectInfo("module-mod_other/b.txt", 7),
                ObjectInfo("tenant-co_other/c.txt", 9),
            ]
        }
    )
    store, _, _, _, _, _ = build_store(buckets=buckets)
    items = await store.list(COMPANY, PLUGIN)
    assert [i.key for i in items] == ["module-mod_equipment/a.txt"]
    assert all(isinstance(i, ObjectInfo) for i in items)
    assert not any(isinstance(i, bytes) for i in items)  # никаких bytes через API


@pytest.mark.asyncio
async def test_grant_and_uninstall_of_one_company_never_name_other_bucket() -> None:
    """Явная изоляция: ни grant, ни uninstall не называют ``tenant-<other>``."""
    store, buckets, _, _, audit, _ = build_store()
    granted = await store.grant(COMPANY, PLUGIN)
    removed = await store.uninstall(COMPANY, PLUGIN)
    assert granted["bucket"] == "tenant-co_123"
    assert removed["bucket"] == "tenant-co_123"
    assert "tenant-co_other" not in str(buckets.ensured)
    assert "tenant-co_other" not in str(buckets.deleted_buckets)
    assert "tenant-co_other" not in str(buckets.deleted_prefixes)
    for event in audit.events:
        assert event["bucket"] == "tenant-co_123"


@pytest.mark.asyncio
async def test_issue_credentials_rejects_widened_session_policy() -> None:
    """Защита от будущего «расширения» session policy: ⊂ parent обязателен."""
    from unittest.mock import patch

    store, _, sts, _, _, _ = build_store()
    widened = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": ["s3:*"],
                "Resource": ["arn:aws:s3:::*"],
            }
        ],
    }
    with patch(
        "prodavan.application.plugins.objects.service.iam.session_policy",
        return_value=widened,
    ):
        with pytest.raises(StsUnavailable):
            await store.issue_credentials(COMPANY, PLUGIN)
    assert sts.calls == []  # до AssumeRole дело не дошло


@pytest.mark.asyncio
async def test_list_rejects_foreign_key_from_adapter() -> None:
    """Адаптер «широко» вернул чужой ключ → fail-closed, а не утечка."""
    from prodavan.application.plugins.objects.ports import ObjectInfo

    class WideBuckets(FakeBuckets):
        async def list_prefix(self, bucket: str, prefix: str):
            return [ObjectInfo("module-mod_other/secret.txt", 1)]

    store, _, _, _, _, _ = build_store(buckets=WideBuckets())
    with pytest.raises(ObjectAccessDenied):
        await store.list(COMPANY, PLUGIN)


# ---------------------------------------------------------------------------
# Аудит: форма ``plugin_object_access`` без bytes/payload/токенов (критерий 11)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_audit_event_has_no_bytes_payload_or_secrets() -> None:
    store, _, _, _, audit, _ = build_store()
    await store.grant(COMPANY, PLUGIN)
    await store.issue_credentials(COMPANY, PLUGIN)
    await store.presign(COMPANY, PLUGIN, "reports/q1.xlsx", "get")
    await store.list(COMPANY, PLUGIN)
    await store.uninstall(COMPANY, PLUGIN)

    assert audit.events, "аудит обязан писать события"
    forbidden_keys = {
        "bytes", "payload", "content", "data", "body", "raw_body",
        "token", "secret", "password", "access_key", "secret_key",
        "session_token", "authorization", "cookie", "pii", "sql",
    }
    for event in audit.events:
        assert event["event"] == AUDIT_EVENT
        assert event["capability"] == "objects"
        assert event["decision"] in ("allow", "deny")
        assert set(event) >= {
            "event", "capability", "company_id", "plugin_id", "bucket",
            "prefix", "operation", "decision",
        }
        lowered = {str(k).lower() for k in event}
        assert not (lowered & forbidden_keys), f"запрещённое поле в аудите: {lowered & forbidden_keys}"
        for value in event.values():
            assert not isinstance(value, (bytes, bytearray, memoryview))
        # Секреты из minio.yaml / minio-api-policy.json не попали в лог.
        serialized = str(event).lower()
        for secret in ("agentscale123", "agentscale-api-secret", "temporary-secret", "temporary-session-token"):
            assert secret not in serialized
        # Ключ объекта хранится только как digest.
        assert "key_digest" in event
        assert "key" not in lowered


@pytest.mark.asyncio
async def test_audit_denied_presign_records_digest_not_raw_key() -> None:
    store, _, _, _, audit, _ = build_store()
    with pytest.raises(ObjectAccessDenied):
        await store.presign(COMPANY, PLUGIN, "../../other-company/secret", "get")
    event = audit.for_op("presign")[-1]
    assert event["decision"] == "deny"
    assert "../../other-company/secret" not in str(event)
    assert event["key_digest"]


@pytest.mark.asyncio
async def test_presigner_unavailable_fails_closed() -> None:
    """Нет PresignPort → явный отказ, URL от root-ключей не подписывается."""
    from prodavan.application.plugins.objects.service import PluginObjectStore

    store = PluginObjectStore(
        buckets=FakeBuckets(),
        sts=FakeSts(),
        presigner=None,
        audit=FakeAudit(),
        clock=FakeClock(),
    )
    with pytest.raises(BucketUnavailable):
        await store.presign(COMPANY, PLUGIN, "a.txt", "get")


@pytest.mark.asyncio
async def test_presigner_error_fails_closed() -> None:
    store, _, _, _, audit, _ = build_store(
        presigner=_ErrorPresigner()
    )
    with pytest.raises(BucketUnavailable):
        await store.presign(COMPANY, PLUGIN, "a.txt", "get")
    assert audit.for_op("presign")[-1]["decision"] == "deny"


class _ErrorPresigner:
    async def presign_get(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        raise RuntimeError("minio unreachable")

    async def presign_put(self, *, bucket: str, key: str, ttl_seconds: int) -> str:
        raise RuntimeError("minio unreachable")


@pytest.mark.asyncio
async def test_usage_returns_bucket_bytes_only() -> None:
    buckets = FakeBuckets(usage={"tenant-co_123": 4096})
    store, _, _, _, _, _ = build_store(buckets=buckets)
    assert await store.usage(COMPANY) == 4096


@pytest.mark.asyncio
async def test_audit_append_failure_fails_closed() -> None:
    """Append failure для позитивной мутации = deny (§4.5)."""
    from prodavan.application.plugins.objects.errors import AuditUnavailable

    audit = FakeAudit(fail=True)
    store, _, _, _, _, _ = build_store(audit=audit)
    with pytest.raises(AuditUnavailable):
        await store.grant(COMPANY, PLUGIN)
