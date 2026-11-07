# Plugin-модули: краткое введение

**Источник истины:** [`plugin-architecture-complete.md`](plugin-architecture-complete.md) (v2, 2026-11-08).  
**Решение:** [ADR-006](../architecture-migration/ADR-006-plugin-modules.md).  
**Реестр ADR:** [`../architecture-migration/README.md`](../architecture-migration/README.md).

## Смысл

Модуль — **полноценное приложение**: свой frontend, свой backend, реальные таблицы в PostgreSQL, свои миграции, динамическая загрузка кода. Declarative-app (JSON-описание экранов + generic runtime) в конечном состоянии удаляется полностью.

## Что важно помнить

1. **Не строим новый слой.** Переиспользуем уже работающие `CapabilityGatewayService`, pod-gateway `:8001`, `PodBridgeClaims`, `TenantUserDbService`, `tenant_infra/*`, `SandboxClaim` pause/resume. Новый сервис адаптеров, новый формат JWT, PgBouncer и Knative — отвергнуты на ревью v1.
2. **Реальные таблицы без прямого DSN.** Схема `tenant_<company>_module_<module>` в уже существующей `agentscale_userdb`. DDL исполняет доверенная платформенная роль по структурированному манифесту; плагин получает typed `db.query`/`db.mutate`. RLS не является границей безопасности.
3. **Миграции поставляет плагин, исполняет платформа.** Liquibase/Flyway/Alembic changelog → изолированный k8s Job → валидация запрещённых statement → подпись артефакта → `DATABASECHANGELOG`. Произвольного SQL из тела HTTP-запроса нет.
4. **Изоляция.** Отдельная схема + grant без `CREATE`, отдельный origin для iframe **без** `allow-same-origin`, NetworkPolicy без egress к PostgreSQL. Единый egress-контроллер (`none`/`webhooks-only`/`full`) для плагинов и агентов.
5. **Внешние адреса.** `/webhook/{plugin}/{company}/*`, `/pluginpage/{plugin}/{company}/*` на отдельном Ingress с `router.priority: 120`.
6. **Засыпание.** `SandboxClaim.operatingMode: Suspended` + warm pool — тот же механизм, что у контейнеров агентов.
7. **Переход.** «Всё под нож» — про конечное состояние, не про метод. Идём strangler'ом с фича-флагом и dual-read: ~13k строк `mod_equipment`, работа в `main`, бэкапов нет.

## Чего здесь нет

Полных контрактов адаптеров, схемы capability token, манифеста артефакта, матрицы рисков, порядка реализации и списка отвергнутых решений v1 — всё это в [`plugin-architecture-complete.md`](plugin-architecture-complete.md).
