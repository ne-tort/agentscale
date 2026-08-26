# Identity — миграция на Keycloak

## Сейчас (as-built)

| Место | Поведение |
|-------|-----------|
| `AUTH_MODE=test` | HS256 test JWT; `POST /auth/test/login` personas |
| `AUTH_MODE=oidc` | JWKS Keycloak (`JwtValidator`); Flutter PKCE |
| Пароли людей / Company | Только в Keycloak (Prodavan не хранит password hash для login) |
| Provisioning | `IdentityProvisioningPort` → Fake или `HttpKeycloakAdminClient` |
| Company create | username=`company_id` + password; `companies.keycloak_sub` |
| Employee invite | email + required actions; `employees.keycloak_sub` сразу после invite |
| Flutter | OIDC AppAuth / desktop PKCE; опциональный `kc_idp_hint` |
| Legacy `POST /auth/login` | Удалён / не канон |

Realm scaffold: [`infra/keycloak/`](../../../infra/keycloak/). Brokers: [identity-brokers.md](identity-brokers.md).

## Target после live cutover

| Место | Поведение |
|-------|-----------|
| Login UI | Redirect / AppAuth → **Keycloak** (соц = broker + hint) |
| API | Только OIDC JWKS; `AUTH_MODE=oidc` на shared env |
| Users | Invite / company principal через Admin API; authz из DB |
| Refresh | Keycloak refresh token |

## Этапы

1. **Deploy Keycloak** (dev): import `realm-prodavan.json`, service-account roles, audience on clients.
2. **API already dual-capable**: `test` \| `oidc` via settings (не смешивать HS256 prod).
3. **Flutter OIDC**: готово; соцкнопки — UI later + `kc_idp_hint`.
4. **IdP brokers** (VK/Yandex): secrets вне git; Account Linking в KC.
5. **Cluster cutover**: `AUTH_MODE=oidc` на k3s (отдельный infra PR) — gap **P-KC-01**.
6. **Admin bootstrap**: platform admin через realm role `platform.admin`.

## Settings

```text
AUTH_MODE=oidc
KEYCLOAK_URL=
KEYCLOAK_REALM=prodavan
KEYCLOAK_AUDIENCE=prodavan-api
KEYCLOAK_INVITE_MODE=admin
KEYCLOAK_ADMIN_CLIENT_ID=prodavan-services
KEYCLOAK_ADMIN_CLIENT_SECRET=
OIDC_FLUTTER_CLIENT_ID=prodavan-flutter
OIDC_JWKS_URL=…/realms/prodavan/protocol/openid-connect/certs
```

`AUTH_TEST_SECRET` / HS256 — только `AUTH_MODE=test`.

## Вне scope

- Живой cutover Keycloak в k3s (infra PR).
- Реальные credentials VK/Yandex в prod realm.
- Полный Account Linking UI + sync `identity_links` из KC Admin API.
- Telegram bot user ↔ Keycloak.
