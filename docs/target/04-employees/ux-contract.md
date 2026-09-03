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
  → ProjectList (select project) → rail Chats → ProjectWorkspace(sessionId)
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

1. System: Overview / Projects / modules / Management; **Chats** — peer `railExtra` block **after** all destinations (not nested under Projects); Settings trailing.  
2. Tap project = **select + open settings**; selected **active** row uses green (`success`) text; pause/error/draft keep status colors. Visiting settings/workspace auto-selects the project. Delete selected → clear selection.  
3. Workspace требует `sessionId`; attachments OK.  
4. Агент может добавить tab/table → UI refresh. Secondary tabs = EntityCollection from `ui_json`.
5. New chat titles: «Новый диалог» / default session «Диалог» then «Диалог {n}» (API ordinal); UI null title → «Диалог».

## Project chat (blocks)

| Block kind | Источник | UI |
|------------|----------|-----|
| `user` | persisted user_message | bubble справа + attachment chips; **16px top margin** от предыдущего bot-контента |
| `assistant_markdown` | text_delta (live + reload) | inline в колонке чата (без bubble); plain text while streaming, GFM after done |
| `thinking` | thinking_delta/complete | muted line; **входит в WorkSession**; consecutive thinking → **один** spoiler; chevron **справа** + inset panel |
| `tool_call` / `tool_result` | tool events | paired by `id` (window, not only adjacent); semantic RU labels; chevron **справа**; expand body path/`+N −M`/fileSize (not empty). **WorkSession** for ≥2 work items; streaming/empty assistant **не** рвёт сессию |
| `approval` | tool_approval_request | inline Allow/Deny + full-page HITL |
| `subagent` | subagent_* | muted line + inset sidechain |
| `plan` | task_progress | checklist (tasks with titles only) |
| `usage` | usage | muted collapsed line (tokens/cost), l10n |

### Composer

Cursor-style field: **+** (chat settings) → attach → text → send/stop; единый фон без border (idle + focus). **Enter** отправляет, **Shift+Enter** — новая строка. Model picker: `AppPreferenceTile` → table page (`AppEntityCollection`), не dropdown.

### Thinking duration

- Streaming: «Размышление…»
- `< 5s`: «Размышление» без времени
- `≥ 5s`: «Размышлял {minutes/seconds}» (human-readable, ru plural)

### Model picker (chat settings)

`AppPreferenceTile` + `ProjectChatModelSelectPage` — table columns: model, in/out price, max tokens, publisher, release date. Catalog metadata matched to live model id **case-insensitive** on name.

### Responsive

| Width | Layout |
|-------|--------|
| `<600px` | full-width, composer pinned bottom |
| `600–1024px` | center column max 768px |
| `>1024px` | center column max 900px |

### Chat history

- **Source:** PostgreSQL `agent_sessions` + `agent_events` via `GET /chat/transcript` — not MinIO, not pod polling.
- **Read when pod down / error:** transcript loads from DB for `status=active|error`; composer disabled with hint until `status=active` and `observed_state == running`.
- **Pagination:** tail load (`limit`, default 100) + `before_seq` cursor on scroll-up; skeleton bubbles on initial load (no spinner).
- **Load perf:** `getProject` ∥ `loadTranscript(sessionId)` on bootstrap; `loadModels` deferred; `pending_approvals` in transcript; cache key `(projectId, sessionId)`.
- **Scroll:** classic `ListView(reverse: true)`; pin near offset 0; `loadOlder` near `maxScrollExtent`; no auto-fill / prepend-anchor.
- **Settings:** «Чат» tile hints to open from sidebar (no auto-open latest).
- **Sidebar API:** `GET/PUT …/me/selection`, `GET …/chats/sidebar`, `PATCH …/sessions/{id}` (`title`, `pin`).
- **Tool panels:** semantic labels from input; expand body is code-style panel (not raw Map dump); unwrap `{status,value}` / `{success}` payloads.

## Empty / loading

- Нет кабинетов: «Нет кабинетов» + «Создать» / «Импорт».  
- Нет проектов: «Нет проектов» + «Создать».  
- Skeletons on reload.

## Definition of done (телефон)

Employee создаёт кабинет → просит в чате проекта вкладку «Поставщики» → видит новую tab с таблицей → Export bundle — без модалок.
