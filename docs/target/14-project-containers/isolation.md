# Project Containers — isolation

## Цель

Песочница проекта **изолирована** от платформенного API и от чужих проектов. Доступ наружу — **только интернет** (и DNS). Нет доступа к Postgres платформы, Redis, Kafka internal, vault mount API, соседним PVC.

## Принципы

1. **Отдельный ServiceAccount** для sandbox pods (не `prodavan-api` / не `prodavan-sandbox` probe SA как runtime identity агента).
2. **NetworkPolicy**: default deny ingress+egress; allow DNS (kube-dns); allow egress TCP 80/443 (и при необходимости S3/MinIO endpoint **только** если hydrate идёт из pod — иначе hydrate только init из API-side object store copy).
3. **Нет** hostPath к API storage; **нет** mount Secret с platform DB / OIDC admin.
4. **AI credentials** в pod — только через узкий inject на старте сессии / env из resolve (короткоживущие), не весь vault.
5. **Peer isolation**: один Project → один Pod; labels обязательны; запрет cross-namespace без явной политики.
6. Workspace files: hydrate from **object store** ([13](../13-platform-infra/)) at create/start; live MinIO CSI mount — future enhancement (hole today).

## NetworkPolicy (канон-скелет)

```text
ingress: deny all (except same-pod / metrics scrape if needed via API proxy)
egress:
  - UDP/TCP 53 → kube-dns
  - TCP 443, 80 → 0.0.0.0/0   # internet
  # optional: TCP → MinIO CIDR only if pod pulls blobs itself
```

Платформенные сервисы (Postgres, Redis, Kafka, Keycloak admin) — **не** в allowlist.

## RBAC

| Actor | Rights |
|-------|--------|
| Container module (API SA with Role) | create/get/list/watch/delete pods, jobs; get metrics; get PVC usage |
| Sandbox Pod SA | минимальный; без list secrets cluster-wide |
| Probe Job SA (`prodavan-sandbox`) | остаётся для PVC probe; **не** runtime project pods |

## Volume / data copy

| Phase | Mechanism |
|-------|-----------|
| Materialize | API / worker пишет object-ws prefix (существующий path) |
| Start pod | initContainer copies or CSI mounts workspace → `/workspace` |
| Pause | Pod stopped; PVC/ephemeral keep per profile; blobs remain in MinIO |
| Delete | Port.delete + Project wipe tree |

## Что не считается изоляцией

- Локальный `MCP_SANDBOX_SPAWN` на API node
- Общий PVC API без NetworkPolicy
- `object-ws` без pod (логический container) — transitional, не end-state
