# 00 — Обзор миграции

## Проблема (почему уходим)

Текущий рантайм (as-built, см. reports/backend-report.md, devops-report.md):

| Симптом | Причина в коде |
|---|---|
| Холодный старт минуту+ | `imagePullPolicy: Always` + `:latest` на образе 4–6 ГБ (`pod_spec.py`) → каждый старт тянет весь образ; emptyDir + полный tar-hydrate workspace поверх |
| Ошибки hydrate | initContainer в Pod-спеке: 401 при устаревшем bridge-gen (пересоздание по лейблу — костыль), 512 MiB in-memory tar, WSL-флейпы |
| Pause убивает контекст | pause = delete pod (grace 30) + rehydrate — сессия агента деградирует, `conversation_rehydrate`-симуляция |
| Латентность сообщений | `_resolve_pod_ip` на каждое сообщение (K8sSandboxClient get_pod) |
| Ненадёжность WSL-ноды | cluster-heal CronJob с kubectl-образом как «лекарство» |
| Приостановка/ресурсы примитивны | желаемое = только RUNNING/ABSENT, resources фиксированы в ConfigMap, контроля нет |
| Масштабирование контейнеров не проверялось | вообще не реализовано (нет HPA/warm pool) |

Корневая причина одна: **API-приложение — самописный k8s-controller**, управляющий подами через httpx REST на глазах. Это антипаттерн, который agent-sandbox решает вендорным контроллером с warm pool, suspend/resume и метриками.

## Цель

Полная миграция backend+devops+frontend на **kubernetes-sigs/agent-sandbox v1.0.2** (v1beta1 API) с сохранением продуктовых возможностей:

- UI-управление жизненным циклом проекта → Sandbox (launch/pause/resume/reload/complete/delete)
- Чат с агентом (SSE через API-прокси) — без изменений протокола для фронтенда
- Workspace файлов агента — персистентный, выживающий pause
- Доступ пода к платформенным данным через `:8001` с идентификацией (Bridge JWT)
- Ограничение ресурсов per-company (профили квот)
- Старт в секунды (warm pool), масштабирование пула по спросу

## Границы (что НЕ делаем)

- Не переписываем чат-протокол агента (SSE-события, транскрипт в PG) — он ортогонален рантайму
- Не меняем auth-модель пользователей (Keycloak/OIDC)
- Не меняем platform-сервисы (Redis/MinIO/Mongo/OpenSearch/Redpanda/Keycloak/SearxNG)
- Не внедряем gVisor/Kata (RuntimeClass) — отдельная итерация
- Не мигрируем MCP/PVC-probe Jobs (`SANDBOX_K8S_JOBS`) — остаются как есть в первой волне, удаление потом
- Multicluster, HA-кластер — вне scope

## Успех-критерии (приёмка)

| ID | Критерий | Метрика |
|---|---|---|
| S1 | Launch (warm) | ≤ 5 с от POST /launch до `observed_state=running` |
| S2 | Resume из paused | ≤ 10 с (suspend/resume, PVC на месте) |
| S3 | Отправка сообщения | без K8s-API вызова на пути запроса (только router) |
| S4 | Pause не теряет FS | файлы, созданные агентом до pause, доступны после resume без hydrate |
| S5 | Утечек подов нет | controller сам reconcil'ит; zombie-reaper в API удалён |
| S6 | Pull-ошибки | imagePullPolicy IfNotPresent + версионные теги; pull только при смене образа |
| S7 | Фронт совместим | без ломающих контрактов `observed_state`, SSE-протокола (см. 05) |
| S8 | Масштабирование | пул расширяется под нагрузкой HPA, scale-to-zero KEDA — опционально |

## Архитектура «до → после» (одна картинка)

```text
ДО:                                  ПОСЛЕ:
Flutter → API:8000                   Flutter → API:8000
             │                                  │
      PodCommand.sync_desired         PodCommand.sync_desired (контракт тот же)
             │                                  │
      K8sPodRuntimeAdapter            AgentSandboxRuntimeAdapter (новый)
      httpx → K8s API                 SDK k8s-agent-sandbox → SandboxClaim CR
             │                                  │
      Pod (pod_spec.py,                agent-sandbox-controller (вендор)
      emptyDir, initContainer                    │ Sandbox CR
      hydrate, env=JWT)                          ▼
             │                          Pod из SandboxTemplate + PVC workspace
      openclaw_bridge                           │
      resolve pod_ip → :3921          sandbox-router → :3921 (X-Sandbox-ID)
             │                                  │
      pause = delete pod              pause = operatingMode Suspended
      + dehydrate tar                 (PVC живёт; dehydrate = snapshot в MinIO)
```

## Источники (as-built анализы)

- `reports/backend-report.md` — реверс pod_service/identity/agent/hydrate
- `reports/devops-report.md` — k3s/Argo/RBAC/NetworkPolicy/образы
- `reports/frontend-report.md` — Flutter-контракты
- `reports/agentsandbox-report.md` — контракт agent-sandbox CRD/SDK/router
- Легаси-канон: `docs/target/14-project-containers/` (справка, не блокер)
