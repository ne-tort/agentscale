"""Security contract for signed auth commands and one-shot password refs."""

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from prodavan.application.auth.credentials import put_registration_password
from prodavan.application.auth.register import (
    AUTH_USER_REGISTER_FAILED,
    AUTH_USER_REGISTERED,
    build_register_command_envelope,
    handle_auth_command_envelope,
    publish_register_command,
)
from prodavan.application.auth.user_admin import FakeUserAdmin
from prodavan.config.settings import settings
from prodavan.core.events.auth_command import (
    AuthCommandConfigurationError,
    AuthCommandProvenanceError,
    AuthCommandRejected,
)
from prodavan.core.events.envelope import auth_command_envelope
from prodavan.core.infra.kafka_manager import KafkaManager
from prodavan.domain.errors import AppError
from tests.fixtures.auth_command_bus import TEST_AUTH_COMMAND_SECRET, FakeCredentialRedis

PASSWORD = "correct-horse-battery-staple-secret"


@pytest.fixture
def credential_redis(_auth_command_bus_isolation: FakeCredentialRedis) -> FakeCredentialRedis:
    return _auth_command_bus_isolation


@pytest.fixture
def admin(monkeypatch: pytest.MonkeyPatch) -> FakeUserAdmin:
    fake = FakeUserAdmin()
    monkeypatch.setattr("prodavan.application.auth.register.get_user_admin", lambda: fake)
    return fake


@pytest.fixture
async def local_bus(monkeypatch: pytest.MonkeyPatch):
    manager = KafkaManager(enabled=False)

    async def _no_bind(payload: dict) -> None:
        return None

    monkeypatch.setattr(manager, "_apply_auth_bind", _no_bind)
    await manager.startup()
    yield manager
    await manager.shutdown()


def _command(*, password_ref: str | None, realm_roles: list[str] | None = None):
    return build_register_command_envelope(
        client_ref="employee:emp-1",
        username="employee@example.test",
        email="employee@example.test",
        password_ref=password_ref,
        realm_roles=realm_roles or ["employee"],
        display_name="Employee",
        request_id="request-1",
    )


@pytest.mark.asyncio
async def test_no_broker_payload_has_only_one_shot_ref(
    local_bus: KafkaManager,
    admin: FakeUserAdmin,
    credential_redis: FakeCredentialRedis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    assert await publish_register_command(
        client_ref="employee:emp-1",
        username="employee@example.test",
        email="employee@example.test",
        password=PASSWORD,
        realm_roles=["employee"],
        display_name="Employee",
    )

    command = local_bus.recent_envelopes()[0]
    assert command["bus"] == "auth_command"
    assert "password" not in command["payload"]
    password_ref = command["payload"]["password_ref"]
    assert isinstance(password_ref, str) and len(password_ref) == 43
    assert command["signature"].startswith("v1.")
    assert PASSWORD not in json.dumps(local_bus.recent_envelopes())
    assert admin.registrations[0]["password"] == PASSWORD
    # Only the auth-event replay marker may remain; the password was GETDEL'ed.
    assert PASSWORD not in credential_redis.values.values()
    assert credential_redis.getdel_calls == 1
    assert PASSWORD not in caplog.text
    assert password_ref not in caplog.text


@pytest.mark.asyncio
@pytest.mark.parametrize("tamper", ["username", "realm_roles", "password_ref", "signature"])
async def test_tampered_command_never_reaches_register(
    tamper: str,
    admin: FakeUserAdmin,
    credential_redis: FakeCredentialRedis,
) -> None:
    password_ref = await put_registration_password(PASSWORD)
    envelope = _command(password_ref=password_ref)
    if tamper == "username":
        envelope.payload["username"] = "attacker@example.test"
    elif tamper == "realm_roles":
        envelope.payload["realm_roles"] = ["admin"]
    elif tamper == "password_ref":
        envelope.payload["password_ref"] = "A" * 43
    else:
        envelope.signature = "v1." + "0" * 64

    with pytest.raises(AuthCommandProvenanceError):
        await handle_auth_command_envelope(envelope)
    assert admin.registrations == []


@pytest.mark.asyncio
async def test_expired_ref_is_rejected_without_register(
    admin: FakeUserAdmin, credential_redis: FakeCredentialRedis
) -> None:
    password_ref = await put_registration_password(PASSWORD, ttl_seconds=5)
    credential_redis.advance(6)
    with pytest.raises(AuthCommandRejected) as caught:
        await handle_auth_command_envelope(_command(password_ref=password_ref))
    assert caught.value.code == "AUTH_CREDENTIAL_EXPIRED_OR_REUSED"
    assert admin.registrations == []


@pytest.mark.asyncio
async def test_reused_ref_is_rejected_without_second_register(
    admin: FakeUserAdmin, credential_redis: FakeCredentialRedis
) -> None:
    password_ref = await put_registration_password(PASSWORD)
    envelope = _command(password_ref=password_ref)
    first = await handle_auth_command_envelope(envelope)
    assert first is not None and first.event_type == AUTH_USER_REGISTERED

    with pytest.raises(AuthCommandRejected) as caught:
        await handle_auth_command_envelope(envelope)
    assert caught.value.code == "AUTH_CREDENTIAL_EXPIRED_OR_REUSED"
    assert len(admin.registrations) == 1
    assert credential_redis.getdel_calls == 2


@pytest.mark.asyncio
async def test_transient_keycloak_failure_retries_without_ref_reuse(
    monkeypatch: pytest.MonkeyPatch,
    admin: FakeUserAdmin,
    credential_redis: FakeCredentialRedis,
    caplog: pytest.LogCaptureFixture,
) -> None:
    class FlakyAdmin:
        def __init__(self) -> None:
            self.calls = 0

        async def register_user(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                # Even a hostile exception detail must not reach logs/events.
                raise AppError(
                    code="KEYCLOAK_ADMIN",
                    title="temporary",
                    status=502,
                    detail=f"upstream echoed {PASSWORD}",
                )
            return await admin.register_user(**kwargs)

    flaky = FlakyAdmin()
    monkeypatch.setattr("prodavan.application.auth.register.get_user_admin", lambda: flaky)
    monkeypatch.setattr(settings, "auth_registration_retry_attempts", 2)
    monkeypatch.setattr(settings, "auth_registration_retry_delay_seconds", 0.0)
    password_ref = await put_registration_password(PASSWORD)

    result = await handle_auth_command_envelope(_command(password_ref=password_ref))
    assert result is not None and result.event_type == AUTH_USER_REGISTERED
    assert flaky.calls == 2
    assert credential_redis.getdel_calls == 1
    assert credential_redis.values == {}
    assert PASSWORD not in caplog.text
    assert password_ref not in caplog.text


@pytest.mark.asyncio
async def test_exhausted_transient_failure_requires_new_ref(
    monkeypatch: pytest.MonkeyPatch, credential_redis: FakeCredentialRedis
) -> None:
    class DownAdmin:
        def __init__(self) -> None:
            self.calls = 0

        async def register_user(self, **_kwargs):
            self.calls += 1
            raise AppError(code="KEYCLOAK_ADMIN", title="temporary", status=502)

    down = DownAdmin()
    monkeypatch.setattr("prodavan.application.auth.register.get_user_admin", lambda: down)
    monkeypatch.setattr(settings, "auth_registration_retry_attempts", 2)
    monkeypatch.setattr(settings, "auth_registration_retry_delay_seconds", 0.0)
    password_ref = await put_registration_password(PASSWORD)

    result = await handle_auth_command_envelope(_command(password_ref=password_ref))
    assert result is not None and result.event_type == AUTH_USER_REGISTER_FAILED
    assert result.payload["retryable"] is True
    assert down.calls == 2
    assert credential_redis.getdel_calls == 1
    assert credential_redis.values == {}


@pytest.mark.asyncio
async def test_signed_but_non_internal_role_is_still_rejected(admin: FakeUserAdmin) -> None:
    password_ref = await put_registration_password(PASSWORD)
    envelope = _command(password_ref=password_ref, realm_roles=["admin"])
    with pytest.raises(AuthCommandRejected) as caught:
        await handle_auth_command_envelope(envelope)
    assert caught.value.code == "AUTH_REGISTRATION_PATH_FORBIDDEN"
    assert admin.registrations == []


@pytest.mark.asyncio
async def test_retained_unsigned_plaintext_command_goes_to_sanitized_dlq(
    local_bus: KafkaManager,
    admin: FakeUserAdmin,
    caplog: pytest.LogCaptureFixture,
) -> None:
    retained = {
        "bus": "auth_command",
        "event_id": PASSWORD,
        "event_type": "auth.user.register",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "schema_version": 1,
        "payload": {
            "request_id": "legacy-request",
            "client_ref": "employee:emp-1",
            "username": "employee@example.test",
            "email": "employee@example.test",
            "password": PASSWORD,
            "password_ref": "legacy-sensitive-ref",
            "realm_roles": ["admin"],
        },
    }

    assert await local_bus._process_auth_command_data(deepcopy(retained)) is False
    assert admin.registrations == []
    assert local_bus.auth_commands_rejected == 1
    assert local_bus.auth_commands_dlq == 1
    dlq = local_bus.recent_auth_command_dlq()
    assert dlq[0]["reason"] == "AUTH_COMMAND_SIGNATURE_MISSING"
    assert "payload" not in dlq[0]
    assert PASSWORD not in json.dumps(dlq)
    assert "legacy-sensitive-ref" not in json.dumps(dlq)
    assert PASSWORD not in caplog.text
    assert "legacy-sensitive-ref" not in caplog.text


@pytest.mark.asyncio
async def test_broker_rejection_is_written_to_dlq_topic(admin: FakeUserAdmin) -> None:
    class Producer:
        def __init__(self) -> None:
            self.sent: list[tuple[str, bytes, bytes]] = []

        async def send_and_wait(self, topic: str, *, value: bytes, key: bytes) -> None:
            self.sent.append((topic, value, key))

    producer = Producer()
    manager = KafkaManager(
        enabled=True,
        bootstrap_servers="kafka:9092",
        auth_command_signing_secret=TEST_AUTH_COMMAND_SECRET,
    )
    manager._producer = producer
    retained = {
        "bus": "auth_command",
        "event_id": "legacy-request",
        "event_type": "auth.user.register",
        "occurred_at": "2026-01-01T00:00:00+00:00",
        "schema_version": 1,
        "payload": {"password": PASSWORD},
    }
    assert await manager._process_auth_command_data(retained) is False
    assert admin.registrations == []
    assert len(producer.sent) == 1
    topic, value, _key = producer.sent[0]
    assert topic == "prodavan.auth.commands.dlq"
    assert PASSWORD not in value.decode("utf-8")
    assert manager.auth_commands_dlq == 1


@pytest.mark.asyncio
async def test_correctly_signed_plaintext_is_rejected_before_local_or_broker_publish(
    local_bus: KafkaManager, admin: FakeUserAdmin, caplog: pytest.LogCaptureFixture
) -> None:
    envelope = auth_command_envelope(
        event_id="legacy-signed",
        event_type="auth.user.register",
        payload={
            "request_id": "legacy-signed",
            "client_ref": "employee:emp-1",
            "username": "employee@example.test",
            "email": "employee@example.test",
            "password": PASSWORD,
            "realm_roles": ["employee"],
        },
    )
    assert await local_bus.publish(envelope) is False
    assert local_bus.recent_envelopes() == []
    assert local_bus.recent_auth_command_dlq()[0]["reason"] == "AUTH_PLAINTEXT_PASSWORD_FORBIDDEN"
    assert admin.registrations == []
    assert PASSWORD not in caplog.text


def test_missing_dedicated_secret_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "auth_command_signing_secret", "")
    with pytest.raises(AuthCommandConfigurationError):
        _command(password_ref=None)
