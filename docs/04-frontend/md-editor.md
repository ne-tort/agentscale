# MD-редактор промптов (M03)

Редактор markdown-документов кабинета: `AGENTS.md`, `profiles/*/README.md`, модули профилей задач. Используется tenant/cabinet admin для настройки поведения агента без деплоя.

---

## Назначение

| Документ | Путь в sandbox | Кто редактирует |
|----------|----------------|-----------------|
| `AGENTS.md` | `prompts/AGENTS.md` | cabinet.admin |
| Profile README | `prompts/profiles/{id}/README.md` | cabinet.admin |
| Profile modules | `prompts/profiles/{id}/*.md` | cabinet.admin |

Изменения попадают в **следующую** сессию агента (M07 `/reset` или новый run).

---

## Layout

### Desktop (≥ 1024 px)

```text
┌─────────────────────────────────────────────────────┐
│ Doc tree (240px) │ Toolbar (icon-only)              │
├──────────────────┼──────────────────────────────────┤
│                  │ Editor (flex) │ Preview (flex)   │
│  File tree       │               │                  │
│                  │               │                  │
└──────────────────┴───────────────┴──────────────────┘
```

`SplitPane` из `core/widgets`:
- Tree: resizable 200–320 px
- Editor | Preview: 50/50 default, draggable

### Mobile (< 768 px)

- Tabs: **Редактор** | **Превью** | **Файлы**
- Toolbar: horizontal scroll icon row

---

## Toolbar (icon-only, no hints)

Все кнопки — `AppIconButton` **без Tooltip**. Semantics labels:

| Icon | Action | Semantics label |
|------|--------|-----------------|
| `bold` | `**text**` wrap | «Жирный» |
| `italic` | `*text*` wrap | «Курсив» |
| `code` | `` `code` `` wrap | «Код» |
| `link` | Insert link dialog | «Ссылка» |
| `list_bulleted` | `- ` prefix | «Маркированный список» |
| `list_numbered` | `1. ` prefix | «Нумерованный список» |
| `title` | `## ` prefix | «Заголовок» |
| `table_chart` | Insert table template | «Таблица» |
| `horizontal_rule` | `---` | «Разделитель» |
| `undo` | Undo | «Отменить» |
| `redo` | Redo | «Повторить» |
| `save` | Save | «Сохранить» |
| `history` | Version history | «История» |

Toolbar height: 40 px, padding `AppSpacing.sm`, gap `AppSpacing.xs`.

---

## Editor engine

**Package:** `packages/markdown_editor/` (fork/wrapper над `flutter_markdown` + custom controller).

| Feature | Реализация |
|---------|------------|
| Syntax highlight | `highlight` package, theme matching app dark/light |
| Line numbers | Gutter 48 px, monospace 14 px |
| Dirty tracking | `ValueNotifier<bool> isDirty` — блокирует CabinetSwitcher |
| Auto-save | Optional debounce 30s (settings); default manual save |
| Max size | 512 KB per document (API limit) |

---

## Preview pane

- `MdPreview`: `flutter_markdown` + custom builders для tables, code blocks
- Sync scroll (optional toggle)
- GitHub-flavored markdown subset

---

## File tree

- Root: `prompts/`
- Directories expandable
- Icons: 📄 md, 📁 folder
- Context menu (long press mobile): Rename (slug docs only), Revert to version

**Read-only nodes:** `profiles/` seed files помечены 🔒 если из pack и `allow_pack_override=false`.

---

## API integration

| Action | Endpoint |
|--------|----------|
| List docs | `GET /api/v1/cabinets/{cid}/prompts/tree` |
| Get doc | `GET /api/v1/cabinets/{cid}/prompts/docs/{path}` |
| Save | `PUT /api/v1/cabinets/{cid}/prompts/docs/{path}` |
| History | `GET /api/v1/cabinets/{cid}/prompts/docs/{path}/versions` |
| Revert | `POST .../versions/{vid}/restore` |

Headers: `X-Cabinet-Id`, `If-Match: {etag}` для optimistic concurrency.

---

## Validation

Client-side:
- Non-empty `AGENTS.md` warning (not blocking)
- Broken markdown links — warning underline

Server-side:
- Path traversal blocked
- Forbidden patterns: embedded secrets (`password=`, `api_key=`) → `VALIDATION_FAILED`

---

## Unsaved changes guard

При `CabinetSwitcher.switchTo()` или route pop:

```dart
if (editor.isDirty) {
  final leave = await ConfirmDialog.show(
    title: 'Несохранённые изменения',
    message: 'Сохранить перед выходом?',
    actions: [Save, Discard, Cancel],
  );
}
```

---

## Keyboard shortcuts

| Shortcut | Action |
|----------|--------|
| `Ctrl+S` | Save |
| `Ctrl+B` | Bold |
| `Ctrl+I` | Italic |
| `Ctrl+K` | Link |
| `Ctrl+\`` | Code |
| `Ctrl+Shift+P` | Toggle preview |

---

## Тесты

| ID | Сценарий |
|----|----------|
| MD-001 | Toolbar buttons insert correct syntax |
| MD-002 | No Tooltip widgets in toolbar (widget test) |
| MD-003 | Dirty guard blocks navigation |
| MD-004 | ETag conflict shows merge dialog |
| MD-005 | Golden: toolbar light/dark |

---

## Связанные документы

- [design-system.md](design-system.md)
- [widget-catalog.md](widget-catalog.md)
- [../03-modules/M03-prompts/](../03-modules/M03-prompts/)
