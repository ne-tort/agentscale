# Argo CD (GitOps)

## Install (declarative)

```bash
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
```

- `install/` — upstream Argo CD v2.13.3 + patches (git timeout, IfNotPresent).
- `sealed-secrets/` — Bitnami controller for `ghcr-pull` SealedSecret.
- `apps/` — AppProject `prodavan` + Application `prodavan-dev` → `infra/k3s/overlays/dev`.
- `root-app.yaml` — syncs `infra/argocd/apps` (apply once).

## Day-2

```bash
cd infra/ops && poetry run prodavan-ops wait --app prodavan-dev
```

No shell bootstrap scripts. Secrets: [`overlays/dev/SECRETS.md`](../k3s/overlays/dev/SECRETS.md).
