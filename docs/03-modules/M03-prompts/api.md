# M03 — API: промпты

Base: `/v1/cabinets/{cid}/prompts`. Requires cabinet access.

## GET /v1/cabinets/{cid}/prompts/tree

**Response 200:**

```json
{
  "root": "prompts/",
  "tree": [
    { "path": "AGENTS.md", "type": "file", "size": 4096 },
    { "path": "profiles/kp/", "type": "dir", "children": [
      { "path": "profiles/kp/README.md", "type": "file", "size": 1200 }
    ]}
  ],
  "current_version": "ver_01JXYZ..."
}
```

---

## GET /v1/cabinets/{cid}/prompts/file

**Query:** `path=profiles/kp/04-search.md`

**Response 200:**

```json
{
  "path": "profiles/kp/04-search.md",
  "content": "# Поиск\n\n1. catalog\n2. s4b\n...",
  "sha256": "abc...",
  "updated_at": "2026-08-19T10:00:00Z",
  "etag": "\"abc...\""
}
```

---

## PUT /v1/cabinets/{cid}/prompts/file

**Headers:** `If-Match: "etag"` (optimistic lock)

**Request:**

```json
{
  "path": "profiles/kp/04-search.md",
  "content": "# Поиск\n\n..."
}
```

**Response 200:** updated file + new etag

Errors: `PATH_FORBIDDEN`, `ETAG_MISMATCH`, `FILE_TOO_LARGE`.

---

## POST /v1/cabinets/{cid}/prompts/versions

Create named snapshot.

**Request:**

```json
{
  "label": "После правки S4B политики"
}
```

**Response 201:**

```json
{
  "version_id": "ver_01JXYZ",
  "files_count": 24,
  "created_at": "2026-08-20T08:00:00Z"
}
```

---

## GET /v1/cabinets/{cid}/prompts/versions

List versions, newest first.

---

## GET /v1/cabinets/{cid}/prompts/versions/{version_id}

Manifest + optional `include_content=true`.

---

## POST /v1/cabinets/{cid}/prompts/versions/{version_id}/rollback

**Request:**

```json
{
  "confirm": true,
  "create_backup_version": true
}
```

---

## GET /v1/cabinets/{cid}/prompts/versions/{version_id}/diff

**Query:** `against=working|ver_other`

Returns unified diff per file.

---

## POST /v1/cabinets/{cid}/prompts/export

**Request:**

```json
{
  "format": "zip",
  "paths": null
}
```

`paths: ["AGENTS.md", "profiles/kp/"]` — partial export.

**Response 200:** download URL

```json
{
  "download_url": "/v1/cabinets/{cid}/prompts/export/download/exp_abc.zip",
  "expires_at": "2026-08-20T09:00:00Z",
  "sha256": "..."
}
```

---

## POST /v1/cabinets/{cid}/prompts/import

Multipart: `file` (zip) OR multiple `files[]`

**Query/form:** `mode=merge|replace`

**Response 200:**

```json
{
  "imported": 12,
  "updated": 3,
  "skipped": 1,
  "errors": []
}
```

---

## POST /v1/cabinets/{cid}/prompts/import/validate

Dry-run zip manifest check.
