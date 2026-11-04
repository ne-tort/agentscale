"""Fixtures for the M04E port acceptance contract; authored, not executed.

The ports speak raw SQL through ``async_sessionmaker``, so the double below is a
statement-level fake: it records every ``(sql, params)`` pair, applies the equality
literals and bind parameters it can see, and returns mapping rows. That is enough
to prove cursor/ordering/partition behaviour, and it deliberately does **not**
emulate PostgreSQL RLS: the tenant predicate and the transaction-local context
readback are asserted on the emitted SQL instead, so a test can never pass because
the fake filtered something the real database would not.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any

from prodavan.application.migration.m04e_definition_version.backfill import (
    BackfillContext,
    EvidenceCandidate,
)

TENANT = "tenant-m04e-ports"
OTHER_TENANT = "tenant-m04e-other"
DEFINITION_ID = "appdef_0001"
OTHER_DEFINITION_ID = "appdef_0002"
APP_ID = "demo-plugin"
MANIFEST_VERSION = "1.0.0"
ARTIFACT_URI = "content://tenant-m04e-ports/appdef_0001/aver_0001"
#: Text that must never reach a refusal, a digest or a log line.
SECRET_MARKER = "super-secret-customer-payload"

SHA_A = "sha256:" + "a" * 64
SHA_B = "sha256:" + "b" * 64
SHA_C = "sha256:" + "c" * 64
SHA_D = "sha256:" + "d" * 64
SHA_E = "sha256:" + "e" * 64
SHA_F = "sha256:" + "f" * 64

SOURCE_SCHEMA = "m04.legacy.v1"
CANONICAL_SCHEMA = "m04e.v1"
SNAPSHOT_ID = "snapshot-2026-11-04"
SNAPSHOT_DIGEST = "sha256:" + "1" * 64
TRANSACTION_ID = "pg:4711"
CORRELATION_ID = "corr-m04e-ports-1"
ACTOR = "migration-operator"


def context(**overrides: Any) -> BackfillContext:
    values: dict[str, Any] = {
        "tenant_id": TENANT,
        "app_definition_id": DEFINITION_ID,
        "source_snapshot_id": SNAPSHOT_ID,
        "source_snapshot_digest": SNAPSHOT_DIGEST,
        "source_schema_version": SOURCE_SCHEMA,
        "canonical_schema_version": CANONICAL_SCHEMA,
        "transaction_id": TRANSACTION_ID,
        "correlation_id": CORRELATION_ID,
        "actor": ACTOR,
        "decision_owner": "legacy",
        "writer_generation": 0,
    }
    values.update(overrides)
    return BackfillContext(**values)


# ---------------------------------------------------------------------------
# Row shapes (exactly the columns the adapter SQL selects)
# ---------------------------------------------------------------------------


def definition_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": DEFINITION_ID,
        "tenant_id": TENANT,
        "app_id": APP_ID,
        "display_name": SECRET_MARKER,
        "description": SECRET_MARKER,
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "status": "published",
        "current_version_id": "aver_0001",
    }
    values.update(overrides)
    return values


def version_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "aver_0001",
        "app_definition_id": DEFINITION_ID,
        "manifest_version": MANIFEST_VERSION,
        "artifact_digest": SHA_A,
        "artifact_uri": ARTIFACT_URI,
        "manifest": {
            "protocol": "1.0",
            "appId": APP_ID,
            "version": MANIFEST_VERSION,
            "displayName": SECRET_MARKER,
            "entrypoints": {"plugin": {"artifact": {"sha256": "a" * 64}}},
            "requestedCapabilities": [],
        },
        "requested_capabilities": [],
        "status": "published",
    }
    values.update(overrides)
    return values


def company_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {"id": TENANT, "status": "active", "deleted_at": None}
    values.update(overrides)
    return values


def tenant_anchor_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "sco_ten_1",
        "tenant_id": TENANT,
        "tenant_type": "company",
        "owner_state": "active",
        "source_kind": "m04.company",
        "source_id": TENANT,
        "schema_version": "m04.owner.v1",
        "payload_digest": SHA_B,
    }
    values.update(overrides)
    return values


def registry_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "sco_reg_1",
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "tenant_id": TENANT,
        "cabinet_id": None,
        "project_id": None,
        "project_status": None,
        "plugin_instance_id": None,
        "owner_state": "active",
        "source_kind": "m04.company",
        "source_id": TENANT,
        "schema_version": "m04.owner.v1",
        "payload_digest": SHA_B,
        "checkpoint": "c1:1:12ab",
    }
    values.update(overrides)
    return values


def scope_mapping_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "sco_map_1",
        "source_kind": "m04.company",
        "source_id": TENANT,
        "schema_version": "m04.owner.v1",
        "source_payload_digest": SHA_B,
        "canonical_payload_digest": SHA_C,
        "scope_registry_id": "sco_reg_1",
        "mapping_state": "mapped",
        "reason_code": None,
        "checkpoint": "c1:1:12ab",
    }
    values.update(overrides)
    return values


def cabinet_anchor_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "sco_cab_1",
        "cabinet_id": "cab_0001",
        "tenant_id": TENANT,
        "tenant_type": "company",
        "owner_state": "active",
        "source_kind": "m04.cabinet",
        "source_id": "cab_0001",
        "schema_version": "m04.owner.v1",
        "payload_digest": SHA_D,
    }
    values.update(overrides)
    return values


def cabinet_grant_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "cgr_0001",
        "cabinet_id": "cab_0001",
        "company_id": TENANT,
        "mode": "assigned_ro",
        "status": "active",
    }
    values.update(overrides)
    return values


def cabinet_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "cab_0001",
        "company_id": TENANT,
        "owner_scope": "company",
        "status": "active",
    }
    values.update(overrides)
    return values


def project_anchor_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "sco_prj_1",
        "project_id": "prj_0001",
        "tenant_id": TENANT,
        "tenant_type": "company",
        "cabinet_id": "cab_0001",
        "project_status": "active",
        "owner_state": "active",
        "source_kind": "m04.project",
        "source_id": "prj_0001",
        "schema_version": "m04.owner.v1",
        "payload_digest": SHA_E,
    }
    values.update(overrides)
    return values


def project_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "prj_0001",
        "company_id": TENANT,
        "cabinet_id": "cab_0001",
        "status": "active",
    }
    values.update(overrides)
    return values


def promotion_row(**overrides: Any) -> dict[str, Any]:
    """A per-partition durable promotion record (the only shape the table allows)."""

    values: dict[str, Any] = {
        "id": "m04e_prom_1",
        "tenant_id": TENANT,
        "definition_id": DEFINITION_ID,
        "revision_id": "rev_0001",
        "outcome": "blocked",
        "source_snapshot_id": SNAPSHOT_ID,
        "source_digest": SNAPSHOT_DIGEST,
        "target_digest": SHA_F,
        "valid_count": 0,
        "archived_count": 0,
        "quarantined_count": 1,
        "current_version_resolution_digest": SHA_C,
        "artifact_equality_digest": SHA_D,
        "gate_evidence": {"MIG-M04-01": "blocked", "MIG-M04-02": "blocked"},
        "previous_revision_id": None,
        "recorded_at": "2026-11-04T00:00:00+00:00",
    }
    values.update(overrides)
    return values


def target_definition_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": DEFINITION_ID,
        "tenant_id": TENANT,
        "app_id": APP_ID,
        "scope_kind": "tenant",
        "scope_id": TENANT,
        "lifecycle": "published",
        "current_version_id": "aver_0001",
        "source_snapshot_id": SNAPSHOT_ID,
        "source_digest": SHA_A,
    }
    values.update(overrides)
    return values


def target_version_row(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "id": "aver_0001",
        "definition_id": DEFINITION_ID,
        "tenant_id": TENANT,
        "app_id": APP_ID,
        "manifest_version": MANIFEST_VERSION,
        "artifact_digest": SHA_A,
        "artifact_uri": ARTIFACT_URI,
        "lifecycle": "published",
        "source_snapshot_id": SNAPSHOT_ID,
        "source_digest": SHA_B,
    }
    values.update(overrides)
    return values


def tables(**overrides: Any) -> dict[str, list[dict[str, Any]]]:
    """A complete, internally consistent partition by default."""

    values: dict[str, list[dict[str, Any]]] = {
        "definitions": [definition_row()],
        "versions": [version_row()],
        "companies": [company_row()],
        "tenant_anchors": [tenant_anchor_row()],
        "registry": [registry_row()],
        "scope_mappings": [scope_mapping_row()],
        "cabinet_anchors": [],
        "cabinet_grants": [],
        "cabinets": [],
        "project_anchors": [],
        "projects": [],
        "promotion": [promotion_row()],
        "target_definitions": [],
        "target_versions": [],
    }
    values.update(overrides)
    return values


# ---------------------------------------------------------------------------
# Statement-level fake session
# ---------------------------------------------------------------------------

#: ``(sql fragment, table key)`` — longest/most specific fragments first.
_ROUTES: tuple[tuple[str, str], ...] = (
    ("FROM m04e_definition_version_promotion_evidence", "promotion"),
    ("FROM scope_owner_project_anchors", "project_anchors"),
    ("FROM scope_owner_cabinet_anchors", "cabinet_anchors"),
    ("FROM scope_owner_tenant_anchors", "tenant_anchors"),
    ("FROM scope_owner_mappings", "scope_mappings"),
    ("FROM scope_registry", "registry"),
    ("FROM cabinet_company_grants", "cabinet_grants"),
    ("FROM cabinet_instances", "cabinets"),
    ("FROM projects", "projects"),
    ("FROM companies", "companies"),
    ("FROM m04e_dynamic_app_definitions", "target_definitions"),
    ("FROM m04e_dynamic_app_versions", "target_versions"),
    ("FROM dynamic_app_definitions", "definitions"),
    ("FROM dynamic_app_versions", "versions"),
)

_LITERAL = re.compile(r"(\w+) = '([^']*)'")
_PAGED_PARAMS = frozenset({"page_limit", "after_source_id"})

#: Bind name -> physical column, per table. The adapters bind a *semantic* name
#: (``definition_id``, ``tenant_id``) whose column differs by table, so the fake must
#: apply the real predicate instead of quietly skipping an unknown column.
_PARAM_COLUMNS: Mapping[str, Mapping[str, str]] = {
    "definitions": {"definition_id": "id"},
    "versions": {"definition_id": "app_definition_id", "version_id": "id"},
    "target_definitions": {"definition_id": "id"},
    "target_versions": {"version_id": "id"},
    "companies": {"tenant_id": "id"},
    "scope_mappings": {"registry_id": "scope_registry_id"},
    "cabinet_grants": {"tenant_id": "company_id"},
    "cabinets": {"cabinet_id": "id"},
    "projects": {"project_id": "id"},
}


class Result:
    def __init__(self, rows: tuple[dict[str, Any], ...] = ()) -> None:
        self._rows = rows

    def mappings(self) -> "Result":
        return self

    def all(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def one(self) -> dict[str, Any]:
        if len(self._rows) != 1:
            raise AssertionError(f"expected exactly one row, got {len(self._rows)}")
        return self._rows[0]

    def one_or_none(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _Transaction:
    async def __aenter__(self) -> "_Transaction":
        return self

    async def __aexit__(self, *_args: object) -> bool:
        return False


class FakeSession:
    """Statement-level PostgreSQL stand-in for the M04E raw-SQL adapters."""

    def __init__(
        self,
        data: Mapping[str, list[dict[str, Any]]] | None = None,
        *,
        readback: Mapping[str, str] | None = None,
        order_rows: bool = True,
        honour_cursor: bool = True,
        key_counts: Mapping[str, int] | None = None,
    ) -> None:
        self.tables = {key: list(value) for key, value in (data or tables()).items()}
        self.readback = {
            "tenant_id": None,
            "migration_role": "true",
            "row_security": "on",
            "transaction_isolation": "serializable",
        }
        self.readback.update(dict(readback or {}))
        self.order_rows = order_rows
        self.honour_cursor = honour_cursor
        self.key_counts = dict(key_counts or {})
        self.statements: list[tuple[str, dict[str, Any]]] = []
        self.applied: dict[str, Any] | None = None

    # -- session surface --------------------------------------------------

    def begin(self) -> _Transaction:
        return _Transaction()

    def in_transaction(self) -> bool:
        return True

    def sql(self) -> tuple[str, ...]:
        return tuple(sql for sql, _params in self.statements)

    def executed(self, fragment: str) -> list[tuple[str, dict[str, Any]]]:
        return [
            (sql, params) for sql, params in self.statements if fragment in sql
        ]

    async def execute(self, statement: Any, params: Mapping[str, Any] | None = None) -> Result:
        sql = str(statement)
        bound = dict(params or {})
        self.statements.append((sql, bound))
        if "set_config(" in sql:
            self.applied = bound
            return Result()
        if "current_setting('app.tenant_id', true)" in sql and "row_security" in sql:
            readback = dict(self.readback)
            if readback["tenant_id"] is None and self.applied is not None:
                readback["tenant_id"] = self.applied.get("tenant_id")
            return Result((readback,))
        if "COUNT(*) FILTER" in sql:
            return Result(({
                "manifest_version_count": self.key_counts.get("manifest_version", 1),
                "artifact_digest_count": self.key_counts.get("artifact_digest", 1),
            },))
        return Result(self._rows_for(sql, bound))

    # -- query emulation --------------------------------------------------

    def _rows_for(self, sql: str, params: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
        key = next((table for fragment, table in _ROUTES if fragment in sql), None)
        if key is None:
            raise AssertionError(f"unexpected statement: {sql}")
        rows = list(self.tables.get(key, ()))

        for column, value in _LITERAL.findall(sql):
            rows = [row for row in rows if column not in row or str(row[column]) == value]

        for name, value in params.items():
            if name in _PAGED_PARAMS:
                continue
            column = _PARAM_COLUMNS.get(key, {}).get(name, name)
            if rows and column not in rows[0]:
                continue
            rows = [row for row in rows if str(row.get(column)) == str(value)]

        if "LIMIT 1" in sql:
            return tuple(rows[:1])

        if self.order_rows:
            rows.sort(key=lambda row: str(row.get("id")))
        cursor = params.get("after_source_id")
        if cursor is not None and self.honour_cursor:
            rows = [row for row in rows if str(row.get("id")) > str(cursor)]
        limit = params.get("page_limit")
        if limit is not None:
            rows = rows[: int(limit)]
        return tuple(rows)


class FakeSessionFactory:
    """Callable ``async_sessionmaker`` stand-in that records the sessions it hands out."""

    def __init__(self, session: FakeSession) -> None:
        self.session = session
        self.opened = 0

    def __call__(self) -> "_SessionContext":
        self.opened += 1
        return _SessionContext(self.session)


class _SessionContext:
    def __init__(self, session: FakeSession) -> None:
        self._session = session

    async def __aenter__(self) -> FakeSession:
        return self._session

    async def __aexit__(self, *_args: object) -> bool:
        return False


def session(
    data: Mapping[str, list[dict[str, Any]]] | None = None, **kwargs: Any,
) -> tuple[FakeSession, FakeSessionFactory]:
    """``FakeSession`` + its factory, forwarding the same surface and order.

    ``data`` is positional here because :class:`FakeSession` takes the table map as
    its first positional argument and the contract writes ``session(tables(...))``.
    An earlier revision of this helper accepted keywords only, which made every call
    site fail with ``TypeError`` before a single assertion ran — a helper that no
    longer mirrors the class it wraps hides nothing about the ports, it only breaks
    the contract's own spelling of the fixture.
    """

    fake = FakeSession(data, **kwargs)
    return fake, FakeSessionFactory(fake)


class _DialectBind:
    """The ``session.get_bind()`` surface the ledger store reads a dialect from."""

    def __init__(self, name: str) -> None:
        self.dialect = SimpleNamespace(name=name)


def dialect_session(
    dialect: str = "postgresql", data: Mapping[str, list[dict[str, Any]]] | None = None,
    **kwargs: Any,
) -> tuple[FakeSession, FakeSessionFactory]:
    """A fake session that also answers ``get_bind().dialect.name``.

    The ledger store proves its dialect before touching a row: on PostgreSQL it must
    bind the transaction-local tenant context (which the fake records as statements),
    and on any other dialect it must refuse unless a mirror is explicitly allowed.
    Without this surface the fake could express neither verdict, and the store would
    fail with an ``AttributeError`` instead of a named refusal — a test that passes
    because the fixture is incomplete rather than because the port is right.

    The ledger tables are *not* routed here: this fake answers dialect/context probes
    and nothing else. Row-level ledger behaviour belongs to the SQLite mirror in
    ``test_m04e_evidence_store.py``, because a table-map fake would let a broken
    predicate pass — the store's own exact-key reads have to be evaluated by a real
    database, not by a dictionary lookup that happens to match.
    """

    fake = FakeSession(data, **kwargs)
    fake.get_bind = lambda: _DialectBind(dialect)  # type: ignore[attr-defined]

    async def _execute(statement: Any, params: Mapping[str, Any] | None = None) -> Any:
        sql = str(statement)
        bound = dict(params or {})
        fake.statements.append((sql, bound))
        if "set_config(" in sql:
            fake.applied = bound
            return Result()
        if "current_setting('app.tenant_id', true)" in sql:
            readback = dict(fake.readback)
            if readback["tenant_id"] is None and fake.applied is not None:
                readback["tenant_id"] = fake.applied.get("tenant_id")
            return Result((readback,))
        raise AssertionError(
            "the dialect-only fake answers context probes only; ledger behaviour is "
            f"exercised against the SQLite mirror, not here: {sql}"
        )

    fake.execute = _execute  # type: ignore[method-assign]
    return fake, FakeSessionFactory(fake)


class _NeverOpenedFactory:
    """A session factory that fails the test if anything opens a session.

    Used by the construction-only assertions: building a store is pure wiring, so a
    constructor that connected would be a defect this test sees instead of a latent one
    waiting for production.
    """

    def __call__(self) -> Any:  # pragma: no cover - only runs on a regression
        raise AssertionError("constructing an adapter must not open a session")


def expected_source_digest(candidate: EvidenceCandidate) -> str:
    """Recompute the source digest independently of the production helper."""

    import json

    encoded = json.dumps({
        "source_kind": candidate.source_kind,
        "source_id": candidate.source_id,
        "schema_version": candidate.schema_version,
        "phase": candidate.phase,
        "tenant_id": candidate.tenant_id,
        "app_definition_id": candidate.app_definition_id,
        "canonical_schema_version": candidate.canonical_schema_version,
        "source_state": candidate.source_state.value,
        "payload": candidate.payload,
    }, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = [
    "ACTOR",
    "APP_ID",
    "ARTIFACT_URI",
    "CANONICAL_SCHEMA",
    "CORRELATION_ID",
    "DEFINITION_ID",
    "FakeSession",
    "FakeSessionFactory",
    "MANIFEST_VERSION",
    "OTHER_DEFINITION_ID",
    "OTHER_TENANT",
    "SECRET_MARKER",
    "SHA_A",
    "SHA_B",
    "SHA_C",
    "SHA_D",
    "SHA_E",
    "SHA_F",
    "SNAPSHOT_DIGEST",
    "SNAPSHOT_ID",
    "SOURCE_SCHEMA",
    "TENANT",
    "TRANSACTION_ID",
    "cabinet_anchor_row",
    "cabinet_grant_row",
    "dialect_session",
    "cabinet_row",
    "company_row",
    "context",
    "definition_row",
    "expected_source_digest",
    "project_anchor_row",
    "project_row",
    "promotion_row",
    "registry_row",
    "scope_mapping_row",
    "session",
    "tables",
    "target_definition_row",
    "target_version_row",
    "tenant_anchor_row",
    "version_row",
]
