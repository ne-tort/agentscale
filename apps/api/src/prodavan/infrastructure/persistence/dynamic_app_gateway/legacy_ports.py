"""SQL implementations of the *legacy* dynamic-app gateway ports.

Task ``MIG-LEGACY-PORTS-01``.

``application/dynamic_app_plugin_gateway/ports.py`` declares three protocols that
``build_shadow_gateway`` needs for "exact parity": ``DynamicPluginRepository``,
``CapabilityScopePort`` and ``CapabilityQuotaPort``.  Until this module existed no
SQL implementation of any of them was in the tree, so ``ShadowGatewayPorts``
could never be stated and M06 stayed blocked on
``m06_shadow_gateway_ports_missing``.  Two of the three are implemented here,
over the tables that already exist; the third is deliberately **absent** (see
"The quota port is not implemented" below), because the only quota tables in the
schema measure something else and an always-allow port would be a lie.

Tables read (all pre-existing, none invented)
---------------------------------------------
=============================  ===============================================
``dynamic_app_definitions``    app identity, tenant, scope  (``AppDefinitionRow``)
``dynamic_app_versions``       immutable manifest/artifact   (``AppVersionRow``)
``dynamic_app_grants``         capability decisions per version+scope
                                                             (``AppGrantRow``)
``dynamic_app_instances``      scoped installation           (``AppInstanceRow``)
``companies``, ``employees``,  the subject's live tenant and its role
``memberships``
``cabinet_instances``,         whether the installation scope really belongs
``cabinet_company_grants``,    to that tenant and is still live
``cabinet_employee_assignments``
``projects``
=============================  ===============================================

Nothing here writes: every method is a read, and no method opens a transaction
of its own beyond the read-only session it is handed by its factory.

The repository resolves exactly, or not at all
----------------------------------------------
``resolve`` answers the *exact* installation — ``(tenant, app_id,
manifest_version, scope)`` — or ``None``.  It never returns a near miss: a
record from a neighbouring scope or tenant would let the facade's
``CROSS_TENANT``/``CROSS_VERSION`` checks fire on data that is not the caller's
own, which is a leak disguised as a quarantine.  A miss therefore surfaces as
``APP_MISSING``; the facade's consistency checks stay in place as a second line
for records that *were* found.

Two related rules follow from the same principle:

* **lifecycle is not filtered here.**  An archived/disabled instance is still
  returned, so the facade can answer ``LIFECYCLE_DENIED`` instead of
  ``APP_MISSING``.  Hiding it would rewrite a policy refusal into "nothing
  there", and the shadow evidence would stop matching the legacy owner.
* **ambiguity is refused, never guessed.**  A uniqueness constraint makes more
  than one definition/version/instance impossible today; if one is observed
  anyway the repository raises :class:`AmbiguousPluginRecord` (the facade turns
  that into a quarantine), because picking "the first row" is exactly how a
  gateway ends up authorizing the wrong installation.

The scope port is fail-closed in both of its halves
---------------------------------------------------
``SqlCapabilityScopePort.allows`` answers "may *this subject* exercise *this
capability* in *this scope*", from two independent sources:

1. **the subject's own authority** — a live, non-deleted ``active`` company, a
   live ``active`` employee bound by ``id`` or ``keycloak_sub``, a membership in
   that company, and an installation scope that provably belongs to it (an
   active cabinet granted to the tenant, an active project of the tenant, or the
   tenant scope itself).  This mirrors ``application/migration/principal_resolver``
   rule for rule; a second, divergent notion of "may act in this scope" would be
   worse than none.
2. **the persisted capability decision** — the ``dynamic_app_grants`` row of the
   installation's *published* version for that *same* scope must carry a
   ``granted`` (and unexpired) entry for the capability that the version's
   manifest binds to ``request.method``.

Both halves answer ``False`` — never ``True`` — when the data is absent,
expired, denied, malformed, or when several installations in the scope disagree
about the same method name (the port signature carries a method name, not an
instance id, so a genuine tie is unanswerable and must be refused).  Database
errors are *not* swallowed: they propagate, and the facade quarantines them as
``PORT_FAILED``.  Only data-shape errors (a grant payload that is not a JSON
array of capability grants) degrade to a refusal, because a malformed row is a
decision not to authorize, not an outage.

The quota port is *not* implemented — and why
---------------------------------------------
``build_shadow_gateway`` requires ``legacy_quota``, and this module does not
supply one.  The two quota tables in the schema do not measure a plugin
invocation:

``company_cabinet_quotas``      ``max_cabinets``, ``max_packages_per_cabinet``,
                               ``max_bundle_import_mb`` — installation-time
                               limits over cabinets/packages, with no relation
                               to a single capability call.
``company_tenant_infra_quotas`` ``*_ops_per_minute`` and per-plane
                               ``max_*_bytes`` for the *storage planes*
                               (cache/docs/userdb/kafka/objects); the rate half
                               lives in Redis counters, not in SQL, and mapping
                               a plugin capability onto one storage plane would
                               be an invented rule.

No per-invocation quota exists, and no usage counter exists in SQL to enforce an
aggregate one.  An always-allow ``CapabilityQuotaPort`` would silently turn
"quota enforced" into "quota ignored" for every shadow observation — the one
degradation this migration may not make.  :func:`build_legacy_gateway_ports`
therefore reports :data:`QUOTA_PORT_BLOCKER` and describes the table that would
be needed (:data:`QUOTA_REQUIRED_TABLE` / :data:`QUOTA_REQUIRED_COLUMNS`).

Engines that drop ``tzinfo``
----------------------------
Every timestamp column read here is ``DateTime(timezone=True)``.  PostgreSQL
returns aware datetimes; SQLite (the mirror used by the acceptance tests) stores
the wall clock and returns it naive.  The domain contracts refuse naive
timestamps, so :func:`_aware_utc` normalises them to UTC instead of letting a
test dialect change the meaning of a policy decision.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.dynamic_app_plugin_gateway.contracts import (
    PluginInvokeRequest,
    PluginMethod,
    PluginOpenRequest,
    PluginRecord,
)
from prodavan.domain.cabinets.types import (
    CabinetAssignmentStatus,
    CabinetCompanyGrantScope,
    CabinetGrantStatus,
    CabinetOwnerScope,
    CabinetStatus,
)
from prodavan.domain.dynamic_apps import (
    AppDefinition,
    AppGrant,
    AppInstance,
    AppLifecycleStatus,
    AppScope,
    AppScopeKind,
    AppVersion,
    CapabilityDecision,
    CapabilityGrant,
    RequestedCapability,
)
from prodavan.domain.identity import EmployeeStatus
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

#: Company states.  ``deleted_at`` (soft delete) is checked separately, because a
#: tombstoned company keeps ``status='active'`` in this schema.
ACTIVE_COMPANY_STATUS: Final = "active"
ACTIVE_PROJECT_STATUS: Final = "active"

# ---------------------------------------------------------------------------
# The missing quota port — a named blocker, never an always-allow stub
# ---------------------------------------------------------------------------

#: Stable code reported wherever a host needs the quota port.
QUOTA_PORT_BLOCKER: Final = "m06_legacy_quota_table_missing"

#: The table that would make a real quota port possible.  Nothing creates it today.
QUOTA_REQUIRED_TABLE: Final = "dynamic_app_capability_quota"

#: Columns such a table needs before a port can answer "allowed" without guessing:
#: the enforceable window, the per-window request/byte ceilings, and the usage
#: counter the port would compare against (kept in the same row so the check is a
#: single read and cannot drift from its own counter).
QUOTA_REQUIRED_COLUMNS: Final[tuple[tuple[str, str], ...]] = (
    ("tenant_id", "text not null — the company the quota belongs to"),
    ("plugin_instance_id", "text not null — the installation the quota bounds"),
    ("capability", "text not null — 'storage.kv', 'network.fetch', … or '*' only if a reviewed constraint says so"),
    ("window_sec", "integer not null — the period the counters are measured over"),
    ("max_requests", "integer not null — ceiling per window"),
    ("max_bytes", "bigint not null — ceiling on requested_bytes per window"),
    ("used_requests", "integer not null — counter the port reads and the executor advances"),
    ("used_bytes", "bigint not null — byte counter, same discipline"),
    ("window_started_at", "timestamptz not null — marks when the counters were reset"),
)

#: One-sentence explanation a log line or a report can carry verbatim.
QUOTA_PORT_REASON: Final = (
    "no per-invocation capability quota exists: company_cabinet_quotas bounds "
    "installed cabinets/packages and company_tenant_infra_quotas bounds storage "
    "planes (rate counters live in Redis), so neither can decide whether one "
    "plugin invocation is within quota; a dynamic_app_capability_quota row "
    "(tenant, instance, capability, window, request/byte ceilings + counters) "
    "is required before a quota port can refuse anything honestly"
)


class AmbiguousPluginRecord(LookupError):
    """More than one row answers the same exact question.

    Raised instead of selecting a row: a gateway that guesses authorizes the
    wrong installation.  The facade catches it and quarantines the call as
    ``PORT_FAILED``; the legacy decision adapter re-raises it as
    ``IdentityResolutionError``.
    """


# ---------------------------------------------------------------------------
# Small, pure helpers
# ---------------------------------------------------------------------------


def _aware_utc(value: datetime | None) -> datetime | None:
    """Interpret a naive timestamp as UTC.

    PostgreSQL ``timestamptz`` always arrives aware, so this only engages on a
    dialect that drops the offset (the SQLite mirror).  A naive value is never
    passed on: the domain contracts reject naive timestamps outright.
    """

    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _text(value: object) -> str:
    """A required identifier: whitespace-stripped, empty means "not stated"."""

    return value.strip() if isinstance(value, str) else ""


def _manifest_methods(manifest: object) -> tuple[PluginMethod, ...]:
    """Parse the manifest's plugin methods with *the facade's own* parser.

    Imported lazily and called as a static function: the facade recomputes
    ``_methods(version.manifest)`` on every ``invoke`` and compares it to the
    recorded context, so a second, locally written parser would be a silent
    source of parity drift.  This is the one place where reaching into a private
    helper is the safer option, and it touches no instance state.
    """

    from prodavan.application.dynamic_app_plugin_gateway.service import (
        DynamicAppPluginGatewayService,
    )

    if not isinstance(manifest, Mapping):
        return ()
    return DynamicAppPluginGatewayService._methods(manifest)


def _grants_of_payload(payload: object) -> tuple[CapabilityGrant, ...]:
    """Decode ``dynamic_app_grants.grants`` exactly like the CRUD adapter writes it.

    ``application/dynamic_apps_definition/sqlalchemy_adapter`` persists
    ``[grant.model_dump(mode="json") for grant in grants]`` and reads it back
    with the same strictness; accepting anything looser here would let a row that
    the writer would refuse authorize a call.
    """

    if not isinstance(payload, list):
        raise ValueError("grant payload must be a JSON array")
    return tuple(CapabilityGrant.model_validate(item) for item in payload)


def _requested_of_payload(payload: object) -> tuple[RequestedCapability, ...]:
    if not isinstance(payload, list):
        raise ValueError("requested capabilities must be a JSON array")
    result: list[RequestedCapability] = []
    for item in payload:
        if not isinstance(item, Mapping):
            raise ValueError("requested capability entries must be objects")
        result.append(
            RequestedCapability(
                name=item["name"],
                reason=item["reason"],
                constraints=item.get("constraints"),
                optional=bool(item.get("optional", False)),
            )
        )
    return tuple(result)


# ---------------------------------------------------------------------------
# DynamicPluginRepository
# ---------------------------------------------------------------------------


class SqlDynamicPluginRepository:
    """``DynamicPluginRepository`` over the four ``dynamic_app_*`` tables.

    Every query is filtered by the tenant the *request* carries — never by a
    value the plugin supplied — so a foreign tenant's rows are unreachable by
    construction rather than by a later comparison.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        self._session_factory = session_factory

    # -- protocol ----------------------------------------------------------

    async def resolve(self, request: PluginOpenRequest) -> PluginRecord | None:
        """The exact installation for this tenant/app/version/scope, or ``None``."""

        if not isinstance(request, PluginOpenRequest):
            return None
        if not (
            _text(request.tenant_id)
            and _text(request.app_id)
            and _text(request.manifest_version)
            and _text(request.subject_id)
            and isinstance(request.scope, AppScope)
        ):
            return None

        async with self._session_factory() as session:
            definition_row = await self._one(
                session,
                select(AppDefinitionRow).where(
                    AppDefinitionRow.tenant_id == request.tenant_id,
                    AppDefinitionRow.app_id == request.app_id,
                ),
                "dynamic_app_definitions(tenant_id, app_id)",
            )
            if definition_row is None:
                return None
            version_row = await self._one(
                session,
                select(AppVersionRow).where(
                    AppVersionRow.app_definition_id == definition_row.id,
                    AppVersionRow.manifest_version == request.manifest_version,
                ),
                "dynamic_app_versions(app_definition_id, manifest_version)",
            )
            if version_row is None:
                return None
            instance_row = await self._one(
                session,
                select(AppInstanceRow).where(
                    AppInstanceRow.app_definition_id == definition_row.id,
                    AppInstanceRow.scope_kind == request.scope.kind.value,
                    AppInstanceRow.scope_id == request.scope.id,
                ),
                "dynamic_app_instances(app_definition_id, scope_kind, scope_id)",
            )
            if instance_row is None:
                return None
            grant_rows = await self._grant_rows(
                session, app_version_id=version_row.id, scope=request.scope
            )
            definition = self._definition(definition_row)
            version = self._version(version_row)
            return PluginRecord(
                definition=definition,
                version=version,
                grants=tuple(self._grant(row) for row in grant_rows),
                instance=self._instance(instance_row),
                methods=_manifest_methods(version.manifest),
            )

    async def get_grants(
        self,
        *,
        instance_id: str,
        app_version_id: str,
        tenant_id: str | None = None,
    ) -> tuple[AppGrant, ...]:
        """Grant rows of one install's version in the instance's own scope.

        ``tenant_id`` is optional on purpose: the protocol states none, and the
        tenant is already implied by the instance's definition.  When a caller
        *does* know the tenant (the facade always does), passing it turns the
        read into an asserted match instead of a derived one.

        An unknown instance, a version that does not belong to it, or a tenant
        that does not own it answers ``()`` — no grants, therefore no authority.
        """

        if not _text(instance_id) or not _text(app_version_id):
            return ()
        async with self._session_factory() as session:
            instance_row, definition_row = await self._install(session, instance_id)
            if instance_row is None or definition_row is None:
                return ()
            if instance_row.app_version_id != app_version_id:
                return ()
            if tenant_id is not None and definition_row.tenant_id != tenant_id:
                return ()
            scope = AppScope(
                kind=AppScopeKind(instance_row.scope_kind), id=instance_row.scope_id
            )
            rows = await self._grant_rows(
                session, app_version_id=app_version_id, scope=scope
            )
            return tuple(self._grant(row) for row in rows)

    async def get_instance(
        self,
        *,
        instance_id: str,
        scope: AppScope,
        tenant_id: str | None = None,
    ) -> AppInstance | None:
        """The installation, only when it sits in ``scope`` and a tenant owns it."""

        if not _text(instance_id) or not isinstance(scope, AppScope):
            return None
        async with self._session_factory() as session:
            instance_row, definition_row = await self._install(session, instance_id)
            if instance_row is None or definition_row is None:
                return None
            if tenant_id is not None and definition_row.tenant_id != tenant_id:
                return None
            if (
                instance_row.scope_kind != scope.kind.value
                or instance_row.scope_id != scope.id
            ):
                return None
            return self._instance(instance_row)

    # -- internals ---------------------------------------------------------

    @staticmethod
    async def _one(
        session: AsyncSession, statement: Any, what: str
    ) -> Any | None:
        """Exactly one row, ``None``, or a refusal — never "the first one"."""

        rows = (await session.execute(statement)).scalars().all()
        if not rows:
            return None
        if len(rows) > 1:
            raise AmbiguousPluginRecord(f"{what} answered {len(rows)} rows")
        return rows[0]

    @staticmethod
    async def _grant_rows(
        session: AsyncSession, *, app_version_id: str, scope: AppScope
    ) -> Sequence[AppGrantRow]:
        """Grant rows for one version and one exact scope, in a stable order."""

        statement = (
            select(AppGrantRow)
            .where(
                AppGrantRow.app_version_id == app_version_id,
                AppGrantRow.scope_kind == scope.kind.value,
                AppGrantRow.scope_id == scope.id,
            )
            .order_by(AppGrantRow.id)
        )
        rows = (await session.execute(statement)).scalars().all()
        if len(rows) > 1:
            # ``uq_dynamic_app_grant_version_scope`` makes this impossible in a
            # correctly migrated database; two decisions for one scope would be
            # an ambiguity the facade must see, not one this port may resolve.
            raise AmbiguousPluginRecord(
                "dynamic_app_grants(app_version_id, scope_kind, scope_id) "
                f"answered {len(rows)} rows"
            )
        return rows

    @staticmethod
    async def _install(
        session: AsyncSession, instance_id: str
    ) -> tuple[AppInstanceRow | None, AppDefinitionRow | None]:
        """The installation and the definition that proves which tenant owns it."""

        instance_row = await session.get(AppInstanceRow, instance_id)
        if instance_row is None:
            return None, None
        definition_row = await session.get(
            AppDefinitionRow, instance_row.app_definition_id
        )
        if definition_row is None:
            # An instance without its definition is a broken row: it can be
            # neither tenant-scoped nor authorized, so it is treated as missing.
            return None, None
        return instance_row, definition_row

    # -- row -> domain -----------------------------------------------------

    @staticmethod
    def _definition(row: AppDefinitionRow) -> AppDefinition:
        return AppDefinition(
            id=row.id,
            tenant_id=row.tenant_id,
            app_id=row.app_id,
            display_name=row.display_name,
            description=row.description,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            status=AppLifecycleStatus(row.status),
            current_version_id=row.current_version_id,
            created_at=_aware_utc(row.created_at),
            updated_at=_aware_utc(row.updated_at),
        )

    @staticmethod
    def _version(row: AppVersionRow) -> AppVersion:
        if not isinstance(row.manifest, Mapping):
            raise ValueError("manifest must be persisted as an object")
        return AppVersion(
            id=row.id,
            app_definition_id=row.app_definition_id,
            manifest_version=row.manifest_version,
            artifact_digest=row.artifact_digest,
            manifest=dict(row.manifest),
            requested_capabilities=_requested_of_payload(row.requested_capabilities),
            status=AppLifecycleStatus(row.status),
            created_at=_aware_utc(row.created_at),
            published_at=_aware_utc(row.published_at),
        )

    @staticmethod
    def _grant(row: AppGrantRow) -> AppGrant:
        return AppGrant(
            id=row.id,
            app_definition_id=row.app_definition_id,
            app_version_id=row.app_version_id,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            grants=_grants_of_payload(row.grants),
            issued_at=_aware_utc(row.issued_at),
            expires_at=_aware_utc(row.expires_at),
        )

    @staticmethod
    def _instance(row: AppInstanceRow) -> AppInstance:
        return AppInstance(
            id=row.id,
            app_definition_id=row.app_definition_id,
            app_version_id=row.app_version_id,
            scope=AppScope(kind=AppScopeKind(row.scope_kind), id=row.scope_id),
            status=AppLifecycleStatus(row.status),
            runtime_ref=row.runtime_ref,
            created_at=_aware_utc(row.created_at),
            updated_at=_aware_utc(row.updated_at),
        )


# ---------------------------------------------------------------------------
# CapabilityScopePort
# ---------------------------------------------------------------------------


class SqlCapabilityScopePort:
    """``CapabilityScopePort``: the subject's authority over the scope *and* a
    persisted capability decision for the exact method being invoked.

    Both halves must allow.  Every other outcome — no employee row, a deleted
    company, an archived cabinet, an unexpired-but-denied capability, a method
    the manifest does not bind exactly once, or two installations in the same
    scope that disagree — answers ``False``.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    async def allows(
        self,
        *,
        tenant_id: str,
        subject_id: str,
        scope: AppScope,
        request: PluginInvokeRequest,
    ) -> bool:
        if not _text(tenant_id) or not _text(subject_id):
            return False
        if not isinstance(scope, AppScope):
            return False
        if not isinstance(request, PluginInvokeRequest):
            return False
        method = _text(request.method)

        async with self._session_factory() as session:
            employee_id = await self._live_member(
                session, tenant_id=tenant_id, subject_id=subject_id
            )
            if employee_id is None:
                return False
            if not await self._scope_belongs_to_tenant(
                session, tenant_id=tenant_id, scope=scope, employee_id=employee_id
            ):
                return False
            decisions = await self._installation_decisions(
                session, tenant_id=tenant_id, scope=scope, method=method
            )
        # A single, unanimous ``True`` is the only allow: an empty set means "no
        # installation here", a mixed set means two installations disagree and
        # the port signature (a method name, not an instance id) cannot say which
        # one the caller meant.
        return decisions == frozenset({True})

    # -- authority over the scope ------------------------------------------

    @staticmethod
    async def _live_member(
        session: AsyncSession, *, tenant_id: str, subject_id: str
    ) -> str | None:
        """The employee id of a live member of ``tenant_id``, or ``None``.

        The subject may be stated either way this repository actually stores it:
        ``employees.id`` (what the principal resolver emits as ``subject_id``) or
        ``employees.keycloak_sub`` (what an auth boundary sees).  Two live rows
        matching one value is a refusal, not a tie-break.
        """

        company = (
            await session.execute(
                select(CompanyRow.status, CompanyRow.deleted_at).where(
                    CompanyRow.id == tenant_id
                )
            )
        ).first()
        if company is None:
            return None
        status, deleted_at = company
        if str(status) != ACTIVE_COMPANY_STATUS or deleted_at is not None:
            return None

        employees = (
            await session.execute(
                select(EmployeeRow.id, EmployeeRow.status, EmployeeRow.deleted_at).where(
                    or_(
                        EmployeeRow.id == subject_id,
                        EmployeeRow.keycloak_sub == subject_id,
                    )
                )
            )
        ).all()
        live = [
            (str(employee_id), str(employee_status))
            for employee_id, employee_status, employee_deleted in employees
            if employee_deleted is None
        ]
        if len(live) != 1:
            return None
        employee_id, employee_status = live[0]
        if employee_status != EmployeeStatus.ACTIVE:
            return None

        membership = (
            await session.execute(
                select(MembershipRow.id).where(
                    MembershipRow.company_id == tenant_id,
                    MembershipRow.employee_id == employee_id,
                )
            )
        ).first()
        return employee_id if membership is not None else None

    async def _scope_belongs_to_tenant(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        scope: AppScope,
        employee_id: str,
    ) -> bool:
        if scope.kind is AppScopeKind.TENANT:
            return scope.id == tenant_id
        if scope.kind is AppScopeKind.CABINET:
            return await self._cabinet_is_granted(
                session, tenant_id=tenant_id, cabinet_id=scope.id, employee_id=employee_id
            )
        if scope.kind is AppScopeKind.PROJECT:
            project = (
                await session.execute(
                    select(
                        ProjectRow.company_id, ProjectRow.cabinet_id, ProjectRow.status
                    ).where(ProjectRow.id == scope.id)
                )
            ).first()
            if project is None:
                return False
            company_id, cabinet_id, status = project
            if str(company_id) != tenant_id:
                return False
            if str(status) != ACTIVE_PROJECT_STATUS:
                return False
            return await self._cabinet_is_granted(
                session,
                tenant_id=tenant_id,
                cabinet_id=str(cabinet_id),
                employee_id=employee_id,
            )
        return False

    @staticmethod
    async def _cabinet_is_granted(
        session: AsyncSession,
        *,
        tenant_id: str,
        cabinet_id: str,
        employee_id: str,
    ) -> bool:
        """Active cabinet + tenant grant + not-revoked assignment.

        Same three conditions, in the same order, as
        ``principal_resolver.SqlTenantIdentityDirectory._cabinet``: an explicit
        revoked assignment stays a revocation even when the company still holds
        the cabinet.
        """

        cabinet = (
            await session.execute(
                select(
                    CabinetInstanceRow.status,
                    CabinetInstanceRow.owner_scope,
                    CabinetInstanceRow.owner_company_id,
                    CabinetInstanceRow.company_grant_scope,
                ).where(CabinetInstanceRow.id == cabinet_id)
            )
        ).first()
        if cabinet is None:
            return False
        status, owner_scope, owner_company_id, grant_scope = cabinet
        if str(status) != CabinetStatus.ACTIVE:
            return False
        granted = str(grant_scope) == CabinetCompanyGrantScope.ALL or (
            str(owner_scope) == CabinetOwnerScope.COMPANY
            and str(owner_company_id or "") == tenant_id
        )
        if not granted:
            grant = (
                await session.execute(
                    select(CabinetCompanyGrantRow.id).where(
                        CabinetCompanyGrantRow.cabinet_id == cabinet_id,
                        CabinetCompanyGrantRow.company_id == tenant_id,
                        CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE,
                    )
                )
            ).first()
            granted = grant is not None
        if not granted:
            return False
        assignment = (
            await session.execute(
                select(CabinetEmployeeAssignmentRow.status).where(
                    CabinetEmployeeAssignmentRow.cabinet_id == cabinet_id,
                    CabinetEmployeeAssignmentRow.employee_id == employee_id,
                )
            )
        ).first()
        return assignment is None or str(assignment[0]) == CabinetAssignmentStatus.ACTIVE

    # -- the persisted capability decision ---------------------------------

    async def _installation_decisions(
        self,
        session: AsyncSession,
        *,
        tenant_id: str,
        scope: AppScope,
        method: str,
    ) -> frozenset[bool]:
        """One boolean per installation of this tenant in this scope.

        An installation that cannot be evaluated (unpublished, no version, no
        method binding, no grant row, malformed payload) contributes ``False``:
        the question "does a grant in this scope allow this capability" then has
        no positive witness, which is a refusal.
        """

        statement = (
            select(AppInstanceRow, AppDefinitionRow)
            .join(
                AppDefinitionRow,
                AppDefinitionRow.id == AppInstanceRow.app_definition_id,
            )
            .where(
                AppDefinitionRow.tenant_id == tenant_id,
                AppInstanceRow.scope_kind == scope.kind.value,
                AppInstanceRow.scope_id == scope.id,
            )
            .order_by(AppInstanceRow.id)
        )
        rows = (await session.execute(statement)).all()
        decisions: set[bool] = set()
        for instance_row, definition_row in rows:
            decisions.add(
                await self._installation_allows(
                    session,
                    instance_row=instance_row,
                    definition_row=definition_row,
                    scope=scope,
                    method=method,
                )
            )
        return frozenset(decisions)

    async def _installation_allows(
        self,
        session: AsyncSession,
        *,
        instance_row: AppInstanceRow,
        definition_row: AppDefinitionRow,
        scope: AppScope,
        method: str,
    ) -> bool:
        if not method:
            return False
        if instance_row.status != AppLifecycleStatus.PUBLISHED.value:
            return False
        version_row = await session.get(AppVersionRow, instance_row.app_version_id)
        if version_row is None or version_row.app_definition_id != definition_row.id:
            return False
        if version_row.status != AppLifecycleStatus.PUBLISHED.value:
            return False
        try:
            bindings = tuple(
                item
                for item in _manifest_methods(version_row.manifest)
                if item.method == method
            )
            if len(bindings) != 1:
                # Zero bindings: the method is not declared.  Two bindings: the
                # manifest is ambiguous and the facade's own parser keeps both,
                # so neither may be treated as "the" capability.
                return False
            grant_rows = await SqlDynamicPluginRepository._grant_rows(
                session, app_version_id=version_row.id, scope=scope
            )
            if len(grant_rows) != 1:
                return False
            row = grant_rows[0]
            now = _aware_utc(self._clock())
            if row.expires_at is not None and now >= _aware_utc(row.expires_at):
                return False
            granted = {
                grant.name.value
                for grant in _grants_of_payload(row.grants)
                if grant.decision is CapabilityDecision.GRANTED
                and (grant.expires_at is None or now < _aware_utc(grant.expires_at))
            }
        except (ValueError, TypeError, KeyError):
            # A malformed grant payload or manifest is not an outage: it is
            # data that cannot prove authority, so the answer is "not allowed".
            # Database errors are deliberately NOT caught here — the facade
            # quarantines those as PORT_FAILED instead of reading an unavailable
            # store as a policy denial.
            return False
        return bindings[0].capability in granted


# ---------------------------------------------------------------------------
# Bundled builder
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LegacyGatewayPorts:
    """The legacy ports this repository can state today, plus what is missing.

    ``quota`` is typed ``None`` rather than ``CapabilityQuotaPort | None`` so a
    caller cannot "fix" a typing complaint by passing a stub: there is exactly
    one way to obtain a quota port, and it is to add the table named in
    :data:`QUOTA_REQUIRED_TABLE` and implement it behind the protocol.
    """

    repository: SqlDynamicPluginRepository
    scope: SqlCapabilityScopePort
    quota: None = None
    blockers: tuple[str, ...] = (QUOTA_PORT_BLOCKER,)

    @property
    def complete(self) -> bool:
        """False while a port required by ``build_shadow_gateway`` is missing."""

        return self.quota is not None and not self.blockers


def build_legacy_gateway_ports(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    clock: Callable[[], datetime] | None = None,
) -> LegacyGatewayPorts:
    """Construct the legacy ports a process can own; name the one it cannot.

    Construction opens no session (both ports only store the factory), so a host
    may call this during startup exactly like the other composition builders.
    The returned ``blockers`` always contains :data:`QUOTA_PORT_BLOCKER` today:
    ``build_shadow_gateway`` will refuse the port set, and reporting *why*
    (a missing table, not a missing wiring) is the honest answer.
    """

    if session_factory is None:
        raise ValueError("legacy gateway ports need an async_sessionmaker")
    return LegacyGatewayPorts(
        repository=SqlDynamicPluginRepository(session_factory=session_factory),
        scope=SqlCapabilityScopePort(session_factory=session_factory, clock=clock),
        quota=None,
        blockers=(QUOTA_PORT_BLOCKER,),
    )


__all__ = [
    "ACTIVE_COMPANY_STATUS",
    "ACTIVE_PROJECT_STATUS",
    "AmbiguousPluginRecord",
    "LegacyGatewayPorts",
    "QUOTA_PORT_BLOCKER",
    "QUOTA_PORT_REASON",
    "QUOTA_REQUIRED_COLUMNS",
    "QUOTA_REQUIRED_TABLE",
    "SqlCapabilityScopePort",
    "SqlDynamicPluginRepository",
    "build_legacy_gateway_ports",
]
