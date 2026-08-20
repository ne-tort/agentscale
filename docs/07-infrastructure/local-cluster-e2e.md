# Local cluster E2E (k3d + Terraform + Argo + CI smoke)

Цель: на **Docker Desktop / WSL** прогнать цепочку без облачных VM.

```text
Terraform local → k3d (prodavan-dev)
CI Images → GHCR → bump overlays/dev
Argo CD Application prodavan-dev → sync
deploy-dev-k3s → curl smoke :8088 (Host: prodavan.local)
```

Compose (`docker-compose.stack.yml`) **не** обязателен для этой проверки. Раннер остаётся вне кластера (`infra/github-runner`).

---

## 1. Prerequisites

- Docker Desktop (WSL2 backend) + контекст `desktop-linux`
- В WSL или Git Bash: `kubectl`, `curl`; `k3d` поставится terraform/`install.sh`
- Terraform ≥ 1.5 (`tools/terraform.exe` в репо или системный `terraform`)
- Self-hosted runner с labels `self-hosted,linux,docker`

---

## 2. Bootstrap кластера

Из корня `prodavan` (предпочтительно WSL):

```bash
export GHCR_TOKEN=ghp_...          # read:packages (или PAT с packages)
export ARGOCD_REPO_TOKEN=ghp_...   # contents:read для private git (опционально)
bash infra/scripts/bootstrap_local_cluster.sh
```

Эквивалент по шагам:

```bash
cd infra/terraform/environments/local
terraform init && terraform apply -auto-approve
export KUBECONFIG="$(terraform output -raw kubeconfig_path)"
bash ../../../scripts/k3d_kubeconfig_for_runner.sh
bash ../../../scripts/bootstrap_local_cluster.sh   # или только argocd-bootstrap.sh
```

Порты k3d:

| Host | Cluster | Назначение |
|------|---------|------------|
| 8088 | 80 | Ingress HTTP (не конфликтует с compose `:8080`) |
| 8443 | 443 | HTTPS |
| 6443 | API | Kubernetes API |

Kubeconfig: `infra/.kube/prodavan-k3d.yaml` (gitignored).

---

## 3. GHCR pull secret

Секрет **не коммитится**:

```bash
export KUBECONFIG=infra/.kube/prodavan-k3d.yaml
kubectl create namespace prodavan --dry-run=client -o yaml | kubectl apply -f -
kubectl -n prodavan create secret docker-registry ghcr-pull \
  --docker-server=ghcr.io \
  --docker-username=ne-tort \
  --docker-password="$GHCR_TOKEN" \
  --dry-run=client -o yaml | kubectl apply -f -
```

Deployments в `overlays/dev` ссылаются на `imagePullSecrets: [ghcr-pull]` и
`imagePullPolicy: IfNotPresent` (устойчивее к TLS flakes; свежие теги тянет
`import_overlay_images.sh` на хосте → `k3d image import`).

---

## 4. Runner + kubeconfig

```powershell
$env:DOCKER_CONTEXT = "desktop-linux"
cd infra\github-runner
# после terraform — файл infra/.kube/prodavan-k3d.yaml должен существовать
docker compose up -d
```

Compose монтирует `../.kube` → `/kube` и задаёт `KUBECONFIG=/kube/prodavan-k3d.yaml` (host network → API `127.0.0.1:6443` без sudo).

---

## 5. CI цепочка

1. Push в `main` (пути apps/infra как в `ci-images.yml`) → **CI Images** → GHCR + bump tags в `infra/k3s/overlays/dev`.
2. После успешного Images → **Deploy Dev k3s** (`workflow_run`) на self-hosted:
   - `ensure_k3d_cluster.sh` (create **или start** после ребута) + optional terraform state
   - `ensure_argocd.sh` → `import_overlay_images.sh` → `wait_prodavan_ready.sh`
   - `smoke_ingress.sh` на `:8088` с `Host: prodavan.local`
3. Ручной прогон: Actions → Deploy Dev k3s → `workflow_dispatch`.

Опциональные secrets: `GHCR_PULL_TOKEN`, `ARGOCD_REPO_TOKEN`.

---

## 5b. После перезагрузки WSL / Docker Desktop

k3d хранит данные в Docker volumes; контейнеры получают `restart=unless-stopped`.
Если API всё же недоступен (server `Exited`, `0/1`):

```bash
# полный recover (ensure → secret/import → wait → smoke)
export GHCR_TOKEN=ghp_...   # optional but recommended
bash infra/scripts/recover_local_stack.sh
```

Только кластер:

```bash
bash infra/scripts/ensure_k3d_cluster.sh
export KUBECONFIG=infra/.kube/prodavan-k3d.yaml
```

Проверка recover:

```bash
# nodes only
bash infra/scripts/test_k3d_recover.sh
# + workloads + HTTP smoke (нужны уже импортированные образы)
TEST_WORKLOADS=1 bash infra/scripts/test_k3d_recover.sh
```

Не полагайтесь на повторный `terraform apply` как на recover: provisioner не перезапускается, если inputs не менялись. Source of truth runtime — `ensure_k3d_cluster.sh` / `recover_local_stack.sh`.

---

## 6. Проверка вручную

```bash
export KUBECONFIG=infra/.kube/prodavan-k3d.yaml
kubectl -n prodavan get pods
curl -sS -H 'Host: prodavan.local' http://127.0.0.1:8088/health
curl -sS -H 'Host: prodavan.local' http://127.0.0.1:8088/ | head
# опционально в hosts: 127.0.0.1 prodavan.local → браузер :8088
```

---

## Troubleshooting

| Проблема | Что проверить |
|----------|----------------|
| ImagePullBackOff | `ghcr-pull` secret, `read:packages`, тег в overlay |
| Argo ComparisonError / repo | `ARGOCD_REPO_TOKEN` или fallback `kubectl apply -k` |
| Port already allocated | compose на `:8080` ок; k3d занимает `:8088` |
| Runner session conflict | новый `RUNNER_NAME`, wipe volume `runner-home` |
| kubectl permission denied | не использовать `/etc/rancher/k3s`; только `infra/.kube/...` |
| Smoke 404 Host | заголовок `Host: prodavan.local` обязателен |
| После reboot API down | `bash infra/scripts/recover_local_stack.sh` |
| Server Exited (128) | ensure детектит unhealthy + `k3d cluster start` / `docker start` |
| Nodes NotReady | ensure делает start + API wait + retry |
| Smoke сразу после start | `smoke_ingress.sh` ретраит до ~2 мин |
| ImagePullBackOff (rancher/*) | `bash infra/scripts/warm_k3d_base_images.sh` (host pull + `k3d image import`) |
| TLS handshake timeout docker.io | warm script с ретраями; VPN/прокси; повторить ensure |

---

## Связанные файлы

- Ensure: `infra/scripts/ensure_k3d_cluster.sh`, `recover_local_stack.sh`, `ensure_argocd.sh`, `wait_prodavan_ready.sh`
- Terraform: `infra/terraform/environments/local`, `infra/terraform/modules/k3s-local`
- Argo: `infra/argocd/apps/prodavan-dev.yaml`
- Workflow: `.github/workflows/deploy-dev-k3s.yml`
- Runner: `infra/github-runner/README.md`
