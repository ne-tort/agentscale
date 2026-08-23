# L04 — Admin + Company control plane

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | Admin shell Overview + Companies + AI Keys + Company contour |
| Plan | [L04](../11-implementation-plan/L04-admin-company.md) |
| Canon | [01-platform-admin](../01-platform-admin/), [03-companies](../03-companies/) |
| Last updated | 2026-08-23 — key expiring alerts via metrics API |
| Owners | — |

---

## Семантика

Platform Admin — компании, keys (L03), квоты/policy, metrics read models. Company — invite/disable (L01), org-вид кабинетов (metadata only); без static `profile_id` grants.

## Что сделано

| Сделано | Gaps |
|---------|------|
| AdminShell NavigationBar: Overview + Companies + AI Keys | Starter bundle catalog |
| Platform Overview tab + no-keys / key-expiring alerts | Subscription expiring alert |
| Create company full-page + quotas on create | |
| AI Keys: list, create, bind, disable, renew, rotate | |
| Agent policy UI incl. token budgets | |
| Company contour: Overview / Employees / Cabinets | |
| Invite employee full-page form | |
| API: employees/summary + list_keys with company_ids | |

## Карта кода

```text
apps/flutter/lib/features/admin/
  admin_shell.dart
  admin_metrics_overview_page.dart
  admin_company_create_page.dart
  {company_list,company_detail,ai_key_list,ai_key_create,ai_key_detail,ai_key_rotate}_page.dart
apps/flutter/lib/features/company/company_invite_employee_page.dart
apps/flutter/lib/core/api/admin_api.dart
```

## Gaps

| Требование | Статус | Заметка |
|------------|--------|---------|
| Key rotate / renew UI | done | detail + rotate page |
| Invite full-page (Company) | done | company_invite_employee_page |
| Create company full-page | done | admin_company_create_page + navigate to detail |
| Overview no-keys alert | done | ai_keys_bound in metrics |
| Key expiring alert | done | ai_keys_expiring_soon + next_key_renewal_at |
| Subscription expiring alert | hole | needs subscription_ends_at |
| High usage alert | hole | configurable thresholds |
| E2E widget tests | hole | |

## Quality | **7** | doing |
