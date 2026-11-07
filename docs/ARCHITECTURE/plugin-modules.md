# Plugin-модули: краткое введение

**Источник истины:** [`plugin-architecture-complete.md`](plugin-architecture-complete.md) (v2, 2026-11-08).  
**Решение:** [ADR-006](../architecture-migration/ADR-006-plugin-modules.md).  
**Реестр ADR:** [`../architecture-migration/README.md`](../architecture-migration/README.md).

## Смысл

Модуль — **полноценное приложение**: свой frontend, свой backend, реальные таблицы в PostgreSQL, свои миграции, динамическая загрузка кода. Declarative-app (JSON-описание экранов + generic runtime) в конечном состоянии удаляется полностью.

## Что важно помнить

1. **Не строим новый слой.** Переиспользуем уже работающие `CapabilityGatewayService`, pod-gateway `:8001`, `PodBridgeClaims`, `TenantUserDbService`, `tenant_infra/*`, `SandboxClaim` pause/resume. Новый сервис адаптеров, новый формат JWT, PgBouncer и Knative — отвергнуты на ревью v1.
2. **Реальные таблицы с прямым DSN.** Отдельная БД `tenant_<company_id>`, схема `module_<module>` во владении роли плагина, полный SQL и self-managed Liquibase/Flyway/Alembic миграции. Платформенная `prodavan` недоступна. Остальная инфраструктура — через typed адаптеры gateway `:8001`. См. [ADR-007](../architecture-migration/ADR-007-direct-plugin-db.md).
3. **RLS не является границей.** Граница — отдельная БД + `GRANT CONNECT` + `NOCREATEDB NOCREATEROLE` + `search_path` + NetworkPolicy + `pgaudit`.
4. **Изоляция.** Отдельный origin для iframe **без** `allow-same-origin`, CSP, `plugin-rpc/1` handshake. Единый egress-контроллер (`none`/`webhooks-only`/`full`) для плагинов и агентов.
5. **Внешние адреса.** `/webhook/{plugin}/{company}/*`, `/pluginpage/{plugin}/{company}/*` на отдельном Ingress с `router.priority: 120`.
6. **Засыпание.** `SandboxClaim.operatingMode: Suspended` + warm pool — тот же механизм, что у контейнеров агентов.
7. **Переход.** «Всё под нож» — про конечное состояние. Strangler с фича-флагом нужен только из-за ~13k строк `mod_equipment`. Это dev-разработка: данные эфемерны, бэкапы, перенос данных, snapshot и dual-read **не нужны**.

## Чего здесь нет

Полных контрактов адаптеров, схемы capability token, манифеста артефакта, матрицы рисков, порядка реализации и списка отвергнутых решений v1 — всё это в [`plugin-architecture-complete.md`](plugin-architecture-complete.md).
