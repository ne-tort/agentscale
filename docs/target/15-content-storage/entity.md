# Content Storage — entities

## Слои

```text
application/content   — AssetService, AliasService, Binding, ACL, Download
infrastructure/files  — FileStoreManager (S3/local, presign, prefix ops)
```

**Import boundary:** `application/content` не импортирует boto3/minio; handlers не ходят в MinIO напрямую. Единый `FileStoreManager` обслуживает и `blobs/`, и legacy-префиксы `projects/` / `cabinet_packages/`.

---

## Asset (логический файл)

Пользовательский «файл»: метаданные, владение, ACL, версии blob.

| Таблица | Ключевые поля |
|---------|---------------|
| `content_assets` | `owner_scope`, `owner_company_id`, `visibility`, `mime`, `title`, `tags`, `created_by` |
| `content_blob_versions` | `asset_id`, `version`, `storage_key`, `size`, `sha256`, `etag` |

Asset **не** имеет публичного URL. Доступ — ACL + presigned URL от File Service.

---

## Alias (first-class entity)

**Alias — отдельная сущность**, не поле asset. Permalink namespace; slug **никогда не меняется** при rebind.

| Таблица | Ключевые поля |
|---------|---------------|
| `content_aliases` | `slug` (unique), `owner_scope`, `owner_company_id`, `visibility`, `label`, `status` |

Свойства:

- Может существовать **без** привязанного файла (placeholder)
- **Собственный ACL** (read alias ≠ read target asset)
- **Собственное владение** — не наследует от asset автоматически

---

## AliasBinding

Temporal many-to-one: один alias → один **текущий** target.

| Таблица | Поля |
|---------|------|
| `content_alias_bindings` | `alias_id`, `asset_id`, `blob_version_id` (nullable = latest), `effective_at`, `superseded_at` |

Rebind: закрыть текущую binding (`superseded_at=now`), создать новую → slug тот же.

---

## ACL

| Таблица | Поля |
|---------|------|
| `content_acl_entries` | `resource_kind` (`asset` \| `alias`), `resource_id`, `principal_kind`, `principal_id`, `permission` |

Default: `visibility=company` → все employees компании-владельца; `private` → creator + explicit ACL.

---

## Domain links (L3)

| Таблица | Назначение |
|---------|------------|
| `content_asset_links` | `asset_id` ↔ project_attachment / project / module |

---

## Миграция attachments

Флаг `CONTENT_ATTACHMENTS_VIA_ASSETS=true`: upload создаёт `content_assets` + link на `project_attachments.content_asset_id`. Legacy `storage_ref` сохраняется для совместимости.
