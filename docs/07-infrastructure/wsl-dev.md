# WSL Development Environment

> **Dev cluster:** только GitOps — [`runbook.md`](runbook.md).  
> Нет `docker-compose` кластера, нет k3d, нет `infra/scripts/`.  
> API/Flutter можно гонять локально без k3s (см. ниже).

Локальная разработка Prodavan на **Windows + WSL2**.

---

## Architecture (dev)

```text
Windows Host
├── Cursor IDE / VS Code
├── Flutter SDK (Windows) → apps/flutter
└── WSL2 Ubuntu
    ├── apps/api (FastAPI) — optional local uvicorn
    ├── k3s (systemd) + Argo CD — единственный путь деплоя
    └── git checkout → ~/git/prodavan (не /mnt/c/)
```

**Recommendation:** clone inside WSL (`~/git/prodavan`) for file watcher performance.

---

## Full stack on k3s (canonical)

См. [`runbook.md`](runbook.md) и [`local-cluster-e2e.md`](local-cluster-e2e.md).

```bash
export KUBECONFIG=~/.kube/prodavan-dev.yaml
kubectl apply -k infra/argocd/install
kubectl apply -k infra/argocd/sealed-secrets
kubectl apply -f infra/argocd/root-app.yaml
cd infra/ops && poetry install
poetry run prodavan-ops wait && poetry run prodavan-ops smoke
```

UI: `http://prodavan.local:8088/` (`Host: prodavan.local`).

**Windows browser:** в `hosts` одна строка `127.0.0.1 prodavan.local`. Traefik слушает **0.0.0.0:8088** в WSL (`hostNetwork` + `hostPort`). На Win10 WSL2 NAT `localhostForwarding` часто **не пробрасывает** порт — скрипт сам настроит portproxy и откроет браузер (IP WSL вручную не нужен):

```powershell
.\tools\win-wsl-portforward.ps1
```

После reboot WSL подожди 2–3 мин, пока k3s и Argo поднимут workloads. Не привязывай `prodavan.local` к IP WSL в `hosts`.

---

## API / Flutter without cluster

```bash
cd apps/api && pip install -e ".[dev]" && uvicorn prodavan.main:app --reload --port 8000
cd apps/flutter && flutter pub get && flutter run -d chrome
```

---

## WSL prerequisites

```powershell
wsl --install -d Ubuntu-22.04
```

In WSL: `git`, `python3`, `poetry`, `kubectl`, `kustomize` on PATH for CI parity.

Runner (outside k3s): [`infra/github-runner/README.md`](../../infra/github-runner/README.md).

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| Windows: `prodavan.local:8088` connection refused | Подожди 2–3 мин после boot WSL; `systemctl status k3s`; `.wslconfig`: `localhostForwarding=true` |
| kubectl connection refused | k3s running? `sudo systemctl status k3s` |
| Argo OutOfSync | merge to `main`; check Application `prodavan-dev` |
| ImagePullBackOff | SealedSecret `ghcr-pull` — [`SECRETS.md`](../../infra/k3s/overlays/dev/SECRETS.md) |

Legacy compose/k3d/bootstrap scripts **removed** — do not restore.
