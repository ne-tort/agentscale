# ProjectContainer — isolation

## Цель

Pod проекта изолирован от:

- брокеров и admin-плоскости (Postgres / Redis / Kafka / Mongo / Keycloak);
- **полного** HTTP surface API (`:8000`) — sandbox NP пускает только **Pod API `:8001`**;
- прямого MinIO (`:9000`) — hydrate через API archive + Bridge JWT;
- чужих Project Pod (ingress только из ns `prodavan` на `:3921`);
- hostPath и platform secrets (MinIO IAM в sandbox не используется).

Разрешено (as-built NetworkPolicy): DNS; интернет 80/443; к ns `prodavan` — **только TCP 8001**; **TCP 5432/5433** → любой non-cluster host (remote equipment catalogs, в т.ч. WSL/Windows gateway вроде `172.21.176.1`).

См. [tenant-infra-gateway.md](../12-layer-docs/tenant-infra-gateway.md).

## Правила

1. Отдельный ServiceAccount sandbox (не API SA).
2. NetworkPolicy egress: DNS; 80/443; TCP **8001** → `prodavan`; TCP **5432/5433** → `0.0.0.0/0` except pod/svc CIDRs (`10.42/16`, `10.43/16`). **Нет** 8000/9000/6379/9092/27017.
3. NetworkPolicy ingress: только из `prodavan` на agent-runtime `:3921`.
4. Нет mount Secret с DB/OIDC/Redis/Mongo/MinIO IAM.
5. AI credentials — узкий inject на сессию / credential broker по Bridge `pod_id`.
6. 1 Project → 1 Pod; labels обязательны.
7. Workspace: SoT = MinIO (на стороне API); в Pod — API-mediated hydrate → emptyDir.
8. Pod→API: Bridge JWT; allowlist + отдельный listener `main_pod` на `:8001`.

## NetworkPolicy (as-built)

```text
egress:
  - DNS → kube-dns (UDP/TCP 53)
  - TCP 80,443 → internet
  - TCP 8001 → namespace prodavan   # Pod API surface only
  - TCP 5432,5433 → 0.0.0.0/0 except 10.42/16,10.43/16  # remote PG catalogs
ingress:
  - TCP 3921 ← namespace prodavan    # API → agent-runtime
```

Path isolation дополнительно: allowlist middleware + Bridge scopes (defense in depth на том же :8001).

## Не изоляция

- Процессы MCP на ноде API (`MCP_SANDBOX_SPAWN`)
- «Только object-ws без Pod»
- Shared cluster Bearer для Pod→API (**отключён**; только Bridge JWT)
