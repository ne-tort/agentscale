# ADR-002: Capability gateway

- **Статус:** Принято
- **Дата:** 2026-11-01

## Контекст

Модулям, агентам и UI нужны единые операции над данными, файлами, MCP, действиями и артефактами. Разрозненные прямые вызовы затрудняют проверку scope, аудит и отзыв разрешений.

## Решение

Вводится capability gateway как единственная прикладная точка доступа к платформенным и доменным capability. Каждый запрос проходит typed contract, tenant/project/session scope, policy check и audit context. Gateway выдаёт только разрешённые query/command/action и формирует capability bundle для runtime.

Capability регистрируется версией и явно описывает permissions, dependencies, resources, triggers и backend service. MCP является адаптером к gateway, а не обходным каналом.

## Последствия

- Авторизация и аудит сосредоточены в одном boundary.
- Доменные операции остаются typed и тестируемыми; generic row/action dispatch не становится публичным контрактом.
- Gateway должен поддерживать idempotency, correlation/run IDs и независимую ревизию policy.
- Совместимость capability проверяется при создании runtime revision.
