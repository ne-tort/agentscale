# Executable test plan миграции plugin-модулей (v4 → acceptance)

**Источник:** [`docs/ARCHITECTURE/plugin-architecture-complete.md`](../ARCHITECTURE/plugin-architecture-complete.md) §11 (22 критерия),
[`docs/migration/plugin-acceptance-map.md`](plugin-acceptance-map.md) (карта владельцев, ведёт родитель),
[`docs/migration/plugin-migration-plan.md`](plugin-migration-plan.md) (§2 таблица владения).
**Исполняемые тесты:** [`apps/api/tests/acceptance/plugins/`](../../apps/api/tests/acceptance/plugins/)
(`conftest.py`, `test_cross_module_contracts.py`, `test_platform_wiring.py`).
**Статус:** тесты НАПИСАНЫ, **НЕ ЗАПУСКАЮТСЯ** (см. раздел «Верификационный freeze» ниже).

---

## 0. Зачем этот документ

В волне 1 каждый агент написал unit-тесты **только в своём каталоге**:

```
apps/api/tests/unit/plugins/{domain,identity,capabilities,transport,tenantdb,objects,
                              artifacts,egress,activation,events,gateway,admin}/**
infra/ops/tests/test_plugin_ingress_priority.py
apps/web/src/features/plugins/{rpc.ts,errors.ts}   # исходники; *.test.* ещё нет
```

Unit-тесты закрывают отдельные контракты (mint, invoke, `should_suspend`, dispatch,
`validate_manifest`, HMAC, redaction). Но **межмодульные стыки не проверял никто**:
identity выпускает токен, capabilities его проверяет, gateway проксирует, admin
оркестрирует установку, tenantdb/objects/artifacts/activation/events/egress живут
каждый в своём trust-контуре. Именно на стыках случались инциденты из §10 дизайна:
«revoke записан, но gateway не узнал», «политика собрана из JWT, а не из grant»,
«событие закоммичено до обработки», «wake выдал токен, который verify не примет».

Этот план превращает карту из 22 критериев в **executable test plan**:
что уже есть → какой МЕЖМОДУЛЬНЫЙ тест ещё нужен → владелец → статус.

**Легенда статусов:**

| Статус | Смысл |
|---|---|
| `unit ✓` | критерий закрыт unit-тестом в каталоге зоны |
| `acceptance ✓ (написан, не запущен)` | межмодульный тест написан в `tests/acceptance/plugins/**` |
| ` acceptance ⚠ (план, тест не написан)` | стык описан, тест ещё предстоит написать |
| `e2e / кластер (отложено)` | требует реального k8s/PG/MinIO/Redpanda — НЕ пишется в этой волне |
| `CI (родитель)` | проверка манифестов/workflow, не Python-тест |

---

## 1. Таблица: № критерия → что проверяет → unit → МЕЖМОДУЛЬНЫЙ тест → владелец → статус

| # | Что проверяет | Где уже есть unit-тест (файл:класс/функция) | Какой МЕЖМОДУЛЬНЫЙ тест ещё нужен | Владелец | Статус |
|---|---|---|---|---|---|
| **1** | Роль плагина владеет **своей** схемой, но `NOCREATEDB NOCREATEROLE NOSUPERUSER` | `unit/plugins/tenantdb/test_sql.py::test_create_role_contains_all_privilege_flags` | Стык `admin.grant → TenantDbPort.ensure_plugin_schema → TenantDatabaseService.grant_plugin`: DDL из `sql.py` реально уходит в `AdminConnectionPort` с флагами и `search_path` без версии. Проверить, что `grant` создаёт роль ТОЛЬКО для своей пары | задача 05 + accept | unit ✓ / `acceptance ⚠` (план; частично покрыто `test_full_install_path_...`) |
| **2** | Роль не может подключиться к `prodavan` или чужой `tenant_*` БД | `unit/plugins/tenantdb/test_sql.py::test_grant_connect_ddl`, `::test_revoke_public_connect_is_bootstrap_only_helper` | Стык `tenantdb ↔ NetworkPolicy`: `EgressConfigGenerator.build_network_policy(PLUGIN)` НЕ содержит `app: agentscale-postgres`; `prodavan` недостижим. Плюс `psql "$prodavan_dsn"` → `FATAL` (реальный кластер) | задача 05+14 + родитель | unit ✓ / `acceptance ✓` (`test_egress_policy_comes_from_grant_not_from_expired_jwt` проверяет NP) / e2e отложено |
| **3** | Плагин сам применяет Liquibase changelog; повторный запуск не меняет схему | `unit/plugins/artifacts/test_layers.py` (migrations как OCI-layer), `unit/plugins/tenantdb/test_sql.py::test_search_path_ddl_has_no_version` | Стык `artifacts ↔ tenantdb`: migrations-layer из подписанного OCI-артефакта → `TenantDatabaseService` отдаёт `secret_ref` плагину → повторный `grant_plugin` НЕ шлёт `CREATE ROLE`/`CREATE SCHEMA` второй раз (идемпотентность DDL) | задача 05 + пилот F10 | unit ✓ / `acceptance ⚠` (план; требует пилотного плагина с реальным changelog) |
| **4** | `/health` не становится Ready при ошибке миграции / checksum mismatch | `unit/plugins/activation/test_activator.py::test_readiness_probe_error_is_not_success`, `::test_wake_timeout_returns_to_zero_and_audits_denial` | Стык `activation ↔ readinessProbe ↔ gateway`: при `ReadinessPort.is_ready=False` `PluginActivator.wake` НЕ выпускает токен, `GatewayAuth`/`GatewayProxy` НЕ проксирует. Проверить на ОДНОМ `ReadinessPort`, подключённом и к activator, и к роутеру | задача 14 + 09 + 11 | unit ✓ / `acceptance ⚠` (план) |
| **5** | `pgaudit` содержит DDL плагина | `unit/plugins/tenantdb/test_sql.py::test_pgaudit_is_observation_not_privilege_substitute` | Стык `tenantdb ↔ общий AuditPort`: DDL-событие из `TenantDatabaseService` попадает в единый append-only sink и НЕ содержит пароль/DSN/params. Реальные логи tenant-кластера — отдельная e2e-проверка | задача 14 | unit ✓ / `acceptance ✓` (`test_full_install_path_...` + `test_audit_across_all_zones_has_no_raw_data`) / логи отложено |
| **6** | Анонимный `/pluginpage/{plugin}/{company}/api/*` без сессии → `401` | `unit/plugins/gateway/test_auth.py` (сессия/публичные страницы) | **Стык web↔gateway**: публичная страница (`rpc.ts` не несёт capability-token) НЕ даёт анонимный доступ к `/api/*`; `is_api=True` требует сессии даже при `public_pages`. Написан: `test_public_page_does_not_open_backend_api_anonymously` | задача 11 + 13 | unit ✓ / **`acceptance ✓`** |
| **7** | Webhook без/с неверной `X-Prodavan-Signature` → `401`; повтор `X-Prodavan-Delivery` не дублирует обработку | `unit/plugins/gateway/test_auth.py` (HMAC + `DeliveryVerdict`) | **Стык web↔gateway**: подпись + идемпотентность на РЕАЛЬНОМ `GatewayAuth` с общим `RateLimitPort`/`AuditPort`. Написан: `test_webhook_hmac_and_delivery_idempotency_across_gateway` | задача 11 | unit ✓ / **`acceptance ✓`** |
| **8** | Egress на хост вне `allowed_hosts` → `403` + событие аудита | `unit/plugins/egress/test_resolver_service.py::test_host_evil_com_gets_403_via_service`, `test_generator.py::test_envoy_deny_route_403_for_non_match` | **Стык egress↔NP-генератор**: `GrantPolicyResolver` (задача 08) и `EgressConfigGenerator` (задача 08/14) читают ОДИН grant; `evil.com` не совпадает ни с одним `EgressRule`, NP не содержит `prodavan`. Написан: `test_egress_policy_comes_from_grant_not_from_expired_jwt` | задача 08 + 14 | unit ✓ / **`acceptance ✓`** |
| **9** | `/webhook/` и `/pluginpage/` не уезжают в SPA | `infra/ops/tests/test_plugin_ingress_priority.py::test_plugins_gateway_outranks_every_dev_router`, `::test_plugins_gateway_paths_are_exactly_the_two_prefixes` | Стык `Ingress priority 120 ↔ GatewayAuth`: приоритет проверен как YAML, но НЕ связан с обработчиком. Резерв: прогнать `/webhook` через `authorize_webhook` и убедиться, что путь НЕ трактуется как статика (`index.html` не отдаётся) | задача 15 + 11 | unit ✓ (YAML) / `acceptance ⚠` (план; частично `test_webhook_hmac_...`) / CI (родитель) |
| **10** | Превышение `ops_per_minute` → `429` + `QUOTA_EXCEEDED` | `unit/plugins/capabilities/test_quotas.py::test_ops_per_minute_exceeded_is_429_and_quota_exceeded`, `test_service.py::test_quota_exceeded_returns_429_via_core_gateway` | Стык `capabilities ↔ transport (HTTP)`: `PluginGatewayDecision.http_status == 429` → `problem_from_decision` даёт problem+json с `QUOTA_EXCEEDED`. Проверить сквозной путь от `FakeQuota` до HTTP-проекции | задача 03 + 04 | unit ✓ / `acceptance ⚠` (план) |
| **11** | Audit-событие не содержит raw SQL, параметров, токенов, payload | `unit/plugins/capabilities/test_audit.py::test_redact_removes_password_token_sql_and_body_values`, `unit/plugins/admin/test_contracts.py::test_audit_event_redaction_has_no_sensitive_fields`, `unit/plugins/objects/test_service.py::test_audit_event_has_no_bytes_payload_or_secrets`, `unit/plugins/events/test_dispatch.py::test_audit_received_has_no_raw_payload_or_url`, `unit/plugins/tenantdb/test_service.py::test_audit_never_contains_raw_sql_or_params` | **Стык ВСЕ зоны → единый sink**: при одновременной работе capabilities/admin/activation/events/gateway ни одна не протащила сырые данные; `redact()` заменяет на `*_digest`. Написан: `test_audit_across_all_zones_has_no_raw_data` | задача 03 + все | unit ✓ / **`acceptance ✓`** |
| **12** | `revoke` деактивирует `CapabilityGrant`; старый `PluginClaims` отклоняется по `grant_id` | `unit/plugins/identity/test_revocation.py::test_revoke_then_check_raises_revoked`, `unit/plugins/capabilities/test_service.py::test_revoked_grant_is_denied_before_adapter_invocation`, `unit/plugins/admin/test_service.py::test_revoke_instantly_revokes_token_by_grant_id` | **Стык identity↔capabilities↔gateway↔admin**: ОДИН `FakeRevocation` на все зоны; отзыв мгновенно виден и на `invoke`, и на `authorize_page`, и после реального `PluginAdminService.revoke`. Написано: `test_revoke_denies_same_token_on_capabilities`, `test_revoke_denies_same_grant_on_gateway`, `test_admin_revoke_is_immediately_visible_to_capabilities` | задачи 02, 12, 11 | unit ✓ / **`acceptance ✓`** |
| **13** | Wake-up из `replicas: 0` удерживает запрос до Ready и выдаёт новый короткий токен | `unit/plugins/activation/test_activator.py::test_wake_from_zero_holds_until_ready`, `::test_wake_mints_fresh_token_not_expired_secret_one` | **Стык activation↔identity↔gateway**: токен из `MintPort` проверяется тем же `KeyRing`, что и входящие запросы; `hold_request` проксирует ТОЛЬКО после Ready. Написано: `test_activation_wake_token_is_verifiable_by_identity_keyring`, `test_full_install_path_...` (wake) | задачи 09, 11, 15 | unit ✓ / **`acceptance ✓`** (hold_request — `acceptance ⚠`) |
| **14** | Активный `job_id` блокирует усыпление backend | `unit/plugins/activation/test_activator.py::test_active_job_blocks_suspend`, `::test_complete_job_then_idle_ttl_suspends` | **Стык events↔idle**: событие из Redpanda не коммитится, пока job активен; общие `ClockPort`/`ScalePort`/`AuditPort` без реального `sleep`. Написано: `test_active_job_blocks_suspend_then_ttl_allows_it`, `test_event_not_lost_when_consumer_is_idle` | задача 09 | unit ✓ / **`acceptance ✓`** |
| **15** | Uninstall дропает схему/роль и bucket/prefix, не трогая чужие БД | `unit/plugins/admin/test_service.py::test_uninstall_does_not_touch_other_company`, `unit/plugins/tenantdb/test_service.py::test_uninstall_drops_only_own_schema_and_role`, `unit/plugins/objects/test_service.py::test_grant_and_uninstall_of_one_company_never_name_other_bucket` | **Стык tenantdb↔objects↔admin**: ВСЕ ТРИ зоны смотрят в ОДНО состояние; ни один вызов порта не получил чужой `company_id`; БД другой компании сохранена. Написан: `test_uninstall_drops_only_own_company_resources` | задачи 05, 06, 12 | unit ✓ / **`acceptance ✓`** |
| **16** | Фича-флаг `plugin_enabled_modules[]` переключает путь без миграции данных | `unit/plugins/admin/test_service.py::test_set_enabled_modules_switches_path_without_data_migration`, `::test_no_data_migration_operation_exists` | Стык `admin ↔ роутинг (strangler)`: переключение флага НЕ трогает declarative-app данные и НЕ пересобирает grant/БД. Проверить на ОДНОМ `GrantStore` + реальном `set_enabled_modules`, что `get_enabled_modules` изменился, а `grants` нет | задача 12 + F10 | unit ✓ / `acceptance ⚠` (план) |
| **17** | Плагин с `PluginClaims` не имеет доступа к agent-surface `:8001` | `unit/plugins/identity/test_allowlist.py::test_agent_surface_is_closed_for_plugin_claims`, `::test_wrong_typ_is_closed_even_on_valid_capabilities_path`; `unit/plugins/transport/deps.py` (код) | **Стык identity↔transport**: РЕАЛЬНО выпущенный токен прогоняется через `assert_plugin_surface` на путях `/api/v1/projects/.../agent/*`; чужой `typ=pod_bridge` отсекается ДО криптографии. Написано: `test_plugin_claims_cannot_reach_agent_surface`, `test_wrong_typ_is_not_a_plugin_capability_token` | задачи 02, 04 | unit ✓ / **`acceptance ✓`** |
| **18** | `cosign verify` до деплоя; неподписанный digest → `QUARANTINED`, деплой по digest, не `:latest` | `unit/plugins/artifacts/test_verification.py::test_cosign_verify_failed_is_quarantined`, `::test_requested_capabilities_wider_than_grant_is_denied`; `test_registry.py::test_publish_after_successful_verify`; `test_oci.py::test_latest_explicitly_forbidden` | **Стык artifacts↔CapabilityGrant↔admin**: `CapabilityGrant.capabilities` УЖЕ, чем `requestedCapabilities` → `INCOMPATIBLE`/`QUARANTINED`; полный grant → `APPLIED` → admin-гейт `PUBLISHED` разрешает установку. Написано: `test_narrow_grant_quarantines_artifact`, `test_full_grant_publishes_then_admin_grant_is_allowed`, `test_full_install_path_...` | задачи 07, 12, 16 | unit ✓ / **`acceptance ✓`** |
| **19** | STS creds плагина не видят объекты чужого bucket/префикса | `unit/plugins/objects/test_service.py::test_other_company_cannot_get_creds_for_tenant_other`, `test_policy.py::test_session_policy_is_scoped_to_its_own_pair_only` | Стык `objects ↔ admin.grant`: `PluginObjectStore.issue_credentials` и `ObjectStorePort.ensure_bucket` получают ОДНУ пару `(company_id, plugin_id)`; creds из `StsPort` перепроверяются на `bucket == tenant-<company>` и `prefix == module-<plugin>/`. Частично: `test_full_install_path_...` | задача 06 | unit ✓ / `acceptance ⚠` (план; `issue_credentials` на стыке не вызван) |
| **20** | Redpanda consumer не теряет событие при простое (ручной commit, `earliest`, DLQ) | `unit/plugins/events/test_dispatch.py::test_idle_consumer_does_not_commit_anything`, `::test_offset_is_not_committed_until_handler_succeeds`, `test_topics_contracts.py::test_consumer_config_fixed_earliest_and_no_auto_commit` | **Стык events↔idle↔activation**: `poll() → None` НЕ даёт ни одного `commit`; wake-хук ДО handler; commit ПОСЛЕ. Написан: `test_event_not_lost_when_consumer_is_idle` | задача 10 | unit ✓ / **`acceptance ✓`** |
| **21** | Один плагин не может исчерпать квоту компании (per-instance/digest) | `unit/plugins/capabilities/test_quotas.py::test_one_noisy_plugin_cannot_exhaust_company_quota`, `::test_two_instances_of_one_company_have_independent_counters` | Стык `identity (artifact_digest) ↔ capabilities (PluginQuotaKey)`: ключ квоты строится ТОЛЬКО из проверенных claims `(company, instance, digest)`, а не из тела запроса. Проверить, что `FakeQuota` с общим `capability_service` не даёт «шумному» инстансу съесть лимит соседа | задача 03 | unit ✓ / `acceptance ⚠` (план) |
| **22** | Egress-политика не зависит от срока жизни JWT | `unit/plugins/egress/test_resolver_service.py::test_expired_token_does_not_change_policy_from_grant`, `::test_policy_is_stable_across_token_rotation`; `unit/plugins/capabilities/test_service.py::test_egress_policy_comes_from_grant_not_from_token` | **Стык egress↔capabilities↔admin**: grant, созданный `PluginAdminService`, читается `GrantPolicyResolver`; свежий и ИСТЁКШИЙ токен дают ОДНУ политику; `EgressPolicyPort` capabilities резолвит по `grant_id`. Написано: `test_egress_policy_comes_from_grant_not_from_expired_jwt`, `test_capabilities_egress_port_reads_grant_not_token` | задача 08 + 03 | unit ✓ / **`acceptance ✓`** |

**Итого:** 22 критерия имеют unit-покрытие; **14 критериев** получили написанные
межмодульные acceptance-тесты (№ 2, 5, 6, 7, 8, 11, 12, 13, 14, 15, 17, 18, 20, 22),
остальные стыки вынесены в раздел 2 как резервные проверки / отложенные e2e.

---

## 2. Стыки, которые никто не покрыл

Ниже — РАЗРЫВЫ МЕЖДУ МОДУЛЯМИ. Unit-тесты каждой зоны проходят по отдельности,
но контракт «один модуль отдаёт → другой принимает» не проверялся нигде.
В скобках — где именно живёт написанный тест или почему он ещё не написан.

### 2.1 `identity ↔ capabilities`
- **Разрыв:** `mint_plugin_claims` (задача 02) выпускает `typ=plugin_capability`,
  `scopes`, `grant_id`, `artifact_digest`; `PluginCapabilityService.invoke`
  (задача 03) требует ровно эти поля и сверяет `grant_id` через
  `GrantRevocationPort`. Unit-тест capabilities конструировал claims вручную.
- **Покрыто:** `test_mint_verify_is_active_invoke_audit_happy_path`,
  `test_missing_revocation_port_fails_closed_on_real_service`.
- **Резерв (не написан):** `get_plugin_claims` (transport/deps.py) →
  `PluginCapabilityService.invoke` на ОДНОМ `Request` и ОДНОМ `app.state`;
  проверить, что `PluginIdentityConfig` и `PluginCapabilityService` собран
  родителем из одного `KeyRing`/`GrantRevocation` (F11, A1/A2 карты).

### 2.2 `capabilities ↔ transport`
- **Разрыв:** `PluginGatewayDecision.code` → `problem_from_decision` →
  `429 QUOTA_EXCEEDED` / `403 GRANT_REVOKED` / `503 ADAPTER_MISSING`;
  `bridge.py` преобразует Pydantic-тела в датаклассы `contracts.py`.
  Формат операции публичный — ТОЧЕЧНЫЙ (`cache.get`), внутренний — голый
  (`get`): расхождение ломает `_prepare`/`dispatch` молча.
- **Покрыто (частично):** `test_mint_verify_...` (голая форма `get`),
  `test_cross_zone_constants_are_consistent` (реестры согласованы).
- **Резерв (не написан):** сквозной HTTP-путь `POST /capabilities/v1/cache/get`
  с валидным Bearer → `get_plugin_claims` → `bridge` → `invoke` → problem+json.
  Требует `FastAPI TestClient` и `app.state`, которые подключает родитель (A1/A2).

### 2.3 `tenantdb ↔ objects ↔ admin`
- **Разрыв:** `PluginAdminService.grant` вызывает СВОИ `TenantDbPort` /
  `ObjectStorePort`, а реальные `TenantDatabaseService.grant_plugin` и
  `PluginObjectStore.grant` — это ДРУГИЕ классы с ДРУГИМИ портами.
  Никто не проверял, что все трое получают одну пару `(company_id, plugin_id)`
  и что uninstall не задевает соседнюю компанию.
- **Покрыто:** `test_full_install_path_artifacts_admin_tenantdb_objects_wake`,
  `test_uninstall_drops_only_own_company_resources`.
- **Резерв (не написан):** `PluginObjectStore.issue_credentials` на стыке
  (кр. 19), а также реальный `AdminConnectionPort` с параметризованным DDL
  (кр. 1/3) — требует адаптера F11 или пилотного плагина.

### 2.4 `artifacts ↔ CI`
- **Разрыв:** `CosignPort.verify(image_ref, identity, issuer)` в Python и
  `.github/workflows/plugin-artifacts.yml` + `.github/scripts/plugin_verify.py`
  (задача 16) должны проверять ОДИН digest и ОДНУ keyless-identity.
  `OciArtifactRef` запрещает `:latest`, но CI может задеплоить по тегу.
- **Покрыто:** `test_narrow_grant_quarantines_artifact`,
  `test_full_grant_publishes_then_admin_grant_is_allowed` (Python-сторона).
- **Резерв (отложено, владелец задача 16/родитель):** статическая проверка
  workflow: `cosign verify` ДО deploy, `newTag`/`image` закреплён по
  `sha256:...`, `id-token: write` для keyless, SBOM/provenance прикреплены.
  Это CI-gate, а не Python acceptance-тест.

### 2.5 `egress ↔ NP-генератор`
- **Разрыв:** `GrantPolicyResolver.resolve(grant_id)` (задача 08) строит
  `EgressPolicy`, а `EgressConfigGenerator.build_network_policy(scope)`
  (задача 08/14) и `build_envoy_routes(policy)` должны строиться из ТОЙ ЖЕ
  политики. Единый генератор + ДВЕ NP + одна схема аудита — §6.1/§13.3.
- **Покрыто:** `test_egress_policy_comes_from_grant_not_from_expired_jwt`
  (обе NP из одного генератора, `prodavan` отсутствует),
  `test_capabilities_egress_port_reads_grant_not_token` (стык с capabilities).
- **Резерв (не написан):** сравнение сгенерированного NP-YAML с манифестами
  `infra/k3s/base/plugins/networkpolicy-*.yaml` (задача 14) — на стыке
  «код ↔ GitOps»; требует читать YAML как данные (образец:
  `infra/ops/tests/test_plugin_ingress_priority.py`).

### 2.6 `activation ↔ gateway`
- **Разрыв:** `PluginActivator.hold_request` требует `ProxyPort` (задача 11),
  `GatewayAuth` решает «пустить», `GatewayProxy` исполняет, `WakeLease` из
  gateway/ports.py — ДРУГОЙ тип, чем `WakeResult` из activation/state.py.
  Никто не проверял, что wake НЕ проксирует до Ready и что новый короткий
  токен из `MintPort` подставляется в `Authorization` вместо входящего.
- **Покрыто:** `test_full_install_path_...` (wake),
  `test_activation_wake_token_is_verifiable_by_identity_keyring`,
  `test_revoke_denies_same_grant_on_gateway`.
- **Резерв (не написан):** `hold_request` → `ProxyPort.forward` →
  `build_forward_headers` (`X-Plugin-*`, отбрасывание `Cookie`/`Authorization`);
  требует `GatewayProxy` и `BackendRequest`, которые компонует родитель (A1).

### 2.7 `events ↔ idle`
- **Разрыв:** `PluginEventDispatcher.consume` вызывает `WakeHookPort.wake`
  ДО `EventHandlerPort.handle` и коммитит offset ТОЛЬКО после успеха.
  `PluginActivator` владеет `active_job_ids` с lease. Если консьюмер «уснул»
  посреди job, as-built `kick`+`auto_commit` терял событие (§13.3).
- **Покрыто:** `test_event_not_lost_when_consumer_is_idle`,
  `test_active_job_blocks_suspend_then_ttl_allows_it`.
- **Резерв (не написан):** один и тот же `job_id` в `register_job` и в
  `DlqRecord.run_id`/аудите при 3 ретраях; проверить, что `wake` НЕ
  усыпляет backend, пока событие не закоммичено. Требует связки
  `WakeHookPort → PluginActivator.wake` (F11).

### 2.8 `web ↔ gateway`
- **Разрыв:** `apps/web/src/features/plugins/rpc.ts` (`plugin-rpc/1`,
  проверка `event.origin`/`event.source`, БЕЗ `allow-same-origin`, токен НЕ
  попадает во frontend) и `GatewayAuth` (сессия, `employee ∈ company`,
  `public_pages` с `ANON_SCOPES`, отдельный rate limit, HMAC webhook).
  Стык: публичная страница НЕ должна давать анонимный `/api/*`; web-тестов
  (`*.test.*`) в каталоге ещё нет.
- **Покрыто:** `test_public_page_does_not_open_backend_api_anonymously`,
  `test_webhook_hmac_and_delivery_idempotency_across_gateway`.
- **Резерв (не написан, владелец задача 13):** front-end тесты `rpc.ts`:
  чужой `origin`/`source` ИГНОРИРУЕТСЯ, а не роняет канал; таймаут handshake;
  повтор `requestId`; `PluginFrameError.detail` не содержит токен. Плюс
  A8/A9 родителя (i18n-юнит `plugins`, маршрут pluginpage в `app/router.tsx`).

---

## 3. Smoke собираемости (`test_platform_wiring.py`)

| Проверка | Что даёт | Статус |
|---|---|---|
| Все `__init__.py` зон `application/plugins/*` импортируются | Ловит «публичный контракт расходится с `__all__`» и битые импорты на стыке | написан |
| Все порты имеют реализацию ИЛИ явную заглушку | Fail-closed: порт не может «тихо отсутствовать»; реестр `PORTS_WITH_INMEMORY_IMPL` | написан |
| `AdapterRegistry` = 9 адаптеров, `db.direct` отсутствует | ADR-007: БД = прямой DSN, а не capability | написан |
| `settings.plugin_platform_enabled=False` по умолчанию | Платформа не обслуживает запросы без явного включения | написан |
| Включение без секрета / без static-origin падает | Связь с `tests/unit/test_settings_guards.py` (guard'ы задачи родителя A3) | написан |
| Константы контуров согласованы (`issuer`/`aud`/`typ`, топик, TTL, префиксы, `plugin-rpc/1`) | Рассинхрон констант ломает стыки, не попадая в unit-тесты | написан |

---

## 4. Верификационный freeze (ОБЯЗАТЕЛЬНО К ПРОЧТЕНИЮ)

> **Тесты НЕ запускаются до явного решения о начале верификации.**
> Это зафиксировано в [`plugin-migration-plan.md`](plugin-migration-plan.md) §0.2,
> [`plugin-acceptance-map.md`](plugin-acceptance-map.md) (правило карты) и
> `AGENTS.md` → «Migration-stage work protocol / Verification freeze».
> Разрешено ТОЛЬКО писать тесты и рассуждать по коду/контрактам/критериям.
> Не запускались и не будут запускаться в этой задаче: `pytest`, сборки,
> компиляция, линт/форматирование, `git`, `kubectl`, `alembic`, `psql`.

### Что ожидается при ПЕРВОМ прогоне (после решения о верификации)

1. **Ожидаемы падения из-за отсутствующих адаптеров — это СИГНАЛ, а не баг.**
   Большинство портов зон (`AdminConnectionPort`, `StsPort`, `CosignPort`,
   `ConsumerPort`, `ScalePort`, `PlatformAdminPort`, `BackendHttpPort`,
   `GrantStorePort` в БД) реализуются родителем в F11 или задачами 14/15/16.
   Acceptance-тесты собирают платформу на in-memory фейках, но если реальный
   wiring в `app.state` ещё не подключён (A1/A2/A10 карты), smoke-тесты и
   сквозные пути честно упадут. Падение = «стык не собран», а не «тест плохой».

2. **`pytest.skip` вместо падения коллекции.** Все импорты чужих модулей идут
   через `conftest._require_module()`: если зона ещё отсутствует/временно не
   импортируется (параллельные агенты правят каталоги в реальном времени),
   тест получает точечный `skip` с понятным сообщением. Это требование
   задания, а не смягчение проверки: после завершения миграции skip'ы
   исчезнут, и ожидаемые падения станут видимыми.

3. **Известные «горячие точки» первого прогона:**
   - `PluginCapabilityService` требует `GrantRevocationPort`, `AuditPort`,
     `QuotaPort`, `AdapterRegistry` — без любого из них `allow` невозможен;
   - `PluginAdminService` требует `PluginAdminPorts` ЦЕЛИКОМ (ни одно поле
     не имеет дефолта) — половина адаптеров приходит от родителя;
   - `PluginActivator` требует `ScalePort`/`ReadinessPort`/`ClockPort`/
     `MintPort`/`AuditPort` и опционально `ProxyPort`;
   - `GatewayAuth.__post_init__` отказывает при любом `None`-порте;
   - `PluginEventDispatcher` отказывает при первом же вызове без порта
     (`MISSING_DEPENDENCY`), а НЕ «буферизует в памяти»;
   - `ArtifactVerifier` при отсутствии `validate_manifest` (задача 01)
     уходит в `QUARANTINED` — это корректный fail-closed, а не ошибка теста.

4. **Что НЕ считается дефектом:** отложенные e2e-проверки (реальный
   PostgreSQL/MinIO/Redpanda/k3s, `psql FATAL`, `DATABASECHANGELOG`,
   `pgaudit`-логи, cosign в CI) — они вынесены за рамки этой волны и
   помечены в таблице как `e2e / кластер (отложено)` / `CI (родитель)`.

### Порядок верификации (когда решение принято)

```text
1) tests/unit/plugins/**                    — зоны по отдельности (уже написаны соседями)
2) tests/acceptance/plugins/test_platform_wiring.py  — собираемость + guard'ы
3) tests/acceptance/plugins/test_cross_module_contracts.py — стыки
4) infra/ops/tests/test_plugin_ingress_priority.py    — YAML/GitOps (задача 15)
5) e2e на dev-контуре (только после Verify Dev и явного решения родителя)
```

---

## 5. Владение файлами этой задачи

| Файл | Назначение |
|---|---|
| `docs/migration/plugin-test-plan.md` | этот документ (создан) |
| `apps/api/tests/acceptance/__init__.py` | маркер пакета (создан) |
| `apps/api/tests/acceptance/plugins/__init__.py` | маркер пакета (создан) |
| `apps/api/tests/acceptance/plugins/conftest.py` | in-memory фейки ВСЕХ портов, `PluginClaims`, «собранная платформа», ленивые импорты + `pytest.skip` |
| `apps/api/tests/acceptance/plugins/test_cross_module_contracts.py` | приёмочные тесты на 8+ стыков (написаны, не запускались) |
| `apps/api/tests/acceptance/plugins/test_platform_wiring.py` | smoke собираемости + settings guard'ы (написаны, не запускались) |

**Не тронуты (правило 1):** `docs/ARCHITECTURE/**`,
`docs/migration/plugin-acceptance-map.md` (ведёт родитель),
`docs/migration/plugin-migration-plan.md`, чужие unit-тесты, исходники
`application/plugins/**`, `config/settings.py`, `api/v1/router.py`,
`core/middleware.py`, web-исходники.

---

## 6. Открытые риски для родителя

1. **F11-интеграция — единый источник состояния.** Acceptance-тесты
   моделируют «один `FakeRevocation` / один `GrantStore` на все зоны». В проде
   это должны быть ОДИН реестр отзывов и ОДНА таблица `CapabilityGrant`;
   если capabilities и gateway получат разные адаптеры, критерий №12
   (мгновенный отзыв) рассыплется. См. A1/A2/A10 карты.
2. **Расхождение формата операции** `cache.get` (публичный) ↔ `get`
   (внутренний `dispatch`) — зафиксировано в `capabilities/service.py` и
   `ports.py`; transport (задача 04) обязан слать точечную форму.
3. **Web-тесты отсутствуют:** `apps/web/src/features/plugins/**` содержит
   только `rpc.ts`/`errors.ts`, без `*.test.*`. Стык web↔gateway проверен
   только с backend-стороны; front-end handshake остаётся резервом задачи 13.
4. **Пилот F10 не реализован:** критерии №3 (Liquibase), №16 (strangler)
   и полный e2e пути установки требуют пилотного плагина с реальным changelog.
5. **CI-часть критерия №18** (cosign/provenance/SBOM, pin по digest) проверяется
   задачей 16/родителем, а не Python acceptance-тестами; здесь закрыта только
   Python-сторона `ArtifactVerifier` + `PluginArtifactRegistry`.

---

**Документ живой:** после отчётов субагентов родитель обновляет
`plugin-acceptance-map.md`, а этот план пополняется строками `acceptance ⚠`.
Тесты/сборки/линт/git в рамках этой задачи НЕ запускались.
