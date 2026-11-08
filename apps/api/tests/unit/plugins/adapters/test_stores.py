"""Тесты ``stores``: in-memory реализации чистых контрактов + аудит без raw данных.

Покрытие:
* аудит append-only и НЕ содержит raw SQL/токенов/PII/DSN/payload;
* ``_assert_redacted`` отказывает при попытке протащить запрещённые данные;
* grant store идемпотентен, деактивация по grant_id, изоляция компаний;
* route registry/unregister идемпотентен;
* ``StaticPlatformAdmin`` НЕ «пускает всех»: без/с неверным токеном — отказ;
* явная пометка, что для кластера нужны БД/Redis (задача A10).
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.admin.contracts import (
    AdminAuditEvent,
    AdminDecision,
    AdminIdentity,
    AdminOperation,
    CapabilityGrantRecord,
    CapabilitySet,
    GrantStatus,
)
from prodavan.application.plugins.adapters.stores import (
    InMemoryAdminAudit,
    InMemoryGrantStore,
    InMemoryRouteRegistry,
    StaticPlatformAdmin,
    _assert_redacted,
)

from .fakes import COMPANY, COMPANY_OTHER, DIGEST, INSTANCE, PLUGIN

NOW = datetime(2026, 11, 8, 9, 0, tzinfo=UTC)


def _grant(*, company_id: str = COMPANY, grant_id: str = "grant_001") -> CapabilityGrantRecord:
    caps = CapabilitySet(capabilities=("db.direct", "cache", "objects"))
    return CapabilityGrantRecord(
        grant_id=grant_id,
        company_id=company_id,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        manifest_version="1.2.0",
        artifact_digest=DIGEST,
        capabilities=caps.capabilities,
        egress_policy=caps.egress_policy,
        quotas=caps.quotas,
        status=GrantStatus.ACTIVE,
        created_at=NOW,
    )


def _event(**over: object) -> AdminAuditEvent:
    base = dict(
        event_id="evt-1",
        occurred_at=NOW,
        company_id=COMPANY,
        plugin_id=PLUGIN,
        operation=AdminOperation.GRANT,
        decision=AdminDecision.ALLOW,
        grant_id="grant_001",
        artifact_digest=DIGEST,
        code="OK",
        reason="decision recorded before mutation",
    )
    base.update(over)
    return AdminAuditEvent(**base)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- аудит


async def test_audit_is_append_only_without_raw_data() -> None:
    audit = InMemoryAdminAudit()

    assert await audit.append(event=_event()) is True
    assert await audit.append(event=_event(event_id="evt-2")) is True

    assert len(audit.events) == 2  # только добавление, без удаления/правки
    for entry in audit.events:
        assert set(entry) == {
            "event_id",
            "occurred_at",
            "company_id",
            "plugin_id",
            "operation",
            "decision",
            "grant_id",
            "artifact_digest",
            "code",
            "reason",
        }
        serialized = " ".join(entry.values()).lower()
        for marker in (
            "select ",
            "insert into ",
            "postgresql://",
            "bearer ",
            "eyj",
            "password=",
            "@example.com",
            "payload",
            "cookie",
        ):
            assert marker not in serialized


@pytest.mark.parametrize(
    "reason",
    [
        "SELECT 1 FROM pg_database",
        "postgresql://user:pass@host/db",
        "Bearer eyJhbGciOiJIUzI1NiJ9.signature",
        "password=hunter2",
        "raw payload body",
    ],
)
def test_audit_rejects_raw_sql_tokens_pii(reason: str) -> None:
    """Redaction — ОТКАЗ, а не «молча вырежем и запишем»."""

    with pytest.raises(ValueError, match="запрещённые raw-данные|запрещённое поле"):
        _assert_redacted(_event(reason=reason))


def test_audit_rejects_non_string_values() -> None:
    with pytest.raises(ValueError, match="должно быть строкой"):
        _assert_redacted(
            AdminAuditEvent(
                event_id="evt-1",
                occurred_at=NOW,
                company_id=COMPANY,
                plugin_id=PLUGIN,
                operation=AdminOperation.GRANT,
                decision=AdminDecision.ALLOW,
                reason=None,  # type: ignore[arg-type]
            )
        )


async def test_audit_append_failure_is_reported_false() -> None:
    audit = InMemoryAdminAudit(append_result=False)
    # Сервис трактует False как deny позитивной мутации (§4.5).
    assert await audit.append(event=_event()) is False


async def test_audit_events_are_copies_not_mutable_originals() -> None:
    audit = InMemoryAdminAudit()
    await audit.append(event=_event())
    snapshot = audit.events
    with pytest.raises(TypeError):
        snapshot[0]["event_id"] = "tampered"  # type: ignore[index]
    assert audit.events[0]["event_id"] == "evt-1"


# --------------------------------------------------------------------------- grant store


async def test_grant_store_create_is_idempotent_and_isolated() -> None:
    store = InMemoryGrantStore()
    grant = _grant()

    assert await store.create(grant=grant) is grant
    assert await store.create(grant=grant) is grant  # без дубля
    assert await store.get(company_id=COMPANY, plugin_id=PLUGIN) is grant
    assert await store.get(company_id=COMPANY_OTHER, plugin_id=PLUGIN) is None
    assert await store.get_by_grant_id(grant_id="grant_001") is grant
    assert len(await store.list_by_company(company_id=COMPANY)) == 1
    assert await store.list_by_company(company_id=COMPANY_OTHER) == ()


async def test_grant_store_deactivate_by_grant_id() -> None:
    store = InMemoryGrantStore()
    await store.create(grant=_grant())

    updated = await store.deactivate(grant_id="grant_001")
    assert updated.status is GrantStatus.REVOKED
    assert updated.revoked_at is not None
    assert (await store.get(company_id=COMPANY, plugin_id=PLUGIN)).status is GrantStatus.REVOKED
    with pytest.raises(KeyError):
        await store.deactivate(grant_id="missing")


async def test_grant_store_delete_is_idempotent() -> None:
    store = InMemoryGrantStore()
    await store.create(grant=_grant())
    assert await store.delete(company_id=COMPANY, plugin_id=PLUGIN) is True
    assert await store.delete(company_id=COMPANY, plugin_id=PLUGIN) is True


async def test_enabled_modules_switches_path_without_data_migration() -> None:
    store = InMemoryGrantStore()
    assert await store.get_enabled_modules(company_id=COMPANY) == ()
    assert await store.set_enabled_modules(
        company_id=COMPANY, plugin_ids=(PLUGIN, PLUGIN, "mod_offers")
    ) == (PLUGIN, "mod_offers")  # дедуп, без переноса данных


# --------------------------------------------------------------------------- routes


async def test_route_registry_is_idempotent() -> None:
    routes = InMemoryRouteRegistry()
    reg = await routes.register(
        company_id=COMPANY, plugin_id=PLUGIN, grant_id="grant_001"
    )
    assert reg.company_id == COMPANY
    assert reg.webhook_path.startswith(f"/webhook/{PLUGIN}/{COMPANY}/")
    assert await routes.unregister(
        company_id=COMPANY, plugin_id=PLUGIN, grant_id="grant_001"
    ) is True
    assert await routes.unregister(
        company_id=COMPANY, plugin_id=PLUGIN, grant_id="grant_001"
    ) is True
    with pytest.raises(ValueError, match="grant_id"):
        await routes.register(company_id=COMPANY, plugin_id=PLUGIN, grant_id="")


# --------------------------------------------------------------------------- platform admin


async def test_static_platform_admin_does_not_allow_everyone() -> None:
    admin = StaticPlatformAdmin(expected_token="secret-token")

    assert await admin.require_platform_admin(authorization=None) == AdminIdentity(
        actor_id="", is_platform_admin=False
    )
    assert await admin.require_platform_admin(authorization="wrong") == AdminIdentity(
        actor_id="", is_platform_admin=False
    )
    assert await admin.require_platform_admin(authorization="") == AdminIdentity(
        actor_id="", is_platform_admin=False
    )
    identity = await admin.require_platform_admin(authorization="secret-token")
    assert identity.is_platform_admin is True
    assert identity.actor_id == "platform-admin"


async def test_static_platform_admin_without_expected_token_denies() -> None:
    admin = StaticPlatformAdmin(expected_token=None)
    result = await admin.require_platform_admin(authorization="any")
    assert result.is_platform_admin is False


def test_cluster_needs_real_implementations_a10() -> None:
    """Явная пометка: in-memory — только dev/тесты (задача A10)."""

    # Документационная проверка: в модуле stores.py есть предупреждение про
    # БД/Redis/A10, чтобы его не «оптимизировали» при ревью.
    from prodavan.application.plugins.adapters import stores

    assert "A10" in (stores.__doc__ or "")
    assert "БД" in (stores.__doc__ or "")
    assert "Redis" in (stores.__doc__ or "")
