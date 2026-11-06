# M04E Content Adapter Readiness

Status: blocked. The adapter resolves only the URI on the exact persisted
`(tenant_id, definition_id, version_id)` row and refuses all byte reads.

## Exact Gate

`m04e_dynamic_app_versions.artifact_uri` has no trusted foreign key or
writer-proven relationship to `content_assets` or `content_blob_versions`.
The M04E version row stores `artifact_uri` and `artifact_digest`, but no asset
ID, blob-version ID, or storage key. Content blob rows store `asset_id` and an
opaque `storage_key`; the inspected source contains no writer that assigns a
legacy dynamic-app `artifact_uri` from either row. The URI format therefore
cannot safely be parsed into a storage key.

Additionally, `MinioFileStore.get_bytes` converts every S3 exception into
`FileNotFoundError`, so current FileStore behavior cannot distinguish a
confirmed missing object from an infrastructure/backend failure. A trusted
mapping and a missing-versus-infrastructure error contract are both required
before reading bytes and checking the persisted SHA-256 digest.

Evidence reviewed: `models/dynamic_app_definition_version.py`,
`models/dynamic_apps.py`, `models/content.py`, `application/content/asset_service.py`,
`application/dynamic_apps_definition_adapter/repository.py`,
`infrastructure/files/manager.py`, `infrastructure/files/port.py`, and
`infrastructure/files/minio_store.py`.

No DDL, live writes, API path, or formal verification was performed.