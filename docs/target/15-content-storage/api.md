# Content API (v1)

Prefix: `/api/v1/content`

## Assets

| Method | Path | Описание |
|--------|------|----------|
| GET | `/assets?company_id=` | Список assets компании (с ACL-фильтром) |
| POST | `/assets` | Создать asset |
| GET | `/assets/{id}` | Получить asset |
| PATCH | `/assets/{id}` | Обновить meta |
| DELETE | `/assets/{id}` | Удалить asset + blobs |
| POST | `/assets/{id}/versions` | Presigned PUT для новой версии |
| POST | `/assets/{id}/versions/{vid}/finalize` | Зафиксировать размер/hash после upload |
| GET | `/assets/{id}/download` | S3: 302 presign; local: inline body |
| GET/PUT | `/assets/{id}/acl` | ACL asset (read / admin write) |

## Aliases

| Method | Path | Описание |
|--------|------|----------|
| GET | `/aliases?company_id=` | Список aliases компании |
| POST | `/aliases` | Создать alias (slug fixed) |
| GET | `/aliases/{id_or_slug}` | Meta alias (ID = `cal_{hex16}`) |
| PATCH | `/aliases/{id}` | Обновить label/status |
| DELETE | `/aliases/{id}` | Удалить alias |
| POST | `/aliases/{id}/bind` | Привязать asset (+ optional version) |
| DELETE | `/aliases/{id}/bind` | Unbind (alias остаётся) |
| GET | `/aliases/{id}/bindings` | История bindings |
| GET | `/aliases/{slug}/resolve` | S3: 302; local: inline |
| GET/PUT | `/aliases/{id}/acl` | ACL alias |

## Типовые сценарии

**Статичная ссылка, меняющийся файл:**

1. POST `/aliases` → slug
2. POST `/assets` + POST `/assets/{id}/versions` → presigned PUT + finalize
3. POST `/aliases/{id}/bind` → rebind без смены slug

**Ссылка до файла:**

1. POST `/aliases` (placeholder)
2. … позже … bind when ready

**Attachments (opt-in):** `CONTENT_ATTACHMENTS_VIA_ASSETS=true` → upload через Content Service, `storage_ref=content://{asset_id}`.
