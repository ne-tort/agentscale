# Keycloak (dev scaffold)

Target: [docs/target/10-identity-keycloak](../../docs/target/10-identity-keycloak/).

## Compose snippet (optional)

```yaml
services:
  keycloak:
    image: quay.io/keycloak/keycloak:26.0
    command: start-dev
    environment:
      KEYCLOAK_ADMIN: admin
      KEYCLOAK_ADMIN_PASSWORD: admin
    ports:
      - "8089:8080"
```

## Prodavan API env

```text
KEYCLOAK_URL=http://localhost:8089
KEYCLOAK_REALM=prodavan
KEYCLOAK_AUDIENCE=prodavan-api
AUTH_LEGACY_HS256_ENABLED=true
```

## Flutter (compile-time)

```text
--dart-define=OIDC_ENABLED=true
--dart-define=KEYCLOAK_URL=http://localhost:8089
--dart-define=KEYCLOAK_REALM=prodavan
--dart-define=KEYCLOAK_CLIENT_ID=prodavan-flutter
```

Create realm `prodavan`, clients `prodavan-flutter` (public+PKCE) and audience `prodavan-api`.
