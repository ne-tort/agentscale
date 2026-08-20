# Self-hosted GitHub Actions runner (OUTSIDE k3s)

Docker Compose runner for WSL / Linux with Docker socket access for `ci-images`.

## Labels

`self-hosted`, `linux`, `docker`, `wsl-dev`

## Start (WSL)

```bash
cd /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/github-runner

# From Windows or WSL with gh authenticated:
export RUNNER_TOKEN=$(gh api -X POST repos/ne-tort/prodavan/actions/runners/registration-token --jq .token)
export REPO_URL=https://github.com/ne-tort/prodavan

docker compose up -d
docker compose logs -f runner
```

Windows PowerShell (token then WSL):

```powershell
$token = gh api -X POST repos/ne-tort/prodavan/actions/runners/registration-token --jq .token
wsl -e bash -lc "cd /mnt/c/Users/qwerty/git/Commerce/prodavan/infra/github-runner && RUNNER_TOKEN='$token' REPO_URL='https://github.com/ne-tort/prodavan' docker compose up -d"
```

## Verify

```bash
gh api repos/ne-tort/prodavan/actions/runners --jq '.runners[] | {name,status,labels:.labels[].name}'
```

## Stop / remove

```bash
docker compose down
# Then remove runner in GitHub UI or:
# gh api -X DELETE repos/ne-tort/prodavan/actions/runners/{id}
```

## Notes

- Not for production deploy credentials; WSL = `wsl-dev` only.
- Requires Docker Engine reachable at `/var/run/docker.sock`.
- Compose uses `network_mode: host` — on some WSL Docker setups bridge NAT cannot reach `api.github.com` (runner stuck on Authentication).
- Prefer Docker Desktop (`desktop-linux`) over flaky nested engines.
