# GitHub Runner (local, OUTSIDE k3s)

Self-hosted GitHub Actions runner на **отдельной VM или bare-metal машине**, **не внутри k3s cluster**. Выполняет deploy jobs, integration tests, docker builds с доступом к private network.

---

## Why outside k3s

| Reason | Explanation |
|--------|-------------|
| **Chicken-and-egg** | Runner деплоит k3s — не может жить inside cluster it deploys |
| **Security** | Runner имеет kubeconfig + git push — blast radius isolation |
| **Resources** | Docker build CPU/RAM не конкурирует с agent workers |
| **Network** | Доступ к staging k3s API private endpoint |

---

## Architecture

```text
┌─────────────────────────────────────┐
│  GitHub.com                          │
│    workflows trigger                 │
└──────────────┬──────────────────────┘
               │ (outbound HTTPS)
┌──────────────▼──────────────────────┐
│  Self-hosted Runner VM               │  ← OUTSIDE k3s
│  labels: self-hosted, linux, staging │
│  - actions-runner service            │
│  - docker                            │
│  - kubectl / argocd CLI              │
│  - kustomize                         │
└──────────────┬──────────────────────┘
               │ private network
┌──────────────▼──────────────────────┐
│  k3s cluster                         │
│  ArgoCD sync                         │
└─────────────────────────────────────┘
```

---

## Machine requirements

| Env label | CPU | RAM | Disk | OS |
|-----------|-----|-----|------|-----|
| `staging` | 4 | 16 GB | 100 GB SSD | Ubuntu 22.04 |
| `prod` | 4 | 16 GB | 100 GB SSD | Ubuntu 22.04 |

Separate machines for staging and prod — **never share**.

---

## Installation

```bash
# On runner VM — NOT in k3s
mkdir actions-runner && cd actions-runner
curl -o actions-runner-linux-x64-2.311.0.tar.gz -L \
  https://github.com/actions/runner/releases/download/v2.311.0/actions-runner-linux-x64-2.311.0.tar.gz
tar xzf ./actions-runner-linux-x64-2.311.0.tar.gz

# Register (repo or org level)
./config.sh --url https://github.com/org/prodavan --token ${RUNNER_TOKEN}
./config.sh --labels self-hosted,linux,staging

# Install service
sudo ./svc.sh install
sudo ./svc.sh start
```

Token: GitHub → Settings → Actions → Runners → New.

---

## Repository layout

```text
infra/github-runner/
├── README.md
├── install-runner.sh
├── systemd/
│   └── actions-runner.service
└── hardening/
    ├── sudoers.d/actions-runner
    └── firewall.sh
```

---

## Labels & workflow targeting

```yaml
jobs:
  deploy:
    runs-on: [self-hosted, linux, staging]
```

| Label | Machine |
|-------|---------|
| `self-hosted` | all custom runners |
| `linux` | OS |
| `staging` | staging runner VM |
| `prod` | prod runner VM |

---

## Credentials on runner

Stored in **OS keyring** or encrypted files — not in git.

| Credential | Storage | Use |
|------------|---------|-----|
| `KUBECONFIG` | `/home/runner/.kube/config` | kubectl debug |
| `ARGOCD_AUTH_TOKEN` | `/home/runner/secrets/argocd` | deploy wait |
| Git push key | deploy key read/write manifest bump | CI commit |
| `STAGING_DATABASE_URL` | GitHub Actions secret → env only | migrate job |

Prod runner: minimal secrets, `environment: production` approval gate.

---

## Hardening

```bash
# firewall.sh — allow only outbound + ssh admin
ufw default deny incoming
ufw allow from ADMIN_IP to any port 22
ufw enable
```

- Runner user **not** root
- Docker group membership — required for build
- No inbound ports except SSH from admin IP
- Auto-updates: unattended-upgrades
- Runner version pin — upgrade quarterly

---

## Docker builds on runner

```yaml
# build.yml can use self-hosted for heavy builds
jobs:
  build-agent-worker:
    runs-on: [self-hosted, linux, staging]
    steps:
      - uses: docker/build-push-action@v5
        with:
          push: true
```

Alternative: GitHub-hosted for API image, self-hosted only for deploy.

### Buildx cache: реальность (as-built)

CI-джобы (`ci-images.yml`) кэшируют docker-слои через `cache-from/to: type=local` в
`/tmp/.buildx-cache-{api,web,agent}` + ручную ротацию (rmtree + rename). Проблемы:

- Раннеры `runner-1..4` — **контейнеры** (`infra/github-runner/`), их `/tmp` —
  container-ephemeral: кэш теряется при пересоздании раннера (compose down -v,
  restart Docker Desktop/WSL).
- Кэш **не разделяется между runner-1..4**: джоба на другом раннере = холодная
  сборка (agent-runtime ~10–15 мин).
- Персистентный volume `prodavan-ci-cache` → `/cache` в compose раннеров заведён
  именно под кэш, но CI **не использует** его.
- Ротация rmtree/rename не защищена от конкурирующих сборок одного образа.

Рекомендация: `cache-from/to: type=registry` (кэш-манифесты в GHCR, shared
между раннерами) либо `type=local` на `/cache` volume. Детали и карта дыр:
[agent-runtime-delivery.md](agent-runtime-delivery.md).

---

## Maintenance

| Task | Frequency |
|------|-----------|
| Rotate registration token | on reinstall |
| Clean docker images | weekly cron |
| Runner software update | monthly |
| Audit logs | quarterly |

```bash
# cleanup
docker system prune -af --filter "until=168h"
```

---

## WSL dev note

Local WSL machine **не** production runner. Optional `self-hosted, wsl, dev` label for personal experiments only — не подключать к prod kubeconfig.

См. [wsl-dev.md](wsl-dev.md).

---

## Failure modes

| Issue | Fix |
|-------|-----|
| Runner offline | `sudo ./svc.sh status`, re-register |
| Job stuck | cancel workflow, restart service |
| Disk full | prune docker |
| kubeconfig expired | refresh from terraform output |

---

## Связанные документы

- [github-actions.md](github-actions.md)
- [argocd.md](argocd.md)
- [topology.md](topology.md)
- [wsl-dev.md](wsl-dev.md)
