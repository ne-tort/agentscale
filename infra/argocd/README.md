# Argo CD (GitOps)

## Install (declarative)

```bash
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
```

- `install/` — upstream Argo CD v2.13.3 + patches (git timeout, IfNotPresent).
- `sealed-secrets/` — Bitnami controller for `ghcr-pull` SealedSecret.
- `apps/` — AppProject `agentscale` + Applications `agentscale-dev` → `infra/k3s/overlays/dev` и `agentscale-prod` → `infra/k3s/overlays/prod` (ветка `prod`).
- `root-app.yaml` — syncs `infra/argocd/apps` (apply once).

## Day-2

```bash
cd infra/ops && poetry run prodavan-ops wait --app agentscale-dev
```

> **Политика сред (2026-10-08):** работаем **только с dev** (`agentscale-dev`). **Prod (`agentscale-prod`) — только по явному требованию**: не дёргать его sync/refresh и не промоциить в prod "заодно" с обычной задачей.

No shell bootstrap scripts. Secrets: [`overlays/dev/SECRETS.md`](../k3s/overlays/dev/SECRETS.md).
