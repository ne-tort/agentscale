# GitHub Actions (stub)

Спецификация: [`docs/07-infrastructure/github-actions.md`](../../docs/07-infrastructure/github-actions.md).

**Статус:** `ci-schemas.yml` и `ci-api.yml` — **I0 done**. Deploy — **I7**.

| Workflow | Status | Purpose |
|----------|--------|---------|
| `ci-api.yml` | active | ruff, pytest health |
| `ci-schemas.yml` | active | validate pack + cabinet-profile schema |
| `ci-flutter.yml` | planned | analyze, unit tests |
| `deploy-staging.yml` | planned I7 | Argo CD sync |

**Commerce submodule:** в CI Commerce-репо — `git submodule update --init prodavan` перед docs lint.
