# L01 — Identity & entitlements

| Поле | Значение |
|------|----------|
| Status | partial |
| Quality | 7 |
| Quality note | API+schema+Principal live; DoD без live KC realm/Admin — поэтому 7; Gaps в as-built |
| Plan | [L01](../11-implementation-plan/L01-identity.md) |
| Canon | [10-identity](../10-identity-keycloak/), [session](../10-identity-keycloak/session.md) |
| Last updated | 2026-08-24 — restored as-built encoding |
| Owners | — |

---

## Семантика

Keycloak — канонический IdP. API — resource server (JWKS → Principal).  
**Company** = org; **Employee** = субъект с keycloak_sub; **Company account** = employee + company.admin.  
Контекст кабинета/проекта — в headers (`X-Cabinet-Id`, `X-Project-Id`), **не** в claims JWT.  
Entitlements = membership в таблице DB. Invite без password в Prodavan API.

**Не** хранилище AI-ключей; не cabinet data plane.

## Что сделано

| Сделано | Не сделано / Gaps |
|---------|-------------------|
| ORM companies / employees / memberships + Alembic identity_001 | Live Keycloak realm cutover + AppAuth Flutter |
| Principal / WorkContext; JWT: AUTH_MODE=oidc (JWKS) \| test (HS256 CI) | Dual-role by sub-only token without email (edge) |
| APP_ENV=prod запрещает AUTH_MODE=test | |
| HttpKeycloakInviteClient (KEYCLOAK_INVITE_MODE=admin) + Fake for tests | E2E against live KC Admin in CI |
| Dual-role platform.admin+employee: /me loads Employee when email in token | |
| API: /me, companies create+invite, disable, session/switch-company (jwt_reissued: false) | PG invite E2E частично skip без Postgres |
| Entitlements: disable → 403; platform admin без обязательной Employee row | |

## Как сделано

1. Domain types — `domain/identity/`; ORM — `infrastructure/persistence/models/identity.py`.
2. JwtValidator: oidc через PyJWKClient; test — HS256 `AUTH_TEST_SECRET`, запрещён в prod.
3. FakeKeycloakInviteClient + HttpKeycloakInviteClient (admin mode via client credentials).
4. Deps: Bearer → Principal; headers → WorkContext; `require_platform_admin`.
5. Contract tests: unauthorized, admin /me, JWT unit (prod forbid test mode).

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-PRINCIPAL | Bearer JWT → Principal (oidc\|test) | **live** |
| C-AUTH-CONFIG | GET /auth/config public OIDC discovery | **live** (subset) |
| C-MEMBERSHIP | Company / Employee / Membership schema+API | **live** |
| C-HEADERS | X-Cabinet-Id, X-Project-Id → WorkContext | **live** |
| C-INVITE | Invite API shape без password (Fake \| Http KC Admin) | **live** (порт); realm cutover — gap |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-API-HEALTH | L00 | live |

## Связи

→ L04, L05, L06 (authz). → Keycloak. Не пишет в cabinet schemas.  
L03 ссылается на companies.id как FK.

## Инварианты

- Switch/open не reissue JWT (`jwt_reissued: false`).
- Claim company_id не обходит DB membership.
- Cabinets не в access_token как source of truth.
- Disable employee → 403.
- Password не принимается в create/invite body.

## Карта кода

```text
apps/api/src/prodavan/
  domain/identity/
  application/identity/service.py
  api/deps.py
  api/v1/identity.py
  infrastructure/auth/jwt.py
  infrastructure/keycloak/invite.py
  infrastructure/keycloak/admin_client.py
  infrastructure/persistence/models/identity.py
apps/api/alembic/versions/2026082302_identity.py
apps/api/tests/integration/test_identity.py
apps/api/tests/unit/test_jwt_validator.py
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| Schema Company/Employee/Membership | done | |
| Principal + JWKS path | done | oidc wired; CI uses test mode |
| Headers work context | done | |
| Invite без password | done | Fake + HttpKeycloakInviteClient |
| Disable → 403 | done | tested with PG when available |
| Flutter AppAuth session | live | PKCE AppAuth + secure storage; KC client redirect URIs — hole |
| Live KC realm cutover | todo | блокирует Status=done |
| Platform admin dual employee row | done | /me loads row when email present |

## Проверка

```text
cd apps/api && ruff check src tests && pytest tests/unit/test_jwt_validator.py tests/integration/test_identity.py -q
# with Postgres + alembic upgrade head: full invite/disable path
```

## Оценка качества

Шкала: [quality-score.md](quality-score.md).

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 1 | API/schema ок; live KC realm/Admin — нет |
| B. Контракты | 2 | C-* live + tests (Fake invite) |
| C. Инварианты и проверки | 2 | no password, no JWT reissue, prod bans test auth |
| D. As-built ясность | 2 | эта карточка |
| **Quality (итог)** | **7** | partial без live KC cutover |
