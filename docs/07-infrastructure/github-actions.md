# GitHub Actions (as-built)

| Workflow | Trigger | Role |
|----------|---------|------|
| `ci-gate.yml` | PR / dispatch | `poetry run prodavan-ops validate` + api/flutter/schemas |
| `ci-images.yml` | main paths / dispatch | GHCR `:latest` + SHA |
| `verify-dev.yml` | after Images / infra push | `rollout` + `wait` + `smoke` |
| `auto-merge.yml` | Gate success | squash + dispatch Images (`AUTO_MERGE_TOKEN`) |
| `ci-nightly.yml` | cron | API integration (`PRODAVAN_CI_HOST`) |

Deploy is **Argo CD** from git. Runners: **Docker Desktop** pack ×4 ([`infra/github-runner`](../../infra/github-runner)).  
After reboot: Admin `Sync-KubeForDocker.ps1` (or `Start-Runners.ps1`). Ops: [`infra/ops`](../../infra/ops).
