# M03 — Persistence: промпты

## Working tree

Files on disk = source of truth. No separate PG content blob.

## .prompts-version.json

```json
{
  "current_version_id": "ver_01JXYZ",
  "working_tree_dirty": false,
  "last_saved_at": "2026-08-20T08:00:00Z"
}
```

## Version storage

```text
storage/cabinets/{tid}/{cid}/prompts/.versions/
└── {version_id}/
    ├── manifest.json
    └── files/              # content-addressed or mirrored paths
        ├── AGENTS.md
        └── profiles/kp/...
```

### manifest.json

```json
{
  "version_id": "ver_01JXYZ",
  "cabinet_id": "0195a1b2-...",
  "label": "После правки S4B политики",
  "parent_version_id": "ver_01JABC",
  "created_at": "2026-08-20T08:00:00Z",
  "created_by": "user-uuid",
  "files": [
    { "path": "AGENTS.md", "sha256": "abc...", "size_bytes": 4096 }
  ]
}
```

## PostgreSQL index (optional)

```sql
CREATE TABLE prompt_versions (
  version_id      TEXT PRIMARY KEY,
  cabinet_id      UUID NOT NULL REFERENCES cabinets(id),
  label           TEXT,
  parent_version_id TEXT,
  files_count     INT NOT NULL,
  manifest_sha256 TEXT NOT NULL,
  created_by      UUID NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_prompt_versions_cab ON prompt_versions (cabinet_id, created_at DESC);
```

Content remains on filesystem/object storage.

## ETag / optimistic locking

ETag = sha256 content. PUT requires match or 409 `ETAG_MISMATCH`.

## Auto-snapshot debounce

Redis key `prm:autosnap:{cid}` TTL 300s prevents version spam.

## Prune job

Delete versions beyond retention keeping:

- last 50 versions
- all labeled versions
- versions referenced by audit
