# Ранбук DevOps (Prodavan) — GitOps

Канон поставки: **git → Argo CD → kustomize → k3s**.  
Императив только `poetry run prodavan-ops {validate|wait|rollout|smoke}`.  
**Запрещены:** `.sh` под `infra/`, docker-compose как кластер, k3d в git, recover/deploy shell.

Репозиторий: [ne-tort/prodavan](https://github.com/ne-tort/prodavan).  
**UI:** http://127.0.0.1:8088/ (Traefik слушает `0.0.0.0:8088`, Ingress без фильтра `host`).  
После bootstrap — единая форма логин/пароль (Keycloak ROPC). Platform Admin: `admin` / `admin`. Issuer: `http://127.0.0.1:8089/realms/prodavan` (hostPort).

---

## 0. Топология

| Компонент | Где |
|-----------|-----|
| Кластер | **k3s** на SSH-хосте. Workloads только из git. |
| Argo CD | `infra/argocd/install` → `root-app.yaml` → `apps/` → `infra/k3s/overlays/dev` |
| Sealed Secrets | `infra/argocd/sealed-secrets` + `overlays/dev/SECRETS.md` |
| Ops CLI | [`infra/ops`](../../infra/ops): `validate` / `wait` / `rollout` / `smoke` |

Образы: `ghcr.io/ne-tort/prodavan-{api,web}:latest` (основной репо, CI Images), `ghcr.io/ne-tort/prodavan-agent-runtime:latest` (агент-runtime — его собирают **два** пайплайна: openclaw-images в подмодуле prodavan-claw **и** build-agent-runtime в CI Images основного репо; см. [agent-runtime-delivery.md](agent-runtime-delivery.md)). `imagePullPolicy: Always`, secret `ghcr-pull`. Probe pod в `prodavan-sandboxes` (см. §1.1).

---

## 1. Git: только PR

```text
ветка → PR → CI Gate → Auto-merge squash
  → CI Images (GHCR)
  → Verify Dev (rollout + wait + smoke)
```

Не `git push origin main`.

**Миграции Alembic** — только через initContainer при деплое API; см. [alembic.md](alembic.md). Ручной `alembic upgrade` / `kubectl exec` на shared env запрещён.

### 1.1 Agent-runtime (probe pod) — отдельный поток

Образ `prodavan-agent-runtime` исторически собирается в **подмодуле `prodavan-claw`** (workflow `openclaw-images.yml`, после auto-merge PR в claw). С 2026-09 его **также** собирает CI Images основного репо — job `build-agent-runtime` в `ci-images.yml` (paths: `prodavan-claw/**`, `.gitmodules`, …): клонирует claw по указателю сабмодуля, buildx local-cache, smoke (артефакты + `/health`). Оба пайплайна пушат один тег `:latest` — это задокументированная дыра №1 в [agent-runtime-delivery.md](agent-runtime-delivery.md) (карта дыр и бэклог фиксов — там же).

```text
prodavan-claw PR → openclaw-ci → auto-merge
  → openclaw-images (build prodavan-agent-runtime:latest → GHCR)
  → trigger-verify (dispatch Verify Dev in ne-tort/prodavan)
    → Verify Dev: prodavan-ops rollout
      → restart prodavan-probe-pod (prodavan-sandboxes, imagePullPolicy: Always)
        → kubelet re-pulls :latest digest
      → wait Argo → smoke
```

**Probe pod** (`prodavan-probe-pod` в namespace `prodavan-sandboxes`) — это long-lived agent-runtime pod для проверки AI-ключей. Включён в `DEPLOYMENT_TARGETS` `prodavan-ops` (вместе с api/web/celery-worker/celery-beat), поэтому `prodavan-ops rollout` перезапускает и его. `imagePullPolicy: Always` → при restart тянется свежий digest.

**Project sandbox pods** (динамические, per-project, создаются `pod_service` через `POD_AGENT_RUNTIME_IMAGE` env) — при создании нового sandbox pod подхватит свежий digest (Always + новый pod = новый pull). Уже запущенные sandbox pods обновляются при reload/sync проекта (recreate).

**Для cross-repo trigger** нужен секрет `PRODAVAN_REPO_TOKEN` в `prodavan-claw` репо (PAT с `actions:write` на `ne-tort/prodavan`). Без него trigger-verify выводит warning (образ запушен, но кластер подхватит при следующем Verify Dev из основного репо — теперь probe-pod в `DEPLOYMENT_TARGETS`, так что он тоже перезапустится).

---

## 2. Bootstrap кластера (канон)

**Только два шага: SSH-хост + Terraform.**

```bash
cd infra/terraform/environments/local
export TF_VAR_ghcr_token="$(gh auth token)"
terraform init
terraform apply -auto-approve
# UI:
#   http://127.0.0.1:8088/  → Sign in → Demo Employee / Platform Admin
```

Подробности хоста/ключа: [`infra/terraform/environments/local/README.md`](../../infra/terraform/environments/local/README.md).

Day-2: merge в `main` + Argo. Не `kubectl apply -k infra/k3s/...` руками.

---

## 3. Reboot / хост

1. k3s → systemd.  
2. Argo selfHeal.  
3. PVC local-path на диске узла.  
4. ImagePullBackOff → `ghcr-pull`, не image import.

Не ставить **Docker Engine** в тот же OS, где k3s (маскировать docker).  
Не оставлять незалогиненный Tailscale рядом с CNI.

---

## 4. CI

| Workflow | Роль |
|----------|------|
| CI Gate | `prodavan-ops validate` (+ pytest ops) + api/flutter/schemas |
| CI Images | buildx → GHCR |
| Verify Dev | `rollout` + `wait` + `smoke` |
| Auto-merge | squash после Gate |

Self-hosted runners и kubeconfig для Verify — только [`infra/github-runner/`](../../infra/github-runner/README.md) (не bootstrap UI).

---

## 5. MinIO (object store)

| Компонент | Значение |
|-----------|----------|
| Bucket | `prodavan` (private, versioning off) |
| Init Job | `prodavan-minio-init` — **Sync hook, wave 9** (до `prodavan-api` wave 10); bucket + IAM user `prodavan-api` |
| API creds | `S3_ACCESS_KEY=prodavan-api` в `prodavan-api-secrets` |
| Root creds | `prodavan-minio` Secret — только init / break-glass |
| PVC | `prodavan-minio-data` (10Gi, RWO) — данные переживают pod reschedule |

**Ротация API creds (dev/staging):**

1. Сгенерировать новый пароль; обновить `prodavan-minio` Secret (`api-password`) и `prodavan-api-secrets` (`S3_SECRET_KEY`) в git (SealedSecret на shared).
2. Argo sync → init Job пересоздаёт/обновляет user policy (idempotent).
3. Rollout `prodavan-api`; smoke `/health/ready`.

**Verify PVC retain:** `prodavan-ops validate` проверяет наличие PVC `prodavan-minio-data` в overlay render.

API **не** вызывает `create_bucket` на startup — только `head_bucket` health.

---

## 5b. MongoDB (Document Store)

| Компонент | Значение |
|-----------|----------|
| Service | `prodavan-mongodb:27017` (ns `prodavan`) |
| Init Job | `prodavan-mongodb-init` — Sync hook wave 9; app user `prodavan` + db `prodavan` |
| API creds | `MONGODB_URL` в `prodavan-api-secrets` (app user, `authSource=admin`) |
| Root creds | `prodavan-mongodb` Secret — только init / break-glass |
| PVC | `prodavan-mongodb-data` (5Gi, RWO) |
| Sandbox | **нет** egress на 27017 — только API process |

Document Store BC: in-proc Port + admin HTTP; Kafka bus `document` → `prodavan.document.events`.

---

## 5c. OpenSearch (Search Index)

| Компонент | Значение |
|-----------|----------|
| Service | `prodavan-opensearch:9200` (ns `prodavan`) |
| Init Job | `prodavan-opensearch-init` — Sync hook wave 9; smoke `/_cluster/health` |
| API | `OPENSEARCH_URL`, `OPENSEARCH_ENABLED`, `OPENSEARCH_REQUIRED` in `prodavan-config` |
| Security | MVP: `DISABLE_SECURITY_PLUGIN=true`; ACL in Search Index BC |
| PVC | `prodavan-opensearch-data` (10Gi, RWO) |
| Sandbox | **нет** egress на 9200 — только API process |

Search Index BC: in-proc Port + admin HTTP `/admin/search-index`; Kafka bus `search` → `prodavan.search.events`.

---

## 6. Keycloak (identity)

| Компонент | Значение |
|-----------|----------|
| Public issuer | `http://127.0.0.1:8089/realms/prodavan` (Keycloak hostPort 8089) |
| In-cluster Admin | `http://prodavan-keycloak:8080` |
| Init Job | `prodavan-keycloak-init` — Sync hook wave 8 (до API); SA roles + user `admin`/`admin` |
| Platform Admin | username `admin`, password `admin`, realm role `platform.admin` |
| API | `AUTH_MODE=oidc`, `KEYCLOAK_INVITE_MODE=admin`, JWKS via in-cluster URL |

Flutter login: ROPC (`grant_type=password`) на client `prodavan-flutter`. One-click test personas удалены.

