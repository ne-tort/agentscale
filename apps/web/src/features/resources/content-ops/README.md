# Content operations

This child feature implements the confirmed `/api/v1/content` asset workflow. It is isolated from the parent Resources UI and shared API/router/style files; the host supplies the service, company id, access-token callback, and optional workspace-binding link.

## Confirmed contracts used

- `GET /content/assets?company_id=...&limit=200` lists readable company assets.
- `POST /content/assets` creates an asset with `owner_company_id`, `visibility`, `mime`, `title`, and `tags`.
- `POST /content/assets/{asset_id}/versions` begins a version with `mime` and returns a version plus `upload_url` and method. The adapter discards `upload_url` and `storage_key`.
- `PUT /content/assets/{asset_id}/versions/{version_id}/upload` accepts raw bytes and `Content-Type`, returns size, SHA-256, ETag, and version metadata. The backend rejects bodies over 100 MiB.
- `GET /content/assets/{asset_id}/download?blob_version_id=...&proxy=true` streams one exact version through the API.
- `GET` and `PUT /content/assets/{asset_id}/acl` read and replace the complete asset ACL using `entries` with `principal_kind`, `principal_id`, and `permission`.

The mounted `content.py` router has no `GET` route for asset version history. `AssetService` serializes version metadata for upload responses, but there is no version-list method and no route to page or deterministically order existing versions. Do not infer a history URL or fabricate version records. The UI reports this blocker; its selector contains only versions returned by uploads in the current session, ordered by version number, creation time, then ID. The asset endpoint reports the latest version number without providing its ID. Company ownership is checked in the adapter and panel as well as requested through the company filter; API-side ACL correctness remains owned by the Content Service.

There is also no source-provenance endpoint or workspace-binding endpoint. The UI displays only metadata actually returned for the asset and session-known versions; it does not infer per-version uploader/source provenance. The adapter removes storage-key fields, URL/URI fields, and URL-like values from version object metadata before rendering. To unblock full history inspection, the backend must confirm a company-ACL-scoped history route, pagination parameters and response envelope, deterministic ordering, public version metadata fields, and exact-version download compatibility. Contract and UI coverage still needed once that route exists: traverse multiple pages, verify stable order across pages, select/download a listed exact version, render loading/empty/error/retry states, reject malformed pages, and verify API-side company ACL rejection. Current acceptance coverage verifies client company filtering and safe exact-version download errors only. The optional workspace link is contextual navigation supplied by the host UI, not a Content Service mutation.

Upload retries reuse a version ID if the API returned it before a failure. Clearing a staged upload does not remove backend records: there is no confirmed delete/unfinalize route for an unfinished version. Error presentation uses safe status messages and does not render backend payload details, authorization values, signed upload URLs, or storage keys.

Acceptance tests are colocated in this directory. Per task scope, they are intentionally not executed.
