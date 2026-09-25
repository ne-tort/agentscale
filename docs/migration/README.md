# Migration → kubernetes-sigs/agent-sandbox

Каноническая документация миграции **agentscale** (fork prodavan) с самописного
k8s-под-рантайма на стек **kubernetes-sigs/agent-sandbox** (v1.0.4).

> Заменяет `INTEGRATION.md` (поверхностный анализ) и частично `docs/target/14-project-containers/k3s-runtime/` (as-built старого рантайма). Прежние доки — исторический контекст, план — здесь.

## Индекс модулей

| # | Модуль | Содержание |
|---|--------|-----------|
| 00 | [overview](00-overview.md) | Цели, принципы, границы, итоговая архитектура в 1 диаграмме |
| 01 | [target-architecture](01-target-architecture.md) | Целевая архитектура: компоненты, маппинг концепций prodavan→agent-sandbox, потоки данных |
| 02 | [identity-port8001](02-identity-port8001.md) | **Критический модуль**: аутентификация пода к API `:8001` в новой архитектуре (Bridge JWT, post-Ready delivery) |
| 03 | [devops](03-devops.md) | Миграция инфраструктуры: controller, SandboxTemplate, WarmPool, RBAC, NetworkPolicy, Argo, образы |
| 04 | [backend](04-backend.md) | Миграция FastAPI: SDK-клиент, переписывание pod_service, hydrate/dehydrate, agent-bridge, БД |
| 05 | [frontend](05-frontend.md) | Миграция Flutter: контейнерные статусы, wake-флоу, чат, метрики |
| 06 | [scaling-suspend](06-scaling-suspend.md) | Suspend/Resume (замена pause=delete), idle-sweeper, HPA/KEDA масштабирование, ресурсы |
| 07 | [rollout](07-rollout.md) | План реализации: фазы, PR-разрезы, порядок, фиче-флаги, откат |
| 08 | [audit](08-audit.md) | Аудит согласованности фронт-бек-девопс, чек-листы приёмки, known-gaps |

## Сводка решений (TL;DR)

1. **Поды агента** создаются не кодом API, а `SandboxWarmPool` → `SandboxClaim` → Sandbox CR (контроллер agent-sandbox, ns `prodavan-sandboxes`). API пишет claim'ы через Python SDK `k8s-agent-sandbox`.
2. **Pause** больше не delete pod: `spec.operatingMode: Suspended` — Pod удаляется, **PVC workspace и Service выживают**, resume = секунды.
3. **Workspace**: emptyDir + tar-hydrate через initContainer → **PVC** (`volumeClaimTemplates` в SandboxTemplate) + разовый пост-Ready перенос данных через files-API SDK. Dehydrate в MinIO остаётся как backup/snapshot, не как обязательный механизм каждого pause.
4. **`:8001` (pod-only surface) остаётся**, идентификация — Bridge JWT (HS256 + generation в Redis) сохраняется как контракт, но **доставка токена меняется**: не env в Pod-спеке (env в claim форсирует cold start мимо warm-пула), а **post-Ready push** от API к agent-runtime `:3921` по внутреннему каналу (NetworkPolicy-защищённому).
5. **IP-резолв пода при каждом сообщении** заменяется на **sandbox-router** (`X-Sandbox-ID`) — стабильный адрес сандбокса, кэш Pod-IP на стороне роутера.
6. **Масштабирование**: HPA (потом KEDA) на `SandboxWarmPool` по метрике `agent_sandbox_claim_creation_total`; idle-sweeper патчит `operatingMode`.
7. **Легаси под нож**: самописный httpx k8s-клиент, `pod_spec.py`, zombie-reaper, cluster-heal CronJob, probe-Job RBAC, `imagePullPolicy: Always` + `:latest`.
