# Состояние миграции plugin-платформы v4 (main, 2026-11-08)

## Реализовано

Полная замена declarative-app на plugin-приложения по дизайну
[`docs/ARCHITECTURE/plugin-architecture-complete.md`](../ARCHITECTURE/plugin-architecture-complete.md) v4:
отдельный trust-контур `PluginClaims`, capability-gateway `/capabilities/v1/*`,
прямой DSN в `tenant_<company>`, OCI/cosign-артефакты, MinIO scoped presigned,
две NetworkPolicy из одного генератора, Redpanda dispatch, activator 0→1,
Plugin Gateway `/webhook/*` + `/pluginpage/*`, iframe `plugin-rpc/1`, фича-флаг
strangler, 6 таблиц состояния платформы (ревизия `2026110801`).

Работа велась на `main` микрокоммитами, без feature-веток и PR (явное правило
пользователя для этой миграции), через субагентов.

## Верификация (локально, зелёная)

| Контур | Команда | Результат |
|---|---|---|
| API | `pytest tests/acceptance tests/unit -q` | **4198 passed**, 0 failed |
| Web | `pnpm exec vitest run` | **785 passed**, 0 errors |
| Web | `pnpm exec tsc --noEmit` | 0 ошибок |
| Web | `pnpm run build` | success |
| infra/ops | `pytest infra/ops/tests -q` | **104 passed**, 0 failed |
| kustomize | `kubectl kustomize overlays/dev` + `overlays/dev/plugins` | оба собираются |
| YAML | все `.github/workflows/*.yml` + `infra/**/*.yaml` | парсятся |
| Alembic | чистый `postgres:17-alpine`, `upgrade head` + `check` | `2026110321 → 2026110801`, 6 таблиц, drift отсутствует |
| Импорты | `prodavan.main`, `main_plugin_gateway`, все зоны plugins | ok |
| Композиция | `create_app()` обоих приложений на реальном Postgres с `PLUGIN_PLATFORM_ENABLED=true` | **18/18 `STATE_KEYS`**, `PluginPlatform`, `PluginGatewayRuntime`, `PluginAdminPorts`, `PluginCapabilityService` привязаны; 10 `_UnwiredPort` зафиксированы как долг F11 |
| HTTP smoke | TestClient по обоим apps | `/health/live` `/health/ready` → 200; `/capabilities/v1/health`, `/webhook/*`, `/api/v1/admin/plugins/` → 401/400/503 (ожидаемые fail-closed), **ни одного 500** |

## Dev-кластер (последнее подтверждённое состояние до потери VM)

| Ресурс | Состояние |
|---|---|
| `agentscale-api` | 2/2 Running, `/health/*` 200 |
| `plugin-gateway` | **2/2 Running**, `/health/live` + `/health/ready` 200 |
| `tenant-postgres` | 1/1 Running (образ `plugin-base-postgres`) |
| `pgbouncer` | 1/1 Running |
| `agentscale-dev` (Argo) | Synced / Healthy |
| `agentscale-plugin.events` + `.dlq` | созданы, RF=1 |
| `plugin-buckets-init` / `tenant-postgres-bootstrap` | Completed |

Последние исправления кластера (все запушены, но **не подтверждены live** из-за
потери доступа к VM):

1. `egress-proxy` readiness :9901 — admin был `127.0.0.1`, kubelet стучится на
   PodIP. Исправлено на `0.0.0.0` во всех 4 источниках (ConfigMap, генератор,
   контроллер, эталон образа) + аннотация `egress-config-revision: "2"` для
   rollout. Тест `test_egress_proxy_admin.py` / `test_admin_bind.py` пинят это.
2. `Ingress plugins-gateway` — был в `sync-wave: "25"`, из-за чего Argo
   `plugin-platform` оставался `Missing` (Ingress нет в live → поздняя волна не
   применяется → Ingress не создаётся). Переведён в `wave 1` одной волной со
   всеми ресурсами оверлея. Приоритет 120 задаётся аннотацией Traefik, не волной.
   **Дополнительный корень (найден позже):** в `plugin-platform.yaml` стоял
   `ignoreDifferences /data` для `egress-proxy-config`, хотя контроллер
   синхронизации НЕ задеплоен как Pod. Арго поэтому вообще не применял правку
   конфига из Git. Запись убрана; регрессионный тест
   `test_argo_applies_egress_configmap_data` это пинит.
3. `plugin-topics-init` — `rpk topic list` печатает таблицу, проверка через
   `awk` по первому столбцу вместо `grep -qx`.
4. `plugin-gateway` — ленивый `RedisManager` (lifespan ещё не стартовал в
   `create_app`), `PLUGIN_RUNTIME_NAMESPACE`, `_admin_ports(services_out)`,
   fail-closed `DeploymentAdminPort`, `imagePullPolicy: Always` + ревизия 5.

## Внешний блокер (НЕ код)

С ~21:00 VM `www@172.31.156.203` перестала отвечать:
`ping`, SSH :22 и k3s :6443 — timeout. К ~22:10 порты 22/6443/8088 снова
открылись (VM частично поднялась), но SSH-демон не отдаёт баннер, k3s API
на `/version` рвёт TLS/EOF, web :8088 не отвечает по таймауту. То есть хост
загружается/нестабилен, кластерные сервисы ещё не восстановились.
Само-hosted раннеры на той же VM поэтому
не могут ни установить `poetry`/`hatchling` (PyPI), ни подключиться к кластеру,
ни отправить результаты в `results-receiver.actions.githubusercontent.com`.
`Verify Dev` падает на шаге `wait-smoke` с сетевыми ошибками — **это не регрессия
кода**: все локальные наборы зелёные, CI Images / CI Schemas / CI Web в последних
успешных прогонах были success.

Что сделать при восстановлении VM (без правки кода):
1. `kubectl get pods -n agentscale-dev` — убедиться, что `egress-proxy` стал
   1/1 после rollout revision 2.
2. `kubectl get ingress -n agentscale-dev` — убедиться, что `plugins-gateway`
   появился (wave 1).
3. `kubectl get applications -n argocd` — `plugin-platform` должен стать
   Synced / Healthy.
4. Повторить `Verify Dev` (workflow_dispatch) — ожидаемо зелёный.

## Известные долги F11 (не блокируют компиляцию/тесты)

- `DeploymentAdminPort` — fail-closed заглушка: grant/uninstall без реального
  k8s create/purge честно отказывают. До первого рабочего плагина нужен адаптер.
- `RouteRegistry` / `InstanceIdResolver` — in-memory; для кластера нужны БД/Redis.
- `cosign attest` в CI добавлен, но keyless OIDC на self-hosted раннере требует
  проверки Fulcio/Rekor; fallback — фиксированный KMS trust root.
- Образы `plugin-base-*` в манифестах пока `:latest`; после стабилизации
  перепинить на `@sha256:<digest>` из `$GITHUB_STEP_SUMMARY`.
- `hello.greetings` capability, `PLUGIN_WEBHOOK_SECRET` при grant, динамический
  `data-host-origin` в статике пилота.
- `GET /health` конфликт между `health_routes.router` и `plugin_gateway.router`
  решён порядком регистрации; при выделении отдельного порта — переименовать пробу.
- Финальное удаление declarative-app (`module_meta_documents`, `ModuleRuntime.tsx`,
  `EquipmentProcurementPage`, `module_*` таблицы) — strangler-карта готова,
  cutover не выполнялся (данные dev эфемерны, перенос не требуется).
