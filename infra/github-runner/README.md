# Self-hosted GitHub Actions runner (OUTSIDE k3s)

## Канон: **system** unit (не `--user`)

На WSL `systemd --user` часто гасится вместе с короткой `wsl`-сессией → runner **offline**, джобы в queue.  
Канон: `/etc/systemd/system/prodavan-actions-runner.service` с `User=www`.

Ожидать в GitHub: `wsl-prodavan-host` **online**, labels `self-hosted`, `linux`/`Linux`, `docker`.

### 1. Install once

```bash
export PATH="${HOME}/.local/bin:/usr/bin:/bin"
mkdir -p ~/prodavan-actions-runner && cd ~/prodavan-actions-runner
# extract actions-runner-linux-x64-*.tar.gz
./config.sh --url https://github.com/ne-tort/prodavan --token <REG_TOKEN> \
  --name wsl-prodavan-host --labels self-hosted,linux,docker,wsl-dev \
  --work _work --unattended --replace
```

Token:

```bash
gh api -X POST repos/ne-tort/prodavan/actions/runners/registration-token --jq .token
```

### 2. `.env` (не в git) — только из bash в WSL

```bash
printf 'DISABLE_RUNNER_UPDATE=1\nDISABLE_AUTO_UPDATE=true\nKUBECONFIG=%s/.kube/prodavan-dev.yaml\n' "$HOME" > ~/prodavan-actions-runner/.env
```

Не пиши `.env` из PowerShell — `\n` превращается в букву `n`.

### 3. systemd system unit

```bash
sudo cp infra/github-runner/prodavan-actions-runner.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now prodavan-actions-runner.service
sudo systemctl status prodavan-actions-runner.service
```

Юнит: [`prodavan-actions-runner.service`](prodavan-actions-runner.service).

Логи: `journalctl -u prodavan-actions-runner -f` → `Listening for Jobs`.  
На PATH у `www`: `kubectl` / `kustomize`.  
`KUBECONFIG` → `~/.kube/prodavan-dev.yaml`.

### Conflict / offline

```bash
sudo systemctl stop prodavan-actions-runner
sudo pkill -9 -u www -f Runner.Listener || true
# подождать ~1–2 мин или удалить runner в GitHub UI/API и config.sh --replace
sudo systemctl start prodavan-actions-runner
gh api repos/ne-tort/prodavan/actions/runners
```

Единственный runner → `cancel-in-progress: false` в workflows. Зависший run в concurrency group блокирует новые — cancel через `gh run cancel <id>`.

## Ops CLI

```bash
cd infra/ops && poetry install
poetry run prodavan-ops validate
poetry run prodavan-ops wait
poetry run prodavan-ops smoke
```

Ранбук: [`docs/07-infrastructure/runbook.md`](../../docs/07-infrastructure/runbook.md).
