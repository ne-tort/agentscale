# 10 — Identity (Keycloak)

**Целевая авторизация Prodavan — Keycloak (OIDC).**  
Локальный password-login + HS256 — legacy.

| Документ | Содержание |
|----------|------------|
| [architecture.md](architecture.md) | IdP, clients, JWKS, роли |
| [session.md](session.md) | Admin / Company / Employee + Keycloak; headers; entitlements |
| [identity-brokers.md](identity-brokers.md) | VK/Yandex через KC Broker; `kc_idp_hint`; без app OAuth |
| [migration.md](migration.md) | As-built → cutover |

Связано: [03-companies](../03-companies/), [04-employees](../04-employees/), [01-platform-admin](../01-platform-admin/).
