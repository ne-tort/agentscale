# L04 — Admin + Company control plane

| Поле | Значение |
|------|----------|
| Status | doing |
| Quality | 7 |
| Quality note | Admin shell + starter catalog + subscription alerts |
| Plan | [L04](../11-implementation-plan/L04-admin-company.md) |
| Canon | [01-platform-admin](../01-platform-admin/), [03-companies](../03-companies/) |
| Last updated | 2026-08-24 — model allowlist UI + enforce; dual-role by sub |
| Owners | — |

---

## Семантика

Platform Admin — компании, keys (L03), квоты/policy, metrics read models. Company — invite/disable (L01), org-вид кабинетов (metadata only); без static `profile_id` grants.

## Что сделано

| Сделано | Gaps |
|---------|------|
| AdminShell NavigationBar: Overview + Companies + AI Keys + Bundles | E2E widget tests |
| Platform Overview tab + no-keys / key-expiring / subscription alerts | |
| Create company inline name → detail | |
| AI Keys: list, create, bind, disable, renew, rotate | |
| Agent policy UI incl. token budgets + max_attachment_mb + HMAC secrets + idle pause hours + model allowlist | USD authoritative billing sync |
| Company contour: Overview / Employees / Cabinets | |
| Invite employee full-page form | |
| `PUT /admin/companies/{id}/subscription` → emits `company.suspended` on expire transition (cancels ACTIVE agent sessions), `company.reactivated` on renew | |
| Natural expiry: `subscription_state` lazy-emits `company.suspended` (dedupe by latest transition; cancels sessions on emit) | |
| Metrics: `subscription_ends_at`, expiring/expired flags | |
| Starter bundle catalog API (`GET /admin/starter-bundles`) | |
| AdminStarterBundlesPage read-only catalog tab | |
| Company detail: platform events list + drain triggers + company/platform idle sweep | |
| `AdminMetricsAlerts` widget (subscription / keys / usage) | |

## Карта кода

```text
apps/flutter/lib/features/admin/
  admin_shell.dart
  admin_metrics_overview_page.dart
  company_list_page.dart (AppInlineAddField)
  company/admin_company_detail_page.dart + general/quotas/policy/events
  {ai_key_list,ai_key_detail}_page.dart
  admin_starter_bundles_page.dart
  widgets/admin_metrics_alerts.dart
apps/flutter/test/admin_widgets_test.dart
apps/flutter/lib/features/company/company_invite_employee_page.dart
apps/flutter/lib/core/api/admin_api.dart
apps/api/src/prodavan/domain/admin/starter_catalog.py
apps/api/src/prodavan/application/admin/starter_bundle_service.py
apps/api/src/prodavan/api/v1/admin_starter_bundles.py
apps/api/src/prodavan/domain/admin/types.py (subscription_read_model)
apps/api/alembic/versions/2026082309_company_subscription.py
```

## Gaps

| Требование | Статус | Заметка |
|------------|--------|---------|
| Key rotate / renew UI | done | detail + rotate page |
| Invite full-page (Company) | done | company_invite_employee_page |
| Create company inline → detail | done | AppInlineAddField name-only; invite/quotas on detail |
| Overview no-keys alert | done | ai_keys_bound in metrics |
| Key expiring alert | done | ai_keys_expiring_soon + next_key_renewal_at |
| High usage alert | done | high_agent_usage + ADMIN_METRICS_TOKEN_ALERT_THRESHOLD |
| Subscription expiring alert | done | subscription_expiring_soon + Flutter Overview |
| Subscription expired alert | done | subscription_expired flag |
| Subscription UI on create/edit | done | lifetime + ends_at on general (seamless) |
| Starter bundle catalog | live | metadata + Admin UI + shipped zip in fixtures |
| Starter bundle download | live | `GET .../bundle` base64 |
| `storage_bytes` / `last_activity_at` | done | workspace scan + activity max |
| E2E widget tests | live (subset) | metrics alerts + AdminShell NavigationBar destinations; full admin navigation flows — hole |
| Redis runtime cache (policy/sub) | **done (P0 subset)** | policy/sub/quota; HMAC via DB `get_ingress_hmac_secrets`; sub flags recompute on cache hit |

## Quality | **7** | doing |
