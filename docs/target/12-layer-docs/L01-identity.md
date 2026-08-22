# L01 — Identity & entitlements

| Поле | Значение |
|------|----------|
| Status | not_started |
| Quality | 0 |
| Quality note | Слой не начат |
| Plan | [L01](../11-implementation-plan/L01-identity.md) |
| Canon | [10-identity](../10-identity-keycloak/), [session](../10-identity-keycloak/session.md) |
| Last updated | 2026-08-23 — добавлена шкала Quality |
| Owners | — |

---

## Семантика

Keycloak — единственный IdP. API — resource server (JWKS → `Principal`).  
**Company** = org; **Employee** = человек с `keycloak_sub`; **Company account** = employee + `company.admin`.  
Контекст кабинета/проекта — headers (`X-Cabinet-Id`, `X-Project-Id`), **не** claims JWT.  
Entitlements и membership — только DB. Invite без password в Prodavan API.

**Не** хранилище AI-ключей; не cabinet data plane.

## Что сделано

—

## Как сделано

—

## Контракты

### Публикует

| ID | Форма | Статус |
|----|-------|--------|
| C-PRINCIPAL | JWKS → Principal | planned |
| C-MEMBERSHIP | Company / Employee / Membership | planned |
| C-HEADERS | X-Cabinet-Id, X-Project-Id | planned |
| C-INVITE | KC invite API shape | planned |

### Потребляет

| ID | Откуда | Статус |
|----|--------|--------|
| C-API-HEALTH | L00 | planned |

## Связи

→ L04, L05, L06 (authz). ← Keycloak. Не пишет в cabinet schemas.

## Инварианты

- Switch/open не reissue JWT.
- Claim `company_id` не обходит DB membership.
- Cabinets не в access_token как source of truth.
- Disable employee → 403.

## Карта кода

```text
—  # заполнить при первой поставке
```

## Gaps vs канон / DoD

| Требование | Статус | Заметка |
|------------|--------|---------|
| DoD L01 | todo | |

## Проверка

```text
—
```

## Оценка качества

Рубрика: [quality-score.md](quality-score.md).

| Ось | Балл 0–2 | Комментарий |
|-----|----------|-------------|
| A. Полнота DoD | 0 | |
| B. Контракты | 0 | |
| C. Инварианты и проверки | 0 | |
| D. As-built ясность | 1 | карточка-заготовка |
| **Quality (итог)** | **0** | not_started |
