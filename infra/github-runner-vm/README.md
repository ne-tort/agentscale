# Self-hosted runners — CI VM (Ubuntu, нативные systemd-сервисы)

Раннеры CI перенесены из Docker Desktop (WSL) на выделенную Linux VM.
Этот каталог — бутстрап и эксплуатация VM-раннеров. Прежний вариант
(Docker Desktop, myoung34-контейнеры) — исторический, см. [`../github-runner/`](../github-runner/).

## VM

| Параметр | Значение |
|----------|----------|
| Адрес | `www@172.31.156.203` (VMware VM, NAT через Hyper-V Default Switch) |
| ОС | Ubuntu 24.04 (python 3.12 из коробки) |
| Ресурсы | 8 vCPU / 15 GiB RAM / 196 GB disk |
| Docker | docker-ce 29.x + buildx/compose plugins |
| Пользователи | `www` (sudo, админ), `runner` (сервисы раннеров, группа docker) |

Раннеры (лейблы `self-hosted,linux,docker,ci-vm`, имена `vm-*`):

| Сервис | Репо | Роль |
|--------|------|------|
| `actions.runner.ne-tort-agentscale.vm-as-1..5` | `ne-tort/agentscale` | Gate / Images / Verify / Nightly |
| `actions.runner.ne-tort-prodavan-claw.vm-claw-1..2` | `ne-tort/prodavan-claw` | openclaw CI / images |

## Кэши (цель миграции — чтобы каждый PR не качал и не пересобирал всё заново)

| Что | Где | Что даёт |
|-----|-----|----------|
| Flutter SDK | `/cache/flutter-sdk` (git clone stable, flock `/cache/flutter-sdk.lock`) | SDK не перекачивается |
| Pub | `/cache/pub` (`PUB_CACHE`) | пакеты не перекачиваются |
| pip / Poetry | `/cache/pip`, `/cache/poetry*` | python-зависимости |
| kubectl / kustomize / setup-python / setup-node | `/cache/kubectl`, `/cache/kustomize`, `/cache/toolcache` (`RUNNER_TOOL_CACHE`) | инструменты и toolcache |
| **Docker слои + RUN cache mounts** | persistent buildx builder `prodavan-ci` (docker-container, volume `buildx_buildkit_prodavan-ci0_state`) | слои образов и `--mount=type=cache` (pip/pub внутри Dockerfile) живут между сборками; GC — 50 GiB / 30 дней (`/etc/buildkit/buildkitd.toml`, копия — `buildkitd.toml` здесь) |

Workflows `CI Images` больше не создают per-job buildkitd и не гоняют `type=local`
кэш через `/tmp` (кэш жил в контейнере раннера и терялся при любом пересоздании).
Единственный buildkitd `prodavan-ci` общий для всех job'ов.

## Dev-кластер (k3s) — на этой же VM

- Кластер поднимается Terraform-ом: [`../terraform/environments/vm/`](../terraform/environments/vm/)
  (k3s + Argo; `host_profile = "vm"` — Docker Engine не отключается, он нужен раннерам).
- Раннеры и кластер на одном хосте: kubectl — `127.0.0.1:6443`, smoke (Traefik) —
  `127.0.0.1:8088`, Keycloak — `127.0.0.1:8089`.
- kubeconfig: `/home/runner/.kube/prodavan-dev.yaml` — кладёт terraform
  (`runner_kubeconfig_path`). Не в git.
- `ci-hosts-update.timer` (host.docker.internal → Windows-gateway) остаётся как
  общая карта «VM → Windows-хост»; CI больше её не использует.

## Бутстрап (однократно, на новой VM)

```bash
# 1. base: docker, инструменты, runner-пользователь, /cache, hosts-timer
sudo python3 01-base.py

# 2. раннеры: сминтить токены регистрации (валидны 1 час)
gh api -X POST repos/ne-tort/agentscale/actions/runners/registration-token --jq .token
gh api -X POST repos/ne-tort/prodavan-claw/actions/runners/registration-token --jq .token
cat >/tmp/runner_tokens.env <<EOF
RUNNER_TOKEN_AS=...
RUNNER_TOKEN_CLAW=...
EOF
chmod 600 /tmp/runner_tokens.env
# kubeconfig с Windows: %USERPROFILE%\.kube\prodavan-dev.yaml -> /tmp/prodavan-dev.yaml
sudo python3 02-runners.py

# 3. persistent buildx builder (idempotent; CI делает то же самое сам)
sudo -u runner docker buildx create --name prodavan-ci \
  --driver docker-container --driver-opt network=host \
  --config /etc/buildkit/buildkitd.toml
sudo -u runner docker buildx use prodavan-ci
```

`02-runners.py` идемпотентен: активные сервисы пропускаются, пересоздаёт только
сломанные (remove + `--replace`). Скрипты — Python, а не `.sh`: контракт репо
запрещает `.sh` под `infra/` (см. `prodavan_ops/validate.py`).

## Эксплуатация

```bash
systemctl list-units 'actions.runner.*'
journalctl -u actions.runner.ne-tort-agentscale.vm-as-1 -f   # "Listening for Jobs"
docker buildx du                          # размер кэша buildkit
docker buildx prune --keep-bytes 20GB     # при необходимости
```

Апгрейд actions/runner: скачать новый tarball поверх `/opt/actions-runners/as-N`
(регистрация в `.runner`/`.credentials` сохраняется), затем `./svc.sh stop && ./svc.sh start`.

## Декомиссия Docker Desktop-раннеров (когда VM подтверждена зелёными CI)

```powershell
cd infra/github-runner
docker compose stop                  # не down -v: кэш prodavan-ci-cache и volumes
                                     # регистрации держим до полной уверенности
gh api repos/ne-tort/agentscale/actions/runners --jq '.runners[] | select(.name|startswith("dd-")) | .id'
# для каждого id: gh api -X DELETE repos/ne-tort/agentscale/actions/runners/<id>
# аналогично для ne-tort/prodavan-claw (dd-claw-*)
```
