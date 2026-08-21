# Identity — Keycloak architecture

## Вердикт

| Тема | Target |
|------|--------|
| IdP | **Keycloak** |
| Протокол | **OIDC** (Authorization Code + PKCE для Flutter; client credentials — только service-to-service) |
| Валидация API | JWT access token → **JWKS Keycloak** (RS256/ES256), не shared HS256 secret |
| Пароли / MFA / reset | **Только в Keycloak** |
| Локальный `POST /auth/login` | Legacy dual-verify → **удалить** после cutover |
| Session / cabinets | См. [session.md](session.md) — headers + DB, **не** reissue JWT |

## Семантика слоя

Identity = **аутентификация**. Prodavan API = **resource server** (авторизация по memberships).  
`AiProviderKey` — **не** часть identity.

## Компоненты

```text
Flutter / Web shell
  → Keycloak (AppAuth PKCE)
  → access_token (+ refresh via Keycloak)
  → Prodavan API  Authorization: Bearer <access_token>
       → JWKS verify
       → map sub → Employee / platform admin
       → authorize: DB memberships + X-Cabinet-Id / X-Project-Id
```

| Компонент | Роль |
|-----------|------|
| **Keycloak** | Login UI, credentials, MFA, refresh, realm roles |
| **Prodavan API** | JWKS validate; enforce Company/Cabinet/Project access |
| **Prodavan DB** | Employee, Company, memberships, grants — не пароли |
| **Flutter** | OIDC client; tokens в secure storage |

## Realm / clients

| Client | Тип | Назначение |
|--------|-----|------------|
| `prodavan-flutter` | public + PKCE | Mobile / web UI |
| `prodavan-api` | audience / resource | Access token audience |
| `prodavan-services` | confidential | Workers / MCP gateway |

Один realm `prodavan` (или per-env). Per-company realm — **вне scope** на старте.

## Claims

| Claim | Использование |
|-------|---------------|
| `sub` | Обязателен → `Employee.keycloak_sub` |
| `email` / `preferred_username` | Профиль |
| realm roles | `platform.admin`, `company.admin`, `company.member` |

Optional `company_id` claim **не** заменяет DB membership check.

## Роли

| Keycloak | Prodavan contour |
|----------|------------------|
| `platform.admin` | Platform Admin UI |
| `company.admin` | Company UI |
| `company.member` (или только DB) | Employee workspace |

## Что не является auth

Ключи Cursor / Codex / Claude — [02-ai-provider-keys](../02-ai-provider-keys/).
