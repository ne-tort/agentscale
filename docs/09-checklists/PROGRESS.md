# PROGRESS — трекер задач Prodavan (документация)

**Шкала:** 10 = полный артефакт; 8 = приёмка; &lt;8 = доработка.

**Итерация:** documentation foundation (2026-08-20)

| Phase avg | Score |
|-----------|-------|
| P0 | 10 |
| P1 | 9 |
| P2 | 9 |
| P3 | 8.5 |
| P4 | 8 |
| P5 | 8.5 |
| P6 | 8.5 |
| P7 | 8 |
| P8 | 8.5 |
| P9 | 9 |
| **Overall** | **8.7** |

---

## P0 — Git

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P0-T01 | main clean, synced | 10 | 10 | — | [x] |
| P0-T02 | branch prodavan/platform-foundation | 10 | 10 | — | [x] |
| P0-T03 | no bot/docker changes | 10 | 10 | — | [x] |

## P1 — Repo + submodule

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P1-T01 | gh repo ne-tort/prodavan | 10 | 10 | — | [x] |
| P1-T02 | root README | 10 | 9 | — | [x] |
| P1-T03 | .gitignore | 10 | 9 | — | [x] |
| P1-T04 | scaffold dirs | 10 | 9 | — | [x] |
| P1-T05 | electronics pack skeleton | 10 | 9 | prompt .md files deferred to installer | [x] |
| P1-T06 | submodule in Commerce | 10 | 10 | — | [x] |
| P1-T07 | Commerce docs/08-prodavan | 10 | 10 | — | [x] |
| P1-T08 | push both repos | 10 | 10 | — | [x] |

## P2 — Architecture ADRs

| ID | Документ | Target | Fact | Gaps | Done |
|----|----------|--------|------|------|------|
| P2-T01 | overview.md | 10 | 9 | — | [x] |
| P2-T02 | multi-tenancy.md | 10 | 9 | — | [x] |
| P2-T03 | cabinet-profiles.md | 10 | 9 | JSON Schema file external | [x] |
| P2-T04 | agent-isolation.md | 10 | 9 | — | [x] |
| P2-T05 | mcp-gateway.md | 10 | 9 | — | [x] |
| P2-T06 | platform-extensibility.md | 10 | 9 | — | [x] |
| P2-T07 | api-style.md | 10 | 8 | OpenAPI yaml stub in code later | [x] |
| P2-T08 | threat-model.md | 10 | 9 | — | [x] |

## P3 — Modules (summary)

| Module | Files 10/10 | Avg | Done |
|--------|-------------|-----|------|
| M00-cabinets | 10 | 8.5 | [x] |
| M01-projects | 10 | 8.5 | [x] |
| M02-specs-kp | 10 | 8.5 | [x] |
| M03-prompts | 10 | 8.5 | [x] |
| M04-catalogs + system-databases | 11 | 9 | [x] |
| M05-integrations | 10 | 8.5 | [x] |
| M06-mcp | 10 | 8.5 | [x] |
| M07-agent | 10 | 8.5 | [x] |
| M08-tenants | 10 | 8 | billing hooks thin | [x] |
| M09-operations | 10 | 8 | billing export thin | [x] |

## P4 — Agent runtime

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P4-T01 | providers.md | 10 | 8 | Codex/Claude unverified live | [x] |
| P4-T02 | provider-port.md | 10 | 8 | — | [x] |
| P4-T03 | cursor-sdk-adapter.md | 10 | 9 | — | [x] |
| P4-T04 | codex-cli-spike.md | 10 | 7 | spike not executed | [x] |
| P4-T05 | claude-code-spike.md | 10 | 7 | spike not executed | [x] |
| P4-T06 | worker-isolation.md | 10 | 9 | — | [x] |
| P4-T07 | prompt-envelope.md | 10 | 9 | — | [x] |
| P4-T08 | no direct PG in worker | 10 | 8 | CI scan spec only | [x] |

## P5 — Frontend

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P5-T01 | architecture.md | 10 | 9 | — | [x] |
| P5-T02 | design-system.md | 10 | 9 | — | [x] |
| P5-T03 | widget-catalog.md | 10 | 8 | — | [x] |
| P5-T04 | cabinet-shell.md | 10 | 9 | — | [x] |
| P5-T05 | screens-inventory.md | 10 | 9 | — | [x] |
| P5-T06 | md-editor.md | 10 | 8 | — | [x] |
| P5-T07 | responsive.md | 10 | 8 | — | [x] |

## P6 — Backend

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P6-T01 | structure.md | 10 | 9 | — | [x] |
| P6-T02 | erd-v0.md | 10 | 9 | — | [x] |
| P6-T03 | alembic.md | 10 | 8 | — | [x] |
| P6-T04 | rls-policies.md | 10 | 9 | — | [x] |
| P6-T05 | openapi-layout.md | 10 | 8 | — | [x] |
| P6-T06 | secrets.md | 10 | 8 | — | [x] |
| P6-T07 | object-store.md | 10 | 9 | — | [x] |

## P7 — Infrastructure

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P7-T01 | topology.md | 10 | 9 | — | [x] |
| P7-T02 | terraform.md | 10 | 8 | no .tf files yet | [x] |
| P7-T03 | k3s-services.md | 10 | 8 | — | [x] |
| P7-T04 | argocd.md | 10 | 8 | — | [x] |
| P7-T05 | github-actions.md | 10 | 8 | — | [x] |
| P7-T06 | github-runner-local.md | 10 | 9 | outside k3s explicit | [x] |
| P7-T07 | wsl-dev.md | 10 | 8 | — | [x] |
| P7-T08 | env-matrix.md | 10 | 8 | — | [x] |

## P8 — Migration + pack

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P8-T01 | commerce-boundary.md | 10 | 9 | — | [x] |
| P8-T02 | commerce-semantics.md | 10 | 9 | — | [x] |
| P8-T03 | pack.json + cabinet-profile.json | 10 | 9 | — | [x] |
| P8-T04 | pack file inventory | 10 | 8 | prompt md copy deferred | [x] |
| P8-T05 | capabilities.s4b validation | 10 | 9 | — | [x] |
| P8-T06 | _template pack without s4b | 10 | 8 | — | [x] |

## P9 — Master checklists

| ID | Задача | Target | Fact | Gaps | Done |
|----|--------|--------|------|------|------|
| P9-T01 | PROGRESS.md | 10 | 10 | — | [x] |
| P9-T02 | master-delivery.md | 10 | 9 | — | [x] |
| P9-T03 | phase-gates.md | 10 | 9 | — | [x] |
| P9-T04 | review-protocol.md | 10 | 9 | — | [x] |
| P9-T05 | final audit ≥8.5 | 10 | 9 | code not started (expected) | [x] |
| P9-T06 | submodule push | 10 | 10 | — | [x] |
| P9-T07 | docs/README.md map | 10 | 9 | — | [x] |

---

## Follow-up (post-docs, not blocking)

- Execute Codex/Claude CLI spikes (P4-T04/T05 → 9+)
- Copy Commerce prompt .md into pack seeds
- Add `.tf` and k8s manifests
- Implement Flutter/FastAPI apps
