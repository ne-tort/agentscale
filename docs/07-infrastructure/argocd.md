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
- `apps/` — AppProject `prodavan` (+ `agent-sandbox`), Applications `prodavan-dev`, `agent-sandbox`, `prodavan-e2e`
- `root-app.yaml` — syncs `infra/argocd/apps` (selfHeal + prune)

## Day-2

Merge to `main` → Argo sync. Verify: `poetry run prodavan-ops wait && smoke`.

**`prodavan-e2e` — постоянно OutOfSync / Missing: by design, не инцидент.**
Приложение manual-sync-only ([`apps/prodavan-e2e.yaml`](../../infra/argocd/apps/prodavan-e2e.yaml),
selfHeal выключен) и указывает на ephemeral e2e overlay
(`infra/k3s/overlays/e2e` — pytest Job с `ttlSecondsAfterFinished`). Sync делает
только CI / `prodavan-ops e2e run`, `prodavan-ops e2e cleanup` удаляет Job —
между прогонами Argo UI стабильно показывает `OutOfSync`/`Missing`.
Реагировать стоит только на out-of-sync у `prodavan-dev` или `agent-sandbox`
(оба selfHeal + prune) — там это признак незасинканного `main`.

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
