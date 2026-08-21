# Employees — UI

Полный UX-контракт (chat-first, chrome): **[ux-contract.md](ux-contract.md)**.

## После логина

```text
Login (OIDC)
  → ContourSelector? (admin+employee)
  → CabinetSelectorPage
  → ProjectListPage
  → ProjectWorkspacePage (chat-first)
```

## Правила

- Смена кабинета/проекта — только pages (`AppSelectorPage`).
- Нет `PopupMenuButton` для сущностей.
- Создание проекта — `ProjectCreatePage`.
- EmptyState + CTA; context `Company › Cabinet › Project`.
