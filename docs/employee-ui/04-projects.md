# 04 — Projects

Страница **Проекты** в `CabinetShell`.

## Layout

```text
AppScaffold
  Column
    AppInlineAddField(title: "Добавить проект")
    Expanded(AppEntityCollection)
```

## Create

1. Inline add → `POST /cabinets/{cabinet_id}/projects` → статус `draft`
2. Navigate → `CabinetProjectSettingsPage(projectId)` — настройка провайдера, ключа, модулей
3. **Запустить проект** → `POST /projects/{id}/launch` — первичный materialize + Pod

## Table columns

- Project name (primary)
- **О проекте** — поле `about`, обрезка ~80 символов в UI
- **Создатель** — `created_by_login` (логин сотрудника)
- Status (`draft` / `active` / `paused`)

## Delete

- Long-press row → delete icon in last column
- `AppConfirmPage` → `DELETE /projects/{id}` (soft delete)
