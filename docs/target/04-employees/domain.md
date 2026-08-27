# Employees — domain

## Семантика

Employee = человек (Keycloak `sub`) в Company.  
Работает в кабинетах, которые **Company назначила** ([assignment](../05-cabinets/assignment.md)).  
Создаёт Projects внутри назначенного кабинета (`cabinet_id` + `owner_employee_id`).

Карта: [00-entities](../00-entities.md).

## Жизненный цикл сессии

1. OIDC login → access_token (Keycloak).
2. API: `sub` → Employee; загрузить **assigned** CabinetInstances.
3. `cabinets.length == 0` → EmptyPlaceholder (нет назначений; создание кабинета — по политике Company, не «свободный zoo» без grant).
4. `cabinets.length == 1` → auto-enter; `X-Cabinet-Id`.
5. `cabinets.length > 1` → **CabinetSelectorPage**.
6. Внутри: UI из meta + projects → Pod workspace.

**Запрещено:** cabinets в JWT; reissue при switch.  
См. [session.md](../10-identity-keycloak/session.md).

## Инварианты

- Employee не видит Admin UI; не управляет чужими сотрудниками.
- Доступ к кабинету только через **active assignment**.
- Смена кабинета — selector page; `X-Cabinet-Id` на клиенте.
- Disabled employee → 403; soft-delete не wipe projects/cabinets (канон).
- Keycloak: `keycloak_sub` заполняется Celery после `auth.user.register`; disable/delete KC — только через Auth Kafka (`auth.user.disable` / `auth.user.delete`).
- BC Employees (`application/employees`) — invite/disable REST; не вызывает Keycloak Admin sync.

## Проекты

`(company_id, cabinet_id, owner_employee_id)`.  
Агент: `cabinet.*` MCP **этого** cabinet_id.  
Delete cabinet → проекты сотрудника в нём удаляются вместе со всеми остальными.
