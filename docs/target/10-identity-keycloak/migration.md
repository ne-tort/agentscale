# Identity — миграция на Keycloak

## Сейчас (as-built)

| Место | Поведение |
|-------|-----------|
| `AUTH_MODE=test` | HS256 mint in pytest fixtures only (no HTTP login) |
| `AUTH_MODE=oidc` | JWKS Keycloak; **Auth Service** BFF (`/auth/login|refresh|logout|broker`) |
| Пароли людей / Company | Только в Keycloak (Prodavan не хранит password hash для login) |
| Provisioning | `IdentityProvisioningPort` → Fake или `HttpKeycloakAdminClient` |
| Flutter | Только Prodavan Auth API — **не** Keycloak hostPort / issuer |
| Legacy `POST /auth/login` (до BFF) / `POST /auth/oidc/token` | Удалены / заменены typed Auth Service |
| `POST /auth/test/login` | Removed |

Realm scaffold: [`infra/keycloak/`](../../../infra/keycloak/). Brokers: [identity-brokers.md](identity-brokers.md). Architecture: [architecture.md](architecture.md).

## Target

| Место | Поведение |
|-------|-----------|
| Login UI | `POST /auth/login` (+ broker start URL later) |
| API | Auth Service → Keycloak in-cluster; JwtValidator JWKS |
| Users | Invite / company principal через Admin API; authz из DB |
| Refresh / logout | `POST /auth/refresh`, `POST /auth/logout` |
| Kafka | `auth.*` events (best-effort) |

## Этапы

1. **Deploy Keycloak** (dev): STS + init Job, audience, `admin`/`admin`.
2. **API**: `AUTH_MODE=oidc`; Auth Service; CI `AUTH_MODE=test` mint only.
3. **Flutter**: password login via Auth Service; route by `/me`; соцкнопки UI later.
4. **IdP brokers** (VK/Yandex): secrets вне git; start/callback уже в Auth Service.
5. **Cluster**: `KEYCLOAK_URL` in-cluster; issuer URL только для JWT `iss` (не для FE).

## Settings

```text
AUTH_MODE=oidc
KEYCLOAK_URL=http://prodavan-keycloak:8080
KEYCLOAK_ISSUER_URL=http://127.0.0.1:8089/realms/prodavan
KEYCLOAK_REALM=prodavan
KEYCLOAK_INVITE_MODE=admin
KEYCLOAK_ADMIN_CLIENT_ID=prodavan-services
KEYCLOAK_ADMIN_CLIENT_SECRET=
OIDC_FLUTTER_CLIENT_ID=prodavan-flutter
OIDC_JWKS_URL=…/realms/prodavan/protocol/openid-connect/certs
```

`AUTH_TEST_SECRET` / HS256 — только `AUTH_MODE=test` (+ HMAC broker state secret reuse).
