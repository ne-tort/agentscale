# M04 — Storage: каталоги

## User catalogs layout

```text
storage/cabinets/{tid}/{cid}/catalogs/user/
├── {catalog_slug}/
│   ├── source.xlsx              # original upload (optional keep)
│   ├── catalog.sqlite           # normalized index
│   ├── manifest.json
│   └── .index-complete
└── ...
```

### manifest.json

```json
{
  "catalog_id": "cat_abc123",
  "slug": "distrib-main",
  "format": "sqlite",
  "schema": {
    "table": "products",
    "columns": { "pn": "part_number", "price": "price_rub", "title": "name", "stock": "qty" }
  },
  "indexed_at": "2026-08-20T07:00:00Z",
  "row_count": 125000
}
```

## Global catalogs (optional shared)

Tenant-wide read-only copies may symlink-read from:

```text
storage/tenants/{tid}/catalogs/shared/
```

User catalog in cabinet may reference shared import — still mounted under cabinet path for isolation.

---

## System databases

Документ **system-databases** описывает платформенные БД, которые оператор **не может удалить** и которые доступны только при выполнении условий профиля.

### s4b-cache

| Свойство | Значение |
| --- | --- |
| ID | `s4b-cache` |
| Тип | Virtual database (MCP `commerce-search`) |
| Профиль | **Только** `electronics-procurement` |
| Deletable | **false** — DELETE API всегда 403 |
| Источник данных | Live S4B.ru API + tenant cache table |
| Credentials | Tenant vault (см. persistence.md) |

#### Видимость по кабинету

```text
IF cabinet.profile_id == 'electronics-procurement'
   AND capabilities.integrations.s4b.enabled
THEN
   list_databases includes s4b-cache
ELSE
   s4b-cache ABSENT from list, MCP, UI
```

#### Cred states и поведение storage

| State | Live API fetch | Cache read | Write cache |
| --- | --- | --- | --- |
| `missing_credentials` | ✗ | ✓ stale if exists | ✗ |
| `credentials_valid` | ✓ | ✓ | ✓ |
| `credentials_invalid` | ✗ | ✓ read-only | ✗ |
| `rate_limited` | ✗ until reset | ✓ read-only | ✗ |

#### Cache storage path

```text
storage/tenants/{tid}/system/s4b-cache/
├── meta.json
└── shards/                   # optional file cache supplement
    └── {pn_prefix}/
```

meta.json:

```json
{
  "database_id": "s4b-cache",
  "tenant_id": "acme-corp",
  "deletable": false,
  "requires_profile": "electronics-procurement",
  "last_global_refresh": "2026-08-20T06:00:00Z",
  "credential_state": "credentials_valid"
}
```

#### S4B search constraints (storage layer)

- Persist only `in_stock=true` offers in cache entries used by M02
- Strip `on_order`, `listNoStock` rows at ingest to cache
- Tag each row: `"source": "s4b"`, `"trusted": per seller list`

### Другие system databases (future)

| id | Profile | Deletable |
| --- | --- | --- |
| `platform-holidays-ru` | all | false |

**No S4B** entries for non-electronics profiles — empty system-databases list.

---

## Credential vault storage (dev)

```text
storage/tenants/{tid}/vault/
└── s4b.enc                     # AES-256-GCM encrypted blob
```

Production: external vault only; local `.enc` disabled.

## Indexing pipeline

1. Upload → `uploading`
2. Convert xlsx/csv → sqlite (`indexing`)
3. Validate schema_hint
4. Write manifest + `.index-complete` → `ready`

## Quotas

| Resource | Default |
| --- | --- |
| User catalogs per cabinet | 20 |
| Max catalog size | 500 MiB |
| s4b-cache entries per tenant | 1M PN (LRU eviction) |

## Negative test IDs

| Test ID | Сценарий |
| --- | --- |
| NEG-CAT-ST-001 | rm s4b-cache directory as operator |
| NEG-CAT-ST-002 | s4b-cache meta on generic cabinet path |
| NEG-CAT-ST-003 | on_order row in cache sqlite |
