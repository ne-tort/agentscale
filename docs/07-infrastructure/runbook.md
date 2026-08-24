# Ранбук DevOps (Prodavan)

Канон поставки, CI и восстановления local k3d. Для агента это **единственный** ops-источник: ссылка из [`AGENTS.md`](../../AGENTS.md).

Репозиторий: [ne-tort/prodavan](https://github.com/ne-tort/prodavan) (private, GitHub Free).  
UI: `http://prodavan.local:8088/` (`Host: prodavan.local`). JWT: `bash infra/scripts/seed_dev_identity.sh`.

---

## 0. Топология (как есть на этой машине)

| Компонент | Где | Docker |
|-----------|-----|--------|
| Self-hosted GHA runner | **Kali WSL host** (`~/prodavan-actions-runner`, `start-kali-host.sh`) | Docker socket → dockerd Kali |
| k3d `prodavan-dev` | Kali WSL | тот же dockerd |
| ~~Runner в Docker (host-net)~~ | **не канон** | контейнер убивался mid-job → Session Conflict |
| ~~Runner на Docker Desktop~~ | **не использовать** | TLS handshake EOF к GitHub (DNS ок, TCP ок) |

Почему Kali host (не Docker Desktop): TLS к GitHub с Desktop/schannel падает. Почему не контейнер на host-net: listener стабильно умирал при старте job (Conflict). Канон: процесс на хосте Kali + `/var/run/docker.sock` для CI Postgres/terraform.

Запуск: `bash infra/github-runner/start-kali-host.sh` — см. [`infra/github-runner/README.md`](../../infra/github-runner/README.md).

Проверено 2026-08-24: `test_k3d_recover.sh` — stop → ensure → nodes Ready (~36s). Контейнеры k3d: `restart=unless-stopped`.

---

## 1. Git: только PR, не пуш в `main`

GitHub Free + private **не даёт branch protection** (403). Контракт всё равно обязателен.

### Как выкатывать изменение

```text
git fetch origin && git checkout -b feat/… origin/main
# … работа …
git push -u origin HEAD
gh pr create          # squash-only в настройках репо
```

Дальше **не мержить руками**, если Gate зелёный:

```text
PR → CI Gate (infra + API unit + Flutter + schemas)
  → Auto-merge squash + удаление ветки
  → dispatch CI Images
  → GHCR :latest + :SHA12
  → Deploy Dev k3s (kubectl к существующему k3d, secret ghcr-pull, rollout)
```

| Можно | Нельзя |
|-------|--------|
| Ветка `feat/` `fix/` `ci/` от `origin/main` | `git push origin main` |
| Draft PR / label `do-not-merge` — Auto-merge skip | Force-push в `main` |
| Ручной `gh pr merge --squash` если Auto-merge красный | Merge commit / rebase merge |

Шаблон PR: [`.github/pull_request_template.md`](../../.github/pull_request_template.md).

### Команды оператора (сводка)

```bash
git fetch origin && git checkout -b feat/my-change origin/main
git push -u origin HEAD
gh pr create --title "…" --body "…"
# ждать CI Gate; Auto-merge сам squash’ит
gh pr checks
```

Авария (runner мёртв, нужно хотфикс в git без ожидания Auto-merge): `gh pr merge --squash` **после зелёного Gate**, не прямой push.

---

## 2. CI workflows

| Workflow | Триггер | Роль |
|----------|---------|------|
| **CI Gate** | каждый PR в `main` | kustomize, пины образов, terraform validate, API **unit**, Flutter, schemas |
| **Auto-merge** | успешный CI Gate | squash + `gh workflow run "CI Images"` (`GITHUB_TOKEN` merge не триггерит `push`) |
| **CI Images** | `workflow_dispatch` / push `main` (apps/packages) | build/push GHCR. На PR **не** собираем (один runner) |
| **Deploy Dev k3s** | успешный CI Images **или** push манифестов/скриптов деплоя | attach API k3d, `ghcr-pull`, import если тот же Docker, rollout, smoke |
| **CI Nightly** | cron 02:00 UTC | integration pytest (I32 — часть красная) |

Self-hosted labels: `self-hosted, linux, docker`. github-hosted `ubuntu-latest` в этой org ломается.

As-built таблица: [`github-actions.md`](github-actions.md).

### Секреты

| Secret | Зачем |
|--------|--------|
| `GHCR_PULL_TOKEN` | PAT `read:packages` (лучше, чем одноразовый `GITHUB_TOKEN` в кластере) |
| `ARGOCD_REPO_TOKEN` | private git для Argo |
| `AUTO_MERGE_TOKEN` | опционально; иначе `GITHUB_TOKEN` + явный dispatch Images |

---

## 3. После ребута WSL / Docker Desktop

Это **единственная** штатная ручная операция. Не каждый PR, только когда хост/WSL/Docker умерли.

Из **Kali** (тот Docker, где k3d):

```bash
export PATH="$HOME/.local/bin:$PATH"
export KUBECONFIG=/mnt/c/Users/qwerty/git/Commerce/prodavan/infra/.kube/prodavan-k3d.yaml
export GHCR_TOKEN=…   # PAT read:packages, чтобы import :latest
bash infra/scripts/recover_local_stack.sh
```

Только кластер (без UI seed):

```bash
bash infra/scripts/ensure_k3d_cluster.sh
```

Проверка устойчивости (как gate):

```bash
# имитация ребута: k3d stop → ensure start → nodes Ready
bash infra/scripts/test_k3d_recover.sh
```

Раннер на Desktop:

```powershell
$env:DOCKER_CONTEXT = "desktop-linux"
cd c:\Users\qwerty\git\Commerce\prodavan\infra\github-runner
docker compose up -d
docker logs -f prodavan-gha-runner   # ждать Listening for Jobs
```

`ensure_k3d_cluster.sh` переписывает `infra/.kube/prodavan-k3d.yaml` (gitignored). Compose монтирует его в `/kube`. Deploy переписывает `127.0.0.1:6443` → `host.docker.internal:6443`.

Не лечить ребут повторным `terraform apply`: provisioner не перезапустится без смены inputs.

---

## 4. Как образы доезжают до k3s

```text
CI Images (Desktop runner) → ghcr.io/ne-tort/prodavan-api|web :latest
  → Kali: import_overlay_images.sh  (docker pull + k3d image import)
     или Deploy, если k3d в том же Docker, что и runner
  → rollout restart, IfNotPresent берёт слой с ноды
```

Если API в `ImagePullBackOff`:

1. Не ставить `Always` в overlay (ребут + TLS GHCR с ноды).
2. С Kali: `GHCR_TOKEN=… bash infra/scripts/import_overlay_images.sh`.
3. Обновить secret: `create_ghcr_pull_secret.sh`.

Third-party (postgres 16.15, redis 7.4.11-alpine, MinIO RELEASE, redpanda v24.2.4) — заморожены, не `:latest`.

---

## 5. Приёмка «девопс живой»

После PR в `main` (без ручного kubectl apply):

1. CI Gate зелёный, PR squash-merged.
2. CI Images зелёный (или пропуск, если не менялись apps — тогда образы прежние).
3. Deploy: attach k3d, не создаёт второй кластер.
4. `curl -H 'Host: prodavan.local' http://127.0.0.1:8088/health/live` → 200.

После ребута WSL: шаг 3 из раздела 3, затем тот же curl.

---

## 6. Чего не делать

- Пушить в `main`.
- SHA-пинить first-party в `overlays/dev` (I18 ImagePullBackOff).
- `k3d cluster create` с Desktop-раннера (второй кластер, порты 6443/8088).
- `kubectl apply -k` мимо `apply_overlay_safe.sh` (immutable Jobs).
- Ждать branch protection на Free private — его нет; Gate + Auto-merge это заменяют.

Когда репо станет public / появится Pro: required check = job **gate** у CI Gate, no force-push.
