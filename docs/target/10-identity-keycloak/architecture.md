# Identity — Keycloak architecture

## Вердикт

| Тема | Target |
|------|--------|
| IdP | **Keycloak** (внутренняя зависимость) |
| Клиент UI | **Только Prodavan Auth Service** (in-process BFF в API) — Flutter **никогда** не ходит в Keycloak |
| Протокол к IdP | OIDC (ROPC / refresh / code+PKCE broker) **внутри** Auth Service |
| Валидация API | JWT → **JWKS** (RS256), не shared HS256 |
| Пароли / MFA / reset | **Только в Keycloak** |
| User id между слоями | JWT **`sub`** (Keycloak user UUID) |
| Dev bridge `POST /auth/test/login` | **Removed** |
| Opaque `POST /auth/oidc/token` | **Removed** — typed `/auth/login` / `/auth/refresh` / `/auth/logout` |
| Session / cabinets | [session.md](session.md) — headers + DB |

Realm GitOps scaffold: [`infra/keycloak/`](../../../infra/keycloak/) (`realm-prodavan.json`).

## Auth Service (in-process)

Изолированный слой в том же процессе API (`application/auth` + `infrastructure/keycloak/token_client`). **Без своей БД.** Состояние — только Keycloak (+ signed broker `state`).

```text
Flutter
  → GET  /auth/config          (auth_mode, brokers, features — без KC URLs)
  → POST /auth/login|refresh|logout
  → GET  /auth/broker/{idp}/start → (API) → Keycloak authorize
  → GET  /auth/broker/callback    → code exchange → redirect app
  → Bearer access_token
  → API JwtValidator JWKS → Principal { sub, roles, email }
       → bind: Company by keycloak_sub | Employee by keycloak_sub
```

Kafka (best-effort, без PG outbox): lifecycle `auth.login`, `auth.first_login`, `auth.login_failed`, `auth.token_refreshed`, `auth.logout` — на `prodavan.platform.events`.

**Регистрация / удаление пользователей — только Kafka Auth commands** (Auth Service domain-agnostic):

```text
Companies / Employees BC
  → DB row keycloak_sub=NULL (create) или soft-delete
  → topic prodavan.auth.commands
       auth.user.register | auth.user.disable | auth.user.delete
Auth Service consumer
  → Keycloak Admin create / disable / delete
  → topic prodavan.auth.events
       auth.user.registered | disabled | deleted | *_failed
Celery / Identity bind
  → set keycloak_sub on registered; metrics on failed
```

Company soft-delete → platform `company.deleted` → cascade Celery (employees → projects → cabinets) + Auth delete company principal.

Флаг «зарегистрирован в KC»: `keycloak_sub IS NOT NULL`. Зомби (unbound, not soft-deleted) → admin metrics `keycloak_unbound` / `employees_keycloak_unbound`.

CI / `KAFKA_ENABLED=false`: buffer-only in-process Fake path (publish → handler → event → bind) без live KC.

**Runtime isolation (сейчас):** Auth Service = **логический слой** в процессе API (`application/auth`), не отдельный Deployment. Kafka consumers Auth — asyncio loops в `KafkaManager` (тот же API pod). Bind `keycloak_sub` — Celery task на `prodavan-celery-worker`. Политика не плодить listener-сервисы: [13-platform-infra/principles.md](../13-platform-infra/principles.md) §3a.

Provisioning (disable / set company password): `IdentityProvisioningPort` →
`HttpKeycloakAdminClient` или Fake — **не** registration. Create/invite sync paths **удалены**.

Health: `GET /auth/health` → probe Keycloak realm.

Соцлогин: [identity-brokers.md](identity-brokers.md) — broker только в Keycloak; старт/callback через Auth Service.

## Три независимых principal

```text
platform.admin  → Platform Admin
company         → Company org account (username = company_id + password; email optional)
employee        → Employee human (employees.keycloak_sub)
```

Связи между ними — **soft** (memberships, key bindings, cabinet assignments), не «Company = Employee».

## Clients

| Client | Тип | Назначение |
|--------|-----|------------|
| `prodavan-flutter` | public | Auth Service ROPC / broker (server-side) |
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

## Env

- `KEYCLOAK_URL` — **in-cluster** (обязателен для Auth Service).
- `KEYCLOAK_ISSUER_URL` — для `iss` / JWKS claim validation (**не** для Flutter).

## Что не identity

AI keys — [02](../02-ai-provider-keys/) (`owner_scope` platform\|company).
