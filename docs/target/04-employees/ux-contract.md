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

## Empty / loading

- Нет кабинетов: «Нет кабинетов» + «Создать» / «Импорт».  
- Нет проектов: «Нет проектов» + «Создать».  
- Skeletons on reload.

## Definition of done (телефон)

Employee создаёт кабинет → просит в чате проекта вкладку «Поставщики» → видит новую tab с таблицей → Export bundle — без модалок.
