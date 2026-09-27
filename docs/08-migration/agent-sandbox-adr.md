# ADR: миграция Prodavan на kubernetes-sigs/agent-sandbox

> Каноничная копия ADR в репозитории (источник: рабочая область agentscale, MIGRATION-DECISIONS.md).
> Все ссылки «Canon: docs/migration/*» в манифестах ведут сюда.

Статус: принято (координатор миграции, 2026-09-26). Заменяет INTEGRATION.md как рабочий план
(INTEGRATION.md сохранён как исследование; его положения проверены по исходникам agent-sandbox
и подтверждены, но ниже зафиксированы исправления и решения, которых там нет).

## 0. Диагноз as-is

- Миграция НЕ начата в коде: SDK `k8s-agent-sandbox` не в зависимостях apps/api,
  манифестов SandboxTemplate/SandboxWarmPool/SandboxClaim/RBAC нет, бекенд работает
  через самописный httpx REST-клиент (infrastructure/k8s/sandbox/client.py) и
  генерацию Pod-spec в коде (pod_spec.py).
- Все артефакты предыдущего агента — исследования (reports/, INTEGRATION.md), не канон.
  Проверено выборочно по исходникам: факты (contract, SDK, роутер) подтверждены.

## 1. Целевая архитектура (решения)

1. **Controller**: agent-sandbox core+extensions, ns `agent-sandbox-system` (Argo-приложение
   из вендоренных манифестов v1.0.2, НЕ из интернета в рантайме).
2. **Роутер**: sandbox-router (2 реплики), `--cache-enabled` (обязателен для warm-pool
   без per-sandbox DNS), `--authz-mode=allow-all` на dev (internal-only), tokenreview/scoped-token —
   отдельной итерацией. Роутер НЕ выставляется наружу (нет Ingress-роута).
3. **Namespace**: `prodavan-sandboxes` остаётся доменом сандбоксов; Template/WarmPool/Claims там же.
4. **Template** `prodavan-agent`: контейнер agent-runtime :3921, probes /health,
   runAsUser 1000, `imagePullPolicy: IfNotPresent` + версионированный тег (НЕ latest: 97MB-образ),
   `service: true` (стабильный FQDN), `envVarsInjectionPolicy: Disallowed`,
   `volumeClaimTemplatesPolicy: Disallowed`, PVC `workspace` RWO в volumeClaimTemplates
   (замена emptyDir — переживёт suspend/рестарт ноды).
   Платформенные env (PORT, API base URL, web search) — в шаблоне (статичны);
   **per-project данные — никогда через env** (env в claim = cold start).
5. **WarmPool** `prodavan-agent-pool`: replicas 2 (dev), `updateStrategy: Recreate`.
6. **Управление жизненным циклом из apps/api**: `k8s-agent-sandbox[async]` SDK
   (kubernetes_asyncio, in-cluster SA `prodavan-api`):
   - RUNNING → create claim из пула (+ labels `prodavan.io/project-id`, pod-labels через
     additionalPodMetadata с allowlist-доменом `prodavan.io`), watch-based Ready;
   - PAUSED → patch `spec.operatingMode: Suspended` (НЕ delete pod);
   - ABSENT → delete claim (каскадно сносит sandbox+PVC);
   - TTL-страховка: `shutdown_after_seconds` = 7d.
7. **Трафик к агенту**: ТОЛЬКО через sandbox-router с заголовками
   `X-Sandbox-ID/X-Sandbox-Namespace/X-Sandbox-Port: 3921`.
   Прямой pod-IP резолв (`_resolve_pod_ip`) и `X-Sandbox-Pod-IP` — удалить.
   SSE-стрим (`/v1/sessions/{id}/send`) — через роутер (WebSocket/SSE проксируется).
8. **Identity**: Bridge JWT минтится в API как сейчас, но доставляется в агент
   post-Ready через существующий `POST /v1/sessions` + `PATCH /v1/sessions/{id}`
   (adapter_state) — НЕ через env пода. Утечка токена в `kubectl describe` закрыта.
   Ключевое упрощение: agent-runtime перерегистрирует сессии после resume сам
   (сессии выживают на PVC).
9. **Workspace**: PVC — SoT. Legacy dehydrate/hydrate (tar→MinIO) остаётся только как:
   (a) разовая миграция существующих воркспейсов; (b) экспорт/бэкап по кнопке.
   initContainer hydrate — удалить. hydrate из MinIO в PVC — отдельный пост-Ready шаг
   (через SDK commands/files или /v1/sessions bootstrap).
10. **Статусы для фронта (новый контракт)**: маппинг conditions:
    - claim/sandbox Ready=True → `running`;
    - Ready=False, reason=SandboxSuspended → `suspended`;
    - Suspended=True, reason=PodTerminating → `pausing`;
    - PodScheduled=False → `provisioning`; NotFound → `absent`;
    - ReconcilerError/InvalidConfiguration → `failed`.
    Поля `runtime`: `sandbox_name`, `claim_name`, `service_fqdn`, `launch_type` (warm|cold),
    `ready`, `suspended`, `pod_ip` (deprecated), `restarts`. `stub`/`object-ws:` — удалить.
11. **Idle-pause**: существующий sweeper → patch Suspended (секунды вместо минут, PVC живёт).
12. **Zombie-reaper/reconcile**: удалить `_reap_zombies` (контроллер делает сам);
    reconcile сводится к желаемому состоянию в PG + sync_desired.

## 2. Найденные дыры в плане INTEGRATION.md (исправления)

1. **Router NetworkPolicy egress порт 8888 → наш агент 3921.** Upstream-манифест
   пропускает egress только на 8888. Обязательно патчить deploy на 3921
   (или слушать 8888). Иначе роутер получит 502 на каждый dial.
2. **Router ingress `from: namespaceSelector: {}`** — ограничить ns `prodavan`.
3. **Сандбокс-ингресс**: NetworkPolicy сандбоксов должна пускать 3921 только из ns
   `agent-sandbox-system` (было: из ns prodavan напрямую).
4. **Egress сандбокса к API**: как было, к :8001 в ns prodavan — сохраняется
   (Bridge JWT на /v1/sessions и internal-pods остаются).
5. **Allowlist доменов лейблов**: контроллер по умолчанию допускает только
   `sandbox.users.io` — нужен ConfigMap `agent-sandbox-config` (key
   `allowed-label-domains` = `prodavan.io`) в ns controller, иначе
   additionalPodMetadata с `prodavan.io/*` будет отвергнут.
6. **claimedPods RBAC**: SA prodavan-api нужен watch на claims И sandboxes
   (suspend = patch sandbox, не claim).
7. **Длина имени**: Sandbox имя ≤63 для Service DNS — claim имена генерит SDK
   (`sandbox-claim-<hex8>`) — ок.
8. **`:latest` в образе агента**: тянуть 97MB на каждый warm-спар при Always —
   недопустимо; тег = git-sha, IfNotPresent, Recreate страйт strategy в пуле.

## 3. Разделение работы (субагенты, изоляция по файлам)

- **devops**: `prodavan/infra/**` — установка controller+router (Argo app + патчи),
  Template/WarmPool, RBAC API-SA, NetworkPolicy-патчи, AppProject whitelist,
  cluster-heal CronJob — удалить.
- **backend**: `prodavan/apps/api/**` — SDK-зависимость, адаптер `adapters/agent_sandbox/`,
  factory/settings (POD_RUNTIME_MODE=agent_sandbox), openclaw_bridge через роутер,
  suspend/resume в PodCommand, удаление zombie-reaper/hydrate-init, статусный маппинг.
- **frontend**: `prodavan/apps/flutter/lib/**` — ContainerRuntime-модель, новые статусы,
  poll-терминальность, wake-UX (suspend→resume секунды), l10n.
- docs/ не трогают субагенты — обновляет координатор.

## 4. Принципы качества (незыблемые)

- Низкая задержка: warm-claim ~2s; suspend/resume — секунды; никаких Always-pull.
- Надёжность: TTL-страховка на sandbox, идемпотентные операции, Redis-gen откупа
  сохраняется для bridge-токенов.
- Прозрачность: статусы = conditions CR (kubectl wait работает), метрики контроллера/роутера.
- Тестируемость: stub-режим сохраняется; новый адаптер под SDK-моками; e2e-прогон.
