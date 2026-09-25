# 03 — DevOps: инфраструктура миграции

## 3.1 Установка agent-sandbox (GitOps, ns agent-sandbox-system)

Вендорные компоненты ставим через **Argo** (не kubectl apply), чтобы не нарушать GitOps-канон.

- `infra/argocd/apps/agent-sandbox.yaml` — новое Application `agent-sandbox` → source: `https://github.com/ne-tort/agentscale.git`? **Нет** — controller не форкаем, ставим из upstream-манифестов. Вариант: helm-chart из OCI или vendored-манифесты.
- **Решение: vendored-манифесты** (как уже сделано с Argo install v2.13.3): `infra/agent-sandbox/install/sandbox-with-extensions-v1.0.2.yaml` — коммитим upstream release-манифест, Argo применяет. Обновление = новый PR со свежим манифестом (visible diff, откат = revert).
- `infra/agent-sandbox/router/` — deployment+service+rbac из `sandbox-router/deploy/` (порты 8080/8081/9090), 2 реплики, PDB. `--cache-enabled=true` (обязателен для X-Sandbox-ID к warm-подам), `--authz-mode=allow-all` (роутер не в публичной зоне; авторизация — наша, платформенным токеном, см. ниже).
- AppProject `prodavan`: добавить destination `agent-sandbox-system` + clusterResourceWhitelist уже покрывает CRD-установку (Namespace). Отдельный AppProject `agent-sandbox` с sourceRepos `https://github.com/kubernetes-sigs/agent-sandbox` — **не** используем (vendored); AppProject prodavan расширяем destination'ом.

## 3.2 Namespace и объекты prodavan-sandboxes

Новый kustomize-слой `infra/k3s/base/prodavan-sandboxes-ks/` (вместо `prodavan-sandbox/`):

1. `sandboxtemplate.yaml` + `sandboxwarmpool.yaml` — из 01 (канон). Ресурсы/образ — параметризованы через kustomize `patches` для dev/e2e (replicas: 2 dev / 0 e2e).
2. `networkpolicy.yaml` — миграция селекторов (см. 3.4).
3. `rbac-sandboxes.yaml` — только SA `prodavan-project-pod` (+ новая Role для SA-API на claims, см. 3.3).
4. Удаляются: `probe-job.yaml`, `hydrate-secret.yaml`, Role `prodavan-pod-service` (pods CRUD), Role `prodavan-project-pod` (secrets get prodavan-minio-hydrate), RoleBinding обоих, cluster-heal Roles в sandboxes.

### Образ agent-runtime

- **Тег вместо :latest**: тег продвигается PR'ом изменения SandboxTemplate (image: `ghcr.io/ne-tort/prodavan-agent-runtime:<sha12>`). Изменение SandboxBlueprint → контроллер помечает stale-поды, `updateStrategy: Recreate` пересоздаёт незанятые. Занятые (claimed) — живут до release; «Обновить проект» (reload) пересоздаёт claim → новый образ.
- `imagePullPolicy: IfNotPresent` в шаблоне. Образ 4–6 ГБ: тёплые поды пула уже имеют слои; pull платится один раз на ноду при смене тега.
- **P3 (отдельная задача, не блокер): digest-пиннинг**.
- probe-pod (`prodavan-probe-pod`, проверка AI-ключей) — остаётся в dev overlay, образ синхронизируется тем же тегом.

## 3.3 RBAC API-приложения (замена pods-CRUD)

SA `prodavan-api` (ns prodavan) получает Role в `prodavan-sandboxes`:

```yaml
kind: Role
metadata: {namespace: prodavan-sandboxes, name: prodavan-sandbox-claims}
rules:
- apiGroups: ["extensions.agents.x-k8s.io"]
  resources: ["sandboxclaims", "sandboxclaims/status"]
  verbs: ["get", "list", "watch", "create", "delete"]
- apiGroups: ["agents.x-k8s.io"]
  resources: ["sandboxes", "sandboxes/status"]
  verbs: ["get", "list", "watch", "patch"]
- apiGroups: ["metrics.k8s.io"]
  resources: ["pods"]
  verbs: ["get", "list"]          # PodMetricsPort остаётся на metrics-server
```

- pods/exec: **удаляем** у SA-API. Канал файлов/команд к поду — router (SDK files API `POST /upload`, `GET /download`). Dehydrate-snapshot — см. 04 (первые волны через SDK files, не exec).
- Role `prodavan-project-pod` (secrets get prodavan-minio-hydrate) — удаляется (legacy hydrate).
- SA `prodavan-project-pod` остаётся как serviceAccount подов (automount=false от template; NetworkPolicy всё равно ограничивает egress до :8001/443/DNS).

## 3.4 NetworkPolicy — миграция селекторов

`prodavan.io/managed-by: pod-service` умирает вместе с pod_spec.py. Поды сандбоксов получают лейблы от контроллера: `agents.x-k8s.io/*`. Но контроллер управляет **всеми** Sandbox ns… В ns `prodavan-sandboxes` живут только наши сандбоксы → podSelector можно **убрать** (весь ns под политикой), но селектор по launch-типу не нужен. Итог:

- egress: `podSelector: {}` (весь ns) → ns prodavan TCP 8001; DNS; интернет 443/80; внешние PG 5432/5433 (кроме кластерных CIDR). Инвариант безопасности тот же, покрытие шире (warm-поды тоже закрыты).
- ingress: from ns `agent-sandbox-system` (router) TCP 3921 **+ from ns prodavan** TCP 3921 (bootstrap-push от API через router — трафик идёт от router, но DirectConnection API→pod:3921 оставляем опцией; в первой волне — только через router: ingress from ns agent-sandbox-system).
- probe-pod — отдельная политика (остаётся его egress).

**Важно**: router стрипает Host/Authorization; на :3921 авторизация платформенным токеном (API→runtime) и Bridge JWT (runtime→API) — сквозные Bearer не пробрасываются, каждый hop свой токен.

## 3.5 Argo / синхронизация

- `prodavan-dev` Application: path тот же (`infra/k3s/overlays/dev`), включит новый слой sandboxes автоматически.
- Sync-waves: agent-sandbox App — до prodavan-dev (wave/bootstrapping через root-app ordering: app `agent-sandbox` создаётся раньше в `apps/` с wave 0).
- `overlays/dev/sandboxes/` — удалить старые rbac/networkpolicy-копии, оставить probe-pod + `pod-agent-bridge-auth` Secret.
- `infra/ops`: `heal`/`validate` переключаются с «delete Terminating pods» на `agent_sandbox` health-checks (claim status, pool readyReplicas); `rollout` — рестарт probe-pod остаётся; добавить `prodavan-ops pool-status`.
- cluster-heal-cronjob — удалить целиком (wsl-флейпы чинит controller-requeue, warm pool самовосстанавливается).
- AppProject whitelist: добавить `Sandbox, SandboxClaim, SandboxTemplate, SandboxWarmPool` (extensions.agents.x-k8s.io, agents.x-k8s.io), Deployment/Service/SA в ns agent-sandbox-system (если в том же AppProject) — либо отдельный AppProject для system-ns.

## 3.6 ConfigMap `prodavan-config` — дифф

| Ключ | Судьба |
|---|---|
| `POD_RUNTIME_MODE` | значения `stub` \| `sandbox` (k8s → sandbox; миграция 07) |
| `POD_SANDBOX_NAMESPACE` | остаётся (`prodavan-sandboxes`) |
| `POD_SANDBOX_IMAGE / HYDRATE_IMAGE` | **удалить** (в SandboxTemplate) |
| `POD_SANDBOX_SA` | остаётся (в шаблоне) |
| `POD_SANDBOX_IMAGE_PULL_SECRET` | удалить (в шаблоне) |
| `POD_SANDBOX_CPU/MEMORY_*` | удалить → в шаблоне; per-company квоты — 06 |
| `POD_READY_TIMEOUT_SEC / PULL / PROVISIONING` | пересмотреть: watch-based, дефолты SDK (sandbox_ready_timeout=180) |
| `POD_AGENT_RUNTIME_*` | остаётся: ENABLED, IMAGE (→шаблон), PORT 3921, API_BASE_URL, AUTH_SECRET, TOKEN, BOOTSTRAP_ENABLED, WEB_SEARCH_* |
| `POD_PROBE_*` | остаётся (dev) |
| `SANDBOX_K8S_JOBS` | остаётся (MCP jobs, вне scope) |
| НОВЫЕ | `POD_SANDBOX_ROUTER_URL=http://sandbox-router-svc.agent-sandbox-system.svc:8080`, `POD_SANDBOX_WARMPOOL=prodavan-agent-pool`, `POD_SANDBOX_SHUTDOWN_TTL_SEC` |

## 3.7 Terraform / bootstrap

- gitops-bootstrap.sh.tpl: после root-app — ждать Healthy `agent-sandbox` App (добавить в ожидания), т.к. prodavan-dev зависит от CRD.
- ghcr-pull Secret в ns `agent-sandbox-system` не нужен (образы registry.k8s.io); в `prodavan-sandboxes` — остаётся (там уже создаётся).

## 3.8 Риски

1. **RWO PVC + single-node k3s**: warm-pool поды резервируют PVC; suspend'нутые тоже. На single-node ок; при multinode — local-path provisioning не переезд. Не блокер dev.
2. **PVC объём**: 5Gi × (replicas пула + активные проекты). Dev-квота диска WSL — контролировать; storageClassName local-path, volumeMode Filesystem.
3. **Vendored v1.0.2 + v1beta1**: слежение за KEP 539.2 (runtime standardization), api-migration-guide при апгрейдах.
4. **Argo orphanedResources**: Pod/Sandbox от контроллера в ns prodavan-sandboxes → добавить ignore Sandbox/SandboxClaim (они «незаявленные» с т.з. kustomize, создаёт API рантаймом).
