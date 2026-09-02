# Employees — UX contract

Эталон: **Claude.ai / Cursor** (chat-first) + динамический cabinet shell.  
Коллекции: [EntityCollection](../07-ui-mobile-core/entity-collection.md).  
Кабинеты: [dynamic-cabinets](../05-cabinets/dynamic-cabinets.md).

## После логина

```text
OIDC Login
  → ContourSelectorPage? (company.admin ∩ employee)
  → Cabinet home:
       list EntityCollection (мои кабинеты)
       actions: Создать | Импорт
  → enter Cabinet → DynamicShell (meta tabs)
  → ProjectList → ProjectWorkspace (chat-first)
```

## Chrome

| Элемент | Правило |
|---------|---------|
| Context | `Company › Cabinet › Project` в слоте AppBar |
| Смена кабинета | Icon → CabinetSelector / list |
| Смена проекта | AppSelectorPage / ProjectList |
| Create cabinet | «Создать» / icon add на списке кабинетов |
| Export / Import | Icon actions на кабинете |

## Внутри кабинета

1. System tabs (Projects, Chat, Context, Tables, Tools) + **dynamic tabs** из meta.  
2. Chat-first в project workspace; attachments OK.  
3. Агент может добавить tab/table → UI refresh.  
4. Secondary tabs = EntityCollection from `ui_json`.

## Project chat (blocks)

| Block kind | Источник | UI |
|------------|----------|-----|
| `user` | persisted user_message | bubble справа + attachment chips |
| `assistant_markdown` | text_delta (live + reload) | GFM markdown, streaming cursor |
| `thinking` | thinking_delta/complete | collapsible reasoning |
| `tool_call` / `tool_result` | tool events | collapsible disclosure |
| `approval` | tool_approval_request | inline Allow/Deny + full-page HITL |
| `subagent` | subagent_* | card + nested events / sidechain |
| `plan` | task_progress | checklist |

### Responsive

| Width | Layout |
|-------|--------|
| `<600px` | full-width, composer pinned bottom |
| `600–1024px` | center column max 768px |
| `>1024px` | center column max 900px |

## Empty / loading

- Нет кабинетов: «Нет кабинетов» + «Создать» / «Импорт».  
- Нет проектов: «Нет проектов» + «Создать».  
- Skeletons on reload.

## Definition of done (телефон)

Employee создаёт кабинет → просит в чате проекта вкладку «Поставщики» → видит новую tab с таблицей → Export bundle — без модалок.
