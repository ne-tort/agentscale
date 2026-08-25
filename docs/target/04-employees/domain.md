# Employees — domain

## Семантика

Employee = человек (Keycloak `sub`) в Company.  
Создаёт и владеет **динамическими кабинетами** (default); работает в projects внутри выбранного кабинета.

## Жизненный цикл сессии (канон)

1. OIDC login → access_token (Keycloak).
2. API: `sub` → Employee; загрузить **owned / accessible CabinetInstances** из DB.
3. `cabinets.length == 0` → EmptyPlaceholder + «Создать» / «Импорт».
4. `cabinets.length == 1` → auto-enter; client `X-Cabinet-Id`.
5. `cabinets.length > 1` → **CabinetSelectorPage**.
6. Внутри: dynamic shell (meta tabs) + projects → workspace.

**Запрещено:** cabinets в JWT; reissue при switch.  
См. [session.md](../10-identity-keycloak/session.md).

## Инварианты

- Employee не видит Admin UI; не управляет чужими сотрудниками (без company.admin).
- Создание кабинета: из Base или import bundle (квоты Company/Admin).
- Смена кабинета — selector page; `X-Cabinet-Id` на клиенте.
- Disabled employee → 403.

## Проекты

`(company_id, cabinet_id, owner_employee_id)`.  
Агент проекта может вызывать `cabinet.*` MCP **этого** cabinet_id.
