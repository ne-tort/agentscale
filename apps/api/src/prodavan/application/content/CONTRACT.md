# Content API — explicit contract

Status: authored in this slice. **No DDL, no Alembic revision, no web change.**
Authoritative runtime path: `apps/api/src/prodavan/api/v1/content.py`
(`/api/v1/content/*`). Pure policy and blockers:
`apps/api/src/prodavan/application/content/contracts.py`.

Scope of this document: what the Content API is, what already existed, what this
slice added, which invariants are enforced server-side, and which scenarios
remain blocked by missing schema.

---

## 1. Object model (from `infrastructure/persistence/models/content.py`)

| Table | Meaning | Immutability |
|---|---|---|
| `content_assets` | Logical file: owner, visibility, mime, title, tags | Only metadata is mutable |
| `content_blob_versions` | One immutable byte identity per `(asset_id, version)` | `sha256` sealed once; new bytes ⇒ new row |
| `content_aliases` | Stable permalink (`slug`) that can be re-pointed | Row is mutable, history is not |
| `content_alias_bindings` | Time-bounded alias → asset version edge | Append-only; old rows get `superseded_at` |
| `content_acl_entries` | `(resource_kind, resource_id) → principal` grants | Replaced wholesale per resource |
| `content_asset_links` | 1:1 `(link_kind, link_id) → asset` domain edge | Re-pointed explicitly |

There is **no** `status`, `archived_at` or `deleted_at` column on `content_assets`,
and no content-scoped idempotency receipt table. Both gaps drive §6.

---

## 2. Principal, tenant and scope — server-side only

`application/content/scope.py::resolve_actor_scope` derives a `ContentActorScope`
from the verified JWT **and persisted relations**:

| Field | Source |
|---|---|
| `employee_id` | `get_current_employee` (active employee row for the JWT `sub`) |
| `tenant_ids` | `memberships` rows for that employee |
| `role_keys` | JWT realm roles **plus** membership roles |
| `cabinet_ids` / `project_ids` | only those ids the caller is actually attached to |
| `is_platform_admin` | `platform.admin` role on the principal |

Rules:

1. `owner_company_id` in a request body is a **selector**, validated against
   `tenant_ids`. A foreign id ⇒ `404 NOT_FOUND` (`company not found`), never `403`.
2. `X-Cabinet-Id` / `X-Project-Id` are accepted only when the caller has an
   active cabinet assignment (or a company grant on that cabinet) / project
   assignment. Otherwise the header value is **dropped**, not trusted.
3. Creating a **platform-owned** object (`owner_company_id = null`) requires
   `platform.admin`; a company tenant cannot publish platform-wide content.
4. `owner_scope` is always derived from the validated owner. The client never
   supplies it.

## 3. Immutable, digest-bound versions

* `POST /assets/{id}/versions` opens an **unsealed** row and returns a presigned
  `upload_url` (plus `storage_key` — see §7).
* Optional `sha256` in the begin body, and/or `X-Content-SHA256` on upload /
  finalize, declare the expected digest. Bytes whose digest differs are rejected
  **before** they are written: `422 CONTENT_DIGEST_MISMATCH`.
* `PUT .../upload` and `POST .../finalize` **seal** the row (`sha256` +
  `object_metadata.sealed = true`).
* Any further upload into a sealed row is `409 CONTENT_VERSION_SEALED`. New
  content ⇒ new version row (`version = max + 1`).
* `finalize` re-reads the stored bytes and refuses to seal when they no longer
  match an already recorded digest (`422 CONTENT_DIGEST_MISMATCH`).
* Downloads and alias binding require a sealed version (`422 NOT_READY`
  otherwise) so a presigned URL can never serve a half-uploaded object.

## 4. ACL matrix

Two gates, in order: **tenant boundary**, then **ACL**.

| Situation | Result |
|---|---|
| Object owned by another tenant | `404 NOT_FOUND` |
| Same tenant, no grant, `visibility=company` | allowed (company default) |
| Same tenant, no grant, `visibility=private`, not creator | `403 FORBIDDEN` |
| Explicit `read`/`write`/`admin` grant matches | allowed per permission |
| Platform-owned object, no grant | `403` (ACL decides; boundary does not apply) |
| Platform admin | allowed |

Principal kinds accepted by `PUT .../acl`:
`employee`, `company`, `platform`, `cabinet`, `project`, `role`.

| Kind | `principal_id` | Extra validation |
|---|---|---|
| `employee` | `emp_…` | must be a member of the resource tenant |
| `company` | `co_…` | a non-admin may only grant its **own** tenant (`422 CONTENT_ACL_CROSS_TENANT`) |
| `cabinet` | `cab_…` | cabinet must exist and belong to the resource tenant |
| `project` | `prj_…` | project must exist and belong to the resource tenant |
| `role` | `platform.admin`, `company`, `company.admin`, `employee`, `member` | `platform.*` only by a platform admin |
| `platform` | literally `platform` | only a platform admin |

ACL rewrite (`PUT`) requires `admin`-level access: an explicit `admin` grant, the
private creator, the owning tenant, or `platform.admin`. Duplicate
`(kind, id, permission)` triples ⇒ `422 CONTENT_ACL_DUPLICATE`.

Every operation re-evaluates the ACL server-side; there is no cached or
client-asserted authorization.

## 5. Endpoints

Existing (unchanged wire shape): `GET/POST /assets`, `GET/PATCH/DELETE
/assets/{id}`, `POST /assets/{id}/versions`, `PUT
/assets/{id}/versions/{vid}/upload`, `POST /assets/{id}/versions/{vid}/finalize`,
`GET /assets/{id}/download`, `GET/POST /aliases`, `GET/PATCH/DELETE
/aliases/{id}`, `POST/DELETE /aliases/{id}/bind`, `GET
/aliases/{id}/bindings`, `GET /aliases/{slug}/resolve`, `GET/PUT
/assets/{id}/acl`, `GET/PUT /aliases/{id}/acl`.

Added by this slice:

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/assets/{id}/versions` | Deterministic version history (`version` desc, cursor) |
| `GET` | `/assets/{id}/versions/{vid}` | Single version metadata |
| `GET` | `/assets/{id}/links` | Asset ↔ entity edges |
| `POST` | `/assets/{id}/links` | Create an edge (`replace` to re-point) |
| `DELETE` | `/assets/{id}/links/{link_id}` | Remove an edge |
| `DELETE` | `/assets/{id}?purge=true[&cascade=true]` | Explicit platform-admin purge |
| `GET` | `/assets?cursor=…`, `/aliases?cursor=…` | Keyset pagination |
| `GET` | `/aliases/{slug}/resolve?proxy=1` | Stream bytes instead of 302 |

`DELETE /assets/{id}` **without** `purge` is the archive path and fails closed
(§6). Status codes: `200` for reads and mutations (matching the shipped client),
`404` / `403` / `409` / `413` / `422` for the documented errors.

### Idempotency

`POST /assets` and `POST /aliases` read `Idempotency-Key` (1–128 chars). The
resource id is `sha256("content-asset\0{actor}\0{tenant}\0{key}")[:16]`, so a
replayed key resolves to the same row:

* same key + same request projection ⇒ the original resource (`200`);
* same key + different body ⇒ `409 IDEMPOTENCY_REPLAY`.

The projection is `owner_company_id, owner_scope, visibility, mime, title,
sorted(tags)` (aliases: `slug, owner, scope, visibility, label, description,
metadata`). A receipt table is **not** required for this behaviour; it is only
required for byte-identical response replay (§6).

## 6. Blockers (recorded, not worked around)

1. **Soft delete / archive — `content_assets_archive_columns_missing`.**
   `content_assets` has no `status`/`archived_at`/`deleted_at`. An archive flag
   cannot be persisted, archived assets cannot be listed, restored or excluded
   from reads, and a `DELETE` that "succeeds" without a column would be a lie.
   The route returns `409 CONTENT_ARCHIVE_UNAVAILABLE` with the blocker text.
   Requires a reviewed revision adding a nullable `archived_at` (+ index) before
   `DELETE` can archive. **No DDL was added here.**
   Evidence is still preserved on every destructive path:
   `AssetService.dependency_report()` lists live alias bindings, asset links and
   version count; purge refuses to run while dependents exist unless
   `cascade=true`, and `cascade` only supersedes bindings/links — it never
   rewrites binding history.
2. **Idempotency receipts — `content_idempotency_receipts_table_missing`.**
   Derived-id replay covers the observable contract (same key ⇒ same resource,
   different body ⇒ `409`). Exact response replay needs a receipt table.
3. **`delete` semantics split.** The public route archives (blocked) or purges
   (platform admin only). `AssetService.delete()` — the unconditional hard delete
   used by `application/projects/attachment_service.py` — is now reachable **only**
   from owning domain services, never from HTTP.
4. **Alias `DELETE` disables, it does not destroy.** `content_aliases.status`
   already models `disabled`. Deleting the row would cascade away
   `content_alias_bindings`, i.e. the evidence of what the permalink served.
   Response: `{"deleted": true, "retained_evidence": true, …}`.

## 7. Compatibility notes

* `storage_key` is still returned by `POST /assets/{id}/versions`. Removing it
  would break `tests/integration/test_content.py` and the module/document upload
  adapters. It is **never** returned by version list/detail.
* Response bodies stay flat (`{"items": …}`, single object) rather than the
  `data`/`meta` envelope in `docs/02-architecture/api-style.md`, because the
  shipped `content-ops` client and the module adapters already parse that shape.
  Changing the envelope is a client-visible breaking change and is out of scope.
* Errors are RFC 7807 `application/problem+json` produced by
  `domain.errors.AppError`; codes are stable and listed in
  `contracts.ContentErrorCode`.
* `GET /assets/{id}/download` now sets `Content-Disposition`,
  `X-Content-SHA256` and `X-Content-Size`. Previously the client had no filename.

## 8. Deliberate non-goals

* No changes to `api/v1/resources.py`, `application/resources/**`, Alembic,
  `models/**`, or the web app.
* No new tables; every new operation is expressible on the existing six content
  tables.
* No M04E artifact mapping: `migration_definition_version/content_adapter.py`
  stays blocked (`m04e_artifact_content_mapping_unproven`) — nothing here proves
  that `m04e_dynamic_app_versions.artifact_uri` maps to a content blob.
