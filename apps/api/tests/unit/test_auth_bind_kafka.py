"""Unit — KafkaManager applies auth bind inline (no Celery gate)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from prodavan.core.events.envelope import auth_event_envelope
from prodavan.core.infra.kafka_manager import KafkaManager, set_kafka_manager


@pytest.mark.asyncio
async def test_local_auth_event_binds_inline(monkeypatch: pytest.MonkeyPatch) -> None:
    set_kafka_manager(None)
    mgr = KafkaManager(enabled=False)
    await mgr.startup()

    calls: list[dict] = []

    async def _apply(session, payload):
        calls.append(dict(payload))
        return {"ok": True, "bound": True}

    class _SessionCtx:
        async def __aenter__(self):
            return MagicMock()

        async def __aexit__(self, *a):
            return None

    factory = MagicMock(return_value=_SessionCtx())

    monkeypatch.setattr(
        "prodavan.application.identity.auth_bind.apply_auth_user_registered_payload",
        _apply,
    )
    monkeypatch.setattr(
        "prodavan.infrastructure.persistence.database.get_session_factory",
        lambda: factory,
    )

    env = auth_event_envelope(
        event_id="e1",
        event_type="auth.user.registered",
        payload={"client_ref": "company:co_1", "sub": "kc_1"},
    )
    await mgr.publish(env)
    assert len(calls) == 1
    assert calls[0]["client_ref"] == "company:co_1"
    assert mgr._auth_bind_enqueues == 1
    await mgr.shutdown()
