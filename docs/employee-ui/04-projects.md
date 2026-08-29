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

1. Inline add → `POST /cabinets/{cabinet_id}/projects`
2. Navigate → `CabinetProjectSettingsPage(projectId)`

## Table columns

- Project name (primary)
- Status (active/paused)
- K8s runtime cell (optional, from runtime presenter)
- Creator display name

## Delete

- Long-press row → delete icon in last column
- `AppConfirmPage` → `DELETE /projects/{id}` (soft delete)
