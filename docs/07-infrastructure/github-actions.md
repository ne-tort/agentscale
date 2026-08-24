# GitHub Actions (as-built)

Репозиторий: [ne-tort/prodavan](https://github.com/ne-tort/prodavan) (private). Runner: **self-hosted** `linux,docker` (github-hosted `ubuntu-latest` в этой org ломается пустыми job’ами).

GitHub Free + private **не даёт branch protection** (403). Вместо required checks: workflow **CI Gate** на каждый PR и **Auto-merge** после зелёного Gate. Прямой push в `main` — только авария.

## Поток

```text
feature branch
  → PR в main
  → CI Gate: infra + API pytest + Flutter + schemas
  → Auto-merge (squash, удалить ветку)
       черновик или label do-not-merge → skip
  → dispatch CI Images  (GITHUB_TOKEN merge не триггерит push-workflows)
  → CI Images: build/push ghcr.io/<owner>/prodavan-api|web :latest и :SHA12
       overlay kustomize остаётся :latest (не бампать SHA в git)
  → Deploy Dev k3s: kubectl к API k3d (host.docker.internal), GHCR Always + rollout, smoke
```

Первый PR, который **добавляет** `auto-merge.yml`, мержить вручную: `workflow_run` читает workflow только с default branch.

Опционально secret `AUTO_MERGE_TOKEN` (PAT с `repo` + `workflow`): тогда merge от пользователя может триггерить native `push`. Без PAT Auto-merge сам делает `gh workflow run "CI Images"`.

## Workflows

| Файл | Когда | Что |
|------|--------|-----|
| `ci-gate.yml` | `pull_request` → `main` | kustomize, pins, terraform validate (Docker `hashicorp/terraform:1.9.8` если нет CLI) |
| `ci-api.yml` | `workflow_call` + `push` `apps/api/**` | Postgres **16.15**, alembic, ruff, **unit** pytest. Integration — `ci-nightly` (сейчас 19 красных на 16.15, I32) |
| `ci-flutter.yml` | `workflow_call` + `push` `apps/flutter/**` | analyze + test |
| `ci-schemas.yml` | `workflow_call` + `push` | `tools/validate_schemas.py` |
| `ci-images.yml` | `push`/`workflow_dispatch` на `main` (пути apps/packages) | GHCR `:latest` + `:SHA12`. На PR не собираем — один self-hosted runner, Gate важнее |
| `deploy-dev-k3s.yml` | успешный CI Images на `main` (не PR) | kubectl к workstation k3d; образы с GHCR, не k3d import |
| `auto-merge.yml` | успешный CI Gate (`pull_request`) | squash + dispatch Images |
| `ci-nightly.yml` | cron 02:00 UTC | интеграция API, Postgres 16.15 |

## Deploy: воспроизводимость

Раннер (Docker Desktop) и k3d (Docker в Kali WSL) часто **разные демоны**. Раннеру не нужно быть в кластере.

- **CI Images** пушит `:latest` в GHCR.
- **k3s** тянет GHCR (`imagePullPolicy: Always` + secret `ghcr-pull`).
- **Deploy** не делает `k3d image import` и не создаёт второй кластер. `attach_ci_kubeconfig.sh` ходит на `https://host.docker.internal:6443` (`tls-server-name: 127.0.0.1`) по `infra/.kube/prodavan-k3d.yaml` (пишет `ensure_k3d_cluster.sh` в том WSL, где k3d).
- Smoke с раннера: `http://host.docker.internal:8088/` (`Host: prodavan.local`).

Пока k3d запущен, цикл PR → merge → GHCR → rollout ручных шагов не требует. После reboot хоста — `recover_local_stack.sh` (кластер long-lived).

## Образы

- First-party API/web: overlay **`:latest`**, kubelet **Always**; CI дополнительно тегает immutable SHA.
- Third-party: замороженные теги (`verify_image_pins.sh`, `ci_infra_validate.sh`).
- Не SHA-пинить overlay — ImagePullBackOff на k3d без digest (I18).

## Настройки репозитория (ручные / `gh`)

```bash
gh api -X PATCH repos/ne-tort/prodavan \
  -f allow_squash_merge=true \
  -f allow_merge_commit=false \
  -f allow_rebase_merge=false \
  -f delete_branch_on_merge=true
```

Когда репозиторий станет public или появится GitHub Pro: включить branch protection на `main` (required **CI Gate** / job `gate`, no force-push). До тех пор контракт соблюдается workflow’ами и дисциплиной PR.

## Локальный аналог Gate

```bash
bash infra/scripts/ci_infra_validate.sh
# API tests — как в ci-api.yml (Postgres 16.15)
# Flutter: cd apps/flutter && flutter analyze && flutter test
python tools/validate_schemas.py
```
