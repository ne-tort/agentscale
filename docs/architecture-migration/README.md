# Реестр ADR

> **Accepted ADR = архитектурное решение, не as-built.** На audited HEAD
> `6ee720dd` plugin v4 не подтверждён vertical slice и platform emergency
> disabled. Live acceptance ведётся отдельно в
> [plugin-acceptance-map.md](../migration/plugin-acceptance-map.md).

| Номер | Решение | Дата | Статус |
|---|---|---|---|
| 001 | Dynamic UI sandbox: build-time registry, код из БД не исполняется | 2026-11-01 | Принято; Superseded where applicable ADR-006/ADR-008 для signed plugin delivery |
| 002 | Capability gateway: единая прикладная точка доступа, typed contract | 2026-11-01 | Действует |
| 003 | No direct infra access: Pod/агент не получают прямых credentials | 2026-11-01 | Действует; ADR-007 supersede только для plugin tenant DB DSN |
| 004 | Runtime revision: immutable ProjectRuntimeRevision, applied_revision_id | 2026-11-01 | Действует |
| 005 | Equipment bounded context: источник цены, свежесть, provenance | 2026-11-01 | Действует |
| 006 | [Plugin-модули как целевая модель](ADR-006-plugin-modules.md) | 2026-11-07, уточнено 2026-11-09 | Принято как target; не vertical slice |
| 007 | [Прямой доступ плагина к своей tenant БД](ADR-007-direct-plugin-db.md) | 2026-11-08, уточнено 2026-11-09 | Принято как target; не live acceptance |
| 008 | [Разделение lifecycle агентов/плагинов и trust-boundaries](ADR-008-lifecycle-and-boundaries.md) | 2026-11-08, уточнено 2026-11-09 | Принято как target; ProjectIdlePauseService не изменять для plugin jobs |
| 009 | [ModuleSource metadata и trusted source→PR→CI→deploy](ADR-009-module-source-authoring.md) | 2026-11-09 | **Предложено**; не supersede, не as-built |

## Правила

- Номер присваивается следующим по порядку, не переиспользуется.
- Новое решение, отменяющее старое, явно помечает Supersedes / Superseded where
  applicable; старый файл не удаляется.
- Предложение не отменяет принятое решение и не разрешает реализацию как факт.
- Канон plugin target: ADR-006/007/008 плюс
  [v4-дизайн](../ARCHITECTURE/plugin-architecture-complete.md) **с authoritative
  errata**. При конфликте errata и старого абзаца действует errata.
- Практическая поддержка отличается от target: смотреть
  [PLUGIN-AUTHORING.md](../PLUGIN-AUTHORING.md).
- При конфликте ADR между собой приоритет у более позднего при явной пометке;
  иначе конфликт считается незакрытым и реализация не начинается.

## Закрытые конфликты и supersede-границы

| Конфликт | Каноническая развязка |
|---|---|
| ADR-001 build-time UI против динамического plugin app | ADR-001 действует для build-time declarative UI; для подписанных OCI plugin-артефактов superseded where applicable ADR-006/ADR-008. Произвольный неподписанный код запрещён. |
| ADR-006 v2: tenant DB не создаётся, `PodBridgeClaims`, `:8001` | Отменено ADR-006 после пересмотра, ADR-007 и ADR-008: tenant DB per company, `PluginClaims`, `/capabilities/v1/*` в Plugin Gateway; `:8001` только агентам. |
| ADR-007:41 использовал `:8001` для plugin adapters | Исправлено: typed adapters живут в Plugin Gateway, не в agent pod-gateway. |
| Platform role выполняет plugin DDL | Отменено: plugin role владеет схемой и сама выполняет DDL миграций; платформа только provisioning и проверка артефакта/границ. |
| Objects только tenant tags, без presigned | Отменено ADR-008: scoped presigned по умолчанию; STS опционально после проверки; теги не IAM. |
| A7 требовал `ProjectIdlePauseService` для plugin jobs | **Отменено ADR-008.** Plugin activator учитывает собственные job leases; agent idle-pause не изменяется. |
| Полный v4 текст выглядит как готовая реализация | Это target snapshot с errata. Текущий статус только STOP-SHIP и acceptance map; ни один сквозной сценарий не подтверждён. |
| Cutover начинался с drop platform tables | Отменено текущим STOP и [безопасным cutover](../migration/plugin-cutover-safety.md): code-first expand/contract A→B; pending `2026110802` не переносить, `removal.py` не выполнять. |