# Employees — domain

## Семантика

Employee = человек (Keycloak `sub`), работающий **внутри** Company в назначенных cabinets.  
Не Admin, не Company ops (кроме случая отдельной роли `company.admin`).

## Жизненный цикл сессии (канон)

1. OIDC login → access_token (Keycloak).
2. API: `sub` → Employee; загрузить memberships + assigned cabinets из **DB**.
3. `cabinets.length == 0` → экран «Нет доступа».
4. `cabinets.length == 1` → auto-enter; set client context `X-Cabinet-Id`.
5. `cabinets.length > 1` → **CabinetSelectorPage** (full screen).
6. Внутри кабинета: projects → workspace (chat-first + module tabs).

**Запрещено:** класть список cabinets / active cabinet в access_token и перевыпускать JWT при switch/open.  
См. [10-identity-keycloak/session.md](../10-identity-keycloak/session.md).

## Инварианты

- Employee не видит Admin UI и не управляет другими сотрудниками.
- Смена кабинета — только selector page; header `X-Cabinet-Id` обновляет клиент.
- Disabled employee → 403 на API (не путать с 401 invalid token).
- Создание cabinet instance — только если `profile_id` ∈ company grants (enforced).

## Проекты

Создаются в контексте `(company_id, cabinet_id, owner_employee_id)`.  
RLS / membership checks на каждом запросе.
