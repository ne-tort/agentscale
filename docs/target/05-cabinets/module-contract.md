# Cabinets — module contract (frozen)

Кабинет = **installable vertical** (BE+FE+данные+MCP+промпты/skills/rules).  
Платформа не знает доменной логики кабинета.

**Деплой сейчас:** modular monolith — [packaging.md](packaging.md).  
**Контракты:** как к будущему microservice (SPI / UiModule — единственные швы).

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
| `manifest` | capabilities, MCP allowlist, project tabs, trigger kinds, base surfaces flags |
| `migrate` | **Только** cabinet schema / tables |
| `execute_command` / `execute_query` | Domain ops |
| `on_platform_event` | Platform → pack |
| `materialize_project(ctx, workspace_path)` | AGENTS (+ vendor aliases), prompts, skills, rules, MCP, seeds из **кабинетной БД/UI**; идемпотентно |

`SpiContext` несёт `session` (DB) на host + ids (company, employee, cabinet instance, project).  
**Запрещено** требовать smuggling session только в payload как канон (payload session — transitional).

### Platform import ban

`prodavan/api/v1/**` и platform application **не** импортируют `cabinets.<pack>.*` internals.  
Доменные HTTP = thin SPI name dispatch (или только `/spi/commands|queries`).

### Cabinet isolation ban

Pack **не** импортирует другой pack's services/tables.  
Shared code — только явный `_base` kit без доменных таблиц.

## Frontend

1. `CabinetUiModule` (`profile_id`, tabs, builders) — единственная регистрация в shell.
2. Код только в `lib/cabinets/<profile_id>/`.
3. Виджеты — mobile core; доменные атомы — внутри pack tree.
4. Shell **не** hardcode procurement panels.

## Контракт с Project

| Владеет platform | Владеет cabinet |
|------------------|-----------------|
| Project row, container_ref, triggers bus, agent routing, AI key resolve, attachments metadata | Domain rows (своя schema), materialize, domain trigger handlers, pack secrets scoped |

## События platform → cabinet

| Event | Действие |
|-------|----------|
| `company.suspended` | Stop accepting work |
| `project.created` / `project.prepare` | materialize + domain init |
| `project.deleted` | Cleanup cabinet DB rows for project |
| `employee.disabled` | Revoke in-flight ops |

## Изоляция (чеклист)

- [ ] Нет SQL между cabinets / в platform schema из pack migrate  
- [ ] MCP только allowlist manifest  
- [ ] Grants: enter/create только если profile ∈ CompanyCabinetGrant  
- [ ] Pack **не** читает AiProviderKey / platform vault напрямую  
- [ ] Файлы BE/FE только в деревьях pack  
- [ ] SPI + UiModule — единственные швы с host  
