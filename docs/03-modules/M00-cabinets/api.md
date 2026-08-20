# M00 — API: кабинеты

Базовый префикс: `/v1`. Все эндпоинты требуют аутентификации и scope `cabinets:read` / `cabinets:write`.

## Заголовки

| Заголовок | Обязательность | Описание |
| --- | --- | --- |
| `Authorization` | да | Bearer JWT |
| `X-Tenant-Id` | да* | tid; *может быть в JWT claim `tid` |
| `X-Cabinet-Id` | для child APIs | активный cid; для CRUD кабинетов — опционален |
| `X-Request-Id` | рекомендуется | трассировка |

## Эндпоинты

### GET /v1/cabinet-profiles

Список доступных профилей (реестр).

**Response 200:**

```json
{
  "items": [
    {
      "id": "electronics-procurement",
      "version": "1.2.0",
      "display_name": "Закупки электроники",
      "description": "Спеки → поиск → КП, S4B, equipment cards",
      "deprecated": false,
      "capabilities_preview": {
        "s4b": true,
        "specs_kp": true
      }
    },
    {
      "id": "generic-assistant",
      "version": "1.0.0",
      "display_name": "Универсальный ассистент",
      "deprecated": false,
      "capabilities_preview": {
        "s4b": false,
        "specs_kp": false
      }
    }
  ]
}
```

---

### GET /v1/cabinets

Список кабинетов tenant.

**Query:** `status=active|archived|all`, `profile_id`, `limit`, `cursor`

**Response 200:**

```json
{
  "items": [
    {
      "id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
      "slug": "zakupki-2026",
      "display_name": "Закупки 2026",
      "profile_id": "electronics-procurement",
      "status": "active",
      "capabilities": {
        "integrations": { "s4b": { "enabled": true } },
        "modules": { "specs_kp": { "enabled": true } }
      },
      "created_at": "2026-08-01T10:00:00Z"
    }
  ],
  "next_cursor": null
}
```

---

### POST /v1/cabinets

Создание кабинета + pack seed.

**Request:**

```json
{
  "slug": "zakupki-2026",
  "display_name": "Закупки 2026",
  "profile_id": "electronics-procurement"
}
```

**Response 201:**

```json
{
  "id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "slug": "zakupki-2026",
  "display_name": "Закупки 2026",
  "profile_id": "electronics-procurement",
  "status": "active",
  "capabilities": { "...": "..." },
  "storage_uri": "prodavan://storage/cabinets/acme-corp/0195a1b2-c3d4-7890-abcd-ef1234567890/",
  "created_at": "2026-08-01T10:00:00Z"
}
```

**Errors:**

| Code | HTTP | Условие |
| --- | --- | --- |
| `SLUG_CONFLICT` | 409 | slug занят |
| `PROFILE_NOT_FOUND` | 404 | неизвестный profile_id |
| `PROFILE_DEPRECATED` | 422 | deprecated без флага |
| `CAPABILITY_FORBIDDEN` | 422 | попытка overrides s4b |
| `SEED_FAILED` | 500 | pack seed pipeline |

---

### GET /v1/cabinets/{cid}

**Response 200:** полный объект Cabinet.

**Errors:** `CABINET_NOT_FOUND` 404, `CABINET_ACCESS_DENIED` 403.

---

### PATCH /v1/cabinets/{cid}

Разрешены только `display_name`, metadata tags. **Не** `profile_id`, **не** capabilities overrides для s4b.

**Request:**

```json
{
  "display_name": "Закупки электроники — основной"
}
```

---

### DELETE /v1/cabinets/{cid}

Мягкое удаление → `archived`. Проекты остаются на диске; новые операции запрещены.

**Response 204**

---

### POST /v1/cabinets/{cid}/restore

Восстановление из archived → active.

---

### POST /v1/cabinets/{cid}/switch

**Ключевой эндпоинт:** атомарная смена активного кабинета в сессии.

**Request:**

```json
{
  "session_id": "sess_abc123",
  "invalidate_agent_cache": true
}
```

**Response 200:**

```json
{
  "cabinet_id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "tenant_id": "acme-corp",
  "workspace_key": "cab:acme-corp:0195a1b2-c3d4-7890-abcd-ef1234567890",
  "capabilities": {
    "integrations": { "s4b": { "enabled": true } },
    "modules": {
      "specs_kp": { "enabled": true },
      "catalogs_system_s4b": { "enabled": true }
    }
  },
  "previous_cabinet_id": "0195ffff-0000-0000-0000-000000000001",
  "switched_at": "2026-08-20T06:00:00Z",
  "downstream_hints": {
    "default_project_required": true,
    "mcp_servers": ["commerce-search", "commerce-s4b", "commerce-offers", "commerce-equipment"]
  }
}
```

**Побочные эффекты:**

1. JWT/session claim `active_cid` обновляется.
2. Agent runtime получает event `cabinet.switched`.
3. MCP server list фильтруется по capabilities (без s4b для non-electronics).
4. UI сбрасывает кэш списка проектов.

**Errors:**

| Code | HTTP |
| --- | --- |
| `CABINET_ARCHIVED` | 409 |
| `CABINET_ACCESS_DENIED` | 403 |
| `SESSION_NOT_FOUND` | 404 |

---

### GET /v1/cabinets/{cid}/capabilities

Read-only effective capabilities (для UI badges и agent bootstrap).

**Response 200:**

```json
{
  "cabinet_id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "profile_id": "electronics-procurement",
  "effective": {
    "s4b": true,
    "specs_kp": true,
    "equipment_cards": true,
    "prompts": true,
    "catalogs_user": true
  },
  "forbidden_always": ["s4b_override"]
}
```

---

## Webhooks (optional)

| Event | Target |
| --- | --- |
| `cabinet.created` | audit log, billing meter |
| `cabinet.switched` | agent session rebind |

## OpenAPI фрагмент (paths)

```yaml
/v1/cabinets/{cid}/switch:
  post:
    operationId: switchCabinet
    summary: Переключить активный кабинет сессии
    parameters:
      - name: cid
        in: path
        required: true
        schema: { type: string, format: uuid }
    requestBody:
      content:
        application/json:
          schema:
            type: object
            properties:
              session_id: { type: string }
              invalidate_agent_cache: { type: boolean, default: true }
    responses:
      '200':
        description: Контекст переключён
      '409':
        description: CABINET_ARCHIVED
```

## Rate limits

| Endpoint | Limit |
| --- | --- |
| POST /cabinets | 10/h per tid |
| POST …/switch | 60/min per session |
| GET list | 120/min |
