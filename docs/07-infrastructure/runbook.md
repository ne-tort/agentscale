# Ранбук DevOps (Prodavan) — GitOps

Канон: **git → Argo CD → kustomize → k3s**.  
Императив только `poetry run prodavan-ops {validate|wait|smoke}`.  
**Запрещены:** `.sh` под `infra/`, docker-compose как кластер, k3d-конфиги в git, recover/deploy shell.

Репозиторий: [ne-tort/prodavan](https://github.com/ne-tort/prodavan).  
UI: `http://localhost:8088/`.

---

## 0. Топология

| Компонент | Где |
|-----------|-----|
| Кластер | **k3s** (kubelet + API). Workloads только из git. |
| Argo CD | `infra/argocd/install` → `root-app.yaml` → `apps/` → `infra/k3s/overlays/dev` |
| Sealed Secrets | `infra/argocd/sealed-secrets` + `overlays/dev/SECRETS.md` |
| GHA runners | **Docker Desktop** pack ×4 ([`infra/github-runner/README.md`](../../infra/github-runner/README.md)) |
| Ops CLI | [`infra/ops`](../../infra/ops): `validate` / `wait` / `smoke` |

Docker нужен **только** для сборки образов в CI Images (и опционально для самого runner-процесса). Кластер, Argo и validate от Docker **не зависят**.

Образы: `ghcr.io/ne-tort/prodavan-{api,web}:latest`, `imagePullPolicy: IfNotPresent`, secret `ghcr-pull` (SealedSecret).

---

## 1. Git: только PR

```text
ветка → PR → CI Gate → Auto-merge squash
  → CI Images (push GHCR)
  → Argo sync / Verify Dev (wait + smoke)
```

Не `git push origin main`.

---

## 2. Bootstrap кластера (один раз)

```bash
# 1) k3s уже установлен и работает (systemd). kubeconfig:
#    sudo cp /etc/rancher/k3s/k3s.yaml ~/.kube/prodavan-dev.yaml
#    # поправить server: https://127.0.0.1:6443 при необходимости
export KUBECONFIG=~/.kube/prodavan-dev.yaml

# 2) Control plane в git
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml

# 3) Secret pull (декларативно — см. overlays/dev/SECRETS.md)

# 4) Проверка
cd infra/ops && poetry install
poetry run prodavan-ops wait
poetry run prodavan-ops smoke
```

Day-2 деплой: **только** merge в `main` + Argo selfHeal. Не `kubectl apply -k infra/k3s/...` руками.

---

## 3. Reboot

1. k3s поднимается через systemd.
2. Argo selfHeal → workloads.
3. PVC на local-path остаются на диске узла (поды Recreate / STS пересоздаются).
4. Нет `recover_*.sh`. ImagePullBackOff → SealedSecret `ghcr-pull`, не image import.

**WSL / Kali:** не запускать **Docker Engine** внутри дистрибутива с k3s
(`systemctl disable --now docker` / `mask`) — иначе постоянные рестарты kube-proxy / NodeNotReady.
GHA runners — **Docker Desktop** на Windows (`infra/github-runner`), не docker в Kali.
Не оставлять **незалогиненный Tailscale** в том же WSL — netmon дергает CNI veth/routes.
**Не дергать `wsl --shutdown` / `wsl --terminate` во время тестов:** WSL шлёт `systemctl poweroff`,
k3s не успевает за 10s → `InitTerminateInstanceInternal` / force reboot → eth0 rename storm,
SandboxChanged, Traefik `:8088` пропадает. Для проверки персистентности — только
`sudo systemctl restart k3s` внутри Kali.
После осознанного `wsl --shutdown` / сна: поднять Kali, при необходимости
`sudo systemctl restart k3s`, с Windows — `tools/win-wsl-portforward.ps1`
или `infra/github-runner/Sync-KubeForDocker.ps1` (Admin).
Terraform `k3s_server`: **не** делает blind `systemctl restart k3s`, если сервис уже active
(HelmChartConfig/manifests подхватываются сами).

---

## 4. CI

| Workflow | Роль |
|----------|------|
| CI Gate | `prodavan-ops validate` + api/flutter/schemas |
| CI Images | buildx → GHCR (единственное легитимное использование Docker в поставке) |
| Verify Dev | `wait` + `smoke` (не деплоит, не создаёт secrets) |
| Auto-merge | squash после Gate |
