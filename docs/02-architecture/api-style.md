# Стиль API (REST + WS/SSE)

Публичный контракт платформы Prodavan: **REST** для CRUD и команд, **WebSocket** для стрима агента, **SSE** для односторонних progress feeds. Спецификация — **OpenAPI 3.1**. Все project-scoped endpoints требуют контекст tenant + **заголовок `X-Cabinet-Id`**.

---

## Base URL & versioning

| Environment | Base URL |
|-------------|----------|
| Production | `https://api.prodavan.app/v1` |
| Staging | `https://api.staging.prodavan.app/v1` |

- Версия в path: `/v1/...`
- Breaking changes → `/v2/`; v1 поддерживается ≥ 12 месяцев
- OpenAPI: `GET /v1/openapi.json`

---

## Authentication

```http
Authorization: Bearer eyJhbGciOiJSUzI1NiIs...
```

JWT access token (short TTL 15 min) + refresh token (httpOnly cookie или `POST /v1/auth/refresh`).

Claims: см. [multi-tenancy.md](multi-tenancy.md).

---

## Обязательные заголовки

| Header | Required | Description |
|--------|----------|-------------|
| `Authorization` | ✅ | Bearer JWT |
| `X-Cabinet-Id` | ✅ project/cabinet scope | UUID активного кабинета |
| `X-Request-Id` | optional | Client-generated UUID; echoed as `trace_id` |
| `Idempotency-Key` | POST create | UUID для идempotent create |
| `Accept-Language` | optional | `ru`, `en` — локализация errors |

### X-Cabinet-Id rules

1. **Обязателен** для:
   - `/v1/cabinets/{cabinet_id}/*`
   - `/v1/projects/*`
   - `/v1/projects/{id}/runs/*`
   - `/v1/projects/{id}/sessions/*`
   - `/v1/projects/{id}/kp/*`

2. **Должен совпадать** с `{cabinet_id}` в path, если path содержит cabinet.

3. **Должен быть** в JWT `cabinet_ids` или caller = tenant admin.

4. **Missing header** → `400 BAD_REQUEST`:

```json
{
  "type": "https://prodavan.app/errors/missing-cabinet-id",
  "title": "Missing X-Cabinet-Id header",
  "status": 400,
  "code": "MISSING_CABINET_ID"
}
```

5. **Project belongs to other cabinet** → `404` (не раскрывать existence).

---

## REST conventions

### Resource naming

- Plural nouns: `/projects`, `/runs`, `/line-items`
- kebab-case в path: `/line-items`, не `/lineItems`
- Nested только для ownership: `/projects/{project_id}/runs`

### HTTP methods

| Method | Usage |
|--------|-------|
| GET | Read, list (cursor pagination) |
| POST | Create, actions (`/finalize`, `/export`) |
| PATCH | Partial update |
| PUT | Full replace (редко) |
| DELETE | Soft delete / archive |

### Pagination

```http
GET /v1/projects?cursor=eyJ...&limit=50
```

Response:

```json
{
  "items": [...],
  "next_cursor": "eyJ...",
  "has_more": true
}
```

### Filtering

`GET /v1/projects/{id}/line-items?needs_review=true&run_id=...`

### Sorting

`?sort=-created_at`

---

## Response envelope

### Success (single resource)

```json
{
  "data": {
    "id": "...",
    "type": "project",
    "attributes": { ... }
  },
  "meta": {
    "trace_id": "..."
  }
}
```

### Success (list)

```json
{
  "data": [ ... ],
  "meta": { "trace_id": "...", "count": 12 }
}
```

### Error (Problem Details, RFC 7807-inspired)

```json
{
  "type": "https://prodavan.app/errors/forbidden",
  "title": "Forbidden",
  "status": 403,
  "code": "FORBIDDEN",
  "detail": "Capability search.s4b not granted",
  "instance": "/v1/projects/abc/runs/def/search",
  "trace_id": "550e8400-e29b-41d4-a716-446655440099",
  "errors": [
    { "field": "cabinet_id", "code": "CAPABILITY_DENIED", "message": "..." }
  ]
}
```

Content-Type: `application/problem+json`

---

## Error codes catalog

| code | HTTP | Meaning |
|------|------|---------|
| `UNAUTHORIZED` | 401 | Invalid/expired JWT |
| `FORBIDDEN` | 403 | RBAC / capability |
| `NOT_FOUND` | 404 | Resource or RLS hide |
| `MISSING_CABINET_ID` | 400 | No X-Cabinet-Id |
| `CABINET_MISMATCH` | 400 | Header ≠ path/project |
| `VALIDATION_FAILED` | 422 | Schema/business validation |
| `CONFLICT` | 409 | Duplicate slug, active session |
| `QUOTA_EXCEEDED` | 429 | Plan limit |
| `RATE_LIMITED` | 429 | API rate limit |
| `IDEMPOTENCY_REPLAY` | 409 | Same key, different body |
| `AGENT_UNAVAILABLE` | 503 | Pod scheduling failed |
| `EXTERNAL_SERVICE` | 502 | S4B/LLM upstream |

Полный enum в OpenAPI `components.schemas.ErrorCode`.

---

## WebSocket (agent stream)

### Connect

```
wss://api.prodavan.app/v1/projects/{project_id}/sessions/{session_id}/stream
```

Query or subprotocol auth:

```http
Sec-WebSocket-Protocol: bearer, {access_token}
X-Cabinet-Id: {uuid}
```

### Event types (server → client)

| type | Payload |
|------|---------|
| `session.status` | `{ status: "running" }` |
| `agent.delta` | `{ text: "..." }` |
| `agent.tool_start` | `{ tool, summary }` |
| `agent.tool_end` | `{ tool, ok, duration_ms }` |
| `run.phase` | `{ run_id, phase }` |
| `error` | `{ code, message, recoverable }` |
| `ping` | `{}` |

Client → server:

| type | Payload |
|------|---------|
| `prompt` | `{ message: "..." }` |
| `cancel` | `{}` |
| `pong` | `{}` |

### Reconnection

- Client sends `Last-Event-Id` equivalent: `?since_seq=1234`
- Server replays from event log buffer (15 min)

---

## SSE (progress feeds)

Для длительных операций без duplex chat:

```http
GET /v1/projects/{project_id}/runs/{run_id}/events
Accept: text/event-stream
X-Cabinet-Id: ...
```

```
event: phase
data: {"phase":"search","progress":0.45}

event: done
data: {"status":"needs_review"}
```

Use cases: bulk export, pack migration, KP generation.

---

## OpenAPI conventions

### File organization

```text
openapi/
  openapi.yaml          # root
  paths/
    projects.yaml
    runs.yaml
    sessions.yaml
    cabinets.yaml
  components/
    schemas/
    parameters/
    responses/
    securitySchemes.yaml
```

### Schema naming

- Entities: `Project`, `SpecRun`, `LineItem`
- Requests: `ProjectCreateRequest`, `RunFinalizeRequest`
- Responses: `ProjectResponse`, `LineItemListResponse`

### Common parameters

```yaml
parameters:
  CabinetIdHeader:
    name: X-Cabinet-Id
    in: header
    required: true
    schema:
      type: string
      format: uuid
  ProjectId:
    name: project_id
    in: path
    required: true
    schema:
      type: string
      format: uuid
```

### Security scheme

```yaml
securitySchemes:
  bearerAuth:
    type: http
    scheme: bearer
    bearerFormat: JWT
```

Global:

```yaml
security:
  - bearerAuth: []
```

### operationId

Pattern: `{resource}_{action}` — `projects_list`, `runs_create`, `sessions_stream`.

### Examples

Каждый POST/PATCH включает `requestBody` example в OpenAPI для codegen Flutter (`openapi_generator`).

---

## Representative endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/tenants/{tid}/cabinets` | List cabinets |
| GET | `/cabinets/{cid}/manifest` | UI manifest |
| GET | `/projects` | List projects in cabinet |
| POST | `/projects` | Create project |
| POST | `/projects/{pid}/runs` | Start spec run |
| GET | `/projects/{pid}/runs/{rid}/line-items` | Positions |
| POST | `/projects/{pid}/runs/{rid}/finalize` | Operator review gate |
| POST | `/projects/{pid}/sessions` | Start agent |
| GET | `/projects/{pid}/kp/export` | Download KP xlsx |
| PUT | `/cabinets/{cid}/integrations/s4b` | Configure S4B |

---

## Rate limits (API)

| Tier | Requests/min/tenant |
|------|---------------------|
| Free | 120 |
| Pro | 600 |
| Enterprise | custom |

Headers:

```http
X-RateLimit-Limit: 600
X-RateLimit-Remaining: 587
X-RateLimit-Reset: 1735693260
```

MCP limits — отдельно, см. [mcp-gateway.md](mcp-gateway.md).

---

## Idempotency

`POST /projects` with `Idempotency-Key: uuid`:

- Same key + same body → replay stored `201` response
- Same key + different body → `409 IDEMPOTENCY_REPLAY`

Stored 24h in Redis.

---

## CORS (Flutter web)

```http
Access-Control-Allow-Origin: https://app.prodavan.app
Access-Control-Allow-Headers: Authorization, Content-Type, X-Cabinet-Id, X-Request-Id, Idempotency-Key
```

Credentials: include (refresh cookie).

---

## Связанные документы

- [overview.md](overview.md)
- [multi-tenancy.md](multi-tenancy.md)
- [mcp-gateway.md](mcp-gateway.md)
- [../01-vision/domain-model.md](../01-vision/domain-model.md)
