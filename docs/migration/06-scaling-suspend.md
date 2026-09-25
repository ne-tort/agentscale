# 06 — Suspend/Resume, ресурсы, масштабирование

## 6.1 Suspend/Resume = новая пауза

| Операция | Раньше | Теперь |
|---|---|---|
| Pause | delete pod (grace 30) + dehydrate tar в MinIO | patch `operatingMode: Suspended`; Pod убит, **PVC+Service живы**; CPU/RAM освобождены |
| Resume | create pod + initContainer hydrate (tar 512 MiB in-memory) + bridge bootstrap | patch `operatingMode: Running` → Pod recreated, PVC attach → Ready; bridge-refresh (02) |
| Стоимость paused | MinIO blobs (дёшево), 0 compute | PVC на диске (local-path), 0 compute |
| Контекст агента | терялся (FS пересобирался из tar) | **выживает целиком** (FS на PVC) — `.openclaw-data`, node_modules и пр. |

- `_prepare_reload` (смена образа): terminate (каскад PVC) + create + seed из MinIO-snapshot — см. 04.3.
- `ProjectCommand.pause/resume` бизнес-логика (status, AI-key gate, идемпотентность) — unchanged.
- `conversation_rehydrate` (prompt-based симуляция) — **удалить**: сессии реактивируются `pod_session_bootstrap`, транскрипт в PG, FS жив. Проверить: старые сессии с hydrate_generation-реликтом — миграционная чистка (07 Phase 7).

## 6.2 Idle-pause (existing → patch operatingMode)

`ProjectIdlePauseService.sweep` остаётся, только `ProjectCommand.pause(reason="idle_pause")` теперь → suspend. Результат: ночные idle-агенты стоят ~0 CPU, PVC дешёвый. Дополнительно (опция, Phase 6):
- TTL-страховка `shutdown_after_seconds` на claim (env `POD_SANDBOX_SHUTDOWN_TTL_SEC`, дефолт 7d) — защита от утечек abandoned-подов, независимо от sweeper'а.
- При suspend ≥ N дней → «expire to MinIO»: terminate + снапшот (компактное хранение неактивных). Порог — per-company policy (idle_pause_after_hours уже есть; добавить `idle_expire_days`).

## 6.3 Ресурсы

- **Базовые** — в SandboxTemplate (100m/256Mi → 1/1Gi как сегодня).
- **Per-company квоты**: env-инъекция невозможна (Disallowed). Варианты:
  a) **Несколько шаблонов** `prodavan-agent-{small,standard,large}` + отдельные warm-pool'ы; adapter выбирает warmpool по company-профилю. Warm=секунды сохраняется (у каждого пула свои поды). ✅ рекомендовано (Phase 6).
  b) Dynamic patch контейнера live-пода — нет (спека immutable после создания в смысле релевантном).
- LimitRange/ResourceQuota на ns `prodavan-sandboxes` — добавить (guard от runaway): Quota pods/PVC-count на проект × компаний; LimitRange по умолчанию.
- `PodMetricsSampler` остаётся (metrics.k8s.io) — метрики живы без изменений для UI.

## 6.4 Масштабирование WarmPool

- **HPA** (Phase 6): target `SandboxWarmPool/prodavan-agent-pool`, External-метрика `agent_sandbox_claim_creation_total` (controller metrics :8443/metrics → ServiceMonitor/prometheus). k3s: свой Prometheus не развёрнут — **простой вариант dev: cron/ops-CLI** `prodavan-ops pool-autoscale` (считает rate за 15 мин, патчит replicas) в CronJob ns prodavan. HPA с external-metrics требует adapter — отложить до появления Prometheus.
- **KEDA scale-to-zero**: minReplicaCount 0 + activationThreshold — после Prometheus (dev-пул и так replicas: 2).
- Ручное: `kubectl scale swp/prodavan-agent-pool --replicas=N` или ops-CLI.
- Пул < спроса → claim ждёт replenish (latency вырастает до cold-create) — метрика `agent_sandbox_creation_latency_ms` (launch_type=warm/cold) — алерт в ops-CLI.

## 6.5 Наблюдаемость

- Controller metrics (см. agentsandbox-report §9) скрейпим (аннотации prometheus как у api; dev — ручной ops-CLI).
- Router: `sandbox_router_requests_total/duration/upstream_errors/authz_decisions`.
- Свои: `prodavan_pod_sandbox_adoptions_total`, `prodavan_pod_suspend_resume_latency` (App-уровень) — через существующий metrics-consumer.
- Алерты (ops validate): pool readyReplicas==0 при claim-очереди; creation_latency p95 > 10s; Suspended-агенты > порога диска (PVC sum).

## 6.6 Стоимость dev-кластера

- Warm pool 2 пода: 200m/512Mi постоянно + 2 PVC 5Gi + образ 4–6 ГБ на ноде (один слой, IfNotPresent).
- Suspend-агенты: только PVC. Idle-экономия реальна уже при ~5 приостановленных проектах (раньше — RAM-под каждый idle не держали, но reload-стоимость была высокой; теперь resume дешёвый).
