# Структура FastAPI backend

Backend Prodavan — **FastAPI** с **Clean Architecture**: domain в центре, infrastructure — адаптеры снаружи. Один deployable `apps/api/`, multi-schema PostgreSQL, интеграция с k3s для agent workers.

---

## Дерево каталогов

```text
apps/api/
├── pyproject.toml
├── alembic/
│   ├── alembic.ini
│   ├── env.py
│   └── versions/
├── src/
│   └── prodavan/
│       ├── main.py                  # FastAPI app factory
│       ├── config/
│       │   ├── settings.py          # pydantic-settings
│       │   └── logging.py
│       │
│       ├── domain/                  # чистый Python, без FastAPI/SQLAlchemy
│       │   ├── entities/
│       │   │   ├── tenant.py
│       │   │   ├── cabinet.py
│       │   │   ├── project.py
│       │   │   ├── spec_run.py
│       │   │   └── agent_session.py
│       │   ├── value_objects/
│       │   │   ├── tenant_context.py
│       │   │   ├── money.py
│       │   │   └── sandbox_path.py
│       │   ├── errors.py            # DomainError hierarchy
│       │   ├── events/
│       │   └── ports/               # Protocol interfaces
│       │       ├── project_repository.py
│       │       ├── agent_runtime.py
│       │       ├── storage_service.py
│       │       ├── secret_vault.py
│       │       └── mcp_gateway.py
│       │
│       ├── application/             # use cases
│       │   ├── services/
│       │   │   ├── create_project.py
│       │   │   ├── switch_cabinet.py
│       │   │   ├── start_agent_session.py
│       │   │   └── import_run_variants.py
│       │   ├── dto/                 # request/response models (Pydantic)
│       │   └── policies/
│       │       ├── rbac.py
│       │       └── capabilities.py
│       │
│       ├── infrastructure/
│       │   ├── persistence/
│       │   │   ├── database.py      # async engine, session factory
│       │   │   ├── models/          # SQLAlchemy ORM (per schema)
│       │   │   │   ├── tenants/
│       │   │   │   ├── projects/
│       │   │   │   ├── specs/
│       │   │   │   ├── integrations/
│       │   │   │   ├── agent/
│       │   │   │   └── ops/
│       │   │   ├── repositories/    # port implementations
│       │   │   └── rls.py           # SET app.* GUCs
│       │   ├── storage/
│       │   │   └── s3_storage.py
│       │   ├── secrets/
│       │   │   └── kms_vault.py
│       │   ├── k8s/
│       │   │   └── worker_spawner.py
│       │   ├── mcp/
│       │   │   └── gateway_client.py
│       │   └── auth/
│       │       ├── jwt.py
│       │       └── password.py
│       │
│       └── api/                     # FastAPI routers (thin)
│           ├── deps.py              # DI, TenantContext
│           ├── exception_handlers.py
│           ├── v1/
│           │   ├── router.py
│           │   ├── auth.py
│           │   ├── cabinets.py
│           │   ├── projects.py
│           │   ├── specs.py
│           │   ├── prompts.py
│           │   ├── integrations.py
│           │   ├── mcp_admin.py
│           │   ├── agent.py
│           │   ├── tenants.py
│           │   └── ops.py
│           └── internal/
│               └── rate_limit.py
│
├── tests/
│   ├── unit/
│   ├── integration/
│   └── conftest.py
│
└── openapi/
    └── openapi.yaml                 # generated or source of truth
```

---

## Слои и правила

### Domain

```python
# domain/ports/project_repository.py
class ProjectRepository(Protocol):
    async def get(self, ctx: TenantContext, project_id: UUID) -> Project: ...
    async def list_by_cabinet(self, ctx: TenantContext, cabinet_id: UUID) -> list[Project]: ...
```

- Entities — dataclasses / pydantic models **без** ORM
- Инварианты проверяются в entity methods или domain services
- **Запрещено:** `from fastapi import`, `from sqlalchemy`

### Application

Use case = один публичный метод `execute()`:

```python
class StartAgentSession:
    def __init__(
        self,
        projects: ProjectRepository,
        runtime: AgentRuntime,
        audit: AuditPort,
    ): ...

    async def execute(self, ctx: TenantContext, project_id: UUID, model: str) -> AgentSession:
        project = await self.projects.get(ctx, project_id)
        # policy checks, quota checks
        session = await self.runtime.spawn(ctx, project, model)
        await self.audit.log("agent.session_started", ...)
        return session
```

- Транзакционная граница: один use case = одна DB transaction ( где нужно)
- DTO mapping на границе api ↔ application

### Infrastructure

- SQLAlchemy 2.0 async, models mapped per PostgreSQL schema
- RLS: `SET LOCAL app.tenant_id` на каждое соединение (см. [rls-policies.md](rls-policies.md))
- K8s client для spawn worker pods

### API (Presentation)

```python
@router.post("/projects/{project_id}/sessions", response_model=SessionResponse)
async def start_session(
    project_id: UUID,
    body: StartSessionRequest,
    ctx: TenantContext = Depends(get_context),
    uc: StartAgentSession = Depends(),
):
    session = await uc.execute(ctx, project_id, body.model)
    return SessionResponse.from_domain(session)
```

- Routers **тонкие** — только validation, DI, mapping
- Exception handlers → RFC 7807 Problem+JSON

---

## PostgreSQL schemas

| Schema | Module | Таблицы |
|--------|--------|---------|
| `tenants` | M08 | tenants, users, cabinets, memberships, invites |
| `projects` | M01 | projects, attachments |
| `specs` | M02 | spec_runs, line_items, offers, variants |
| `prompts` | M03 | prompt_documents, versions |
| `catalogs` | M04 | catalog_databases, imports |
| `integrations` | M05 | policies, s4b, web_shops, call_log |
| `mcp` | M06 | servers, installations, bindings |
| `agent` | M07 | sessions, messages, runs, events |
| `ops` | M09 | audit_log, metrics |

См. [erd-v0.md](erd-v0.md).

---

## Dependency Injection

**FastAPI Depends** + factory functions in `api/deps.py`:

```python
async def get_db_session(ctx: TenantContext) -> AsyncGenerator[AsyncSession, None]:
    async with session_factory() as session:
        await apply_rls(session, ctx)
        yield session
```

Lifetime:
- `Request` scope: DB session, TenantContext
- `Singleton`: settings, K8s client, KMS client

---

## Async & concurrency

- FastAPI async endpoints default
- CPU-bound (xlsx parse) → `run_in_executor` или Celery/ARQ worker (v2)
- SSE agent stream: `StreamingResponse` + async generator
- DB: `asyncpg` driver

---

## Cross-cutting

| Concern | Location |
|---------|----------|
| Auth JWT | `infrastructure/auth/jwt.py` |
| RBAC | `application/policies/rbac.py` |
| Capabilities | `application/policies/capabilities.py` |
| Audit | `infrastructure/persistence/repositories/audit_repository.py` |
| Tracing | OpenTelemetry middleware, `trace_id` in context |
| Idempotency | `Idempotency-Key` header middleware |

---

## Internal API

Prefix `/internal/v1/` — service-to-service, mTLS or cluster network:

- `POST /internal/v1/integrations/check-rate-limit`
- `POST /internal/v1/agent/session-ready`
- `GET /internal/v1/health`

Не exposed через public ingress.

---

## Pack extensions

Cabinet packs (`packages/cabinet-packs/`) регистрируют:

```python
# entry point in pyproject.toml
[project.entry-points."prodavan.domain"]
electronics_procurement = "prodavan_packs.electronics:register"
```

Registry hooks:
- Extra use cases (classify rules)
- MCP tool filters
- Alembic seed data

Ядро **не импортирует** pack modules напрямую — только через plugin registry.

---

## Тестирование

| Layer | Strategy |
|-------|----------|
| Domain | Pure unit tests |
| Application | Mock ports |
| Infrastructure | testcontainers PostgreSQL |
| API | httpx AsyncClient + fixtures |

---

## Связанные документы

- [erd-v0.md](erd-v0.md)
- [alembic.md](alembic.md)
- [rls-policies.md](rls-policies.md)
- [openapi-layout.md](openapi-layout.md)
- [../02-architecture/overview.md](../02-architecture/overview.md)
