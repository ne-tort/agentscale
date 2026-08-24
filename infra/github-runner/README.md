# Self-hosted GitHub Actions runner (OUTSIDE k3s)

## Канон: процесс на Kali WSL (не Docker)

На этой машине:

1. **Docker Desktop** — TLS к GitHub ломается (`SSL connection could not be established` / EOF). DNS при этом резолвит — проблема не в nameserver.
2. **Контейнер на Kali `network_mode: host`** — TLS OK, но listener **умирает mid-job** → GitHub `Session Conflict` / offline busy.
3. **Хост Kali** — TLS OK, jobs стабильны. Docker-задачи (Postgres CI, terraform image) идут через `/var/run/docker.sock`.

```bash
# из Kali
export PATH=/home/www/.local/bin:/usr/bin:/bin
cd /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/github-runner
# .env: REPO_URL + ACCESS_TOKEN, RUNNER_NAME=wsl-prodavan-host
# jq в ~/.local/bin; Python через venv в workflow (PEP 668)
bash start-kali-host.sh
tail -f ~/prodavan-actions-runner/runner.out
```

Ожидать: `Listening for Jobs` на `2.336.0`.  
`DISABLE_RUNNER_UPDATE=1` в `~/prodavan-actions-runner/.env`.

Тот же Docker, что и k3d → Deploy: `k3d image import`, kubectl `127.0.0.1:6443`.

## Fallback: Docker host-net (только если host runner недоступен)

`bash start-kali.sh` — см. скрипт; не канон на этой машине.

## Не использовать Docker Desktop для runner

`desktop-linux` + host net: SSL EOF к github.com / broker.actions.githubusercontent.com.

## Session conflict

Если GitHub показывает runner busy/offline и `A session for this runner already exists`:

1. Не крутить cancel-in-progress на единственном runner (в Gate уже `false`).
2. Новый `RUNNER_NAME`, удалить offline runner через API, подождать ~3–5 мин.
3. `gh api repos/ne-tort/prodavan/actions/runners`.

## Token

```powershell
gh api -X POST repos/ne-tort/prodavan/actions/runners/registration-token --jq .token
# или ACCESS_TOKEN= (PAT, manage runners) в .env
```

## Verify

```bash
gh api repos/ne-tort/prodavan/actions/runners --jq '.runners[] | {name,status,busy}'
docker exec prodavan-gha-runner curl -fsS -o /dev/null -w '%{http_code}\n' https://api.github.com/zen
```

## После reboot WSL

```bash
# Kali dockerd + k3d
bash infra/scripts/ensure_k3d_cluster.sh
export PATH=/usr/bin:/bin
docker start prodavan-gha-runner || (cd infra/github-runner && bash start-kali.sh)
```

Ранбук: [`docs/07-infrastructure/runbook.md`](../../docs/07-infrastructure/runbook.md).
