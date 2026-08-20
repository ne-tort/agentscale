# WSL Development Environment

Локальная разработка Prodavan на **Windows + WSL2**. Primary dev path для full-stack: Flutter (Windows), API (WSL), PostgreSQL/MinIO (WSL docker).

---

## Architecture (dev)

```text
Windows Host
├── Cursor IDE / VS Code
├── Flutter SDK (Windows) → apps/flutter
└── WSL2 Ubuntu 22.04
    ├── apps/api (FastAPI)
    ├── docker compose (PG, MinIO, Redis)
    ├── k3d (optional mini k3s)
    └── git checkout → /mnt/c/Users/.../prodavan (prefer ~/git)
```

**Recommendation:** clone repo inside WSL filesystem (`~/git/prodavan`) — not `/mnt/c/` — for Docker and file watcher performance.

---

## Prerequisites

### WSL2

```powershell
# Windows PowerShell (admin)
wsl --install -d Ubuntu-22.04
wsl --set-default-version 2
```

### WSL packages

```bash
sudo apt update && sudo apt install -y \
  build-essential git curl jq \
  python3.12 python3.12-venv \
  docker.io docker-compose-v2

# Docker in WSL
sudo usermod -aG docker $USER
# Enable Docker Desktop WSL integration OR native dockerd
```

### Windows-side

- Flutter SDK 3.24+
- Cursor IDE with WSL remote extension
- Git for Windows (optional — use WSL git)

---

## Quick start

```bash
cd ~/git/prodavan

# Infrastructure
docker compose -f infra/docker-compose.dev.yml up -d
# Services: postgres:5432, minio:9000, redis:6379

# API
cd apps/api
python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
export DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@localhost:5432/prodavan
export DEV_KMS_KEY=dev-key-base64
alembic upgrade head
uvicorn prodavan.main:app --reload --host 0.0.0.0 --port 8000

# Flutter (Windows terminal)
cd apps/flutter
flutter pub get
flutter run -d chrome --dart-define=API_BASE=http://localhost:8000
```

---

## docker-compose.dev.yml (essential services)

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: prodavan
      POSTGRES_PASSWORD: prodavan
      POSTGRES_DB: prodavan
    ports: ["5432:5432"]
    volumes: [pgdata:/var/lib/postgresql/data]

  minio:
    image: minio/minio
    command: server /data --console-address ":9001"
    ports: ["9000:9000", "9001:9001"]
    environment:
      MINIO_ROOT_USER: minio
      MINIO_ROOT_PASSWORD: minio123

  redis:
    image: redis:7-alpine
    ports: ["6379:6379"]

volumes:
  pgdata:
```

---

## Environment file

```bash
# apps/api/.env (gitignored)
DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@localhost:5432/prodavan
JWT_SECRET=dev-secret-change-me
DEV_KMS_KEY=...
CURSOR_API_KEY=...                    # for agent tests
OBJECT_STORE_ENDPOINT=http://localhost:9000
OBJECT_STORE_ACCESS_KEY=minio
OBJECT_STORE_SECRET_KEY=minio123
OBJECT_STORE_BUCKET=prodavan-dev
REDIS_URL=redis://localhost:6379/0
AGENT_PROVIDER=cursor-sdk
MCP_GATEWAY_URL=http://localhost:8080  # if running gateway locally
```

---

## Agent development modes

| Mode | Setup | Use |
|------|-------|-----|
| **A: No worker** | API only | REST CRUD dev |
| **B: Local worker** | API spawns docker container as "pod" | Agent integration |
| **C: k3d** | k3d cluster in WSL | Full isolation test |

Mode B docker run:

```bash
docker run --rm -v ~/git/prodavan/dev-workspace:/workspace \
  -e CURSOR_API_KEY=$CURSOR_API_KEY \
  prodavan/agent-worker:dev
```

Mode C:

```bash
# Preferred: Terraform + Argo (see local-cluster-e2e.md)
bash infra/scripts/bootstrap_local_cluster.sh
export KUBECONFIG=infra/.kube/prodavan-k3d.yaml
kubectl -n prodavan get pods
curl -sS -H 'Host: prodavan.local' http://127.0.0.1:8088/health

# Minimal without Argo:
# k3d cluster create prodavan-dev -p "8088:80@loadbalancer"
# kubectl apply -k infra/k3s/overlays/dev
```

Полный runbook: [local-cluster-e2e.md](local-cluster-e2e.md).

---

## Flutter + WSL networking

| Target | API URL |
|--------|---------|
| Flutter Web (Chrome Windows) | `http://localhost:8000` |
| Flutter Android emulator | `http://10.0.2.2:8000` |
| WSL curl | `http://localhost:8000` |

WSL2 localhost forwarding — automatic on recent Windows.

---

## Claude Code / Codex spike (dev only)

```bash
# WSL — personal subscription experiments
npm install -g @anthropic-ai/claude-code
claude auth login

export AGENT_PROVIDER=claude-code-cli
export FEATURE_CLAUDE_CLI=true
```

**Not for shared dev database with real tenant data.**

---

## VS Code / Cursor tasks

```json
{
  "tasks": [
    {
      "label": "dev: docker up",
      "type": "shell",
      "command": "docker compose -f infra/docker-compose.dev.yml up -d"
    },
    {
      "label": "dev: api",
      "type": "shell",
      "command": "source .venv/bin/activate && uvicorn prodavan.main:app --reload",
      "options": { "cwd": "${workspaceFolder}/apps/api" }
    }
  ]
}
```

---

## Common issues

| Problem | Solution |
|---------|----------|
| Slow file watch on `/mnt/c` | Move repo to `~/git` |
| Docker permission denied | `usermod -aG docker`, re-login |
| PG connection refused | `docker compose ps`, check port |
| MinIO bucket missing | run `scripts/dev-init-minio.sh` |
| Flutter CORS | API `CORSMiddleware` allow localhost |

---

## Commerce repo side-by-side

```bash
~/git/Commerce    # legacy MVP reference
~/git/prodavan    # target platform
```

Migration testing: import sample `projects/demo/` via script — см. [../08-migration/](../08-migration/).

---

## Связанные документы

- [env-matrix.md](env-matrix.md)
- [github-runner-local.md](github-runner-local.md)
- [local-cluster-e2e.md](local-cluster-e2e.md)
- [topology.md](topology.md)
- [../06-agent-runtime/claude-code-spike.md](../06-agent-runtime/claude-code-spike.md)
