# `web_contract_parity` — cross-layer contract test

vitest mocks `fetch` (so it can only assert the string a client *would* send) and
pytest never loads a browser (so it cannot see what the UI reads). Neither side alone
can notice that a path, a field name or an error status drifted. This suite puts both
sides in one process:

| Side | Source | How |
|---|---|---|
| Server routes | `app.openapi()` on an app assembled from the real routers via `include_router` | `server_contract.operations()` |
| Server response fields | the serializer functions and pydantic models that build the body (`_definition_view`, `ResourceView.as_payload`, `AssetResource`, …) | `server_contract.dynamic_app_fields()` / `resource_fields()` / `content_fields()` |
| Server error statuses | the code→status tables plus every `status=` / positional status literal at a raise site (AST) | `server_contract.server_errors()` |
| Server vocabularies | the enums and constants the API accepts/emits | `server_contract.*_vocabulary()` |
| Web paths | `DYNAMIC_ROUTE_BINDINGS` in `api/dynamic.ts`, template literals in `content-ops/service.ts` | `web_contract.dynamic_route_bindings()` / `content_ops_calls()` |
| Web fields | the normalizers in `api/dynamic.ts`, `features/resources/wire.ts`, and the `content-ops` reader | `web_contract.read_field_aliases()` |
| Web error handling | the status→message tables a client switches on | `web_contract.status_keys()` |

Run it:

```powershell
cd apps/api; $env:PYTHONPATH="apps/api/src"
python -m pytest tests/migration/web_contract_parity -q
```

## What it checks

1. **Paths** (`test_paths_parity.py`) — every request the client can build matches a
   registered operation; the identifiers it interpolates are exactly the parameters the
   server declares (`company_id` excluded: the shell owns the tenant); every query key
   it sends is accepted. A missing path is a 404 only visible in a deployed browser.
2. **Dynamic payloads** (`test_dynamic_payload_parity.py`) — nothing the client
   *requires* is absent from the serializer; every wire name it reads is served or listed
   in `UNSERVED_BY_DESIGN` (and such a field must then be optional); every lifecycle
   value the serializer can emit is accepted by the client's union.
3. **Content payloads** (`test_content_payload_parity.py`) — client DTO fields exist on
   the response model; every ACL principal kind the model can return is accepted on the
   read path; every HTTP status the adapter maps is producible.
4. **Resource payloads** (`test_resource_payload_parity.py`) — the `wire.ts` normalizers
   read only served fields and produce every DTO field the screens render; lifecycle,
   plan-status and entry-status vocabularies match the server exactly; each capability
   the blocked `ResourceService` refuses has its API route pinned (so the blocker text
   cannot drift into claiming an endpoint is missing).

## Divergences this suite found (all fixed on the side that was wrong)

### Web side was wrong

| # | Divergence | Fix |
|---|---|---|
| 1 | `dynamic.ts` read `status`, `version`, `definition_id`, `version_id`, `cabinet_id`… The API sends `lifecycle`, `manifest_version`, `app_definition_id`, `app_version_id`, and a `scope: {kind, id}` object. Every response would have failed `requireEnum`/`requireString`. | Normalizers read the served names; `normalizeScope` expands `{kind, id}` into `scope`/`cabinetId`/`projectId`. |
| 2 | `dynamic.ts` required `kind`, `versionCount`, `latestVersion`, `requestedCapabilityCount`, `routes`, `manifest`, `createdBy`, `ownerCabinetId`, `ownerCompanyId`, `grantedBy`, `capabilityVersion` — none of which the API serializes. | Made optional; screens render `KIND_UNAVAILABLE` / `ROUTES_UNAVAILABLE` / `MANIFEST_UNAVAILABLE` instead of a guessed value. |
| 3 | A grant row carries a **set** of capabilities (`{"capabilities": [{name, decision, …}]}`); the client parsed one capability per row and expected `capability_id`/`granted_by` at the top level. | `normalizeGrants` expands each row into one DTO per capability; `normalizeGrant` returns the first. |
| 4 | `DYNAMIC_MODULE_STATUSES` omitted `revoked`, which `api_lifecycle()` can emit — one such row would fail the whole list response. | Added `revoked` (+ label and tone). |
| 5 | `content-ops` rejected ACL principal kinds `cabinet`, `project`, `role` that `ContentPrincipalKind` returns — an entire access list became unreadable. | `AclPrincipalKind` and `safeAclEntry` accept all six kinds. |
| 6 | `ResourceLifecycle` was `draft\|published\|retired\|blocked`; the API emits `draft\|active\|archived\|quarantined`. Every live resource rendered as neither active nor published. | Type + `resourceLifecycleLabel`/`Tone` use the served vocabulary; the workspace counts `active`. |
| 7 | `MaterializationStatus` was `planned\|applying\|applied\|failed\|blocked\|stale`; the plan API answers only `ready\|blocked`. `canSyncMaterialization` could never be true. | Plan status is `ready\|blocked`; the apply outcomes moved to `MaterializationOutcome`. |
| 8 | `MaterializationEntryStatus` allowed `applied`/`failed`; a plan's entries are always `planned`. The "applied/total" ring always read 0. | Entry status is `planned`; the ring counts the plan's entry set. |
| 9 | `features/resources` had no wire→DTO adapter at all: the screens read camelCase names (`activeVersionNumber`, `bindingCount`, `createdAt`) from a payload that only has snake_case (`active_version`, `binding_count`, `created_at`). | New `features/resources/wire.ts` is the single adapter, covered by tests 4 above. |
| 10 | `content-ops.listAssets` refused the whole page when *any* row lacked `owner_company_id`, which contradicts its own "exclude other-company assets" contract. | Rows attributable to another company are filtered; a row attributed to the requested company without an `id` is still refused. |

### Test/harness side was wrong

| # | Divergence | Fix |
|---|---|---|
| 11 | `ContentOpsPanel.acceptance.test.tsx` awaited `waitFor` **inside** the same `act` scope as the click. React 19 only commits when the scope exits, so three tests timed out for three seconds each on every interaction. | Assertions run after the `act` scope; `mountPanel` follows the same order. |
| 12 | `content_api/conftest.py` returned before its first `yield` when PostgreSQL was absent, so pytest reported `did not yield a value` and turned all 52 tests into setup errors instead of skips. | The fixture still yields on that branch. |
| 13 | `resources/fixtures.ts` and `view-model.test.ts` modelled plans as `stale`/`applying`/`failed` and entries as `applied` — states the plan API cannot produce. | Fixtures use `ready`/`planned`; the negative `canSync` cases use `blocked`, the only other plan state. |

### Accepted, with the reason recorded in code

- A **definition has no `kind` column**, so the API cannot send `kind`, `routes`,
  `latestVersion`, `requestedCapabilityCount`, `versionCount`, `ownerCompanyId` or
  `ownerCabinetId`. The client keeps them optional and the screen says "not reported by
  the server". Adding them is an API/DDL change, not a web fix.
- A **version view carries `artifact_digest`, not the manifest body**; the digest is the
  immutable evidence and the client treats it as such.
- A **grant view does not echo the accepted capability version** (it belongs to the
  manifest) and a **grant is issued for a scope+version, not for one instance**, so
  `capabilityVersion` and `instanceId` stay optional.
- A **plan view has no `profileId`/`revisionId`/`projectName`**; the DTO fields exist for
  the workspace shell and are left empty rather than fabricated.
- The `m04e_ports` / `m04e_ledger` / `migration_chain_host` failures seen during a
  full-suite run belong to other in-flight tasks (their source files are being edited
  outside this task's zone), not to this contract.
