# Equipment catalog sources

This feature is an isolated admin source/catalog configuration surface. It does not register a global route or read app context. The equipment workspace can mount `EquipmentSourcesPage` in its Source configuration area when its host injects `createEquipmentSourcesService({ getAccessToken })`; without it, the workspace shows a blocker or a read-only supplied snapshot. Catalog rows describe ingestion configuration and do not become procurement offers.

## Verified API boundary

- Admin module catalog: `GET /api/v1/admin/modules`, then `GET /api/v1/admin/modules/mod_equipment`.
- Source schema/actions: `GET /api/v1/admin/modules/mod_equipment/meta/documents`.
- Catalog rows: `GET|POST /api/v1/admin/modules/mod_equipment/data/catalogs`, `PATCH /api/v1/admin/modules/mod_equipment/data/catalogs/{row_id}`.
- Local files: `POST /api/v1/admin/modules/mod_equipment/content/upload` (multipart `file`, complete FileRef required).
- PostgreSQL DSN/password: `POST /api/v1/admin/modules/mod_equipment/secrets/upload`; only the returned secret reference is placed into the row.
- Probe, databases, tables, and reindex: `POST /api/v1/admin/modules/mod_equipment/actions/{metadata_action_id}/invoke`, with `row_id`. The action IDs are resolved from the actual `actions` metadata and must be enabled.

The adapter checks that the module is present in the admin module catalog, parses `catalogs_settings` and the action metadata, and never guesses a missing action. Save/action success is followed by a fresh API read. Queued indexing is polled while the catalog row reports `queued` or `indexing`.

## Deliberate API gaps

- Pause/resume controls are disabled. Metadata has a `pause_toggle`, but no isolated pause/resume endpoint exists. A catalog row `PATCH` fires owner row actions, including auto-index; patching only `paused` could therefore reindex instead of safely pausing.
- Create/update row APIs also auto-run remote probe and OpenSearch indexing actions. Saving is labelled as configuration save, and its resulting row is read back; the page does not claim that a queued task completed.
- The server does not return a prior secret value. DSN and password fields are write-only, secret uploads return only a reference, and references are not rendered. Error details are scrubbed before display.
- Reindex is blocked unless the metadata schema supplies required mappings and the action exists/enabled. API action failures or ambiguous responses remain errors.

Freshness uses `last_indexed_at` and `reindex_interval_hours` from the row. Errors preserve a visible warning and last successful time when provided. The backend row has no independent last-good timestamp; successful `last_indexed_at` is retained by the backend indexer across failures.

## Failures are codes, not sentences

`EquipmentSourcesRequestError` carries a `code` (`EquipmentSourcesErrorCode`), the HTTP `status` when there was a response, and an optional `detail` — the server's own `detail`/`title`, kept as data and shown only as a second line under the translated sentence. The technical `message` stays for logs and the stack and is never rendered.

The page maps each code with `SOURCE_ERROR_KEYS` to `equipment.srcErr<Code>`; the detail is scrubbed by `sanitizeCatalogMessage` exactly like any other server text. Adding a failure means adding a code, a key in both locales of `i18n/units/equipment.ts`, and an entry in that map.

Dates come from `catalogFreshness(body, locale, now)` and `i18n/format`, never from `toLocaleString()`: the interface language is what the reader sees, and it is not the browser's.

Tests cover metadata discovery, action gating, real route/payload contracts, multipart upload, secret references, freshness, error redaction, and blocked pause behavior. Per migration freeze these tests have not been executed.