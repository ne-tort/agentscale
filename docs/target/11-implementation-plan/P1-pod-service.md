# P1 — pod_service (Project Pod runtime BC)

| Поле | Значение |
|------|----------|
| Priority | **P1** (после `project_service` BC; блокирует закрытие **P-POD-01..05**, **L07** Quality ≥ 8) |
| Canon | [14-project-containers/](../14-project-containers/) · design: [pod-service.md](../14-project-containers/pod-service.md) |
| Refactor | **Significant refactor allowed** в `project_service` runtime-слое; employee API — только совместимое deprecate |
| Status | `done` (Phases 0–4) |
| Зависит от | `project_service` BC (merged), MinIO materialize (P0 subset), Relations facade |

## Цель

Выделить **изолированный in-process BC** `application/pod_service/` — единственный writer k8s Pod и учётной записи runtime (**ProjectPod**, 1:1 Project).

| Слой | Ответственность |
|------|-----------------|
| **`project_service`** | **WHEN** — business lifecycle Project (status, ACL, AI key gate, triggers) |
| **`pod_service`** | **HOW** — provision / pause / terminate Pod, hydrate, reconcile, `pod.*` events |

Employee UI **не** получает `/pods/*` — только `POST /projects/{id}/pause|resume|delete`. Admin «Контейнеры» — read + force-kill/reconcile.

## Предусловия (as-built после PR #98/#99)

| Готово | Где |
|--------|-----|
| `ProjectCommand` / `ProjectQuery` / `ProjectAccessPolicy` | `application/project_service/` |
| `project_runtime_units` + `ProjectRuntimeManager` | transitional 0..N; **переносится** в pod_service |
| `ContainerRuntimePort` stub (`object-ws:{key}`) | `project_service/adapters/` |
| Platform events whitelist | `domain/projects/types.py` — только `project.*`, нет `pod.*` |
| Cascade pause (company/cabinet/idle) | через `ProjectCommand.pause` → `ProjectRuntimeManager` |

## Gaps, которые закрывает P1

| ID | Канон | Сейчас | Волна |
|----|-------|--------|-------|
| **P-POD-01** | k8s Pod + inert → delete Pod | object-ws stub | Phase 3 |
| **P-POD-02** | `pod_service` BC isolated | runtime в `project_service` | Phase 1 |
| **P-POD-03** | 1:1 ProjectPod | 0..N runtime units | Phase 1–2 |
| **P-POD-04** | `pod.*` lifecycle events | только `project.*` | Phase 2 |
| **P-POD-05** | Relations pod↔project bind | FK only | Phase 2 |
| **P-PRJ-03** | runtime 1:1 | runtime-units API | Phase 4 (deprecate API) |

---

## Архитектура (целевая)

```text
Employee / cascade / idle_pause
  → ProjectCommand.pause|resume|delete|complete
    → PodCommand.sync_desired(project_id, desired_state)
      → PodRuntimePort (stub → k8s)
      → PodLifecycleEmitter → pod.* (outbox + Kafka)
    → ProjectLifecycleEmitter → project.* (после pod transition)
```

```text
application/pod_service/
  __init__.py                 # PodCommand, PodQuery, PodLifecycleEmitter
  command.py                  # sync_desired, provision, attach, detach, force_kill
  query.py                    # get_for_project, runtime_summary, list_orphans
  lifecycle_emitter.py        # pod.* → PlatformEventService
  reconcile.py                # drift worker (Phase 3)
  ports/
    pod_runtime.py            # PodRuntimePort
    hydrate.py                # HydratePort (MinIO → /workspace)
  adapters/
    stub_pod_runtime.py       # object-ws (Phase 1)
    k8s_pod_runtime.py        # real Pod (Phase 3)
```

**Импорт-правило:** снаружи pod_service — только `PodCommand` / `PodQuery`. Запрещено: прямой k8s client из API handlers, `ProjectRow` writes из pod_service.

---

## Фазы и PR-разрез

Каждая фаза = **отдельный PR** → CI Gate → merge → CI Images → Verify Dev.

### Phase 0 — Design lock ✅

| Задача | Артефакт | Статус |
|--------|----------|--------|
| Границы BC, 1:1, desired state, API policy | [pod-service.md](../14-project-containers/pod-service.md) | done |
| ADR pause=delete Pod | [adr.md](../14-project-containers/adr.md) | done |
| Операционный план | этот документ | done |

**Exit:** design review; gaps P-POD-02..05 зафиксированы в [09-gap-map](../09-gap-map.md).

---

### Phase 1 — Skeleton + перенос runtime (P-POD-02, P-POD-03 partial)

**PR title:** `feat(pods): pod_service BC skeleton + sync_desired stub`

#### 1.1 Schema

| Действие | Детали |
|----------|--------|
| Alembic | `project_pods` table (см. поля в pod-service.md) |
| Migrate | `project_runtime_units` → one primary row per project → `project_pods` |
| Constraints | `UNIQUE(project_id) WHERE project_id IS NOT NULL AND status != 'terminated'` |
| Deprecate | `primary_runtime_unit_id`, `project_runtime_units` — read-only mirror до Phase 4 |

**ProjectPodRow поля (минимум Phase 1):**

```text
id              pod_* (new_runtime_unit_id → new_pod_id)
project_id      FK nullable (null = orphan pool)
workspace_key   denorm
status          pending|provisioning|running|pausing|paused|failed|terminating|terminated
desired_state   absent|running
runtime_ref     nullable
last_error      nullable text
hydrate_generation int default 0
created_at / updated_at
```

#### 1.2 Domain

| Файл | Содержание |
|------|------------|
| `domain/pods/types.py` | `PodStatus`, `PodDesiredState`, `new_pod_id()` |
| `domain/pods/__init__.py` | re-exports |

#### 1.3 pod_service package

| Method | Поведение Phase 1 |
|--------|-------------------|
| `PodCommand.sync_desired(project_id, desired, principal, reason?)` | map Project.status → desired; idempotent |
| `PodCommand.provision_for_project(project_id)` | lazy row if missing |
| `PodQuery.get_for_project(project_id)` | single pod or None |
| `PodQuery.runtime_summary(project_id)` | `{status, desired_state, runtime_ref, last_error}` |
| `StubPodRuntimeAdapter` | port from `ContainerRuntimePort` logic (`object-ws`) |

#### 1.4 project_service refactor

| Было | Станет |
|------|--------|
| `ProjectRuntimeManager` in command | inject `PodCommand`; delete manager from public API |
| `pause_all` / `ensure_running` | `_sync_pod(project, desired)` → `PodCommand.sync_desired` |
| `attach_runtime_unit` / `detach` / `list_runtime_units` | thin delegate to PodCommand **или** deprecate stub (Phase 4 remove) |
| `stop_project_runtime` (agent sessions) | остаётся в project_service **до** pause; порядок: sessions → pod |

**Порядок в `ProjectCommand.pause`:**

```text
1. stop_project_runtime (agent sessions)
2. row.status = paused
3. PodCommand.sync_desired(absent)
4. emit project.paused
```

#### 1.5 API (минимальные изменения)

| Endpoint | Phase 1 |
|----------|---------|
| `GET /projects/{id}` | добавить `runtime: PodQuery.runtime_summary()` |
| `POST .../runtime-units` | **deprecated** header `Deprecation: true`; без новых фич |
| Admin containers | без изменений (Phase 3) |

#### 1.6 Tests

| Suite | Cases |
|-------|-------|
| `test_pod_service_command.py` | sync_desired idempotent; pause→absent; resume→running (stub) |
| `test_pod_service_query.py` | get_for_project 1:1 invariant |
| `test_project_service_domain.py` | pause/resume вызывает pod sync (mock PodCommand) |
| Migration test | backfill primary unit → pod row |

#### DoD Phase 1

- [ ] `application/pod_service/` существует; ruff + mypy clean subset
- [ ] `ProjectCommand` не импортирует k8s / `ContainerRuntimePort` напрямую
- [ ] `project_pods` populated для existing projects (migration)
- [ ] Unit tests green; CI Gate pass
- [ ] As-built [L07](../12-layer-docs/L07-projects-runtime.md) § pod_service skeleton

---

### Phase 2 — Events + Relations (P-POD-04, P-POD-05)

**PR title:** `feat(pods): pod.* platform events + Relations bind/unbind`

#### 2.1 Kafka whitelist

Добавить в `PLATFORM_EVENT_TYPES`:

```text
pod.provisioned
pod.started
pod.paused
pod.resumed          # optional; можно только pod.started
pod.terminated
pod.failed
pod.hydrated         # optional granular
pod.reconciled       # Phase 3 worker
```

`PodLifecycleEmitter` — PG outbox + Kafka (как `ProjectLifecycleEmitter`).

**Порядок emit:** `pod.*` **до** `project.started` / `project.paused` (documented in pod-service.md).

#### 2.2 Relations

| Command | Effect |
|---------|--------|
| `RelationsCommand.bind_pod_to_project(pod_id, project_id)` | FK + `relation.granted` binding |
| `RelationsCommand.unbind_pod_from_project(pod_id)` | FK null + `relation.revoked` |
| `RelationsQuery.has_pod_binding(project_id)` | admin ACL |

Attach/detach — **admin/internal only** в Phase 2 (без employee UI).

#### 2.3 project_service integration

| Изменение | Детали |
|-----------|--------|
| `project.started` | emit только после `pod.started` (или stub equivalent) |
| `GET /projects/{id}` | `runtime.events` optional last pod event |
| Remove | `project.runtime_unit.attached/detached` из whitelist **после** migrate consumers (compat window 1 PR) |

#### 2.4 Tests

| Case | Assert |
|------|--------|
| pause project | outbox: `pod.paused` then `project.paused` |
| resume project | `pod.started` then `project.resumed` |
| bind/unbind | Relations row + Kafka envelope |
| whitelist reject | unknown `pod.foo` → 422 |

#### DoD Phase 2

- [ ] Все transitions Phase 1 emit `pod.*`
- [ ] Relations bind/unbind covered unit tests
- [ ] Gap **P-POD-04**, **P-POD-05** → partial done (bind API admin-only)
- [ ] [00-relations.md](../00-relations.md) § binding pod↔project

---

### Phase 3 — Real k8s adapter (P-POD-01)

**PR title:** `feat(pods): k8s PodRuntimePort + hydrate + reconcile worker`

#### 3.1 Infrastructure (GitOps only)

| Artifact | Path / note |
|----------|-------------|
| Namespace | `prodavan-sandboxes` (или overlay dev) |
| NetworkPolicy | [isolation.md](../14-project-containers/isolation.md) |
| Sandbox image | tools + egress-only |
| RBAC SA | API pod_service identity → create/delete Pod in sandboxes ns |

**Запрещено:** `kubectl apply` вручную; только `infra/k3s` + Argo.

#### 3.2 K8sPodRuntimeAdapter

| Method | k8s |
|--------|-----|
| `create_pod(spec)` | Pod + labels ([k8s-contract.md](../14-project-containers/k8s-contract.md)) |
| `delete_pod(ref, grace?)` | delete; grace=0 for force_kill |
| `get_status(ref)` | phase, restarts |
| `list_managed_pods()` | label selector `managed-by=container-runtime` |

#### 3.3 HydratePort

| Step | |
|------|--|
| Read | MinIO `projects/{workspace_key}/` |
| Write | Pod `/workspace` (init container or sidecar job) |
| Bump | `hydrate_generation` on rematerialize → force re-hydrate on next start |

Feature flag: `POD_RUNTIME_MODE=stub|k8s` (default stub in dev without sandboxes).

#### 3.4 Reconcile worker

| Job | |
|-----|--|
| Celery beat / ops | `PodReconcileService.run()` |
| Drift | Project paused + Pod Running → delete |
| Zombies | k8s Pod without row / deleted project → force_kill + audit `pod.reconciled` |
| Orphans | `list_orphans()` admin metric |

#### 3.5 Admin surface

| Endpoint | |
|----------|--|
| `GET /admin/containers` | join ProjectPod + ProjectQuery |
| `POST /admin/containers/{pod_id}/force-kill` | `PodCommand.force_kill` |
| `POST /admin/containers/reconcile` | trigger worker (admin only) |

#### DoD Phase 3

- [ ] Verify Dev: create project → trigger agent → Pod Running in sandboxes ns (or documented stub fallback)
- [ ] Pause → Pod absent; MinIO blobs retained
- [ ] Reconcile fixes injected drift in integration test
- [ ] Gap **P-POD-01** → done
- [ ] [admin-ui.md](../14-project-containers/admin-ui.md) aligned

---

### Phase 4 — Cleanup + API tighten

**PR title:** `refactor(pods): drop runtime-units API, legacy columns`

| Remove / deprecate | |
|--------------------|--|
| `POST/GET/DELETE .../runtime-units` | 410 or removed after deprecation window |
| `ProjectRuntimeManager` | deleted |
| `project_runtime_units` table | dropped (after data in `project_pods`) |
| `projects.container_ref` writes | read-only mirror of `pod.runtime_ref` → then drop |
| `project.runtime_unit.*` events | removed from whitelist |
| `container_lifecycle.py` legacy | delete if unused |

#### DoD Phase 4

- [ ] Gaps **P-POD-02..05**, **P-PRJ-03** closed in [09-gap-map](../09-gap-map.md)
- [ ] Import lint: `project_service` не импортирует `ContainerRuntimePort`
- [ ] L07 Quality ≥ 8; checklist P1 → `done`

---

## Матрица тестов (сквозная)

| Сценарий | Unit | Integration | Verify Dev |
|----------|------|-------------|------------|
| create project (no pod) | ✓ | | |
| first agent trigger → provision | ✓ | ✓ | ✓ |
| pause / resume | ✓ | ✓ | ✓ |
| complete (read-only) | ✓ | | |
| soft delete project | ✓ | ✓ | ✓ |
| purge workspace | ✓ | mock wipe | |
| company cascade pause | mock | ✓ | smoke |
| idle_pause sweep | mock | ✓ | |
| attach/detach pod (admin) | ✓ | | |
| force_kill | ✓ | k8s mock | admin |
| reconcile zombie | ✓ | ✓ | optional |
| idempotent sync_desired | ✓ | | |

---

## Риски и mitigations

| Риск | Mitigation |
|------|------------|
| Дублирование orchestration project ↔ pod | Единственный entry: `sync_desired`; lint import guard |
| Event order (pod vs project) | Documented; tests on outbox sequence |
| Migration 1:N → 1:1 | Backfill script in Alembic; keep deprecated table 1 release |
| k8s RBAC / quota | Phase 3 behind feature flag; stub default in dev |
| command.py encoding corruption (historical) | Verify UTF-8 on touch; ruff + pytest in CI |

---

## Явно не делать (Non-goals)

- Employee-facing `/pods/*` CRUD
- Agent SDK / MCP inside pod_service
- PVC probe Job как runtime ([README](../14-project-containers/README.md))
- Upstream OpenClaw / GLM sidecars (Platform OpenClaw — один адаптер в agent-bridge)
- Multi-pod per project (sandbox/worker) — future; schema extensible but API 1:1

---

## Definition of Done (P1 целиком)

1. **Изолированно:** k8s writes только в `pod_service/adapters/k8s_*`
2. **Контракт:** `project_service` → `PodCommand.sync_desired` на каждый lifecycle transition
3. **1:1:** invariant enforced DB + command layer
4. **Kafka:** полный `pod.*` lifecycle; outbox always
5. **Relations:** attach/detach через `RelationsCommand` + audit events
6. **GitOps:** sandbox ns + NetworkPolicy в `infra/k3s`; Verify Dev green
7. **Docs:** pod-service.md + as-built L07 + gap-map updated

---

## Связь с контрактами

| ID | Поставщик | Потребитель |
|----|-----------|-------------|
| C-POD-RUNTIME | pod_service | project_service, admin, reconcile worker |
| C-POD-EVENTS | pod_service | metrics, audit (future) |
| C-PROJECT-LC | project_service | UI, cascade, idle_pause |
| C-OBJECT-STORE | MinIO (P0) | HydratePort |
| C-REL-BIND | Relations | pod attach/detach audit |

---

## Ориентир по срокам (не оценка — порядок)

```text
Phase 0 (design)     ── done
Phase 1 (skeleton)   ── 1 PR, ~3–5d dev
Phase 2 (events)     ── 1 PR, ~2–3d
Phase 3 (k8s)        ── 1–2 PR (adapter + GitOps), ~1–2w
Phase 4 (cleanup)    ── 1 PR, ~2d
```

Phase 3 можно параллелить GitOps overlay с adapter code после Phase 1 merged.

---

## Чеклист для PR (копировать)

```markdown
- [ ] Только PodCommand/PodQuery снаружи pod_service
- [ ] sync_desired idempotent
- [ ] Migration + backfill tested
- [ ] Unit tests pod + project integration
- [ ] PLATFORM_EVENT_TYPES updated (if Phase 2+)
- [ ] docs/target updated (gap-map, L07 as-built)
- [ ] No tools/_*.sh in PR
- [ ] CI Gate → merge → Verify Dev
```
