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

import hashlib
import hmac as _hmac
import json
from dataclasses import dataclass
from typing import Any

import pytest

from prodavan.domain.plugins.validation import validate_manifest

from .conftest import (
    FIXED_EPOCH,
    ISSUER,
    FIXED_NOW,
    TEST_COMPANY,
    TEST_DIGEST,
    TEST_GRANT_ID,
    TEST_INSTANCE,
    TEST_OTHER_COMPANY,
    TEST_PLUGIN,
    TEST_QUOTA_VALUES,
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
    quotas = pytest.importorskip("prodavan.application.plugins.capabilities.quotas")

    class NetworkAdapter:
        def quota_usage(self, request: Any) -> Any:
            return quotas.QuotaUsage(
                ops=1, rows=0, bytes=0, keys=0, object_bytes=0, egress_bytes=1
            )

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


def _publication_metadata() -> Any:
    """CI provenance с явной, различной привязкой bundle и backend OCI digest."""

    artifacts = pytest.importorskip("prodavan.application.plugins.artifacts")
    registry_module = pytest.importorskip(
        "prodavan.application.plugins.artifacts.registry"
    )
    backend_ref = artifacts.OciArtifactRef(
        registry="ghcr.io",
        repository=f"ne-tort/plugin-backends/{TEST_PLUGIN}",
        digest="sha256:" + "b" * 64,
    )
    return registry_module.PublicationMetadata(
        source_id="agentscale/plugins",
        commit_sha="a" * 40,
        backend_digest=backend_ref.digest,
        backend_image_ref=backend_ref.image_ref(),
        run_url="https://github.com/agentscale/plugins/actions/runs/42",
    )


# ===========================================================================
# 1. identity -> capabilities -> audit: сквозной путь с валидным grant
#    Стык: identity↔capabilities (критерии №11, №12, §4.2/§4.5)
# ===========================================================================


@pytest.mark.asyncio
async def test_mint_verify_is_active_invoke_audit_happy_path(
    platform: Platform, valid_claims: Any
) -> None:
    """mint → verify → ``is_active`` → ``invoke`` → два события аудита.

    Unit-тесты identity проверяют mint/verify, а capabilities — invoke на
    «своём» claims. Здесь подтверждается, что ОДИН и тот же проверенный
    ``PluginClaims`` (с ``typ=plugin_capability``, своим ``iss``/``aud``)
    принимается ядром capabilities, а ``GrantRevocationPort`` и ``AuditPort``
    подключены к тому же состоянию, что и identity.
    """

    platform.grant_store.seed_active_grant()

    # 1) identity: подпись и claims уже проверены фикстурой valid_claims.
    assert valid_claims.typ == "plugin_capability"
    assert valid_claims.grant_id == TEST_GRANT_ID
    assert valid_claims.company_id == TEST_COMPANY
    assert valid_claims.artifact_digest == TEST_DIGEST
    # В токене НЕТ долгоживущих политик (кр. 22): только grant_id + короткий TTL.
    assert not hasattr(valid_claims, "egress")
    assert not hasattr(valid_claims, "quotas")

    # 2) revocation: тот же grant_id активен для capabilities.
    assert await platform.revocation.is_active(TEST_GRANT_ID, now=FIXED_NOW) is True

    # 3) capabilities: реальный сервис на реальном ядре gateway.
    service = platform.capability_service()
    decision = await service.invoke(
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


@pytest.mark.asyncio
async def test_exact_grant_quotas_drive_enforcement_not_runtime_defaults(
    platform: Platform, valid_claims: Any
) -> None:
    contracts = pytest.importorskip("prodavan.application.plugins.admin.contracts")
    quota_module = pytest.importorskip("prodavan.application.plugins.capabilities.quotas")
    effective = contracts.QuotaSet(
        ops_per_minute=1,
        max_rows=1,
        max_bytes=2,
        max_keys=1,
        max_object_bytes=2,
        max_egress_bytes=2,
    )
    seeded = platform.grant_store.seed_active_grant(quotas=effective)
    assert seeded.quotas == effective
    await platform.instance_store.create(
        instance=contracts.PluginInstanceRecord(
            plugin_instance_id=TEST_INSTANCE,
            company_id=TEST_COMPANY,
            plugin_id=TEST_PLUGIN,
            manifest_version=TEST_VERSION,
            artifact_digest=TEST_DIGEST,
            status=contracts.PluginInstanceStatus.ACTIVE,
            tenant_db_name="tenant_co_alpha",
            bucket_name="tenant-co-alpha",
            created_at=FIXED_NOW,
            updated_at=FIXED_NOW,
        )
    )
    admin_snapshot = await platform.admin_service().list(company_id=TEST_COMPANY)
    assert admin_snapshot.items[0].quotas == effective

    service = platform.capability_service()
    first = await service.invoke(
        claims=valid_claims,
        capability="cache",
        operation="get",
        request=make_cache_get_request(),
        now=FIXED_NOW,
    )
    second = await service.invoke(
        claims=valid_claims,
        capability="cache",
        operation="get",
        request=make_cache_get_request(),
        now=FIXED_NOW,
    )

    assert first.allowed is True
    assert second.allowed is False
    assert second.http_status == 429
    assert second.code.value == "quota_exceeded"
    ops = [item for item in platform.quota.decisions if item.op is quota_module.QuotaOp.OPS]
    assert [item.limit for item in ops] == [1, 1]
    assert ops[-1].allowed is False
    assert admin_snapshot.items[0].quotas.ops_per_minute == ops[-1].limit
    # Read-only grant evidence и enforcement используют один durable QuotaSet.
    stored = await platform.grant_store.get_by_grant_id(grant_id=TEST_GRANT_ID)
    assert stored.quotas == effective


@pytest.mark.asyncio
async def test_missing_revocation_port_fails_closed_on_real_service(
    platform: Platform, valid_claims: Any
) -> None:
    """Стык fail-closed: без подключённого порта отзывов invoke = deny.

    Здесь проверяется, что собранная платформа НЕ имеет «тихого» дефолта,
    когда интегратор забыл связать identity-реестр с capability-сервисом.
    """

    caps = pytest.importorskip("prodavan.application.plugins.capabilities")
    platform.grant_store.seed_active_grant()
    registry = caps.AdapterRegistry()
    registry.register("cache", platform.cache_adapter)
    service = caps.PluginCapabilityService(
        registry=registry,
        grant_store=platform.grant_store,
        quota=platform.quota,
        audit=platform.audit,
        revocation=None,
    )
    decision = await service.invoke(
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


@pytest.mark.asyncio
async def test_revoke_denies_same_token_on_capabilities(
    platform: Platform, valid_claims: Any
) -> None:
    """Отзыв по ``grant_id`` деактивирует ещё НЕ истёкший токен мгновенно."""

    platform.grant_store.seed_active_grant()
    service = platform.capability_service()
    request = make_cache_get_request()

    before = await service.invoke(
        claims=valid_claims, capability="cache", operation="get",
        request=request, now=FIXED_NOW,
    )
    assert before.allowed is True

    # Тот же токен, тот же grant_id — но реестр отзывов уже деактивирован.
    await platform.revocation.revoke(TEST_GRANT_ID, reason="admin revoke")
    assert await platform.revocation.is_active(TEST_GRANT_ID, now=FIXED_NOW) is False

    after = await service.invoke(
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
    assert allowed.grant.artifact_digest == TEST_DIGEST
    assert allowed.grant.published_artifact_digest == TEST_DIGEST

    await platform.revocation.revoke(TEST_GRANT_ID, reason="admin revoke")
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


# ---------------------------------------------------------------------------
# CI publication fixture: durable approve -> PluginCiPublicationService -> grant
# ---------------------------------------------------------------------------

CI_COMMIT = "a" * 40
"""Immutable commit SHA, к которому привязаны OIDC claims и OCI annotations."""

CI_WORKFLOW = (
    "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "b" * 40
)
"""Trusted reusable workflow на immutable SHA (НЕ ``refs/heads/main``)."""


@dataclass(frozen=True)
class _CiFixture:
    """Реальный CI service + реестр + scoped source/OIDC/loader doubles."""

    source: Any
    command: Any
    service: Any
    registry: Any
    store: Any
    oidc: Any


def _ci_publication_fixture(
    platform: Platform, manifest: dict[str, Any]
) -> _CiFixture:
    """Собрать ``PluginCiPublicationService`` на ОБЩИХ acceptance-фейках.

    ``approval_reader`` — тот же ``FakePublicationApprovalStore``, в который
    ``admin.approve_publication`` пишет durable решение: CI и admin видят ОДИН
    exact binding. ``source_repository``/``oidc_validator``/``artifact_loader``
    — scoped test doubles (сети/OCI registry здесь нет и не должно быть);
    verifier, registry, cosign и часы — реальные production-классы платформы.
    """

    publication = pytest.importorskip(
        "prodavan.application.plugins.artifacts.publication"
    )
    artifacts = pytest.importorskip("prodavan.application.plugins.artifacts")
    sources = pytest.importorskip("prodavan.application.plugins.sources.types")
    oidc_module = pytest.importorskip("prodavan.application.plugins.artifacts.oidc")

    source = sources.ModuleSource(
        id="psrc_acceptance_ci",
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        repo_id=424242,
        full_name="ne-tort/agentscale",
        private=True,
        default_branch="main",
        installation_credential_ref=(
            f"vault://github_app/{TEST_COMPANY}/{TEST_PLUGIN}/installation"
        ),
        trusted_workflow_ref=CI_WORKFLOW,
        status=sources.ModuleSourceStatus.READY,
        created_at=FIXED_NOW,
        updated_at=FIXED_NOW,
    )
    claims = oidc_module.GitHubActionsClaims(
        repository=source.full_name,
        repository_id=str(source.repo_id),
        ref="refs/heads/main",
        sha=CI_COMMIT,
        event_name="push",
        job_workflow_ref=CI_WORKFLOW,
        run_id="123",
        run_attempt="2",
    )

    class _SourceRepository:
        async def get_by_id(self, source_id: str) -> Any:
            assert source_id == source.id
            return source

    class _Oidc:
        def __init__(self) -> None:
            self.calls = 0

        async def validate_for_source(self, token: str, **kwargs: Any) -> Any:
            self.calls += 1
            assert token == "acceptance-oidc-token"
            return claims

    canonical_data = publication._canonical_manifest_bytes(manifest)
    config_digest = "sha256:" + hashlib.sha256(canonical_data).hexdigest()
    backend_digest = "sha256:" + "b" * 64
    backend_ref = f"ghcr.io/ne-tort/plugin-backends/{TEST_PLUGIN}@" + backend_digest
    oci = {
        "schemaVersion": 2,
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "artifactType": "application/vnd.agentscale.plugin.oci",
        "config": {
            "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
            "digest": config_digest,
            "size": len(canonical_data),
        },
        "layers": [
            {
                "mediaType": "application/vnd.agentscale.plugin.manifest.v1+json",
                "digest": config_digest,
                "size": len(canonical_data),
                "annotations": {
                    "com.agentscale.plugin.layer.role": "canonical-manifest",
                    "org.opencontainers.image.title": "canonical-manifest.json",
                },
            },
            {
                "mediaType": "application/vnd.agentscale.plugin.frontend",
                "digest": "sha256:" + "d" * 64,
                "size": 10,
                "annotations": {
                    "com.agentscale.plugin.layer.role": "frontend-dist"
                },
            },
            {
                "mediaType": "application/vnd.agentscale.plugin.liquibase+xml",
                "digest": "sha256:" + "e" * 64,
                "size": 10,
                "annotations": {"com.agentscale.plugin.layer.role": "migrations"},
            },
        ],
        "annotations": {
            "plugin_id": TEST_PLUGIN,
            "manifest_version": TEST_VERSION,
            "protocol": "plugin-rpc/1",
            "requestedCapabilities": ",".join(manifest["capabilities"]),
            "min_platform_version": "4.0.0",
            "backend_digest": backend_digest,
            "com.agentscale.plugin.backend_image_ref": backend_ref,
            "com.agentscale.plugin.source_repository": source.full_name,
            "com.agentscale.plugin.source_sha": CI_COMMIT,
            "com.agentscale.plugin.manifest_digest": config_digest,
        },
    }
    remote = json.dumps(oci, sort_keys=True, separators=(",", ":")).encode("utf-8")
    bundle_digest = "sha256:" + hashlib.sha256(remote).hexdigest()
    bundle_ref = f"ghcr.io/ne-tort/plugins/{TEST_PLUGIN}/bundle@" + bundle_digest

    class _Loader:
        """Scoped OCI loader: байты только этого bundle, digest проверяется."""

        async def load_manifest(self, ref: Any) -> Any:
            assert ref.digest == bundle_digest
            return publication.LoadedOciObject(
                data=remote, digest=ref.digest, size=len(remote)
            )

        async def load_blob(
            self, ref: Any, *, digest: str, expected_size: int | None = None
        ) -> Any:
            assert digest == config_digest
            assert expected_size == len(canonical_data)
            return publication.LoadedOciObject(
                data=canonical_data, digest=digest, size=len(canonical_data)
            )

    command = publication.ArtifactPublicationCommand(
        source_id=source.id,
        plugin_id=TEST_PLUGIN,
        version=TEST_VERSION,
        bundle_ref=bundle_ref,
        bundle_digest=bundle_digest,
        backend_ref=backend_ref,
        backend_digest=backend_digest,
        manifest=oci,
        commit_sha=CI_COMMIT,
        ref="refs/heads/main",
        run_url=(
            f"https://github.com/{source.full_name}/actions/runs/123/attempts/2"
        ),
        evidence=publication.PublicationEvidence(
            scan_verified=True, csp_verified=True
        ),
    )
    verifier = artifacts.ArtifactVerifier(
        platform.cosign,
        validator=platform.validator,
        # Cosign identity — trusted workflow на immutable SHA: CI service
        # сверяет evidence.identity с source.expected_signer_identity.
        identity=source.expected_signer_identity,
        issuer=ISSUER,
        compatibility=artifacts.CompatibilityGate(platform_version="4.0.0"),
        clock=lambda: platform.clock.now(),
    )
    store = artifacts.InMemoryRegistryStore()
    registry = artifacts.PluginArtifactRegistry(
        store=store, verifier=verifier, clock=lambda: platform.clock.now()
    )
    oidc = _Oidc()
    service = publication.PluginCiPublicationService(
        source_repository=_SourceRepository(),
        oidc_validator=oidc,
        artifact_loader=_Loader(),
        verifier=verifier,
        artifact_registry=registry,
        approval_reader=platform.publication_approvals,
        instance_scope_resolver=publication.DurablePluginInstanceScopeResolver(
            grant_store=platform.grant_store,
            instance_store=platform.instance_store,
        ),
        # The publication boundary requires the typed domain result before
        # deterministic scope validation; verifier still uses platform.validator.
        manifest_validator=validate_manifest,
    )
    return _CiFixture(
        source=source,
        command=command,
        service=service,
        registry=registry,
        store=store,
        oidc=oidc,
    )


# ===========================================================================
# 4. Grant.capabilities УЖЕ, чем manifest.capabilities -> INCOMPATIBLE / QUARANTINED
#    Стык: artifacts↔CapabilityGrant (критерий №18, §5.1 compatibility gate)
# ===========================================================================


@pytest.mark.asyncio
async def test_narrow_grant_quarantines_artifact(
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
    result = await registry.publish_async(
        oci_ref,
        valid_manifest,
        outcome.evidence,
        grant=narrow,
        metadata=_publication_metadata(),
    )
    assert result.outcome is RegistryOutcome.QUARANTINED
    assert artifacts.COMPATIBILITY_ERROR_CODE == "INCOMPATIBLE"


@pytest.mark.asyncio
async def test_legacy_direct_registry_publish_is_not_ci_approval_contract(
    platform: Platform, valid_manifest: dict[str, Any], oci_ref: Any
) -> None:
    """LEGACY unit-стык: direct verifier → ``publish_async`` → admin-гейт.

    Это НЕ полный CI approval contract: здесь нет ни durable
    ``PluginPublicationApproval``, ни ``PluginCiPublicationService``
    (OIDC/OCI/exact approval binding), ни grant из пустого target state.
    Тест намеренно сохранён как узкий legacy unit для compatibility gate и
    PUBLISHED-гейта кр. 18; полный путь ``approve → CI publish → grant``
    проверяет ``test_ci_approval_publish_then_admin_grant_from_empty_state``.
    """

    from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome

    registry = platform.artifact_registry()
    full = _GrantCapabilities(
        capabilities=tuple(valid_manifest["capabilities"])
    )
    outcome = _verifier_of(registry).verify(
        oci_ref,
        valid_manifest,
        grant=full,
        scan_verified=True,
        # Вариант A: CSP подтверждается static-origin, а не полем манифеста.
        csp_verified=True,
    )
    assert outcome.evidence.verified is True
    metadata = _publication_metadata()
    published = await registry.publish_async(
        oci_ref,
        valid_manifest,
        outcome.evidence,
        grant=full,
        metadata=metadata,
    )
    assert published.outcome is RegistryOutcome.APPLIED
    record = registry.current(TEST_PLUGIN)
    assert record is not None
    assert record.digest == oci_ref.digest
    assert record.publication is not None
    assert record.publication == metadata
    assert record.publication.backend_digest != oci_ref.digest
    assert record.publication.backend_image_ref.endswith("@" + metadata.backend_digest)

    # Тот же digest виден админскому порту (PUBLISHED-гейт кр. 18).
    platform.artifact_admin.seed_published()
    assert platform.artifact_admin.records[
        (TEST_PLUGIN, TEST_VERSION, TEST_DIGEST)
    ].is_published


@pytest.mark.asyncio
async def test_ci_approval_publish_then_admin_grant_from_empty_state(
    platform: Platform, valid_manifest: dict[str, Any]
) -> None:
    """ПОЛНЫЙ CI approval contract: approve → CI publish → admin grant.

    В отличие от legacy direct-registry unit выше:
      * durable approval создаётся ТОЛЬКО через ``admin.approve_publication``;
      * публикацию выполняет ``PluginCiPublicationService`` (source → OIDC →
        OCI bundle → exact approval binding → verifier → registry), а не
        прямой ``registry.publish_async`` с заранее готовым evidence;
      * admin читает PUBLISHED через production ``ArtifactRegistryAdapter``
        поверх ТОГО ЖЕ реестра — без ``seed_published()``;
      * target state (grants/instances) перед grant ПУСТОЙ: ACTIVE появляется
        только из grant-saga, никакого ``seed_active_grant``.
    """

    contracts = pytest.importorskip("prodavan.application.plugins.admin.contracts")
    adapters = pytest.importorskip(
        "prodavan.application.plugins.adapters.artifacts_adapter"
    )
    fixture = _ci_publication_fixture(platform, valid_manifest)
    capability_set = make_capability_set(
        capabilities=tuple(valid_manifest["capabilities"])
    )
    bundle_digest = fixture.command.bundle_digest
    admin = platform.admin_service()

    # 0) Пустой target state: ни grant, ни instance, ни legacy artifact seed.
    assert platform.grant_store.grants == {}
    assert platform.instance_store.instances == {}
    assert platform.artifact_admin.records == {}

    # 1) Administrative pre-approval с EXACT bundle/capability binding.
    approval_receipt = await admin.approve_publication(
        actor_id="acceptance-platform-admin",
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=bundle_digest,
        capabilities=capability_set,
    )
    assert approval_receipt.created is True
    assert approval_receipt.approval.consumed_at is None

    # 2) CI publication читает ТОТ ЖЕ approval store, но НЕ создаёт grant.
    response = await fixture.service.publish(
        fixture.command, oidc_token="acceptance-oidc-token"
    )
    assert response.status == "PUBLISHED", (
        f"CI publication с exact approval обязана пройти: {response.code}"
    )
    record = fixture.registry.current(TEST_PLUGIN)
    assert record is not None
    assert record.digest == bundle_digest
    assert record.publication is not None
    assert record.publication.commit_sha == CI_COMMIT
    pending = await platform.publication_approvals.get(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=bundle_digest,
    )
    assert pending is not None and pending.consumed_at is None, (
        "CI publish НЕ должен consume approval: consumption принадлежит grant-saga"
    )
    assert platform.grant_store.grants == {}, "CI никогда не создаёт CapabilityGrant"

    # 3) admin grant из пустого target state через production adapter.
    ci_admin = platform.admin_service(
        artifact_registry=adapters.ArtifactRegistryAdapter(
            registry=fixture.registry, store=fixture.store
        )
    )
    receipt = await ci_admin.grant(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=bundle_digest,
        capabilities=capability_set,
    )

    assert receipt.replayed is False
    assert receipt.grant.status is contracts.GrantStatus.ACTIVE
    assert receipt.grant.artifact_digest == bundle_digest
    # Долгоживущая политика берётся из APPROVAL, а не из тела запроса/JWT.
    assert receipt.grant.capabilities == capability_set.capabilities
    assert receipt.grant.egress_policy == capability_set.egress_policy
    assert receipt.grant.quotas == capability_set.quotas
    assert receipt.grant.webhook_secret_ref is not None
    assert receipt.routes.grant_id == receipt.grant.grant_id
    instance = await platform.instance_store.get(
        company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN
    )
    assert instance is not None
    assert instance.status is contracts.PluginInstanceStatus.ACTIVE
    assert instance.plugin_instance_id == receipt.plugin_instance_id
    # Canonical identity initial grant: тот же deterministic instance id, что
    # вычисляется из (company, plugin) ДО source/approval/grant. CI source и
    # admin saga обязаны адресовать ОДНУ установку, не «inst_alpha_1»-literal.
    helpers = pytest.importorskip("prodavan.domain.plugins.helpers")
    assert receipt.plugin_instance_id == helpers.plugin_instance_id_for(
        TEST_COMPANY, TEST_PLUGIN
    )
    assert receipt.plugin_instance_id == TEST_INSTANCE
    assert fixture.source.plugin_instance_id == receipt.plugin_instance_id

    # 4) Approval consumed РОВНО один раз и только grant-saga.
    consumed = await platform.publication_approvals.get(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=bundle_digest,
    )
    assert consumed is not None
    assert consumed.consumed_at == FIXED_NOW
    assert [
        approval_id for approval_id, _ts in platform.publication_approvals.consumed
    ] == [approval_receipt.approval.approval_id]

    # 5) Control-plane pre-delivery — явная фаза ДО Deployment в grant-saga.
    assert [
        request.request_id for request in platform.secret_delivery.pre_deliver_calls
    ] == [f"grant:{receipt.plugin_instance_id}:{bundle_digest}"]
    assert (TEST_COMPANY, TEST_PLUGIN) in platform.activation_admin.deployments
    assert (TEST_COMPANY, TEST_PLUGIN) in platform.tenant_db.schemas
    assert f"module-{TEST_PLUGIN}/" in platform.objects_admin.buckets[
        f"tenant-{TEST_COMPANY}"
    ]
    # Legacy direct-registry seed не использовался ни на одном шаге.
    assert platform.artifact_admin.records == {}


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
    # 1) Артефакт опубликован (кр. 18) — иначе grant fail-closed. Это legacy
    # seed ONLY артефактного PUBLISHED-гейта, НЕ подмена durable approval и НЕ
    # seed ACTIVE grant: target state ниже остаётся пустым.
    platform.artifact_admin.seed_published()

    service = platform.admin_service()
    # Новый/revoked grant требует durable EXACT publication approval:
    # approve → grant, consumption — только в grant-saga (§11 кр. 18/12).
    approval_receipt = await service.approve_publication(
        actor_id="acceptance-platform-admin",
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )
    assert approval_receipt.created is True
    assert platform.grant_store.grants == {}, "approval не создаёт grant"

    receipt = await service.grant(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )

    assert receipt.grant.status is contracts.GrantStatus.ACTIVE
    assert receipt.grant.grant_id
    # Initial grant создаёт canonical deterministic instance (domain.helpers),
    # а не произвольный/literal id из тела запроса.
    helpers = pytest.importorskip("prodavan.domain.plugins.helpers")
    assert receipt.plugin_instance_id == helpers.plugin_instance_id_for(
        TEST_COMPANY, TEST_PLUGIN
    )
    assert receipt.plugin_instance_id == TEST_INSTANCE
    assert receipt.database.database_name == f"tenant_{TEST_COMPANY}"
    assert receipt.schema.schema_name == f"module_{TEST_PLUGIN}"
    assert receipt.objects.bucket == f"tenant-{TEST_COMPANY}"
    assert receipt.routes.grant_id == receipt.grant.grant_id
    # DSN наружу не ушёл: только secret_ref.
    assert "secret_ref" in receipt.database.dsn_ref
    assert "postgresql://" not in repr(receipt)
    assert receipt.replayed is False

    # Exact approval consume ровно один раз и только grant-saga (§5.5).
    stored_approval = await platform.publication_approvals.get(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
    )
    assert stored_approval is not None
    assert stored_approval.consumed_at == FIXED_NOW
    assert [
        approval_id for approval_id, _ts in platform.publication_approvals.consumed
    ] == [approval_receipt.approval.approval_id]

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
# 5c. revoke→re-grant: stable instance, fresh grant, revoked id остаётся inactive
# ===========================================================================


@pytest.mark.asyncio
async def test_revoke_then_regrant_keeps_instance_and_never_resurrects_old_grant(
    platform: Platform,
) -> None:
    contracts = pytest.importorskip("prodavan.application.plugins.admin.contracts")
    platform.artifact_admin.seed_published()
    service = platform.admin_service()
    await service.approve_publication(
        actor_id="acceptance-platform-admin",
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )
    grant_args = {
        "company_id": TEST_COMPANY,
        "plugin_id": TEST_PLUGIN,
        "manifest_version": TEST_VERSION,
        "artifact_digest": TEST_DIGEST,
        "capabilities": make_capability_set(),
    }
    first = await service.grant(**grant_args)
    await service.revoke(company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN)

    second = await service.grant(**grant_args)

    assert second.plugin_instance_id == first.plugin_instance_id == TEST_INSTANCE
    assert second.grant.grant_id != first.grant.grant_id
    assert second.grant.status is contracts.GrantStatus.ACTIVE
    assert second.grant.revoked_at is None
    old = platform.grant_store.history[first.grant.grant_id]
    assert old.status is contracts.GrantStatus.REVOKED
    assert old.revoked_at is not None
    assert not old.is_active
    assert await platform.revocation.is_active(
        first.grant.grant_id, now=FIXED_NOW
    ) is False
    assert await platform.revocation.is_active(
        second.grant.grant_id, now=FIXED_NOW
    ) is True
    current = await platform.grant_store.get(
        company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN
    )
    assert current == second.grant
    assert platform.grant_store.grants == {
        (TEST_COMPANY, TEST_PLUGIN): second.grant
    }


# ===========================================================================
# 5d. uninstall→reinstall: новая approval/publish, stable instance и fresh grant
# ===========================================================================


@pytest.mark.asyncio
async def test_uninstall_then_reinstall_keeps_instance_and_uses_fresh_grant(
    platform: Platform,
) -> None:
    contracts = pytest.importorskip("prodavan.application.plugins.admin.contracts")
    platform.artifact_admin.seed_published()
    service = platform.admin_service()
    await service.approve_publication(
        actor_id="acceptance-platform-admin",
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )
    first = await service.grant(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(),
    )
    removed = await service.uninstall(
        company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN
    )
    assert removed.fully_removed is True
    assert await platform.revocation.is_active(
        first.grant.grant_id, now=FIXED_NOW
    ) is False

    # Consumed approval is not reused for a new installation. A newly approved
    # artifact preserves the deterministic installation identity, not the grant PK.
    reinstall_version = "1.2.1"
    reinstall_digest = "sha256:" + "b" * 64
    platform.artifact_admin.seed_published(
        manifest_version=reinstall_version,
        artifact_digest=reinstall_digest,
    )
    approval = await service.approve_publication(
        actor_id="acceptance-platform-admin",
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=reinstall_version,
        artifact_digest=reinstall_digest,
        capabilities=make_capability_set(),
    )
    assert approval.created is True
    second = await service.grant(
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=reinstall_version,
        artifact_digest=reinstall_digest,
        capabilities=make_capability_set(),
    )

    assert second.plugin_instance_id == first.plugin_instance_id == TEST_INSTANCE
    assert second.grant.grant_id != first.grant.grant_id
    assert second.grant.status is contracts.GrantStatus.ACTIVE
    assert second.grant.revoked_at is None
    assert await platform.revocation.is_active(
        first.grant.grant_id, now=FIXED_NOW
    ) is False
    assert await platform.revocation.is_active(
        second.grant.grant_id, now=FIXED_NOW
    ) is True
    assert platform.grant_store.grants == {
        (TEST_COMPANY, TEST_PLUGIN): second.grant
    }


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

    # Каждый company/plugin/SemVer/digest требует СВОЕГО exact approval:
    # общий OCI-digest не «делится» между компаниями (durable approval binding).
    for company_id in (TEST_COMPANY, TEST_OTHER_COMPANY):
        approval_receipt = await service.approve_publication(
            actor_id="acceptance-platform-admin",
            company_id=company_id,
            plugin_id=TEST_PLUGIN,
            manifest_version=TEST_VERSION,
            artifact_digest=TEST_DIGEST,
            capabilities=make_capability_set(),
        )
        assert approval_receipt.created is True

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
# 6b. Fail-closed: новый grant из пустого state требует EXACT approval (§11)
# ===========================================================================


@pytest.mark.asyncio
async def test_new_grant_from_empty_state_requires_exact_publication_approval(
    platform: Platform,
) -> None:
    """Без durable exact approval новый grant не создаёт НИЧЕГО.

    Negative-компаньон полного CI contract: legacy ``seed_published()``
    открывает ТОЛЬКО artifact-гейт, но не заменяет durable approval; approval
    с другим capability binding тоже не расширяет право на grant.
    """

    errors = pytest.importorskip("prodavan.domain.errors")
    platform.artifact_admin.seed_published()
    service = platform.admin_service()

    # 1) Approval отсутствует -> PUBLICATION_APPROVAL_REQUIRED, state пустой.
    with pytest.raises(errors.AppError) as missing:
        await service.grant(
            company_id=TEST_COMPANY,
            plugin_id=TEST_PLUGIN,
            manifest_version=TEST_VERSION,
            artifact_digest=TEST_DIGEST,
            capabilities=make_capability_set(),
        )
    assert missing.value.code == "PUBLICATION_APPROVAL_REQUIRED"
    assert platform.grant_store.grants == {}
    assert platform.instance_store.instances == {}
    assert platform.secret_delivery.pre_deliver_calls == []

    # 2) Approval с ДРУГИМ capability binding не разрешает этот grant.
    await service.approve_publication(
        actor_id="acceptance-platform-admin",
        company_id=TEST_COMPANY,
        plugin_id=TEST_PLUGIN,
        manifest_version=TEST_VERSION,
        artifact_digest=TEST_DIGEST,
        capabilities=make_capability_set(capabilities=("cache",)),
    )
    with pytest.raises(errors.AppError) as mismatch:
        await service.grant(
            company_id=TEST_COMPANY,
            plugin_id=TEST_PLUGIN,
            manifest_version=TEST_VERSION,
            artifact_digest=TEST_DIGEST,
            capabilities=make_capability_set(),
        )
    assert mismatch.value.code == "PUBLICATION_APPROVAL_BINDING_MISMATCH"
    assert platform.grant_store.grants == {}
    assert platform.instance_store.instances == {}
    assert platform.secret_delivery.pre_deliver_calls == []


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
    platform.subscriptions.register(subscription, active=True)

    # 1) Простой: записей нет.
    assert await dispatcher.consume(subscription) is None
    assert platform.consumer.commits == [], "при простое коммит недопустим"
    assert platform.consumer.polls == 1

    # 2) Событие появилось: wake ДО handler, commit ПОСЛЕ.
    event = make_event()
    signed_wire = platform.event_signer().signed_wire(
        event, topic="agentscale.plugin.events"
    )
    platform.consumer.records.append(FakeRecord(value=signed_wire))
    outcome = await dispatcher.consume(subscription)
    assert outcome is events.DeliveryOutcome.ACK
    assert platform.consumer.commits == [17]
    assert len(platform.wake_hook.calls) == 1
    assert len(platform.handler.calls) == 1
    assert platform.raw_dlq.publish_calls == []
    # Аудит событий не содержит raw payload, только канонические id/digest.
    assert platform.audit.typed
    assert all(
        "payload_digest" in details for _, details in platform.audit.typed
    )


@pytest.mark.asyncio
async def test_signed_event_duplicate_acks_without_second_delivery(
    platform: Platform,
) -> None:
    """Дедуп по subscription/source event сохраняет ACK, но не повторяет handler."""

    events = pytest.importorskip("prodavan.application.plugins.events")
    dispatcher = platform.event_dispatcher()
    subscription = make_subscription()
    platform.subscriptions.register(subscription, active=True)
    event = make_event()
    signed_wire = platform.event_signer().signed_wire(
        event, topic="agentscale.plugin.events"
    )
    platform.consumer.records.extend(
        [FakeRecord(value=signed_wire), FakeRecord(value=signed_wire)]
    )

    first = await dispatcher.consume(subscription)
    duplicate = await dispatcher.consume(subscription)

    assert first is events.DeliveryOutcome.ACK
    assert duplicate is events.DeliveryOutcome.ACK
    assert platform.consumer.commits == [17, 17]
    assert len(platform.wake_hook.calls) == 1
    assert len(platform.handler.calls) == 1
    assert len(platform.idempotency.remembered) == 1
    assert platform.raw_dlq.publish_calls == []


@pytest.mark.asyncio
async def test_malformed_event_uses_durable_raw_dlq_and_dedup(
    platform: Platform,
) -> None:
    """Malformed bytes идут только в raw DLQ; retry подтверждает natural key."""

    events = pytest.importorskip("prodavan.application.plugins.events")
    dispatcher = platform.event_dispatcher()
    subscription = make_subscription()
    platform.subscriptions.register(subscription, active=True)
    poison = b'{"event_id":"untrusted","payload":'
    consumed = FakeRecord(
        value={}, raw_value=poison, partition=3, offset=91
    )
    platform.consumer.records.append(consumed)

    first = await dispatcher.consume(subscription)

    assert first is events.DeliveryOutcome.DLQ
    assert platform.consumer.commits == [91]
    assert platform.handler.calls == []
    assert platform.wake_hook.calls == []
    assert platform.dlq.published == []
    assert len(platform.raw_dlq.records) == 1
    raw = platform.raw_dlq.records[0]
    assert raw.topic == "agentscale.plugin.events"
    assert raw.partition == 3
    assert raw.offset == 91
    assert raw.consumer_group == subscription.consumer_group
    assert raw.error_kind == "JSONDecodeError"
    assert raw.payload_digest == f"sha256:{hashlib.sha256(poison).hexdigest()}"
    assert "payload" not in raw.to_wire()
    assert "event_id" not in raw.to_wire()

    # Повтор после неопределённого результата/commit retry не создаёт дубль.
    platform.consumer.records.append(consumed)
    second = await dispatcher.consume(subscription)
    assert second is events.DeliveryOutcome.DLQ
    assert platform.consumer.commits == [91, 91]
    assert len(platform.raw_dlq.publish_calls) == 2
    assert len(platform.raw_dlq.records) == 1


@pytest.mark.asyncio
async def test_event_subscription_negative_cases_are_keyed_by_id(
    platform: Platform,
) -> None:
    """Чужая id не наследует active подписки; revoked id отклоняется до poll."""

    events = pytest.importorskip("prodavan.application.plugins.events")
    dispatcher = platform.event_dispatcher()
    subscription = make_subscription()
    unknown = events.Subscription(
        id="sub_unknown",
        plugin_id=TEST_PLUGIN,
        event_types=subscription.event_types,
        consumer_group="plugin-mod_equipment-sub_unknown",
    )
    platform.subscriptions.register(subscription, active=True)

    with pytest.raises(events.PluginEventError) as missing:
        await dispatcher.consume(unknown)
    assert missing.value.code is events.PluginEventErrorCode.UNKNOWN_SUBSCRIPTION

    platform.subscriptions.register(subscription, active=False)
    with pytest.raises(events.PluginEventError) as revoked:
        await dispatcher.consume(subscription)
    assert revoked.value.code is events.PluginEventErrorCode.UNKNOWN_SUBSCRIPTION
    assert platform.consumer.polls == 0
    assert platform.consumer.commits == []
    assert platform.handler.calls == []


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
    seeded = platform.grant_store.seed_active_grant()
    # NON-default quota: resolver/enforcement обязаны читать exact grant.quotas,
    # а не hardcoded default фейка (get_grant раньше возвращал 1_000_000).
    assert seeded.quotas.max_egress_bytes == TEST_QUOTA_VALUES["max_egress_bytes"]
    assert seeded.quotas.max_egress_bytes != 1_000_000, "нужен NON-default лимит"
    lookup = platform.grant_store.get_grant(TEST_GRANT_ID)
    assert lookup is not None
    assert (
        lookup.raw["egress"]["max_egress_bytes"]
        == seeded.quotas.max_egress_bytes
    ), "get_grant обязан маппить max_egress_bytes из grant.quotas"
    resolver = platform.egress_resolver()

    fresh = resolver.resolve_claims(valid_claims)
    assert fresh.max_egress_bytes == TEST_QUOTA_VALUES["max_egress_bytes"], (
        "resolver обязан взять квоту из durable grant, а не из default"
    )
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


@pytest.mark.asyncio
async def test_capabilities_egress_port_reads_grant_not_token(
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
        grant_store=platform.grant_store,
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
    decision = await service.invoke(
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
    assert page.claims.anonymous is True
    assert page.claims.artifact_digest == TEST_DIGEST
    assert page.grant.artifact_digest == TEST_DIGEST
    assert page.grant.published_artifact_digest == TEST_DIGEST
    assert page.grant.capability_token == ""
    assert page.grant.operations == frozenset()

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
    assert first.claims.plugin_instance_id == TEST_INSTANCE
    assert first.grant.plugin_instance_id == TEST_INSTANCE
    assert first.claims.artifact_digest == TEST_DIGEST
    assert first.grant.artifact_digest == TEST_DIGEST
    assert first.grant.published_artifact_digest == TEST_DIGEST
    for method in ("claim_delivery_lease", "commit_delivery", "release_delivery"):
        assert callable(getattr(platform.rate_limit, method, None))

    # До commit повтор — processing lease, а НЕ ложный duplicate из legacy fake.
    with pytest.raises(gateway.GatewayError) as in_progress:
        await auth.authorize_webhook(
            plugin_id=TEST_PLUGIN, company_id=TEST_COMPANY,
            path="/webhook", method="POST", raw_body=body, headers=headers,
        )
    assert in_progress.value.gateway_code is gateway.GatewayErrorCode.DELIVERY_IN_PROGRESS

    # Duplicate допустим только после confirmed backend success, как в production.
    await auth.commit_webhook(first)

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

    before = await caps_service.invoke(
        claims=valid_claims, capability="cache", operation="get",
        request=make_cache_get_request(), now=FIXED_NOW,
    )
    assert before.allowed is True

    receipt = await admin.revoke(company_id=TEST_COMPANY, plugin_id=TEST_PLUGIN)
    assert receipt.token_revoked is True

    # Общий реестр отзывов: capabilities видит отзыв без дополнительной связи.
    after = await caps_service.invoke(
        claims=valid_claims, capability="cache", operation="get",
        request=make_cache_get_request(), now=FIXED_NOW,
    )
    assert after.allowed is False
    assert platform.audit.events, "revoke и deny обязаны писать аудит"


@pytest.mark.asyncio
async def test_activation_wake_token_is_verifiable_by_identity_keyring(
    platform: Platform,
) -> None:
    """Стык activation↔identity: токен из ``MintPort`` проверяется KeyRing'ом.

    Критерий №13 требует НОВЫЙ короткий токен при wake. Мало получить строку
    от ``MintPort`` — надо убедиться, что её примет тот же trust-контур,
    который проверяет входящие запросы (иначе wake выдаст мусор).
    """

    identity = pytest.importorskip("prodavan.application.plugins.identity")
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


__all__: list[str] = []
