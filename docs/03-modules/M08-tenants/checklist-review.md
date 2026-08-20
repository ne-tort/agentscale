# M08 — Чеклист ревью

| # | вопрос | балл | red flags |
| --- | --- | --- | --- |
| R1 | Нет глобальных catalogs/runs? | | /catalogs at root |
| R2 | Каждая таблица tenant/cabinet scoped? | | Orphan rows |
| R3 | RLS enabled + tested? | | Policy missing schema |
| R4 | JWT cid matches resource? | | Trust query param |
| R5 | Refresh token rotation? | | Reuse allowed |
| S1 | Password bcrypt cost ≥ 12? | | Plain md5 |
| S2 | Invite token hashed? | | Plain in DB |
| S3 | Platform admin can't read inbox? | | Shared DB role |
| S4 | Break-glass audited? | | Silent access |
| S5 | MFA for owner (enterprise)? | | |
| A1 | OpenAPI auth documented? | | |
| A2 | switch-cabinet updates perms? | | Stale cr |
| A3 | LAST_OWNER on remove? | | Orphan tenant |
| A4 | Suspend blocks login? | | Token still works |
| A5 | Hard delete complete? | | Orphan S3 |
| T1 | Cross-tenant integration test suite? | | None |
| T2 | Storage isolation test? | | |
| T3 | RBAC matrix automated? | | Manual only |
| T4 | RLS bypass attempt? | | Superuser in app |
| T5 | Provision rollback on FS fail? | | Partial tenant |
| U1 | Hidden nav without perm? | | Disabled only |
| U2 | 403 friendly page? | | Raw JSON |
| U3 | Cabinet switcher clear? | | |
| U4 | Role badges visible? | | |
| U5 | Invite expiry shown? | | |

**Verdict:** Reject if any red flag on R1–R3 or S3.
