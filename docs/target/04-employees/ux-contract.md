# Employees — UX contract

Эталон workspace: **Claude.ai / Cursor** (chat-first) + **Vercel** (sticky context).  
Коллекции (проекты, prompts, runs, …): [EntityCollection](../07-ui-mobile-core/entity-collection.md).  
Лаконичность: [principles](../07-ui-mobile-core/principles.md) §4. Chat — исключение из table-surface, атомы из core.

## После логина

```text
OIDC Login
  → (если company.admin И employee) ContourSelectorPage
    → CabinetSelectorPage   # если N>1; если 1 — auto
    → ProjectListPage       # EntityCollection
    → ProjectWorkspacePage  # chat-first + module tabs
```

## Chrome

| Элемент | Правило |
|---------|---------|
| Context | Слот AppBar / subtitle контейнера: `Company › Cabinet › Project` |
| Смена кабинета | `AppIconButton` → `CabinetSelectorPage` (не PopupMenu) |
| Смена проекта | → `AppSelectorPage` или ProjectList (EntityCollection) |
| Primary create | Icon add и/или labeled «Создать» на ProjectList; **не** «создать кабинет» на employee home |

## Project workspace (chat-first)

1. **Chat** — главный tab/pane: streaming, markdown, attachments, tool-call disclosure (collapsed), stop/retry/copy.
2. Secondary tabs из cabinet manifest (`specs`, `variants`, …) — EntityCollection где есть список сущностей.
3. Empty chat: короткие **noun/action chips** или пустой composer — без абзацев «Загрузите спеку чтобы…».
4. Attach → OS file picker → register attachment → inbox (см. [chat-attachments](../06-projects-runtime/chat-attachments.md)).

## Empty / loading

- Нет проектов: EmptyState «Нет проектов» + «Создать».
- Нет кабинетов: EmptyState «Нет кабинетов» (факт; без «обратитесь к…» в chrome — детали в Help позже).
- Skeletons на EntityCollection reload.

## Definition of done (телефон)

Employee выбирает кабинет → проект → прикрепляет xlsx в чат → видит stream + статус прогона — без модалок.
