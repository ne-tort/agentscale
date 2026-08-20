# M04 — UI: каталоги

## Catalogs page

**Route:** `/c/{cid}/settings/catalogs`

### User catalogs section

- Table: name, slug, format, rows, status, trusted badge
- Upload button → wizard (file, slug, trusted checkbox)
- Reindex, archive actions
- Indexing progress spinner

### System databases section

**Visible only:** `electronics-procurement` cabinet

| Column | s4b-cache |
| --- | --- |
| Name | S4B API Cache |
| Type | System |
| Deletable | **No** (lock icon, no delete button) |
| Cached P/N | count |
| Last refresh | timestamp |
| Credential state | badge |

#### Credential state badges

| State | Badge | Color | Action |
| --- | --- | --- | --- |
| missing_credentials | «Не настроено» | gray | Link to creds settings |
| credentials_valid | «Подключено» | green | Re-validate button |
| credentials_invalid | «Ошибка входа» | red | Fix credentials |
| rate_limited | «Лимит API» | orange | Show reset timer |

**Generic cabinet:** system databases section **hidden entirely** (not empty table — absent).

---

## S4B credentials settings

**Route:** `/settings/tenant/s4b-credentials`

Tenant-level (not per cabinet). Link from catalogs page when electronics cabinet active.

- Username field
- Password field (masked)
- Save → validate → state badge update
- Delete credentials → confirm → missing_credentials
- **Never** show password after save

Test connection button → POST validate.

---

## Rate limited UX

Banner on M02 run search phase:

«S4B временно недоступен (лимит API). Используется кэш. Повтор после {time}.»

---

## Negative test IDs (UI)

| Test ID | Сценарий |
| --- | --- |
| NEG-CAT-UI-001 | Delete button on s4b-cache — absent |
| NEG-CAT-UI-002 | System DB section on generic — hidden |
| NEG-CAT-UI-003 | Password shown in DOM after save |
