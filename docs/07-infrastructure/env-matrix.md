# Environment Matrix

Сводная таблица переменных окружения и конфигурации по средам: **dev (WSL)**, **staging**, **prod**.

---

## Legend

| Symbol | Meaning |
|--------|---------|
| ✓ | Required |
| ○ | Optional |
| — | Not used |
| 🔒 | Secret (K8s Secret / GitHub Secret) |

---

## Core platform

| Variable | dev | staging | prod | Secret | Description |
|----------|-----|---------|------|--------|-------------|
| `ENV` | dev | staging | prod | | Environment name |
| `LOG_LEVEL` | debug | info | info | | Logging verbosity |
| `DATABASE_URL` | ✓ | ✓ 🔒 | ✓ 🔒 | 🔒 | PostgreSQL async URL |
| `REDIS_URL` | ✓ | ✓ | ✓ | ○ | Rate limit cache |
| `JWT_SECRET` / `JWT_PRIVATE_KEY` | dev key | ✓ 🔒 | ✓ 🔒 | 🔒 | Token signing |
| `JWT_ACCESS_TTL_MIN` | 60 | 30 | 15 | | Access token lifetime |
| `JWT_REFRESH_TTL_DAYS` | 30 | 14 | 7 | | Refresh token |
| `CORS_ORIGINS` | `*` | staging domain | prod domain | | Flutter origins |

---

## Object storage

| Variable | dev | staging | prod | Secret | Description |
|----------|-----|---------|------|--------|-------------|
| `OBJECT_STORE_ENDPOINT` | localhost:9000 | 🔒 | 🔒 | | S3 endpoint |
| `OBJECT_STORE_ACCESS_KEY` | minio | 🔒 | 🔒 | 🔒 | |
| `OBJECT_STORE_SECRET_KEY` | minio123 | 🔒 | 🔒 | 🔒 | |
| `OBJECT_STORE_BUCKET` | prodavan-dev | prodavan-staging | prodavan-prod | | |
| `OBJECT_STORE_REGION` | us-east-1 | ru-central1 | ru-central1 | | |
| `OBJECT_STORE_USE_SSL` | false | true | true | | |

---

## Secrets / KMS

| Variable | dev | staging | prod | Secret | Description |
|----------|-----|---------|------|--------|-------------|
| `DEV_KMS_KEY` | ✓ | — | — | 🔒 | Local encryption only |
| `KMS_KEY_ID` | — | ✓ 🔒 | ✓ 🔒 | 🔒 | Cloud KMS key |
| `KMS_PROVIDER` | local | yandex | yandex | | local / yandex / vault |

---

## Agent runtime

| Variable | dev | staging | prod | Secret | Description |
|----------|-----|---------|------|--------|-------------|
| `AGENT_PROVIDER` | cursor-sdk | cursor-sdk | cursor-sdk | | Provider id |
| `CURSOR_API_KEY` | ✓ 🔒 | ✓ 🔒 | ✓ 🔒 | 🔒 | Platform Cursor key |
| `CURSOR_MODEL_DEFAULT` | claude-sonnet-4 | same | same | | Default model |
| `AGENT_CURSOR_SANDBOX_ENABLED` | true | true | true | | SDK sandbox |
| `OPENAI_API_KEY` | ○ | — | — | 🔒 | Codex spike dev only |
| `FEATURE_CODEX_PROVIDER` | false | false | false | | Feature flag |
| `FEATURE_CLAUDE_CLI` | false | false | **false** | | Claude CLI — never prod |
| `AGENT_IDLE_TIMEOUT_MIN` | 60 | 30 | 30 | | Pod idle kill |
| `AGENT_MAX_PODS_PER_TENANT` | 999 | 10 | plan-based | | Quota |

---

## MCP Gateway

| Variable | dev | staging | prod | Secret | Description |
|----------|-----|---------|------|--------|-------------|
| `MCP_GATEWAY_URL` | localhost:8080 | internal DNS | internal DNS | | Worker → gateway |
| `MCP_SESSION_JWT_TTL_MIN` | 60 | 30 | 15 | | Tool auth token |
| `S4B_API_BASE_URL` | https://api.s4b.ru | same | same | | External API |

---

## Worker pod (injected)

| Variable | Source | Description |
|----------|--------|-------------|
| `CABINET_ID` | Orchestrator | Session scope |
| `PROJECT_ID` | Orchestrator | |
| `TENANT_ID` | Orchestrator | |
| `SESSION_JWT` | Orchestrator | MCP auth |
| `WORKSPACE_PATH` | `/workspace` | Mount path |
| `S4B_LOGIN` | Ephemeral secret | Decrypted per session |
| `S4B_PASSWORD` | Ephemeral secret | |

---

## Flutter client (dart-define)

| Define | dev | staging | prod |
|--------|-----|---------|------|
| `API_BASE` | http://localhost:8000 | https://api.staging.prodavan.local | https://api.prodavan.ru |
| `ENV` | dev | staging | prod |
| `ENABLE_AGENT_DEBUG` | true | true | false |

---

## Feature flags

| Flag | dev | staging | prod | Description |
|------|-----|---------|------|-------------|
| `FEATURE_S4B` | true | true | true | electronics cabinets |
| `FEATURE_WEB_SHOPS` | true | true | plan | |
| `FEATURE_KP_EXPORT` | true | true | true | |
| `FEATURE_THINKING_STREAM` | true | true | false | Extended SSE |
| `DEBUG_INTEGRATIONS` | true | false | **false** | Web snapshots |

---

## Resource limits

| Resource | dev | staging | prod |
|----------|-----|---------|------|
| API replicas | 1 | 2 | 2–10 HPA |
| MCP gateway replicas | 1 | 2 | 2–8 |
| PG instance | docker | db.t4g.medium | db.r6g.large |
| Max upload size MB | 50 | 50 | 50 (plan override) |
| Cabinet storage quota GB | 999 | 20 | plan |

---

## Commerce → Prodavan mapping

| Commerce env | Prodavan equivalent |
|--------------|---------------------|
| `TELEGRAM_BOT_TOKEN` | — (replaced by JWT) |
| `CURSOR_API_KEY` | `CURSOR_API_KEY` (same) |
| `S4B_API_LOGIN/PASSWORD` (global) | Per-cabinet encrypted (M05) |
| `COMMERCE_PROJECT` | JWT `project_id` + object store path |
| `COMMERCE_RUNS_DIR` | `projects/{slug}/runs/` in S3 |
| `COMMERCE_PROJECTS_DIR` | `tenants/.../projects/` |
| Docker volume `./projects` | S3 + PVC subPath |
| Docker volume `./catalogs` | Per-cabinet `catalogs/` |

---

## Validation on startup

API `settings.py` validates:

```python
@model_validator(mode="after")
def check_prod_secrets(self):
    if self.env == "prod":
        assert self.dev_kms_key is None
        assert self.feature_claude_cli is False
        assert self.jwt_secret != "dev-secret-change-me"
    return self
```

Fail fast on misconfiguration.

---

## Связанные документы

- [wsl-dev.md](wsl-dev.md)
- [secrets.md](../05-backend/secrets.md)
- [topology.md](topology.md)
- [../08-migration/commerce-boundary.md](../08-migration/commerce-boundary.md)
