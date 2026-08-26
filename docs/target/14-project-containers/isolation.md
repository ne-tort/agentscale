# ProjectContainer — isolation

## Цель

Pod проекта изолирован от:

- платформенного API / Postgres / Redis / Kafka / Keycloak admin;
- чужих Project Pod и их volumes;
- hostPath и platform secrets.

Разрешено: DNS + интернет (80/443). Данные кабинета — scoped MCP / schema creds, не суперюзер platform DB.

## Правила

1. Отдельный ServiceAccount sandbox (не API SA).
2. NetworkPolicy: deny-all; allow DNS; egress 80/443.
3. Нет mount Secret с DB/OIDC admin.
4. AI credentials — узкий inject на сессию.
5. 1 Project → 1 Pod; labels обязательны.
6. Workspace: SoT = MinIO; в Pod — hydrate (CSI live mount — усиление позже).

## NetworkPolicy (скелет)

```text
ingress: deny
egress:
  - DNS → kube-dns
  - TCP 80,443 → internet
```

## Не изоляция

- Процессы MCP на ноде API (`MCP_SANDBOX_SPAWN`)
- Общий PVC API без NetworkPolicy
- «Только object-ws без Pod»
