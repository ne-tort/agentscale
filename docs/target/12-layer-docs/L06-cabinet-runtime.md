# L06 — Cabinet Runtime

| Поле | Значение |
|------|----------|
| Status | done |
| Quality | 8 |
| Quality note | API Runtime: instance+meta+rows+MCP+bundle+packages; UI interpreters L05; sandbox start L07; L04 quotas soft stub |
| Plan | [L06](../11-implementation-plan/L06-cabinet-runtime.md) |
| Canon | [05-cabinets](../05-cabinets/) |
| Last updated | 2026-08-24 — restored as-built encoding |
| Owners | — |

---

## Семантика

CabinetInstance: schema-per-instance, meta+UI, cabinet.*, MCP packages, bundles. Ownership Employee+Company+Admin; peers isolated.

**Не** container (L07); не AgentPort (L08); не static pack.

## Что сделано

| Сделано | Gaps / соседний слой |
|---------|----------------------|
| Instance CRUD + schema-per-instance + Base tabs; archive + hard-delete | UI interpreters (L05) |
| Meta tables create + tabs list | columns/views mutate API (create/update/delete subset) |
| Rows query/upsert/delete (physical + json_document) | — |
| cabinet.* MCP dispatcher | audit events (MCP + meta HTTP) |
| platform_event SPI → meta_audit + manifest `platform_events`; opt-in `src/on_platform_event.py` (stdin/stdout JSON lite) | full MCP stdio protocol; bubblewrap |
| Bundle v1 export/import → new schema (+ packages) | |
| MCP packages validate/deploy/list/disable/export + rematerialize projects | sandbox process start (L07 materialize) |
| Soft max 20 packages/cabinet | Company quotas (L04) — wired via CompanyQuotaService |
| Materialize via L07 project runtime | Real FS layout wired from L07 materialize |
| Cabinet create/import enforces `assert_can_create_cabinet` + bundle size | |

## Как сделано

1. `package_codec.validate_package_zip` — runtime/entry/shell allowlist, path safety.
2. `CabinetPackagesService` — registry `meta_mcp_packages` + file artifacts under `storage/cabinet_packages/`.
3. Bundle pack/unpack embeds `mcp_packages/*.zip`; import redeploys into new instance.
4. HTTP `/mcp-packages` + MCP tools `cabinet.mcp_packages.*`.
5. `CompanyQuotaService.assert_can_create_cabinet` on create/import path; package/bundle quotas on deploy/import.

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-INSTANCE | CRUD + ACL | **live** |
| C-META-DATA | meta + rows | **live** (subset) |
| C-CABINET-MCP | dispatcher | **live** (subset) |
| C-BUNDLE | export/import v1 | **live** |
| C-MCP-PKG | deploy/list/disable/export | **live** (registry; no sandbox run) |
| C-MATERIALIZE | consumed from L07 | **live** (L07 object-ws + dual-read) |

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| Package deploy + strict manifest | done | object-store read/write; replace deletes old zip; archive wipes prefix; hard-delete drops schema |
| Sandbox start on materialize | live (subset) | L07 prepare + opt-in local spawn; k8s hole |
| Company/Admin quotas | done | L04 CompanyQuotaService on create/import/packages |
| columns/views CRUD | done | PATCH column type + metadata |
| UI meta interpreters | live (subset) | tables settings + custom tabs + view edit |
| Non-system tabs from bundle | done | import_bundle_views_and_tabs |
| Hard-delete archived instance | **done** (subset) | `DELETE /cabinets/{id}` DROP SCHEMA + wipe; orphan schema GC admin/Celery/CronJob |
| Redis cache / rate limits | **done** (subset) | MCP call RL + admin ops RL (C-CACHE) |

## Проверка

```text
cd apps/api && ruff check src tests && pytest tests/unit/test_cabinet_*.py -q
```

## Оценка качества

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. DoD API | 2 | Runtime API closed; UI/sandbox out of layer close |
| B. Контракты | 2 | C-MCP-PKG live |
| C. Инварианты | 2 | shell false, path allowlist, peer ACL |
| D. As-built | 2 | эта карточка |
| **Quality** | **8** | done для API Runtime Phase A |
