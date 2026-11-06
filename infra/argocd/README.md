# Argo CD (GitOps)

## Install (declarative)

```bash
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml          # dev-host (root app of apps/)
# или, на выделенном prod-хосте (terraform gitops_apps=prod):
kubectl apply -f infra/argocd/root-app-prod.yaml
```

На реальном кластере это делает не человек, а `gitops_bootstrap` из terraform
(`infra/terraform/modules/k3s-dev-host/`): apply install → wait argocd-server +
argocd-repo-server (600s) → secret `repo-agentscale` → apply sealed-secrets →
wait controller (300s) → секреты `ghcr-pull` в namespaces → apply root app →
wait `agent-sandbox` Synced+Healthy (8 мин) → wait `agentscale-dev` (или
`agentscale-prod` на prod-хосте) Synced+Healthy (18 мин).

## Состав

- `install/` — вендоренный upstream Argo CD **v2.13.3** + патчи:
  git-таймаут/HTTP1.1/hostNetwork для repo-server, `IfNotPresent` для ВСЕХ
  ворклоадов (теги pinned+публичные — рестарты подов не зависят от реестров),
  health-кастомизации `argocd-cm` (PVC Pending=Healthy для
  WaitForFirstConsumer, Lost=Degraded; Ingress=Healthy — Traefik на k3s без
  status.loadBalancer).
- `sealed-secrets/` — вендоренный манифест Bitnami Sealed Secrets controller
  **v0.27.3** (`controller-v0.27.3.yaml`, без remote-fetch при `apply -k`).
  Секрет `ghcr-pull` в git НЕ запечатан: bootstrap создаёт его императивно
  как обычный docker-registry secret, поэтому пересоздание ключа контроллера
  на новом кластере ничего не ломает.
- `apps/` — обычный каталог (БЕЗ kustomization.yaml, иначе `directory`
  include/exclude root-приложений игнорируется):
  - `00-project.yaml` (AppProject `agentscale`, sync-wave −10),
    `00-agent-sandbox-project.yaml` (AppProject `agent-sandbox`, sync-wave −10);
  - `agent-sandbox.yaml` — платформа (CRDs + controller + router из
    `infra/agent-sandbox`), sync-wave **−5**: CRDs появляются раньше, чем
    `*-sandboxes`-слой платформы начнёт применять SandboxTemplate/WarmPool;
  - `agentscale-dev.yaml` — `infra/k3s/overlays/dev`, ветка `main`, auto-sync;
  - `agentscale-e2e.yaml` — `infra/k3s/overlays/e2e`, только ручной sync из CI;
  - `agentscale-prod.yaml` — `infra/k3s/overlays/prod`, ветка `prod`
    (содержимое не трогать без явной задачи на prod).
- `root-app.yaml` — **dev-хост**: синкает `infra/argocd/apps` с
  `directory.exclude: agentscale-prod.yaml` — dev-bootstrap prod-контур НЕ
  создаёт. Внимание: повторный apply этого файла на кластере, где
  `agentscale-prod` уже синкался старым (нефильтрованным) root app, сработает
  prune и удалит prod-приложение каскадом.
- `root-app-prod.yaml` — **выделенный prod-хост**: тот же каталог с
  `directory.include: {00-*.yaml,agent-sandbox.yaml,agentscale-prod.yaml}`
  (один gobwas-паттерн, `{a,b}` поддерживается — сверено с исходниками
  argo-cd v2.13.3).

## Образы (все pinned, публичные; теги проверены на доступность 2026-11-05)

| Образ | Где |
|---|---|
| `quay.io/argoproj/argocd:v2.13.3` | install (server, repo-server, app-controller, applicationset, notifications, init-контейнеры) |
| `ghcr.io/dexidp/dex:v2.41.1` | install (dex-server) |
| `redis:7.0.15-alpine` | install (argocd-redis) |
| `docker.io/bitnami/sealed-secrets-controller:0.27.3` | sealed-secrets |
| `registry.k8s.io/agent-sandbox/agent-sandbox-controller:v1.0.4` | через app `agent-sandbox` (infra/agent-sandbox) |
| `registry.k8s.io/agent-sandbox/sandbox-router-go:v1.0.4` | через app `agent-sandbox` (infra/agent-sandbox) |

App-образы платформы (`ghcr.io/ne-tort/...`) живут в `infra/k3s/**` и тянутся
через `ghcr-pull`-секреты, которые bootstrap создаёт в `agentscale-dev`,
`agentscale-dev-sandboxes`, `agentscale`, `agentscale-sandboxes`.

## Day-2

```bash
cd infra/ops && poetry run prodavan-ops wait --app agentscale-dev
```

> **Политика сред (2026-10-08):** работаем **только с dev** (`agentscale-dev`).
> **Prod (`agentscale-prod`) — только по явному требованию**: не дёргать его
> sync/refresh и не промоциить в prod "заодно" с обычной задачей.

Секреты: [`overlays/dev/SECRETS.md`](../k3s/overlays/dev/SECRETS.md).
