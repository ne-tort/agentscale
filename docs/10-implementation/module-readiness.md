# Module implementation readiness

Матрица **Impl** по модулям M00–M09 на 2026-08-20 (после bridge-артефактов).

Шкала Impl: см. [`09-checklists/implementation-readiness.md`](../09-checklists/implementation-readiness.md).

| Module | Doc | Impl | Можно кодить? | Gaps |
|--------|-----|------|---------------|------|
| M00 Cabinets | 8.5 | **7** | Частично (после I0) | Capability registry BL-04; нет Alembic |
| M01 Projects | 8.5 | **6** | После M00 | OpenAPI paths missing; no object-store driver |
| M02 Specs/KP | 8.5 | **6** | После M01 | Pipeline MCP not ported; kp template path |
| M03 Prompts | 8.5 | **7** | После M00 | Seed AGENTS needs Prodavan edit (BL-07) |
| M04 Catalogs | 9.0 | **7** | После M00 | system DB `s4b` DDL sketch only in ERD |
| M05 Integrations | 8.5 | **6** | После M04 | S4B client lib not extracted from Commerce |
| M06 MCP | 8.5 | **5** | После M05 | Gateway process spec only |
| M07 Agent | 8.5 | **4** | **Нет** | Worker pod, SSE, BL-01 spikes |
| M08 Tenants | 8.0 | **6** | I1 first | Auth tables not migrated |
| M09 Operations | 8.0 | **5** | После I6 | Billing hooks thin |

**Среднее Impl модулей:** 6.0

---

## BL-04 — Capability naming

В repo **два namespace** (нужно слить до I2):

| Источник | Примеры |
|----------|---------|
| `cabinet-profile.json` (pack) | `procurement.kp`, `procurement.s4b`, `procurement.pipeline` |
| `cabinet-profiles.md` (ADR пример) | `search.s4b`, `kp.export`, `agent.session` |

**Решение для I2 (рекомендация):** canonical = **`procurement.*`** в pack и JWT; ADR reference example обновить при старте I2. FeatureGate pattern: `procurement.s4b` → nav item `s4b_settings`.

---

## Контракты в repo (Impl+)

| Артефакт | Путь | Статус |
|----------|------|--------|
| Cabinet profile schema | `packages/schemas/cabinet-profile/v1.json` | ✅ |
| Electronics pack | `packages/cabinet-packs/electronics-procurement/` | ✅ |
| Prompt seeds | `.../prompts/AGENTS.md`, `.../prompts/kp/*.md` | ✅ (Commerce copy) |
| OpenAPI stub | `apps/api/openapi/openapi.yaml` | ✅ ~5% paths |
| ERD | `docs/05-backend/erd-v0.md` | ✅ doc only |
| RLS | `docs/05-backend/rls-policies.md` | ✅ doc only |
| MCP tool list | per-module `mcp-tools.md` | ✅ doc only |

---

## Per-module «Definition of Ready» (DoR)

Модуль готов к sprint, когда:

- [ ] Impl ≥ 7 в таблице выше
- [ ] OpenAPI paths для модуля добавлены в `openapi.yaml`
- [ ] Первая Alembic revision для schema модуля
- [ ] ≥ 3 пункта из `checklist-implementation.md` помечены как автотесты
- [ ] Upstream модуль Impl ≥ 7

---

## Negative tests (must implement)

| ID | Module | Test |
|----|--------|------|
| NEG-CAB-01 | M00 | User A cannot switch to cabinet B (other tenant) |
| NEG-CAT-01 | M04 | Non-electronics profile cannot attach `s4b` system DB |
| NEG-S4B-01 | M05 | `include_on_order` rejected at MCP gateway |
| NEG-AGT-01 | M07 | Worker pod env has no `DATABASE_URL` |
| NEG-TEN-01 | M08 | RLS hides rows without `app.tenant_id` |

Документированы в module `security.md`; код — I1–I6.
