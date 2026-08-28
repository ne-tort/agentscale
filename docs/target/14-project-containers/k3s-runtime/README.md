# k3s Runtime — модуль управления Pod в кластере

Изолированный **инфраструктурный слой** внутри BC `pod_service`: единственный writer объектов k8s для project sandboxes.

| | |
|--|--|
| **Не** | отдельный deployable microservice |
| **Не** | прямой доступ из API handlers / `project_service` |
| **Да** | Port/Adapter внутри `application/pod_service/` + thin client в `infrastructure/k8s/` |
| **SoT desired state** | PostgreSQL (`project_pods`) + `PodCommand.sync_desired` |
| **Async fan-out** | Kafka `pod.*` + `relation.*` (события, не команды) |

## Зачем отдельный модуль docs

P1 (`pod_service`) закрыл **доменную** модель (1:1, events, stub). P2 — **реальный k3s**: create/pause/delete Pod, hydrate файлов, metrics, reconcile zombies.

Документы здесь — канон **как** подключать k3s, **куда** класть код и **когда** использовать Kafka vs синхронный вызов.

## Карта документов

| Файл | Содержание |
|------|------------|
| [architecture.md](architecture.md) | Слои, ADR: не плодить BC; Kafka только для events |
| [ports.md](ports.md) | `PodRuntimePort`, `HydratePort`, `PodFilesPort`, `PodMetricsPort` |
| [operations.md](operations.md) | create / pause / resume / delete / force-kill → k8s API |
| [file-sync.md](file-sync.md) | MinIO → Pod, hot copy, rematerialize |
| [metrics-observability.md](metrics-observability.md) | phase, restarts, CPU/RAM, admin UI |
| [async-events.md](async-events.md) | pod.* Kafka, Celery reconcile, idempotency |
| [patterns-references.md](patterns-references.md) | Operator vs adapter, готовые libs, GitHub refs |
| [gitops-rbac.md](gitops-rbac.md) | namespace, SA, RBAC, NetworkPolicy (as-built paths) |

**План реализации:** [P2-k3s-runtime](../../11-implementation-plan/P2-k3s-runtime.md)

## Связь с существующим каноном

| Документ | Связь |
|----------|--------|
| [pod-service.md](../pod-service.md) | BC границы, `PodCommand`, whitelist events |
| [k8s-contract.md](../k8s-contract.md) | labels, namespace, zombies |
| [isolation.md](../isolation.md) | NetworkPolicy, SA |
| [lifecycle.md](../lifecycle.md) | pause = delete Pod, resume = new Pod + hydrate |
| [13-platform-infra](../../13-platform-infra/) | Kafka, MinIO, Celery managers |

## Non-goals (P2)

- Helm charts production hardening (отдельная infra-волна)
- Multi-cluster / federated scheduling
- GPU / custom device plugins
- In-cluster agent SDK (модуль 08) — agent **внутри** Pod, не platform API
