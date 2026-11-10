"""Provenance, freshness and one-time identity binding for auth.events."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from prodavan.application.auth.register import RegisterUserCommand, register_user
from prodavan.application.auth.user_admin import FakeUserAdmin
from prodavan.config.settings import settings
from prodavan.core.events.auth_event import sign_auth_event_envelope
from prodavan.core.events.envelope import auth_event_envelope
from prodavan.core.infra.kafka_manager import KafkaManager
from tests.fixtures.auth_command_bus import TEST_AUTH_COMMAND_SECRET, TEST_AUTH_EVENT_SECRET


@pytest.fixture
def bind_calls() -> list[dict]:
    return []


@pytest.fixture
def event_bus(monkeypatch: pytest.MonkeyPatch, bind_calls: list[dict]) -> KafkaManager:
    manager = KafkaManager(enabled=False, auth_event_signing_secret=TEST_AUTH_EVENT_SECRET)

    async def _bind(payload: dict) -> None:
        bind_calls.append(dict(payload))

    monkeypatch.setattr(manager, "_apply_auth_bind", _bind)
    return manager


def _registered(*, occurred_at: str | None = None):
    envelope = auth_event_envelope(
        event_id="registered-event-1",
        event_type="auth.user.registered",
        occurred_at=occurred_at,
        payload={
            "request_id": "request-1",
            "client_ref": "employee:emp-1",
            "sub": "kc-user-1",
            "username": "employee@example.test",
            "email": "employee@example.test",
            "realm_roles": ["employee"],
        },
    )
    return sign_auth_event_envelope(envelope, signing_secret=TEST_AUTH_EVENT_SECRET)


@pytest.mark.asyncio
async def test_forged_unsigned_registered_never_binds_and_uses_sanitized_dlq(
    event_bus: KafkaManager, bind_calls: list[dict], caplog: pytest.LogCaptureFixture
) -> None:
    forged = auth_event_envelope(
        event_id="forged-secret-id",
        event_type="auth.user.registered",
        payload={
            "request_id": "forged",
            "client_ref": "employee:secret-client-ref",
            "sub": "secret-sub",
            "realm_roles": ["admin"],
        },
    ).to_dict()
    assert await event_bus._process_auth_event_data(forged) is False
    assert bind_calls == []
    assert event_bus.auth_events_rejected == 1
    record = event_bus.recent_auth_event_dlq()[0]
    assert record["reason"] == "AUTH_EVENT_SIGNATURE_MISSING"
    assert "payload" not in record
    assert "secret-client-ref" not in json.dumps(record)
    assert "secret-sub" not in json.dumps(record)
    assert "secret-client-ref" not in caplog.text
    assert "secret-sub" not in caplog.text
    assert "forged-secret-id" not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["client_ref", "sub", "realm_roles"])
async def test_tampered_registered_fields_never_bind(
    field: str, event_bus: KafkaManager, bind_calls: list[dict]
) -> None:
    data = _registered().to_dict()
    if field == "realm_roles":
        data["payload"][field] = ["admin"]
    elif field == "client_ref":
        data["payload"][field] = "company:attacker"
    else:
        data["payload"][field] = "kc-attacker"

    assert await event_bus._process_auth_event_data(data) is False
    assert bind_calls == []
    assert event_bus.recent_auth_event_dlq()[0]["reason"] == "AUTH_EVENT_SIGNATURE_INVALID"


@pytest.mark.asyncio
async def test_expired_registered_never_binds(event_bus: KafkaManager, bind_calls: list[dict]) -> None:
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    data = _registered(occurred_at=old).to_dict()
    assert await event_bus._process_auth_event_data(data) is False
    assert bind_calls == []
    assert event_bus.recent_auth_event_dlq()[0]["reason"] == "AUTH_EVENT_EXPIRED"


@pytest.mark.asyncio
async def test_replayed_registered_binds_at_most_once(
    event_bus: KafkaManager, bind_calls: list[dict]
) -> None:
    data = _registered().to_dict()
    assert await event_bus._process_auth_event_data(deepcopy(data)) is True
    assert len(bind_calls) == 1

    assert await event_bus._process_auth_event_data(deepcopy(data)) is False
    assert len(bind_calls) == 1
    assert event_bus.recent_auth_event_dlq()[0]["reason"] == "AUTH_EVENT_REPLAYED"
    assert event_bus.auth_events_dlq == 1


@pytest.mark.asyncio
async def test_local_producer_signs_and_replay_does_not_bind_twice(
    event_bus: KafkaManager, bind_calls: list[dict]
) -> None:
    # Domain result is unsigned; KafkaManager is the trusted producer edge.
    envelope = auth_event_envelope(
        event_id="local-registered-1",
        event_type="auth.user.registered",
        payload={"client_ref": "employee:emp-1", "sub": "kc-user-1"},
    )
    assert envelope.signature is None
    assert await event_bus.publish(envelope) is True
    assert envelope.signature is not None and envelope.signature.startswith("v1.")
    assert len(bind_calls) == 1

    assert await event_bus.publish(envelope) is True
    assert len(bind_calls) == 1
    assert event_bus.recent_auth_event_dlq()[0]["reason"] == "AUTH_EVENT_REPLAYED"


@pytest.mark.asyncio
async def test_other_trusted_auth_events_are_signed_but_do_not_bind(
    event_bus: KafkaManager, bind_calls: list[dict]
) -> None:
    envelope = auth_event_envelope(
        event_id="disabled-1",
        event_type="auth.user.disabled",
        payload={"client_ref": "employee:emp-1", "sub": "kc-user-1"},
    )
    assert await event_bus.publish(envelope) is True
    assert envelope.signature is not None
    assert bind_calls == []
    assert event_bus.recent_auth_event_dlq() == []


@pytest.mark.asyncio
async def test_command_key_cannot_sign_auth_event(event_bus: KafkaManager, bind_calls: list[dict]) -> None:
    envelope = auth_event_envelope(
        event_id="wrong-key-1",
        event_type="auth.user.registered",
        payload={"client_ref": "employee:emp-1", "sub": "kc-attacker"},
    )
    sign_auth_event_envelope(envelope, signing_secret=TEST_AUTH_COMMAND_SECRET)
    assert await event_bus._process_auth_event_data(envelope.to_dict()) is False
    assert bind_calls == []


@pytest.mark.asyncio
async def test_broker_forged_event_is_written_to_auth_events_dlq() -> None:
    class Producer:
        def __init__(self) -> None:
            self.sent: list[tuple[str, bytes]] = []

        async def send_and_wait(self, topic: str, *, value: bytes, key: bytes) -> None:
            self.sent.append((topic, value))

    producer = Producer()
    manager = KafkaManager(
        enabled=True,
        bootstrap_servers="kafka:9092",
        auth_event_signing_secret=TEST_AUTH_EVENT_SECRET,
    )
    manager._producer = producer
    forged = auth_event_envelope(
        event_id="forged-1",
        event_type="auth.user.registered",
        payload={"client_ref": "employee:secret-ref", "sub": "secret-sub"},
    ).to_dict()
    assert await manager._process_auth_event_data(forged) is False
    assert len(producer.sent) == 1
    topic, value = producer.sent[0]
    assert topic == "prodavan.auth.events.dlq"
    assert "secret-ref" not in value.decode("utf-8")
    assert "secret-sub" not in value.decode("utf-8")
    assert manager.auth_events_dlq == 1


@pytest.mark.asyncio
async def test_missing_event_secret_fails_bus_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[dict] = []
    manager = KafkaManager(enabled=False)

    async def _bind(payload: dict) -> None:
        calls.append(payload)

    monkeypatch.setattr(manager, "_apply_auth_bind", _bind)
    monkeypatch.setattr(settings, "auth_event_signing_secret", "")
    envelope = auth_event_envelope(
        event_id="missing-secret-1",
        event_type="auth.user.registered",
        payload={"client_ref": "employee:emp-1", "sub": "kc-user-1"},
    )
    assert await manager.publish(envelope) is False
    assert calls == []
    assert manager.recent_auth_event_dlq()[0]["reason"] == "AUTH_EVENT_SIGNING_REQUIRED"


@pytest.mark.asyncio
async def test_direct_register_user_needs_no_event_signing_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeUserAdmin()
    monkeypatch.setattr("prodavan.application.auth.register.get_user_admin", lambda: fake)
    monkeypatch.setattr(settings, "auth_event_signing_secret", "")
    result = await register_user(
        RegisterUserCommand(
            request_id="direct-1",
            client_ref="employee:emp-1",
            username="employee@example.test",
            email="employee@example.test",
            password="typed-direct-password",
            realm_roles=["employee"],
            display_name=None,
        )
    )
    assert result.event_type == "auth.user.registered"
    assert result.signature is None
