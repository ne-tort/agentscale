# ADR-008: Разделение lifecycle агентов и плагинов, замена as-built привязок

**Статус:** Принято (2026-11-08); уточнено 2026-11-09. **Target design, не as-built:** plugin platform emergency disabled и не подтверждён vertical slice.
**Связанные:** ADR-002 (capability gateway), ADR-003, ADR-006, ADR-007.

## Контекст

v2/v3 дизайна исходили из «as-built уже работает → переиспользуем как есть».
Независимое ревью показало, что это подменяет архитектурный выбор стоимостью
миграции. Плагин — **иной principal**, чем агентский Pod:

| | Агентский Pod | Плагин |
|---|---|---|
| Scope | project, эфемерная сессия | company, долгоживущий instance |
| Состояние | файловый workspace; live volume текущего project Pod — `emptyDir`, durability через archive/object storage | stateless workload, состояние в своей БД |
| Триггер | пользовательский turn, long-running | HTTP request-driven (iframe, webhook) |
| Identity | `sub=pod:{pod_id}` + acting_employee | `plugin_id` + version + artifact digest + grant |

Решение заказчика: возможна и желательна **миграция на более профессиональную
модель**, а не цепляние за старое.

## Решение

Переиспользуются **код и контракты**; заменяются **точки привязки**.

| Компонент | Решение |
|---|---|
| `CapabilityGatewayService`, `AuditEvent`, `CapabilityGrant`, `dynamic_app_registry` | переиспользовать |
| `:8001` pod-gateway, `PodBridgeClaims`, `PodSurfaceAllowlistMiddleware` | оставить агентам **без изменений**; для плагинов — отдельный `PluginClaims` (`iss=agentscale-plugins`, `aud=capability-gateway`, `grant_id`, `artifact_digest`, короткий TTL) и route-group `/capabilities/v1/*` в Plugin Gateway. Identity выдаётся и проверяется на request/короткий lease, не хранится как статический `PLUGIN_CLAIMS` в pod env |
| Один MinIO-бакет + tenant-теги | заменить на tenant/prefix-scoped objects; **scoped presigned по умолчанию**, STS session policy только после отдельной проверки; `TenantObjectsService` остаётся фасадом аудита/квот |
| Celery-outbox как шина событий | outbox — только для исходящих webhook'ов; события плагинов → Redpanda `agentscale.plugin.events` с ручным commit, `earliest`, DLQ |
| `tar.gz + Ed25519` | OCI в GHCR + cosign keyless + SBOM/provenance + pin по digest; ADR-009 **предлагает** атомарную backend+bundle digest pair и пока не принят |
| `SandboxClaim`/`SandboxWarmPool` для плагинов | **отказаться**: Deployment `replicas: 0` + Plugin Gateway activator (патч scale, удержание запроса до Ready, idle TTL, `ignoreDifferences /spec/replicas`). Агенты остаются на SandboxClaim |
| «Единая NP» | две NP из одного генератора, общая схема egress-аудита |
| Egress-правила из JWT | из `CapabilityGrant` в БД; токен несёт `grant_id` |

### Почему не SandboxClaim (факты)

`SandboxClaim` CRD не имеет `image`/`containerPort`/`command`, требует `warmPoolRef`,
`envVarsInjectionPolicy: Disallowed`, тащит PVC 5Gi; `SandboxTemplate` один на пул и
греет `prodavan-agent-runtime:3921`, а не образ плагина; `ensure_running` не ждёт
Ready (`application/pod_service/adapters/agent_sandbox/runtime.py:336-348`).
Для N плагинов × M версий потребовалось бы N×M пулов против квоты 45 подов.

### Почему не KEDA/Knative/SPIRE/cert-manager в v1

`infra/argocd/apps/00-project.yaml:37-88` — жёсткий whitelist 27 kinds, ни одного
`keda.sh/*`, `serving.knative.dev/*`, `cert-manager.io/*`, `autoscaling/HPA` там нет.
`infra/k3s/base/guards/app-budget.yaml` — 45 подов / 12Gi requests / 26Gi limits,
3 OOM-инцидента. KEDA HTTP Add-on остаётся целевым вариантом на будущее (beta),
subdomain + cert-manager DNS-01 — целью при появлении публичного домена.

## Последствия

- Агентская система не меняется; риск регрессии агентского контура минимален.
- Появляется отдельный trust-boundary для плагинов, что устраняет privilege
  escalation через `/agent/*` и `/internal/pods/*`.
- Собственный activator — код, который надо поддерживать (idempotency при
  конкурентных wake, watchdog на застрявший 0→1, дедлайн Ready).
- Холодный старт доминируется pull-ом образа; никакой activator его не ускоряет —
  митигация: предпрогрев кэша на единственной ноде и лимит одновременных wake-up.
- **Требование доработать `ProjectIdlePauseService` отменено.** Этот сервис
  принадлежит агентским проектам. Plugin activator обязан учитывать собственные
  активные `job_id`/leases (target `ActivationState.active_job_ids`) перед
  усыплением plugin workload; agent idle-pause не связывается с plugin jobs.
  A7 в старой интеграционной карте — отменён, а не completed acceptance.

## Связанные документы

- `docs/ARCHITECTURE/plugin-architecture-complete.md` (v4), §1, §4, §5, §6, §7.4 — **target snapshot только вместе с errata**.
- `docs/migration/plugin-acceptance-map.md` — текущая карта live acceptance (сейчас `[x]` отсутствуют).
- `docs/PLUGIN-AUTHORING.md` — что описано как Supported/Target/Not implemented.
