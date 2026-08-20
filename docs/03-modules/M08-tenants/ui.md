# M08 — UI

## Auth screens

- `/login` — email/password, tenant picker if multiple
- `/invite/{token}` — accept invite, set password
- `/forgot-password`

## Tenant admin

Route: `/tenant/settings`

### Tabs

1. **Общие** — name, slug (readonly), plan
2. **Кабинеты** — list, create, archive
3. **Пользователи** — members, invites, roles
4. **Биллинг** — later

## Cabinet switcher

Header dropdown:

```text
ACME Corp ▾
  ├── Основной кабинет ✓
  ├── Филиал СПб
  └── + Создать кабинет
```

Switch → `POST switch-cabinet` → reload app context.

## Users management

### Tenant members table

| User | Tenant role | Cabinets | Actions |
| --- | --- | --- | --- |
| ivan@... | admin | Main (admin), SPb (viewer) | Edit, Remove |

### Invite modal

- Email
- Tenant role
- Cabinet assignments (multi)
- Send invite

## Cabinet members

Route: `/cabinet/{id}/settings/members`

Assign `cabinet.admin | operator | viewer`.

## RBAC UX

- Hide nav items without permission (not just disable)
- 403 page: «Недостаточно прав» + contact admin
- Role badges on profile menu

## Platform admin (separate app)

`/platform/tenants` — list, suspend, metadata  
No file browser, no chat access.

## Onboarding wizard

New tenant:

1. Create account
2. Name organization
3. First cabinet auto-created
4. Optional invite team
5. → first project (M07)
