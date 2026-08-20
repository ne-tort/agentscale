# M00 — MCP tools: кабинеты

MCP-сервер: `prodavan-cabinets`. Инструменты доступны агенту **после** bootstrap сессии; switch меняет контекст без перезапуска MCP process.

## list_cabinets

Список кабинетов tenant, доступных пользователю.

**Input:**

```json
{
  "status": "active",
  "profile_id": null
}
```

**Output:**

```json
{
  "items": [
    {
      "id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
      "slug": "zakupki-2026",
      "display_name": "Закупки 2026",
      "profile_id": "electronics-procurement",
      "is_active": true
    }
  ]
}
```

---

## describe_cabinet

Детали кабинета + effective capabilities.

**Input:** `{ "cabinet_id": "uuid" }`

**Output:**

```json
{
  "cabinet_id": "...",
  "profile_id": "electronics-procurement",
  "capabilities": {
    "s4b": true,
    "specs_kp": true,
    "equipment_cards": true
  },
  "workspace_key": "cab:acme-corp:0195a1b2-...",
  "storage_root": "prodavan://storage/cabinets/acme-corp/0195a1b2-.../"
}
```

---

## switch_cabinet

Обёртка над `POST /v1/cabinets/{cid}/switch` для агента.

**Input:**

```json
{
  "cabinet_id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "reason": "operator requested project in other cabinet"
}
```

**Output:**

```json
{
  "ok": true,
  "workspace_key": "cab:acme-corp:0195a1b2-...",
  "mcp_servers_enabled": ["commerce-search", "commerce-s4b", "commerce-offers", "commerce-equipment"],
  "mcp_servers_disabled": [],
  "message": "Active cabinet switched. Re-list projects before continuing."
}
```

**Side effects:** downstream MCP `commerce-s4b` регистрируется **только** если `capabilities.s4b === true`.

---

## get_active_cabinet

Текущий cid сессии без switch.

**Output:**

```json
{
  "cabinet_id": "0195a1b2-c3d4-7890-abcd-ef1234567890",
  "profile_id": "electronics-procurement",
  "capabilities": { "s4b": true }
}
```

---

## list_cabinet_profiles

Реестр профилей (read-only для агента при создании кабинета оператором).

---

## Политика видимости MCP по профилю

| MCP server | electronics-procurement | generic-assistant |
| --- | --- | --- |
| `prodavan-cabinets` | ✓ | ✓ |
| `prodavan-projects` | ✓ | ✓ |
| `commerce-search` | ✓ | ✗ |
| `commerce-s4b` | ✓ | **✗** |
| `commerce-offers` | ✓ | ✗ |
| `commerce-equipment` | ✓ | ✗ |

Агент **не должен** вызывать `commerce-s4b` в non-electronics кабинете — tool не регистрируется в capability filter.

## Negative test IDs (MCP)

| Test ID | Вызов | Ожидание |
| --- | --- | --- |
| NEG-CAB-MCP-001 | switch_cabinet на чужой tid | error |
| NEG-CAB-MCP-002 | describe_cabinet archived | `CABINET_ARCHIVED` |
| NEG-CAB-MCP-003 | commerce-s4b после switch на generic | tool not found |
| NEG-CAB-MCP-004 | workspace_key с cid ≠ active | isolation error |

## Bootstrap sequence (agent)

```text
1. get_active_cabinet
2. if null → list_cabinets → prompt operator / switch_cabinet
3. describe_cabinet → load capabilities
4. register allowed MCP servers
5. proceed to M01 list_projects
```
