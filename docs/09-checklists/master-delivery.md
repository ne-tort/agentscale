# Master delivery checklist

Сводный чеклист документации Prodavan (~250 пунктов). Детальный трекинг: [PROGRESS.md](PROGRESS.md).

## P0 Git (3)

- [x] P0-T01 fetch; main clean
- [x] P0-T02 branch prodavan/platform-foundation
- [x] P0-T03 no bot/docker edits

## P1 Repository (8)

- [x] P1-T01 gh repo ne-tort/prodavan
- [x] P1-T02 README.md
- [x] P1-T03 .gitignore
- [x] P1-T04 docs/apps/packages/infra scaffold
- [x] P1-T05 electronics-procurement pack
- [x] P1-T06 git submodule in Commerce
- [x] P1-T07 Commerce docs/08-prodavan
- [x] P1-T08 push both repos

## P2 Architecture (8 + vision)

- [x] P2-T01 overview.md
- [x] P2-T02 multi-tenancy.md
- [x] P2-T03 cabinet-profiles.md
- [x] P2-T04 agent-isolation.md
- [x] P2-T05 mcp-gateway.md
- [x] P2-T06 platform-extensibility.md
- [x] P2-T07 api-style.md
- [x] P2-T08 threat-model.md
- [x] 00-glossary.md
- [x] 01-vision/product-vision.md
- [x] 01-vision/domain-model.md

## P3 Modules M00–M09 (100 files)

Each module: README, domain, api, persistence, storage, mcp-tools, ui, security, checklist-implementation, checklist-review.

- [x] M00-cabinets (10)
- [x] M01-projects (10)
- [x] M02-specs-kp (10)
- [x] M03-prompts (10)
- [x] M04-catalogs (10 + system-databases.md)
- [x] M05-integrations (10)
- [x] M06-mcp (10)
- [x] M07-agent (10)
- [x] M08-tenants (10)
- [x] M09-operations (10)
- [x] 03-modules/README.md

## P4 Agent runtime (8)

- [x] providers.md
- [x] provider-port.md
- [x] cursor-sdk-adapter.md
- [x] codex-cli-spike.md
- [x] claude-code-spike.md
- [x] worker-isolation.md
- [x] prompt-envelope.md
- [x] agent no direct DB (documented)

## P5 Frontend (7)

- [x] architecture.md
- [x] design-system.md
- [x] widget-catalog.md
- [x] cabinet-shell.md
- [x] screens-inventory.md
- [x] md-editor.md
- [x] responsive.md

## P6 Backend (7)

- [x] structure.md
- [x] erd-v0.md
- [x] alembic.md
- [x] rls-policies.md
- [x] openapi-layout.md
- [x] secrets.md
- [x] object-store.md

## P7 Infrastructure (8)

- [x] topology.md
- [x] terraform.md
- [x] k3s-services.md
- [x] argocd.md
- [x] github-actions.md
- [x] github-runner-local.md (outside k3s)
- [x] wsl-dev.md
- [x] env-matrix.md

## P8 Migration + pack (6)

- [x] commerce-boundary.md
- [x] commerce-semantics.md
- [x] pack.json + cabinet-profile.json
- [x] shops/allowlist.json
- [x] theme/tokens.json
- [x] _template pack (no s4b)

## P9 Checklists (4)

- [x] PROGRESS.md
- [x] master-delivery.md
- [x] phase-gates.md
- [x] review-protocol.md

## Cross-cutting invariants

- [x] Tenant → Cabinet → Project hierarchy
- [x] cabinet_id RLS documented
- [x] S4B electronics-procurement only
- [x] S4B non-deletable, cred-gated
- [x] Icon-only UI, no default hints
- [x] core/widgets reuse
- [x] MCP Gateway tenant+cabinet scope
- [x] Agent worker sandbox path
- [x] Commerce boundary isolated
- [x] Local GH runner not in k3s

## Negative tests documented

- [x] NEG-CAB cross-cabinet
- [x] NEG-CAT S4B on wrong profile
- [x] ESC agent escape catalog
- [x] NEG-SKP s4b without creds

## Score summary

| Scale | Score | Gate |
|-------|-------|------|
| **Doc** | **8.7/10** | ✅ ≥ 8.5 |
| **Impl** | **6.7/10** | ⚠️ need ≥ 7 for I0 |

See [PROGRESS.md](PROGRESS.md) and [implementation-readiness.md](implementation-readiness.md).
