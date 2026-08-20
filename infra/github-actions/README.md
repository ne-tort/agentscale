# GitHub Actions (stub)

Спецификация: [`docs/07-infrastructure/github-actions.md`](../../docs/07-infrastructure/github-actions.md).

**Статус:** `ci-schemas.yml` и `ci-api.yml` — **I0 done**. Deploy — **I7**.

| Workflow | Status | Purpose |
|----------|--------|---------|
| `ci-api.yml` | active | ruff, pytest health |
| `ci-schemas.yml` | active | validate pack + cabinet-profile schema |
| `ci-images.yml` | active | self-hosted build/push GHCR + k3s tag bump |
| `ci-flutter.yml` | active | analyze + palette hex guard |
| `deploy-staging.yml` | planned I7 | Argo CD sync wait |

**Commerce submodule:** в CI Commerce-репо — `git submodule update --init prodavan` перед docs lint.
