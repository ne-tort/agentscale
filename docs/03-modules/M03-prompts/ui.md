# M03 — UI: промпты

## Prompts navigator

**Route:** `/c/{cid}/settings/prompts`

- Tree view left: AGENTS.md, profiles/, skills/
- Editor right
- Version dropdown header

## MD editor — icon-only toolbar

**Requirement:** toolbar buttons **без текстовых подписей** — только иконки + tooltip + aria-label.

| Icon | Action | aria-label |
| --- | --- | --- |
| Bold | **bold** | Жирный |
| Italic | *italic* | Курсив |
| Heading | ## | Заголовок |
| List | - item | Маркированный список |
| Link | [text](url) | Ссылка |
| Code | `code` | Код |
| Preview | toggle | Предпросмотр |
| Save | PUT file | Сохранить |
| History | versions panel | История версий |

Keyboard shortcuts duplicate actions (Ctrl+B, Ctrl+S).

## Unsaved changes guard

- Switch file → confirm if dirty
- Switch cabinet (M00) → confirm modal (linked to M00 UI spec)
- Browser beforeunload

## Version panel

- List with label, date, author
- Actions: preview, diff, rollback (admin confirm)
- Compare: side-by-side diff

## Import / export

**Export:**

- Icon download → full zip default
- Checkbox tree for partial export

**Import:**

- Drag zip or multi-file
- Mode radio: merge / replace (replace = destructive confirm)
- Validate preview table before apply

## Preview mode

Rendered markdown with GFM; code blocks highlighted.

## Negative test IDs (UI)

| Test ID | Сценарий |
| --- | --- |
| NEG-PRM-UI-001 | Toolbar has text buttons — fail |
| NEG-PRM-UI-002 | Replace import without confirm |
| NEG-PRM-UI-003 | Missing aria-label on icons |
