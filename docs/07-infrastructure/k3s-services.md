# k3s Services (as-built)

Kubernetes-манифесты сервисов **Agentscale** в k3s: `infra/k3s/`, поставка — Argo CD
([`argocd.md`](argocd.md)), проверка формы — `prodavan-ops validate`. Документ описывает
**то, что реально лежит в репозитории**, а не целевую архитектуру: `mcp-gateway`,
`agent-worker` PodTemplate и отдельный `prodavan-ws` в кластер не поставлены (см.
закомментированный `Deferred:` в `base/kustomization.yaml`).

Образы — в основном `ghcr.io/ne-tort/*` (сторонние сервисы версионированы и
зеркалированы в GHCR: rate limit Docker Hub на холодной ноде, #299). Единственное
исключение — `overlays/prod/userdb-init.yaml`, где Job `agentscale-userdb-init`
всё ещё тянет `postgres:16.15` напрямую из Docker Hub; в dev тот же Job уже
переведён на зеркало `ghcr.io/ne-tort/postgres:16.15`. `prodavan-ops validate`
этого не ловит: он рендерит только `overlays/dev`.

---

## Directory layout

```text
infra/k3s/
├── base/
│   ├── kustomization.yaml          # platform + guards + api + web + ingress
│   ├── platform/                   # redis, minio, mongodb, opensearch, kafka(redpanda),
│   │                               # celery(worker/beat/indexer), keycloak, searchnet,
│   │                               # gotenberg, pdb
│   ├── guards/app-budget.yaml      # LimitRange + ResourceQuota для ns приложения
│   ├── agentscale-api/             # configmap, secret, pvc, deployment, service
│   ├── agentscale-web/             # deployment, service
│   ├── agentscale-sandbox/         # rbac-api.yaml — единственный SA (agentscale-api)
│   ├── agentscale-sandboxes-ks/    # SandboxTemplate, WarmPool, RBAC, NetworkPolicy, bridge-secret
│   └── ingress.yaml                # два Ingress: web (:8088) и websecure (:443)
├── overlays/
│   ├── dev/                        # ns agentscale-dev + sandboxes
│   ├── e2e/                        # ephemeral pytest Job (manual Argo sync)
│   ├── prod/                       # ns agentscale + sandboxes, пины SHA
│   └── sandboxes/                  # re-export dev/sandboxes (валидация сборки)
└── node-manifests/
    └── platform-guards.yaml        # k3s add-on: LimitRange/Quota для argocd, kube-system,
                                    # agent-sandbox-system (вне AppProject agentscale)
```

Оверлея **staging нет**: контуров три — dev, prod, e2e (плюс отдельный sandboxes-слой
в каждом контуре).

---

## Контуры и неймспейсы

| Контур | Argo Application | Namespace (приложение) | Namespace (sandbox) |
|--------|------------------|------------------------|---------------------|
| dev | `agentscale-dev` ← ветка `main` | `agentscale-dev` | `agentscale-dev-sandboxes` |
| prod | `agentscale-prod` ← ветка `prod` | `agentscale` | `agentscale-sandboxes` |
| e2e | `agentscale-e2e` (manual sync) | `agentscale-dev` (Job) | `agentscale-dev-sandboxes` |

`argocd` и `agent-sandbox-system` — Argo и контроллер agent-sandbox соответственно.

---

## agentscale-api

`base/agentscale-api/deployment.yaml`. Один Deployment на контур, `replicas: 1`,
`strategy: Recreate` — старый под снимается до создания нового (тот же PVC
`agentscale-api-storage` монтируют celery-воркеры, а кластер single-node).

```yaml
initContainers:
  - name: migrate
    image: ghcr.io/ne-tort/agentscale-api:latest   # dev: Always; prod: IfNotPresent (пин SHA)
    command: ["./scripts/migrate.sh"]
containers:
  - name: api          # uvicorn prodavan.main:app     :8000 — публичная поверхность
  - name: api-pod      # uvicorn prodavan.main_pod:app :8001 — Pod Identity Bridge
```

- **`api-pod`** — отдельный процесс на `:8001`; его видит только sandbox-неймспейс
  (NetworkPolicy → `:8001`), в нём выключены фоновые воркеры
  (`CELERY_ENABLED`, `KAFKA_CONSUMER_ENABLED`, `TRIGGER_WORKER_ENABLED`,
  `IDLE_PAUSE_WORKER_ENABLED`, `POD_RECONCILE_WORKER_ENABLED`,
  `METRICS_STORAGE_SAMPLER_ENABLED` = `false`).
- Пробы: `startupProbe` → `/health/live` (60×5с), `readinessProbe` → `/health/ready`
  (`timeoutSeconds: 5`, 12 попыток — проба ходит в БД + Redis, дефолтная 1с на
  холодной ноде давала вечный `context deadline exceeded`), `livenessProbe` →
  `/health/live`.
- Ресурсы: api `100m/256Mi` → `1 CPU/1Gi`; api-pod `50m/256Mi` → `1 CPU/1Gi`.
- SA `agentscale-api`, `imagePullSecrets: [ghcr-pull]`, PVC `agentscale-api-storage`
  в `/data/storage` у всех трёх контейнеров.
- `imagePullPolicy`: **base** — `IfNotPresent`; **dev overlay**
  (`patch-dev-api.yaml`) переопределяет на `Always` для всех трёх контейнеров, потому
  что тег `:latest`; **prod** (`patch-prod.yaml`) оставляет `IfNotPresent` — тег
  пиновый SHA. `prodavan-ops validate` отдельно требует `Always` у `migrate`
  в dev-render: `IfNotPresent` кэширует старый head → `upgrade` — noop, API отдаёт 500.

Service `agentscale-api` — ClusterIP, порты `8000` (`http`) и `8001` (`pod-http`).
HPA нет.

---

## agentscale-web

`base/agentscale-web/deployment.yaml`: `replicas: 1`, образ
`ghcr.io/ne-tort/agentscale-web:latest`, контейнер `web` на `:80`, пробы `/health`,
ресурсы `50m/64Mi` → `250m/128Mi`. Service `agentscale-web` — ClusterIP `:80`.
Dev-оверлей ставит `imagePullPolicy: Always`.

---

## ingress (Traefik)

`base/ingress.yaml` — два Ingress-объекта, `ingressClassName: traefik`:

| Объект | Entrypoint | Правила |
|--------|-----------|---------|
| `agentscale` | `web` — hostPort **8088** в dev, **80** в prod (задаёт terraform `http_port`) | `/api`, `/health` → `agentscale-api:8000`; `/` → `agentscale-web:80` |
| `agentscale-https` | `websecure` (:443) | те же пути; `tls: [{}]` — wildcard SNI |

**TLS — self-signed сертификат из default TLSStore Traefik** (`kube-system/agentscale-tls`,
SANs: IP VM + `agentscale.dev`/`agentscale.local`/`localhost`, кладётся Terraform-модулем
через `https_tls_sans`). **cert-manager и Let's Encrypt не используются** —
`cluster-issuer: letsencrypt-prod` в манифестах отсутствует (целевой прод-TLS на домене —
не реализован).

Маршрутизация по контурам (as-built 2026-10-08, «любой публичный адрес = prod»):

- `websecure` (:443): prod оставляет **wildcard catch-all** (`priority: 50`), dev
  пинит `agentscale.dev` + `localhost` (`priority: 100`) — dev-имена выигрывают, всё
  прочее (публичный IP через punnel/VPN, сканеры) уходит в prod. Публичный IP в
  маршрутизации не хардкодится.
- `web` (:8088): dev остаётся wildcard, prod пинит `agentscale.local`
  (`patch-prod.yaml`) + дополнительно `web-nodeport.yaml` и
  `web-https-ip-ingressroute.yaml`.

---

## platform (общие сервисы)

Все — в неймспейсе приложения (`agentscale`/`agentscale-dev`), `base/platform/`.
Один Pod на сервис, `PodDisruptionBudget maxUnavailable: 0` для kafka/redis/minio/
postgres/opensearch (локальный single-node: запрет добровольного eviction, чтобы
PVC-брокеры не дренировались посреди записи).

| Сервис | Kind / имя | Образ | Порт |
|--------|-----------|-------|------|
| Redis | StatefulSet `agentscale-redis` + PVC `agentscale-redis-data` | `ghcr.io/ne-tort/redis:7.4.11-alpine` | 6379 |
| MinIO | StatefulSet `agentscale-minio` + PVC `agentscale-minio-data`, Job `agentscale-minio-init` | `ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z`, `ghcr.io/ne-tort/mc:RELEASE.2024-10-08T09-37-26Z` | 9000 (S3), 9001 (console) |
| MongoDB | StatefulSet `agentscale-mongodb` + PVC, Job `agentscale-mongodb-init` | `ghcr.io/ne-tort/mongo:7.0.14` | 27017 |
| OpenSearch | StatefulSet `agentscale-opensearch` + PVC, Job `agentscale-opensearch-init` | `ghcr.io/ne-tort/opensearch:2.17.1` | 9200, 9300 |
| Kafka (Redpanda) | StatefulSet `agentscale-kafka` + Job `agentscale-kafka-init` | `ghcr.io/ne-tort/redpanda:v24.2.4` | 9092, 9644 |
| Keycloak | StatefulSet `agentscale-keycloak` + PVC, Job `agentscale-keycloak-init` | `ghcr.io/ne-tort/keycloak:26.0` | 8080, **hostPort 8089** (dev) / 8099 (prod) |
| Celery | Deployments `agentscale-celery-worker`, `-beat`, `-indexer` | `ghcr.io/ne-tort/agentscale-api:latest` | — |
| searchnet | Deployment `searchnet` | `ghcr.io/ne-tort/searchnet:71ca334445d6` (`Always`) | 3950 |
| Gotenberg | Deployment `gotenberg` | `ghcr.io/ne-tort/gotenberg:8.20.1` | 3000 |

- **Keycloak** — публичный issuer на hostPort (`http://127.0.0.1:8089/realms/agentscale`
  в dev, `:8099` в prod); Admin REST — in-cluster `:8080`. Realm накатывает
  init-Job из ConfigMap `agentscale-keycloak-realm`.
- **Celery** использует тот же образ, что API; `RUN_MIGRATIONS=0` (миграции — только
  initContainer API), `--concurrency=1 --prefetch-multiplier=1 --max-memory-per-child=350000`
  против prefork-OOM в 1Gi cgroup, общий RWO-PVC `agentscale-api-storage`.
- **searchnet** — web-search gateway; `Always` осознанно: пакет приватный, а новый Pod
  должен поднять ровно свою сборку.

### Postgres

Отдельного CNPG в кластере нет: Postgres — **Deployment** в оверлее контура
(`overlays/dev/postgres.yaml`, `overlays/prod/postgres.yaml`), `replicas: 1`,
`strategy: Recreate`, образ `ghcr.io/ne-tort/postgres:16.15`, PVC 5Gi, `PGDATA` с
подкаталогом, `preStop pg_ctl stop -m fast`, `startupProbe` 120 попыток на WAL-recovery
после грязного shutdown. Соседнюю БД Tenant User DB создаёт Job-хук
`agentscale-userdb-init` (`argocd.argoproj.io/hook: Sync`).

---

## agent-sandbox (Pod'ы проектов)

`base/agentscale-sandboxes-ks/` — слой agent-sandbox (ADR:
[`08-migration/agent-sandbox-adr.md`](../08-migration/agent-sandbox-adr.md)):

| Объект | Имя | Суть |
|--------|-----|------|
| `SandboxTemplate` | `agentscale-agent` | blueprint Pod'а: контейнер `agent-runtime` на `:3921`, `runAsNonRoot`/uid 1000/fsGroup 1000, VCT `workspace` 5Gi, `service: true`, `envVarsInjectionPolicy: Disallowed`, `volumeClaimTemplatesPolicy: Disallowed`, `networkPolicyManagement: Unmanaged` |
| `SandboxWarmPool` | `agentscale-agent-pool` | `replicas: 2`, `updateStrategy: Recreate` |
| `Role`/`RoleBinding` | `agentscale-sandbox-claims` | API пишет `SandboxClaims`, читает/патчит `Sandboxes`, `pods: get/list/watch/delete`; `pods/exec` нет — файловый/командный канал идёт через router |
| `NetworkPolicy` | `agentscale-sandboxes-egress` | Ingress :3921 только из `agent-sandbox-system` (+ legacy-правило из ns приложения), egress — только `:8001` в ns приложения, DNS, публичный 80/443, внешний 5432/5433 |
| `Secret` | `pod-agent-bridge-auth` | bridge-token для API → agent-runtime |

Образ runtime — `ghcr.io/ne-tort/prodavan-agent-runtime:58211a516231`
(пин `prodavan-claw@58211a5`), `imagePullPolicy: IfNotPresent`. Пин один и тот же в
`SandboxTemplate`, в `POD_AGENT_RUNTIME_IMAGE` (base + dev configmap) и в probe-pod;
история и правила обновления — [`agent-runtime-delivery.md`](agent-runtime-delivery.md).

**Probe-pod** (dev-оверлей, `overlays/dev/sandboxes/probe-pod.yaml`): Deployment
`agentscale-probe-pod` + Service в `agentscale-dev-sandboxes`, тот же пин образа и
`IfNotPresent`; используется для проверки AI-ключей и списка моделей
(`POD_PROBE_ENABLED`, в prod `false`).

Динамические Pod'ы создаёт **контроллер agent-sandbox** по `SandboxClaim`, а не API:
API больше не делает `pods: create`.

---

## guards (защита узла от OOM)

- `base/guards/app-budget.yaml` — `LimitRange app-container-limits`
  (`default 500m/512Mi`, `max 2 CPU/4Gi`) и `ResourceQuota app-budget`
  (requests 12Gi, limits 26Gi, cpu 8/14, pods 45) в неймспейсе приложения. Причина:
  сумма лимитов на проде доходила до 17.8 ГиБ при allocatable 14.9 ГиБ, нода трижды
  уходила в глобальный OOM (2026-10-21…23).
- `node-manifests/platform-guards.yaml` — тот же приём для `argocd`, `kube-system`,
  `agent-sandbox-system` + `ResourceQuota argocd-budget`. Это **k3s add-on**
  (`/var/lib/rancher/k3s/server/manifests`), а не Argo: эти неймспейсы вне
  AppProject `agentscale`.

---

## Чего в кластере нет

Документ раньше описывал объекты, которых в репозитории не существует. Явно, чтобы не
искать их снова:

| Отсутствует | Факт |
|-------------|------|
| `base/prodavan-api/`, `base/prodavan-ws/`, `base/redis/`, `workers/`, `overlays/staging/` | каталогов нет; API — `base/agentscale-api/`, кэш — `base/platform/redis.yaml` |
| `mcp-gateway` | не поставлен; в `base/kustomization.yaml` помечен `Deferred` |
| `agent-worker` PodTemplate + namespace `prodavan-workers` | не поставлен; Pod'ы проектов — `SandboxClaim` → `agentscale-*-sandboxes` |
| HPA (`hpa.yaml`) | ни одного `HorizontalPodAutoscaler` в `infra/k3s`; всё `replicas: 1` (+ warm-pool 2) |
| неймспейсы `prodavan`, `prodavan-workers`, `prodavan-data` | не существуют; см. таблицу контуров |
| ingress-class `ingress-nginx` | контроллер — **Traefik** (штатный k3s) |
| cert-manager / `letsencrypt-prod` | нет; TLS — self-signed default TLSStore |
| образы `ghcr.io/org/prodavan-api:1.0.0`, `prodavan-mcp-gateway`, `minio/minio:latest` | только `ghcr.io/ne-tort/*` с версионными тегами (`postgres:16.15` из Docker Hub остался лишь в prod-оверлее `userdb-init`) |
| бакет `prodavan-prod` | бакет называется `agentscale` (`S3_BUCKET`) |

`minio/minio:latest` не просто «не используется» — он **заваливает гейт**:
`prodavan-ops validate` (`verify_image_pins`) отклоняет любой сторонний образ с тегом
`:latest` («third-party must not use :latest»), а `REQUIRED_SNIPPETS` требует
`ghcr.io/ne-tort/minio:RELEASE.2024-10-02T17-50-41Z`. Первопартийные `:latest`
(`agentscale-api`, `agentscale-web`, `prodavan-agent-runtime`, `searchnet`) — наоборот,
обязательны, и dev-render должен содержать `imagePullPolicy: Always`.

---

## ResourceQuota и «сколько подов»

Отдельной квоты на sandbox-неймспейс нет — там действует `NetworkPolicy` и лимиты
`SandboxTemplate` (per-sandbox `100m/256Mi` → `1 CPU/1Gi`), а общий потолок подов
контура задан `ResourceQuota app-budget` (45 подов) в неймспейсе приложения.

---

## Monitoring hooks

На Pod'ах API (`base/agentscale-api/deployment.yaml`) — аннотации
`prometheus.io/scrape: "true"`, `prometheus.io/port: "8000"`, `prometheus.io/path: "/metrics"`.
Самого Prometheus/Operator и `ServiceMonitor` в репозитории нет: аннотации рассчитаны
на внешний скрейпер.

---

## Связанные документы

- [topology.md](topology.md)
- [argocd.md](argocd.md)
- [agent-runtime-delivery.md](agent-runtime-delivery.md)
- [env-matrix.md](env-matrix.md)
- [runbook.md](runbook.md)
