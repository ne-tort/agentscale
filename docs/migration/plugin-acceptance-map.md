# Acceptance-карта миграции plugin-модулей (v4)

**Источник:** [`docs/ARCHITECTURE/plugin-architecture-complete.md`](../ARCHITECTURE/plugin-architecture-complete.md) §11.  
**Правило:** тесты пишутся вместе с кодом, но НЕ запускаются до явного решения о верификации. Родитель ведёт эту карту; субагент отмечает готовность в своём отчёте, родитель — здесь.

Легенда: `[ ]` не начато · `[~]` код написан, тесты есть, не проверено · `[x]` подтверждено.

## F0 — Контракты

| # | Критерий | Владелец | Состояние |
|---|---|---|---|
| 1 | Роль плагина владеет **своей** схемой, но `NOCREATEDB NOCREATEROLE NOSUPERUSER` | задача 05 | [~] |
| 2 | Роль не может подключиться к `prodavan` или чужой `tenant_*` БД | задача 05 + 14 | [~] |
| 3 | Плагин сам применяет Liquibase changelog; повторный запуск не меняет схему | задача 05 (+ пилот F10) | [ ] |
| 4 | `/health` не становится Ready при ошибке миграции / checksum mismatch | задача 14 | [~] |
| 5 | `pgaudit` содержит DDL плагина | задача 14 | [~] |

## F1 — Identity

| # | Критерий | Владелец | Состояние |
|---|---|---|---|
| 6 | Анонимный `/pluginpage/{plugin}/{company}/api/*` без сессии → `401` | задача 11 | [~] |
| 7 | Webhook без/с неверной `X-Prodavan-Signature` → `401`; повтор `X-Prodavan-Delivery` не дублирует обработку | задача 11 | [~] |
| 8 | Egress на хост вне `allowed_hosts` → `403` + событие аудита | задача 08 | [~] |
| 9 | `/webhook/` и `/pluginpage/` не уезжают в SPA | задачи 14, 15 | [~] |
| 10 | Превышение `ops_per_minute` → `429` + `QUOTA_EXCEEDED` | задача 03 | [~] |
| 11 | Audit-событие не содержит raw SQL, параметров, токенов, payload | задача 03 | [~] |
| 12 | `revoke` деактивирует `CapabilityGrant`; старый `PluginClaims` отклоняется по `grant_id` | задачи 02, 12 | [~] |

## F2 — Capability / HTTP

| # | Критерий | Владелец | Состояние |
|---|---|---|---|
| 17 | Плагин с `PluginClaims` не имеет доступа к agent-surface `:8001` | задачи 02, 04 | [~] |
| 21 | Один плагин не может исчерпать квоту компании (per-instance/digest) | задача 03 | [~] |
| 22 | Egress-политика не зависит от срока жизни JWT | задача 08 | [~] |

## F3 — Данные и объекты

| # | Критерий | Владелец | Состояние |
|---|---|---|---|
| 15 | Uninstall дропает схему/роль и bucket/prefix, не трогая чужие БД | задачи 05, 06, 12 | [~] |
| 19 | STS creds плагина не видят объекты чужого bucket/префикса | задача 06 | [~] |

## F4 — Артефакты

| # | Критерий | Владелец | Состояние |
|---|---|---|---|
| 18 | `cosign verify` до деплоя; неподписанный digest → `QUARANTINED`, деплой по digest, не `:latest` | задачи 07, 16 | [~] |

## F7/F8 — События и засыпание

| # | Критерий | Владелец | Состояние |
|---|---|---|---|
| 13 | Wake-up из `replicas: 0` удерживает запрос до Ready и выдаёт новый короткий токен | задачи 09, 11, 15 | [~] |
| 14 | Активный `job_id` блокирует усыпление backend | задача 09 + родитель (`idle_pause.py`) | [~] |
| 16 | Фича-флаг `plugin_enabled_modules[]` переключает путь без миграции данных | задача 12 | [~] |
| 20 | Redpanda consumer не теряет событие при простое (ручной commit, `earliest`, DLQ) | задача 10 | [~] |

---

## Родительская интеграция (Fa)

| # | Что сделать | Файлы | Состояние |
|---|---|---|---|
| A1 | Зарегистрировать `/capabilities/v1` и `/admin/plugins`, `/webhook`, `/pluginpage` роутеры | `api/v1/router.py` | [ ] |
| A2 | Подключить plugin-ветку allowlist в middleware | `core/middleware.py` | [ ] |
| A3 | Настройки + guard'ы | `config/settings.py` | [x] |
| A4 | Env в ConfigMap (секреты — отдельным Secret) | `infra/k3s/base/agentscale-api/configmap.yaml` | [x] |
| A5 | Включить provenance/sbom в CI платформенных образов | `.github/workflows/ci-images.yml` | [ ] |
| A6 | kustomization: подключить `base/plugins` и `overlays/dev/plugins` | `infra/k3s/base/kustomization.yaml`, `overlays/dev/kustomization.yaml` | [ ] |
| A7 | Реестр активных `job_id` в `ProjectIdlePauseService` | `application/projects/idle_pause.py` | [ ] |
| A8 | Зарегистрировать i18n-юнит `plugins` в каталоге | `apps/web/src/i18n/catalogue.ts` | [ ] |
| A9 | Добавить маршрут pluginpage в роутер web | `apps/web/src/app/router.tsx` | [ ] |
| A10 | Модели БД для `CapabilityGrant`/`PluginInstance` + Alembic-ревизия | `infrastructure/persistence/models/**`, `alembic/versions/**` | [ ] |
| A11 | Общий коммит и Verify Dev | — | [ ] |

## Известные архитектурные долги (не блокеры F0–F9)

1. **Проекция `AppVersion`:** плагинный манифест `plugin-rpc/1` не проходит legacy
   `validate_manifest` (`protocol == "1.0"`). Реестр хранит нормализованную обёртку +
   исходный подписанный manifest под `plugin_manifest`. `AppVersion` НЕ ослаблять.
2. **MinIO STS** — требуется подтверждение официальной документацией
   `AssumeRole` / policy variables; fallback — scoped presigned с явным префиксом.
3. **Keyless cosign на self-hosted runners** — проверить OIDC `id-token: write` и
   приватный ghcr; fallback — cosign с KMS-ключом и зафиксированным trust root.
4. **Wildcard subdomain** — отложен до публичного домена + cert-manager DNS-01
   (in-tree солвера для reg.ru нет; делегирование `_acme-challenge` на Cloudflare/acme-dns).
5. **KEDA HTTP Add-on** — целевой вариант после снятия ограничений whitelist kinds
   и бюджета ноды; в v4 собственный activator.

---

**Обновляется родителем после каждого отчёта субагента.**
