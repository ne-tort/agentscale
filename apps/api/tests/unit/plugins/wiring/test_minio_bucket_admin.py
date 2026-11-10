"""Пины безопасных резолверов F11-композиции (MinIO + gateway sessions).

Урок раунда 2: массовая правка unwired-портов дважды ломала `create_app()`,
при этом полный pytest на fakes регрессию НЕ ловил. Здесь проверяются именно
выделенные резолверы `_minio_bucket_admin()` / `_gateway_sessions()`:

* без env/БД они НЕ бросают и возвращают ``None`` (вызывающий оставит
  fail-closed ``_UnwiredPort``, имя попадёт в ``plugin_unwired_ports``);
* с валидной конфигурацией возвращают РЕАЛЬНЫЕ адаптеры, а не заглушки;
* MinIO-конфиг берётся ровно из ``PLUGIN_MINIO_*`` — фолбэка на root/S3_* нет.
"""

from __future__ import annotations

from typing import Any

from prodavan.application.plugins.wiring import install

_MINIO_ENV = {
    "PLUGIN_MINIO_ENDPOINT": "http://agentscale-minio:9000",
    "PLUGIN_MINIO_ADMIN_ACCESS_KEY_REF": "vault://ai_keys/minio/admin-access",
    "PLUGIN_MINIO_ADMIN_SECRET_KEY_REF": "vault://ai_keys/minio/admin-secret",
    "PLUGIN_MINIO_PRESIGN_ACCESS_KEY_REF": "vault://ai_keys/minio/presign-access",
    "PLUGIN_MINIO_PRESIGN_SECRET_KEY_REF": "vault://ai_keys/minio/presign-secret",
    "PLUGIN_MINIO_STS_ENABLED": "false",
    "PLUGIN_MINIO_REGION": "us-east-1",
}


def test_minio_bucket_admin_is_fail_closed_without_env(monkeypatch: Any) -> None:
    """Отсутствие конфигурации ≠ падение startup и ≠ тихий allow."""
    for key in _MINIO_ENV:
        monkeypatch.delenv(key, raising=False)

    assert install._minio_bucket_admin() is None


def test_gateway_sessions_is_real_sql_directory_with_db(monkeypatch: Any) -> None:
    """В кластерной конфигурации SessionPort — реальный SQL-каталог, не заглушка."""
    from prodavan.infrastructure.plugins.adapters.gateway_adapters import (
        SessionAdapter,
        SqlEmployeeDirectory,
    )

    sessions = install._gateway_sessions()

    assert isinstance(sessions, SessionAdapter)
    assert isinstance(sessions._directory, SqlEmployeeDirectory)
    assert sessions._directory._session_factory is not None
    assert sessions._cookie_sessions is None  # opaque cookie = fail-closed


def test_gateway_grants_uses_sql_store_not_in_memory(monkeypatch: Any) -> None:
    """GrantPort читает CapabilityGrant из SQL, а не из in-memory дубля."""
    import asyncio
    from dataclasses import dataclass, field
    from types import SimpleNamespace

    from prodavan.application.plugins.admin.contracts import GrantStatus
    from prodavan.infrastructure.plugins.adapters.gateway_adapters import GrantAdapter

    @dataclass
    class _Policy:
        mode: str = "webhooks-only"
        allowed_hosts: tuple[str, ...] = ("hooks.slack.com",)
        allowed_paths: tuple[str, ...] = ("/services/",)
        approved_for_full: bool = False

    @dataclass
    class _Record:
        grant_id: str = "grant_1"
        company_id: str = "co_1"
        plugin_id: str = "p_1"
        plugin_instance_id: str = "inst_1"
        manifest_version: str = "1.0.0"
        artifact_digest: str = "sha256:" + "a" * 64
        capabilities: tuple[str, ...] = ("cache",)
        egress_policy: Any = field(default_factory=_Policy)
        status: Any = GrantStatus.ACTIVE

        @property
        def is_active(self) -> bool:
            return self.status is GrantStatus.ACTIVE

    class _Store:
        async def get(self, *, company_id: str, plugin_id: str) -> Any:
            return _Record()

    class _Registry:
        async def get_async(self, *, plugin_id: str, version: str) -> Any:
            record = _Record()
            if plugin_id != record.plugin_id or version != record.manifest_version:
                return None
            return SimpleNamespace(
                digest=record.artifact_digest,
                manifest={"webhooks": ["/webhook/p_1"]},
            )

    grants = install._gateway_grants(
        SimpleNamespace(grant_store=_Store()),
        {"artifact_registry": _Registry()},
    )
    assert isinstance(grants, GrantAdapter)

    record = asyncio.run(grants.active_grant(plugin_id="p_1", company_id="co_1"))
    assert record is not None
    assert record.grant_id == "grant_1"
    assert record.active is True
    assert "cache" in record.operations
    # GrantAdapter отдаёт урезанную проекцию GatewayGrant: capability_token
    # намеренно ПУСТ (свежий токен приходит из WakeLease при wake-up).
    assert record.capability_token == ""
    # Egress-политика осталась в SQL-записи GrantRecord, а не в JWT.
    raw = asyncio.run(_Store().get(company_id="co_1", plugin_id="p_1"))
    assert raw.egress_policy.allowed_hosts == ("hooks.slack.com",)

    # Нет SQL-репозитория -> None -> честный _UnwiredPort, не падение startup.
    assert install._gateway_grants(SimpleNamespace()) is None


def test_gateway_secrets_is_typed_fail_closed_without_ref() -> None:
    """SecretPort привязан, но без выданного webhook secret_ref НЕ принимает подпись."""
    import asyncio
    from types import SimpleNamespace

    from prodavan.infrastructure.plugins.adapters.gateway_adapters import SecretAdapter

    class _Store:
        async def get(self, *, company_id: str, plugin_id: str) -> Any:
            return None  # grant есть, но PLUGIN_WEBHOOK_SECRET ещё не выдан

    secrets = install._gateway_secrets(SimpleNamespace(grant_store=_Store()))
    assert isinstance(secrets, SecretAdapter)
    assert asyncio.run(secrets.webhook_secrets(plugin_id="p", company_id="co")) == ()
    assert asyncio.run(secrets.webhook_secret(plugin_id="p", company_id="co")) is None
    # Нет стора -> None -> честный _UnwiredPort, старт не падает.
    assert install._gateway_secrets(SimpleNamespace()) is None


def test_activation_mint_uses_sql_grant_and_verified_payload() -> None:
    """activation.MintPort перевыпускает JWT из SQL-гранта, не из Secret."""
    import asyncio
    from dataclasses import dataclass, field
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from prodavan.application.plugins.admin.contracts import GrantStatus
    from prodavan.application.plugins.wiring import install as _install
    from prodavan.application.plugins.wiring.config import PluginPlatformConfig

    from .conftest import FakeSettings

    @dataclass
    class _Record:
        grant_id: str = "grant_1"
        company_id: str = "co_1"
        plugin_id: str = "p_1"
        plugin_instance_id: str = "inst_1"
        manifest_version: str = "1.0.0"
        artifact_digest: str = "sha256:" + "a" * 64
        capabilities: tuple[str, ...] = ("cache",)
        status: Any = GrantStatus.ACTIVE
        quotas: Any = field(default_factory=dict)

        @property
        def is_active(self) -> bool:
            return self.status is GrantStatus.ACTIVE

    class _Store:
        async def get_by_instance_id(self, *, plugin_instance_id: str) -> Any:
            return _Record()

    config = PluginPlatformConfig.from_settings(FakeSettings())
    mint = _install._activation_mint(SimpleNamespace(grant_store=_Store()), config)
    assert mint is not None

    claims = asyncio.run(mint.mint(plugin_instance_id="inst_1", reason="wake"))
    assert claims.token
    assert claims.jti
    assert claims.expires_at > claims.issued_at
    assert claims.claims["grant_id"] == "grant_1"
    # В коротком токене НЕТ egress/квот (§4.2).
    assert "egress" not in claims.claims and "quotas" not in claims.claims

    # Нет SQL-репозитория -> None -> честный _UnwiredPort, старт не падает.
    assert _install._activation_mint(SimpleNamespace(), config) is None


def test_tenant_db_secrets_async_bridge_rotates_to_empty() -> None:
    """tenantdb.SecretPort async-мост: rotate(value='') инвалидирует DSN."""
    import asyncio
    from unittest.mock import MagicMock

    from prodavan.application.plugins.wiring import install as _install

    fake_store = MagicMock()
    fake_store.put.return_value = "file://ai_keys/tenant.secret"
    monkeypatch = None  # локальная подмена через атрибут модуля
    import prodavan.infrastructure.secrets.store as _store_module

    original = _store_module.get_secret_store
    _store_module.get_secret_store = lambda: fake_store
    try:
        port = _install._tenant_db_secrets()
        assert port is not None
        assert asyncio.run(port.put(key="tenant", value="postgresql://u:p@h/db")) == (
            "file://ai_keys/tenant.secret"
        )
        # revoke: rotate(value="") НЕ должна пасть на FileSecretStore.put("").
        assert asyncio.run(port.rotate(key="tenant", value="")) == ""
        fake_store.delete.assert_called_once_with("file://ai_keys/tenant.secret")
        asyncio.run(port.delete(secret_ref="file://ai_keys/tenant.secret"))
    finally:
        _store_module.get_secret_store = original


def test_in_memory_ports_have_their_own_diagnostic_key() -> None:
    """Привязанные in-memory порты НЕ должны числиться «совсем не подключено»."""
    assert install.IN_MEMORY_PORTS_STATE_KEY == "plugin_in_memory_ports"
    assert install.IN_MEMORY_PORTS_STATE_KEY != install.UNWIRED_PORTS_STATE_KEY
    assert install.IN_MEMORY_PORTS_STATE_KEY in install._OWN_STATE_KEYS
    assert "IN_MEMORY_PORTS_STATE_KEY" in install.__all__


def test_minio_bucket_admin_is_real_with_valid_env(monkeypatch: Any) -> None:
    """В кластерной конфигурации порт привязан к реальному адаптеру."""
    for key, value in _MINIO_ENV.items():
        monkeypatch.setenv(key, value)
    from prodavan.infrastructure.plugins.minio.adapters import MinioBucketAdmin

    class _Secrets:
        def get(self, ref: str) -> str:
            return {
                "vault://ai_keys/minio/admin-access": "plugin-object-admin",
                "vault://ai_keys/minio/admin-secret": "plugin-object-admin-secret",
                "vault://ai_keys/minio/presign-access": "plugin-object-presigner",
                "vault://ai_keys/minio/presign-secret": "plugin-object-presigner-secret",
            }[ref]

    buckets = install._minio_bucket_admin(_Secrets())

    assert isinstance(buckets, MinioBucketAdmin)
    assert buckets.owns_bucket is True
    assert buckets.config is not None
    assert buckets.config.admin_access_key == "plugin-object-admin"
    assert buckets.config.sts_enabled is False
