# ProjectContainer — isolation

## Цель

Pod проекта изолирован от:

- брокеров и admin-плоскости (Postgres / Redis / Kafka / Mongo / Keycloak);
- **не-Pod** HTTP surface платформы (admin, OIDC employee APIs) — через Pod Identity Bridge + allowlist, не через NP path filter;
- чужих Project Pod и их volumes;
- hostPath и platform secrets (кроме узкого MinIO hydrate secret).

Разрешено (as-built NetworkPolicy): DNS; интернет 80/443; к ns `prodavan` — **API `:8000`** и **MinIO `:9000`**. Данные кабинета/модуля — scoped Bridge JWT + module scopes, не DSN platform DB.

См. [tenant-infra-gateway.md](../12-layer-docs/tenant-infra-gateway.md).

## Правила

1. Отдельный ServiceAccount sandbox (не API SA).
2. NetworkPolicy: deny-all egress; allow DNS; 80/443; TCP 8000+9000 → `prodavan`.
3. Нет mount Secret с DB/OIDC admin / Redis / Mongo.
4. AI credentials — узкий inject на сессию.
5. 1 Project → 1 Pod; labels обязательны.
6. Workspace: SoT = MinIO; в Pod — hydrate (CSI live mount — усиление позже).
7. Pod→API: Bridge JWT с `project_id`/`scopes`; middleware deny-by-default вне Pod surface.

## NetworkPolicy (as-built)

```text
ingress: deny (default)
egress:
  - DNS → kube-dns (UDP/TCP 53)
  - TCP 80,443 → internet
  - TCP 8000,9000 → namespace prodavan   # API + MinIO hydrate
```

**Не** открывать: 6379, 9092, 5432, 27017, Keycloak.

Path-level изоляция («только API модуля») — **не** в NetworkPolicy; см. Pod API surface в tenant-infra-gateway.

## Не изоляция

- Процессы MCP на ноде API (`MCP_SANDBOX_SPAWN`)
- Общий PVC API без NetworkPolicy
- «Только object-ws без Pod»
- Shared cluster Bearer без Bridge scopes (legacy gap — не расширять tenant data plane на нём)
