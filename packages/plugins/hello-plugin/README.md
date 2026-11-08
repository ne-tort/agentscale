# `hello_plugin` — пилотный плагин (эталон v4)

> **Это эталон для всех будущих плагинов, а НЕ перенос `mod_equipment`.**
> Пилот проверяет контур v4 end-to-end (§8 этап 2): OCI-артефакт → установка →
> grant DSN → self-managed Liquibase → wake-up → iframe/`plugin-rpc/1` →
> webhook-подпись. Данные dev **эфемерны**: сид создаёт тестовые приветствия
> заново, переноса/бэкапов/snapshots/dual-write **нет** (§8, §13).
> `mod_equipment` (~13k строк) мигрирует **позже strangler'ом** за фича-флагом
> `plugin_enabled_modules[]` по одному bounded context'у (§8 этапы 3–6).

Плагин — **полноценное приложение** (§0 цель 1): свой frontend, свой
backend-контейнер, реальные типизированные таблицы в своей БД компании и
self-managed Liquibase-миграции. Не jsonb-строки `module_instance_data_rows`.

## Структура (слои единого OCI-артефакта)

```
packages/plugins/hello-plugin/
├── manifest.json                 # контракт задачи 01, plugin-rpc/1, БЕЗ самореференции
├── migrations/
│   └── changelog.xml             # 0001 greetings, 0002 greeting_stats (typed, +DOWN)
├── backend/
│   ├── app.py                    # FastAPI: /health, /webhook/ping, /api/greetings, /api/stats
│   ├── liquibase_url.py          # DSN -> JDBC (Python-логика в контейнере, не shell-парсинг)
│   ├── entrypoint.sh             # КОНТЕЙНЕРНЫЙ entrypoint: liquibase update -> exec uvicorn
│   ├── requirements.txt
│   └── Dockerfile                # python:3.13-slim, non-root 10001, HEALTHCHECK
├── frontend/
│   ├── index.html                # CSP, отдельный origin, без allow-same-origin
│   └── app.js                    # plugin-rpc/1 handshake + plugin.rpc.request
└── README.md
```

## 1. Сборка в OCI (задача 16, §5.1)

Backend-собирается из корня пакета (контекст включает `backend/` и `migrations/`):

```bash
docker build -f backend/Dockerfile -t ghcr.io/ne-tort/plugins/hello_plugin@sha256:<digest> .
```

Задача 16 упаковывает **единый OCI-артефакт** в GHCR:

| Слой | Что |
|---|---|
| backend image | `linux/amd64`, `python:3.13-slim`, non-root |
| frontend | `frontend/index.html` + `app.js`, mediaType `application/vnd.agentscale.plugin.frontend` |
| migrations | `migrations/changelog.xml` |
| annotations | `plugin_id`, `manifest_version`, `protocol`, `requestedCapabilities`, `min_platform_version`, `artifact_digest` |

- Подпись — **cosign keyless по digest**, не по тегу (`id-token: write`,
  `--certificate-identity` / `--certificate-oidc-issuer`), SBOM + provenance
  включены. `cosign verify` выполняется **до** создания Deployment.
- **Pin по digest, `:latest` запрещён.** Неподписанный/несовпавший digest →
  `QUARANTINED`; откат = деплой предыдущего immutable digest.
- В `manifest.json` **нет** `artifact_digest`/`signature`: манифест подписывается
  как OCI-annotation и не может ссылаться на самого себя (проверка `not.anyOf`
  в `packages/schemas/plugin-manifest/v1/schema.json`).
- `imagePullPolicy: IfNotPresent` задаётся в k8s Deployment (§7.4), **не в Dockerfile**.

## 2. Получение DSN при grant (задачи 05 / 12, §2, §5.1, §5.5)

`POST /gateway/plugins/hello_plugin/grant` (задача 12) через lifecycle задачи 05:

1. `CREATE DATABASE tenant_<company_id>` (одна БД на компанию, отдельный
   tenant-кластер, **не** `prodavan`).
2. Роль `tenant_<company>_plugin_hello_plugin`: `LOGIN`, `NOSUPERUSER
   NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`, `CONNECTION LIMIT 10`;
   `REVOKE CONNECT ... FROM PUBLIC` → `GRANT CONNECT` только на свою БД.
3. `CREATE SCHEMA module_hello_plugin AUTHORIZATION <роль>` +
   `ALTER ROLE ... SET search_path = 'module_hello_plugin'`.
4. k8s **Secret** (не ConfigMap, не plaintext в манифесте): `GATEWAY_URL`,
   `PLUGIN_ID`, `TENANT_DB_DSN`, короткий `PLUGIN_CLAIMS` lease,
   `PLUGIN_WEBHOOK_SECRET`. Ротация при reinstall/revoke.
5. Deployment `replicas: 0` по образу из того же OCI digest.

`TENANT_DB_DSN` — **прямой DSN к своей БД компании** (вариант B, ADR-007),
полный SQL: JOIN/CTE/DDL/индексы/миграции. Это **не** capability `db.direct`
и **не** gateway-адаптер; остальная инфраструктура (`cache`, `objects`, `docs`,
`events`, `jobs`, `pods`, `mcp`, `secrets`, `network`) идёт через typed-адаптеры
`/capabilities/v1/*`. Платформенная БД `prodavan` недоступна вообще.

Граница — привилегии PostgreSQL + `pg_hba.conf` + NetworkPolicy (egress пода:
tenant-PG :5432, Plugin Gateway :8080, egress-proxy, MinIO :9000, DNS),
а не RLS/GUC и не парсер SQL. `pgaudit` фиксирует DDL как факт.

## 3. Старт и миграции (§3, §11 критерии 3, 4)

`backend/entrypoint.sh` — **контейнерный** entrypoint, а не хостовый `.sh` для
инфраструктуры (AGENTS.md, принцип 2): он не трогает k8s и не управляет
кластером, а только готовит состояние своего контейнера.

```
liquibase update (changelog.xml, свой DSN, LIQUIBASE_ALLOW_EXECUTE_COMMAND=false)
  → DATABASECHANGELOG в схеме плагина
  → LIQUIBASE_UPDATE_OK=1
  → exec python -m uvicorn app:app --port 8080
```

- `/health` = **200/Ready только после успешного `update`**; до этого 503
  (`migrations_pending`), при ошибке БД 503, при checksum mismatch контейнер
  вообще не стартует (`set -eu`). Критерий 4.
- Повторный запуск идемпотентен по `DATABASECHANGELOG`; `plan` = `updateSQL`,
  `rollback` = `rollbackCount` (у обоих changeset'ов есть явный `rollback`).
  Критерий 3.
- **Версия плагина НЕ в имени схемы/таблиц/индексов** — иначе при обновлении
  данные останутся в старой схеме (§3.3).

## 4. Пробуждение (задача 09, §7.4)

Плагин **не** использует `SandboxClaim`/WarmPool (они для stateful агентов):

- Deployment `replicas: 0`, `readinessProbe: /health`, `imagePullPolicy:
  IfNotPresent`, явные `resources`, Argo `ignoreDifferences /spec/replicas`.
- Plugin Gateway при HTTP (iframe, webhook, pluginpage), cron, событии или
  `POST /gateway/plugins/wake` патчит `deployments/scale` и **удерживает
  соединение до Ready**, затем проксирует и выдаёт новый короткий
  `PluginClaims` с TTL 300 с (`grant_id` из долгоживущего `CapabilityGrant`).
- Idle TTL ведёт Gateway; активный `job_id` с lease запрещает усыпление
  (задача 09, `ActivationState.active_job_ids`).
- Токен — только в `Authorization`, **никогда в URL query**.

## 5. Подпись и webhook (задача 07 / §7.5, §11 критерий 7)

`POST /webhook/ping` переиспользует as-built контракт
`docs/external-api.md`, а не изобретает новый:

- `X-Prodavan-Signature: sha256=<hmac>`, где `sha256 = HMAC(secret, raw_body)`;
  секрет — `PLUGIN_WEBHOOK_SECRET` из Secret, показывается один раз, в БД не
  хранится. Сверка через `hmac.compare_digest`.
- Обязательные `X-Prodavan-Event`, `X-Prodavan-Delivery`. **Без подписи или с
  неверной → 401.** Повтор `X-Prodavan-Delivery` → 200 без повторной обработки
  (идемпотентность; в проде — outbox `webhook_deliveries` +
  `FOR UPDATE SKIP LOCKED` + lease, защита от replay по времени).
- Ретраи 1м/5м/30м/2ч → `exhausted`, rate limit per `(plugin, company)`.

## 6. Frontend (задача 13 / §5.2, §5.3)

- Хост задачи 13 создаёт `<iframe sandbox="allow-scripts" ...>` **БЕЗ
  `allow-same-origin`** на отдельном static-origin, `referrerpolicy="no-referrer"`,
  `credentialless`. Плагин не может снять собственный sandbox.
- `plugin-rpc/1` handshake: `host.ready` → `plugin.ready` (эхо `plugin_id`,
  `version`, `digest`, `nonce`) → `grant.ready`.
- **Каждое** входящее сообщение проверяет `event.origin` и точный
  `WindowProxy` (`event.source === window.parent`); `origin === "null"` и
  неизвестный origin отбрасываются fail-closed.
- Кнопка «Поздороваться» → `plugin.rpc.request` (envelope `kind: "request"`)
  → хост проксирует в `/pluginpage/hello_plugin/{company}/api/greetings`.
  **Нет `?token=` в URL**, нет `allow-same-origin`, нет прямых вызовов shared
  API (`connect-src 'none'` в CSP), capability-токен во frontend не попадает.

## 7. Что намеренно отсутствует

| Нет | Почему |
|---|---|
| `:8001` pod-gateway | agent-surface (`/agent/*`, `/internal/pods/*`), плагин company-scoped (§4.1, критерий 17) |
| `artifact_digest` / `signature` в манифесте | самореференция, вычисляются платформой из OCI/cosign (§5.1) |
| `egress` / `allowed_hosts` в манифесте | только `egress_ref: grant:hello_plugin`, правила из `CapabilityGrant` в БД, не из JWT (§6.1) |
| `module_instance_data_rows`, jsonb | плагин владеет typed-схемой (§0, §2) |
| Версия в имени схемы/таблиц | §3.3 |
| Бэкапы/перенос/dual-read/snapshot | dev, данные эфемерны (§8) |
| `migrate-to-plugin` для `mod_equipment` | strangler позже, по одному контексту за флагом (§8) |

## 8. Acceptance (§11), покрываемые пилотом

| # | Критерий | Где |
|---|---|---|
| 3 | Плагин сам применяет changelog; повтор не меняет схему | `entrypoint.sh`, `changelog.xml`, `test_changelog.py` |
| 4 | `/health` не Ready при ошибке миграции | `app.py`, `test_health.py` |
| 7 | Webhook без/с неверной подписью → 401; повтор delivery не дублирует | `app.py`, `test_webhook.py` |
| 16 | Фича-флаг `plugin_enabled_modules[]` переключает путь без миграции данных | §8, README (странглер), `test_manifest.py` |
| 17 | Нет доступа к agent-surface `:8001` | `app.py`, `test_no_pod_gateway.py` |
| 18 | Pin по digest, запрет `:latest` | §5.1, `test_manifest.py` |

> Тесты в `apps/api/tests/unit/plugins/pilot/` **написаны, но НЕ запускались**
> (verification freeze, §9 плана). Сборка образа, `liquibase`, `psql`,
> `kubectl`, `git`, `alembic`, линт и компиляция также не запускались.
