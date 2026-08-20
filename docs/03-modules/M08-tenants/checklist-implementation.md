# M08 — Чеклист реализации

| # | пункт | балл | критерий 10 |
| --- | --- | --- | --- |
| 1.1 | tenants + cabinets DDL | | FK cascade |
| 1.2 | users + memberships | | Unique constraints |
| 1.3 | Invite flow E2E | | Email + accept |
| 1.4 | JWT issue + refresh rotate | | Security tests |
| 1.5 | switch-cabinet token | | cid updated |
| 2.1 | RBAC matrix implemented | | All permissions |
| 2.2 | Permission middleware | | 403 tests |
| 2.3 | GET /me/permissions | | Matches matrix |
| 2.4 | tenant.owner protections | | LAST_OWNER |
| 2.5 | Platform admin routes gated | | |
| 3.1 | RLS all schemas | | Cross-tenant CI |
| 3.2 | GUC set on connection | | Pool-safe |
| 3.3 | Storage prefix enforcement | | Traversal blocked |
| 3.4 | Tenant provision job | | FS + DB atomic |
| 3.5 | Hard delete job | | Full wipe |
| 4.1 | Login UI | | |
| 4.2 | Cabinet switcher | | |
| 4.3 | Invite UI | | |
| 4.4 | Members management | | |
| 4.5 | Onboarding wizard | | |
| 5.1 | bcrypt + lockout | | |
| 5.2 | JWKS rotation | | |
| 5.3 | Audit invite/login | | M09 |
| 5.4 | Plan quotas | | |
| 5.5 | Break-glass audit | | |

**Порог:** средний ≥ 8, RLS isolation 10/10.
