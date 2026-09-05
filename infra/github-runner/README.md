# Self-hosted GitHub Actions runners — Docker Desktop (**CI only**)

Не bootstrap. После `terraform apply` UI: **http://127.0.0.1:8088/**  
( Traefik `0.0.0.0:8088`, Ingress без `host` — браузеру kubeconfig/portproxy не нужны. )

Этот каталог — только self-hosted runners для Gate / Images / Verify на ноутбуке.

## Сколько раннеров

| Workflow | Параллельные job'ы |
|----------|-------------------|
| **CI Gate** | infra + api + flutter + schemas = **4** |
| CI Images | build-api + build-web = 2 |
| Verify Dev | 1 |

Канон: `RUNNER_REPLICAS=4`.

## Quick start

```powershell
cd infra/github-runner
copy .env.example .env   # ACCESS_TOKEN=gh auth token
.\Start-Runners.ps1
```

Скрипт:

1. Пишет явный DNS в `%USERPROFILE%\.docker\daemon.json` (`Ensure-DockerDns.ps1`) — нужен **restart Docker Desktop**, если DNS менялся.
2. Синкает kubeconfig + portproxy (`Sync-KubeForDocker.ps1`).
3. Нормализует `EPHEMERAL=` (см. ниже) и поднимает `×4` replicas.
4. Удаляет offline registrations на GitHub.

Для **Verify Dev** (kubectl к API k3s из контейнера): Sync или `TF_VAR_export_docker_kubeconfig=true` при apply.  
Это **не** нужно, чтобы открыть страницу в браузере.

```powershell
.\Sync-KubeForDocker.ps1   # optional, CI Verify only
docker compose ps
gh api repos/ne-tort/prodavan/actions/runners --jq '.runners[]|{name,status,busy}'
```

Stop: `docker compose down` · wipe cache: `docker compose down -v`

## DNS (обязательно явно)

Два разных DNS-пути — оба должны быть заданы, иначе flaky `lookup …: Try again` / stuck pulls:

| Путь | Кто резолвит | Где задаём |
|------|--------------|------------|
| **Runner container** (checkout, curl, pip) | compose `dns:` → ExtServers `8.8.8.8` / `1.1.1.1` | `docker-compose.yml` |
| **`docker build` / sibling** через `/var/run/docker.sock` | Docker **engine** daemon | `Ensure-DockerDns.ps1` → `daemon.json` `"dns": ["8.8.8.8","1.1.1.1"]` |

Не полагаться на дефолт Desktop (`192.168.65.7` без overrides). После правки `daemon.json` — перезапуск Docker Desktop.

Проверка:

```powershell
# runner
docker compose exec runner cat /etc/resolv.conf
# expect ExtServers: [8.8.8.8 1.1.1.1]

# engine (build path) — after Desktop restart
docker run --rm alpine cat /etc/resolv.conf
# expect nameserver 8.8.8.8 (or both), not only 192.168.65.7 with Overrides: []
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

## Cache / labels / files

См. volume `prodavan-ci-cache`, labels `self-hosted,linux,docker`, `Start-Runners.ps1`, `Sync-KubeForDocker.ps1`, `Ensure-DockerDns.ps1`.
