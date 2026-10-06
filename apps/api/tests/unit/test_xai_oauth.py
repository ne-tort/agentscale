"""xAI (Grok) OAuth: device-code flow + серверный refresh токенов.

HTTP (auth.x.ai) и Redis мокаются: проверяем контракт состояний, хранение
блоба токенов в секрете ключа, ротацию refresh_token, single-flight skew и
деактивацию ключа при invalid_grant.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

import prodavan.application.ai_keys.oauth.xai_oauth as xai
from prodavan.application.ai_keys.oauth.xai_oauth import (
    XaiOAuthService,
    parse_token_blob,
    token_blob,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow

PRINCIPAL = Principal(sub="u1", roles=frozenset())


class FakeSecrets:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def put(self, key_id: str, secret: str) -> str:
        ref = f"file://ai_keys/{key_id}.secret"
        self.values[ref] = secret
        return ref

    def get(self, secret_ref: str) -> str:
        return self.values.get(secret_ref, "")

    def delete(self, secret_ref: str) -> None:
        self.values.pop(secret_ref, None)


class FakeAudit:
    def __init__(self) -> None:
        self.events: list[tuple[str, str | None]] = []

    async def record(self, *, event_type: str, key_id: str | None, principal: Any, detail: Any = None) -> None:
        self.events.append((event_type, key_id))


class FakeSession:
    def __init__(self, row: AiProviderKeyRow | None) -> None:
        self._row = row
        self.committed = 0

    async def get(self, model: Any, key: str) -> Any:
        return self._row

    async def commit(self) -> None:
        self.committed += 1


class FakeRedis:
    """in-memory замена core.infra.cache (patch в модуле xai_oauth)."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, *, ttl_sec: int | None = 300) -> bool:
        self.store[key] = value
        return True

    async def delete(self, key: str) -> bool:
        return self.store.pop(key, None) is not None


def _key_row(key_id: str = "aik_grok1", api_kind: str = "xai_oauth", secret_ref: str = "") -> AiProviderKeyRow:
    return AiProviderKeyRow(
        id=key_id,
        name="Grok",
        provider="xai",
        api_kind=api_kind,
        owner_scope="company",
        owner_company_id="co1",
        secret_ref=secret_ref,
        status="disabled",
    )


def _service(row: AiProviderKeyRow | None, secrets: FakeSecrets | None = None):
    secrets = secrets or FakeSecrets()
    audit = FakeAudit()
    svc = XaiOAuthService(FakeSession(row), secrets=secrets, audit=audit)  # type: ignore[arg-type]
    return svc, secrets, audit


@pytest.fixture
def fake_redis(monkeypatch: pytest.MonkeyPatch) -> FakeRedis:
    redis = FakeRedis()

    async def _get(key: str) -> str | None:
        return await redis.get(key)

    async def _set(key: str, value: str, *, ttl_sec: int | None = 300) -> bool:
        return await redis.set(key, value, ttl_sec=ttl_sec)

    async def _delete(key: str) -> bool:
        return await redis.delete(key)

    monkeypatch.setattr(xai, "cache_get", _get)
    monkeypatch.setattr(xai, "cache_set", _set)
    monkeypatch.setattr(xai, "cache_delete", _delete)
    return redis


def _script_posts(monkeypatch: pytest.MonkeyPatch, responses: list[tuple[int, dict, str]]) -> list[dict]:
    calls: list[dict] = []
    queue = list(responses)

    async def _post_form(url: str, data: dict[str, str]) -> tuple[int, dict[str, Any], str]:
        calls.append({"url": url, "data": data})
        if not queue:
            raise AssertionError("unexpected extra HTTP call")
        return queue.pop(0)

    monkeypatch.setattr(xai, "_post_form", _post_form)
    return calls


# ---------------------------------------------------------------- device flow


@pytest.mark.asyncio
async def test_start_device_flow_returns_link_and_code(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    svc, _secrets, audit = _service(_key_row())
    _script_posts(monkeypatch, [(200, {
        "device_code": "dev_secret_code",
        "user_code": "GROK-1234",
        "verification_uri": "https://x.ai/activate",
        "verification_uri_complete": "https://x.ai/activate?code=GROK-1234",
        "interval": 5,
        "expires_in": 600,
    }, "")])

    out = await svc.start_device_flow("aik_grok1", principal=PRINCIPAL)

    assert out["status"] == "pending"
    assert out["user_code"] == "GROK-1234"
    assert out["verification_uri_complete"] == "https://x.ai/activate?code=GROK-1234"
    # device_code не покидает сервер
    assert "device_code" not in out
    assert ("ai_key.oauth_started", "aik_grok1") in audit.events
    stored = json.loads(fake_redis.store[xai._redis_key("aik_grok1")])
    assert stored["device_code"] == "dev_secret_code"


@pytest.mark.asyncio
async def test_start_device_flow_rejects_non_xai_key(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    svc, _, _ = _service(_key_row(api_kind="openai_api"))
    _script_posts(monkeypatch, [])
    with pytest.raises(AppError) as ei:
        await svc.start_device_flow("aik_grok1", principal=PRINCIPAL)
    assert ei.value.code == "OAUTH_NOT_SUPPORTED"


@pytest.mark.asyncio
async def test_device_flow_pending_then_authorized(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    row = _key_row()
    svc, secrets, audit = _service(row)
    _script_posts(monkeypatch, [
        (200, {
            "device_code": "dc",
            "user_code": "ABCD",
            "verification_uri": "https://x.ai/activate",
            "interval": 1,
            "expires_in": 600,
        }, ""),
        # первый poll: пользователь ещё не подтвердил
        (400, {"error": "authorization_pending"}, ""),
        # второй poll: токены
        (200, {
            "access_token": "acc_1",
            "refresh_token": "ref_1",
            "expires_in": 3600,
        }, ""),
    ])
    await svc.start_device_flow("aik_grok1", principal=PRINCIPAL)

    out = await svc.device_flow_status("aik_grok1", principal=PRINCIPAL)
    assert out["status"] == "pending"

    # interval=1s: немедленно снова — ждём next_poll; сдвигаем время через sleep-симуляцию
    state = json.loads(fake_redis.store[xai._redis_key("aik_grok1")])
    state["next_poll_epoch"] = 0.0
    fake_redis.store[xai._redis_key("aik_grok1")] = json.dumps(state)

    out = await svc.device_flow_status("aik_grok1", principal=PRINCIPAL)
    assert out["status"] == "authorized"

    # токены — в секрете ключа (JSON-блоб), ключ активирован
    blob = parse_token_blob(secrets.values["file://ai_keys/aik_grok1.secret"])
    assert blob is not None
    assert blob["access_token"] == "acc_1"
    assert blob["refresh_token"] == "ref_1"
    assert row.status == "active"
    assert row.secret_ref == "file://ai_keys/aik_grok1.secret"
    assert ("ai_key.oauth_authorized", "aik_grok1") in audit.events
    # стейт очищен, повторный статус — none
    assert await svc.device_flow_status("aik_grok1", principal=PRINCIPAL) == {"status": "none"}


@pytest.mark.asyncio
async def test_device_flow_denied_and_slow_down(
    monkeypatch: pytest.MonkeyPatch, fake_redis: FakeRedis
) -> None:
    svc, _, _ = _service(_key_row())
    _script_posts(monkeypatch, [
        (200, {"device_code": "dc", "user_code": "X", "verification_uri": "u", "interval": 5, "expires_in": 600}, ""),
        (400, {"error": "slow_down"}, ""),
        (400, {"error": "access_denied"}, ""),
    ])
    await svc.start_device_flow("k", principal=PRINCIPAL)

    out = await svc.device_flow_status("k", principal=PRINCIPAL)
    assert out["status"] == "pending"
    assert out["interval_sec"] == 5 + xai.DEVICE_SLOW_DOWN_INCREMENT_SEC

    state = json.loads(fake_redis.store[xai._redis_key("k")])
    state["next_poll_epoch"] = 0.0
    fake_redis.store[xai._redis_key("k")] = json.dumps(state)
    out = await svc.device_flow_status("k", principal=PRINCIPAL)
    assert out["status"] == "denied"


# ------------------------------------------------------------------- refresh


@pytest.mark.asyncio
async def test_ensure_fresh_returns_valid_token_without_http(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = FakeSecrets()
    expires = (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat()
    ref = secrets.put("aik_grok1", json.dumps({
        "v": 1, "access_token": "acc", "refresh_token": "ref", "id_token": None, "expires_at": expires,
    }))
    row = _key_row(secret_ref=ref)
    svc, _, _ = _service(row, secrets)
    _script_posts(monkeypatch, [])  # ни одного HTTP-вызова

    out = await svc.ensure_fresh_access_token(row, secrets.get(ref))
    assert out == "acc"


@pytest.mark.asyncio
async def test_ensure_fresh_rotates_refresh_token(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = FakeSecrets()
    # токен истекает через 60s < skew 120s → refresh
    expires = (datetime.now(tz=UTC) + timedelta(seconds=60)).isoformat()
    ref = secrets.put("aik_grok1", json.dumps({
        "v": 1, "access_token": "old_acc", "refresh_token": "old_ref", "id_token": None, "expires_at": expires,
    }))
    row = _key_row(secret_ref=ref)
    svc, _, _ = _service(row, secrets)
    calls = _script_posts(monkeypatch, [(200, {
        "access_token": "new_acc",
        "refresh_token": "new_ref",
        "expires_in": 3600,
    }, "")])

    out = await svc.ensure_fresh_access_token(row, secrets.get(ref))
    assert out == "new_acc"
    assert calls[0]["data"]["grant_type"] == "refresh_token"
    assert calls[0]["data"]["refresh_token"] == "old_ref"
    blob = parse_token_blob(secrets.get(ref))
    assert blob is not None and blob["refresh_token"] == "new_ref"


@pytest.mark.asyncio
async def test_ensure_fresh_invalid_grant_disables_key(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = FakeSecrets()
    expires = (datetime.now(tz=UTC) - timedelta(seconds=1)).isoformat()
    ref = secrets.put("aik_grok1", json.dumps({
        "v": 1, "access_token": "acc", "refresh_token": "ref", "id_token": None, "expires_at": expires,
    }))
    row = _key_row(secret_ref=ref)
    row.status = "active"
    svc, _, audit = _service(row, secrets)
    _script_posts(monkeypatch, [(400, {"error": "invalid_grant"}, "")])

    with pytest.raises(AppError) as ei:
        await svc.ensure_fresh_access_token(row, secrets.get(ref))
    assert ei.value.code == "OAUTH_REAUTH_REQUIRED"
    assert row.status == "disabled"
    assert ("ai_key.oauth_revoked", str(row.id)) in audit.events


@pytest.mark.asyncio
async def test_ensure_fresh_transient_error_serves_unexpired(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = FakeSecrets()
    # expires_at уже в прошлом для skew, но ещё не истёк реально → grace
    expires = (datetime.now(tz=UTC) + timedelta(seconds=30)).isoformat()
    ref = secrets.put("aik_grok1", json.dumps({
        "v": 1, "access_token": "acc", "refresh_token": "ref", "id_token": None, "expires_at": expires,
    }))
    row = _key_row(secret_ref=ref)
    row.status = "active"
    svc, _, _ = _service(row, secrets)
    _script_posts(monkeypatch, [(503, {}, "upstream")])

    out = await svc.ensure_fresh_access_token(row, secrets.get(ref))
    assert out == "acc"
    # транзиентная ошибка не гасит ключ
    assert row.status == "active"


@pytest.mark.asyncio
async def test_ensure_fresh_legacy_plain_secret_passthrough(monkeypatch: pytest.MonkeyPatch) -> None:
    secrets = FakeSecrets()
    ref = secrets.put("aik_grok1", "xai-static-api-key")
    row = _key_row(secret_ref=ref)
    svc, _, _ = _service(row, secrets)
    _script_posts(monkeypatch, [])

    out = await svc.ensure_fresh_access_token(row, "xai-static-api-key")
    assert out == "xai-static-api-key"


def test_token_blob_roundtrip() -> None:
    now = datetime(2026, 10, 6, 12, 0, tzinfo=UTC)
    raw = token_blob(access_token="a", refresh_token="r", expires_in=600, now=now)
    blob = parse_token_blob(raw)
    assert blob is not None
    assert blob["expires_at"] == (now + timedelta(seconds=600)).isoformat()
    assert parse_token_blob("not json") is None
    assert parse_token_blob(json.dumps({"v": 1})) is None


# --------------------------------------------------- контуры потребления секрета


@pytest.mark.asyncio
async def test_pod_probe_resolve_secret_returns_access_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """Регрессия: pod-probe пушил в lease ВЕСЬ JSON-блоб как bearer — xAI
    отвечал 400, bridge глушил его в 200 {models: []}. Lease обязан нести
    access_token."""
    from prodavan.application.ai_keys.probe.pod_probe_service import ProbePodService

    secrets = FakeSecrets()
    expires = (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat()
    ref = secrets.put("aik_grok1", json.dumps({
        "v": 1, "access_token": "acc_pod", "refresh_token": "ref", "id_token": None, "expires_at": expires,
    }))
    row = _key_row(secret_ref=ref)
    _script_posts(monkeypatch, [])  # токен свежий — HTTP не нужен

    svc = ProbePodService(FakeSession(row), secrets=secrets)  # type: ignore[arg-type]
    secret = await svc._resolve_secret(row)
    assert secret == "acc_pod"


@pytest.mark.asyncio
async def test_effective_secret_for_row_passthrough_and_xai(monkeypatch: pytest.MonkeyPatch) -> None:
    """Единый резолвер: обычный ключ — как есть, xai_oauth — access_token."""
    from prodavan.application.ai_keys.service import AiKeysService

    secrets = FakeSecrets()
    plain_ref = secrets.put("aik_plain", "sk-static")
    plain_row = _key_row(api_kind="openai_api", secret_ref=plain_ref)
    svc = AiKeysService(FakeSession(plain_row), secrets=secrets)  # type: ignore[arg-type]
    assert await svc.effective_secret_for_row(plain_row) == "sk-static"

    expires = (datetime.now(tz=UTC) + timedelta(minutes=30)).isoformat()
    xai_ref = secrets.put("aik_grok1", json.dumps({
        "v": 1, "access_token": "acc_eff", "refresh_token": "ref", "id_token": None, "expires_at": expires,
    }))
    xai_row = _key_row(secret_ref=xai_ref)
    _script_posts(monkeypatch, [])
    assert await svc.effective_secret_for_row(xai_row) == "acc_eff"
