# Self-hosted GitHub Actions runner (OUTSIDE k3s)

Docker Compose runner for building `ci-images` / `deploy-dev-k3s` with Docker socket access.

## Prefer Docker Desktop (Windows)

Run compose via **Windows** `docker.exe` / context `desktop-linux`, not Kali’s nested `dockerd`.
Nested WSL dockerd dies with “Session terminated, killing shell” when the WSL session ends.

```powershell
$env:DOCKER_CONTEXT = "desktop-linux"
cd c:\Users\qwerty\git\Commerce\prodavan\infra\github-runner
copy .env.example .env   # fill ACCESS_TOKEN or RUNNER_TOKEN
docker compose build
docker compose up -d
docker logs -f prodavan-gha-runner
```

Expect a stable line: `Listening for Jobs`.

If GitHub shows the runner **busy/offline** with a stuck `in_progress` job and compose logs
`A session for this runner already exists`, cancel/force-cancel that run, then register under a
**new** `RUNNER_NAME` (wipe the `runner-home` volume). Do not leave two listeners on the same name.

## Kubeconfig (local k3d, no sudo)

Deploy jobs need cluster access without `/etc/rancher/k3s/k3s.yaml`.

1. Create cluster: `bash infra/scripts/bootstrap_local_cluster.sh` (or terraform local apply).
2. Export: `bash infra/scripts/k3d_kubeconfig_for_runner.sh` → `infra/.kube/prodavan-k3d.yaml`.
3. Compose mounts `../.kube` → `/kube` and sets `KUBECONFIG=/kube/prodavan-k3d.yaml`.
4. `network_mode: host` so the container reaches k3d API on `127.0.0.1:6443`.

See [local-cluster-e2e.md](../../docs/07-infrastructure/local-cluster-e2e.md).

## Labels

`self-hosted`, `linux`, `docker`, `wsl-dev`

Default compose name: `wsl-prodavan` (or `wsl-prodavan-2` after a stuck-session recovery).

## Token

```powershell
# one-shot registration token
gh api -X POST repos/ne-tort/prodavan/actions/runners/registration-token --jq .token

# or put a PAT (repo admin) in .env as ACCESS_TOKEN=
```

Optional: place `actions-runner-linux-x64-2.328.0.tar.gz` next to compose to skip download.

## Verify

```powershell
gh api repos/ne-tort/prodavan/actions/runners --jq '.runners[] | {name,status,busy,labels:[.labels[].name]}'
```

## Stop / remove

```powershell
$env:DOCKER_CONTEXT = "desktop-linux"
docker compose down
gh api repos/ne-tort/prodavan/actions/runners --jq '.runners[] | select(.name=="wsl-prodavan") | .id'
# gh api -X DELETE repos/ne-tort/prodavan/actions/runners/<id>
```

## Notes

- `network_mode: host` helps Desktop/WSL DNS reach `broker.actions.githubusercontent.com`.
- Do not commit `.env` or the runner tarball credentials.
- For pure Linux WSL without Desktop: keep a long-lived WSL session (`sleep infinity`) if using distro dockerd.
