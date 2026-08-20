# M01 — Security: проекты

## Isolation model

```text
Tenant → Cabinet → Project → Run
```

Каждый уровень наследует RLS предыдущего. API проверяет:

1. JWT `tid`
2. JWT `cab` === project.cabinet_id
3. JWT `pid` (optional strict) === requested pid

## RBAC

| Action | viewer | operator | admin |
| --- | --- | --- | --- |
| list projects | ✓ | ✓ | ✓ |
| create | ✗ | ✓ | ✓ |
| open | ✓ | ✓ | ✓ |
| archive | ✗ | ✓ | ✓ |

## commerce.sqlite

- File permissions 0600 service account
- No cross-project ATTACH
- Backup per project prefix

## Audit events

- project.created, project.opened, project.archived
- inbox.upload (filename, size, hash — not content)

## Cross-cabinet

Project storage path **always** includes `{cid}`. NEG-PRJ-001 … 007 mandatory in CI.

## Negative test IDs

| Test ID | Security focus |
| --- | --- |
| NEG-PRJ-SEC-001 | IDOR GET /projects/{other_pid} |
| NEG-PRJ-SEC-002 | Workspace key injection |
| NEG-PRJ-SEC-003 | commerce.sqlite copy to other project |
