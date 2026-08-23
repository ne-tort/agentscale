# L01 — Identity & entitlements

## Цель

Keycloak OIDC как единственный IdP; Prodavan API — resource server (JWKS). Company/Employee/membership в DB. Контекст кабинета/проекта — только headers. Invite без password в Prodavan API.

## Канон

- [10-identity-keycloak/](../10-identity-keycloak/) — особенно [session.md](../10-identity-keycloak/session.md), [architecture.md](../10-identity-keycloak/architecture.md), [migration.md](../10-identity-keycloak/migration.md)
- [00-glossary](../00-glossary.md) (Company ≠ User)
- [00-principles](../00-principles.md) §Admin/Company/Employee

## Зависимости

| Нужно | Даёт |
|-------|------|
| L00 skeleton + DB | `Principal`, entitlement service, invite provisioning hooks |

## Контракты (публикует)

| Контракт | Описание |
|----------|----------|
| `Principal` | `{ sub, roles, email? }` после JWKS validate |
| `Employee` / `Company` / `Membership` | Schema + repository API |
| Headers | `X-Cabinet-Id`, `X-Project-Id` — **не** в access_token |
| Authz helpers | `require_platform_admin`, `require_company_admin`, `require_membership`, later cabinet ACL |
| Invite API shape | email + required actions via KC Admin API; **нет** password field |
| Contour routing hint | API/UI: роль → Admin / Company / Employee contour |

## Изоляция

Можно закрыть API+DB+KC realm **без** Flutter shells (L04/L05). UI login (AppAuth) — часть DoD клиента, может идти параллельно с L02.

## DoD

- [ ] KC realm/roles: `platform.admin`, membership/`company.admin` по канону. *(API roles + Fake invite; live realm — Gap)*
- [x] API отклоняет невалидный/просроченный token; JWKS cache ок. *(oidc path; CI: AUTH_MODE=test)*
- [x] **Нет** `POST /auth/login` с password как канон.
- [x] Switch company / open cabinet **не** reissue JWT.
- [x] Entitlements только из DB (membership helpers; claim company_id не source of truth).
- [x] Invite company.admin и employee: email flow shape; статус `invited` → `active` после первого login. *(Fake KC)*
- [x] Disable employee → 403 на API.
- [x] Contract tests на headers + Principal.

## Не считать готовым, если…

- Cabinets/projects зашиты в JWT claims как источник истины.
- Пароль принимается Prodavan API.
- «Временно» HS256 shared secret без dual-verify плана из migration.md.
- Есть только login demo user без invite/disable lifecycle.

## Exit gate

Автотесты authz + ручной прогон invite на dev KC. Контракт `Principal` зафиксирован в contracts-index.
