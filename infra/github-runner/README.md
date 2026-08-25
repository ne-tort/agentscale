# Self-hosted GitHub Actions runners — **Docker Desktop**

## Сколько раннеров

| Workflow | Параллельные job'ы |
|----------|-------------------|
| **CI Gate** | `infra` + `api` + `flutter` + `schemas` = **4** (потом `gate`) |
| CI Images | `build-api` + `build-web` = 2 |
| Verify Dev | 1 (`wait-smoke`) |

**Канон: 4 реплики** (`RUNNER_REPLICAS=4`) — закрывает пик Gate. Больше 4 на одном ПК обычно не нужно (CPU/RAM + Docker builds).

Управление: Docker Desktop UI / `docker compose` из Windows. Старый host/WSL runner (`wsl-prodavan-host`) **снят**.

## Quick start (Windows)

1. Docker Desktop запущен (context `desktop-linux`).
2. Токен:

```powershell
cd infra/github-runner
copy .env.example .env
# В .env:
# ACCESS_TOKEN=<gh auth token с scope repo>
```

```powershell
gh auth token   # вставить в .env как ACCESS_TOKEN
```

3. (Verify Dev / smoke) один раз после reboot WSL:

```powershell
.\Sync-KubeForDocker.ps1   # Admin: kubeconfig + portproxy 6443/8088
```

4. Старт пака:

```powershell
.\Start-Runners.ps1        # Admin не обязателен, если docker без elevation
# или:
docker compose up -d --scale runner=4
```

5. Проверка:

```powershell
docker compose ps
gh api repos/ne-tort/prodavan/actions/runners --jq '.runners[]|{name,status,busy,labels:[.labels[].name]}'
```

Остановить: `docker compose down` (кэш **сохраняется**)  
Снести кэш: `docker compose down -v`  
Логи: `docker compose logs -f`

## Persistent cache (`prodavan-ci-cache` → `/cache`)

Общий named volume на все 4 реплики (раннеры только под Prodavan):

| Path | Что |
|------|-----|
| `/cache/flutter-sdk` | Flutter stable SDK (clone once) |
| `/cache/pub` | `PUB_CACHE` |
| `/cache/poetry-cli` | Poetry CLI (venv bootstrap) |
| `/cache/poetry` + `/cache/poetry-venvs` | Poetry cache + project virtualenvs |
| `/cache/pip` | pip wheel cache (api) |
| `/cache/toolcache` | `RUNNER_TOOL_CACHE` |

В логах Gate/Flutter ищи `cache HIT` / `cache MISS`.

## Сеть Docker Desktop

Раннеры монтируют `docker.sock` Desktop-движка. Job'ы публикуют порты на Desktop VM; из контейнера раннера хост — **`host.docker.internal`** (env `PRODАVAN_CI_HOST` в compose).

- API tests Postgres: `host.docker.internal:55432`
- Smoke / k3s: Windows `portproxy` 8088/6443 → WSL (скрипт `Sync-KubeForDocker.ps1`)

## Labels

`self-hosted,linux,docker,docker-desktop` — workflows используют `[self-hosted, linux, docker]`.

## Файлы

| Файл | Назначение |
|------|------------|
| `docker-compose.yml` | сервис `runner`, scale N, volume `prodavan-ci-cache` |
| `.env.example` | токены / replicas |
| `Start-Runners.ps1` | `compose up --scale` |
| `Sync-KubeForDocker.ps1` | kubeconfig + portproxy для Verify |

Image: local build `prodavan-github-runner:py312` (FROM myoung34 jammy + **Python 3.12** — api/ops требуют `>=3.12`).

## Ops CLI (локально в WSL, не в runner)

```bash
cd infra/ops && poetry install
poetry run prodavan-ops validate
poetry run prodavan-ops wait
poetry run prodavan-ops smoke
```
