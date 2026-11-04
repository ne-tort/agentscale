"""Test-owned harness for the SQL legacy gateway ports (``MIG-LEGACY-PORTS-01``).

Why the tables are *mirrored* instead of ``create_all``-ed
----------------------------------------------------------
The real models declare PostgreSQL ``JSONB`` columns and PostgreSQL-only ``CHECK``
expressions (``artifact_digest ~ '^sha256:…'``, ``manifest->>'protocol' = '1.0'``,
``manifest ? 'appId'``).  SQLite renders ``JSONB`` as its own ``JSON`` type and
rejects the regex operator, so a ``create_all`` of the production metadata is not
possible on the mirror dialect — the same constraint
``tests/migration/principal_resolver/db_fixtures.py`` and
``tests/migration/m06_cutover/sqlite_evidence.py`` work around.

What keeps the mirror honest
----------------------------
* the primary keys, uniques and **foreign keys** are re-stated here, so a test
  cannot insert an installation whose definition does not exist, a grant whose
  version does not exist, or two grants for one scope — the very invariants the
  ports rely on when they treat "two rows" as an ambiguity;
* every read in the tests goes through the **real ORM model classes** and the
  **real adapter classes**, so what is proven is that the adapters' queries agree
  with the registered models rather than with an invented schema;
* the port-specific expectations are duplicated nowhere: the facade's own parser
  and the CRUD adapter's own payload shape are used to build the fixtures.

Seed helpers write through the ORM classes for the same reason.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.dynamic_apps import (
    AppDefinitionRow,
    AppGrantRow,
    AppInstanceRow,
    AppVersionRow,
)
from prodavan.infrastructure.persistence.models.identity import (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
)
from prodavan.infrastructure.persistence.models.projects import ProjectRow

#: Stable identities shared by every test module in this package.
TENANT_A = "comp_aaaaaaaaaaaaaaaaaaaa"
TENANT_B = "comp_bbbbbbbbbbbbbbbbbbbb"
CABINET_A = "cab_aaaaaaaaaaaaaaaaaaaa"
CABINET_B = "cab_bbbbbbbbbbbbbbbbbbbb"
PROJECT_A = "prj_aaaaaaaaaaaaaaaaaaaa"
PROJECT_B = "prj_bbbbbbbbbbbbbbbbbbbb"
EMPLOYEE_A = "emp_aaaaaaaaaaaaaaaaaaaa"
EMPLOYEE_B = "emp_bbbbbbbbbbbbbbbbbbbb"
SUBJECT_A = "kc-subject-aaaa"
SUBJECT_B = "kc-subject-bbbb"
DEFINITION_A = "app_aaaaaaaaaaaaaaaaaaaa"
DEFINITION_B = "app_bbbbbbbbbbbbbbbbbbbb"
VERSION_A = "aver_aaaaaaaaaaaaaaaaaaaa"
VERSION_B = "aver_bbbbbbbbbbbbbbbbbbbb"
INSTANCE_A = "ains_aaaaaaaaaaaaaaaaaaaa"
INSTANCE_B = "ains_bbbbbbbbbbbbbbbbbbbb"
GRANT_A = "agr_aaaaaaaaaaaaaaaaaaaa"
GRANT_B = "agr_bbbbbbbbbbbbbbbbbbbb"
APP_ID = "demo-plugin"
MANIFEST_VERSION = "1.0.0"
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64

#: A manifest the facade's own parser accepts: one exact plugin method binding.
MANIFEST: dict = {
    "protocol": "1.0",
    "appId": APP_ID,
    "version": MANIFEST_VERSION,
    "displayName": "Demo plugin",
    "requestedCapabilities": [{"name": "storage.kv", "reason": "persist drafts"}],
    "entrypoints": {
        "plugin": {
            "methods": [
                {
                    "method": "kv.put",
                    "capability": "storage.kv",
                    "operation": "write",
                    "resource": "drafts",
                    "domain": "storage",
                }
            ]
        }
    },
}

#: Exactly what ``application/dynamic_apps_definition/sqlalchemy_adapter`` persists.
CAPABILITY_GRANT: dict = {
    "name": "storage.kv",
    "decision": "granted",
    "constraints": {"operations": ["write"], "resources": ["drafts"], "domains": ["storage"]},
    "granted_by": "administrator",
}

def _mirror(model: type, metadata: sa.MetaData, *constraints: sa.schema.Constraint) -> sa.Table:
    """Copy one registered model's columns (JSONB -> JSON) and stated constraints.

    ``server_default`` is copied too: the real tables rely on ``func.now()`` for
    ``created_at``/``updated_at``, and a mirror that dropped it would make every
    seed insert fail on a NOT NULL the production schema never enforces.
    """

    columns = []
    for column in model.__table__.columns:
        column_type = sa.JSON() if isinstance(column.type, JSONB) else column.type
        columns.append(
            sa.Column(
                column.name,
                column_type,
                primary_key=column.primary_key,
                nullable=column.nullable if not column.primary_key else False,
                server_default=column.server_default,
            )
        )
    return sa.Table(model.__tablename__, metadata, *columns, *constraints)


def dynamic_app_metadata(*, without_unique: frozenset[str] = frozenset()) -> sa.MetaData:
    """The four ``dynamic_app_*`` tables with their real uniqueness relations.

    The foreign keys are declared with ``ForeignKey`` on the mirrored columns
    through table-level constraints so the ordering of ``create_all`` is derived
    rather than hand-maintained.

    ``without_unique`` names unique constraints to leave out.  It exists for one
    purpose only: the ports treat "the query for an exact row answered twice" as
    an ambiguity they must refuse, and such a state is *unreachable* while the
    constraints are installed.  A test that wants to prove the refusal therefore
    has to model a database whose constraint was dropped — a deviated schema,
    which is precisely what a defensive guard is for.
    """

    metadata = sa.MetaData()
    definitions = _mirror(
        AppDefinitionRow,
        metadata,
        *(
            ()
            if "uq_dynamic_app_definition_tenant_app" in without_unique
            else (
                sa.UniqueConstraint(
                    "tenant_id", "app_id", name="uq_dynamic_app_definition_tenant_app"
                ),
            )
        ),
    )
    versions = _mirror(
        AppVersionRow,
        metadata,
        sa.ForeignKeyConstraint(
            ["app_definition_id"], [definitions.c.id], name="fk_version_definition"
        ),
        sa.UniqueConstraint(
            "app_definition_id",
            "manifest_version",
            name="uq_dynamic_app_version_manifest_version",
        ),
        sa.UniqueConstraint(
            "id", "app_definition_id", name="uq_dynamic_app_version_id_definition"
        ),
    )
    grants = _mirror(
        AppGrantRow,
        metadata,
        sa.ForeignKeyConstraint(
            ["app_definition_id"], [definitions.c.id], name="fk_grant_definition"
        ),
        sa.ForeignKeyConstraint(
            ["app_version_id", "app_definition_id"],
            [versions.c.id, versions.c.app_definition_id],
            name="fk_dynamic_app_grant_version_definition",
        ),
        *(
            ()
            if "uq_dynamic_app_grant_version_scope" in without_unique
            else (
                sa.UniqueConstraint(
                    "app_version_id",
                    "scope_kind",
                    "scope_id",
                    name="uq_dynamic_app_grant_version_scope",
                ),
            )
        ),
    )
    instances = _mirror(
        AppInstanceRow,
        metadata,
        sa.ForeignKeyConstraint(
            ["app_definition_id"], [definitions.c.id], name="fk_instance_definition"
        ),
        sa.ForeignKeyConstraint(
            ["app_version_id", "app_definition_id"],
            [versions.c.id, versions.c.app_definition_id],
            name="fk_dynamic_app_instance_version_definition",
        ),
        sa.UniqueConstraint(
            "app_definition_id",
            "scope_kind",
            "scope_id",
            name="uq_dynamic_app_instance_scope",
        ),
    )
    return metadata


#: The platform tables the scope port reads for the subject's own authority.
#: Mirrored without constraints: they are read, never joined through the dynamic-app
#: relations, and their real foreign keys point at tables (``scope_registry``,
#: ``m04e_*``) this suite has no reason to build.
SCOPE_MODELS: tuple[type, ...] = (
    CompanyRow,
    EmployeeRow,
    MembershipRow,
    CabinetInstanceRow,
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    ProjectRow,
)


def scope_metadata(metadata: sa.MetaData) -> sa.MetaData:
    """Add the identity/scope tables the scope port reads into ``metadata``."""

    for model in SCOPE_MODELS:
        _mirror(model, metadata)
    return metadata


def make_engine():
    """A single-connection in-memory engine; ``StaticPool`` keeps the schema alive."""

    return create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )


async def create_schema(
    engine,
    *,
    without_unique: frozenset[str] = frozenset(),
    with_scope_tables: bool = True,
) -> sa.MetaData:
    metadata = dynamic_app_metadata(without_unique=without_unique)
    if with_scope_tables:
        scope_metadata(metadata)
    async with engine.begin() as connection:
        # SQLite enforces foreign keys only when asked; the ports rely on the
        # relations, so a fixture that bypassed them would prove nothing.
        await connection.execute(sa.text("PRAGMA foreign_keys=ON"))
        await connection.run_sync(metadata.create_all, checkfirst=False)
    return metadata


__all__ = [
    "APP_ID",
    "CABINET_A",
    "CABINET_B",
    "CAPABILITY_GRANT",
    "DEFINITION_A",
    "DEFINITION_B",
    "DIGEST_A",
    "DIGEST_B",
    "EMPLOYEE_A",
    "EMPLOYEE_B",
    "GRANT_A",
    "GRANT_B",
    "INSTANCE_A",
    "INSTANCE_B",
    "MANIFEST",
    "MANIFEST_VERSION",
    "PROJECT_A",
    "PROJECT_B",
    "SUBJECT_A",
    "SUBJECT_B",
    "TENANT_A",
    "TENANT_B",
    "VERSION_A",
    "VERSION_B",
    "create_schema",
    "dynamic_app_metadata",
    "make_engine",
]
