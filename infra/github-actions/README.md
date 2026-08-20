# GitHub Actions

Спецификация: [`docs/07-infrastructure/github-actions.md`](../../docs/07-infrastructure/github-actions.md).

Локальный k3s E2E: [`docs/07-infrastructure/local-cluster-e2e.md`](../../docs/07-infrastructure/local-cluster-e2e.md).

| Workflow | Status | Purpose |
|----------|--------|---------|
| `ci-api.yml` | active | ruff, pytest health |
| `ci-schemas.yml` | active | validate pack + cabinet-profile schema |
| `ci-images.yml` | active | self-hosted build/push GHCR + k3s tag bump |
| `ci-flutter.yml` | active | analyze + palette hex guard |
| `deploy-dev-k3s.yml` | active (local-dev) | after Images: terraform/k3d, Argo wait, curl smoke `:8088` |
| `deploy-staging.yml` | planned | cloud staging Argo sync |

**Commerce submodule:** в CI Commerce-репо — `git submodule update --init prodavan` перед docs lint.
