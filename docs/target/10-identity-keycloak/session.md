# Identity — session & entitlements (канон)

## Семантика

| Сущность | Что это | Не путать с |
|----------|---------|-------------|
| **Company** | Организация (org), бывший Tenant | User / login |
| **Employee** | Человек с `keycloak_sub`, membership в Company | Company account как «логин компании» |
| **Company account** | Employee с ролью `company.admin` (открывает Company UI) | Отдельная таблица «аккаунт компании» |
| **Platform Admin** | Пользователь с realm role `platform.admin` | Employee компании |

**Жёстко:** `Company ≠ User`. Один Employee может состоять в нескольких Company (редко); активная Company выбирается после login через selector или единственный membership.

## Session model (законченный)

```text
OIDC access_token (Keycloak)
  → API: JWKS validate → Principal { sub, roles, email? }
  → DB: Employee by keycloak_sub (+ memberships, cabinet grants)

Контекст работы (НЕ в access_token):
  X-Cabinet-Id: <uuid>
  X-Project-Id: <proj_*>   # когда операция project-scoped
```

| Инвариант | Правило |
|-----------|---------|
| API **не** issuer | `POST .../switch`, `.../open` **не** перевыпускают access JWT |
| Entitlements | Только DB: membership + cabinet assignment ⊆ company grants |
| Cabinets в токене | **Запрещено** как канон (legacy HS256 claims — dual-verify only) |
| Пароли | Только Keycloak; Prodavan API **не** принимает password на invite/create |
| Authorization | Даже при claim `company_id` — re-check membership в DB |

## Роли → UI contour

| После OIDC | Shell |
|------------|-------|
| `platform.admin` | Admin |
| `company.admin` (+ membership) | Company |
| иначе employee membership | Employee (cabinet selector → workspace) |

Если у человека и `company.admin`, и employee cabinets — после login: выбор контура page (`AppSelectorPage`: «Админ компании» / «Работа в кабинетах»), не modal.

## Provisioning

1. **Admin → create Company** → row в DB + (опционально) KC group/`company_id` attribute; invite first `company.admin` через Keycloak Admin API (email + required actions).
2. **Company → invite Employee** → KC user create/invite + DB Employee stub со статусом `invited` → после первого login sync `keycloak_sub` → `active`.
3. **Disable Employee** → DB status + (опционально) KC disable; API 403; не удалять историю проектов.

## Связанные документы

- [architecture.md](architecture.md) — IdP / clients / JWKS
- [migration.md](migration.md) — cutover с HS256
- [../04-employees/domain.md](../04-employees/domain.md) — employee session flow
