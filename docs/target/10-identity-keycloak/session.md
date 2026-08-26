# Identity — session & entitlements (канон)

Карта: [00-entities](../00-entities.md). Architecture: [architecture.md](architecture.md).

## Семантика: у кого Keycloak

| Сущность | Realm role | Login | DB bind |
|----------|------------|-------|---------|
| **Platform Admin** | `platform.admin` | KC user | role only |
| **Company** | `company` | **username = `company_id` + password** | `companies.keycloak_sub` |
| **Employee** | `employee` | email invite | `employees.keycloak_sub` |

Email для Company **не обязателен** (опциональный `contact_email` — не логин).  
Company и Employee — разные users. Membership = soft link.

## Session

```text
OIDC access_token
  → Principal { sub, roles, email?, username? }
  → role company → Company by keycloak_sub (else username==company_id soft-bind)
  → else → Employee by keycloak_sub / invite email
```

## Provisioning Company

1. Admin → `POST /companies` `{ name, password, contact_email?, admin_email? }`
2. DB Company row → KC user `username=company.id`, password set, role `company`
3. Credentials issued: **company_id + password** (no auto email)
4. Optional `admin_email` → Employee + membership (soft)

## Soft links

| Link | Table |
|------|-------|
| Employee ↔ Company | `memberships` |
| Platform AI key → Company | `company_ai_key_bindings` |
| Company-owned AI key | `owner_scope=company` |
| Employee ↔ IdP provider (UX) | `identity_links` (не authz) |

Authz: только `employees.keycloak_sub` / `companies.keycloak_sub`. Brokers: [identity-brokers.md](identity-brokers.md).
