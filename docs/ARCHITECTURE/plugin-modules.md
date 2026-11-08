# Plugin-модули: краткое введение

**Источник истины:** [`plugin-architecture-complete.md`](plugin-architecture-complete.md) (v4, 2026-11-08).  
**Решения:** [ADR-006](../architecture-migration/ADR-006-plugin-modules.md), [ADR-007](../architecture-migration/ADR-007-direct-plugin-db.md), [ADR-008](../architecture-migration/ADR-008-lifecycle-and-boundaries.md).  
**Реестр ADR:** [`../architecture-migration/README.md`](../architecture-migration/README.md).

## Смысл

Модуль — **полноценное приложение**: свой frontend, свой backend, реальные таблицы в PostgreSQL, свои миграции, динамическая загрузка кода. Declarative-app (JSON-описание экранов + generic runtime) в конечном состоянии удаляется полностью.

## Что важно помнить

1. **Переиспользуем код и контракты, а не «как есть».** Ядро `CapabilityGatewayService`, `AuditEvent`, `dynamic_app_registry` — да. А вот `:8001`, `PodBridgeClaims`, общий MinIO-бакет, Celery-outbox, `SandboxClaim` и zip+Ed25519 **заменяются** на профессиональную модель (см. ADR-008). Плагин — не агентский Pod: другой scope, stateless, HTTP-driven.
2. **Реальные таблицы с прямым DSN.** Отдельная БД `tenant_<company_id>`, схема `module_<module>` во владении роли плагина, полный SQL и self-managed Liquibase/Flyway/Alembic миграции. Платформенная `prodavan` недоступна. Остальная инфраструктура — через typed адаптеры в отдельном trust-контуре `/capabilities/v1/*`. См. [ADR-007](../architecture-migration/ADR-007-direct-plugin-db.md).
3. **RLS не является границей.** Граница — отдельная БД + `GRANT CONNECT` + `NOCREATEDB NOCREATEROLE` + `search_path` + две NP из одного генератора + `pgaudit`.
4. **Изоляция.** Отдельный static-origin для iframe **без** `allow-same-origin`, CSP, `plugin-rpc/1` handshake; bucket-per-tenant + STS; OCI/cosign с pin по digest.
5. **Внешние адреса.** `/webhook/{plugin}/{company}/*`, `/pluginpage/{plugin}/{company}/*` на отдельном Ingress с `router.priority: 120` (v1); цель — wildcard subdomain.
6. **Засыпание.** Deployment `replicas: 0` + Plugin Gateway activator + `ignoreDifferences`. `SandboxClaim` остаётся только агентам.
7. **Переход.** «Всё под нож» — про конечное состояние. Strangler с фича-флагом нужен только из-за ~13k строк `mod_equipment`. Это dev-разработка: данные эфемерны, бэкапы, перенос данных, snapshot и dual-read **не нужны**.

## Чего здесь нет

Полных контрактов адаптеров, схемы capability token, манифеста артефакта, матрицы рисков, порядка реализации и списка отвергнутых решений v1 — всё это в [`plugin-architecture-complete.md`](plugin-architecture-complete.md).
