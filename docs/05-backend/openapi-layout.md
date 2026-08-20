# OpenAPI layout

REST API Prodavan специфицируется в **OpenAPI 3.1**. Спецификация — контракт между FastAPI backend и Flutter client (codegen).

---

## Расположение файлов

```text
apps/api/
├── openapi/
│   ├── openapi.yaml          # source of truth (modular)
│   ├── components/
│   │   ├── schemas/
│   │   ├── parameters/
│   │   ├── responses/
│   │   └── securitySchemes.yaml
│   └── paths/
│       ├── auth.yaml
│       ├── cabinets.yaml
│       ├── projects.yaml
│       ├── specs.yaml
│       ├── prompts.yaml
│       ├── integrations.yaml
│       ├── mcp.yaml
│       ├── agent.yaml
│       ├── tenants.yaml
│       └── ops.yaml
└── src/prodavan/api/         # implementation follows spec
```

**Workflow v1:** hand-written YAML → codegen Pydantic models + Dart client.

**Workflow v2 (optional):** FastAPI generates YAML → CI diff check against committed spec.

---

## URL layout

### Public API

Base: `https://{host}/api/v1`

| Prefix | Module | Description |
|--------|--------|-------------|
| `/auth/*` | M08 | login, refresh, logout |
| `/tenants/*` | M08 | tenant admin |
| `/cabinets/*` | M00 | CRUD, switch, manifest |
| `/cabinets/{cid}/projects/*` | M01 | projects, attachments |
| `/cabinets/{cid}/specs/*` | M02 | runs, items, variants, kp export |
| `/cabinets/{cid}/prompts/*` | M03 | prompt documents |
| `/cabinets/{cid}/catalogs/*` | M04 | catalog DBs |
| `/cabinets/{cid}/integrations/*` | M05 | S4B, web shops |
| `/cabinets/{cid}/mcp/*` | M06 | MCP admin |
| `/cabinets/{cid}/projects/{pid}/agent/*` | M07 | sessions, messages, stream |
| `/ops/*` | M09 | audit, metrics |

### Internal API

Base: `/internal/v1` — cluster-only, mTLS.

---

## Common parameters

### Path

| Param | Type | Description |
|-------|------|-------------|
| `cabinet_id` | uuid | Cabinet scope |
| `project_id` | uuid | Project scope |
| `run_id` | uuid | Spec run |
| `session_id` | uuid | Agent session |

### Headers

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | yes* | `Bearer {access_token}` |
| `X-Cabinet-Id` | conditional | Active cabinet (if not in JWT) |
| `X-Request-Id` | optional | Client correlation |
| `Idempotency-Key` | optional | POST create operations |
| `If-Match` | optional | ETag for prompts |

*Except `/auth/login`, `/auth/register`, health.

---

## Security schemes

```yaml
securitySchemes:
  bearerAuth:
    type: http
    scheme: bearer
    bearerFormat: JWT

security:
  - bearerAuth: []
```

JWT claims (documented in schema `TokenClaims`):

```yaml
TokenClaims:
  type: object
  properties:
    sub: { type: string, format: uuid }
    tenant_id: { type: string, format: uuid }
    cabinet_ids: { type: array, items: { type: string, format: uuid } }
    exp: { type: integer }
```

---

## Response format

### Success

Direct resource or wrapper:

```json
{
  "id": "uuid",
  "display_name": "Закупка август"
}
```

Lists paginated:

```json
{
  "items": [],
  "next_cursor": "base64...",
  "total": 42
}
```

### Error (RFC 7807)

```json
{
  "type": "https://prodavan.ru/errors/FORBIDDEN",
  "title": "Forbidden",
  "status": 403,
  "code": "FORBIDDEN",
  "detail": "Недостаточно прав для cabinet.admin",
  "trace_id": "abc-123"
}
```

---

## SSE endpoints (OpenAPI extension)

Agent stream не REST polling — documented as extension:

```yaml
/cabinets/{cabinet_id}/projects/{project_id}/agent/sessions/{session_id}/stream:
  get:
    operationId: streamAgentSession
    x-sse: true
    parameters:
      - name: Last-Event-ID
        in: header
        schema: { type: string }
    responses:
      "200":
        content:
          text/event-stream:
            schema:
              $ref: "#/components/schemas/StreamEvent"
```

Codegen: отдельный hand-written Dart `SseClient` (не openapi-generator).

---

## Codegen pipeline

### Dart (Flutter)

```bash
openapi-generator generate \
  -i openapi/openapi.yaml \
  -g dart-dio \
  -o packages/api_client \
  --additional-properties=pubName=prodavan_api
```

Post-process:
- Export barrel `api_client.dart`
- Custom interceptors wrap generated Dio client

### Python (optional)

```bash
datamodel-code-generator --input openapi.yaml --output src/prodavan/application/dto/generated.py
```

Prefer hand-written DTOs v1; generated for parity check.

---

## Versioning

- URL prefix `/v1` — breaking changes → `/v2`
- Non-breaking: add optional fields, new endpoints
- Deprecation header: `Sunset: Sat, 01 Jan 2028 00:00:00 GMT`

---

## Validation

CI:

```bash
openapi-cli validate openapi/openapi.yaml
schemathesis run --base-url http://localhost:8000 openapi/openapi.yaml  # staging
```

Breaking change detection:

```bash
openapi-diff openapi/openapi.yaml openapi/openapi.prev.yaml
```

---

## Tags (Swagger UI groups)

| Tag | Module |
|-----|--------|
| Auth | M08 |
| Cabinets | M00 |
| Projects | M01 |
| Specs & KP | M02 |
| Prompts | M03 |
| Catalogs | M04 |
| Integrations | M05 |
| MCP | M06 |
| Agent | M07 |
| Tenants | M08 |
| Operations | M09 |

---

## Связанные документы

- [structure.md](structure.md)
- [../04-frontend/architecture.md](../04-frontend/architecture.md)
- [../03-modules/M05-integrations/api.md](../03-modules/M05-integrations/api.md)
