# Руководство по F11-интеграции plugin-платформы (v4)

> **HISTORICAL REPORT / NOT CURRENT ACCEPTANCE.** Содержимое относится к ранней
> волне и не переподтверждено на audited HEAD `6ee720dd`. Любые «реализовано»,
> `[x]`, `acceptance ✓`, «эталон» и прошлые test counts — только historical
> claims. Актуальный статус: [STOP-SHIP](plugin-migration-status.md) и
> [live-only acceptance map](plugin-acceptance-map.md). Написанный код ≠ live.

**Статус:** справочный документ для родителя и будущих разработчиков, готовящих F11.
**Источник истины:** [`docs/ARCHITECTURE/plugin-architecture-complete.md`](../ARCHITECTURE/plugin-architecture-complete.md) (v4), [`docs/migration/plugin-migration-plan.md`](plugin-migration-plan.md), [`docs/migration/plugin-acceptance-map.md`](plugin-acceptance-map.md), [`docs/migration/plugin-test-plan.md`](plugin-test-plan.md), [`docs/migration/minio-sts-verification.md`](minio-sts-verification.md), [`docs/migration/plugin-network-alignment.md`](plugin-network-alignment.md), ADR-001…008 в [`docs/architecture-migration/`](../architecture-migration/).
**Контур:** `main` (dev), worktree `wt-migration`. **Документационная задача:** код, конфиги и существующие `docs/**` этим файлом не изменяются.
**Верификация:** тесты, сборки, линт, `git`, `kubectl` **не запускались** (verification freeze, план §0.2, дизайн §9).

---

## 0. Что уже лежит на диске (волна 1) и чего ещё нет

| Слой | Что создано | Чего НЕТ (цель F11) |
|---|---|---|
| `domain/plugins/**` | типы, `__init__.py` с `__all__`, валидация манифеста | — |
| `application/plugins/**` (11 зон + `wiring`, `strangler`) | контракты, порты, сервисы, fail-closed заглушки | реальные адаптеры к БД / MinIO / Redpanda / k8s / cosign / auth |
| `application/plugins/wiring/**` | `build_plugin_platform`, `attach_plugin_platform`, `PluginPlatform`, `DurableGrantRevocation`, `UnavailableAdapter`, `state.py` | вызов из `main.py`, `admin_ports=`, `grant_store=`, `cosign=` |
| `api/v1/plugin_*.py` | 3 роутера (capabilities / gateway / admin) | регистрация capability/gateway в корне (`main.py`) — сделана частично, admin в `/api/v1` |
| `apps/web/src/features/plugins/**` | iframe, `plugin-rpc/1`, хук `usePluginFrame`, тесты | роутер web, каталог i18n (`A8`, `A9`) |
| `infra/k3s/{base,overlays/dev}/plugins/**` | tenant-PG, PgBouncer, Gateway, 2 NP, egress-proxy, Ingress 120, шаблон Deployment | подключение `base/plugins` в родительских kustomization (`A6`), новые образы |
| `infra/argocd/apps/plugin-platform.yaml` | отдельное Argo Application, `ignoreDifferences`, `prune: false` | — |
| `.github/workflows/plugin-artifacts.yml` | OCI/cosign/SBOM/provenance, pin по digest | проверка keyless OIDC на self-hosted runner |
| `packages/plugins/hello-plugin/**` | пилотный плагин (эталон, НЕ перенос `mod_equipment`) | сборка/публикация OCI-артефакта |

Ключевой факт: **F11 — это композиция, а не дописывание зон**. Все зоны спроектированы fail-closed: отсутствие адаптера даёт явный отказ (503/`AdapterMissingError`/`QUARANTINED`), а не «тихий» успех. Первый прогон после снятия freeze **ожидаемо** покажет падения на незаполненных адаптерах — см. §6.

---

## 1. Карта модулей

Путь ниже — относительно корня worktree. «Публичный контракт» = `__init__.py` зоны (`__all__`). «Внешние порты» = `Protocol`, реализации которых обязан подключить интегратор.

| Каталог | Ответственность | Публичный контракт (`__init__.py`) | Внешние порты (Protocol) | На что опирается |
|---|---|---|---|---|
| `apps/api/src/prodavan/domain/plugins/` | F0: доменные типы `PluginManifest`, `PluginClaims`, `CapabilityGrant`, `EgressPolicy`, `ArtifactDigest`, `PluginInstance`, `DecisionCode`, `RegistryOutcome`, JSON Schema манифеста `plugin-rpc/1` | `types.py`, `validation.py`, `__init__.py` (`__all__` из 37 имён) | нет (чистые типы) | дизайн v4 §4.2/§4.4/§5.1; `packages/schemas/plugin-manifest/v1/schema.json` |
| `apps/api/src/prodavan/application/plugins/identity/` | F1: отдельный trust-контур: `PluginClaims` (`iss=agentscale-plugins`, `aud=capability-gateway`, `typ=plugin_capability`), HS256/EdDSA `KeyRing`/`kid`/JWKS, mint/verify, отзыв по `grant_id`, allowlist `^/capabilities/v1/` | `types`, `keys`, `mint`, `revocation`, `allowlist`; `MintGrant`, `MintInstance`, `RevocationPort`, `InMemoryRevocation`, `GrantRevocation`, `assert_plugin_surface` | `RevocationPort`, `MintGrant`, `MintInstance` | `PyJWT[crypto]`; НЕ `core/middleware.py`, НЕ `pod_identity/bridge.py` (§4.1, ADR-008) |
| `apps/api/src/prodavan/application/plugins/capabilities/` | F2: обёртка над ядром `CapabilityGatewayService` (identity → scope → grant → quota → adapter → audit), 9 typed адаптеров, квоты per-`(company, instance, digest)`, redaction аудита, мост `CAPABILITY_BRIDGE` к declarative-именам ядра | `contracts`, `ports`, `quotas`, `audit`, `service`, `errors`; `PluginCapabilityService`, `AdapterRegistry`, `InMemoryQuota`, `InMemoryAudit`, `FailingAudit`, `CAPABILITY_BRIDGE` | `CachePort`, `ObjectsPort`, `DocsPort`, `EventsPort`, `JobsPort`, `PodsPort`, `McpPort`, `SecretsPort`, `NetworkPort`, `GrantRevocationPort`, `QuotaPort`, `AuditPort`, `EgressPolicyPort` | `application/capability_gateway_service.py` (импортируется, НЕ правится); `domain/plugins` |
| `apps/api/src/prodavan/application/plugins/transport/` | F2-HTTP: зависимость `get_plugin_claims` (Bearer → verify → revocation → `assert_plugin_surface`), Pydantic-тела `extra='forbid'`, мост в доменные Request, problem+json, plugin-ветка middleware (guard) | `deps`, `bodies`, `bridge`, `errors`, `guard`; `PluginClaimsDep`, `PluginIdentityConfig`, `register_plugin_surface_guard` | `KeyringLike`, `RevocationLike`, `CapabilityServiceLike` (структурные) | `identity`, `capabilities`; читает `app.state.plugin_keyring` / `plugin_revocation` / `plugin_capability_service` |
| `apps/api/src/prodavan/application/plugins/tenantdb/` | F3: `CREATE DATABASE tenant_<company>`, роль `NOCREATEDB NOCREATEROLE NOSUPERUSER`, схема `module_<plugin>`, `search_path`, `GRANT/REVOKE CONNECT`, `NOLOGIN` при revoke, `secret_ref` вместо plaintext DSN | `naming`, `sql`, `service`, `errors`; `TenantDatabaseService`, `TenantDsn`, `AdminConnectionPort`, `SecretPort`, `AuditPort` | `AdminConnectionPort`, `SecretPort`, `AuditPort` | bootstrap-Job `infra/k3s/base/plugins/tenant-postgres.yaml`; дизайн §2 (PUBLIC/acldefault), ADR-007 |
| `apps/api/src/prodavan/application/plugins/objects/` | F3: bucket-per-tenant `tenant-<company>` + жёсткий префикс `module-<plugin>/`, IAM/session policy, short-lived STS creds (breaking STS-only TTL 900..3600) / scoped presigned (TTL 60..3600), аудит `plugin_object_access` | `policy`, `ports`, `service`, `errors`; `PluginObjectStore`, `BucketAdminPort`, `StsPort`, `PresignPort`, `AuditPort` | `BucketAdminPort`, `StsPort`, `PresignPort`, `AuditPort` | `infrastructure/plugins/minio/config.py` (5 env, без фолбэка на root); §5.4 |
| `apps/api/src/prodavan/application/plugins/artifacts/` | F4: OCI-манифест (3 слоя), cosign keyless verify по digest, совпадение digest, `validate_manifest`, CompatibilityGate (semver/protocol/capabilities/min_platform/scan/CSP), реестр на `AppVersion`/`RegistryOutcome`/`QuarantineEvidence`, quarantine/rollback | `oci`, `layers`, `verification`, `registry`, `errors`; `ArtifactVerifier`, `PluginArtifactRegistry`, `CompatibilityGate`, `InMemoryRegistryStore` | `CosignPort`, `ManifestValidatorPort`, `RegistryStorePort`, `CapabilityGrantLike` | `domain/dynamic_apps` (`AppVersion`), `domain/plugins.validation`, `.github/scripts/plugin_verify.py` |
| `apps/api/src/prodavan/application/plugins/egress/` | F5/F6: единый генератор правил, политика `none`/`webhooks-only`/`full` из `CapabilityGrant` в БД, резолвер по `grant_id`, проверка на КАЖДОМ хопе (redirect/DNS-rebinding, R-10), единая схема egress-аудита, **fail-closed замок `alignment.py`** (`AS_BUILT_SELECTORS`, запрет применения NP при «уезде» к выдуманным лейблам) | `policy`, `generator`, `resolver`, `service`, `alignment`, `errors`; `EgressService`, `EgressConfigGenerator`, `GrantPolicyResolver`, `build_audit_event`, `assert_generated_policy_matches_as_built`, `apply_generated_policy_guarded` | `GrantStorePort`, `AuditSinkPort`, `HopInspectorPort` | `gateway` (network capability), `infra/k3s/base/plugins/egress-proxy.yaml` (Envoy, старт `clusters: []` → 403), `infra/k3s/base/platform/plugin-topics-init.yaml`, [`plugin-network-alignment.md`](plugin-network-alignment.md) |
| `apps/api/src/prodavan/application/plugins/activation/` | F8: Deployment `replicas: 0` + wake/hold-until-Ready + idle TTL + реестр активных `job_id` с lease; `SandboxClaim` не используется | `activator`, `state`, `state_store`, `ports`, `errors`; `PluginActivator`, `ActivationState`, `InMemoryStateStore`, `SystemClock` | `ScalePort`, `ReadinessPort`, `ClockPort`, `MintPort`, `AuditPort`, `StateStorePort`, `ProxyPort` | `gateway` (WakeLease), k8s `deployments/scale` (RBAC в `plugin-gateway.yaml`), ADR-008 |
| `apps/api/src/prodavan/application/plugins/events/` | F7: Redpanda `agentscale.plugin.events`, ключ = `company_id`, `earliest` + ручной commit (не `enable_auto_commit`), DLQ + 3 retry, идемпотентность `(subscription_id, source_event_id)`, wake перед доставкой | `topics`, `contracts`, `ports`, `dispatch`, `errors`; `PluginEventDispatcher`, `WakeHook` | `ProducerPort`, `ConsumerPort`, `DlqPort`, `TopicAdminPort`, `SubscriptionStorePort`, `IdempotencyStorePort`, `PublishedEventStorePort`, `EventHandlerPort`, `WakeHookPort`, `AuditPort`, `ConsumedRecord` | `infra/k3s/base/platform/kafka.yaml` (`auto_create_topics_enabled=false`) + **Sync-хук `infra/k3s/base/platform/plugin-topics-init.yaml`** создаёт 2 топика плагинов; outbox `webhook_deliveries` НЕ шина (§7.3) |
| `apps/api/src/prodavan/application/plugins/gateway/` | F6: всегда-running Plugin Gateway — сессия + `employee ∈ company` + активный grant, `public_pages`/anon-scope, HMAC webhook'а, идемпотентность `X-Prodavan-Delivery`, rate limit, allowlist доверенных заголовков, wake → hold-until-Ready → proxy (SSE без буферизации) | `auth`, `proxy`, `headers`, `ports`, `errors`; `GatewayAuth`, `GatewayProxy`, `Sha256HmacVerifier`, `build_forward_headers`, `TRUSTED_HEADERS` | `SessionPort`, `GrantPort`, `SecretPort`, `HmacPort`, `RateLimitPort`, `ActivationPort`, `BackendHttpPort`, `AuditPort` | `activation`, `api/v1/plugin_gateway.py`; `core/middleware.py` (CORS), Ingress 120 |
| `apps/api/src/prodavan/application/plugins/admin/` | F3-admin: grant / revoke / uninstall / wake / list / `set_enabled_modules` (strangler), все операции идемпотентны и fail-closed, аудит до позитивной мутации | `contracts`, `ports`, `service`, `errors`; `PluginAdminService`, `PluginAdminPorts` (11 обязательных полей) | `TenantDbPort`, `ObjectStorePort`, `ArtifactRegistryPort`, `GrantStorePort`, `RevocationPort`, `MintPort`, `ActivationPort`, `RouteRegistryPort`, `AuditPort`, `PlatformAdminPort` | зоны 02/05/06/07/09; `api/v1/plugin_admin.py` |
| `apps/api/src/prodavan/application/plugins/wiring/` | **F11 composition root**: `PluginPlatformConfig` (проекция всех `PLUGIN_*` + дубль guard'ов), `build_keyring`, `DurableGrantRevocation` (БД + TTL-кэш), `build_adapter_registry` (9 имён + `UnavailableAdapter`), `build_plugin_platform`, `attach_plugin_platform` → `app.state` | `__init__.py` (`__all__`); `config.py`, `keyring.py`, `revocation.py`, `adapters.py`, `platform.py`, `state.py` | `GrantStorePort` (обязателен), `CosignPort` (обязателен), `PluginAdminPorts` (обязателен), `PORT_OVERRIDES` (~45 портов), `SERVICE_OVERRIDES` | все 11 зон; `config/settings.py`; `main.py` вызывает родитель |
| `apps/api/src/prodavan/application/plugins/strangler/` | F10: сторона чтения `plugin_enabled_modules[]`, `StranglerRouter` (ровно один путь, без dual-read/parity), карта переноса `mod_equipment` (offers→prices→budget→documents→tenders→`mod_management`), `CutoverPlan` | `__init__.py`; `flags.py`, `routing.py`, `contexts.py`, `cutover.py` | `FeatureFlagStorePort` (`enabled`/`set`/`list`) | `admin.GrantStorePort.set_enabled_modules` (запись флага — НЕ здесь); §8/§13.1 |
| `apps/api/src/prodavan/application/plugins/adapters/` | **F11-слой стыка**: тонкие адаптеры `PluginAdminPorts` → реальные сервисы зон; `build_plugin_admin_ports` / `build_plugin_admin_service` (fail-closed), in-memory реализации чистых контрактов (A10 — только тесты/dev), маппинг ошибок чужих зон в `PluginAdminError` | `__init__.py` с полным `__all__`: `AdminActivationAdapter`, `GatewayActivationAdapter`, `ArtifactRegistryAdapter`, `TenantDbAdapter`, `ObjectStoreAdapter`, `IdentityRevocationAdapter`, `IdentityMintAdapter`, `build_plugin_admin_ports`, `AdapterDependencies`, `InMemoryGrantStore`/`InMemoryRouteRegistry`/`InMemoryAdminAudit`/`StaticPlatformAdmin` | `DeploymentAdminPort`, `InstanceIdResolver`, `DigestLookupPort`, `BucketAdminPort`/`StsPort`, `AuditSinkPort`, реальный `PlatformAdminPort` (OIDC/JWKS) | `admin`, `identity`, `tenantdb`, `objects`, `artifacts`, `activation`, `gateway`; коллизии имён разрешены алиасами (`AdminMintPort`/`ActivatorMintPort`/`AdminActivationPort`/`GatewayActivationPort`, `test_mint_port_alias_collision.py`) |
| `apps/api/src/prodavan/infrastructure/persistence/repositories/plugins/` | F11/A10: SQL-репозиторий `CapabilityGrantRow`, `DbGrantRevocation` (sync `is_active` + async refresh, TTL-кэш, revoke инвалидирует сразу), снимок фича-флага | `_schema.py`, `grants.py`, `revocation.py` (публичного `__init__.py` нет) | читает `AsyncSession`/`AsyncSessionMaker` | `wiring.revocation.DurableGrantRevocation` (альтернативный адаптер), A10 |
| `apps/api/src/prodavan/infrastructure/plugins/minio/` | F11: **реальные адаптеры** `MinioBucketAdmin` / `MinioSts` (opt-in) / `MinioPresigner` / `MinioAuditAdapter`, фабрика `build_plugin_object_store(config)`, конфигурация из `PLUGIN_MINIO_*` env fail-closed; `STS_ENABLED=false` по умолчанию — блокер §12.8 **снят** выбором fallback ([`docs/migration/minio-sts-verification.md`](minio-sts-verification.md)) | `__init__.py` (`build_plugin_object_store`), `config.py`, `adapters.py`, `README.md` | `QuotaAdminPort`, `AuditSinkPort` (инжектируются родителем; без них — fail-closed) | `objects`; отдельный principal `plugin-object-presigner`, явный запрет `agentscale`/`agentscale-api`/root как admin; `infra/k3s/base/platform/plugin-{minio-policy.json,buckets-init.yaml}` |
| `apps/web/src/features/plugins/` | F9: `PluginFrame` (sandbox `allow-scripts allow-forms` БЕЗ `allow-same-origin`), `assertSafeSrc` (запрет query/fragment/токена в URL), `plugin-rpc/1` с проверкой `event.origin` + `event.source` + `nonce`, `usePluginFrame` (`PluginInfoPort`) | `index.ts`; `PluginFrame.tsx`, `rpc.ts`, `usePluginFrame.ts`, `errors.ts` | `PluginInfoPort` (реализует родитель через admin API) | `VITE_PLUGIN_STATIC_ORIGIN`; `apps/web/src/i18n` (юнит `plugins` — `A8`) |
| `infra/k3s/base/plugins/` | F3/F5/F8 k3s: tenant-PG 16.15 + bootstrap-Job, PgBouncer, plugin-gateway (2 реплики, RBAC только `deployments/scale`), NP `plugin-egress`, NP-эталон агентов (reference-only), egress-proxy/Envoy | `kustomization.yaml`, `README.md` | — (манифесты) | `app-budget.yaml` (45 подов), `00-project.yaml` whitelist |
| `infra/k3s/overlays/dev/plugins/` | dev-специфика: Ingress `plugins-gateway` c `router.priority: 120`, шаблон Deployment `replicas: 0` image по digest | `kustomization.yaml`, `ingress-plugins.yaml`, `plugin-deployment-template.yaml` | — | `infra/argocd/apps/plugin-platform.yaml` рендерит именно этот каталог |
| `infra/argocd/apps/` | GitOps: `plugin-platform.yaml` (wave 1, `ignoreDifferences`, `prune: false`), справка `plugin-apps-project-notes.md` | — | — | `00-project.yaml`, `root-app.yaml` (dev подхватывает `plugin-*.yaml`, prod — нет) |
| `infra/k3s/base/platform/plugin-topics-init.yaml`, `plugin-minio-policy.json`, `plugin-buckets-init.yaml` | F11-инфраструктура от соседних агентов: Sync-хук wave 5 на 2 топика `agentscale.plugin.events`/`.dlq`, IAM-политика `plugin-object-admin` (только `tenant-*`, `Deny` на `agentscale/*`), Job отдельного `plugin-object-presigner` | — | — | `plugin-network-alignment.md`, `infrastructure/plugins/minio/__init__.py`; имена Secret сверены с API Deployment |
| `.github/workflows/plugin-artifacts.yml`, `.github/scripts/{plugin_manifest_builder,plugin_verify}.py` | F4/CI: build с `provenance: true`/`sbom: true`, cosign keyless по digest, `oras push` 3 слоя, verify-before-publish → `PUBLISH_ALLOWED`/`QUARANTINED`, запрет `:latest` | — | `CosignRunner` (инъекция) | `id-token: write`, `COSIGN_CERTIFICATE_IDENTITY` |
| `packages/plugins/hello-plugin/` | F10-пилот: manifest `plugin-rpc/1`, Liquibase changelog, backend `/health`/`/webhook/ping`, frontend `plugin-rpc/1`, README-эталон | `manifest.json`, `README.md` | — | §2/§3/§5.1; **не** перенос `mod_equipment` |

**Зоны, у которых `__init__.py` появился только в ходе чтения (соседние агенты):** `wiring`, `strangler`, `adapters`. Все три уже имеют публичный `__all__`; `adapters` дополнительно даёт `build_plugin_admin_ports` — готовый источник обязательного `admin_ports=` (но с in-memory `stores`, пригодными лишь для тестов/одного процесса, A10). `test_platform_wiring.py::PLUGIN_ZONES` перечисляет 11 «основных» зон и не включает эти надстройки — при F11 стоит расширить реестр или явно классифицировать их.

---

## 2. Порядок сборки (F11)

Точная **целевая** последовательность F11 (шаги 1–4 уже есть в `apps/api/src/prodavan/main.py::create_app()`, шаги 5–7 предстоит добавить родителю; шаг 8 частично сделан — `main.py:61-71` монтирует роутеры в корне под `settings.plugin_platform_enabled`):

```
1) install_trace_id_log_filter() / build_lifespan_manager() / FastAPI(...)
2) register_trace_id → register_json_charset → register_pod_surface_allowlist
   → register_cors(app, allow_origins=settings.cors_origin_list)
   → register_exception_handlers(app)
3) app.include_router(health_routes.router)                 # корневая /health — ПЕРВОЙ
4) app.include_router(v1_router, prefix=settings.api_v1_prefix)   # /api/v1/*
5) build_plugin_platform(settings, grant_store=..., cosign=..., admin_ports=..., ...)
6) if platform is not None: attach_plugin_platform(app, platform)  # app.state.*
7) install_plugin_gateway_runtime(app, PluginGatewayRuntime(auth=..., proxy=...))
8) app.include_router(plugin_gateway.router)              # БЕЗ префикса (корень)
   app.include_router(plugin_capabilities.router)         # БЕЗ префикса (корень)
```

| Роутер | Где монтируется | Почему |
|---|---|---|
| `api/v1/health.py::router` | **корень** (`/health`), зарегистрирован первым | k3s-probe; `plugin_gateway.router` тоже объявляет `GET /health`, и `main.py:59-60` явно фиксирует, что k8s-проба использует ПЕРВУЮ регистрацию |
| `api/v1/router.py::router` | `prefix=settings.api_v1_prefix` → `/api/v1` | все существующие API + `plugin_admin.router` (`prefix=/admin/plugins` → `/api/v1/admin/plugins/*`, `router.py:94`) |
| `api/v1/plugin_gateway.py::router` | **корень** (без `/api/v1`) | внешние адреса §7.1: `/webhook/{plugin}/{company}/*`, `/pluginpage/{plugin}/{company}/*`. Их ведёт отдельный Ingress `plugins-gateway` с `router.priority: 120` (`infra/k3s/overlays/dev/plugins/ingress-plugins.yaml`) |
| `api/v1/plugin_capabilities.py::router` | **корень**, собственный `prefix="/capabilities/v1"` | `identity/allowlist.py::_CAPABILITIES_RE` разрешает ТОЛЬКО `^/capabilities/v1/...`; `core/middleware.py::_PLUGIN_SURFACE_RE` и `transport/guard.py` дают `403 FORBIDDEN_SURFACE` на любом другом пути. Префикс `/api/v1` физически закрыл бы ВСЕ эндпоинты (комментарии `plugin_capabilities.py:13-15`, `transport/__init__.py:24-25`, `main.py:55-57`) |

**Почему Plugin Gateway в корне (Ingress priority 120).** Traefik превращает каждый `path` Ingress'а в отдельный router, а `router.priority` — аннотация на **Ingress**, а не на path. Один объект с `/`, `/api`, `/webhook` дал бы всем путям одинаковый приоритет и повторил инцидент #331 («login POST → index.html»). Поэтому цепочка: `plugins-gateway` **120** > `agentscale-https-api` **110** > `agentscale-https-web` **100** (SPA) > prod wildcard catch-all **50**. Проверяется `infra/ops/tests/test_plugin_ingress_priority.py`. `plugins-gateway` — отдельный объект только в `overlays/dev/plugins/`, родитель НЕ добавляет его в `overlays/dev/kustomization.yaml` (иначе два Argo Application на один Ingress → «пила» selfHeal'а; `plugin-apps-project-notes.md` §4.2).

**Порядок важен и внутри wiring:** `build_plugin_platform` сначала проецирует `Settings` в `PluginPlatformConfig` и валидирует guard'ы (секрет, static-origin, TTL 60..900, egress), `enabled=False` → `None` (ни одного сервиса не создаётся); затем KeyRing → `DurableGrantRevocation` (обязателен `grant_store=`, in-memory подмена запрещена) → реестр адаптеров → все зоны через `PORT_OVERRIDES` → `ArtifactVerifier` (обязателен `cosign=`) → `PluginAdminService` (обязателен `admin_ports=`; собрать его удобно через `adapters.build_plugin_admin_ports`) → финальная проверка «ни одно поле не `None`». `attach_plugin_platform` кладёт 18 ключей в `app.state` (`state.py:50-69`); `plugin_admin_ports`/`plugin_admin_service` и `plugin_gateway_runtime` привязываются отдельно (задачи 11/12 ждут свои ключи).

**Обязательный вызов `install_plugin_gateway_runtime(app, runtime)`** — `api/v1/plugin_gateway.py:75`. Без него `_require_runtime` даёт `RUNTIME_NOT_INSTALLED` → 503 для всех `/webhook/*` и `/pluginpage/*` (fail-closed, `main.py:58`).

**Plugin-ветка allowlist:** либо `register_plugin_surface_guard(app)` из `transport/guard.py`, либо `A2` (плагиновая ветка в `core/middleware.py`, где уже есть `_is_plugin_token`/`_PLUGIN_SURFACE_RE`, `core/middleware.py:133-144`). Оба варианта не трогают агентский `PodSurfaceAllowlistMiddleware`. Регистрировать guard нужно **до** CORS/роутинга, но после `register_trace_id`.

---

## 3. Таблица адаптеров (все `Protocol` по зонам)

Обозначения: **in-memory** = реализация есть, но только для тестов/одного процесса; **требуется реализация** = F11 подключает реальный адаптер (`PORT_OVERRIDES` в `wiring/platform.py`); **заглушка** = `UnavailableAdapter` / `_MissingPort`, который ЯВНО отказывает при вызове.

| Зона | Protocol | Файл объявления | Кто реализует / состояние |
|---|---|---|---|
| identity | `RevocationPort` | `identity/revocation.py:29` | `InMemoryRevocation` (**in-memory**); в проде `wiring.DurableGrantRevocation` или `infrastructure/persistence/repositories/plugins/revocation.py::DbGrantRevocation` |
| identity | `MintGrant`, `MintInstance` | `identity/mint.py:45,77` | структурные: `admin.CapabilityGrantRecord` / `MintRequest` + адаптер к `identity.mint_plugin_claims` (**требуется реализация** в F11) |
| capabilities | `PluginClaims` (структурный) | `capabilities/ports.py:100` | `identity/types.py::PluginClaims` (удовлетворяет структурно) |
| capabilities | `GrantRevocationPort` | `capabilities/ports.py:128` | `DurableGrantRevocation.is_active` / `DbGrantRevocation` (**требуется реализация**, `grant_store=`) |
| capabilities | `CachePort` | `capabilities/ports.py:143` | **требуется реализация**: Redis с key-rewrite `tenant:{company}:plugin:{module}:{instance}:*`; сейчас `UnavailableAdapter` |
| capabilities | `ObjectsPort` | `capabilities/ports.py:153` | **требуется реализация**: адаптер к `objects.PluginObjectStore` |
| capabilities | `DocsPort` | `capabilities/ports.py:167` | **требуется реализация**: Mongo namespace per tenant |
| capabilities | `EventsPort` | `capabilities/ports.py:173` | **требуется реализация**: адаптер к `events.PluginEventDispatcher` |
| capabilities | `JobsPort` | `capabilities/ports.py:180` | **требуется реализация**: Celery + outbox, `job_id`/`run_id`/lease |
| capabilities | `PodsPort` | `capabilities/ports.py:188` | **требуется реализация**: typed invoke (плагин не имеет k8s API) |
| capabilities | `McpPort` | `capabilities/ports.py:194` | **требуется реализация**: register typed tools |
| capabilities | `SecretsPort` | `capabilities/ports.py:200` | **требуется реализация**: read по `secret_ref` (k8s Secret) |
| capabilities | `NetworkPort` | `capabilities/ports.py:206` | **требуется реализация**: egress-proxy по правилам из grant |
| capabilities | `QuotaPort` | `capabilities/quotas.py:176` | `InMemoryQuota` (**in-memory**); прод — per-`(company, instance, digest)` счётчик в БД |
| capabilities | `AuditPort` | `capabilities/audit.py:307` | `InMemoryAudit`, `FailingAudit` (**in-memory**); прод — append-only sink |
| capabilities | `EgressPolicyPort` | `capabilities/service.py:187` | **требуется реализация**: `egress.GrantPolicyResolver` по `grant_id` из БД (не из JWT) |
| transport | `KeyringLike`, `RevocationLike` | `transport/deps.py:71,78` | `identity.keys.KeyRing`, `DurableGrantRevocation` через `app.state` |
| transport | `CapabilityServiceLike` | `transport/bridge.py:49` | `capabilities.PluginCapabilityService` через `app.state.plugin_capability_service` |
| tenantdb | `AdminConnectionPort` | `tenantdb/service.py:102` | **требуется реализация**: asyncpg/SQLAlchemy к tenant-кластеру (2 зоны: `cluster` / `database`), `dsn_sslmode="require"` |
| tenantdb | `SecretPort` | `tenantdb/service.py:128` | **требуется реализация**: k8s Secret (`TENANT_DB_DSN` как `secret_ref`, ротация) |
| tenantdb | `AuditPort` | `tenantdb/service.py:149` | **требуется реализация**: append-only, без SQL/паролей/DSN |
| objects | `BucketAdminPort` | `objects/ports.py:92` | **реализовано**: `infrastructure/plugins/minio/adapters.py::MinioBucketAdmin` (в ходе чтения волны), фабрика `build_plugin_object_store`. Требуется привязка Secret `agentscale-minio-plugin-admin` |
| objects | `StsPort` | `objects/ports.py:143` | **Опциональная оптимизация, по умолчанию ВЫКЛЮЧЕНА**: `MinioSts` существует, но `AssumeRole` + session policy не подтверждены официальной документацией (6/6 попыток `web_fetch` неудачны); `PLUGIN_MINIO_STS_ENABLED=false` → рабочий путь **scoped presigned**. См. [`minio-sts-verification.md`](minio-sts-verification.md) |
| objects | `PresignPort` | `objects/ports.py:165` | **реализовано**: `MinioPresigner` с ОТДЕЛЬНЫМ ключом `plugin-object-presigner` (не root/as-built `agentscale-api`), явный префикс; требуется привязка `PLUGIN_MINIO_PRESIGN_*` из Secret |
| objects | `AuditPort` | `objects/ports.py:177` | `MinioAuditAdapter` + `InMemoryPluginAuditSink` (**in-memory**); прод — append-only `AuditSinkPort`, без него аудит отказывает (`strict=True` → deny) |
| artifacts | `CosignPort` | `artifacts/verification.py:143` | **требуется реализация**: cosign CLI/HTTP (CI `.github/scripts/plugin_verify.py`); обязательный `cosign=` в `build_plugin_platform` |
| artifacts | `ManifestValidatorPort` | `artifacts/verification.py:160` | `domain/plugins.validation.validate_manifest` (ленивый импорт в `ArtifactVerifier`) |
| artifacts | `CapabilityGrantLike` | `artifacts/verification.py:171` | структурный: `CapabilityGrantRecord.capabilities` / `CapabilitySet` |
| artifacts | `RegistryStorePort` | `artifacts/registry.py:87` | `InMemoryRegistryStore` (**in-memory**); прод — БД/`AppVersion`/`RegistryOutcome` (A10) |
| egress | `GrantStorePort` | `egress/resolver.py:41` | **требуется реализация**: тот же `grant_store`, что и admin/capabilities (политика из БД) |
| egress | `AuditSinkPort` | `egress/service.py:102` | **требуется реализация**: общий append-only sink (единая схема с агентами) |
| egress | `HopInspectorPort` | `egress/service.py:109` | **требуется реализация**: Envoy ext_authz/controller, факты о каждом хопе (R-10) |
| activation | `ScalePort` | `activation/ports.py:88` | **требуется реализация**: k8s `deployments/scale` (RBAC в `plugin-gateway.yaml`) |
| activation | `ReadinessPort` | `activation/ports.py:104` | **требуется реализация**: `readinessProbe /health`, hold-until-Ready |
| activation | `ClockPort` | `activation/ports.py:117` | `SystemClock` (реализация есть) |
| activation | `MintPort` | `activation/ports.py:132` | **требуется реализация**: адаптер к `identity.mint_plugin_claims` (новый токен при каждом wake) |
| activation | `AuditPort` | `activation/ports.py:146` | **требуется реализация**: append-only `plugin_wake_up`/`plugin_suspend` |
| activation | `StateStorePort` | `activation/ports.py:159` | `InMemoryStateStore` (**in-memory**); прод — БД/ConfigMap с CAS (`compare_and_swap`) |
| activation | `ProxyPort` | `activation/ports.py:183` | **требуется реализация**: `gateway.GatewayProxy.forward` |
| events | `ConsumedRecord` | `events/ports.py:38` | **требуется реализация**: запись aiokafka |
| events | `ProducerPort` | `events/ports.py:68` | **требуется реализация**: aiokafka producer, ключ `company_id` |
| events | `ConsumerPort` | `events/ports.py:96` | **требуется реализация**: dispatch с ручным commit, `earliest` |
| events | `DlqPort` | `events/ports.py:144` | **требуется реализация**: DLQ-продюсер |
| events | `TopicAdminPort` | `events/ports.py:158` | **требуется реализация**: rpk/Redpanda Admin (`auto_create_topics_enabled=false`) |
| events | `SubscriptionStorePort` | `events/ports.py:189` | **требуется реализация**: БД подписок |
| events | `IdempotencyStorePort` | `events/ports.py:208` | **требуется реализация**: БД/Redis `(subscription_id, source_event_id)` |
| events | `PublishedEventStorePort` | `events/ports.py:228` | **требуется реализация**: БД/Redis, идемпотентность `event_id` |
| events | `EventHandlerPort` | `events/ports.py:253` | **требуется реализация**: `POST /events/*` backend плагина |
| events | `WakeHookPort` | `events/ports.py:263` | **требуется реализация**: `PluginActivator.wake` перед доставкой |
| events | `AuditPort` | `events/ports.py:280` | **требуется реализация**: append-only, только `payload_digest` |
| gateway | `SessionPort` | `gateway/ports.py:112` | **требуется реализация**: общий auth-контур (OIDC bearer / cookie `session`) |
| gateway | `GrantPort` | `gateway/ports.py:129` | **требуется реализация**: `GrantStore` + revocation + `public_pages` из манифеста |
| gateway | `SecretPort` | `gateway/ports.py:148` | **требуется реализация**: HMAC-секрет из secret store (НЕ из БД); пилот добавляет `PLUGIN_WEBHOOK_SECRET` в тот же Secret |
| gateway | `HmacPort` | `gateway/ports.py:156` | `Sha256HmacVerifier` (реализация есть, `hmac.compare_digest`) |
| gateway | `RateLimitPort` | `gateway/ports.py:201` | **требуется реализация**: Redis `SET NX` + TTL / `INCR` + `EXPIRE`, атомарный `claim_delivery` |
| gateway | `ActivationPort` | `gateway/ports.py:253` | **требуется реализация**: тонкий адаптер `(plugin_id, company_id)` → `instance_id` → `PluginActivator.wake` → `WakeLease` (прямого структурного совпадения НЕТ) |
| gateway | `BackendHttpPort` | `gateway/ports.py:321` | **требуется реализация**: httpx/streaming к backend/static-origin (SSE живым итератором) |
| gateway | `AuditPort` | `gateway/ports.py:358` | **требуется реализация**: append-only `GatewayAuditEvent` |
| admin | `TenantDbPort` | `admin/ports.py:47` | **требуется реализация**: адаптер к `tenantdb.TenantDatabaseService` |
| admin | `ObjectStorePort` | `admin/ports.py:78` | **требуется реализация**: адаптер к `objects.PluginObjectStore` |
| admin | `ArtifactRegistryPort` | `admin/ports.py:101` | **требуется реализация**: адаптер к `artifacts.PluginArtifactRegistry` (только PUBLISHED) |
| admin | `GrantStorePort` | `admin/ports.py:125` | **требуется реализация**: БД `CapabilityGrantRow` + фича-флаг (A10, `infrastructure/persistence/repositories/plugins/grants.py`) |
| admin | `RevocationPort` | `admin/ports.py:171` | **требуется реализация**: async `revoke`/`is_revoked` (`DbGrantRevocation`) |
| admin | `MintPort` | `admin/ports.py:191` | **требуется реализация**: адаптер к `identity.mint_plugin_claims` |
| admin | `ActivationPort` | `admin/ports.py:202` | **требуется реализация**: адаптер к `activation.PluginActivator` |
| admin | `RouteRegistryPort` | `admin/ports.py:244` | **требуется реализация**: Ingress/NP/egress-генератор (задачи 14/15) |
| admin | `AuditPort` | `admin/ports.py:265` | **требуется реализация**: append-only `AdminAuditEvent.redacted()` |
| admin | `PlatformAdminPort` | `admin/ports.py:281` | **требуется реализация**: реальная PlatformAdmin-auth; до подключения 503 |
| wiring | `GrantStorePort` | `wiring/revocation.py:90` | **требуется реализация**: sync `get_grant`/`deactivate` поверх `CapabilityGrantRow.status` (обязательный `grant_store=`). Промежуточно — `adapters/stores.py::InMemoryGrantStore` (**только тесты/однопроцессный dev**, A10) |
| adapters | `DeploymentAdminPort`, `InstanceIdResolver`, `DigestLookupPort` | `adapters/activation_adapter.py`, `artifacts_adapter.py`, `build.py` | **in-memory** реализации в `adapters/stores.py` для тестов; в кластере нужны k8s-клиент, БД/Redis с CAS, GHCR/MinIO, настоящая PlatformAdmin-auth (A10) |
| adapters | `AdminActivationPort`/`GatewayActivationPort`, `AdminMintPort`/`ActivatorMintPort`, `AdminRevocationPort` | `adapters/__init__.py` | **реализовано** тонкими адаптерами с алиасами (разрешает коллизии `MintPort`/`ActivationPort`, см. `test_mint_port_alias_collision.py`) |
| wiring | `AppStateLike`, `RequestLike` | `wiring/state.py:90,98` | `FastAPI`/`Request` (структурно) |
| wiring | `UnavailableAdapter` | `wiring/adapters.py:81` | **заглушка**: все 9 методов есть, каждый бросает `AdapterMissingError`; `adapter_report()` явно перечисляет долги |
| strangler | `FeatureFlagStorePort` | `strangler/flags.py:33` | `InMemoryFeatureFlagStore` (**in-memory**); прод — адаптер к `admin.GrantStorePort` |
| web | `PluginInfoPort` | `apps/web/src/features/plugins/usePluginFrame.ts:17` | **требуется реализация**: родитель через admin API / реестр / gateway |
| web | `PluginChannel`, `PluginChannelFactory` | `apps/web/src/features/plugins/rpc.ts:91,106` | `createPluginChannel` (реализация есть) |

**Реестр портов для «подъёмного» теста** — `apps/api/tests/acceptance/plugins/test_platform_wiring.py::PORTS_WITH_INMEMORY_IMPL` (пара `(zone, PortName)` → реализация/заглушка); **executable test plan** на 22 критерия — [`docs/migration/plugin-test-plan.md`](plugin-test-plan.md).

### 3.1 Коллизии имён и способ их разрешения

Одно и то же имя `Protocol` объявлено в разных зонах независимо (осознанная граница: зоны не импортируют чужие порты). Интегратор **не должен** делать `from ... import *`.

| Имя | Где объявлено | Разница контракта | Разрешение |
|---|---|---|---|
| `MintPort` | `activation/ports.py:132` — `async mint(plugin_instance_id, reason) -> MintedClaims`; `admin/ports.py:191` — `async mint(request: MintRequest) -> IssuedPluginClaims` | wake выпускает токен по id инстанса; admin — по типизированному запросу grant'а | Квалифицированные импорты: `from prodavan.application.plugins.activation.ports import MintPort as ActivationMintPort`, `from ...admin.ports import MintPort as AdminMintPort` (ровно так сделано в `wiring/platform.py:68-71`). В `adapters/__init__.py` те же алиасы вынесены в публичный контракт (`AdminMintPort`, `ActivatorMintPort`, `IdentityAdminMintPort`), а коллизия закреплена тестом `test_mint_port_alias_collision.py`. Один объект-адаптер может реализовывать оба, но связывать их надо разными override'ами: `activation_mint=` и `admin_ports.mint=` |
| `ActivationPort` | `gateway/ports.py:253` — `wake(plugin_id, company_id) -> WakeLease` + `wait_ready`; `admin/ports.py:202` — `ensure_deployment`/`suspend`/`wake`/`runtime_status`/`purge_infra` | шлюз будит по паре «плагин+компания»; admin управляет полным lifecycle | Разные адаптеры: `gateway_activation=` (тонкий маппер в `PluginActivator.wake`) и `admin_ports.activation=`. Прямого структурного совпадения нет (ключи `plugin_instance_id` vs `(plugin_id, company_id)`), это зафиксировано в `gateway/ports.py:269-271`. Готовые реализации — `adapters.GatewayActivationAdapter` / `AdminActivationAdapter` с алиасами `GatewayActivationPort`/`AdminActivationPort` |
| `AuditPort` | **Коллизия из задания «в 5 зонах»; фактически 8 объявлений**: `capabilities/audit.py:307` (`append(PluginAuditEvent)`), `tenantdb/service.py:149` (`async record(event, company_id, plugin_id, payload)`), `objects/ports.py:177` (`async record(event: dict)`), `events/ports.py:280`, `gateway/ports.py:358` (`async record(GatewayAuditEvent)`), `activation/ports.py:146` (`sync record(ActivationAuditEvent)`), `admin/ports.py:265` (`async append(event) -> bool`), `egress/service.py:102` (`AuditSinkPort`, `sync record(Mapping)`) | синхронный/асинхронный, разные типы событий, разные методы (`append` vs `record`) | **Не объединять в один импорт.** `wiring/platform.py` использует алиасы (`AuditPort as ActivationAuditPort`, `ClockPort`, `MintPort as ActivationMintPort`) и раздельные override'ы: `audit=`, `tenant_db_audit=`, `object_audit=`, `event_audit=`, `egress_audit=`, `gateway_audit=`, `activation_audit=`, `admin_ports.audit=`. Общий append-only sink реализует **несколько** структурных протоколов сразу (как `DbGrantRevocation` реализует три контракта отзыва) |
| `GrantStorePort` | `admin/ports.py:125` (async CRUD + `set_enabled_modules`), `egress/resolver.py:41` (`GrantLookup` по `grant_id`), `wiring/revocation.py:90` (sync `get_grant`/`deactivate`), `strangler/flags.py:33` (`FeatureFlagStorePort`: enabled/set/list) | один и тот же `CapabilityGrant` читается с разных сторон и в разных режимах (sync/async) | Один SQL-репозиторий (`infrastructure/persistence/repositories/plugins/grants.py`) + тонкие адаптеры под каждый порт. `test_cross_module_contracts.py` явно требует, чтобы grant, созданный admin, читался egress и revocation (**один** `grant_store`) |
| `SecretPort` | `tenantdb/service.py:128` (`TENANT_DB_DSN` как `secret_ref`), `gateway/ports.py:148` (`webhook_secret`) | разные секреты, разные хранилища | Разные override'ы `tenant_db_secrets=` / `gateway_secrets=`; HMAC-секрет пилота (`PLUGIN_WEBHOOK_SECRET`) — ещё один ключ того же Secret, а не новый источник (`hello-plugin/README.md:75-78`) |
| `RevocationPort` | `identity/revocation.py:29` (sync `is_active`), `admin/ports.py:171` (async `revoke`/`is_revoked`), `capabilities/ports.py::GrantRevocationPort` | sync/async | `DurableGrantRevocation` (`wiring`) и `DbGrantRevocation` (`infrastructure/.../revocation.py`) реализуют **все три** контракта сразу; `transport/deps.py:203-221` поддерживает `check()`/`is_revoked()`/`is_active()` как равнозначные формы |
| `PluginClaims` | `domain/plugins/types.py`, `identity/types.py`, `capabilities/ports.py:100` (структурный минимум) | полный dataclass vs структурный протокол | `capabilities` намеренно НЕ импортирует чужой каталог; `wiring/platform.py` передаёт проверенный `identity.PluginClaims` как есть ( duck-typing ). При F11 не «унифицировать» типы, а сохранить структурную границу |
| `ScopedCredentials`, `WakeResult`, `EgressPolicy`, `CapabilityGrant`, `bucket_name` | продублированы в `capabilities/contracts.py`, `objects/ports.py`, `activation/state.py` vs `admin/contracts.py`, `egress/policy.py`, `admin/contracts.py`, `capabilities/quotas` vs `admin/contracts.py`, `tenantdb/naming.py` vs `objects/policy.py` | разные слои (домен / приложение / админ / инфраструктура) | Квалифицированный импорт + адаптер-маппер. `test_platform_wiring.py::test_cross_zone_constants_are_consistent` проверяет, что **значения** констант (префиксы `tenant_`/`tenant-`/`module_`/`module-`, TTL 300, топик, `plugin-rpc/1`, версия 4.0.0) совпадают, хотя типы разные |

**Правило для интегратора:** импорт всегда `from prodavan.application.plugins.<zone>.ports import X as <Zone>X`, а «широкий» `from prodavan.application.plugins import ...` использовать только для `__init__.py` зон (у них `__all__` полный). Список `PORT_OVERRIDES` в `wiring/platform.py:124-172` — канонический реестр «какой порт под каким именем инъектится»; `adapter_<name>` — отдельный префикс для реализаций capability (исключает коллизию `object_buckets` vs `adapter_objects`, `platform.py:291-303`).

---

## 4. Цепочки запросов

### (а) Внешний webhook → Gateway auth/proxy → activator → backend

```
POST https://agentscale.dev/webhook/{plugin}/{company}/...
  → Ingress plugins-gateway (priority 120, websecure) → plugin-gateway:8080
  → api/v1/plugin_gateway.py::_handle_webhook
      · raw_body читается ОДИН раз, HMAC считается ровно по нему
      · GatewayAuth.authorize_webhook (gateway/auth.py:508-706):
          1) обязательные X-Prodavan-Signature / -Event / -Delivery (иначе 401)
          2) rate limit per (plugin, company)
          3) активный grant (GrantPort)   ← grant_id проверяется ЗДЕСЬ
          4) секрет из SecretPort (не БД)
          5) HMAC: sha256=HMAC(secret, raw_body), compare_digest  ← ПОДПИСЬ ЗДЕСЬ
          6) атомарный claim_delivery: повтор → 200 без forward,
             повтор с чужим телом → 401 REPLAY_DETECTED
      · GatewayProxy.forward → ActivationPort.wake → hold-until-Ready → backend
      · _to_response (SSE — живой итератор, без буферизации)
```

| Где что | Файл |
|---|---|
| `grant_id` | `gateway/auth.py::_active_grant` → `GatewayGrant.grant_id`; затем `GatewayClaims.grant_id`, `GatewayAuditEvent.grant_id`; повторная проверка на `/capabilities/v1/*` — `transport/deps.py:200-237` + `wiring/revocation.DurableGrantRevocation.is_active` |
| Подпись | `gateway/auth.py:603-618` через `Sha256HmacVerifier` (`gateway/auth.py:124-152`); секрет `SecretPort.webhook_secret`; входящий `X-Prodavan-Signature` **отбрасывается** при проксировании (`headers.py:72-76`), чтобы backend не решил, что «тоже проверил подпись» и не ослабил проверку JWT |
| Redaction | `GatewayAuditEvent` (`gateway/ports.py:337-355`): только идентификаторы + `payload_digest = sha256(raw_body)`, **никогда** raw body/токены/секреты/PII; `capabilities/audit.py::redact` для invocation-событий; `tenantdb/sql.py::DdlStatement.safe_repr` вырезает пароль из DDL |

### (б) Браузер → iframe → `plugin-rpc/1` → Gateway → `/capabilities/v1/*`

```
Shell (agentscale.dev)
  → usePluginFrame: PluginInfoPort → connected/granted/digest, resolveStaticOrigin
  → <iframe sandbox="allow-scripts allow-forms"  БЕЗ allow-same-origin
           referrerpolicy="no-referrer" credentialless
           src="https://plugin-origin.agentscale.dev/tenant-<co>/<plugin>/<version>/index.html">
  → plugin-rpc/1 handshake: host.ready(instanceId, nonce) → plugin.ready(plugin_id, version, digest, nonce)
       · event.source === iframe.contentWindow, event.origin === acceptedOrigin ("null" для opaque)
       · capability token НЕ передаётся ни в URL, ни в postMessage
  → plugin.rpc.request → хост проксирует в /pluginpage/{plugin}/{company}/api/*
  → Plugin Gateway: сессия + employee ∈ company + активный grant (public_pages — исключение, anon-scope)
  → backend плагина (Authorization: Bearer <PluginClaims> или X-Plugin-Capability-Token)
  → backend → POST /capabilities/v1/<capability>/<operation> c Bearer PluginClaims
  → PluginSurfaceGuardMiddleware / identity.allowlist → transport.deps.get_plugin_claims
      verify_plugin_token → GrantRevocation.check → assert_plugin_surface
  → PluginCapabilityService.invoke → CapabilityGatewayService.authorize →
      scope → grant → quota (429 QUOTA_EXCEEDED) → AdapterRegistry.dispatch → AuditEvent
```

| Где что | Файл |
|---|---|
| `grant_id` | `GatewayAuth.authorize_page` (`gateway/auth.py`), `GatewayClaims`, затем **на каждом capability-запросе**: `transport/deps.py:200-237`, `capabilities/service.py` (`GrantRevocationPort.is_active`), `wiring/platform.py:29-34` |
| Подпись | подпись **JWT** `PluginClaims`: `identity/mint.py::verify_plugin_token` + `identity/keys.py::KeyRing` (HS256/EdDSA, `kid`, без dev-фолбэков). Подпись артефакта — отдельно: `cosign verify` до PUBLISHED (`artifacts/verification.py`). Входящие `Authorization`/`Cookie` клиента отбрасываются (`headers.py:63-77`), доверенный токен выставляется allowlist'ом |
| Redaction | `capabilities/audit.py::PluginAuditEvent.redacted()` (только `sql_digest`/`payload_digest`/`artifact_digest`, §4.5, крит. №11); `apps/web/.../PluginFrame.tsx::assertSafeSrc` запрещает `token`/`secret`/`auth` в query и `#fragment`; `rpc.ts` не содержит capability token |

### (в) Событие → Redpanda → wake → handler → commit

```
publish (PluginEventDispatcher.publish)
  · TopicAdminPort.ensure_topic / ensure_dlq_topic (auto_create_topics_enabled=false)
  · ключ партиции = company_id (events/topics.py::partition_key)
  · идемпотентность по event_id, только payload_digest в шину/аудит
  → топик agentscale.plugin.events
consume (одна запись за вызов)
  · auto_offset_reset="earliest", enable_auto_commit=False (contracts.consumer_config)
  · идемпотентность (subscription_id, source_event_id)
  · WakeHookPort → PluginActivator.wake (0→1, hold-until-Ready) ДО доставки
  · EventHandlerPort → POST /events/* backend плагина (grant_id из claims)
  · УСПЕХ → ручной commit offset'а
  · ОШИБКА → до 3 ретраев → DLQ → commit; любая ошибка wake/аудита/DLQ/хранилища
    НЕ коммитит, позиция возвращается через seek (событие не теряется при простое)
```

| Где что | Файл |
|---|---|
| `grant_id` | подписка привязана к grant'у (`Subscription`), wake выпускает новый короткий `PluginClaims` с `grant_id` (`activation/state.py::WakeResult`, `activation/ports.py::MintPort`); handler backend проверяет JWT |
| Подпись | `PluginClaims` при wake (новый, TTL 300 с, не истёкший из Secret — `activation/ports.py:132-142`); событие само по себе подписи не несёт, целостность = `payload_digest` + Redpanda |
| Redaction | `events/contracts.py` — в шину/аудит/DLQ не попадает raw payload, только `canonical_digest`; `events/ports.py::AuditPort` — append-only `plugin_event_received` / `plugin_event_dlq` |

### (г) grant → artifact verify → tenant DB → bucket → grant → mint

```
POST /api/v1/admin/plugins/grant (PlatformAdminPort, fail-closed 503 без порта)
  → PluginAdminService.grant (admin/service.py:215-391):
      1) validate company/plugin/version/digest
      2) ArtifactRegistryPort.get → статус PUBLISHED (иначе ARTIFACT_NOT_PUBLISHED)
           ↑ до этого в CI/реестре: cosign verify → digest → validate_manifest →
             CompatibilityGate → RegistryOutcome.APPLIED / QUARANTINED
      3) идемпотентность по (digest, version, capabilities)
      4) _decision_audit ДО первой мутации (audit failure = deny)
      5) TenantDbPort.ensure_company_database → tenant_<company>
         · bootstrap-Job: GRANT CONNECT админской роли → REVOKE CONNECT, TEMPORARY FROM PUBLIC
           (включая template1) → GRANT CONNECT только роли плагина
         · CREATE ROLE NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 10
         · CREATE SCHEMA module_<plugin> AUTHORIZATION <роль>, search_path
         · TENANT_DB_DSN → SecretPort.put → secret_ref (не plaintext)
      6) ObjectStorePort.ensure_bucket → tenant-<company> + module-<plugin>/*
         · bucket_policy / session_policy с ЯВНЫМ префиксом (без policy variables до §12.8)
         · scoped presigned URL с IAM-политикой на префикс `module-<plugin>/*`
           (STS creds — опциональная оптимизация, по умолчанию отключена)
      7) CapabilityGrantRecord (grant_id, egress_policy + quotas В БД, не в JWT)
      8) ActivationPort.ensure_deployment → Deployment replicas: 0 по digest
      9) MintPort.mint → короткий PluginClaims (grant_id + jti + TTL, scopes)
     10) RouteRegistryPort.register → маршруты + egress-правила из grant
     11) _audit(ALLOW) → GrantReceipt
```

| Где что | Файл |
|---|---|
| `grant_id` | создаётся в `admin/service.py:340`; проверяется `GatewayAuth` + `DurableGrantRevocation.is_active` на каждом запросе; `revoke` → `deactivate` + `RevocationPort.revoke` + `ALTER ROLE ... NOLOGIN` → мгновенный отзыв по `grant_id` (крит. №12) |
| Подпись | **артефакта**: `CosignPort.verify` с `--certificate-identity`/`--certificate-oidc-issuer`, по `@sha256:` digest, до PUBLISHED (`artifacts/verification.py`, `.github/scripts/plugin_verify.py`); **токена**: `identity.mint_plugin_claims` (HS256/EdDSA). Неподписанный/несовпавший digest → `QUARANTINED`, деплой по digest, `:latest` запрещён |
| Redaction | `AdminAuditEvent.redacted()` (`admin/contracts.py:379-394`): нет raw SQL/параметров/DSN/токенов; `DdlStatement.__repr__` скрывает `params`, `safe_text()` вырезает `PASSWORD '...'` (`tenantdb/sql.py:93-120`); `PluginPlatformConfig.as_report()`/`Platform.report()` не содержат секретов (`wiring/config.py:237-263`) |

**Общее для всех цепочек:** egress-политика и квоты читаются из `CapabilityGrant` в БД по `grant_id`, а не из короткого JWT (крит. №22); решение об авторизации и факт исполнения — **разные** события аудита (§4.5); ошибка append-only записи для позитивной мутации = deny/system failure.

---

## 5. GitOps-порядок

| Wave | Объект | Файл | Что даёт |
|---|---|---|---|
| **-10** | AppProject `agentscale` / `agent-sandbox` | `infra/argocd/apps/00-project.yaml` (не редактировался) | whitelist 27 kinds, destinations, repo |
| **-5** | приложение `agent-sandbox` | `infra/argocd/apps/agent-sandbox.yaml` | регистрирует CRD `extensions.agents.x-k8s.io` / `agents.x-k8s.io` |
| **0** | `agentscale-dev` | `infra/argocd/apps/agentscale-dev.yaml` | namespace `agentscale-dev` / `agentscale-dev-sandboxes`, базовые сервисы, dev Ingress 100/110 |
| **1** | `plugin-platform` (Application) | `infra/argocd/apps/plugin-platform.yaml:32` | `spec.source.path: infra/k3s/overlays/dev/plugins`, `destination.namespace: agentscale-dev`, `selfHeal: true`, `prune: false`, `CreateNamespace=true`, `RespectIgnoreDifferences=true`, `Wait=false` |
| **25** | Ingress `plugins-gateway` (внутри Application) | `infra/k3s/overlays/dev/plugins/ingress-plugins.yaml:40` | `router.priority: 120`, `entrypoints: websecure`, `tls.hosts [agentscale.dev, localhost]`, ровно `/webhook` и `/pluginpage` → `plugin-gateway:8080` |

`-10 / -5 / 0 / 1` — `sync-wave` **на Application в app-of-apps**; `25` — wave **ресурса внутри** `plugin-platform` (не путать, комментарий `plugin-platform.yaml:20-31`). Проверяется `infra/ops/tests/test_plugin_ingress_priority.py::test_plugin_platform_sync_wave_is_after_crds` и `::test_plugins_gateway_sync_wave_is_25`.

### `ignoreDifferences` — 4 объекта (5 записей, из них 2 по Deployment)

| # | Объект | Путь | Кто правит вне Git / зачем |
|---|---|---|---|
| 1 | `apps / Deployment` | `/spec/replicas` (+ `jqPathExpressions: [.spec.replicas]`) | activator патчит `deployments/scale` 0→1; без исключения `selfHeal` вернул бы 0 посреди запроса (крит. №13). Запись НЕ сужена по `name` — имена Deployment динамические |
| 2 | `apps / Deployment` | `/status` | статус шлют контроллеры → вечный OutOfSync под selfHeal |
| 3 | ConfigMap `tenant-postgres-config` | `/data` | задача 05 пишет `tenant_hba.conf` — пары `(role, tenant_<company>)` + `pg_reload_conf()` |
| 4 | ConfigMap `egress-proxy-config` | `/data` | задача 08 генерирует Envoy-кластеры из `CapabilityGrant` |
| 5 | Secret `pgbouncer-credentials` | `/data` | задача 05 пишет `userlist.txt` — SCRAM-секреты ролей + `RELOAD;` |
| 6 | ConfigMap `tenant-postgres-bootstrap` | (в списке НЕТ) | bootstrap-SQL лежит в Git; Job-хук sync-wave 1 идемпотентен |

> В задании сказано «`ignoreDifferences` на 4 объекта»: по файлу это **5 записей на 4 уникальных объекта** — Deployment (две записи: `/spec/replicas` + `/status`), ConfigMap `tenant-postgres-config`, ConfigMap `egress-proxy-config`, Secret `pgbouncer-credentials`. Controller-правимые **данные** — ровно 3 объекта (`/data` у двух ConfigMap и одного Secret); четвёртый объект — Deployment `/spec/replicas`. Игнорируется точечно `/data`, не весь объект: откат к пустому allowlist не открывает доступ (fail-closed), но ломает выданный grant. См. `plugin-platform.yaml:144-180`, `infra/k3s/base/plugins/README.md:115-123`.

**`prune: false`** — намеренно: Deployment backend'а плагина создаётся платформой при grant (`admin API`, §5.1), а не обязательно лежит в Git. `prune: true` удалил бы живой backend при первом синке (`plugin-platform.yaml:77-84`).

**Bootstrap Job** — `infra/k3s/base/plugins/tenant-postgres.yaml`, Sync-хук wave 1 (`bootstrap.sql` + `platform-revoke.sql`): создаёт `plugin_platform_admin` (LOGIN CREATEDB CREATEROLE NOSUPERUSER), **ПЕРЕД** revoke выдаёт ей `CONNECT` на каждую БД (иначе `CREATE SCHEMA` при grant упадёт 42501), затем `REVOKE CONNECT, TEMPORARY ... FROM PUBLIC` на tenant- и платформенном инстансах, включая `template1` (чтобы новые `tenant_*` наследовали закрытый ACL), и fail-closed проверку `has_database_privilege`. Мгновенный отзыв при revoke — `ALTER ROLE ... NOLOGIN`, не зависящий от PUBLIC и Redis.

**Новые образы (задача родителя, ещё НЕ собраны):**

| Образ | Где | Риск |
|---|---|---|
| `ghcr.io/ne-tort/postgres:16.15` **с pgaudit** | `tenant-postgres.yaml:301`, `shared_preload_libraries=pgaudit` | ванильный `postgres:16.15` **не содержит pgaudit** → FATAL на старте (намеренный fail-closed, «тихого» отключения аудита нет) |
| `ghcr.io/ne-tort/pgbouncer:1.24.1` (SCRAM) | `pgbouncer.yaml:166` | `auth_type = scram-sha-256`, `userlist.txt` из Secret |
| `ghcr.io/ne-tort/envoy:v1.31.2` | `egress-proxy.yaml:157` | стартовый конфиг `clusters: []` → `direct_response: 403` |
| `ghcr.io/ne-tort/agentscale-api:latest` | `plugin-gateway.yaml:181` | платформенный образ; новый код Plugin Gateway должен попасть в CI Images до Argo sync |
| OCI-артефакт плагина по `@sha256:` | `.github/workflows/plugin-artifacts.yml` | `:latest` запрещён, `imagePullPolicy: IfNotPresent` в шаблоне Deployment |

**Бюджет:** always-running плагиновая платформа = **+5 подов** (gateway 2, tenant-PG 1, PgBouncer 1, egress-proxy 1) против квоты 45 / 12Gi requests; плюс кратковременный bootstrap-Job и проснувшиеся поды (лимит одновременных wake-up = 2/3). `networkpolicy-agent.yaml` — **справочный эталон**, в боевой рендер не включается (`base/plugins/kustomization.yaml:13-16`).

---

## 6. Что НЕ запускалось (verification freeze)

Явно зафиксировано: **тесты, сборки, компиляция, линт, форматирование, `git status/diff/add/commit/push`, `kubectl`, `kustomize render`, `argocd`, `docker`/`oras`/`cosign`, `alembic`, `psql`, `liquibase` — НЕ запускались** (план миграции §0.2, дизайн v4 §9, `AGENTS.md` «Verification freeze»). Тесты написаны вместе с кодом: `apps/api/tests/unit/plugins/**`, `apps/api/tests/acceptance/plugins/**` (`test_platform_wiring.py`, `test_cross_module_contracts.py`, `conftest.py` с in-memory `Platform`), `apps/web/src/features/plugins/*.test.*`, `infra/ops/tests/test_plugin_ingress_priority.py`, `apps/api/tests/unit/plugins/pilot/**`.

**При первом прогоне ожидаемы падения** — это **СИГНАЛ, а не баг**:

1. `apps/api/tests/acceptance/plugins/conftest.py::_require_module` ловит `ImportError`/`ModuleNotFoundError`/`SyntaxError`/`AttributeError` и даёт `pytest.skip` с понятным сообщением «модуль ещё не собран / временно не импортируется» (параллельные агенты правили каталоги). Skip'ы исчезнут после завершения композиции.
2. `wiring/adapters.py::UnavailableAdapter` — все 9 capability зарегистрированы, но каждый вызов бросает `AdapterMissingError` → `PluginCapabilityService` даёт `ADAPTER_FAILED`/system deny (503), НЕ «200 с None». `adapter_report()`/`Platform.report()` явно перечисляют `stub_adapters` и `requires_f11_work`.
3. `wiring/platform.py::_MissingPort` / `_MissingObjectPorts` / `_MissingGatewayPort` — любой вызов неподключённого порта бросает `PluginPlatformConfigError` с текстом «F11: pass a real adapter via build_plugin_platform overrides».
4. `build_plugin_platform` **упадёт на старте**, если не переданы обязательные `grant_store=`, `cosign=`, `admin_ports=`; `attach_plugin_platform(app, None)` — `PluginPlatformNotAttached`; роутеры без `app.state` отдают 503 (`PLUGIN_PLATFORM_UNAVAILABLE`, `IDENTITY_NOT_CONFIGURED`, `RUNTIME_NOT_INSTALLED`).
5. `infra/ops/tests/test_plugin_ingress_priority.py` читает реальные YAML; до подключения `ingress-plugins.yaml` в родительский kustomization тест приоритетов может быть красным (`plugin-apps-project-notes.md` §4.1) — это индикатор незавершённого `A6`, а не дефект манифеста.
6. CI `plugin-artifacts.yml` на self-hosted runner без подтверждённого OIDC `id-token: write` честно уведёт артефакт в `QUARANTINED` — запрещено «временно отключить проверку подписи».

**Правило для первого прогона:** сначала закрыть `requires_f11_work` из `adapter_report()`, затем смотреть на реальные `Fail`. Не «смягчать» тесты и не подменять заглушки на in-memory «успех».

---

## 7. Чек-лист Verify Dev (что проверить вживую)

Инфраструктурные проверки требуют живого кластера и вынесены в Verify Dev (исключение из freeze — только после явного решения о начале верификации). Dev-контур: `http://172.31.156.203:8088`, Argo `agentscale-dev` ← `main`.

| # | Проверка | Как / где | Ожидаемый результат |
|---|---|---|---|
| 1 | **`pgaudit` загрузился** | лог `tenant-postgres` после старта; `SHOW shared_preload_libraries`; `SELECT * FROM pg_extension WHERE extname='pgaudit'` (если расширение создано) | `pgaudit` в `shared_preload_libraries`, нет `FATAL: unrecognized configuration parameter`; DDL плагина (`CREATE TABLE` из Liquibase) появляется в append-only логе `pgaudit.log=ddl,write,role` (крит. №5) |
| 2 | **`REVOKE PUBLIC` применён** | bootstrap-Job `tenant-postgres-bootstrap` зелёный; `\l` / `SELECT datacl FROM pg_database WHERE datname IN ('prodavan','template1','tenant_<co>')`; тот же REVOKE на платформенном инстансе (`agentscale`, `agentscale_userdb`) | у `PUBLIC` нет `CONNECT,TEMPORARY`; у `plugin_platform_admin` и владельца БД `CONNECT` сохранён; новые `tenant_*` наследуют закрытый ACL из `template1` |
| 3 | **`pg_hba` отклоняет чужую пару** | `psql "host=tenant-postgres dbname=tenant_<other> user=tenant_<co>_plugin_<mod>"` и `dbname=prodavan`; попытка с чужим паролем | `FATAL: no pg_hba.conf entry ...` / `permission denied for database` (крит. №2). Пары `(role, tenant_<co>)` заданы полями `DATABASE`/`USER` в `tenant_hba.conf`, **без `map=`**; `pg_ident.conf` — пустая заглушка; финальные строки `reject all`. Роль с `NOLOGIN` после revoke не подключается вообще |
| 4 | **`cosign verify` до деплоя** | job `.github/workflows/plugin-artifacts.yml`: `cosign sign --yes <ref@sha256>`, `plugin_verify.py` для backend-образа И OCI-артефакта, `COSIGN_CERTIFICATE_IDENTITY` + issuer | `PUBLISH_ALLOWED`; неподписанный/несовпавший digest → `QUARANTINED` + job красный, деплой по `@sha256:`, в манифесте/Deployment нет `:latest` (крит. №18). На self-hosted подтвердить OIDC-токен и `write:packages` у GHCR-токена |
| 5 | **`/webhook/` не уезжает в SPA** | `curl -X POST https://agentscale.dev/webhook/hello_plugin/co_alpha/ping` с валидными `X-Prodavan-Signature/Event/Delivery`; затем без подписи; `GET /pluginpage/...` | валидный → ответ backend (не `index.html`), без подписи → `401` problem+json, повтор `Delivery` → `200 {"status":"duplicate"}` без повторной обработки; приоритеты 120 > 110 > 100 > 50; `GET /health` остаётся корневой пробой, зарегистрированной первой |
| 6 | **0→1 wake** | `POST /api/v1/admin/plugins/wake` (или первый HTTP на спящий плагин), наблюдать `deployments/scale`, `readinessProbe /health`, `202 + Retry-After` при неготовности | реплики 0→1, Gateway **удерживает** соединение до Ready, выдаёт НОВЫЙ короткий `PluginClaims` (TTL 300 с), Argo selfHeal НЕ возвращает 0 (`ignoreDifferences` + `RespectIgnoreDifferences=true`), активный `job_id` блокирует усыпление (крит. №13/№14) |
| 7 | **Redpanda commit** | топик `agentscale.plugin.events`, consumer group с `auto_offset_reset=earliest`, `enable_auto_commit=False`; остановить консьюмера, опубликовать, поднять снова; 3 ретрая → DLQ | событие не потеряно, offset закоммичен **только после успеха** handler'а; в шине/аудите/DLQ нет raw payload, только `payload_digest` (крит. №20) |
| 8 | **STS / presigned** | получить scoped presigned для `tenant-<co>/module-<plugin>/`, попробовать `GET` чужого bucket/префикса; проверить presign TTL ∈ [60, 3600], отдельный STS TTL ∈ [900, 3600] и canary-only policy | свой ключ доступен, чужой → `AccessDenied`; root/service-креды (`agentscale`, `agentscale-api`) нигде не выдаются; по умолчанию работает только scoped presigned, `PLUGIN_MINIO_STS_ENABLED=false` (крит. №19). Включение STS — только после ручной проверки 8 пунктов из `minio-sts-verification.md` |
| 9 | **iframe без `allow-same-origin`** | DevTools на странице плагина: атрибут `sandbox`, `event.origin`, `document.origin` отдельного static-origin, `Referer`, URL | `sandbox="allow-scripts allow-forms"` **БЕЗ** `allow-same-origin`, `credentialless`, `referrerpolicy="no-referrer"`, `event.origin === "null"` (opaque) и сверяется с `expectedOrigin`, `plugin-rpc/1` handshake с `nonce`; в URL нет `?token=`, capability token не попадает во frontend; CSP `connect-src 'none'` для shared API; анонимный `/pluginpage/.../api/*` без сессии → `401` (крит. №6, №17) |

**Дополнительно (по acceptance-карте):** `ops dns-check` (обязательный шаг Verify Dev, `AGENTS.md`); `prodavan-ops validate` (kustomize render dev + overlays + argocd); `\du` на роли плагина (`NOCREATEDB NOCREATEROLE NOSUPERUSER`, `42501` на `CREATE DATABASE`/`CREATE ROLE`); `DATABASECHANGELOG` идемпотентен, `/health` не Ready при ошибке миграции; `429 QUOTA_EXCEEDED` при превышении `ops_per_minute`; `revoke` → старый `PluginClaims` отклоняется по `grant_id`; `plugin_enabled_modules[]` переключает путь без миграции данных.

---

## 8. Быстрые ссылки для интегратора

| Что | Файл |
|---|---|
| Точка входа приложения | `apps/api/src/prodavan/main.py` (`create_app`, строки 27-72) |
| Composition root F11 | `apps/api/src/prodavan/application/plugins/wiring/__init__.py`, `platform.py`, `state.py`; `adapters/__init__.py` (`build_plugin_admin_ports`) |
| Обязательные override'ы | `wiring/platform.py` (`PORT_OVERRIDES`, `SERVICE_OVERRIDES`, `OVERRIDE_NAMES`) |
| Роутеры | `apps/api/src/prodavan/api/v1/plugin_{capabilities,gateway,admin}.py`, `api/v1/router.py:94` |
| Родительская интеграция | `docs/migration/plugin-acceptance-map.md` (A1…A11), `docs/migration/plugin-migration-plan.md` (F11) |
| GitOps | `infra/argocd/apps/plugin-platform.yaml`, `infra/argocd/apps/plugin-apps-project-notes.md`, `infra/k3s/base/plugins/README.md` |
| Тесты «подъёма» | `apps/api/tests/acceptance/plugins/test_platform_wiring.py`, `test_cross_module_contracts.py`, `conftest.py`; план [`docs/migration/plugin-test-plan.md`](plugin-test-plan.md) |
| Сверки | `docs/migration/plugin-network-alignment.md` (as-built лейблы + замок NP), `docs/migration/minio-sts-verification.md` (STS → scoped presigned) |
| Приоритеты Ingress | `infra/ops/tests/test_plugin_ingress_priority.py` |
| CI артефактов | `.github/workflows/plugin-artifacts.yml`, `.github/scripts/plugin_verify.py` |
| ADR | `docs/architecture-migration/ADR-006-plugin-modules.md`, `ADR-007-direct-plugin-db.md`, `ADR-008-lifecycle-and-boundaries.md` |

---

**Конец документа.** Документ только читал существующие файлы (включая появившиеся в ходе чтения `wiring`/`strangler`/`adapters`, MinIO-адаптеры, `plugin-test-plan.md`, `minio-sts-verification.md`, `plugin-network-alignment.md`); код, конфиги и существующие `docs/**` не изменены. Тесты/сборки/линт/git/kubectl не запускались.
