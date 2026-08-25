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

Для **Verify Dev** (kubectl к API k3s из контейнера): Sync или `TF_VAR_export_docker_kubeconfig=true` при apply.  
Это **не** нужно, чтобы открыть страницу в браузере.

```powershell
.\Sync-KubeForDocker.ps1   # optional, CI Verify only
docker compose ps
gh api repos/ne-tort/prodavan/actions/runners --jq '.runners[]|{name,status,busy}'
```

Stop: `docker compose down` · wipe cache: `docker compose down -v`

## Cache / labels / files

См. volume `prodavan-ci-cache`, labels `self-hosted,linux,docker`, `Start-Runners.ps1`, `Sync-KubeForDocker.ps1`.
