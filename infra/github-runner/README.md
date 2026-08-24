# Self-hosted GitHub Actions runner (OUTSIDE k3s)

## Канон: процесс на Kali WSL (не Docker)

1. **Docker Desktop** — TLS к GitHub ломается (EOF). DNS при этом ок.
2. **Контейнер host-net** — TLS OK, но listener умирает mid-job → Session Conflict.
3. **Хост Kali** — канон. Docker-задачи через `/var/run/docker.sock`.

```bash
export PATH="${HOME}/.local/bin:/usr/bin:/bin"
mkdir -p ~/prodavan-actions-runner && cd ~/prodavan-actions-runner
# extract actions-runner-linux-x64-2.336.0.tar.gz
printf 'DISABLE_RUNNER_UPDATE=1\n' > .env
./config.sh --url https://github.com/ne-tort/prodavan --token <REG_TOKEN> \
  --name wsl-prodavan-host --labels self-hosted,linux,docker,wsl-dev \
  --work _work --unattended --replace
nohup env PATH="${HOME}/.local/bin:/usr/bin:/bin" ./run.sh >runner.out 2>&1 &
```

Ожидать: `Listening for Jobs` на `2.336.0`.  
`KUBECONFIG` → `infra/.kube/prodavan-k3d.yaml`. kubectl на `127.0.0.1:6443`.

**Инструменты на PATH раннера** (бинарники на хосте, не через Docker): `kubectl`, `kustomize`, `terraform`.  
Раннер **вне** k3s; кластер про Docker не знает — kubelet тянет образы из GHCR по `ghcr-pull`.

## Ops CLI (Poetry)

```bash
cd infra/ops
poetry install
poetry run prodavan-ops validate
poetry run prodavan-ops wait
poetry run prodavan-ops smoke
poetry run prodavan-ops seed
```

Под `infra/` **нет** `.sh`.

## Session conflict

1. `cancel-in-progress: false` на единственном runner.
2. Новый `RUNNER_NAME`, удалить offline через API, подождать.
3. `gh api repos/ne-tort/prodavan/actions/runners`

Ранбук: [`docs/07-infrastructure/runbook.md`](../../docs/07-infrastructure/runbook.md).
