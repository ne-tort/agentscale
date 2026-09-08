# Container env & secrets — Pod runtime configuration

Meta-table syntax для **переменных окружения sandbox Pod** и **секретов** без plaintext в meta JSON.

**As-built (MVP):** Pod получает `WORKSPACE_KEY`, `PROJECT_ID`, MinIO hydrate credentials, плюс merged `container_env` / `container_env_secrets` из module meta — literal, static `secret_ref`, и row `value_from` / `secret_ref_from` ([container_env_loader.py](../../../apps/api/src/prodavan/application/pod_service/container_env_loader.py)).

См. [AI keys / Vault](../../02-ai-provider-keys/domain.md) · [env-matrix](../../../07-infrastructure/env-matrix.md).

## Три concerns (не смешивать)

```text
1. secrets upload   → user enters value in meta UI → Vault/file store → secret_ref in row
2. secret reference → row / module meta holds secret_ref only (never plaintext)
3. pod env bind     → container_env* slugs → inject on launch/sync into k8s Pod spec
```

| Concern | Meta slug | Stored where | Pod effect |
|---------|-----------|--------------|------------|
| Upload secret | column `secret_ref` + UI | Vault `vault://…` | — |
| Reference | row body `secret_ref` | DB JSONB | — |
| Plain env | `container_env` | meta document | env var in Pod |
| Secret env | `container_env_secrets` | meta + Vault resolve | envFrom / secretKeyRef |

## Slug: `container_env` (plain values)

Optional meta document — array of env entries applied when project Pod starts.

```json
[
  {
    "env_name": "LOG_LEVEL",
    "value": "info",
    "when": ["project.launch", "project.sync"]
  },
  {
    "env_name": "CUSTOM_FLAG",
    "value_from": {
      "table_slug": "settings",
      "row_id": "default",
      "field": "flag_value"
    }
  }
]
```

| Field | Description |
|-------|-------------|
| `env_name` | Kubernetes container env name (UPPER_SNAKE) |
| `value` | Literal string (non-secret only) |
| `value_from` | Read from cabinet row field at materialize/launch time |
| `when` | Lifecycle events: `project.launch`, `project.sync`, `project.resumed`, `project.reload` |

**Rules:**
- No API keys, tokens, passwords in `value` — use `container_env_secrets`.
- Resolved env snapshot may be written to project workspace meta for audit (future).

## Slug: `container_env_secrets`

Maps Pod env names to **secret references**, resolved at launch by platform (never stored in meta plaintext).

```json
[
  {
    "env_name": "S4B_API_TOKEN",
    "secret_ref_from": {
      "table_slug": "integrations",
      "row_id": "{row_id}",
      "field": "s4b_secret_ref"
    },
    "when": ["project.launch", "project.sync"]
  },
  {
    "env_name": "CUSTOM_WEBHOOK_SECRET",
    "secret_ref": "vault://cabinet_secrets/cab_{cabinet_id}/wh_001",
    "when": ["project.launch"]
  }
]
```

| Field | Description |
|-------|-------------|
| `secret_ref` | Static platform ref (`vault://…`, `file://…` dev) |
| `secret_ref_from` | Dynamic ref from row field (column type `secret_ref`) |

**Runtime (target):**
1. `PodCommand.sync_desired(RUNNING)` loads enabled project's module bindings
2. Merge `container_env` + resolved `container_env_secrets`
3. `pod_spec.py` adds env / envFrom Secret (k8s Secret created per Pod or shared SA)

**Gap:** P-META-ENV-01 — **done** (#162); P-META-ENV-02 row refs — **done** (#163).

## Column type: `secret_ref` (row storage)

Like `file_ref`, but stores **reference** after secure upload:

```json
{
  "table_slug": "integrations",
  "name": "s4b_secret_ref",
  "type": "secret_ref",
  "label": "S4B token",
  "secret": {
    "provider": "vault",
    "path_prefix": "cabinet_secrets/{cabinet_id}/",
    "rotate": false
  }
}
```

**Row value after UI upload:**

```json
{
  "secret_ref": "vault://cabinet_secrets/cab_abc/s4b_token_001",
  "label": "S4B production",
  "created_at": "2026-08-30T12:00:00Z"
}
```

**UI flow (as-built):**
1. Column type `secret_ref` rendered with core `AppValuePreference` (`widget: value` + `secret: true` / type `secret_ref`) — same tile as URL/login, obscured.
2. On save, Flutter calls `POST /cabinets/{id}/modules/{id}/secrets/upload` (requires cabinet write ACL).
3. Platform `CabinetSecretStore.put(cabinet_id, …)` → `vault://cabinet_secrets/{cabinet_id}/…` or file backend.
4. Row stores only `{secret_ref, secret_ref_prefix?, label?, created_at?}` — **never** plaintext.
5. Plaintext strings and extra `password`/`secret` keys in the object are rejected by row validator.
6. `secret_ref` must include this cabinet's id (`assert_cabinet_secret_scope`) — cannot bind another company's vault path.
7. Pod env resolve (`container_env_secrets`) re-checks cabinet scope before `SecretStore.get`.

**Forbidden:** dedicated `secret_upload` meta widget (removed). Use core value preference + `secret: true`.

**Gap:** none — P-META-VAULT-01 shipped (column type, upload API, Flutter masked core widget).

## Slug: `secrets` (optional module-level catalog)

Declarative list of named secrets for documentation + validation (not plaintext):

```json
[
  {
    "id": "s4b_token",
    "label": "S4B API",
    "env_name": "S4B_API_TOKEN",
    "scope": { "projects": "bound" },
    "upload": {
      "provider": "vault",
      "path_template": "cabinet_secrets/{cabinet_id}/s4b"
    }
  }
]
```

Links row `secret_ref` columns to expected Vault paths. Optional in v1.

## Vault integration (as-built vs target)

| Area | As-built | Target |
|------|----------|--------|
| AI provider keys | `vault://ai_keys/{id}` via `RoutingSecretStore` | unchanged |
| Cabinet/module secrets | — | `vault://cabinet_secrets/…` |
| Pod env inject | MinIO only | `container_env_secrets` → k8s Secret |
| Meta plaintext | Forbidden by guide | Enforced by validator |

Settings: `VAULT_ADDR`, `VAULT_TOKEN`, `VAULT_KV_MOUNT`, `VAULT_KV_PATH_PREFIX` — see [L03-ai-keys](../../12-layer-docs/L03-ai-keys.md).

## Security invariants

1. **Meta JSON never contains secret values** — only `secret_ref` strings.
2. **Upload and bind are separate** — upload creates ref; `container_env_secrets` consumes ref at launch.
3. **ACL:** secret readable only by cabinet/project scope; Pod SA receives projected Secret, not Vault token.
4. **Audit:** `secret.uploaded`, `pod.env.bound` events (target).

## Backend implementation map (target)

| Step | Component |
|------|-----------|
| Parse slugs | `ModuleMetaDocumentService` + validator |
| Resolve row refs | `MaterializePlanner` / launch hook |
| Vault read | `RoutingSecretStore.get(secret_ref)` |
| k8s inject | `pod_spec.build_pod_body` env + Secret volumes |
| Launch hook | `PodCommand._apply_running` after materialize |

## Gaps summary

| ID | Description | Priority |
|----|-------------|----------|
| P-META-ENV-01 | `container_env` + static `container_env_secrets` → pod_spec | **done** (#162) |
| P-META-ENV-02 | `value_from` / `secret_ref_from` row resolution at launch | **done** (#163) |
| P-META-VAULT-01 | `secret_ref` column + upload UI → Vault | **done** |
| P-META-VAULT-02 | Cabinet-scoped Vault path prefix + ACL | **done** |
