# Plugin-модули: краткое введение

> **TARGET + отдельный static implementation inventory; live unverified.**
> Plugin platform сейчас emergency disabled, а независимый аудит на HEAD
> `6ee720dd` не подтверждает ни один сквозной сценарий. Полный текст v4 читается
> только с [errata](plugin-architecture-complete.md). Текущее состояние —
> [STOP-SHIP](../migration/plugin-migration-status.md); все acceptance gates —
> [pending](../migration/plugin-acceptance-map.md); authoring-матрица —
> [PLUGIN-AUTHORING](../PLUGIN-AUTHORING.md). Наличие контрактов ниже не делает
> target as-built live и не разрешает deploy.

**Target design:** [`plugin-architecture-complete.md`](plugin-architecture-complete.md) с errata.
**Решения:** [ADR-006](../architecture-migration/ADR-006-plugin-modules.md), [ADR-007](../architecture-migration/ADR-007-direct-plugin-db.md), [ADR-008](../architecture-migration/ADR-008-lifecycle-and-boundaries.md); [ADR-009](../architecture-migration/ADR-009-module-source-authoring.md) — **Предложено**.
**Реестр ADR:** [`../architecture-migration/README.md`](../architecture-migration/README.md).

## Смысл

Модуль — **полноценное приложение**: свой frontend, свой backend, реальные таблицы в PostgreSQL, свои миграции, динамическая загрузка кода. Declarative-app (JSON-описание экранов + generic runtime) в конечном состоянии удаляется полностью.

## Что важно помнить

1. **Переиспользуем код и контракты, а не «как есть».** Ядро `CapabilityGatewayService`, `AuditEvent`, `dynamic_app_registry` — да. А вот `:8001`, `PodBridgeClaims`, общий MinIO-бакет, Celery-outbox, `SandboxClaim` и zip+Ed25519 **заменяются** на профессиональную модель (см. ADR-008). Плагин — не агентский Pod: другой scope, stateless, HTTP-driven.
2. **Реальные таблицы с прямым DSN.** Отдельная БД `tenant_<company_id>`, схема `module_<module>` во владении роли плагина, полный SQL и self-managed Liquibase/Flyway/Alembic миграции. Платформенная `prodavan` недоступна. Остальная инфраструктура — через typed адаптеры в отдельном trust-контуре `/capabilities/v1/*`. См. [ADR-007](../architecture-migration/ADR-007-direct-plugin-db.md).
3. **RLS не является границей.** Граница — отдельная БД + `GRANT CONNECT` + `NOCREATEDB NOCREATEROLE` + `search_path` + две NP из одного генератора + `pgaudit`.
4. **Изоляция.** Отдельный static-origin для iframe **без** `allow-same-origin`, CSP и стабилизированный target RPC; tenant/prefix-scoped objects. Для текущей operation `objects.scoped_credentials` STS остаётся typed-unwired до отдельного post-probe availability; presign/static credentials не являются fallback для этой prefix-wide operation. OCI/cosign с pin по digest.
5. **Внешние адреса.** `/webhook/{plugin}/{company}/*`, `/pluginpage/{plugin}/{company}/*` на отдельном Ingress с `router.priority: 120` (v1); цель — wildcard subdomain.
6. **Засыпание.** Deployment `replicas: 0` + Plugin Gateway activator + `ignoreDifferences`. `SandboxClaim` остаётся только агентам.
7. **Переход.** «Всё под нож» — только target. Текущий cutover остановлен; безопасный путь — code-first expand/contract по одному context, без drop-first. Pending `2026110802` не переносить, `removal.py` не выполнять, migration-only helpers сохранить. Отсутствие snapshot **данных** не отменяет предложенный ADR-009 source snapshot/patch. См. [safe cutover](../migration/plugin-cutover-safety.md).

## Текущие static contracts — не live acceptance

| Путь | Фактический текущий контракт | Pending |
|---|---|---|
| Jobs | Server-owned `PLUGIN_JOB_DECLARATIONS` принимает только exact `(plugin_id, version, artifact_digest)` verified selectors. Signed operations образуют scoped `ManifestJobTasks`; API использует typed registration hook, standalone — deferred factory с собственным lifecycle. | Real worker, grant/digest/lease/retry fences и execution evidence |
| MCP | `mcp_servers` пересекается с durable digest-scoped registration. `PluginCapabilityService` вызывает `mcp.invoke`, broker выбирает unique canonical `rpc_operations` без infra remap; authenticated JSON-RPC mount — `POST /capabilities/v1/mcp/servers/{server_id}`. | Real registration/discovery/client/backend flow, revoke, quota/audit |
| Runtime | Фактические `plugin_instance_id`/`backend_digest` проецируются read-only через `downwardAPI`; service proxy использует fresh wake и fixed identity headers. Hello production provider stubs отвечают 503. | Real projection/wake/provider behavior и negative spoofing cases |
| Events | `PLUGIN_EVENT_SIGNING_SECRET_REF` — typed SecretStore reference, required для enabled. Dev sealed-example и read-only mounts существуют; `optional` только для disabled platform. | Real sealed secret, resolution, signing, rotation, missing-key failure |
| Objects STS | `objects.scoped_credentials` требует enabled + post-probe availability; до этого typed-unwired, без presign/static fallback для prefix-wide operation. | Target probe, AssumeRole/session policy/TTL/isolation evidence |
| CI publication | Protected `PLUGIN_PLATFORM_PUBLICATION_CONFIG`, release reverify, pinned Trivy evidence, static-origin CSP + `X-AgentScale-Bundle-Digest`; фактический OIDC POST расположен только после этих проверок. | Protected live inputs и воспроизводимый successful run отсутствуют — blockers |
| Pods / delivery | `pods.allowlist` — exact instance/project/job tuples. `secret_delivery: {mode: k8s_secret, allowlist}` reference-only; отдельный delivery Secret имеет ownership/CAS/versioned exact-set lifecycle и read-only mount. | Real pods, delivery/rotation/revoke/delete и RBAC evidence |

## Чего здесь нет

Полных контрактов адаптеров, схемы capability token, манифеста артефакта, матрицы рисков, порядка реализации и списка отвергнутых решений v1 — всё это в [`plugin-architecture-complete.md`](plugin-architecture-complete.md). Этот краткий документ фиксирует лишь текущие static contracts и не заменяет их pending acceptance.
