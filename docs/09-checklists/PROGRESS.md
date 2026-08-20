# PROGRESS — трекер Prodavan

**Две шкалы** (см. [`implementation-readiness.md`](implementation-readiness.md)):

| Шкала | Вопрос | Gate |
|-------|--------|------|
| **Doc** | Спека полная? | avg ≥ 8.5, min ≥ 6 |
| **Impl** | Можно кодить без новых ADR? | avg ≥ 7, min ≥ 5 (для старта I0) |

**Итерация docs:** 2026-08-20 · **Переаудит Impl:** 2026-08-20

| Phase | Doc avg | Impl avg |
|-------|---------|----------|
| P0 | 10 | 10 |
| P1 | 9 | **8** |
| P2 | 8.9 | **8** |
| P3 | 8.5 | **7** |
| P4 | 8.0 | **5** |
| P5 | 8.4 | **5** |
| P6 | 8.6 | **8** |
| P7 | 8.1 | **5** |
| P8 | 8.7 | **8** |
| P9 | 9.0 | **10** |
| **Overall** | **8.7** | **8.7** |

**Вердикт:** Doc ✅ · **I3 complete** — следующий шаг **I4 M02 specs pipeline**.

---

## P0 — Git

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P0-T01 | main clean, synced | 10 | 10 | — | [x] |
| P0-T02 | branch prodavan/platform-foundation | 10 | 10 | — | [x] |
| P0-T03 | no bot/docker changes | 10 | 10 | — | [x] |

## P1 — Repo + submodule

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P1-T01 | gh repo ne-tort/prodavan | 10 | 10 | — | [x] |
| P1-T02 | root README | 9 | 8 | — | [x] |
| P1-T03 | .gitignore | 9 | 8 | — | [x] |
| P1-T04 | scaffold dirs | 9 | **8** | infra/ added | [x] |
| P1-T05 | electronics pack | 9 | **8** | prompts copied; AGENTS adapt I3 | [x] |
| P1-T06 | submodule in Commerce | 10 | 10 | — | [x] |
| P1-T07 | Commerce docs/08-prodavan | 10 | 10 | — | [x] |
| P1-T08 | push both repos | 10 | 10 | — | [x] |

## P2 — Architecture ADRs

| ID | Документ | Doc | Impl | Impl Gaps | Done |
|----|----------|-----|------|-----------|------|
| P2-T01 | overview.md | 9 | 8 | — | [x] |
| P2-T02 | multi-tenancy.md | 9 | 8 | — | [x] |
| P2-T03 | cabinet-profiles.md | 9 | **8** | v1.json in repo | [x] |
| P2-T04 | agent-isolation.md | 9 | 7 | worker not built | [x] |
| P2-T05 | mcp-gateway.md | 9 | 6 | gateway not built | [x] |
| P2-T06 | platform-extensibility.md | 9 | 8 | — | [x] |
| P2-T07 | api-style.md | 8 | **8** | openapi.yaml stub | [x] |
| P2-T08 | threat-model.md | 9 | 7 | escape tests not coded | [x] |

## P3 — Modules (summary)

| Module | Doc | Impl | Impl blocker | Done |
|--------|-----|------|--------------|------|
| M00-cabinets | 8.5 | **7** | BL-04 capabilities | [x] |
| M01-projects | 8.5 | **6** | needs M00 + storage | [x] |
| M02-specs-kp | 8.5 | **6** | pipeline port | [x] |
| M03-prompts | 8.5 | **7** | BL-07 AGENTS adapt | [x] |
| M04-catalogs | 9.0 | **7** | s4b DDL migration | [x] |
| M05-integrations | 8.5 | **6** | S4B client extract | [x] |
| M06-mcp | 8.5 | **5** | gateway process | [x] |
| M07-agent | 8.5 | **4** | BL-01 spikes | [x] |
| M08-tenants | 8.0 | **6** | I1 migrations | [x] |
| M09-operations | 8.0 | **5** | metrics/billing | [x] |

## P4 — Agent runtime

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P4-T01 | providers.md | 8 | 5 | — | [x] |
| P4-T02 | provider-port.md | 8 | 6 | interface not in code | [x] |
| P4-T03 | cursor-sdk-adapter.md | 9 | 6 | — | [x] |
| P4-T04 | codex-cli-spike.md | 7 | **3** | **spike not executed** | [x] |
| P4-T05 | claude-code-spike.md | 7 | **3** | **spike not executed** | [x] |
| P4-T06 | worker-isolation.md | 9 | 5 | no k8s NetworkPolicy | [x] |
| P4-T07 | prompt-envelope.md | 9 | 7 | — | [x] |
| P4-T08 | no direct PG in worker | 8 | 5 | CI grep not wired | [x] |

## P5 — Frontend

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P5-T01 | architecture.md | 9 | 5 | — | [x] |
| P5-T02 | design-system.md | 9 | 5 | — | [x] |
| P5-T03 | widget-catalog.md | 8 | 5 | widgets stub only | [x] |
| P5-T04 | cabinet-shell.md | 9 | 5 | NavGate stub | [x] |
| P5-T05 | screens-inventory.md | 9 | 5 | — | [x] |
| P5-T06 | md-editor.md | 8 | 4 | — | [x] |
| P5-T07 | responsive.md | 8 | 4 | — | [x] |

## P6 — Backend

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P6-T01 | structure.md | 9 | **8** | src/ scaffold | [x] |
| P6-T02 | erd-v0.md | 9 | 7 | no migrations yet | [x] |
| P6-T03 | alembic.md | 8 | **7** | env.py wired | [x] |
| P6-T04 | rls-policies.md | 9 | 6 | policies not in SQL | [x] |
| P6-T05 | openapi-layout.md | 8 | **7** | stub only | [x] |
| P6-T06 | secrets.md | 8 | 5 | — | [x] |
| P6-T07 | object-store.md | 9 | 6 | — | [x] |

## P7 — Infrastructure

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P7-T01 | topology.md | 9 | 6 | — | [x] |
| P7-T02 | terraform.md | 8 | **5** | infra/terraform README | [x] |
| P7-T03 | k3s-services.md | 8 | 5 | infra/k8s README | [x] |
| P7-T04 | argocd.md | 8 | 5 | — | [x] |
| P7-T05 | github-actions.md | 8 | **5** | infra/github-actions README | [x] |
| P7-T06 | github-runner-local.md | 9 | 6 | runner not installed | [x] |
| P7-T07 | wsl-dev.md | 8 | 7 | — | [x] |
| P7-T08 | env-matrix.md | 8 | 6 | — | [x] |

## P8 — Migration + pack

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P8-T01 | commerce-boundary.md | 9 | 8 | — | [x] |
| P8-T02 | commerce-semantics.md | 9 | 8 | — | [x] |
| P8-T03 | pack.json + cabinet-profile.json | 9 | 8 | — | [x] |
| P8-T04 | pack file inventory | 8 | **8** | prompts in repo | [x] |
| P8-T05 | capabilities.s4b validation | 9 | 8 | schema allOf | [x] |
| P8-T06 | _template pack without s4b | 8 | 7 | — | [x] |

## P9 — Master checklists

| ID | Задача | Doc | Impl | Impl Gaps | Done |
|----|--------|-----|------|-----------|------|
| P9-T01 | PROGRESS.md | 10 | 10 | dual scale | [x] |
| P9-T02 | master-delivery.md | 9 | 8 | — | [x] |
| P9-T03 | phase-gates.md | 9 | 8 | Impl gates added | [x] |
| P9-T04 | review-protocol.md | 9 | 9 | Impl section | [x] |
| P9-T05 | audit | 9 | 9 | Impl 6.7 documented | [x] |
| P9-T06 | submodule push | 10 | 10 | — | [x] |
| P9-T07 | docs/README.md map | 9 | 9 | 10-implementation | [x] |

---

## I0 — Scaffold (done 2026-08-20)

| ID | Task | Impl | Done |
|----|------|------|------|
| I0-T01 | FastAPI + Alembic scaffold | 8 | [x] |
| I0-T02 | Flutter create + folders | 8 | [x] |
| I0-T03 | ci-schemas.yml + ci-api.yml | 8 | [x] |
| I0-T04 | Health integration test | 8 | [x] |

## I1 — Auth / M08 (done 2026-08-20)

| ID | Task | Impl | Done |
|----|------|------|------|
| I1-T01 | Alembic tenants schema + RLS | 8 | [x] |
| I1-T02 | prodavan_app role (non-superuser) | 8 | [x] |
| I1-T03 | register/login/refresh/me | 8 | [x] |
| I1-T04 | JWT + bcrypt | 8 | [x] |
| I1-T05 | NEG-TEN-01 RLS test | 8 | [x] |

## I2 — M00 Cabinets + Pack seed (done 2026-08-20)

| ID | Task | Impl | Done |
|----|------|------|------|
| I2-T01 | cabinet_profiles + cabinets schema + s4b trigger | 8 | [x] |
| I2-T02 | Pack seeder (prompts/shops/theme → storage) | 8 | [x] |
| I2-T03 | capabilities snapshot (`procurement.*` → BL-04) | 8 | [x] |
| I2-T04 | CRUD / switch / manifest / profiles API | 8 | [x] |
| I2-T05 | NEG-CAB-004 archived switch, NEG-CAB-006 slug | 8 | [x] |

## I3 — M01 Projects + M03 Prompts (done 2026-08-20)

| ID | Task | Impl | Done |
|----|------|------|------|
| I3-T01 | projects table + RLS (cabinet scope) | 8 | [x] |
| I3-T02 | Project storage: inbox/runs/export, project.json, commerce.sqlite | 8 | [x] |
| I3-T03 | CRUD / open / stats + active cabinet deps | 8 | [x] |
| I3-T04 | Prompts tree + file GET | 8 | [x] |
| I3-T05 | NEG-PRJ-002, NEG-PRJ-003 tests | 8 | [x] |
| I3-T06 | Prompt PUT + ETag + versions/rollback | 8 | [x] |
| I3-T07 | Flutter cabinet shell + NavGate + auth | 8 | [x] |

## Next: I4 — M02 Specs / KP pipeline

Spikes (parallel): P4-T04, P4-T05 → Impl 8+.
