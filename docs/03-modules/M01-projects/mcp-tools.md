# M01 — MCP tools: проекты

Сервер: `prodavan-projects`. Требует active cabinet + желательно active project.

## list_projects

```json
{ "status": "active", "limit": 50 }
```

## create_project

```json
{
  "slug": "client-beta",
  "display_name": "Клиент Beta"
}
```

## open_project

```json
{ "project_id": "proj_7f3a9c2e" }
```

Returns workspace_key и storage_paths.

## get_active_project

Текущий pid сессии.

## describe_project

Metadata + stats + last runs summary.

## Workspace propagation

Все последующие M02 tools получают:

```json
{
  "workspace_key": "cab:acme-corp:0195a1b2-...:proj_7f3a9c2e",
  "run_id": "optional"
}
```

Middleware validates workspace_key matches session.

## Negative test IDs

| Test ID | Вызов |
| --- | --- |
| NEG-PRJ-MCP-001 | create без cab |
| NEG-PRJ-MCP-002 | open archived |
| NEG-PRJ-MCP-003 | wrong workspace_key in child tool |
