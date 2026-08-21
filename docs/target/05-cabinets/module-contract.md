# Cabinets — module contract (frozen)

Кабинет = **installable vertical** (BE+FE+DB+MCP+prompts). Платформа не знает доменной логики кабинета.

## Идентификация

| Поле | Описание |
|------|----------|
| `profile_id` | Стабильный id. Канон: `generic-assistant`, `equipment-procurement` |
| `display_name` | UI |
| `version` | Semver модуля |
| `min_platform_version` | Совместимость |

**Alias:** `electronics-procurement` → deprecated map на `equipment-procurement` (не новый product id).

## SPI (обязательный surface)

Host вызывает **только**:

| Method | Назначение |
|--------|------------|
| `health` | Liveness |
| `manifest` | capabilities, MCP allowlist, project tabs, trigger kinds |
| `migrate` | Cabinet-scoped DB |
| `execute_command` / `execute_query` | Domain ops |
| `on_platform_event` | Platform → pack |
| `materialize_project(ctx, workspace_path)` | AGENTS.md, prompts, MCP config, seeds; **идемпотентно** |

`SpiContext` несёт `session` (DB) на host; **запрещено** требовать smuggling session только в payload как канон (payload session — transitional).

### Platform import ban

`prodavan/api/v1/**` и platform application **не** импортируют `cabinets.<pack>.services.*`.  
Доменные HTTP facades = thin SPI name dispatch (или удаляются в пользу `/spi/commands|queries`).

## Frontend

1. `CabinetUiModule` (`profile_id`, `supportedTabs`, `buildTab`).
2. Только mobile core widgets.
3. Registry: `profile_id → UiModule`. Shell **не** hardcode procurement panels.

## Контракт с Project

| Владеет platform | Владеет cabinet |
|------------------|-----------------|
| Project row, container_ref, triggers bus, agent routing, AI key resolve, attachments metadata | Domain rows, materialize, domain trigger handlers, pack secrets (S4B) scoped |

## События platform → cabinet

| Event | Действие |
|-------|----------|
| `company.suspended` | Stop accepting work |
| `project.created` / `project.prepare` | materialize + domain init |
| `project.deleted` | Cleanup cabinet DB |
| `employee.disabled` | Revoke in-flight ops |

## Изоляция

- Нет SQL между cabinets.
- MCP только allowlist из manifest.
- Grants enforced: create/enter cabinet instance только если profile ∈ CompanyCabinetGrant.
- Pack **не** читает AiProviderKey напрямую.
