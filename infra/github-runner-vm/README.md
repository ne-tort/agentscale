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

## IPv4-приоритет для egress

У VM есть DNS-ответы AAAA, но рабочего IPv6 egress через Hyper-V сейчас нет.
Docker/Go может выбрать IPv6-адрес первым, что проявляется как
`net/http: network is unreachable` или TLS handshake timeout. `01-base.py`
идемпотентно добавляет в `/etc/gai.conf` правило
`precedence ::ffff:0:0/96 100` до первого сетевого шага bootstrap. Уже активная
эквивалентная строка не дублируется, остальные правила сохраняются.

Для точечного применения на **существующей** VM (без повторного полного bootstrap)
выполнить с Windows из Git Bash:

```bash
ssh -t www@172.31.156.203 "sudo python3 -c 'from pathlib import Path; p=Path(\"/etc/gai.conf\"); line=\"precedence ::ffff:0:0/96 100\"; text=p.read_text(encoding=\"utf-8\") if p.exists() else \"\"; fields=line.split(); None if any(item.partition(\"#\")[0].split() == fields for item in text.splitlines()) else p.write_text(text + (\"\n\" if text and not text.endswith(\"\n\") else \"\") + \"# Prefer IPv4: Docker/Go may otherwise select unreachable IPv6 egress.\n\" + line + \"\n\", encoding=\"utf-8\")'"
```

Команда только дополняет `gai.conf` и ничего не перезапускает. Правило повышает
приоритет IPv4 при сортировке адресов `getaddrinfo(3)` для dual-stack имён; это
**не** отключение IPv6 и не принудительный IPv4-only режим. На уже запущенные
процессы распространение настройки зависит от того, когда они читают resolver-
конфиг: если ошибка сохранится, может понадобиться отдельный контролируемый
рестарт runner/Docker (в команду намеренно не включён). Проверка порядка адресов
на VM: `getent ahosts github.com`.

Ограничение: host `/etc/gai.conf` действует на пользователей glibc `getaddrinfo`.
Go binary с resolver `netgo` и контейнеры с собственным `/etc/gai.conf` могут его
игнорировать; такой случай требует отдельной настройки соответствующего runtime.

## Кэши (цель миграции — чтобы каждый PR не качал и не пересобирал всё заново)

| Что | Где | Что даёт |
|-----|-----|----------|
| pip / Poetry | `/cache/pip`, `/cache/poetry*` | python-зависимости |
| pnpm store | `/cache/pnpm-store` (`--store-dir` в CI Web job) | зависимости `apps/web` не перекачиваются; node 22 — в `/cache/toolcache` (setup-node), pnpm активируется corepack'ом |
| kubectl / kustomize / setup-python / setup-node | `/cache/kubectl`, `/cache/kustomize`, `/cache/toolcache` (`RUNNER_TOOL_CACHE`) | инструменты и toolcache |
| **Docker слои + RUN cache mounts** | persistent buildx builder `agentscale-ci` (docker-container, volume `buildx_buildkit_agentscale-ci0_state`) | слои образов и `--mount=type=cache` (pip внутри Dockerfile) живут между сборками; GC — 20 GiB / 7 дней (`/etc/buildkit/buildkitd.toml`, копия — `buildkitd.toml` здесь) |

Workflows `CI Images` больше не создают per-job buildkitd и не гоняют `type=local`
кэш через `/tmp` (кэш жил в контейнере раннера и терялся при любом пересоздании).
Единственный buildkitd `agentscale-ci` общий для всех job'ов.

## Гигиена диска (важно: ВМ общая с dev-кластером)

2026-10-14 диск ВМ заполнился до 84% и утянул dev-контур. Две причины и обе
закрыты:

1. **Утечка анонимных томов.** Образ `postgres` объявляет `VOLUME`, поэтому
   `docker rm -f` контейнера smoke-теста оставлял анонимный том ~140 МБ. За
   ~300 прогонов это дало ~42 ГБ. Теперь все teardown'ы используют `docker rm -fv`
   + `docker volume prune -f` (`ci-api.yml`, `ci-e2e.yml`, `ci-nightly.yml`,
   `ci-images.yml`).
2. **Кэш buildx без ограничения по размеру.** Политика была 50 GiB / 30 дней —
   расчёт на выделенный build-хост. Кэш дорос до 31 ГБ. Политика ужесточена до
   20 GiB / 7 дней.

Плюс защита, не зависящая от workflow (отменённый или упавший прогон сам за
собой не уберёт):

- **`ci-disk-guard.py`** — systemd-таймер `ci-disk-guard.timer` (каждые 30 мин,
  `OnBootSec=5min`). Убирает осиротевшие smoke/pg-контейнеры, анонимные
  dangling-тома, оставляет 2 новейших sha-тега на репозиторий (k3s работает из
  containerd — docker-образы по sha это чистый мусор CI), и подрезает кэши
  buildx по бюджету. Именованные тома и образы запущенных контейнеров не
  трогает.
- **Алерт по порогу**: при 80% (`--warn-pct`) и 90% (`--crit-pct`) пишет в
  journal и вызывает `/usr/local/sbin/ci-disk-alert.sh LEVEL MSG`, если тот
  существует (например, curl в ntfy). Положить свой скрипт — и алерты пойдут
  без правок в репо.
- **Concurrency в CI**: у `ci-api` / `ci-images` / `ci-web` / `ci-schemas`
  группы `ci-<name>-${{ github.ref }}` с `cancel-in-progress: true` — новый
  push отменяет предыдущий прогон вместо того, чтобы складывать вторую полную
  сборку/тест на ту же ВМ.

Проверить руками:

```bash
sudo python3 /usr/local/sbin/ci-disk-guard.py --dry-run   # что будет удалено
systemctl list-timers ci-disk-guard.timer
journalctl -u ci-disk-guard.service -n 20
df -h /
```

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
sudo -u runner docker buildx create --name agentscale-ci \
  --driver docker-container --driver-opt network=host \
  --config /etc/buildkit/buildkitd.toml
sudo -u runner docker buildx use agentscale-ci
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
docker compose stop                  # не down -v: кэш agentscale-ci-cache и volumes
                                     # регистрации держим до полной уверенности
gh api repos/ne-tort/agentscale/actions/runners --jq '.runners[] | select(.name|startswith("dd-")) | .id'
# для каждого id: gh api -X DELETE repos/ne-tort/agentscale/actions/runners/<id>
# аналогично для ne-tort/prodavan-claw (dd-claw-*)
```
