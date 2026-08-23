# L06 ? Cabinet Runtime

| ???? | ???????? |
|------|----------|
| Status | done |
| Quality | 8 |
| Quality note | API Runtime: instance+meta+rows+MCP+bundle+packages; UI interpreters L05; sandbox start L07; L04 quotas soft stub |
| Plan | [L06](../11-implementation-plan/L06-cabinet-runtime.md) |
| Canon | [05-cabinets](../05-cabinets/) |
| Last updated | 2026-08-23 ? MCP packages |
| Owners | ? |

---

## ?????????

CabinetInstance: schema-per-instance, meta?UI, cabinet.*, MCP packages, bundles. Ownership Employee+Company+Admin; peers isolated.

**??** container (L07); ?? AgentPort (L08); ?? static pack.

## ??? ???????

| ??????? | Gaps / ????????? ???? |
|---------|---------------------------|
| Instance CRUD + schema-per-instance + Base tabs | UI interpreters (L05) |
| Meta tables create + tabs list | columns/views mutate API |
| Rows query/upsert/delete (physical) | json_document rows |
| cabinet.* MCP dispatcher | audit events |
| Bundle v1 export/import ? new schema (+ packages) | |
| MCP packages validate/deploy/list/disable/export | sandbox process start (L07 materialize) |
| Soft max 20 packages/cabinet | Company quotas (L04) ? wired via CompanyQuotaService |
| Materialize stub port | Real FS layout (L07) |

## ??? ???????

1. package_codec.validate_package_zip ? runtime/entry/shell allowlist, path safety.
2. CabinetPackagesService ? registry meta_mcp_packages + file artifacts under storage/cabinet_packages/.
3. Bundle pack/unpack embeds mcp_packages/*.zip; import redeploys into new instance.
4. HTTP /mcp-packages + MCP tools cabinet.mcp_packages.*.

## ?????????

### ?????????

| ID | ????? | ?????? |
|----|-------|--------|
| C-INSTANCE | CRUD + ACL | **live** |
| C-META-DATA | meta + rows | **live** (subset) |
| C-CABINET-MCP | dispatcher | **live** (subset) |
| C-BUNDLE | export/import v1 | **live** |
| C-MCP-PKG | deploy/list/disable/export | **live** (registry; no sandbox run) |
| C-MATERIALIZE | stub port | **live** (stub) |

## Gaps vs ????? / DoD

| ?????????? | ?????? | ??????? |
|------------|--------|---------|
| Package deploy + strict manifest | done | |
| Sandbox start on materialize | hole | L07 |
| Company/Admin quotas | done | L04 CompanyQuotaService |
| columns/views CRUD | hole | |
| UI meta interpreters | hole | L05 |
| Non-system tabs from bundle | hole | |

## ????????

`	ext
cd apps/api && ruff check src tests && pytest tests/unit/test_cabinet_*.py -q
`

## ?????? ????????

| ??? | ???? 0?2 | ??????????? |
|-----|----------|-------------|
| A. DoD API | 2 | Runtime API closed; UI/sandbox out of layer close |
| B. ????????? | 2 | C-MCP-PKG live |
| C. ?????????? | 2 | shell false, path allowlist, peer ACL |
| D. As-built | 2 | ??? ???????? |
| **Quality** | **8** | done ??? API Runtime Phase A |
