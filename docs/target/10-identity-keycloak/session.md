# Identity — session & entitlements (канон)

Карта сущностей: [00-entities](../00-entities.md).

## Семантика: у кого Keycloak

| Сущность | Keycloak | Смысл |
|----------|----------|--------|
| **Platform Admin** | Да, realm role `platform.admin` | Оператор платформы |
| **Company** | Да, **орг-аккаунт** (company principal) | Логин в Company UI: сотрудники, assign кабинетов, квоты, метрики |
| **Employee** | Да, `keycloak_sub` на Employee row | Работа в назначенных кабинетах |

Один человек может иметь несколько principals (редко): например platform.admin + employee membership — после login выбор контура.

**Устарело как единственная модель:** «Company ≠ login; Company UI только через Employee + `company.admin`».  
Допустимо временно в коде; **цель** — у Company свои KC-креды (см. [gap](../09-gap-map.md)).

## Session model

```text
OIDC access_token (Keycloak)
  → API: JWKS validate → Principal { sub, roles, email? }
  → DB bind:
       platform.admin → Admin contour
       company principal → Company contour (company_id)
       employee sub → Employee + memberships + cabinet assignments

Контекст работы (НЕ в access_token):
  X-Cabinet-Id: <uuid>
  X-Project-Id: <proj_*>
```

| Инвариант | Правило |
|-----------|---------|
| API **не** issuer | switch/open **не** перевыпускают access JWT |
| Entitlements | DB: membership + **cabinet assignment** + peer isolation |
| Пароли | Только Keycloak; Prodavan API **не** принимает password |
| Authorization | re-check в DB на каждый scoped запрос |

## Роли → UI contour

| После OIDC | Shell |
|------------|-------|
| `platform.admin` | Admin |
| company principal (org KC) | Company |
| employee membership | Employee (cabinet selector → только **assigned**) |

Если несколько контуров у одного человека — `AppSelectorPage`, не modal.

## Provisioning

1. **Admin → create Company** → DB Company + **Keycloak credentials для орг-аккаунта** (+ optional first human invite).
2. **Company → invite Employee** → KC user + DB Employee `invited` → login sync `keycloak_sub` → `active`.
3. **Company → assign Cabinet** → grant Employee↔Cabinet ([assignment](../05-cabinets/assignment.md)).
4. **Disable Employee** → DB status + optional KC disable; API 403.

## Связанные документы

- [architecture.md](architecture.md) — IdP / clients / JWKS  
- [migration.md](migration.md) — cutover  
- [../03-companies/domain.md](../03-companies/domain.md)  
- [../04-employees/domain.md](../04-employees/domain.md)  
