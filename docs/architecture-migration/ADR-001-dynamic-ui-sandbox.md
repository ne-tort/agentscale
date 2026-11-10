# ADR-001: Dynamic UI sandbox

- **Статус:** Принято; **Superseded where applicable** ADR-006/ADR-008 для подписанной динамической доставки plugin app. Для build-time declarative UI и запрета произвольного кода из tenant data остаётся действующим.
- **Дата:** 2026-11-01

## Границы supersede

ADR-006 разрешает подписанные OCI-артефакты plugin app, а ADR-008 заменяет trust
и lifecycle агентов на отдельные `PluginClaims`, Plugin Gateway и activator.
Это не отменяет принцип ADR-001: без подписи/digest/compatibility/CSP произвольный
код из БД, Pod или неподписанного пакета по-прежнему не доставляется.

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
- Для подписанной plugin-доставки supersede-границу определяют ADR-006/ADR-008;
  ADR-009, пока он в статусе «Предложено», авторинг и trusted CI не закрепляет.
