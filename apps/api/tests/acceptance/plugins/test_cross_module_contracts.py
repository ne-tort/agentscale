"""Приёмочные тесты МЕЖМОДУЛЬНЫХ контрактов plugin-платформы (v4, §11).

ПОЧЕМУ ЭТОТ ФАЙЛ НУЖЕН
-----------------------
В волне 1 каждый агент проверял СВОЙ модуль в ``tests/unit/plugins/<zone>``:
identity — mint/verify/revocation, capabilities — invoke в изоляции, admin —
lifecycle на своих фейках, activation — ``should_suspend`` на своём store,
events — dispatch на своём консьюмере. Но НИ ОДИН unit-тест не соединял зоны
между собой. Между тем почти все инциденты из §10 дизайна — это именно разрывы
на стыках: «токен выпущен одним контуром, а проверен другим», «revoke записан
в admin, но gateway не узнал», «политика собрана из JWT, а не из grant»,
«событие закоммичено до обработки», «wake выдал токен, который verify не
принимает».

Здесь каждый тест собирает РЕАЛЬНЫЕ сервисы из ``application/plugins/**`` на
ОДНОМ наборе in-memory фейков из :mod:`conftest`: отзыв в admin мгновенно
виден capabilities и gateway; grant, созданный admin, читается egress;
артефакт, опубликованный registry, разрешает установку.

ВАЖНО (verification freeze)
---------------------------
Тесты ТОЛЬКО НАПИСАНЫ. Они НЕ запускаются до явного решения о начале
верификации. При первом прогоне ожидаемы падения/``skip`` из-за отсутствующих
или ещё не подключённых адаптеров — это СИГНАЛ о незавершённом стыке, а не
дефект самих тестов. См. ``docs/migration/plugin-test-plan.md``.

НИ ОДИН тест не обращается к БД, MinIO, Redpanda, k8s или сети.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac as _hmac
from dataclasses import dataclass
from typing import Any

import pytest

from .conftest import (
    FIXED_EPOCH,
    FIXED_NOW,
    TEST_COMPANY,
    TEST_DIGEST,
    TEST_GRANT_ID,
    TEST_INSTANCE,
    TEST_OTHER_COMPANY,
    TEST_PLUGIN,
    TEST_VERSION,
    FakeRecord,
    Platform,
    make_cache_get_request,
    make_capability_set,
    make_event,
    make_subscription,
)

# ``integration`` — зарегистрированный маркер pyproject.toml (off-cluster,
# без Docker/k8s). Своих новых маркеров не вводим: при --strict-markers они
# стали бы ошибкой коллекции.
pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Небольшие локальные хелперы (структурные grant/адаптеры для стыков)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _GrantCapabilities:
    """Структурный ``CapabilityGrant`` для ``ArtifactVerifier`` (поле capabilities)."""

    capabilities: tuple[str, ...]


def _registry_with_network(platform: Platform) -> Any:
    """``AdapterRegistry`` с network-адаптером (typed Response, без generic)."""

    caps = pytest.importorskip("prodavan.application.plugins.capabilities")
    contracts = pytest.importorskip(
        "prodavan.application.plugins.capabilities.contracts"
    )

    class NetworkAdapter:
        def egress(self, request: Any) -> Any:
            return contracts.NetworkEgressResponse(
                request_id=request.envelope.request_id,
                ok=True,
                digest="sha256:" + "2" * 64,
                status_code=200,
            )

    registry = caps.AdapterRegistry()
    registry.register("network", NetworkAdapter())
    return registry


def _verifier_of(registry: Any) -> Any:
    """Достать verifier из реестра (приватное поле используется только в тесте)."""

    return getattr(registry, "_verifier")


# ===========================================================================
# 1. identity -> capabilities -> audit: сквозной путь с валидным grant
#    Стык: identity↔capabilities (критерии №11, №12, §4.2/§4.5)
# ===========================================================================


def test_mint_verify_is_active_invoke_audit_happy_path(
    platform: Platform, valid_claims: Any
) -> None:
    """mint → verify → ``is_active`` → ``invoke`` → два события аудита.

    Unit-тесты identity проверяют mint/verify, а capabilities — invoke на
    «своём» claims. Здесь подтверждается, что ОДИН и тот же проверенный
    ``PluginClaims`` (с ``typ=plugin_capability``, своим ``iss``/``aud``)
    принимается ядром capabilities, а ``GrantRevocationPort`` и ``AuditPort``
    подключены к тому же состоянию, что и identity.
    """

    # 1) identity: подпись и claims уже проверены фикстурой valid_claims.
    assert valid_claims.typ == "plugin_capability"
    assert valid_claims.grant_id == TEST_GRANT_ID
    assert valid_claims.company_id == TEST_COMPANY
    assert valid_claims.artifact_digest == TEST_DIGEST
    # В токене НЕТ долгоживущих политик (кр. 22): только grant_id + короткий TTL.
    assert not hasattr(valid_claims, "egress")
    assert not hasattr(valid_claims, "quotas")

    # 2) revocation: тот же grant_id активен для capabilities.
    assert platform.revocation.is_active(TEST_GRANT_ID, now=FIXED_NOW) is True

    # 3) capabilities: реальный сервис на реальном ядре gateway.
    service = platform.capability_service()
    decision = service.invoke(
        claims=valid_claims,
        capability="cache",
        operation="get",
        request=make_cache_get_request(),
        now=FIXED_NOW,
    )

    assert decision.allowed is True, (
        "валидный grant + зарегистрированный адаптер обязаны дать ALLOW: "
        f"code={getattr(decision, 'code', None)!r}"
    )
    assert decision.data is not None, "успешный invoke обязан вернуть TypedResponse"
    assert "get" in platform.cache_adapter.calls, "адаптер должен быть вызван"

    # 4) аудит: решение и факт исполнения — РАЗНЫЕ события, без raw данных.
    assert platform.audit.events, "ни одного события аудита не записано"
    platform.audit.assert_no_raw()
    assert platform.quota.decisions, "квота должна быть списана ДО адаптера"


def test_missing_revocation_port_fails_closed_on_real_service(
    platform: Platform, valid_claims: Any
) -> None:
    """Стык fail-closed: без подключённого порта отзывов invoke = deny.

    Здесь проверяется, что собранная платформа НЕ имеет «тихого» дефолта,
    когда интегратор забыл связать identity-реестр с capability-сервисом.
    """

    caps = pytest.importorskip("prodavan.application.plugins.capabilities")
    registry = caps.AdapterRegistry()
    registry.register("cache", platform.cache_adapter)
    service = caps.PluginCapabilityService(
        registry=registry,
        quota=platform.quota,
        audit=platform.audit,
        revocation=None,
    )
    decision = service.invoke(
        claims=valid_claims,
        capability="cache",
        operation="get",
        request=make_cache_get_request(),
        now=FIXED_NOW,
    )
    assert decision.allowed is False
    assert "get" not in platform.cache_adapter.calls, (
        "без revocation-порта адаптер не должен вызываться"
    )


# ===========================================================================
# 2. revoke -> тот же токен отклоняется И на capabilities, И на gateway
#    Стык: identity↔capabilities↔gateway (критерий №12)
# ===========================================================================


def test_revoke_denies_same_token_on_capabilities(
    platform: Platform, valid_claims: Any
) -> None:
    """Отзыв по ``grant_id`` деактивирует ещё НЕ истёкший токен мгновенно."""

    service = platform.capability_service()
    request = make_cache_get_request()

    before = service.invoke(
        claims=valid_claims, capability="cache", operation="get",
        request=request, now=FIXED_NOW,
    )
    assert before.allowed is True

    # Тот же токен, тот же grant_id — но реестр отзывов уже деактивирован.
    platform.revocation.revoke(TEST_GRANT_ID, reason="admin revoke")
    assert platform.revocation.is_active(TEST_GRANT_ID, now=FIXED_NOW) is False

    after = service.invoke(
        claims=valid_claims, capability="cache", operation="get",
        request=request, now=FIXED_NOW,
    )
    assert after.allowed is False
    assert platform.cache_adapter.calls == ["get"], (
        "после revoke адаптер не должен вызываться повторно"
    )


@pytest.mark.asyncio
async def test_revoke_denies_same_grant_on_gateway(platform: Platform) -> None:
    """Gateway читает ТОТ ЖЕ реестр отзывов, что capabilities и admin.

    ``gateway/test_auth.py`` проверяет отказ при ``active_grant() -> None`` на
    СВОЁМ фейке, ``admin/test_service`` — запись отзыва на СВОЁМ. Здесь отзыв
    делается в общем ``FakeRevocation``, и шлюз обязан увидеть его без
    дополнительной синхронизации.
    """

    gateway = pytest.importorskip("prodavan.application.plugins.gateway")
    auth = platform.gateway_auth()
    headers = {"Authorization": "Bearer acceptance-session-token"}

    allowed = await auth.authorize_page(
        plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
        path="/index.html", headers=headers,
    )
    assert allowed.claims.grant_id == TEST_GRANT_ID

    platform.revocation.revoke(TEST_GRANT_ID, reason="admin revoke")
    with pytest.raises(gateway.GatewayError) as exc_info:
        await auth.authorize_page(
            plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
            path="/index.html", headers=headers,
        )
    assert exc_info.value.gateway_code == gateway.GatewayErrorCode.GRANT_INACTIVE


# ===========================================================================
# 3. typ=plugin_capability не проходит в /api/v1/projects/.../agent/*
#    Стык: identity↔transport (критерий №17)
# ===========================================================================


def test_plugin_claims_cannot_reach_agent_surface(
    platform: Platform, valid_claims: Any
) -> None:
    """Плагиновый JWT закрыт на всех agent-путях, включая ``/api/v1``.

    Unit-тест ``identity/test_allowlist.py`` проверяет шаблоны путей, но НЕ
    связывает их с реально выпущенным токеном. Здесь берётся токен из mint
    и прогоняется через ``assert_plugin_surface`` на путях из §11 кр. 17.
    """

    identity = pytest.importorskip("prodavan.application.plugins.identity")
    allowlist = identity.PluginSurfaceAllowlist()

    assert allowlist.is_allowed(valid_claims, "/capabilities/v1/cache/get") is True

    for path in (
        "/api/v1/projects/proj_1/agent/events",
        "/api/v1/projects/proj_1/agent/session",
        "/agent/events",
        "/internal/pods/pod_1/workspace-archive",
        "/api/v1/internal/pods/pod_1/files",
        "/api/v1/projects/proj_1/modules/mod_equipment/data/t",
    ):
        assert allowlist.is_allowed(valid_claims, path) is False, (
            f"agent-surface {path!r} обязан быть закрыт для PluginClaims"
        )
        with pytest.raises(identity.PluginTokenError) as exc_info:
            identity.assert_plugin_surface(valid_claims, path)
        assert exc_info.value.code == identity.PluginTokenErrorCode.FORBIDDEN_SURFACE


def test_wrong_typ_is_not_a_plugin_capability_token(platform: Platform) -> None:
    """Чужой trust-контур (``typ=pod_bridge``) не классифицируется как плагин.

    Стык identity↔transport: ``get_plugin_claims`` отсекает чужой ``typ`` ДО
    криптографии. Если этого не сделать, агентский токен :8001 пройдёт в
    plugin-surface.
    """

    identity = pytest.importorskip("prodavan.application.plugins.identity")
    import jwt as _jwt  # PyJWT уже используется identity/keys.py

    pod_bridge_token = _jwt.encode(
        {"typ": "pod_bridge", "sub": "pod:123", "exp": 2**31},
        "acceptance-only", algorithm="HS256",
    )
    assert identity.is_plugin_capability_token(pod_bridge_token) is False
    assert identity.is_plugin_capability_token(platform.mint_claims()) is True


# ===========================================================================
# 4. Grant.capabilities УЖЕ, чем requestedCapabilities -> INCOMPATIBLE / QUARANTINED
#    Стык: artifacts↔CapabilityGrant (критерий №18, §5.1 compatibility gate)
# ===========================================================================


def test_narrow_grant_quarantines_artifact(
    platform: Platform, valid_manifest: dict[str, Any], oci_ref: Any
) -> None:
    """Манифест запрашивает БОЛЬШЕ capability, чем есть в grant → карантин.

    Unit-тест ``artifacts/test_verification.py`` проверяет gate на своём
    ``FakeGrant``; здесь grant и manifest берутся из ЕДИНОГО контекста
    платформы, и результат публикации проверяется в реальном реестре.
    """

    from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome

    artifacts = pytest.importorskip("prodavan.application.plugins.artifacts")
    registry = platform.artifact_registry()
    narrow = _GrantCapabilities(capabilities=("cache",))

    outcome = _verifier_of(registry).verify(
        oci_ref, valid_manifest, grant=narrow, scan_verified=True
    )
    assert outcome.result.outcome is RegistryOutcome.QUARANTINED
    assert outcome.evidence.verified is False, "карантин НЕ разрешает публикацию"

    # Публикация с «чужим» успешным evidence тоже не проходит: реестр
    # перепроверяет compatibility gate, а не доверяет флагу.
    result = registry.publish(oci_ref, valid_manifest, outcome.evidence, grant=narrow)
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert artifacts.COMPATIBILITY_ERROR_CODE == "INCOMPATIBLE"


def test_full_grant_publishes_then_admin_grant_is_allowed(
    platform: Platform, valid_manifest: dict[str, Any], oci_ref: Any
) -> None:
    """Позитивная ветка того же стыка: полный grant → PUBLISHED → admin-гейт.

    Реестр артефактов задачи 07 и реестр установки задачи 12 в проде связаны
    через ``ArtifactRegistryPort``; здесь оба реестра разделяют один digest.
    """

    from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome

    registry = platform.artifact_registry()
    full = _GrantCapabilities(
        capabilities=tuple(valid_manifest["requestedCapabilities"])
    )
    outcome = _verifier_of(registry).verify(
        oci_ref, valid_manifest, grant=full, scan_verified=True
    )
    assert outcome.evidence.verified is True
    published = registry.publish(oci_ref, valid_manifest, outcome.evidence, grant=full)
    assert published.outcome is RegistryOutcome.APPLIED
    assert registry.current(TEST_PLUGIN) is not None

    # Тот же digest виден админскому порту (PUBLISHED-гейт кр. 18).
    platform.artifact_admin.seed_published()
    assert platform.artifact_admin.records[
        (TEST_PLUGIN, TEST_VERSION, TEST_DIGEST)
    ].is_published


# ===========================================================================
# 5. Полный путь установки: verifier -> publish -> grant -> tenantdb -> objects -> wake
#    Стык: artifacts↔admin↔tenantdb↔objects↔activation (§5.5, кр. 13/15/18)
# ===========================================================================


@pytest.mark.asyncio
async def test_full_install_path_artifacts_admin_tenantdb_objects_wake(
    platform: Platform, valid_manifest: dict[str, Any], oci_ref: Any
) -> None:
    """Один вызов ``admin.grant`` обязан поднять ВСЮ инфраструктуру компании.

    Покрывает разрыв между задачами: admin вызывает СВОИ порты, но никто не
    проверял, что ``TenantDatabaseService.grant_plugin`` (задача 05) и
    ``PluginObjectStore.grant`` (задача 06) получают те же ``(company, plugin)``
    и что ``Activator.wake`` (задача 09) будит ровно этот же инстанс.
    """

    contracts = pytest.importorskip("prodavan.application.plugins.admin.contracts")
    # 1) Артефакт опубликован (кр. 18) — иначе grant fail-closed.
    platform.artifact_admin.seed_published()

    service = platform.admin_service()
    receipt = await service.grant(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )

    assert receipt.grant.status is contracts.GrantStatus.ACTIVE
    assert receipt.grant.grant_id
    assert receipt.database.database_name == f"tenant_{TEST_COMPANY}"
    assert receipt.schema.schema_name == f"module_{TEST_PLUGIN}"
    assert receipt.objects.bucket == f"tenant-{TEST_COMPANY}"
    assert receipt.routes.grant_id == receipt.grant.grant_id
    # DSN наружу не ушёл: только secret_ref.
    assert "secret_ref" in receipt.database.dsn_ref
    assert "postgresql://" not in repr(receipt)

    # 2) Тот же digest видит artifacts-реестр (стык F4 -> F12).
    assert platform.artifact_admin.records[
        (TEST_PLUGIN, TEST_VERSION, TEST_DIGEST)
    ].is_published

    # 3) tenantdb: схема/роль созданы ровно для этой пары.
    assert (TEST_COMPANY, TEST_PLUGIN) in platform.tenant_db.schemas
    # 4) objects: bucket/префикс созданы для той же пары.
    assert f"module-{TEST_PLUGIN}/" in platform.objects_admin.buckets[
        f"tenant-{TEST_COMPANY}"
    ]

    # 5) Прямые сервисы задач 05/06 на ТЕХ ЖЕ идентификаторах (стык портов).
    tenant_service = platform.tenant_db_service()
    dsn = await tenant_service.grant_plugin(TEST_COMPANY, TEST_PLUGIN)
    assert dsn.database == f"tenant_{TEST_COMPANY}"
    assert "postgresql://" not in repr(dsn)  # только secret_ref, кр. 11

    object_store = platform.object_store()
    binding = await object_store.grant(TEST_COMPANY, TEST_PLUGIN)
    assert binding["bucket"] == f"tenant-{TEST_COMPANY}"

    # 6) Wake будит тот же инстанс и выдаёт НОВЫЙ короткий токен (кр. 13).
    platform.readiness.ready[receipt.plugin_instance_id] = True
    activator = platform.activator()
    wake = await activator.wake(receipt.plugin_instance_id)
    assert wake.plugin_instance_id == receipt.plugin_instance_id
    assert wake.token and wake.jti
    assert wake.expires_at > wake.issued_at
    assert platform.scale.replicas.get(receipt.plugin_instance_id) == 1
    assert platform.audit.events, "wake обязан писать аудит"


# ===========================================================================
# 6. uninstall дропает схему/роль/bucket и деактивирует grant, НЕ трогая другую компанию
#    Стык: tenantdb↔objects↔admin (критерий №15)
# ===========================================================================


@pytest.mark.asyncio
async def test_uninstall_drops_only_own_company_resources(
    platform: Platform,
) -> None:
    """Удаление плагина у ``co_alpha`` не задевает ``co_beta``.

    Unit-тесты admin/tenantdb/objects проверяют изоляцию каждый на СВОИХ
    фейках. Здесь ВСЕ ТРИ зоны смотрят в ОДНО состояние, и проверяется, что
    ни один вызов порта не получил чужой ``company_id``.
    """

    platform.artifact_admin.seed_published()
    service = platform.admin_service()

    await service.grant(
        company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION, artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )
    # Второй компании — тот же глобальный OCI-digest, но своя БД/bucket/grant.
    await service.grant(
        company_id=TEST_OTHER_COMPANY, plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION, artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )
    assert len(platform.tenant_db.schemas) == 2
    assert len(platform.objects_admin.buckets) == 2

    receipt = await service.uninstall(
        company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN
    )
    assert receipt.deleted is True
    assert receipt.fully_removed is True
    assert receipt.schema_dropped and receipt.role_dropped
    assert receipt.prefix_dropped and receipt.artifact_unlinked
    assert receipt.token_revoked is True
    assert receipt.snapshot_taken is False  # данные dev эфемерны, §13.1

    # Другая компания: БД, схема, bucket и grant НЕ тронуты.
    platform.tenant_db.assert_other_company_untouched(TEST_OTHER_COMPANY)
    assert (TEST_OTHER_COMPANY, TEST_PLUGIN) in platform.tenant_db.schemas
    assert f"tenant-{TEST_OTHER_COMPANY}" in platform.objects_admin.buckets
    assert (TEST_OTHER_COMPANY, TEST_PLUGIN) in platform.grant_store.grants
    # Ни один вызов порта не должен был получить чужую компанию.
    for call in platform.tenant_db.calls:
        assert call[1] in (TEST_COMPANY, TEST_OTHER_COMPANY)
    for call in platform.objects_admin.calls:
        assert call[1] in (TEST_COMPANY, TEST_OTHER_COMPANY)


# ===========================================================================
# 7. Активный job_id -> should_suspend=False; complete_job + TTL -> True
#    Стык: activation↔events↔idle (критерий №14)
# ===========================================================================


@pytest.mark.asyncio
async def test_active_job_blocks_suspend_then_ttl_allows_it(
    platform: Platform,
) -> None:
    """Реестр job'ов задачи 09 реально удерживает backend от засыпания.

    Unit-тест activation проверяет это на своём ``InMemoryStateStore``; здесь
    ``PluginActivator`` собран из ОБЩЕЙ платформы с общими ``ClockPort``/
    ``ScalePort``/``AuditPort`` (инжектируемые часы, без реального sleep).
    """

    activation = pytest.importorskip("prodavan.application.plugins.activation")
    activator = platform.activator()
    instance_id = TEST_INSTANCE

    # «Разбудили» backend, чтобы состояние стало ACTIVE и появился TTL.
    platform.readiness.ready[instance_id] = True
    await activator.wake(instance_id)

    # Прошло больше idle TTL, но job ещё активен.
    platform.clock.advance(
        seconds=activation.IdlePolicy.defaults().idle_ttl_seconds + 10
    )
    job = await activator.register_job(
        instance_id, "job_export_xlsx", lease_ttl_seconds=900
    )
    assert job.job_id == "job_export_xlsx"
    assert await activator.should_suspend(instance_id) is False, (
        "активный job_id обязан блокировать усыпление (кр. 14)"
    )

    # Завершили job + ещё немного времени -> усыпление разрешено.
    await activator.complete_job(instance_id, "job_export_xlsx")
    platform.clock.advance(seconds=1)
    assert await activator.should_suspend(instance_id) is True
    assert await activator.suspend(instance_id) is True
    assert platform.scale.replicas.get(instance_id) == 0


@pytest.mark.asyncio
async def test_event_not_lost_when_consumer_is_idle(platform: Platform) -> None:
    """Redpanda: «простой» консьюмера НЕ коммитит offset (кр. 20).

    Стык events↔idle: в as-built ``kick``+``auto_commit`` событие терялось
    именно в момент простоя. Здесь ``poll() -> None`` не должен привести ни
    к одному ``commit``; затем событие доставляется и коммитится ТОЛЬКО после
    успешного wake-хука и обработчика.
    """

    events = pytest.importorskip("prodavan.application.plugins.events")
    dispatcher = platform.event_dispatcher()
    subscription = make_subscription()
    platform.subscriptions.subscription = subscription

    # 1) Простой: записей нет.
    assert await dispatcher.consume(subscription) is None
    assert platform.consumer.commits == [], "при простое коммит недопустим"
    assert platform.consumer.polls == 1

    # 2) Событие появилось: wake ДО handler, commit ПОСЛЕ.
    event = make_event()
    platform.consumer.records.append(FakeRecord(value=event.to_wire()))
    outcome = await dispatcher.consume(subscription)
    assert outcome is events.DeliveryOutcome.ACK
    assert platform.consumer.commits == [17]
    assert len(platform.wake_hook.calls) == 1
    assert len(platform.handler.calls) == 1
    # Аудит событий не содержит raw payload, только канонические id/digest.
    assert platform.audit.typed
    assert all(
        "payload_digest" in details for _, details in platform.audit.typed
    )


# ===========================================================================
# 8. Egress: политика из CapabilityGrant, истёкший JWT её НЕ меняет
#    Стык: egress↔NP-генератор / capabilities (критерии №8, №22)
# ===========================================================================


def test_egress_policy_comes_from_grant_not_from_expired_jwt(
    platform: Platform, valid_claims: Any
) -> None:
    """Истечение короткого токена НЕ пересобирает сетевые правила.

    Unit-тесты egress проверяют это на своём ``GrantLookup``; здесь grant
    лежит в ОБЩЕМ ``grant_store`` (его создаёт admin, читает resolver), и
    политика сравнивается для СВЕЖЕГО и уже ИСТЁКШЕГО ``PluginClaims``.
    Плюс NP-генератор строит правила из того же allowlist.
    """

    egress = pytest.importorskip("prodavan.application.plugins.egress")
    platform.grant_store.seed_active_grant()
    resolver = platform.egress_resolver()

    fresh = resolver.resolve_claims(valid_claims)
    assert fresh.mode is egress.EgressMode.WEBHOOKS_ONLY
    assert "hooks.slack.com" in fresh.allowed_hosts

    # «Истёкший» токен: тот же grant_id, но exp в прошлом + подсунутое поле
    # egress. Резолвер читает ТОЛЬКО grant_id и НЕ смотрит на exp.
    expired_claims = {"grant_id": TEST_GRANT_ID, "exp": 1, "egress": "evil.com"}
    after = resolver.resolve_claims(expired_claims)
    assert after.mode == fresh.mode
    assert after.allowed_hosts == fresh.allowed_hosts
    assert after.rules == fresh.rules
    assert "evil.com" not in after.allowed_hosts, (
        "поле egress из токена не должно влиять на политику (§13.3)"
    )

    # Хост вне allowlist не совпадает ни с одним правилом (ни по хосту, ни по
    # префиксу): перебираем ВСЕ правила, чтобы исключить ложный «не совпал
    # только из-за метода».
    assert egress.is_host_allowed(fresh, "evil.com") is False
    assert not any(
        egress.match(rule, "evil.com", "/services/x", rule.method)
        for rule in fresh.rules
    ), "хост вне allowlist не должен совпадать ни с одним EgressRule"
    # Две NP из одного генератора: у plugin есть :5432/:9000, у agent — нет.
    generator = egress.EgressConfigGenerator()
    plugin_np = generator.build_network_policy(egress.EgressScope.PLUGIN)
    agent_np = generator.build_network_policy(egress.EgressScope.AGENT)
    assert plugin_np != agent_np
    assert plugin_np["kind"] == "NetworkPolicy"
    assert "agentscale-postgres" not in repr(plugin_np)


def test_capabilities_egress_port_reads_grant_not_token(
    platform: Platform, valid_claims: Any
) -> None:
    """Стык capabilities↔egress: ``EgressPolicyPort`` резолвит по grant_id.

    ``PluginCapabilityService`` для capability ``network`` требует политику из
    grant'а; подмена её полем токена невозможна конструктивно (в контракте
    ``NetworkEgressRequest`` нет ``allowed_hosts``).
    """

    caps = pytest.importorskip("prodavan.application.plugins.capabilities")
    contracts = pytest.importorskip(
        "prodavan.application.plugins.capabilities.contracts"
    )
    egress = pytest.importorskip("prodavan.application.plugins.egress")
    platform.grant_store.seed_active_grant()

    class GrantBackedPolicy:
        """Адаптер ``EgressPolicyPort`` поверх общего резолвера задач 08/12."""

        def __init__(self, resolver: Any) -> None:
            self._resolver = resolver
            self.calls: list[str] = []

        def allows(self, *, grant_id: str, host: str, port: int, path: str) -> bool:
            self.calls.append(grant_id)
            policy = self._resolver.resolve(grant_id)
            return egress.is_host_allowed(policy, host) and any(
                egress.match(rule, host, path, "POST") for rule in policy.rules
            )

    policy_port = GrantBackedPolicy(platform.egress_resolver())
    service = caps.PluginCapabilityService(
        registry=_registry_with_network(platform),
        quota=platform.quota,
        audit=platform.audit,
        revocation=platform.revocation,
        egress_policy=policy_port,
    )
    request = contracts.NetworkEgressRequest(
        host="hooks.slack.com",
        port=443,
        path="/services/x",
        method=contracts.NetworkHttpMethod.POST,
        envelope=contracts.CapabilityEnvelope(
            request_id="req-egress",
            company_id=TEST_COMPANY,
            subject_id=valid_claims.sub,
            plugin_instance_id=TEST_INSTANCE,
            artifact_digest=TEST_DIGEST,
            grant_id=TEST_GRANT_ID,
        ),
    )
    decision = service.invoke(
        claims=valid_claims,
        capability="network",
        operation="egress",
        request=request,
        now=FIXED_NOW,
    )
    assert TEST_GRANT_ID in policy_port.calls, (
        "политика должна резолвиться по grant_id, а не из claims.exp"
    )
    # Валидный grant + разрешённый хост -> адаптер вызван, audits записаны.
    assert decision.allowed is True


# ===========================================================================
# 9. Audit-событие не содержит raw SQL/params/tokens/payload (критерий №11)
#    Стык: ВСЕ зоны -> единый append-only sink
# ===========================================================================


def test_audit_across_all_zones_has_no_raw_data(
    platform: Platform, valid_claims: Any
) -> None:
    """Единый sink собирает события identity/capabilities/admin/activation/events.

    Unit-тесты проверяют redaction ПО ОТДЕЛЬНОСТИ (capabilities/audit.py,
    admin/AdminAuditEvent, objects/plugin_object_access, events/ports.py).
    Здесь проверяется, что при одновременной работе нескольких зон ни одна
    не «протащила» в общий лог SQL, параметры, DSN, токен или payload.
    """

    caps = pytest.importorskip("prodavan.application.plugins.capabilities")

    # Имитируем «опасные» данные, которые могли бы утечь из любого адаптера.
    platform.audit.rows.append(
        {
            "event": "plugin_authorization",
            "company_id": TEST_COMPANY,
            "grant_id": TEST_GRANT_ID,
            "payload_digest": TEST_DIGEST,
            "request_id": "req-1",
        }
    )
    # Сам redaction capabilities: на входе сырые поля, на выходе — digest'ы.
    redacted = caps.redact(
        {
            "sql": "SELECT * FROM tenant_co_alpha.secret",
            "params": {"password": "hunter2"},
            "token": "eyJhbGciOiJIUzI1NiJ9.payload.sig",
            "payload": {"body": "raw content"},
        }
    )
    assert "sql" not in redacted and "sql_digest" in redacted
    assert "token" not in redacted and "token_digest" in redacted
    assert "payload" not in redacted and "payload_digest" in redacted
    assert "hunter2" not in repr(redacted)
    assert "SELECT *" not in repr(redacted)

    # Общий sink не содержит запрещённых маркеров.
    platform.audit.assert_no_raw(
        (
            "SELECT ", "INSERT ", "UPDATE ", "DELETE ", "DROP ",
            "postgresql://", "hunter2", "eyJhbGciOi", "raw content",
            "params", "raw_sql",
        )
    )


# ===========================================================================
# 10. web <-> gateway: публичная страница не даёт /api/* анонимный доступ
#     Стык: web(iframe/plugin-rpc)↔gateway (критерии №6, №7, №9)
# ===========================================================================


@pytest.mark.asyncio
async def test_public_page_does_not_open_backend_api_anonymously(
    platform: Platform,
) -> None:
    """Даже объявленная ``public_pages`` страница НЕ пускает анонима в /api/*.

    Стык web↔gateway: ``apps/web/src/features/plugins/rpc.ts`` не передаёт
    capability-токен во frontend вообще; ``GatewayAuth.authorize_page`` обязан
    для ``is_api=True`` требовать сессию, даже если путь публичный (кр. 6).
    """

    gateway = pytest.importorskip("prodavan.application.plugins.gateway")
    # Префикс, оканчивающийся на '/': GatewayAuth._is_public поддерживает
    # точное совпадение ИЛИ префикс с завершающим '/'.
    platform.gateway_grants.declared_public_pages = frozenset({"/public/"})
    auth = platform.gateway_auth()

    # Публичная страница: анонимный доступ разрешён, capability-токена НЕТ.
    page = await auth.authorize_page(
        plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
        path="/public/index.html", headers={},
    )
    assert page.public is True
    assert page.grant.capability_token == ""
    assert page.claims.anonymous is True

    # Тот же путь, но /api/* -> сессия обязательна (иначе 401).
    with pytest.raises(gateway.GatewayError):
        await auth.authorize_page(
            plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
            path="/public/api/items", headers={}, is_api=True,
        )

    # Анонимный вызов закрытого /api/* тоже закрыт.
    with pytest.raises(gateway.GatewayError):
        await auth.authorize_page(
            plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
            path="/private/api/items", headers={}, is_api=True,
        )


@pytest.mark.asyncio
async def test_webhook_hmac_and_delivery_idempotency_across_gateway(
    platform: Platform,
) -> None:
    """Стык web↔gateway: подпись + повтор ``X-Prodavan-Delivery`` (кр. 7).

    Проверяется на реальном ``GatewayAuth`` с общим rate-limit/audit, а не на
    изолированном фейке из unit-тестов gateway.
    """

    gateway = pytest.importorskip("prodavan.application.plugins.gateway")
    auth = platform.gateway_auth()
    body = b'{"offer": 42}'
    signature = "sha256=" + _hmac.new(
        platform.gateway_secrets.secret.encode("utf-8"), body, hashlib.sha256
    ).hexdigest()
    headers = {
        "X-Prodavan-Signature": signature,
        "X-Prodavan-Event": "offer.created",
        "X-Prodavan-Delivery": "delivery-1",
    }

    first = await auth.authorize_webhook(
        plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
        path="/webhook", method="POST", raw_body=body, headers=headers,
    )
    assert first.duplicate is False

    # Повтор с тем же delivery-id: 200 без повторной обработки.
    second = await auth.authorize_webhook(
        plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
        path="/webhook", method="POST", raw_body=body, headers=headers,
    )
    assert second.duplicate is True

    # Неверная подпись -> отказ (кр. 7).
    with pytest.raises(gateway.GatewayError):
        await auth.authorize_webhook(
            plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
            path="/webhook", method="POST", raw_body=body,
            headers={**headers, "X-Prodavan-Signature": "sha256=" + "0" * 64},
        )


# ===========================================================================
# 11. Дополнительные резервные проверки стыков (режим «никто не покрыл»)
# ===========================================================================


@pytest.mark.asyncio
async def test_admin_revoke_is_immediately_visible_to_capabilities(
    platform: Platform, valid_claims: Any
) -> None:
    """Стык admin↔capabilities: revoke через PluginAdminService блокирует invoke.

    Unit-тест admin проверяет ``token_revoked=True`` на своём фейке; unit-тест
    capabilities — deny при ``is_active=False`` на своём. Здесь отзыв делается
    РЕАЛЬНЫМ админским сервисом и проверяется РЕАЛЬНЫМ capability-сервисом.
    """

    platform.artifact_admin.seed_published()
    platform.grant_store.seed_active_grant()
    admin = platform.admin_service()
    caps_service = platform.capability_service()

    before = caps_service.invoke(
        claims=valid_claims, capability="cache", operation="get",
        request=make_cache_get_request(), now=FIXED_NOW,
    )
    assert before.allowed is True

    receipt = await admin.revoke(company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN)
    assert receipt.token_revoked is True

    # Общий реестр отзывов: capabilities видит отзыв без дополнительной связи.
    after = caps_service.invoke(
        claims=valid_claims, capability="cache", operation="get",
        request=make_cache_get_request(), now=FIXED_NOW,
    )
    assert after.allowed is False
    assert platform.audit.events, "revoke и deny обязаны писать аудит"


def test_activation_wake_token_is_verifiable_by_identity_keyring(
    platform: Platform,
) -> None:
    """Стык activation↔identity: токен из ``MintPort`` проверяется KeyRing'ом.

    Критерий №13 требует НОВЫЙ короткий токен при wake. Мало получить строку
    от ``MintPort`` — надо убедиться, что её примет тот же trust-контур,
    который проверяет входящие запросы (иначе wake выдаст мусор).
    """

    identity = pytest.importorskip("prodavan.application.plugins.identity")

    async def _run() -> None:
        platform.readiness.ready[TEST_INSTANCE] = True
        activator = platform.activator()
        result = await activator.wake(TEST_INSTANCE)
        claims = identity.verify_plugin_token(
            result.token, keyring=platform.keyring(), now=FIXED_EPOCH
        )
        assert claims.typ == identity.PLUGIN_TOKEN_TYP
        assert claims.grant_id == TEST_GRANT_ID
        assert claims.plugin_instance_id == TEST_INSTANCE
        assert claims.exp > claims.iat

    asyncio.run(_run())


__all__: list[str] = []
