# План миграции: plugin-модули (v4 → код)

**Статус:** в работе. **Ветка:** `main` (dev), без feature-веток.  
**Источник истины по архитектуре:** [`docs/ARCHITECTURE/plugin-architecture-complete.md`](../ARCHITECTURE/plugin-architecture-complete.md) (v4).  
**ADR:** 006 (целевая модель), 007 (`db.direct`), 008 (разделение trust/lifecycle).  
**Формат работы:** микрокоммиты `git add -A && git commit && git push origin HEAD:main` одной командой, без гигиены, без переписывания истории. Тесты пишутся как часть реализации, но **не запускаются** до явного решения о верификации.

---

## 0. Правила для субагентов (обязательные)

1. Работать **только в своём каталоге** (см. таблицу владения §2). Не трогать чужие файлы, не откатывать чужие изменения.
2. **Не запускать** тесты, сборки, компиляцию, линт, форматирование, `git status/diff`, `kubectl`, `alembic`, `psql`. Писать тесты — обязательно; запускать — нет.
3. **Не делать** `git add` / `commit` / `push`. Общий коммит делает родитель.
4. Не трогать общие точки входа: `api/v1/router.py`, `config/settings.py`, `core/middleware.py`, `main.py`, `main_pod.py`, `infrastructure/persistence/models/__init__.py`, `infra/k3s/{base,overlays/dev}/kustomization.yaml`, `.github/workflows/ci-images.yml`, `docs/ARCHITECTURE/plugin-architecture-complete.md`, `docs/PRODUCT.md`, `docs/architecture-migration/ADR-*`, `docs/workflow/*`. Их ведёт родитель.
5. Каждый публичный контракт — с типами, валидацией и unit-тестами в том же каталоге. Fail-closed: отсутствие интеграции = явная ошибка, а не молчаливый успех.
6. **Финальный отчёт — только родительскому агенту через `send_message`**, никуда больше. В отчёте: что прочитано/изменено, список файлов, нерешённые риски, и явная пометка, что тесты/сборки/линт/git не запускались. Отчёт отправлять даже если изменений нет.
7. Ответы и комментарии в коде — на русском (где уместно), идентификаторы — английские.

---

## 1. Фазы

| Фаза | Содержание | Критерий готовности |
|---|---|---|
| F0 | Контракты, доменные типы, схема manifest | типы компилируются концептуально, тесты написаны |
| F1 | Identity: `PluginClaims`, mint/verify, grant revocation | токен нельзя подделать, отзыв по `grant_id` |
| F2 | Capability core + HTTP surface `/capabilities/v1/*` | typed адаптеры, per-`(company, instance, digest)` квоты |
| F3 | Tenant DB lifecycle (БД/роль/схема/`CONNECT`) + объекты (bucket/STS) | роль не видит чужую БД и чужой bucket |
| F4 | Артефакты: OCI/cosign, compatibility gate, quarantine/rollback | деплой только по проверенному digest |
| F5 | Сеть: egress-генератор, две NP, CSP/static-origin | правила из `CapabilityGrant`, не из JWT |
| F6 | Plugin Gateway, Ingress priority 120, webhook-контракт | `/webhook/` не уезжает в SPA |
| F7 | Redpanda dispatch, реестр активных `job_id`, idle-pause | события не теряются, backend не уснёт посреди job |
| F8 | Activator (`replicas: 0` + wake/hold-until-Ready) + GitOps `ignoreDifferences` | 0→1 без новых CRD |
| F9 | Web iframe host + `plugin-rpc/1` | отдельный origin, проверка `event.origin` |
| F10 | Пилотный плагин, strangler по контекстам, удаление declarative-app | фича-флаг переключает путь |
| F11 | Интеграция родителем: router, settings, models, kustomization, CI | общий коммит, Verify Dev |

Итерации внутри фаз дробятся на задачи по мере отчётов. Бэклог пополняется родителем.

---

## 2. Таблица владения файлами (первая волна)

| # | Задача | Каталог / файлы (только свои) |
|---|---|---|
| 01 | Доменные типы + JSON Schema manifest | `packages/schemas/plugin-manifest/v1/**`, `apps/api/src/prodavan/domain/plugins/**`, `apps/api/tests/unit/plugins/domain/**` |
| 02 | `PluginClaims` mint/verify/JWKS/revocation | `apps/api/src/prodavan/application/plugins/identity/**`, `apps/api/tests/unit/plugins/identity/**` |
| 03 | Capability core: реестр адаптеров, квоты, typed контракты | `apps/api/src/prodavan/application/plugins/capabilities/**`, `apps/api/tests/unit/plugins/capabilities/**` |
| 04 | HTTP surface `/capabilities/v1/*` + plugin-middleware | `apps/api/src/prodavan/api/v1/plugin_capabilities.py`, `apps/api/src/prodavan/application/plugins/transport/**`, `apps/api/tests/unit/plugins/transport/**` |
| 05 | Tenant DB lifecycle | `apps/api/src/prodavan/application/plugins/tenantdb/**`, `apps/api/tests/unit/plugins/tenantdb/**` |
| 06 | Object storage: bucket + STS/presigned | `apps/api/src/prodavan/application/plugins/objects/**`, `apps/api/tests/unit/plugins/objects/**` |
| 07 | Artifact registry: OCI/cosign/compatibility/quarantine | `apps/api/src/prodavan/application/plugins/artifacts/**`, `apps/api/tests/unit/plugins/artifacts/**` |
| 08 | Egress policy generator | `apps/api/src/prodavan/application/plugins/egress/**`, `apps/api/tests/unit/plugins/egress/**` |
| 09 | Activator + idle/active-job registry | `apps/api/src/prodavan/application/plugins/activation/**`, `apps/api/tests/unit/plugins/activation/**` |
| 10 | Redpanda dispatch (события плагинов) | `apps/api/src/prodavan/application/plugins/events/**`, `apps/api/tests/unit/plugins/events/**` |
| 11 | Plugin Gateway (proxy/webhook/pluginpage) | `apps/api/src/prodavan/application/plugins/gateway/**`, `apps/api/src/prodavan/api/v1/plugin_gateway.py`, `apps/api/tests/unit/plugins/gateway/**` |
| 12 | Admin API: grant/revoke/uninstall/wake | `apps/api/src/prodavan/api/v1/plugin_admin.py`, `apps/api/src/prodavan/application/plugins/admin/**`, `apps/api/tests/unit/plugins/admin/**` |
| 13 | Web iframe host + `plugin-rpc/1` | `apps/web/src/features/plugins/**`, `apps/web/src/i18n/units/plugins.ts`, `apps/web/src/features/plugins/**/*.test.*` |
| 14 | k3s manifests: tenant PG, PgBouncer, pgaudit, NP, Ingress 120 | `infra/k3s/base/plugins/**`, `infra/k3s/overlays/dev/plugins/**` (kustomization — только родитель) |
| 15 | Argo AppProject whitelist / app / `ignoreDifferences`, тест приоритетов | `infra/argocd/apps/plugin-*.yaml`, `infra/ops/tests/test_plugin_ingress_priority.py` |
| 16 | CI: cosign/provenance/SBOM, pin по digest | `.github/workflows/plugin-artifacts.yml` (новый файл), `.github/scripts/plugin_verify.py` |
| 17+ | Резерв: интеграция, пилот, strangler, i18n, docs, тесты контрактов | назначается родителем по мере освобождения слотов |

**Родитель владеет:** общими точками входа, `docs/**`, реестром ADR, общими коммитами, этим планом.

---

## 3. Бэклог (первая очередь)

### F0
- [x] ADR-006/007/008, реестр ADR, дизайн v4
- [ ] Доменные типы: `PluginManifest`, `PluginInstance`, `CapabilityGrant`, `EgressPolicy`, `ArtifactDigest`, `PluginClaims`
- [ ] JSON Schema манифеста + protocol `plugin-rpc/1`
- [ ] Acceptance-карта: 22 критерия → тест-кейсы

### F1
- [ ] Mint/verify `PluginClaims` (EdDSA/HS256 + `kid` + JWKS)
- [ ] Revocation по `grant_id`, короткий TTL, `jti`
- [ ] Отдельная ветка allowlist middleware (без правки `core/middleware.py`)

### F2
- [ ] `CapabilityGatewayService` → переиспользование ядра через адаптер
- [ ] Typed контракты: cache/objects/docs/events/jobs/pods/mcp/secrets/network
- [ ] Квоты per-`(company, instance, digest)`, `429 QUOTA_EXCEEDED`
- [ ] `/capabilities/v1/*` route-group

### F3
- [ ] `CREATE DATABASE tenant_<company>` / роль / схема / `search_path` / `CONNECT`
- [ ] `NOCREATEDB NOCREATEROLE NOSUPERUSER`, `pgaudit`
- [ ] grant/revoke/uninstall, purge без переноса данных
- [ ] bucket-per-tenant + STS session policy / scoped presigned
- [ ] NP egress :9000 только для plugin-ns

### F4
- [ ] OCI manifest (backend image + frontend + migrations как layers)
- [ ] cosign verify, SBOM/provenance, compatibility gate
- [ ] `QUARANTINED` / rollback через `dynamic_app_registry`
- [ ] pin по digest, запрет `:latest`

### F5
- [ ] Egress-правила из `CapabilityGrant` в БД
- [ ] Две NP из одного генератора, единая схема аудита
- [ ] static-origin + CSP + `allow-same-origin` запрет

### F6
- [ ] Plugin Gateway: grant check, session/`employee ∈ company`, headers allowlist
- [ ] Ingress `router.priority: 120` + entrypoints + ingressClassName
- [ ] Тест приоритетов, webhook HMAC/idempotency/outbox

### F7
- [ ] Топик `agentscale.plugin.events`, ручной commit, `earliest`, DLQ
- [ ] Реестр активных `job_id` с lease
- [ ] Доработка `ProjectIdlePauseService` (только родитель, общий файл)

### F8
- [ ] Deployment `replicas: 0`, wake/hold-until-Ready, watchdog, лимит wake-up
- [ ] `ignoreDifferences /spec/replicas` в Argo Application

### F9
- [ ] iframe host, `plugin-rpc/1` handshake, проверка `event.origin`
- [ ] Публичные страницы, anon-scope

### F10
- [ ] Пилотный плагин с Liquibase и тестовыми данными
- [ ] Фича-флаг `plugin_enabled_modules[]`
- [ ] Strangler по контекстам `mod_equipment`, cutover, удаление declarative-app

### F11
- [ ] Регистрация router/settings/models/main_pod родителем
- [ ] kustomization, CI workflow, общий коммит → Verify Dev

---

## 4. Что НЕ делаем (фиксация решений)

- KEDA / Knative / SPIRE / cert-manager в v1 / Temporal — не вводим (whitelist 27 kinds, 45 подов, 12Gi requests, 3 OOM).
- Бэкапы, перенос данных, dual-read/dual-write, snapshot, метрики парности — не нужны (dev, данные эфемерны).
- RLS, GUC `app.tenant_id`, «единая NP», egress из JWT, `SandboxClaim` для плагинов, `tar.gz + Ed25519`, теги как граница — отвергнуты.
- Платформенная БД `prodavan` недоступна плагину никак.

---

## 5. Открытые блокеры

1. Подтвердить MinIO STS `AssumeRole` / policy variables по официальной документации; при недоступности — scoped presigned.
2. Принятие ADR-008 (уже принят, файл есть).
3. Родительская интеграция общих точек входа после F0–F2.

---

**План живой:** родитель пополняет бэклог после каждого отчёта субагента и сразу занимает освободившийся слот следующей задачей.
