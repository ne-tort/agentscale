# Self-hosted GitHub Actions runners — Docker Desktop (**CI only**)

> **Переехали на VM:** раннеры перенесены на выделенную Linux-VM
> (нативные systemd-сервисы, persistent buildx builder, общий `/cache`).
> Актуальная директория — [`../github-runner-vm/`](../github-runner-vm/).
> Этот каталог — исторический (Docker Desktop / WSL), `docker compose` сюда
> больше не поднимается; порядок вывода dd-* раннеров из эксплуатации —
> в конце README `github-runner-vm`.


Не bootstrap. После `terraform apply` UI: **http://127.0.0.1:8088/**  
( Traefik `0.0.0.0:8088`, Ingress без `host` — браузеру kubeconfig/portproxy не нужны. )

Этот каталог — только self-hosted runners для Gate / Images / Verify на ноутбуке.

## Сколько раннеров

| Workflow | Параллельные job'ы |
|----------|-------------------|
| **CI Gate** | infra + api + flutter + schemas = **4** |
| CI Images | build-api + build-web = 2 |
| Verify Dev | 1 |

Канон: **4 named services** `runner-1..4` (не `docker compose --scale`).

## Почему дохли после рестарта WSL / Docker Desktop

Образ `myoung34/github-runner` при старте **переконфигурирует** раннер, если нет
`CONFIGURED_ACTIONS_RUNNER_FILES_DIR` с сохранённым `.runner`.

`--scale 4` даёт четыре контейнера с **общим** (или пустым) registration state → после
жёсткого рестарта Docker Desktop:

1. локальный `.runner` «уже есть», но `configuredSettings` битый;
2. deregister падает;
3. контейнер exit 2 → `restart: unless-stopped` → crash-loop;
4. на GitHub остаются **offline** `dd-agentscale-*`.

Фикс: у каждого сервиса свой `RUNNER_NAME` + volume `agentscale-runner-N-files` на `/runner-files`
и **`DISABLE_AUTOMATIC_DEREGISTRATION=true`** (без него myoung34 делает `exit 1` сразу после
«Storing data to /runner-files»).

## Quick start

```powershell
cd infra/github-runner
copy .env.example .env   # ACCESS_TOKEN=gh auth token
.\Start-Runners.ps1
```

Скрипт:

1. Пишет явный DNS в `%USERPROFILE%\.docker\daemon.json` (`Ensure-DockerDns.ps1`) — нужен **restart Docker Desktop**, если DNS менялся.
2. Синкает kubeconfig + portproxy (`Sync-KubeForDocker.ps1`).
3. Нормализует `EPHEMERAL=` (см. ниже) и поднимает `runner-1..4`.
4. Удаляет offline registrations на GitHub.

После reboot / WSL restart:

```powershell
.\Ensure-RunnersHealthy.ps1
# или просто tools\win-wsl-keepalive.ps1 — он вызывает Ensure
```

Для **Verify Dev** (kubectl к API k3s из контейнера): Sync или `TF_VAR_export_docker_kubeconfig=true` при apply.  
Это **не** нужно, чтобы открыть страницу в браузере.

```powershell
.\Sync-KubeForDocker.ps1   # optional, CI Verify only
docker compose ps
gh api repos/ne-tort/agentscale/actions/runners --jq '.runners[]|{name,status,busy}'
```

Стоп: `docker compose down` · wipe cache+reg: `docker compose down -v`  
Reset только registration: `$env:PRODAVAN_RUNNER_RESET_REG=1; .\Start-Runners.ps1`

## prodavan-claw runners

Private `ne-tort/prodavan-claw` больше не получает GitHub-hosted runner (job 0 steps / 3s fail).
CI/images там тоже `runs-on: [self-hosted, linux, docker]`.

```powershell
docker compose --profile claw up -d claw-runner-1 claw-runner-2
gh api repos/ne-tort/prodavan-claw/actions/runners --jq '.runners[]|{name,status,busy}'
```

Поставка claw: `openclaw-ci` (PR) → Auto-merge squash → `openclaw-images` → GHCR
`ghcr.io/ne-tort/prodavan-agent-runtime:latest` (dev ConfigMap `POD_AGENT_RUNTIME_IMAGE`).
После нового `:latest` — **reload** project Pod (imagePullPolicy Always).

## DNS

| Путь | Кто резолвит | Где |
|------|--------------|-----|
| **Runner container** (gh, pip, curl) | compose `dns:` → ExtServers `8.8.8.8` / `1.1.1.1` | `docker-compose.yml` |
| **`docker build` / sibling** via socket | Docker **embedded** DNS (Desktop `192.168.65.7`) | default — **do not** override in `daemon.json` |

`Ensure-DockerDns.ps1` **снимает** вредный `daemon.json` `dns: [8.8.8.8,…]`: публичные резолверы обходят embedded DNS и ломают `host.docker.internal` (migration smoke / portproxy).

Явный DNS только у runner-контейнеров. Sibling-контейнеры CI Images migration smoke ходят по user-defined bridge (имя Postgres), не через `host.docker.internal`.

Проверка:

```powershell
docker compose exec runner-1 cat /etc/resolv.conf
# ExtServers: [8.8.8.8 1.1.1.1]

docker run --rm alpine getent hosts host.docker.internal
# must resolve (embedded DNS)
```

`dns_opt: timeout:2, attempts:3, use-vc` — устойчивость к UDP-дропам на Desktop.

## EPHEMERAL (ловушка myoung34)

Образ [`myoung34/github-runner`](https://github.com/myoung34/docker-github-actions-runner): **любая непустая** `EPHEMERAL` включает `--ephemeral`.

```text
EPHEMERAL=false   →  всё равно ephemeral (!)
EPHEMERAL=        →  persistent (нужно нам)
EPHEMERAL=1       →  one-job + exit (только если сознательно)
```

С `EPHEMERAL=false` раннеры после job умирают, `restart: unless-stopped` перерегистрирует их → очередь CI «висит», в GitHub копятся **offline** runners. `Start-Runners.ps1` чистит `.env` и offline IDs.

В логах должно быть **Listening for Jobs** и **не** должно быть `Ephemeral option is enabled`.

## Session Conflict после hard-kill

`DISABLE_AUTOMATIC_DEREGISTRATION=true` обязателен вместе с persist-volume, но после
жёсткого kill (Docker Desktop / WSL) GitHub ещё держит старую session → в логах
`A session for this runner already exists` / `Conflict. Retrying…`.

`Ensure-RunnersHealthy.ps1` (из keepalive) детектит это по логам за последние 5 минут
и делает wipe registration volumes + re-register. Не делай `docker compose restart`
пока runners busy — лучше дождаться idle или вызвать Ensure.

| Что | Где |
|-----|-----|
| Общий CI cache | volume `agentscale-ci-cache` → `/cache` |
| Registration per runner | `agentscale-runner-1-files` … `-4-files` → `/runner-files` |
| Labels | `self-hosted,linux,docker,docker-desktop` |
| Heal | `Ensure-RunnersHealthy.ps1` (из keepalive) |
