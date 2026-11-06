# ADR-001: Dynamic UI sandbox

- **Статус:** Принято
- **Дата:** 2026-11-01

## Контекст

Платформе нужны модульные UI-возможности, но произвольный JavaScript из БД, Pod или неподписанного пакета создаёт непредсказуемую поверхность атаки и ломает воспроизводимость релизов.

## Решение

UI capability packages поставляются как версионируемый код и регистрируются в основном web build через typed registry. Динамически меняются только entitlement, installation, feature flags, navigation metadata и resource bindings. Произвольный код из БД/Pod не исполняется.

Для будущей remote/federated доставки обязательны подписанные артефакты, compatibility checks, CSP и rollback; это не входит в первую миграционную волну.

## Последствия

- UI имеет проверяемые версии и обычный CI/CD-контракт.
- Backend manifest и frontend package должны согласовываться по capability version.
- Изменение состава функций не требует загрузки исполняемого кода из tenant data.
- DSL может использоваться для authoring/import, но не является runtime API.
