# Alembic — миграции PostgreSQL

Управление схемой БД через **Alembic** в `apps/api/alembic/`. Миграции — единственный способ изменения DDL в shared environments; ручной `ALTER` на staging/prod запрещён.

---

## Конфигурация

### alembic.ini

```ini
[alembic]
script_location = alembic
prepend_sys_path = src
version_path_separator = os
sqlalchemy.url = driver://user:pass@localhost/prodavan

[post_write_hooks]
hooks = ruff
ruff.type = exec
ruff.executable = ruff
ruff.options = format REVISION_SCRIPT_FILENAME
```

URL берётся из env `DATABASE_URL` в `env.py` (override alembic.ini).

### env.py

```python
def run_migrations_online():
    connectable = create_async_engine(settings.database_url)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
```

- Async SQLAlchemy 2.0
- `include_schemas=True` — миграции multi-schema
- `compare_type=True` для autogenerate review

---

## Naming convention

### Revision ID

Формат: `YYYYMMDDNN` (date + sequence per day)

| Revision | Description |
|----------|-------------|
| `2026080801` | tenants schema initial |
| `2026080802` | invites + refresh_tokens |
| `2026080803` | RLS policies all schemas |
| `2026082001` | integrations schema initial |
| `2026082002` | partition integration_call_log |
| `2026082003` | specs schema initial |
| `2026082004` | agent schema initial |
| `2026082005` | mcp schema initial |

### File name

`{revision}_{slug}.py` — например `2026082001_integrations_initial.py`

### Slug rules

- Lowercase, underscores
- Max 48 chars
- Verb-noun: `add_spec_runs`, `rls_projects`

---

## Schema creation order

Dependency order при initial bootstrap:

```text
1. CREATE SCHEMA tenants, cabinets, projects, specs, prompts,
                 catalogs, integrations, mcp, agent, ops
2. tenants.* (no FK outside)
3. cabinets.* (FK → tenants.cabinets)
4. projects.*
5. specs.* (FK → projects)
6. prompts.*
7. catalogs.*
8. integrations.*
9. mcp.*
10. agent.*
11. ops.*
12. RLS ENABLE + CREATE POLICY (batch revision)
13. PARTITIONS for call_log, audit_log
```

---

## Шаблон миграции

```python
"""integrations initial

Revision ID: 2026082001
Revises: 2026080803
Create Date: 2026-08-20
"""
from alembic import op
import sqlalchemy as sa

revision = "2026082001"
down_revision = "2026080803"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS integrations")
    op.create_table(
        "cabinet_integration_policies",
        sa.Column("cabinet_id", sa.UUID(), nullable=False),
        # ...
        schema="integrations",
    )
    op.execute("""
        ALTER TABLE integrations.cabinet_integration_policies
        ENABLE ROW LEVEL SECURITY
    """)


def downgrade() -> None:
    op.drop_table("cabinet_integration_policies", schema="integrations")
    op.execute("DROP SCHEMA IF EXISTS integrations CASCADE")
```

---

## RLS в миграциях

RLS policies **всегда** в dedicated revision после CREATE TABLE:

```python
def upgrade() -> None:
    for table in CABINET_SCOPED_TABLES:
        op.execute(f"""
            CREATE POLICY cabinet_isolation ON {table}
            USING (
                tenant_id = current_setting('app.tenant_id', true)::uuid
                AND cabinet_id = current_setting('app.cabinet_id', true)::uuid
            )
        """)
```

См. [rls-policies.md](rls-policies.md).

---

## Seed data

### Platform seeds (в миграциях)

- `cabinets.cabinet_profiles` — registry профилей
- `mcp.server_definitions` — catalog MCP servers
- `mcp.agent_profiles` — kp, generic

### Pack seeds (runtime, не Alembic)

Seed pipeline M00 копирует файлы из `packages/cabinet-packs/` — **не** SQL migrations.

---

## Autogenerate workflow

```bash
# Dev only — review обязателен
alembic revision --autogenerate -m "add_line_items_index"

# Inspect diff, edit manually:
# - remove spurious drops
# - add RLS separately
# - verify schema= parameter
```

Autogenerate **не** создаёт RLS — добавлять вручную.

---

## CI/CD

GitHub Actions job `db-migrate`:

```yaml
- run: alembic upgrade head
  env:
    DATABASE_URL: ${{ secrets.STAGING_DATABASE_URL }}
```

- Staging: auto on merge to `main`
- Prod: manual approval gate + ArgoCD PreSync hook

---

## Rollback policy

| Environment | Downgrade |
|-------------|-----------|
| dev | `alembic downgrade -1` allowed |
| staging | downgrade только при hotfix PR |
| prod | **no downgrade** — forward-fix migration only |

Destructive changes (DROP COLUMN) — двухфазная миграция:
1. Deploy code ignoring column
2. Drop column migration

---

## Локальная разработка

```bash
cd apps/api
export DATABASE_URL=postgresql+asyncpg://prodavan:prodavan@localhost:5432/prodavan
alembic upgrade head
python -m prodavan.cli.seed_dev  # optional dev data
```

Docker Compose поднимает PostgreSQL 16 + auto-migrate on api start (dev).

---

## Pack migrations

Cabinet packs могут регистрировать дополнительные revisions через entry point:

```python
[project.entry-points."prodavan.alembic"]
electronics = "prodavan_packs.electronics.alembic:migrations"
```

Alembic `version_locations` включает pack paths; ordering через `depends_on`.

---

## Связанные документы

- [erd-v0.md](erd-v0.md)
- [rls-policies.md](rls-policies.md)
- [structure.md](structure.md)
