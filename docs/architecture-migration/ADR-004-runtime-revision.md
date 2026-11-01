# ADR-004: Immutable runtime revision

- **Статус:** Принято
- **Дата:** 2026-11-01

## Контекст

Сейчас desired runtime собирается несколькими компонентами: bindings, materialization, env, MCP, bridge scopes и Pod lifecycle. Повторное вычисление приводит к расхождению между workspace, процессом агента и API policy.

## Решение

Вводится immutable `ProjectRuntimeRevision`, создаваемый при launch, sync или reload. Revision фиксирует effective module bindings/versions, dependency graph, prompt/file hashes и provenance, MCP package locks/digests, non-secret env snapshot/hash, secret descriptors, bridge scopes, action policy, provider/model config, agent image digest и ownership/prune manifest.

Жизненный цикл: `planned -> validated -> applying -> ready`, либо `failed`; применение идемпотентно по revision/bundle digest и fail-closed. Project хранит `desired_revision_id` и `applied_revision_id`. Pod стартует только с конкретной ready revision. Старые manifest/generation поля остаются compatibility projections на время миграции.

## Последствия

- Runtime становится воспроизводимым и объяснимым по provenance.
- Sync создаёт новую ревизию, не переписывая применённую.
- Ошибки обязательной materialization блокируют готовность, а не маскируются warning.
- Потребуются lock/reconcile и безопасное удаление superseded revisions.
