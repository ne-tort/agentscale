# Keycloak realm — Agentscale

Канон: [docs/target/10-identity-keycloak](../../docs/target/10-identity-keycloak/).  
Brokers (VK/Yandex): [identity-brokers.md](../../docs/target/10-identity-keycloak/identity-brokers.md).

## Auth flow as-built (важно для redirectUris/issuer)

**Логин НЕбраузерный**: Flutter (web/desktop) ходит только в API (`POST /api/v1/auth/login`,
`apps/api/src/prodavan/api/v1/auth.py` — «Flutter never talks to Keycloak»); API выполняет
ROPC (`password_grant`, client `agentscale-flutter`, `directAccessGrantsEnabled=true`)
in-cluster на `KEYCLOAK_URL=http://agentscale-keycloak:8080`. `/api/v1/auth/config`
намеренно НЕ отдаёт issuer/token/revoke URL. Валидация токена — строгое строковое сравнение
`iss` с `KEYCLOAK_ISSUER_URL` + JWKS in-cluster (`infrastructure/auth/jwt.py`).

Поэтому:

- **issuer `http://127.0.0.1:8089/realms/agentscale` — внутренняя константа контура**, а не
  адрес, по которому кто-то ходит из браузера. Он должен совпадать в трёх местах:
  `KC_HOSTNAME` (STS) = `KEYCLOAK_ISSUER_URL` (base configmap и patch-dev) = фактический `iss`
  в токенах. Логин работает одинаково со всех origin'ов UI (http://172.31.156.203:8088,
  http://localhost:8088, https://agentscale.dev) — origin не участвует в обмене токенов.
  Prod-контур пинит свой issuer через `overlays/prod` (hostPort **8099**) — не смешивать.
- **redirectUris** участвуют только в единственном code-flow — Identity Broker
  (`GET /api/v1/auth/broker/{idp}/start`, redirect_uri = публичный origin API +
  `/api/v1/auth/broker/callback`). Сейчас брокеры ВЫКЛЮЧЕНЫ (`identityProviders: []`,
  init Job не создаёт IdP), т.е. redirectUris — страховка на будущее включение VK/Yandex:
  список в init Job и в обоих JSON покрывает все dev-origin'ы (VM-IP:8088, agentscale.dev,
  localhost http/https, 127.0.0.1, deeplink `agentscale://`). Функциональный источник
  списка — **init Job** (`infra/k3s/base/platform/keycloak.yaml`); JSON-файлы —
  документация, держать в синхроне (см. «Import» ниже).

## Сущности (realm roles)

| Role | Кто | Contour |
|------|-----|---------|
| `platform.admin` | Platform Admin | Admin UI |
| `company` | **орг-аккаунт Company** (не Employee) | Company UI |
| `employee` | человек-сотрудник | Employee UI |

Interim: DB membership `company.admin` на Employee ещё может открывать Company contour, пока UI не переведён на org principal.

## Clients

| Client | Type | Notes |
|--------|------|-------|
| `agentscale-flutter` | public + PKCE | UI; **Direct Access Grants on** for first-party username/password (company_id); default scope includes `agentscale-audience` → `aud=agentscale-api`; request `offline_access` for long-lived refresh |
| `agentscale-api` | audience | claim target for mapper |
| `agentscale-services` | confidential + service account | Admin API invite / workers; default scope includes `agentscale-audience` |

## Token lifetimes (realm)

| Setting | Value | Meaning |
|---------|-------|---------|
| `accessTokenLifespan` | `3600` (1h) | Short-lived access JWT |
| `ssoSessionIdleTimeout` / `ssoSessionMaxLifespan` | `315360000` (~10y) | Online session ceiling |
| `offlineSessionIdleTimeout` / `offlineSessionMaxLifespan` | `315360000` (~10y) | Offline refresh (with `offline_access` scope) |

Flutter requests `offline_access` so refresh survives days/weeks without re-login. Access is refreshed proactively (~60s skew) and on HTTP 401.

## Audience

Client scope `agentscale-audience` (mapper `aud-agentscale-api`) is attached as **default** on `agentscale-flutter` and `agentscale-services`. After import, confirm access tokens contain `"aud": "agentscale-api"` (or array including it).

Keycloak Admin API assigns scopes with **PUT** (`kcadm.sh update clients/.../default-client-scopes/<scopeId>`). Using `create` returns 404 and silently leaves tokens without `aud=agentscale-api` (API then rejects with UNAUTHORIZED).

## Service account checklist (`agentscale-services`)

After import, in Keycloak Admin → Clients → `agentscale-services` → Service account roles → `realm-management`:

| Role | Зачем |
|------|-------|
| `manage-users` | create / disable / required actions |
| `view-users` | lookup by email/username (idempotent invite) |
| `query-users` | Admin user search |
| `view-realm` | read realm roles (`employee`, `company`, …) |
| `manage-realm` | only if assigning realm roles via Admin API needs it in your KC version — prefer minimal |

Copy client secret → API `KEYCLOAK_ADMIN_CLIENT_SECRET`.

## Identity Providers (VK / Yandex) — placeholders

`identityProviders: []` in the realm JSON on purpose. **Do not** commit IdP client secrets.

When enabling social login (humans only — Employee / Admin):

1. Create IdP aliases **`vk`** and **`yandex`** (OpenID Connect / social plugins as available).
2. Put `clientId` / `clientSecret` in the secret store / KC vault — never in git.
3. Enable **First Broker Login** + **Account Linking** (email conflict → KC linking UI, not silent merge in Agentscale API).
4. Flutter social buttons open Auth Service `GET /auth/broker/{vk|yandex}/start` (API redirects to KC); client stays `agentscale-flutter`.

**Company org principal** — login is `company_id` + password. Native Flutter uses Resource Owner Password (Direct Access Grants) via `TokenSession.loginWithPassword`; browser/IdP flows stay on PKCE.

# Import (dev / k3s)

GitOps: `infra/k3s/base/platform/keycloak.yaml`. Realm is **created by**
`agentscale-keycloak-init` via Admin API (empty realm → built-in scopes → clients/roles/users).
Do **not** use `--import-realm` with a partial JSON — it drops `roles`/`profile`/`email` scopes.

`realm-agentscale.json` here is the **documentation** of intended clients/roles (keep in sync with
the init Job). A copy may exist under `infra/k3s/base/platform/` for reference ConfigMap.

1. Argo sync → Keycloak STS (hostPort **8089**) + init Job (SA roles + `admin`/`admin` + `platform.admin`).
2. Public issuer: `http://127.0.0.1:8089/realms/agentscale`.
3. API: `AUTH_MODE=oidc`, `KEYCLOAK_INVITE_MODE=admin`, secrets as in runbook.

## Compose snippet (optional laptop-only)

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    command: start-dev --import-realm
    volumes:
      - ./realm-agentscale.json:/opt/keycloak/data/import/realm-agentscale.json:ro
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
    ports:
      - "8089:8080"
```

## API env

```text
KEYCLOAK_URL=http://localhost:8089
KEYCLOAK_REALM=agentscale
KEYCLOAK_AUDIENCE=agentscale-api
KEYCLOAK_INVITE_MODE=admin
KEYCLOAK_ADMIN_CLIENT_ID=agentscale-services
KEYCLOAK_ADMIN_CLIENT_SECRET=<from KC>
AUTH_MODE=oidc
```
