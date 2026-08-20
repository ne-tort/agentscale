# M06 — UI

Маршрут: `/cabinet/{id}/settings/mcp`

## Экран «MCP серверы»

### Layout

- **Левая колонка:** platform catalog (available servers)
- **Правая колонка:** installed servers для кабинета

### Карточка server

| элемент | описание |
| --- | --- |
| Badge | official / community |
| Version | из server_definitions |
| State | installed / enabled / disabled / error |
| Tools count | из last discovery |
| Actions | Install, Enable, Disable, Uninstall, Rediscover |

### Install flow

1. Click «Установить» на catalog card
2. Confirm modal (лимит тарифа)
3. Card moves to installed (disabled)
4. Prompt «Включить сейчас?»

### Enable flow

Progress spinner → discovery → tool count badge.  
On error: expandable `error_message`, button «Повторить».

## Экран «Профили tools»

### Built-in profiles

Read-only cards: `kp`, `catalog-only`, `readonly-audit`

- Expand: allowed_servers, denylist, effective tool count preview

### Custom profiles (tenant.admin)

Form:

- profile_id (slug)
- allowed_servers (multi-select)
- tool_allowlist OR tool_denylist (radio mode)
- max_tools

Live preview: `GET effective-tools`

## Экран «Tool catalog browser»

Searchable table:

| Tool | Server | Description | In profile kp? |
| --- | --- | --- | --- |

Filter by server, profile inclusion.

## Agent session indicator (M07)

Chat header chip: `MCP: 6 servers · 38 tools · profile kp`  
Click → drawer with tool list (read-only).

## Permissions

| action | role |
| --- | --- |
| View installations | cabinet.viewer |
| Install/enable/disable | cabinet.admin |
| Custom profiles | tenant.admin |
| Install community MCP | tenant.admin + approval |

## Empty states

- No installations: CTA «Установить prodavan-pipeline»
- All disabled: warning «Агент не сможет искать цены»
