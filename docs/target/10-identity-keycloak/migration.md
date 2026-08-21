# Identity — миграция на Keycloak

## Сейчас (legacy код)

| Место | Поведение |
|-------|-----------|
| `POST /api/v1/auth/login` | `login_id` + password → HS256 JWT (`jwt_secret`) |
| `auth_service.py` | bcrypt / локальные users |
| Flutter login screen | Форма логин/пароль → API |
| `deps.py` | `decode_access_token` локальным секретом |

## Target после cutover

| Место | Поведение |
|-------|-----------|
| Login UI | Redirect / AppAuth → **Keycloak** |
| `POST /auth/login` | **Удалён** (или 410 Gone) |
| API | JWKS Keycloak; опционально introspection |
| Users | Создание/invite через Keycloak Admin API или Admin UI + sync `sub` в DB |
| Refresh | Keycloak refresh token (не свой refresh endpoint, если не нужен BFF) |

## Этапы

1. **Deploy Keycloak** (dev/stage): realm, clients, JWKS URL в settings.
2. **API dual-verify** (короткое окно): принимать и legacy HS256, и Keycloak JWT; метрики по issuer.
3. **Flutter OIDC**: убрать форму пароля; secure storage для tokens.
4. **Provision**: миграция существующих users → Keycloak (required actions: update password); связать `sub` с rows в DB.
5. **Cut legacy**: выключить HS256 login; удалить password columns / hashing path из auth use-cases (кроме break-glass если отдельно решено).
6. **Admin bootstrap**: platform admin только через Keycloak role `platform.admin`.

## Settings (целевые имена)

```text
KEYCLOAK_URL=
KEYCLOAK_REALM=prodavan
KEYCLOAK_CLIENT_ID=prodavan-flutter   # public
KEYCLOAK_API_AUDIENCE=prodavan-api
KEYCLOAK_JWKS_URL=…/realms/prodavan/protocol/openid-connect/certs
```

`JWT_SECRET` / HS256 — только до конца dual-verify, затем удалить.

## Вне scope этого документа

- Федерация корпоративных IdP компаний (SAML/OIDC broker) — later.
- Telegram bot user ↔ Keycloak link — отдельное решение (не блокирует web/mobile cutover).
