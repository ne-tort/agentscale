"""Webhook /webhook/ping: HMAC-подпись, 401 без/с неверной подписью,
идемпотентность по X-Prodavan-Delivery (повтор не дублирует обработку).

Критерий 7 §11, §7.5 v4 + docs/external-api.md. Тесты написаны,
но НЕ запускаются (verification freeze).
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi import HTTPException

from .conftest import (
    APP_PATH,
    FakeRequest,
    hmac_signature,
    load_app_module,
    read_text,
)

SECRET = "pilot-webhook-secret"
BODY = b'{"event":"ping","company_id":"co_test"}'


def _call_ping(
    module: Any,
    *,
    body: bytes = BODY,
    signature: str | None,
    delivery: str | None = "delivery-0001",
    event: str | None = "ping",
) -> Any:
    try:
        return asyncio.run(
            module.webhook_ping(
                FakeRequest(body),
                x_prodavan_signature=signature,
                x_prodavan_delivery=delivery,
                x_prodavan_event=event,
            )
        )
    except HTTPException as exc:
        return exc


# --- подпись: 401 без/с неверной ---------------------------------------------

def test_webhook_missing_signature_401(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    result = _call_ping(module, signature=None)
    assert isinstance(result, HTTPException)
    assert result.status_code == 401


def test_webhook_invalid_signature_401(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    result = _call_ping(module, signature="sha256=" + "0" * 64)
    assert isinstance(result, HTTPException)
    assert result.status_code == 401


def test_webhook_tampered_body_invalidates_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    """HMAC считается по RAW body: подмена тела => подпись не сходится."""
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    signature = hmac_signature(SECRET, BODY)
    result = _call_ping(module, body=BODY + b" ", signature=signature)
    assert isinstance(result, HTTPException)
    assert result.status_code == 401


def test_webhook_wrong_secret_invalidates_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    result = _call_ping(module, signature=hmac_signature("другой-секрет", BODY))
    assert isinstance(result, HTTPException)
    assert result.status_code == 401


def test_webhook_missing_delivery_401(monkeypatch: pytest.MonkeyPatch) -> None:
    """Delivery — обязательный idempotency key; без него обработка не начинается."""
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    result = _call_ping(module, signature=hmac_signature(SECRET, BODY), delivery=None)
    assert isinstance(result, HTTPException)
    assert result.status_code == 401


def test_webhook_wrong_signature_prefix_401(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    result = _call_ping(module, signature="md5=deadbeef")
    assert isinstance(result, HTTPException)
    assert result.status_code == 401


def test_webhook_valid_signature_200(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    result = _call_ping(module, signature=hmac_signature(SECRET, BODY))
    assert not isinstance(result, HTTPException)
    assert result.status_code == 200
    assert b'"ok":true' in result.body.replace(b" ", b"")


# --- идемпотентность: повтор delivery не дублирует ---------------------------

def test_webhook_replay_does_not_duplicate_processing(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    signature = hmac_signature(SECRET, BODY)
    first = _call_ping(module, signature=signature, delivery="delivery-0001")
    assert not isinstance(first, HTTPException)
    assert first.status_code == 200
    assert len(module._seen_deliveries) == 1

    second = _call_ping(module, signature=signature, delivery="delivery-0001")
    assert not isinstance(second, HTTPException)
    assert second.status_code == 200
    assert second.body == first.body, "повтор возвращает сохранённый результат"
    assert len(module._seen_deliveries) == 1, "обработка НЕ дублируется"


def test_webhook_replay_with_different_body_same_delivery_is_not_reprocessed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Ретрай outbox может прийти с тем же delivery — результат стабилен."""
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    first = _call_ping(module, signature=hmac_signature(SECRET, BODY), delivery="delivery-0002")
    second = _call_ping(
        module,
        body=b'{"event":"ping","company_id":"co_test","attempt":2}',
        signature=hmac_signature(SECRET, b'{"event":"ping","company_id":"co_test","attempt":2}'),
        delivery="delivery-0002",
    )
    assert second.body == first.body
    assert len(module._seen_deliveries) == 1


def test_webhook_distinct_deliveries_are_processed_once_each(monkeypatch: pytest.MonkeyPatch) -> None:
    module = load_app_module(monkeypatch, webhook_secret=SECRET)
    signature = hmac_signature(SECRET, BODY)
    _call_ping(module, signature=signature, delivery="delivery-0003")
    _call_ping(module, signature=signature, delivery="delivery-0004")
    assert len(module._seen_deliveries) == 2


# --- контракт / sec -----------------------------------------------------------

def test_signature_uses_compare_digest_and_hmac_sha256() -> None:
    raw = read_text(APP_PATH)
    assert "hmac.compare_digest" in raw, "сравнение без утечки по времени"
    assert "hashlib.sha256" in raw
    assert "HMAC(secret, raw_body)" in raw or "raw_body" in raw
    assert "startswith(\"sha256=\")" in raw


def test_webhook_secret_from_env_not_plaintext() -> None:
    raw = read_text(APP_PATH)
    assert "PLUGIN_WEBHOOK_SECRET" in raw
    assert 'os.environ.get("PLUGIN_WEBHOOK_SECRET", "")' in raw
    # Секрет не захардкожен в коде: пустой env => отказ (fail-closed).
    assert 'if not WEBHOOK_SECRET' in raw


def test_webhook_path_matches_manifest() -> None:
    import json
    from .conftest import MANIFEST_PATH, read_text as _read

    manifest = json.loads(_read(MANIFEST_PATH))
    assert manifest["webhooks"] == ["/webhook/ping"]
    assert "@app.post(\"/webhook/ping\")" in read_text(APP_PATH)


def test_webhook_does_not_call_pod_gateway() -> None:
    raw = read_text(APP_PATH)
    assert ":8001" not in raw
    assert "main_pod" not in raw
