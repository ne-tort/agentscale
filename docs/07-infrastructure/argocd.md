# Argo CD GitOps (as-built)

Живой путь — [`infra/argocd/README.md`](../../infra/argocd/README.md) и [`runbook.md`](runbook.md).

## Bootstrap (once)

```bash
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
```

- `install/` — Argo CD v2.13.3 + patches (git timeout, IfNotPresent)
- `sealed-secrets/` — Bitnami controller for `ghcr-pull`
- `apps/` — AppProject `prodavan` + Application `prodavan-dev` → `infra/k3s/overlays/dev`
- `root-app.yaml` — syncs `infra/argocd/apps` (selfHeal + prune)

## Day-2

Merge to `main` → Argo sync. Verify: `poetry run prodavan-ops wait && smoke`.

Secrets: [`infra/k3s/overlays/dev/SECRETS.md`](../../infra/k3s/overlays/dev/SECRETS.md).

---

## Aspirational (cloud / multi-env)

Ниже — целевой дизайн staging/prod (App-of-Apps per env, external secrets operator).  
**Не** смешивать с dev bootstrap выше.

```text
infra/argocd/
├── install/           # shared control plane
├── apps/              # dev Application(s)
└── (future) staging/  # separate overlay + Application
```

См. также [`terraform.md`](terraform.md) для cloud skeleton modules.
