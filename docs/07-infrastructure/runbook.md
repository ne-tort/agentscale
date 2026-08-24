# Ранбук DevOps (Prodavan) — GitOps

Канон: **git → Argo CD → cluster**. Императив только через `poetry run prodavan-ops …`.  
**`.sh` под `infra/` запрещены.**

Репозиторий: [ne-tort/prodavan](https://github.com/ne-tort/prodavan).  
UI: `http://prodavan.local:8088/` (`Host: prodavan.local`).

---

## 0. Топология

| Компонент | Где |
|-----------|-----|
| k3s `prodavan-dev` | нативный кластер, манифесты [`infra/k3s/overlays/dev`](../../infra/k3s/overlays/dev) |
| Argo CD | `kubectl apply -k infra/argocd/install` затем `kubectl apply -f infra/argocd/root-app.yaml` |
| Workloads | Application `prodavan-dev` → `infra/k3s/overlays/dev` |
| GHA runner | процесс на Kali host ([`infra/github-runner/README.md`](../../infra/github-runner/README.md)) |
| Ops CLI | [`infra/ops`](../../infra/ops) (Poetry) |

Не использовать Docker Desktop для runner (TLS EOF). Не контейнер host-net (Session Conflict).

Образы first-party: `ghcr.io/ne-tort/prodavan-{api,web}:latest`, `imagePullPolicy: IfNotPresent`, secret `ghcr-pull` (SealedSecret / bootstrap `prodavan-ops ensure-ghcr-secret`).

---

## 1. Git: только PR

```text
ветка от main → gh pr create → CI Gate → Auto-merge squash
  → CI Images (push GHCR)
  → Argo sync / Verify Dev (wait + smoke)
```

Не `git push origin main`.

---

## 2. Bootstrap кластера (один раз)

```bash
k3d cluster create --config infra/k3d/prodavan-dev.yaml
# kubeconfig → infra/.kube/prodavan-k3d.yaml
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
cd infra/ops && poetry install
# seal & apply ghcr-pull (see overlays/dev/SECRETS.md) OR:
export GHCR_TOKEN=… GHCR_USERNAME=…
poetry run prodavan-ops ensure-ghcr-secret
poetry run prodavan-ops wait
poetry run prodavan-ops smoke
poetry run prodavan-ops seed
```

Terraform `environments/local` — только метаданные/outputs, **без** shell provisioners.

---

## 3. Reboot

1. k3s поднимается (systemd / install script — см. bootstrap).
2. Argo selfHeal → workloads.
3. PVC Retain → данные на месте.
4. Нет `recover_*.sh`. Если ImagePullBackOff: проверить `ghcr-pull` (SealedSecret / `prodavan-ops ensure-ghcr-secret`) — kubelet сам тянет из GHCR.

---

## 4. CI

| Workflow | Роль |
|----------|------|
| CI Gate | `poetry run prodavan-ops validate` + api/flutter/schemas |
| CI Images | buildx → GHCR `:latest` + SHA |
| Verify Dev | `wait` + `smoke` (не создаёт secret, не деплоит) |
| Auto-merge | squash после Gate |

---

## 5. API migrations

`initContainer: alembic upgrade head` в Deployment `prodavan-api`. Образ без shell entrypoint.
