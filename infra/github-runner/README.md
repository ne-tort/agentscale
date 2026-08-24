# Self-hosted GitHub Actions runner (OUTSIDE k3s)

## Канон: Kali WSL docker + `network_mode: host`

На этой машине **Docker Desktop host-net ломает TLS к GitHub**: TCP к `:443` есть, handshake обрывается (`unexpected eof` / runner `SSL connection could not be established`). DNS при этом резолвит нормально — проблема не в nameserver.

Kali host и контейнер с `--network host` на Kali: `https://api.github.com/zen` → 200.

```bash
# из Kali (PATH без Docker Desktop wrappers)
export PATH=/usr/bin:/bin:/usr/sbin:/sbin
cd /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/github-runner
# .env: ACCESS_TOKEN или RUNNER_TOKEN, RUNNER_NAME=wsl-prodavan-kali3
# Скрипт: build --network=host, preload 2.336.0, fresh volume, DISABLE_RUNNER_UPDATE
bash start-kali.sh
docker logs -f prodavan-gha-runner
```

Ожидать: `GitHub TLS OK`, затем `Listening for Jobs` на версии `2.336.0`.

Тот же Docker, что и k3d → Deploy может `k3d image import`, kubectl на `127.0.0.1:6443`.

Entrypoint: `FORCE_PUBLIC_DNS` (1.1.1.1/8.8.8.8), `wait_github_tls`, пишет `DISABLE_RUNNER_UPDATE=1` в `/opt/actions-runner/.env` (docker `-e` недостаточно).

## Не использовать Docker Desktop для runner на этой машине

`desktop-linux` + host net: SSL EOF к github.com / broker.actions.githubusercontent.com.  
Если когда-нибудь Desktop TLS починится — можно снова, но сейчас канон = Kali.

## Session conflict

Если GitHub показывает runner busy/offline и лог `A session for this runner already exists`:

1. Cancel stuck workflow run.
2. Новый `RUNNER_NAME` в `.env`, wipe volume `github-runner_runner-home`.
3. Удалить offline runners: `gh api repos/ne-tort/prodavan/actions/runners`.

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
