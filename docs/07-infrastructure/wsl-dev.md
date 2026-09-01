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

UI: `http://localhost:8088/`.

**После reboot WSL (Win10):** k3s поднимается через systemd; drop-in `prodavan-boot-heal` (Terraform) маскирует Docker, чистит stuck pods, пересоздаёт Traefik и ждёт `:8088`. На Windows:

```powershell
# Держит WSL живым (иначе InitTerminate гасит k3s)
powershell -File tools/win-wsl-keepalive.ps1
# Проброс 127.0.0.1:8088 → WSL (если NAT не пробросил сам)
powershell -ExecutionPolicy Bypass -File tools/win-wsl-portforward.ps1
```

В WSL вручную (если UI всё ещё мёртв):

```bash
export KUBECONFIG=~/.kube/prodavan-dev.yaml
cd ~/git/prodavan/infra/ops && poetry run prodavan-ops heal
```

Первый apply после merge: `cd infra/terraform/environments/local && terraform apply` (ставит скрипты в `/usr/local/lib/prodavan/`).

**Windows browser (Win10 + WSL2):** Traefik слушает `0.0.0.0:8088` в WSL. Ingress без `host` — любой Host (`localhost` / `127.0.0.1`). Если `http://localhost:8088/` не открывается (NAT без localhostForwarding), Admin PowerShell:

```powershell
.\tools\win-wsl-portforward.ps1
```

Скрипт сам пробросит `127.0.0.1:8088 → WSL` и откроет браузер. `hosts` / `prodavan.local` не нужны.

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
| Windows: `localhost:8088` connection refused | `tools/win-wsl-keepalive.ps1`; подожди до 6 мин; Admin `.\tools\win-wsl-portforward.ps1`; WSL `prodavan-ops heal` |
| k3s flaps / NodeNotReady after reboot | `terraform apply` (boot-heal drop-in); mask docker in WSL; `prodavan-ops heal` |
| API pod Terminating, 502 | `prodavan-ops heal` (force-delete stuck); dev CronJob `prodavan-cluster-heal` |
| kubectl connection refused | k3s running? `sudo systemctl status k3s` |
| Argo OutOfSync | merge to `main`; check Application `prodavan-dev` |
| ImagePullBackOff (platform or project pod) | `ghcr-pull` в **`prodavan`** и **`prodavan-sandboxes`** — [`SECRETS.md`](../../infra/k3s/overlays/dev/SECRETS.md) |
| Flutter: «metrics-server недоступен» на контейнере | См. [k8s metrics-server](#k8s-metrics-server) ниже |

Legacy compose/k3d/bootstrap scripts **removed** — do not restore.

---

## k8s metrics-server

**Не путать с Prodavan Metrics BC:** отдельного Deployment `prodavan-metrics` нет. CPU/RAM pod'ов читает `prodavan-api` через **cluster addon** `metrics-server` (namespace `kube-system`, k3s ставит по умолчанию).

Проверка в WSL:

```bash
export KUBECONFIG=~/.kube/prodavan-dev.yaml
kubectl get deployment -n kube-system metrics-server
kubectl top pods -n prodavan-sandboxes   # нужны running sandbox pod'ы
```

RBAC для API: `infra/k3s/base/prodavan-sandbox/rbac-sandboxes.yaml` — `metrics.k8s.io/pods` get/list.

Prodavan pipeline: `pod_service` sampler → Kafka `prodavan.metrics.events` → Metrics BC (Redis) → REST для UI. Health gate — k8s Ready (`get_status` / `wait_ready`), **не** metrics-server. Если metrics-server недоступен, UI показывает warning-баннер; pod/project остаются healthy, reconcile продолжает работать.

Канон: [`docs/target/14-project-containers/k3s-runtime/metrics-observability.md`](../target/14-project-containers/k3s-runtime/metrics-observability.md).
