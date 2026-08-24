# Self-hosted GitHub Actions runner (OUTSIDE k3s)

## Канон: процесс на хосте

Раннер — обычный Actions runner. Кластер (k3s) о нём не знает.  
Docker на раннере нужен только для **CI Images** (buildx) и api/nightly Postgres fixture — не для GitOps validate/wait.

```bash
export PATH="${HOME}/.local/bin:/usr/bin:/bin"
mkdir -p ~/prodavan-actions-runner && cd ~/prodavan-actions-runner
# extract actions-runner-linux-x64-*.tar.gz
printf 'DISABLE_RUNNER_UPDATE=1\nKUBECONFIG=%s/.kube/prodavan-dev.yaml\n' "$HOME" > .env
./config.sh --url https://github.com/ne-tort/prodavan --token <REG_TOKEN> \
  --name wsl-prodavan-host --labels self-hosted,linux,docker,wsl-dev \
  --work _work --unattended --replace
nohup env PATH="${HOME}/.local/bin:/usr/bin:/bin" ./run.sh >runner.out 2>&1 &
```

Ожидать: `Listening for Jobs`.  
На PATH: `kubectl`/`kustomize` (для Gate).  
`KUBECONFIG` → `~/.kube/prodavan-dev.yaml` (kubeconfig **не** в git).

## Ops CLI

```bash
cd infra/ops
poetry install
poetry run prodavan-ops validate
poetry run prodavan-ops wait
poetry run prodavan-ops smoke
```

Под `infra/` **нет** `.sh`, нет docker-compose кластера, нет k3d.

## Session conflict

1. `cancel-in-progress: false` на единственном runner.
2. Новый `RUNNER_NAME`, удалить offline через API, подождать.
3. `gh api repos/ne-tort/prodavan/actions/runners`

Ранбук: [`docs/07-infrastructure/runbook.md`](../../docs/07-infrastructure/runbook.md).
