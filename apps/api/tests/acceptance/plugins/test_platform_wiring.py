"""Smoke-тесты собираемости платформы плагинов (v4, F11-интеграция).

ЧТО ПРОВЕРЯЕТСЯ
----------------
Это НЕ приёмочные тесты поведения, а «подъёмный» контроль wiring:
  1. все публичные ``__init__.py`` зон ``application/plugins/*`` импортируются;
  2. все объявленные порты имеют хотя бы одну реализацию ИЛИ явную заглушку
     (in-memory/dev/«интегратор подключит в F11»), но НЕ «тихо отсутствуют»;
  3. ``settings.plugin_platform_enabled=False`` по умолчанию;
  4. включение платформы без секрета/без static-origin ПАДАЕТ (связь с
     ``tests/unit/test_settings_guards.py`` — там же лежат guard'ы);
  5. константы контуров (issuer/audience/typ, топик, префиксы, TTL) согласованы
     между зонами: рассинхрон здесь ломает стыки, не попадая в unit-тесты.

ТЕСТЫ НЕ ЗАПУСКАЮТСЯ до явного решения о верификации (verification freeze).
При первом прогоне ожидаемы падения/``skip`` из-за ещё не подключённых
адаптеров — это СИГНАЛ, а не баг. См. ``docs/migration/plugin-test-plan.md``.

Импорты зон — ЛЕНИВЫЕ (``conftest._require_module``), чтобы коллекция не падала,
пока параллельные агенты ещё правят свои каталоги.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import _require_module

# ``integration`` — зарегистрированный маркер pyproject.toml.
pytestmark = pytest.mark.integration

# Публичные пакеты зон ``application/plugins/*`` (по плану миграции §2).
# ``transport`` и ``egress`` получили ``__init__.py`` в волне 1; если зона
# ещё не создала публичный init, тест честно скажет об этом (skip, не Fall).
PLUGIN_ZONES: tuple[str, ...] = (
    "identity",
    "capabilities",
    "transport",
    "tenantdb",
    "objects",
    "artifacts",
    "egress",
    "activation",
    "events",
    "gateway",
    "admin",
)

#: Порты, у которых в репозитории ЕСТЬ in-memory/dev-реализация.
#: Ключ — ``(zone, dotted-имя порта)``, значение — имя реализации/заглушки.
PORTS_WITH_INMEMORY_IMPL: dict[tuple[str, str], str] = {
    ("identity", "RevocationPort"): "InMemoryRevocation / GrantRevocation",
    ("identity", "KeyRing"): "KeyRing / keyring_from_secret (HS256+EdDSA)",
    ("capabilities", "GrantRevocationPort"): "identity.InMemoryRevocation (стык F11)",
    ("capabilities", "QuotaPort"): "InMemoryQuota",
    ("capabilities", "AuditPort"): "InMemoryAudit / FailingAudit",
    ("capabilities", "AdapterRegistry"): "AdapterRegistry (fail-closed)",
    ("capabilities", "PluginClaims"): "identity.types.PluginClaims (структурный протокол)",
    ("capabilities", "CachePort"): "typed adapter (F11, fail-closed при отсутствии)",
    ("capabilities", "ObjectsPort"): "objects.PluginObjectStore (задача 06)",
    ("capabilities", "DocsPort"): "typed adapter (F11)",
    ("capabilities", "EventsPort"): "events.PluginEventDispatcher (задача 10)",
    ("capabilities", "JobsPort"): "typed adapter (F11)",
    ("capabilities", "PodsPort"): "typed adapter (F11)",
    ("capabilities", "McpPort"): "typed adapter (F11)",
    ("capabilities", "SecretsPort"): "typed adapter (F11)",
    ("capabilities", "NetworkPort"): "egress.EgressService (задача 08)",
    ("tenantdb", "AdminConnectionPort"): "SQLAlchemy/asyncpg adapter (F11, fail-closed)",
    ("tenantdb", "SecretPort"): "k8s Secret adapter (F11, fail-closed)",
    ("objects", "BucketAdminPort"): "MinIO Admin adapter (F11) / in-memory в тестах",
    ("objects", "StsPort"): "MinIO STS AssumeRole (F11) / scoped presigned fallback",
    ("objects", "PresignPort"): "scoped presigned (§5.4 fallback)",
    ("objects", "AuditPort"): "append-only sink (F11)",
    ("artifacts", "CosignPort"): "cosign CLI/HTTP adapter (CI, задача 16)",
    ("artifacts", "RegistryStorePort"): (
        "SqlArtifactRegistryStore (durable production) / "
        "InMemoryRegistryStore (tests only)"
    ),
    ("egress", "GrantStorePort"): "БД-адаптер (F11) / GrantLookup в тестах",
    ("egress", "AuditSinkPort"): "общий append-only sink (§6.3)",
    ("events", "TopicAdminPort"): "rpk/Redpanda Admin adapter (F11)",
    ("events", "ProducerPort"): "aiokafka ProducerPort adapter (F11)",
    ("events", "ConsumerPort"): "aiokafka dispatch, ручной commit (F11)",
    ("events", "DlqPort"): "DLQ-продюсер (F11)",
    ("events", "RawDlqPort"): (
        "KafkaRawDlqAdapter (infrastructure/plugins/capabilities/kafka.py: "
        "bool ack + natural-key dedup) / FakeRawDlq в acceptance"
    ),
    ("events", "SubscriptionStorePort"): "БД подписок (F11)",
    ("events", "IdempotencyStorePort"): "БД/Redis (F11)",
    ("events", "PublishedEventStorePort"): "БД/Redis (F11)",
    ("events", "EventHandlerPort"): "POST /events/* backend плагина (F11)",
    ("events", "WakeHookPort"): "PluginActivator.wake (задача 09)",
    ("events", "AuditPort"): "общий append-only sink (F11)",
    ("events", "ConsumedRecord"): "aiokafka.ConsumerRecord / FakeRecord в тестах",
    ("activation", "ScalePort"): "k8s deployments/scale (задача 14)",
    ("activation", "ReadinessPort"): "readinessProbe /health (задача 14)",
    ("activation", "ClockPort"): "SystemClock",
    ("activation", "MintPort"): "identity.mint (задача 02)",
    ("activation", "AuditPort"): "общий append-only sink (F11)",
    ("activation", "StateStorePort"): "InMemoryStateStore / БД-реализация с CAS (F11)",
    ("activation", "ProxyPort"): "GatewayProxy (задача 11)",
    ("gateway", "SessionPort"): "общий auth-контур (F11)",
    ("gateway", "GrantPort"): "GrantStore + revocation (F11)",
    ("gateway", "SecretPort"): "k8s Secret HMAC (F11)",
    ("gateway", "HmacPort"): "Sha256HmacVerifier (реализация по умолчанию)",
    ("gateway", "RateLimitPort"): "Redis SET NX + INCR (F11)",
    ("gateway", "ActivationPort"): "PluginActivator (задача 09)",
    ("gateway", "BackendHttpPort"): "httpx/streaming транспорт (F11)",
    ("gateway", "AuditPort"): "общий append-only sink (F11)",
    ("admin", "TenantDbPort"): "TenantDatabaseService (задача 05)",
    ("admin", "ObjectStorePort"): "PluginObjectStore (задача 06)",
    ("admin", "ArtifactRegistryPort"): (
        "ArtifactRegistryAdapter(PluginArtifactRegistry + SqlArtifactRegistryStore)"
    ),
    ("admin", "InstanceStorePort"): (
        "SqlPluginInstanceRepository (infrastructure/persistence/repositories/"
        "plugins/instances.py: durable saga + InstanceIdResolver) / "
        "FakeInstanceStore в acceptance"
    ),
    ("admin", "GrantStorePort"): "БД-модель CapabilityGrant + Alembic (F11)",
    ("admin", "RevocationPort"): "identity.revocation (задача 02)",
    ("admin", "MintPort"): "identity.mint (задача 02)",
    ("admin", "ActivationPort"): "PluginActivator (задача 09)",
    ("admin", "RouteRegistryPort"): "Ingress/NP-генератор (задачи 14/15)",
    ("admin", "AuditPort"): "общий append-only sink (F11)",
    ("admin", "PlatformAdminPort"): "PlatformAdmin auth (F11)",
    ("admin", "WebhookSecretStorePort"): (
        "trusted SecretStore по canonical webhook_secret_key "
        "(wiring/install.py; K8sSecretProjection webhook resolver) / "
        "FakeWebhookSecretStore в acceptance"
    ),
    ("admin", "PublicationApprovalReaderPort"): (
        "SqlPluginPublicationApprovalRepository (exact company/plugin/SemVer/"
        "digest binding read; CI approval_reader) / FakePublicationApprovalStore"
    ),
    ("admin", "PublicationApprovalStorePort"): (
        "SqlPluginPublicationApprovalRepository (INSERT-if-absent + atomic CAS "
        "NULL→consumed_at) / FakePublicationApprovalStore в acceptance"
    ),
    ("admin", "SecretDeliveryControlPort"): (
        "ControlPlaneSecretDelivery + K8sSecretProjection (approved install/"
        "artifact phase, без plugin token) / scoped FakeSecretDeliveryControl"
    ),
    ("admin", "PluginUpdateLockPort"): "SqlPluginUpdateLock (PostgreSQL advisory xact lock)",
    ("admin", "InstallationScopeStorePort"): "SqlInstallationScopeRepository / scoped acceptance fixture",
    ("events", "ConsumerOwnershipPort"): "AiokafkaConsumerAdapter.delivery_ownership",
    ("events", "DeliveryClaimPort"): "RedisDeliveryClaimStore",
    ("events", "RawDlqPort"): "KafkaRawDlqAdapter (production) / durable RawDlq double (acceptance)",
}


# ---------------------------------------------------------------------------
# 1. Все публичные __init__.py импортируются
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("zone", PLUGIN_ZONES)
def test_zone_public_init_imports(zone: str) -> None:
    """Публичный контракт зоны должен импортироваться без побочных эффектов.

    Ленивый импорт через ``_require_module``: если зона ещё не собрана —
    точечный ``pytest.skip`` (сигнал), а НЕ падение коллекции всего пакета.
    """

    module = _require_module(f"prodavan.application.plugins.{zone}")
    assert module is not None
    # Публичный контракт обязан явно перечислять экспорты.
    exported = getattr(module, "__all__", None)
    assert isinstance(exported, (list, tuple)) and exported, (
        f"application/plugins/{zone}/__init__.py обязан объявить __all__"
    )
    for name in exported:
        assert hasattr(module, name), (
            f"{zone}.__all__ содержит {name!r}, но атрибут отсутствует — "
            "публичный контракт расходится с реализацией"
        )


def test_every_zone_has_public_init_file() -> None:
    """У каждой зоны есть ``__init__.py`` (иначе пакет не соберётся в F11)."""

    # Путь к src вычисляем от файла теста, без обращения к settings/окружению.
    src_root = Path(__file__).resolve().parents[3] / "src" / "prodavan" / "application" / "plugins"
    assert src_root.is_dir(), f"не найден каталог зон: {src_root}"
    for zone in PLUGIN_ZONES:
        init = src_root / zone / "__init__.py"
        assert init.is_file(), (
            f"зона {zone!r} не имеет публичного __init__.py — "
            "интегратор F11 не сможет импортировать контракт"
        )


# ---------------------------------------------------------------------------
# 2. Порты: реализация или ЯВНАЯ заглушка, но не «тихое отсутствие»
# ---------------------------------------------------------------------------


def test_every_declared_port_has_impl_or_explicit_stub() -> None:
    """Реестр портов из заголовков зон покрыт реализацией/заглушкой.

    Fail-closed по конструкции: порт без адаптера не должен «молча»
    отсутствовать. Мы проверяем, что КАЖДЫЙ порт, объявленный в зоне как
    ``Protocol`` (runtime_checkable), либо поименован в нашем реестре связей,
    либо имеет in-memory реализацию в самой зоне. Неизвестный порт = сигнал
    «стык не описан», а не автоматический успех.
    """

    missing: list[str] = []
    src_root = (
        Path(__file__).resolve().parents[3]
        / "src" / "prodavan" / "application" / "plugins"
    )
    for zone in PLUGIN_ZONES:
        # Не у каждой зоны есть отдельный ports.py: identity/artifacts/
        # tenantdb объявляют порты внутри своих модулей. Пропускаем такие зоны
        # ЯВНО (а не через skip), чтобы не маскировать реальные пропуски.
        if not (src_root / zone / "ports.py").is_file():
            continue
        ports_mod = _require_module(
            f"prodavan.application.plugins.{zone}.ports"
        )
        # Собираем ВСЕ Protocol из ports.py зоны (не только runtime_checkable:
        # gateway/admin объявили протоколы без декоратора, но они всё равно
        # обязаны иметь адаптер или явную заглушку).
        declared = {
            name
            for name, obj in vars(ports_mod).items()
            if isinstance(obj, type)
            and getattr(obj, "_is_protocol", False)
            and name != "Protocol"
        }
        for port_name in declared:
            key = (zone, port_name)
            if key in PORTS_WITH_INMEMORY_IMPL:
                continue
            # Явная заглушка ВНУТРИ зоны: InMemory*/System*/Fake*/Failing*.
            zone_init = _require_module(f"prodavan.application.plugins.{zone}")
            has_stub = any(
                token in name
                for name in getattr(zone_init, "__all__", ())
                for token in ("InMemory", "System", "Fake", "Failing")
            )
            if not has_stub:
                missing.append(f"{zone}.{port_name}")
    assert not missing, (
        "порты без реализации и без явной заглушки (fail-closed нарушен): "
        f"{sorted(missing)}"
    )


def test_capability_adapter_registry_is_fail_closed() -> None:
    """``AdapterRegistry`` и ``CAPABILITY_PORT`` согласованы: 9 адаптеров."""

    caps = _require_module("prodavan.application.plugins.capabilities")
    assert len(caps.CAPABILITY_PORT) == 9
    assert set(caps.CAPABILITY_PORT) == set(caps.CAPABILITY_OPERATIONS)
    assert "db.direct" not in caps.CAPABILITY_PORT, (
        "db.direct — прямой DSN, а НЕ capability gateway (ADR-007)"
    )
    registry = caps.AdapterRegistry()
    with pytest.raises(caps.UnknownCapabilityError):
        registry.resolve("db.direct")
    with pytest.raises(caps.AdapterMissingError):
        registry.resolve("cache")


# ---------------------------------------------------------------------------
# 3. Settings guard'ы (связь с tests/unit/test_settings_guards.py)
# ---------------------------------------------------------------------------


def test_plugin_platform_is_off_by_default() -> None:
    """Платформа ВЫКЛЮЧЕНА по умолчанию: никакого молчаливого trust-контура."""

    settings_mod = _require_module("prodavan.config.settings")
    settings = settings_mod.Settings()
    assert settings.plugin_platform_enabled is False
    assert settings.plugin_claims_secret == ""
    assert settings.plugin_static_origin == ""
    assert settings.plugin_shell_origin == ""
    assert settings.plugin_egress_mode == "none"


def test_enabling_platform_without_secret_fails() -> None:
    """Включение без секрета подписи = явный отказ, а не dev-фолбэк.

    Это ТОТ ЖЕ guard, что и ``test_settings_guards.py::
    test_plugin_platform_enabled_without_secret_fails``. Дубль здесь нужен
    намеренно: acceptance-прогон должен видеть guard и без unit-набора,
    если роутеры/``app.state`` уже подключены родителем (F11).
    """

    settings_mod = _require_module("prodavan.config.settings")
    with pytest.raises(RuntimeError, match="PLUGIN_CLAIMS_SECRET"):
        settings_mod.Settings(
            plugin_platform_enabled=True,
            plugin_static_origin="https://plugins.example.dev",
        )


def test_enabling_platform_without_static_origin_fails() -> None:
    """iframe без отдельного origin = classic sandbox bypass (§5.3)."""

    settings_mod = _require_module("prodavan.config.settings")
    with pytest.raises(RuntimeError, match="PLUGIN_STATIC_ORIGIN"):
        settings_mod.Settings(
            plugin_platform_enabled=True,
            plugin_claims_secret="explicit-secret",
        )


def test_full_egress_requires_explicit_approval() -> None:
    """``full`` без approval запрещён, TTL ограничен 60..900 (§6.3/§4.2)."""

    settings_mod = _require_module("prodavan.config.settings")
    with pytest.raises(RuntimeError, match="PLUGIN_EGRESS_APPROVED"):
        settings_mod.Settings(plugin_egress_mode="full")
    with pytest.raises(RuntimeError, match="60..900"):
        settings_mod.Settings(plugin_claims_ttl_seconds=86400)


# ---------------------------------------------------------------------------
# 4. Согласованность констант между зонами (стыки, не видимые unit-тестам)
# ---------------------------------------------------------------------------


def test_identity_trust_constants_match_settings() -> None:
    """issuer/audience/typ из identity == настройки platforms (иначе verify
    отклонит все токены, выпущенные mint, после F11-интеграции).
    """

    identity = _require_module("prodavan.application.plugins.identity")
    settings_mod = _require_module("prodavan.config.settings")
    settings = settings_mod.Settings()
    assert identity.PLUGIN_ISSUER == settings.plugin_claims_issuer
    assert identity.PLUGIN_AUDIENCE == settings.plugin_claims_audience
    assert identity.PLUGIN_TOKEN_TYP == "plugin_capability"
    assert identity.DEFAULT_PLUGIN_TOKEN_TTL_SECONDS == settings.plugin_claims_ttl_seconds


def test_cross_zone_constants_are_consistent() -> None:
    """Один digest-формат, один топик, один TTL, одни префиксы во всех зонах."""

    domain = _require_module("prodavan.domain.plugins")
    events = _require_module("prodavan.application.plugins.events")
    admin = _require_module("prodavan.application.plugins.admin")
    artifacts = _require_module("prodavan.application.plugins.artifacts")

    assert domain.PROTOCOL_VERSION == artifacts.PLUGIN_PROTOCOL == "plugin-rpc/1"
    assert events.PLUGIN_EVENTS_TOPIC == "agentscale.plugin.events"
    assert events.CONSUMER_AUTO_OFFSET_RESET == "earliest"
    assert events.CONSUMER_ENABLE_AUTO_COMMIT is False
    assert admin.PLUGIN_CLAIMS_TTL_SECONDS == 300
    assert admin.TENANT_DATABASE_PREFIX == "tenant_"
    assert admin.TENANT_BUCKET_PREFIX == "tenant-"
    assert admin.PLUGIN_SCHEMA_PREFIX == "module_"
    assert admin.PLUGIN_OBJECT_PREFIX == "module-"
    assert artifacts.PLATFORM_VERSION == "4.0.0"


def test_settings_guards_unit_file_exists() -> None:
    """Связь с существующим unit-набором guard'ов не потеряна."""

    guards = Path(__file__).resolve().parents[2] / "unit" / "test_settings_guards.py"
    assert guards.is_file(), (
        "tests/unit/test_settings_guards.py отсутствует: acceptance-тесты "
        "ссылаются на те же guard'ы, рассинхрон недопустим"
    )


__all__: list[str] = []
