# AI authoring guide

Инструкции для ИИ, создающего meta module **без human developer**.

## Алгоритм (minimal module)

```text
1. Определи одну главную table (slug, label, storage_kind=json_document)
2. Добавь columns (name, type, label, required) — 3–8 полей optimum
3. Создай view collection (title_field, 2–4 columns, create_row, open_form)
4. Создай view form (fields for edit)
5. Создай tab (title 1–3 слова, view_slug, order ≥ 100)
6. Если нужен Pod: materialize rule для AGENTS.md или seed JSON
7. Если agent query: mcp_tools rows_query wrapper
8. Validate references (09-validation-rules.md)
9. PUT slugs via admin API or manifest split
```

## Шаблон мысли

| Вопрос | Meta ответ |
|--------|------------|
| Что храним? | `tables` + `columns` |
| Как список? | `views` kind=collection |
| Как редактировать? | `views` kind=form + row_tap |
| Где в nav? | `tabs.title` |
| Что в Pod? | `materialize` rules |
| Что agent tool? | `mcp_tools` declarative |

## Правила лаконичности (UX)

| ✓ | ✗ |
|---|---|
| title: «Поставщики» | «Список разрешённых поставщиков компании» |
| empty: «Нет строк» | Paragraph how-to |
| 4 columns in table | 12 columns |
| enum 3–5 values | enum 40 values |

## Типовые рецепты

### R1 — Simple CRUD table

Slugs: `tables`, `columns`, `views` (×2), `tabs`  
No `materialize`, no `mcp_tools`.

### R2 — Data + agent read

R1 + `mcp_tools` kind=`rows_query` per search need.

### R3 — Data + files in Pod

R1 + column `file_ref` + `materialize` copy_blob + optional `actions` manual copy.

### R4 — Agent instructions only

Table `agent_docs` (body_md text) + materialize → `AGENTS.md`  
Minimal UI tab optional (read-only form).

### R5 — Project-scoped

R1 + `scope.projects: bound` on tab/view + admin binds module to project via API.

## Anti-patterns for AI

| ✗ | Why |
|---|-----|
| Domain-specific view kinds | Use collection/form only |
| Embedded SQL | Use rows_query tool |
| Duplicate table slugs across modules on same cabinet | Bind validation fails |
| Storing UI strings in row data | Use column labels in meta |
| `system: true` on module tabs | Reserved for platform |

## Example prompt → output mapping

**User:** «Вкладка поставщиков: имя, статус актив/блок, регион»

**AI produces:**

1. `tables`: `[{ slug: suppliers, label: Поставщики, storage_kind: json_document }]`
2. `columns`: name/text, status/enum, region/text
3. `views`: suppliers_list (collection), suppliers_form (form)
4. `tabs`: title Поставщики, view suppliers_list

## Checklist before submit

- [ ] All slugs lowercase snake_case  
- [ ] Every `view_slug` in tabs exists in views  
- [ ] Every `field` in ui_json exists in columns  
- [ ] Tab title ≤ 3 words  
- [ ] At least one primary_action or create path  
- [ ] file_ref has materialize if Pod needs file  
- [ ] No secrets in meta bodies  

## Manifest split (pseudo)

```python
for slug in ["tables", "columns", "views", "tabs", "materialize", "actions", "mcp_tools"]:
    if slug in manifest:
        PUT /admin/modules/{id}/meta/documents/{slug} body=manifest[slug]
```

## References

- [suppliers-module example](examples/suppliers-module.md)  
- [agent-context-module example](examples/agent-context-module.md)  
- [data-only-module example](examples/data-only-module.md)
