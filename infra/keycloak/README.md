# Keycloak realm — Prodavan

Канон: [docs/target/10-identity-keycloak](../../docs/target/10-identity-keycloak/).  
Brokers (VK/Yandex): [identity-brokers.md](../../docs/target/10-identity-keycloak/identity-brokers.md).

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
| `prodavan-flutter` | public + PKCE | UI; **Direct Access Grants on** for first-party username/password (company_id); default scope includes `prodavan-audience` → `aud=prodavan-api`; request `offline_access` for long-lived refresh |
| `prodavan-api` | audience | claim target for mapper |
| `prodavan-services` | confidential + service account | Admin API invite / workers; default scope includes `prodavan-audience` |

## Token lifetimes (realm)

| Setting | Value | Meaning |
|---------|-------|---------|
| `accessTokenLifespan` | `3600` (1h) | Short-lived access JWT |
| `ssoSessionIdleTimeout` / `ssoSessionMaxLifespan` | `315360000` (~10y) | Online session ceiling |
| `offlineSessionIdleTimeout` / `offlineSessionMaxLifespan` | `315360000` (~10y) | Offline refresh (with `offline_access` scope) |

Flutter requests `offline_access` so refresh survives days/weeks without re-login. Access is refreshed proactively (~60s skew) and on HTTP 401.

## Audience

Client scope `prodavan-audience` (mapper `aud-prodavan-api`) is attached as **default** on `prodavan-flutter` and `prodavan-services`. After import, confirm access tokens contain `"aud": "prodavan-api"` (or array including it).

## Service account checklist (`prodavan-services`)

After import, in Keycloak Admin → Clients → `prodavan-services` → Service account roles → `realm-management`:

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
3. Enable **First Broker Login** + **Account Linking** (email conflict → KC linking UI, not silent merge in Prodavan API).
4. Flutter social buttons pass `kc_idp_hint=vk|yandex` on authorize; one OIDC client stays `prodavan-flutter`.

**Company org principal** — login is `company_id` + password. Native Flutter uses Resource Owner Password (Direct Access Grants) via `TokenSession.loginWithPassword`; browser/IdP flows stay on PKCE.

# Import (dev / k3s)

GitOps: `infra/k3s/base/platform/keycloak.yaml`. Realm is **created by**
`prodavan-keycloak-init` via Admin API (empty realm → built-in scopes → clients/roles/users).
Do **not** use `--import-realm` with a partial JSON — it drops `roles`/`profile`/`email` scopes.

`realm-prodavan.json` here is the **documentation** of intended clients/roles (keep in sync with
the init Job). A copy may exist under `infra/k3s/base/platform/` for reference ConfigMap.

1. Argo sync → Keycloak STS (hostPort **8089**) + init Job (SA roles + `admin`/`admin` + `platform.admin`).
2. Public issuer: `http://127.0.0.1:8089/realms/prodavan`.
3. API: `AUTH_MODE=oidc`, `KEYCLOAK_INVITE_MODE=admin`, secrets as in runbook.

## Compose snippet (optional laptop-only)

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    command: start-dev --import-realm
    volumes:
      - ./realm-prodavan.json:/opt/keycloak/data/import/realm-prodavan.json:ro
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
    ports:
      - "8089:8080"
```

## API env

```text
KEYCLOAK_URL=http://localhost:8089
KEYCLOAK_REALM=prodavan
KEYCLOAK_AUDIENCE=prodavan-api
KEYCLOAK_INVITE_MODE=admin
KEYCLOAK_ADMIN_CLIENT_ID=prodavan-services
KEYCLOAK_ADMIN_CLIENT_SECRET=<from KC>
AUTH_MODE=oidc
```
