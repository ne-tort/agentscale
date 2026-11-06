# Argo CD GitOps (as-built)

Живой путь — [`infra/argocd/README.md`](../../infra/argocd/README.md) и [`runbook.md`](runbook.md).

## Bootstrap (once)

Bootstrap выполняется **Terraform на VM** ([runbook.md §2](runbook.md)): provisioner
`gitops_bootstrap` сам применяет манифесты ниже, создаёт секреты `repo-agentscale` и
`ghcr-pull` (из `TF_VAR_ghcr_token`) и дожидается Argo + платформы. Ручной
`kubectl apply` как способ бутстрапа кластера запрещён; команды ниже — справка о том,
что делает bootstrap-скрипт из checkout на VM:

```bash
# внутренности gitops-bootstrap.sh (не операторский шаг):
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
```

- `install/` — Argo CD v2.13.3 (vendored) + patches (git timeout/repo-server `hostNetwork` — runbook §3.2, IfNotPresent, health)
- `sealed-secrets/` — Bitnami controller для секретов платформы (`ghcr-pull` dev-кластер получает напрямую из terraform-bootstrap)
- `apps/` — AppProject `agentscale` (+ `agent-sandbox`), Applications `agentscale-dev`, `agentscale-prod`, `agentscale-e2e`, `agent-sandbox`
- `root-app.yaml` — syncs `infra/argocd/apps` (selfHeal + prune); для выделенного prod-сервера — `root-app-prod.yaml` (только prod-приложения, ветка `prod`)

## Day-2

Merge to `main` → Argo sync. Verify: `poetry run prodavan-ops wait && smoke`.

**`agentscale-e2e` — постоянно OutOfSync / Missing: by design, не инцидент.**
Приложение manual-sync-only ([`apps/agentscale-e2e.yaml`](../../infra/argocd/apps/agentscale-e2e.yaml),
selfHeal выключен) и указывает на ephemeral e2e overlay
(`infra/k3s/overlays/e2e` — pytest Job с `ttlSecondsAfterFinished`). Sync делает
только CI / `prodavan-ops e2e run`, `prodavan-ops e2e cleanup` удаляет Job —
между прогонами Argo UI стабильно показывает `OutOfSync`/`Missing`.
Реагировать стоит только на out-of-sync у `agentscale-dev` или `agent-sandbox`
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
