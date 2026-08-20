# Secrets management

Управление секретами Prodavan: S4B credentials per cabinet, JWT signing keys, provider API keys, KMS integration. **Секреты никогда не попадают в logs, prompts агента и client responses.**

---

## Классификация секретов

| Secret | Scope | Storage | Rotation |
|--------|-------|---------|----------|
| JWT signing key | Platform | K8s Secret / Vault | 90 days |
| Database password | Platform | K8s Secret | 90 days |
| S4B login/password | Cabinet | PostgreSQL encrypted | Manual + alert 90d |
| Cursor API key | Platform/Worker | K8s Secret | On compromise |
| Object store keys | Platform | K8s Secret / IAM | 90 days |
| MCP worker env | Session | Injected at pod spawn | per session |
| GitHub Actions secrets | CI | GitHub encrypted | manual |

---

## Encryption at rest (S4B credentials)

### Algorithm

**AES-256-GCM** with tenant-scoped DEK.

```text
plaintext (login/password)
  → AES-256-GCM encrypt(DEK_tenant, nonce)
  → store login_enc, password_enc BYTEA in integrations.s4b_credentials
```

### Key hierarchy

```text
CMK (KMS master)
  └── DEK tenant:{tenant_id}:integrations  (envelope encrypted)
        └── per-field ciphertext in PostgreSQL
```

KMS providers:
- **Dev:** local master key file (`DEV_KMS_KEY`) — never in prod
- **Prod:** Yandex Cloud KMS / HashiCorp Vault / AWS KMS

### Implementation

```python
class SecretVault(Protocol):
    async def encrypt(self, tenant_id: UUID, plaintext: bytes, context: str) -> bytes: ...
    async def decrypt(self, tenant_id: UUID, ciphertext: bytes, context: str) -> bytes: ...
```

`infrastructure/secrets/kms_vault.py`

---

## Decryption boundaries

| Component | Can decrypt S4B? |
|-----------|------------------|
| FastAPI API gateway | **No** — only writes encrypted |
| MCP worker pod | **Yes** — in memory for session lifetime |
| MCP Gateway | **Yes** — passes to adapter, not logged |
| Flutter client | **No** |
| Agent prompt | **No** |
| Audit log | **No** — event type only |

Flow:

```text
PUT /integrations/s4b/credentials (API)
  → vault.encrypt → store BYTEA

Agent tool s4b.search_articles (worker)
  → gateway loads creds → vault.decrypt in worker memory
  → HTTP to api.s4b.ru
  → zeroize buffer after request
```

---

## Kubernetes secrets

### prodavan-api

```yaml
apiVersion: v1
kind: Secret
metadata:
  name: prodavan-api-secrets
  namespace: prodavan
type: Opaque
stringData:
  DATABASE_URL: postgresql+asyncpg://...
  JWT_PRIVATE_KEY: |
    -----BEGIN EC PRIVATE KEY-----
    ...
  KMS_KEY_ID: "..."
```

Mounted as env vars; **not** files in prod (except JWT for rotation ease).

### agent-worker (per pod template)

```yaml
env:
  - name: CURSOR_API_KEY
    valueFrom:
      secretKeyRef:
        name: agent-provider-secrets
        key: cursor_api_key
  - name: CABINET_ID
    value: "{injected by orchestrator}"
  - name: S4B_LOGIN
    value: "{decrypted by init container, tmpfs only}"  # v2
```

MVP: orchestrator injects decrypted S4B via K8s Secret created per session, deleted on pod terminate.

---

## Environment variables (non-secret config)

Public config in ConfigMap `prodavan-config`:

```yaml
S4B_API_BASE_URL: "https://api.s4b.ru"
MCP_GATEWAY_URL: "http://mcp-gateway.prodavan.svc:8080"
AGENT_DEFAULT_MODEL: "claude-sonnet-4"
```

Never mix secrets into ConfigMaps.

---

## Rotation procedures

### S4B credentials (operator-initiated)

1. Tenant admin PUT new login/password via UI
2. API encrypts with latest `key_version`
3. Old ciphertext overwritten
4. Audit: `integration.s4b_credentials_rotated`
5. MCP worker sessions: pick up on next tool call (cache TTL 60s)

### JWT signing key

1. Generate new key pair, add as `JWT_PRIVATE_KEY_V2`
2. API issues tokens with `kid: v2`, validates both v1+v2
3. After token TTL max elapsed, remove v1
4. Zero-downtime rotation

### KMS DEK rotation

1. KMS re-encrypt DEK envelope (no data re-read)
2. Field-level re-encrypt: background job per tenant (optional)

---

## CI/CD secrets

GitHub Actions:

| Secret | Use |
|--------|-----|
| `STAGING_DATABASE_URL` | migrate staging |
| `KUBECONFIG_STAGING` | deploy |
| `CURSOR_API_KEY` | integration tests (optional) |
| `DOCKER_REGISTRY_TOKEN` | push images |

**Local GitHub Runner** (outside k3s) stores secrets in OS keychain / encrypted env file — см. [../07-infrastructure/github-runner-local.md](../07-infrastructure/github-runner-local.md).

---

## Forbidden practices

- ❌ Secrets in git (incl. `.env` committed)
- ❌ Secrets in `prompts/` AGENTS.md
- ❌ Logging request bodies with Authorization header
- ❌ Returning `password_enc` in GET API
- ❌ Agent tool args containing raw credentials
- ❌ Shared S4B creds across tenants (Commerce anti-pattern)

---

## Audit events (no secret values)

```json
{
  "event_type": "integration.s4b_credentials_rotated",
  "cabinet_id": "uuid",
  "payload": { "action": "put", "key_version": 2 }
}
```

---

## Dev / local

`.env.example` (no real values):

```bash
DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@localhost:5432/prodavan
JWT_SECRET=dev-only-change-me
DEV_KMS_KEY=base64:...
CURSOR_API_KEY=...
```

`docker compose` loads `.env` — file in `.gitignore`.

---

## Связанные документы

- [../03-modules/M05-integrations/security.md](../03-modules/M05-integrations/security.md)
- [rls-policies.md](rls-policies.md)
- [object-store.md](object-store.md)
- [../07-infrastructure/env-matrix.md](../07-infrastructure/env-matrix.md)
