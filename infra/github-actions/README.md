# GitHub Actions (stub)

Спецификация: [`docs/07-infrastructure/github-actions.md`](../../docs/07-infrastructure/github-actions.md).

**Статус:** workflow YAML — в итерации **I0** (lint + test) и **I7** (deploy).

Обязательные jobs v0:

| Workflow | Trigger | Purpose |
|----------|---------|---------|
| `ci-api.yml` | push, PR | ruff, pytest, openapi diff |
| `ci-flutter.yml` | push, PR | analyze, unit tests |
| `ci-schemas.yml` | push, PR | validate pack JSON + cabinet-profile schema |
| `deploy-staging.yml` | tag `v*` | Argo CD sync (manual approve) |

**Commerce submodule:** в CI Commerce-репо — `git submodule update --init prodavan` перед docs lint.
