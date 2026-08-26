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
| `prodavan-flutter` | public + PKCE | UI; **Direct Access Grants off**; default scope includes `prodavan-audience` → `aud=prodavan-api` |
| `prodavan-api` | audience | claim target for mapper |
| `prodavan-services` | confidential + service account | Admin API invite / workers; default scope includes `prodavan-audience` |

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

**Company org principal does not use brokers** — login remains `company_id` + password (KC login form / username). Prefer Direct Access Grants **off** for `prodavan-flutter`; company signs in via KC UI with username=`company_id`.

## Import (dev)

1. Start Keycloak (`start-dev`).
2. Admin → Create realm → partial import `realm-prodavan.json` (or Clients/Roles manually from file).
3. Complete **service account checklist** above; set secret → API `KEYCLOAK_ADMIN_CLIENT_*`.
4. API: `AUTH_MODE=oidc`, `KEYCLOAK_INVITE_MODE=admin`.

Invite flow for **employees** uses email + required actions.  
**Company** principals: username = `company_id`, password set at create (no email).

## Compose snippet

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
