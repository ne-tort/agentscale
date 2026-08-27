# 10 — Identity (Keycloak)

**Целевая авторизация Prodavan — Keycloak за in-process Auth Service.**  
Flutter говорит только с Prodavan `/auth/*`; Keycloak — внутренняя зависимость.

| Документ | Содержание |
|----------|------------|
| [architecture.md](architecture.md) | Auth Service BFF, JWKS, роли, `sub` |
| [session.md](session.md) | Admin / Company / Employee + Keycloak; headers; entitlements |
| [identity-brokers.md](identity-brokers.md) | VK/Yandex через Auth Service → KC Broker |
| [migration.md](migration.md) | As-built → cutover |

Связано: [03-companies](../03-companies/), [04-employees](../04-employees/), [01-platform-admin](../01-platform-admin/).
