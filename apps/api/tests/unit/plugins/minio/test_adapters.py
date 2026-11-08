"""Тесты MinIO-адаптеров портов задачи 06 (НАПИСАНЫ, НЕ ЗАПУСКАЮТСЯ).

Покрытие обязательных кейсов:
  * ensure_bucket создаёт ``tenant-<co>``, а НЕ ``agentscale``;
  * несколько плагинов компании МЕРДЖАТ policy (не перезаписывают);
  * STS недоступен → ``StsUnavailable``, root-креды НЕ возвращаются;
  * presign TTL вне 60..3600 → ``InvalidTtl``;
  * ключ с ``../`` → ``ObjectAccessDenied`` (проверка НА УРОВНЕ АДАПТЕРА тоже);
  * audit не содержит payload/bytes/секретов;
  * uninstall удаляет ТОЛЬКО свой bucket;
  * config без кредов → fail-closed ошибка.

Дополнительно (вердикт родителя): отдельный подписывающий principal,
Presigner обязателен в дефолтном режиме, revoke-семантика presigned URL,
внешний endpoint, whitelist-проверка политик, отсутствие bytes в контракте.

Критерии v4 §11: №11 (аудит без payload), №15 (uninstall не трогает чужие),
№19 (scoped доступ), §5.4, §4.5, §13.3.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from prodavan.application.plugins.objects import errors as err
from prodavan.application.plugins.objects import policy as iam
from prodavan.application.plugins.objects import ports
from prodavan.application.plugins.objects.service import AUDIT_EVENT, PluginObjectStore
from prodavan.infrastructure.plugins.minio import adapters
from prodavan.infrastructure.plugins.minio import config as cfgmod
from prodavan.infrastructure.plugins.minio import build_plugin_object_store
from tests.unit.plugins.minio.conftest import (  # type: ignore[import-not-found]
    ADMIN_KEY,
    ADMIN_SECRET,
    ENDPOINT,
    PRESIGN_KEY,
    PRESIGN_SECRET,
    PUBLIC_ENDPOINT,
    FakeAuditSink,
    FakeClock,
    FakeQuotaAdmin,
    FakeS3Client,
    FakeStsClient,
    make_config,
    make_env,
)

COMPANY = "co_123"
OTHER_COMPANY = "co_other"
PLUGIN_A = "mod_equipment"
PLUGIN_B = "mod_invoices"
BUCKET = iam.bucket_name(COMPANY)
OTHER_BUCKET = iam.bucket_name(OTHER_COMPANY)


def _admin(**kw: Any) -> adapters.MinioBucketAdmin:
    client = kw.pop("client", None) or FakeS3Client()
    quotas = kw.pop("quota_admin", None) or FakeQuotaAdmin()
    return adapters.MinioBucketAdmin(
        config=make_config(), client=client, quota_admin=quotas, **kw
    )


def _policy_statements(raw: str) -> list[dict[str, Any]]:
    return json.loads(raw)["Statement"]


# ===========================================================================
# ensure_bucket: tenant-<co>, НЕ agentscale; МЕРДЖ политик
# ===========================================================================
@pytest.mark.asyncio
async def test_ensure_bucket_creates_tenant_bucket_not_agentscale() -> None:
    """grant создаёт tenant-<company>, а общий as-built бакет не трогается."""
    client = FakeS3Client()
    admin = _admin(client=client)
    policy = iam.bucket_policy(COMPANY, PLUGIN_A)

    await admin.ensure_bucket(BUCKET, policy=policy, quota_bytes=1024)

    assert client.created_buckets == [BUCKET]
    assert BUCKET.startswith("tenant-")
    assert "agentscale" not in client.created_buckets
    assert "agentscale" not in client.buckets
    assert BUCKET in client.buckets
    # Политика записана и квота выставлена именно на свой бакет.
    assert client.policy_calls[0]["Bucket"] == BUCKET
    assert admin.quota_admin.calls == [(BUCKET, 1024)]


@pytest.mark.asyncio
async def test_ensure_bucket_rejects_agentscale_and_foreign_names() -> None:
    """Имя agentscale / не-tenant / чужой tenant-* → ObjectAccessDenied."""
    admin = _admin()
    for bad in ("agentscale", "agentscale-api", "prodavan", "bucket", ""):
        with pytest.raises(err.ObjectAccessDenied):
            await admin.ensure_bucket(bad, policy={}, quota_bytes=1024)
    with pytest.raises(err.ObjectAccessDenied):
        await admin.ensure_bucket(
            "tenant-co_Upper", policy={}, quota_bytes=1024
        )


@pytest.mark.asyncio
async def test_ensure_bucket_is_idempotent_for_same_plugin() -> None:
    """Повторный grant того же плагина не плодит дубли Statement."""
    client = FakeS3Client()
    admin = _admin(client=client)
    policy = iam.bucket_policy(COMPANY, PLUGIN_A)

    await admin.ensure_bucket(BUCKET, policy=policy, quota_bytes=1024)
    first = _policy_statements(client.buckets[BUCKET]["policy"])
    await admin.ensure_bucket(BUCKET, policy=policy, quota_bytes=1024)
    second = _policy_statements(client.buckets[BUCKET]["policy"])

    assert client.created_buckets == [BUCKET]  # второй раз НЕ создаём
    assert len(second) == len(first) == 2


@pytest.mark.asyncio
async def test_multiple_plugins_of_company_merge_policy_not_overwrite() -> None:
    """ГЛАВНЫЙ КОНТРАКТ: 2-й плагин компании НЕ отзывает доступ 1-му.

    У обоих плагинов ОДИНАКОВЫЕ Sid (policy.py), различаются ARN/префиксы.
    Дедуп по Sid перезаписал бы Statement первого — тест это ловит.
    """
    client = FakeS3Client()
    admin = _admin(client=client)

    await admin.ensure_bucket(
        BUCKET, policy=iam.bucket_policy(COMPANY, PLUGIN_A), quota_bytes=1024
    )
    await admin.ensure_bucket(
        BUCKET, policy=iam.bucket_policy(COMPANY, PLUGIN_B), quota_bytes=1024
    )

    statements = _policy_statements(client.buckets[BUCKET]["policy"])
    # 2 Statement на плагин (ListBucket + ReadWrite) = 4, ни один не потерян.
    assert len(statements) == 4
    resources = [r for s in statements for r in s["Resource"]]
    assert f"arn:aws:s3:::{BUCKET}" in resources
    assert any(f"module-{PLUGIN_A}/*" in r for r in resources)
    assert any(f"module-{PLUGIN_B}/*" in r for r in resources)
    # ARN общего as-built бакета не появился.
    assert not any("arn:aws:s3:::agentscale" in r for r in resources)


def test_merge_bucket_policies_is_pure_and_does_not_drop_existing() -> None:
    """Чистая функция мерджа: существующие Statement сохраняются."""
    existing = iam.bucket_policy(COMPANY, PLUGIN_A)
    incoming = iam.bucket_policy(COMPANY, PLUGIN_B)
    merged = adapters.merge_bucket_policies(existing, incoming, bucket=BUCKET)
    assert len(merged["Statement"]) == 4
    assert existing["Statement"][0] in merged["Statement"]
    assert incoming["Statement"][0] in merged["Statement"]


def test_merge_rejects_wide_or_agentscale_policy() -> None:
    """Широкая политика (Resource '*', agentscale/*, s3:*) → отказ."""
    for bad in (
        {"Version": iam.POLICY_VERSION, "Statement": [
            {"Sid": "Wide", "Effect": "Allow", "Action": ["s3:GetObject"],
             "Resource": ["*"]}
        ]},
        {"Version": iam.POLICY_VERSION, "Statement": [
            {"Sid": "Legacy", "Effect": "Allow", "Action": ["s3:GetObject"],
             "Resource": ["arn:aws:s3:::agentscale/*"]}
        ]},
        {"Version": iam.POLICY_VERSION, "Statement": [
            {"Sid": "Admin", "Effect": "Allow", "Action": ["s3:*"],
             "Resource": [f"arn:aws:s3:::{BUCKET}/module-{PLUGIN_A}/*"]}
        ]},
    ):
        with pytest.raises(err.BucketUnavailable):
            adapters.merge_bucket_policies(None, bad, bucket=BUCKET)


# ===========================================================================
# STS: недоступен → StsUnavailable, root-креды НЕ возвращаются
# ===========================================================================
@pytest.mark.asyncio
async def test_sts_unavailable_by_default_never_returns_root_creds() -> None:
    """Документация MinIO STS не подтверждена ⇒ StsUnavailable ДО вызова."""
    sts_client = FakeStsClient()
    sts = adapters.MinioSts(
        config=make_config(), sts_client=sts_client, sts_available=False
    )
    session = iam.session_policy(COMPANY, PLUGIN_A)
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(
            company_id=COMPANY, plugin_id=PLUGIN_A,
            ttl_seconds=900, session_policy=session,
        )
    # Ни одного сетевого вызова, ни одного root/service-ключа.
    assert sts_client.calls == []
    assert not hasattr(sts, "root_access_key")


@pytest.mark.asyncio
async def test_sts_failure_does_not_fallback_to_admin_creds() -> None:
    """Даже при включённом флаге сбой AssumeRole = StsUnavailable, не root."""
    sts_client = FakeStsClient(fail=True)
    sts = adapters.MinioSts(
        config=make_config(sts_enabled=True),
        sts_client=sts_client,
        sts_available=True,
    )
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(
            company_id=COMPANY, plugin_id=PLUGIN_A, ttl_seconds=900,
            session_policy=iam.session_policy(COMPANY, PLUGIN_A),
        )


@pytest.mark.asyncio
async def test_sts_rejects_admin_or_root_like_credentials() -> None:
    """Адаптер не имеет права вернуть admin-ключи как «временные»."""
    sts_client = FakeStsClient(return_admin_creds=True)
    sts = adapters.MinioSts(
        config=make_config(sts_enabled=True),
        sts_client=sts_client, sts_available=True, clock=FakeClock(),
    )
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(
            company_id=COMPANY, plugin_id=PLUGIN_A, ttl_seconds=900,
            session_policy=iam.session_policy(COMPANY, PLUGIN_A),
        )


@pytest.mark.asyncio
async def test_sts_rejects_creds_without_session_token() -> None:
    """Без SessionToken это постоянные креды — отказ."""
    sts_client = FakeStsClient(return_without_token=True)
    sts = adapters.MinioSts(
        config=make_config(sts_enabled=True),
        sts_client=sts_client, sts_available=True, clock=FakeClock(),
    )
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(
            company_id=COMPANY, plugin_id=PLUGIN_A, ttl_seconds=900,
            session_policy=iam.session_policy(COMPANY, PLUGIN_A),
        )


@pytest.mark.asyncio
async def test_sts_rejects_tampered_session_policy() -> None:
    """Session policy не равна канонической → отказ ДО сети."""
    sts_client = FakeStsClient()
    sts = adapters.MinioSts(
        config=make_config(sts_enabled=True), sts_client=sts_client,
        sts_available=True,
    )
    tampered = iam.session_policy(COMPANY, PLUGIN_A)
    tampered["Statement"][0]["Resource"] = ["*"]
    with pytest.raises(err.StsUnavailable):
        await sts.assume_role(
            company_id=COMPANY, plugin_id=PLUGIN_A, ttl_seconds=900,
            session_policy=tampered,
        )
    assert sts_client.calls == []


@pytest.mark.asyncio
async def test_sts_opt_in_returns_scoped_credentials() -> None:
    """При opt-in: только временные креды, свой bucket/prefix, TTL-граница."""
    clock = FakeClock(now=1_800_000_000.0)
    sts_client = FakeStsClient(clock=clock)
    sts = adapters.MinioSts(
        config=make_config(sts_enabled=True), sts_client=sts_client,
        sts_available=True, clock=clock,
    )
    session = iam.session_policy(COMPANY, PLUGIN_A)
    creds = await sts.assume_role(
        company_id=COMPANY, plugin_id=PLUGIN_A, ttl_seconds=900,
        session_policy=session,
    )
    assert isinstance(creds, ports.ScopedCredentials)
    assert creds.access_key == "ASIA_PLUGIN_TEMP"
    assert creds.access_key not in (ADMIN_KEY, "agentscale", "agentscale-api")
    assert creds.secret_key not in (ADMIN_SECRET, PRESIGN_SECRET)
    assert creds.session_token == "temporary-session-token"
    assert creds.bucket == BUCKET
    assert creds.prefix == f"module-{PLUGIN_A}/"
    assert creds.policy == session
    assert creds.expires_at <= clock.time() + 900
    # AssumeRole получил ровно каноническую session policy и TTL.
    call = sts_client.calls[0]
    assert call["DurationSeconds"] == 900
    assert json.loads(call["Policy"]) == session


@pytest.mark.asyncio
async def test_service_issue_credentials_is_denied_in_default_factory() -> None:
    """Фабрика по умолчанию: issue_credentials → StsUnavailable (STS opt-in)."""
    sink = FakeAuditSink()
    store = build_plugin_object_store(
        make_config(), audit_sink=sink, quota_admin=FakeQuotaAdmin(),
        s3_client=FakeS3Client(), presign_client=FakeS3Client(),
        allow_noop_audit=False,
    )
    assert isinstance(store, PluginObjectStore)
    with pytest.raises(err.StsUnavailable):
        await store.issue_credentials(COMPANY, PLUGIN_A, 900)
    # Presigner при этом ОБЯЗАТЕЛЬНО подключён (основной путь).
    assert store._presigner is not None


# ===========================================================================
# Presign: TTL 60..3600 и жёсткий префикс НА УРОВНЕ АДАПТЕРА
# ===========================================================================
@pytest.mark.parametrize("ttl", [0, 1, 59, 3601, 7200, -100, "abc", None])
@pytest.mark.asyncio
async def test_presign_ttl_out_of_range_is_invalid_ttl(ttl: Any) -> None:
    """TTL вне [60, 3600] → InvalidTtl, НЕ кламп (as-built делал max(60,...))."""
    presigner = adapters.MinioPresigner(config=make_config(), client=FakeS3Client())
    with pytest.raises(err.InvalidTtl):
        await presigner.presign_get(
            bucket=BUCKET, key=f"module-{PLUGIN_A}/a.txt", ttl_seconds=ttl
        )


@pytest.mark.parametrize("ttl", [60, 900, 3600])
@pytest.mark.asyncio
async def test_presign_ttl_boundaries_are_allowed(ttl: int) -> None:
    client = FakeS3Client()
    presigner = adapters.MinioPresigner(config=make_config(), client=client)
    url = await presigner.presign_get(
        bucket=BUCKET, key=f"module-{PLUGIN_A}/a.txt", ttl_seconds=ttl
    )
    assert url.startswith(ENDPOINT)
    assert client.presign_calls[0]["ExpiresIn"] == ttl
    assert client.presign_calls[0]["operation"] == "get_object"


@pytest.mark.parametrize(
    "bad_key",
    [
        "../secret",
        "../../other-company/secret",
        f"module-{PLUGIN_A}/../../secret",
        f"module-{PLUGIN_A}/../module-{PLUGIN_B}/secret",
        "module-other/secret",
        "tenant-co_other/secret",
        "/etc/passwd",
        f"/{BUCKET}/module-{PLUGIN_A}/secret",
        "s3://tenant-co_other/secret",
        "a//b",
        "a\\..\\b",
        "*",
        "?",
        "",
        "\x00evil",
        "plain.txt",  # ключ БЕЗ префикса плагина адаптер не достраивает
    ],
)
@pytest.mark.asyncio
async def test_presign_rejects_traversal_at_adapter_level(bad_key: str) -> None:
    """``../`` и чужие префиксы → ObjectAccessDenied НА УРОВНЕ АДАПТЕРА.

    Сервис задачи 06 нормализует ключ сам; здесь проверяется ДУБЛЬ: порт не
    доверяет входу (ports.py). URL не подписывается вообще.
    """
    client = FakeS3Client()
    presigner = adapters.MinioPresigner(config=make_config(), client=client)
    with pytest.raises(err.ObjectAccessDenied):
        await presigner.presign_put(
            bucket=BUCKET, key=bad_key, ttl_seconds=900
        )
    assert client.presign_calls == []


@pytest.mark.asyncio
async def test_presign_normalized_key_and_own_bucket_only() -> None:
    """Валидный ключ подписывается как module-<plugin>/... в tenant-<co>."""
    client = FakeS3Client()
    presigner = adapters.MinioPresigner(config=make_config(), client=client)
    await presigner.presign_put(
        bucket=BUCKET, key=f"module-{PLUGIN_A}/reports/q1.xlsx", ttl_seconds=900
    )
    call = client.presign_calls[0]
    assert call["operation"] == "put_object"
    assert call["HttpMethod"] == "PUT"
    assert call["Params"]["Bucket"] == BUCKET
    assert call["Params"]["Key"] == f"module-{PLUGIN_A}/reports/q1.xlsx"
    assert "agentscale" not in call["Params"]["Bucket"]


@pytest.mark.asyncio
async def test_presign_rejects_foreign_bucket_at_adapter_level() -> None:
    """Даже валидный tenant-* чужой компании не подписывается этим вызовом.

    Адаптер дополнительно сверяет, что ключ не адресует ``tenant-*``; имя
    buckets валидируется каноническим генератором.
    """
    client = FakeS3Client()
    presigner = adapters.MinioPresigner(config=make_config(), client=client)
    with pytest.raises(err.ObjectAccessDenied):
        await presigner.presign_get(
            bucket="agentscale", key=f"module-{PLUGIN_A}/a.txt", ttl_seconds=900
        )
    assert client.presign_calls == []


@pytest.mark.asyncio
async def test_presign_uses_separate_principal_and_public_endpoint() -> None:
    """Подпись от ОТДЕЛЬНОГО ключа и (опционально) внешнего host.

    as-built agentscale-api имеет agentscale/* — подпись им запрещена.
    Перепись Host после подписи инвалидировала бы SigV4, поэтому внешний
    endpoint задаётся ДО подписи через presign_endpoint.
    """
    client = FakeS3Client(endpoint=PUBLIC_ENDPOINT)
    config = make_config(public_endpoint=PUBLIC_ENDPOINT)
    assert config.presign_endpoint == PUBLIC_ENDPOINT
    assert config.admin_access_key != config.presign_access_key
    presigner = adapters.MinioPresigner(config=config, client=client)
    url = await presigner.presign_get(
        bucket=BUCKET, key=f"module-{PLUGIN_A}/a.txt", ttl_seconds=900
    )
    assert url.startswith(PUBLIC_ENDPOINT)


@pytest.mark.asyncio
async def test_presign_rejects_url_on_wrong_endpoint() -> None:
    """URL с чужим netloc = отказ (scoped подпись не уводит плагина)."""
    client = FakeS3Client(endpoint="https://evil.example.com")
    presigner = adapters.MinioPresigner(config=make_config(), client=client)
    with pytest.raises(err.BucketUnavailable):
        await presigner.presign_get(
            bucket=BUCKET, key=f"module-{PLUGIN_A}/a.txt", ttl_seconds=900
        )


@pytest.mark.asyncio
async def test_service_presign_normalizes_and_uses_presigner() -> None:
    """Сквозной путь сервиса задачи 06 с нашим MinioPresigner."""
    sink = FakeAuditSink()
    presign_client = FakeS3Client()
    store = build_plugin_object_store(
        make_config(), audit_sink=sink, quota_admin=FakeQuotaAdmin(),
        s3_client=FakeS3Client(), presign_client=presign_client,
    )
    url = await store.presign(COMPANY, PLUGIN_A, "reports/q1.xlsx", "put")
    assert "module-mod_equipment/reports/q1.xlsx" in url
    call = presign_client.presign_calls[0]
    assert call["Params"]["Bucket"] == BUCKET
    assert call["Params"]["Key"] == f"module-{PLUGIN_A}/reports/q1.xlsx"
    assert call["ExpiresIn"] == ports.DEFAULT_TTL_SECONDS
    # В аудите нет URL/подписи — только key_digest.
    for event in sink.rows:
        assert "url" not in str(event).lower() or "key_digest" in event
        assert "X-Amz-Signature" not in str(event)


# ===========================================================================
# Аудит: append-only, без payload/bytes/секретов
# ===========================================================================
@pytest.mark.asyncio
async def test_audit_record_has_no_payload_bytes_or_secrets() -> None:
    """Каноническое событие пишется БЕЗ bytes/payload/токенов/URL."""
    sink = FakeAuditSink()
    audit = adapters.MinioAuditAdapter(sink=sink)
    event = {
        "event": AUDIT_EVENT,
        "capability": "objects",
        "company_id": COMPANY,
        "plugin_id": PLUGIN_A,
        "bucket": BUCKET,
        "prefix": f"module-{PLUGIN_A}/",
        "operation": "presign",
        "decision": "allow",
        "code": None,
        "reason": "ok",
        "key_digest": "sha256:" + "a" * 64,
        "ttl_seconds": 900,
        "object_size": None,
        "ts": 1_800_000_000.0,
    }
    await audit.record(event)
    row = sink.rows[0]
    assert row["event"] == AUDIT_EVENT
    assert row["decision"] == "allow"
    serialized = json.dumps(row, ensure_ascii=False)
    for forbidden in ("payload", "bytes", "password", "secret", "X-Amz-Signature"):
        assert forbidden not in serialized or f"{forbidden}_digest" in serialized
    # Ни одного bytes-значения.
    assert not any(isinstance(v, (bytes, bytearray, memoryview)) for v in row.values())


@pytest.mark.asyncio
async def test_audit_rejects_bytes_and_nested_structures() -> None:
    """Попытка пронести payload/bytes → AuditUnavailable (fail-closed)."""
    audit = adapters.MinioAuditAdapter(sink=FakeAuditSink())
    base = {
        "event": AUDIT_EVENT, "company_id": COMPANY, "plugin_id": PLUGIN_A,
        "operation": "presign", "decision": "allow", "ts": 1.0,
    }
    for bad in (
        {**base, "object_size": b"raw-payload"},
        {**base, "code": bytearray(b"x")},
        {**base, "reason": {"password": "p"}},
        {**base, "key_digest": ["a", "b"]},
    ):
        with pytest.raises(err.AuditUnavailable):
            await audit.record(bad)


@pytest.mark.asyncio
async def test_audit_redacts_signature_like_values() -> None:
    """Даже под безопасным ключом URL/подпись заменяется на digest."""
    sink = FakeAuditSink()
    audit = adapters.MinioAuditAdapter(sink=sink)
    await audit.record({
        "event": AUDIT_EVENT, "company_id": COMPANY, "plugin_id": PLUGIN_A,
        "operation": "presign", "decision": "allow", "ts": 1.0,
        "reason": "https://minio:9000/x?X-Amz-Signature=deadbeef",
    })
    row = sink.rows[0]
    assert "X-Amz-Signature" not in str(row)
    assert row["reason"].startswith("sha256:")
    assert row["reason_redacted"] is True


@pytest.mark.asyncio
async def test_audit_missing_sink_is_fail_closed() -> None:
    """Нет append-only sink → AuditUnavailable, а не тихий noop."""
    audit = adapters.MinioAuditAdapter(sink=None)
    with pytest.raises(err.AuditUnavailable):
        await audit.record({
            "event": AUDIT_EVENT, "company_id": COMPANY, "plugin_id": PLUGIN_A,
            "operation": "grant", "decision": "allow", "ts": 1.0,
        })


@pytest.mark.asyncio
async def test_audit_sink_failure_is_fail_closed() -> None:
    """Ошибка append → AuditUnavailable (сервис превращает в deny)."""
    audit = adapters.MinioAuditAdapter(sink=FakeAuditSink(fail=True))
    with pytest.raises(err.AuditUnavailable):
        await audit.record({
            "event": AUDIT_EVENT, "company_id": COMPANY, "plugin_id": PLUGIN_A,
            "operation": "grant", "decision": "allow", "ts": 1.0,
        })


def test_redact_drops_unknown_fields_without_value() -> None:
    """Неизвестное поле фиксируется фактом изъятия, но значение не пишется."""
    clean = adapters._redact_audit_event({
        "event": AUDIT_EVENT, "company_id": COMPANY, "plugin_id": PLUGIN_A,
        "operation": "presign", "decision": "allow", "ts": 1.0,
        "payload": {"secret": "very-secret-value"},
    })
    assert "payload" not in clean
    assert clean["payload_redacted"] is True
    assert "very-secret-value" not in json.dumps(clean, ensure_ascii=False)


# ===========================================================================
# Uninstall / delete: ТОЛЬКО свой bucket
# ===========================================================================
@pytest.mark.asyncio
async def test_uninstall_deletes_only_own_bucket() -> None:
    """delete_bucket получает ровно tenant-<company>, чужой бакет не тронут."""
    client = FakeS3Client()
    client.buckets = {
        BUCKET: {"objects": {f"module-{PLUGIN_A}/a.txt": 10}, "policy": None},
        OTHER_BUCKET: {"objects": {f"module-{PLUGIN_A}/b.txt": 20}, "policy": None},
        "agentscale": {"objects": {"legacy.txt": 30}, "policy": None},
    }
    admin = _admin(client=client)
    await admin.delete_bucket(BUCKET)

    assert client.deleted_buckets == [BUCKET]
    assert OTHER_BUCKET in client.buckets
    assert client.buckets[OTHER_BUCKET]["objects"] == {
        f"module-{PLUGIN_A}/b.txt": 20
    }
    assert "agentscale" in client.buckets  # as-built не трогаем
    assert BUCKET not in client.buckets


@pytest.mark.asyncio
async def test_service_uninstall_deletes_only_own_bucket() -> None:
    """Сквозной uninstall сервиса задачи 06 с нашим адаптером."""
    client = FakeS3Client()
    client.buckets = {
        BUCKET: {"objects": {}, "policy": None},
        OTHER_BUCKET: {"objects": {}, "policy": None},
    }
    sink = FakeAuditSink()
    store = build_plugin_object_store(
        make_config(), audit_sink=sink, quota_admin=FakeQuotaAdmin(),
        s3_client=client, presign_client=FakeS3Client(),
    )
    result = await store.uninstall(COMPANY, PLUGIN_A)
    assert result["bucket"] == BUCKET
    assert result["bucket_deleted"] is True
    assert client.deleted_buckets == [BUCKET]
    assert OTHER_BUCKET in client.buckets


@pytest.mark.asyncio
async def test_delete_prefix_removes_only_own_module_prefix() -> None:
    """В режиме «префикс + STS» чистится ТОЛЬКО module-<plugin>/."""
    client = FakeS3Client()
    client.buckets = {
        BUCKET: {"objects": {
            f"module-{PLUGIN_A}/a.txt": 10,
            f"module-{PLUGIN_B}/b.txt": 20,
        }, "policy": None},
    }
    admin = _admin(client=client)
    deleted = await admin.delete_prefix(BUCKET, f"module-{PLUGIN_A}/")
    assert deleted == 1
    assert client.buckets[BUCKET]["objects"] == {f"module-{PLUGIN_B}/b.txt": 20}


@pytest.mark.asyncio
async def test_delete_prefix_rejects_wide_prefix() -> None:
    """Пустой/широкий префикс → отказ, а не «удалим всё в бакете»."""
    admin = _admin()
    for bad in ("", "/", "tenant-", f"module-{PLUGIN_A}", "module-/"):
        with pytest.raises(err.ObjectAccessDenied):
            await admin.delete_prefix(BUCKET, bad)


@pytest.mark.asyncio
async def test_list_prefix_and_usage_are_metadata_only() -> None:
    """Листинг/usage возвращают метаданные, НЕ bytes, и только свой префикс."""
    client = FakeS3Client()
    client.buckets = {
        BUCKET: {"objects": {
            f"module-{PLUGIN_A}/a.txt": 10,
            f"module-{PLUGIN_B}/b.txt": 20,
        }, "policy": None},
    }
    admin = _admin(client=client)
    infos = await admin.list_prefix(BUCKET, f"module-{PLUGIN_A}/")
    assert [i.key for i in infos] == [f"module-{PLUGIN_A}/a.txt"]
    assert all(isinstance(i, ports.ObjectInfo) for i in infos)
    assert await admin.get_usage(BUCKET) == 30


# ===========================================================================
# Config: fail-closed, запрет root, отдельный presigner
# ===========================================================================
def test_config_from_env_ok() -> None:
    config = cfgmod.MinioPluginConfig.from_env(make_env())
    assert config.endpoint == ENDPOINT
    assert config.sts_enabled is False
    assert config.admin_access_key != config.presign_access_key
    assert config.presign_endpoint == ENDPOINT
    # Секреты не попадают в repr.
    assert ADMIN_SECRET not in repr(config)
    assert PRESIGN_SECRET not in repr(config)


@pytest.mark.parametrize(
    "missing",
    [
        "PLUGIN_MINIO_ENDPOINT",
        "PLUGIN_MINIO_ADMIN_ACCESS_KEY",
        "PLUGIN_MINIO_ADMIN_SECRET_KEY",
        "PLUGIN_MINIO_PRESIGN_ACCESS_KEY",
        "PLUGIN_MINIO_PRESIGN_SECRET_KEY",
    ],
)
def test_config_without_credentials_fails_closed(missing: str) -> None:
    """Нет любого обязательного кредика → ошибка, НЕ фолбэк на root/S3_*."""
    env = make_env()
    env.pop(missing)
    # Даже если «рядом» лежат root/as-built значения — они НЕ читаются.
    env["MINIO_ROOT_USER"] = "agentscale"
    env["MINIO_ROOT_PASSWORD"] = "agentscale123"
    env["S3_BUCKET"] = "agentscale"
    with pytest.raises(err.BucketUnavailable):
        cfgmod.MinioPluginConfig.from_env(env)


def test_config_rejects_root_and_as_built_access_keys() -> None:
    for bad in ("agentscale", "agentscale-api", "root", "minio", "minioadmin"):
        with pytest.raises(err.BucketUnavailable):
            cfgmod.MinioPluginConfig.from_env(
                make_env(PLUGIN_MINIO_ADMIN_ACCESS_KEY=bad)
            )
        with pytest.raises(err.BucketUnavailable):
            cfgmod.MinioPluginConfig.from_env(
                make_env(PLUGIN_MINIO_PRESIGN_ACCESS_KEY=bad)
            )


def test_config_requires_distinct_admin_and_presigner_principals() -> None:
    """Один ключ на admin и подпись = широкая политика as-built → отказ."""
    with pytest.raises(err.BucketUnavailable):
        cfgmod.MinioPluginConfig.from_env(
            make_env(PLUGIN_MINIO_PRESIGN_ACCESS_KEY=ADMIN_KEY)
        )


def test_config_unknown_sts_flag_is_error() -> None:
    """Неопознанный флаг STS → ошибка, а не молчаливый False/True."""
    with pytest.raises(err.BucketUnavailable):
        cfgmod.MinioPluginConfig.from_env(
            make_env(PLUGIN_MINIO_STS_ENABLED="maybe")
        )


def test_config_rejects_endpoint_with_credentials_or_path() -> None:
    for bad in (
        "http://user:pass@minio:9000",
        "http://minio:9000/path",
        "ftp://minio:9000",
        "not-a-url",
    ):
        with pytest.raises(err.BucketUnavailable):
            cfgmod.MinioPluginConfig.from_env(make_env(PLUGIN_MINIO_ENDPOINT=bad))


def test_config_public_endpoint_used_for_presign_only() -> None:
    config = cfgmod.MinioPluginConfig.from_env(
        make_env(PLUGIN_MINIO_PUBLIC_ENDPOINT=PUBLIC_ENDPOINT)
    )
    assert config.endpoint == ENDPOINT          # админ ходит in-cluster
    assert config.presign_endpoint == PUBLIC_ENDPOINT  # подпись на внешний


# ===========================================================================
# Квоты: fail-closed без Admin API (НЕ безлимит)
# ===========================================================================
@pytest.mark.asyncio
async def test_quota_unavailable_is_fail_closed_not_unlimited() -> None:
    admin = _admin(quota_admin=adapters.UnavailableQuotaAdmin())
    with pytest.raises(err.BucketUnavailable):
        await admin.set_quota(BUCKET, 1024)
    with pytest.raises(err.BucketUnavailable):
        await admin.ensure_bucket(
            BUCKET, policy=iam.bucket_policy(COMPANY, PLUGIN_A), quota_bytes=1024
        )


@pytest.mark.asyncio
async def test_set_quota_rejects_non_positive() -> None:
    admin = _admin()
    for bad in (0, -1, "abc"):
        with pytest.raises(err.BucketUnavailable):
            await admin.set_quota(BUCKET, bad)


# ===========================================================================
# Контракт без bytes / фабрика
# ===========================================================================
def test_factory_wires_all_ports_and_no_bytes_in_signature() -> None:
    """Фабрика возвращает PluginObjectStore, presigner ОБЯЗАТЕЛЕН."""
    sink = FakeAuditSink()
    store = build_plugin_object_store(
        make_config(), audit_sink=sink, quota_admin=FakeQuotaAdmin(),
        s3_client=FakeS3Client(), presign_client=FakeS3Client(),
    )
    assert isinstance(store, PluginObjectStore)
    assert store._presigner is not None
    assert store._sts is not None
    assert store._audit is not None
    # Ни один публичный метод адаптеров не аннотирован bytes.
    import inspect
    for cls in (
        adapters.MinioBucketAdmin, adapters.MinioSts,
        adapters.MinioPresigner, adapters.MinioAuditAdapter,
    ):
        for name, method in inspect.getmembers(cls, inspect.iscoroutinefunction):
            if name.startswith("_"):
                continue
            hints = str(inspect.signature(method))
            assert "bytes" not in hints, f"{cls.__name__}.{name}: {hints}"


def test_factory_rejects_foreign_config_type() -> None:
    with pytest.raises(TypeError):
        build_plugin_object_store(object())  # type: ignore[arg-type]


def test_policy_json_has_explicit_deny_for_agentscale() -> None:
    """Статическая проверка НОВОЙ политики (без kubectl/mc)."""
    import pathlib
    here = pathlib.Path(__file__).resolve()
    # Не завязываемся на точную глубину: идём вверх до корня worktree,
    # где лежит infra/k3s/base/platform/.
    path: pathlib.Path | None = None
    for parent in here.parents:
        candidate = parent / "infra" / "k3s" / "base" / "platform" / "plugin-minio-policy.json"
        if candidate.is_file():
            path = candidate
            break
    assert path is not None, "plugin-minio-policy.json не найден от корня worktree"
    raw = json.loads(path.read_text(encoding="utf-8"))
    denies = [s for s in raw["Statement"] if s["Effect"] == "Deny"]
    assert denies, "должен быть явный Deny"
    resources = [r for s in denies for r in s["Resource"]]
    assert "arn:aws:s3:::agentscale/*" in resources
    assert "arn:aws:s3:::agentscale" in resources
    # Ни один Allow не содержит as-built бакет и wildcard Resource.
    for statement in raw["Statement"]:
        if statement["Effect"] != "Allow":
            continue
        for resource in statement["Resource"]:
            assert resource != "*"
            assert not resource.startswith("arn:aws:s3:::agentscale")
