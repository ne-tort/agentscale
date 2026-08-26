# Identity — Keycloak architecture

## Вердикт

| Тема | Target |
|------|--------|
| IdP | **Keycloak** |
| Протокол | **OIDC** (Authorization Code + PKCE для Flutter; client credentials — service-to-service) |
| Валидация API | JWT → **JWKS** (RS256), не shared HS256 |
| Пароли / MFA / reset | **Только в Keycloak** |
| Dev bridge `POST /auth/test/login` | `AUTH_MODE=test` — personas `platform_admin` / `company_principal` / `demo_employee` |
| Session / cabinets | [session.md](session.md) — headers + DB |

Realm GitOps scaffold: [`infra/keycloak/`](../../../infra/keycloak/) (`realm-prodavan.json`).

## Три независимых principal

```text
platform.admin  → Platform Admin
company         → Company org account (username = company_id + password; email optional)
employee        → Employee human (employees.keycloak_sub)
```

Связи между ними — **soft** (memberships, key bindings, cabinet assignments), не «Company = Employee».

## Компоненты

```text
Flutter (PKCE, optional kc_idp_hint)
  → Keycloak (local users | Identity Broker VK/Yandex)
  → Bearer access_token
  → API JWKS → Principal { sub, roles, email }
       → bind: Company by keycloak_sub | Employee by keycloak_sub
       → authorize: memberships / assignments / owner_scope
```

Provisioning (invite / company principal / disable): `IdentityProvisioningPort` →
`HttpKeycloakAdminClient` (Admin API) или Fake в tests.

Соцлогин: [identity-brokers.md](identity-brokers.md) — broker только в Keycloak; API не знает провайдера для authz.

## Clients

| Client | Тип | Назначение |
|--------|-----|------------|
| `prodavan-flutter` | public + PKCE | UI |
| `prodavan-api` | audience | `aud=prodavan-api` |
| `prodavan-services` | confidential | Invite Admin API / workers |

## Realm roles

| Role | Contour |
|------|---------|
| `platform.admin` | Admin |
| `company` | Company (орг-аккаунт) |
| `employee` | Employee workspace |

Interim: DB membership `company.admin` на Employee ещё открывает Company contour (human bridge).

## Claims

| Claim | Использование |
|-------|---------------|
| `sub` | → `Company.keycloak_sub` или `Employee.keycloak_sub` (один sub на Employee, broker прозрачен) |
| `preferred_username` | Company login = `company_id` |
| `email` | Employee invite bind (fallback) |
| realm roles | contour hint; **authz всегда из DB** |

Опциональный audit: `identity_links` (provider ↔ employee) — не для authz.

## Что не identity

AI keys — [02](../02-ai-provider-keys/) (`owner_scope` platform\|company).
