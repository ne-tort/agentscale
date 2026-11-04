"""Assembly of the complete ``ShadowGatewayPorts`` for the M06 shadow gateway.

Task ``MIG-M06-PORTS-01``.

``build_shadow_gateway`` needs sixteen ports and refuses a partial set
(``legacy_scope``/``legacy_quota`` are hard requirements, because exact parity
*compares* the live legacy owners).  Until this module existed no caller
composed them, so ``ChainDependencies.m06_ports`` was permanently ``None`` and
the M06 stage could only ever report ``m06_shadow_gateway_ports_missing``.

This module is that composer.  It is deliberately **not** a second composition
root: it constructs exactly the ports the gateway declares, from the adapters
that already implement them, and reports every one it could not state.

What is derived, and from what
==============================

================================  ==============================================
port                              constructed as
================================  ==============================================
``legacy_repository``             ``SqlDynamicPluginRepository(factory)``
``legacy_scope``                  ``SqlCapabilityScopePort(factory)``
``company``                       ``SqlAuthenticatedCompanyPort(factory)``
``canonical_grants``              ``SqlCanonicalGrantReadPort(factory)``
``evidence_read``                 ``SqlCanonicalEvidenceStore(factory)``
``evidence_transaction``          the same store (it owns both halves)
``m04_scope``                     ``SqlAlchemyScopeBackfillAdapter(factory, ctx)``
``m05_instances``                 ``build_sql_resolve_plugin_instance_port(...)``
``handoff_partitions``            ``SqlPartitionHandoffReadStore(factory, scope)``
================================  ==============================================

Stated by the host, never guessed
=================================

``legacy_quota``
    There is **no** implementation and no table that could carry one
    (``infrastructure/persistence/dynamic_app_gateway/legacy_ports.py`` explains
    why in full: the only quota tables bound installed cabinets and storage
    planes, not one plugin invocation).  It is therefore an argument, and its
    absence is the named ``m06_legacy_quota_table_missing`` — never an
    always-allow stub, which would silently turn "quota enforced" into "quota
    ignored" for every shadow observation.
``m04e_definition_versions``
    ``ServerDefinitionVersionRepository`` wraps resolvers bound to a
    *caller-owned* ``AsyncSession`` inside one SERIALIZABLE transaction.  A
    session factory cannot produce that session, so the repository is supplied.
``m05_upstream`` / ``m04e_definition_versions``
    Per-partition / per-snapshot values (``M05UpstreamEvidence`` is the exact
    M04+M04E+M05 gate bundle of one snapshot).
``method_mappings`` / ``method_servers`` / ``method_versions``
    The versioned method registry has **no persistence-backed**
    ``CapabilityMethodMappingReadPort`` in the tree
    (``m06_grants_gateway/method_mapping.py::DeclaredMappingReadPort`` says so
    explicitly), and the server/version tables are server-owned configuration.
    All three are stated by the host together: a mapping port with no
    server/version entry for its methods would deny every method, so the pair is
    validated as one decision rather than three independent ones.
``protocol``
    Optional by contract on ``ShadowGatewayPorts``.  Omitted, the adapter falls
    back to its documented exact-version comparison; supplied, that comparison is
    delegated to the port.  Absence is therefore *not* a blocker and is never
    replaced by a fabricated "supports everything" object.

Fail-closed rules
=================

* **Every missing port is a code, never an exception and never a substitute.**
  The builder answers a :class:`ShadowPortsPlan`; ``ports is None`` whenever
  anything required is absent.
* **A plan that says "complete" really constructs.**  The assembled value is
  passed through ``build_shadow_gateway`` before it is returned, so
  ``plan.complete`` and "the facade accepts this graph" cannot drift apart.
* **The facade stays shadow.**  ``DECISION_OWNER``/``CANONICAL_READ_OWNER`` are
  asserted to remain ``legacy`` and ``CANONICAL_POSITIVE_WRITES`` to stay off;
  a graph that violated either would be refused with
  :data:`BLOCKER_OWNERSHIP_CHANGED` instead of being returned.
* **Nothing runs.**  Every constructor here only stores a factory or validates
  scalars: no session is opened, no evidence row is read, no decision is taken.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from .composition import ShadowGatewayPorts, build_shadow_gateway

if TYPE_CHECKING:  # pragma: no cover - typing only
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

    from prodavan.application.canonical_grants.ports import (
        AuthenticatedCompanyPort,
        CanonicalGrantReadPort,
    )
    from prodavan.application.capability_method_mapping.ports import (
        CapabilityMethodMappingReadPort,
        ProtocolCompatibilityReadPort,
    )
    from prodavan.application.dynamic_app_plugin_gateway.ports import (
        CapabilityQuotaPort,
        CapabilityScopePort,
        DynamicPluginRepository,
    )
    from prodavan.application.migration.evidence_ports import (
        ReviewedM04RlsEvidence,
        SqlPartitionHandoffReadStore,
    )
    from prodavan.application.migration.m04_scope.scope_migration import ResolveScopePort
    from prodavan.application.migration.m04e_definition_version.repository import (
        ServerDefinitionVersionRepository,
    )
    from prodavan.application.migration.m05_instance_runtime.runtime_service import (
        FailClosedResolvePluginInstance,
        M05UpstreamEvidence,
    )
    from prodavan.application.migration.tenant_context import (
        RlsTenantScopeContextAdapter,
        RlsTrustedTenantContextAdapter,
    )

    from .adapter import EvidenceReadPort, TrustedEvidenceTransactionPort

    SessionFactory = async_sessionmaker[AsyncSession]

# ---------------------------------------------------------------------------
# Blocker vocabulary (stable codes: an operator greps for these, not for prose)
# ---------------------------------------------------------------------------

#: No session factory was supplied: no durable port of any kind can exist.
BLOCKER_SESSION_FACTORY_MISSING: Final = "m06_ports_session_factory_missing"

BLOCKER_COMPANY_UNAVAILABLE: Final = "m06_ports_company_unavailable"
BLOCKER_M04_SCOPE_UNAVAILABLE: Final = "m06_ports_m04_scope_unavailable"
BLOCKER_M04E_DEFINITION_VERSIONS_MISSING: Final = (
    "m06_ports_m04e_definition_versions_missing"
)
BLOCKER_M05_INSTANCES_UNAVAILABLE: Final = "m06_ports_m05_instances_unavailable"
BLOCKER_M05_UPSTREAM_MISSING: Final = "m06_ports_m05_upstream_missing"
BLOCKER_HANDOFF_PARTITIONS_MISSING: Final = "m06_ports_handoff_partitions_missing"
BLOCKER_LEGACY_REPOSITORY_UNAVAILABLE: Final = "m06_ports_legacy_repository_unavailable"
BLOCKER_LEGACY_SCOPE_UNAVAILABLE: Final = "m06_ports_legacy_scope_unavailable"
#: The quota port has no table behind it; re-used verbatim from the legacy module
#: so one code names one cause everywhere it is reported.
BLOCKER_LEGACY_QUOTA_UNAVAILABLE: Final = "m06_legacy_quota_table_missing"
BLOCKER_CANONICAL_GRANTS_UNAVAILABLE: Final = "m06_ports_canonical_grants_unavailable"
BLOCKER_METHOD_MAPPINGS_MISSING: Final = "m06_ports_method_mappings_missing"
BLOCKER_METHOD_CONFIGURATION_MISSING: Final = "m06_ports_method_configuration_missing"
BLOCKER_METHOD_CONFIGURATION_INCONSISTENT: Final = (
    "m06_ports_method_configuration_inconsistent"
)
BLOCKER_EVIDENCE_READ_UNAVAILABLE: Final = "m06_ports_evidence_read_unavailable"
BLOCKER_EVIDENCE_TRANSACTION_UNAVAILABLE: Final = "m06_ports_evidence_transaction_unavailable"
#: The assembled graph was refused by ``build_shadow_gateway`` itself.
BLOCKER_NOT_CONSTRUCTIBLE: Final = "m06_ports_graph_not_constructible"
#: The facade would no longer be a shadow (owner or write gate changed).
BLOCKER_OWNERSHIP_CHANGED: Final = "m06_ports_shadow_ownership_changed"
#: A partition was stated but its writer generation cannot form an evidence scope.
BLOCKER_PARTITION_INVALID: Final = "m06_ports_partition_invalid"

#: Field name -> the code reported when that field is absent.  One entry per
#: required field of :class:`ShadowGatewayPorts`, so a report can name the exact
#: hole instead of restating "the port set is incomplete".
FIELD_BLOCKERS: Final[tuple[tuple[str, str], ...]] = (
    ("company", BLOCKER_COMPANY_UNAVAILABLE),
    ("m04_scope", BLOCKER_M04_SCOPE_UNAVAILABLE),
    ("m04e_definition_versions", BLOCKER_M04E_DEFINITION_VERSIONS_MISSING),
    ("m05_instances", BLOCKER_M05_INSTANCES_UNAVAILABLE),
    ("m05_upstream", BLOCKER_M05_UPSTREAM_MISSING),
    ("handoff_partitions", BLOCKER_HANDOFF_PARTITIONS_MISSING),
    ("legacy_repository", BLOCKER_LEGACY_REPOSITORY_UNAVAILABLE),
    ("legacy_scope", BLOCKER_LEGACY_SCOPE_UNAVAILABLE),
    ("legacy_quota", BLOCKER_LEGACY_QUOTA_UNAVAILABLE),
    ("canonical_grants", BLOCKER_CANONICAL_GRANTS_UNAVAILABLE),
    ("method_mappings", BLOCKER_METHOD_MAPPINGS_MISSING),
    ("evidence_read", BLOCKER_EVIDENCE_READ_UNAVAILABLE),
    ("evidence_transaction", BLOCKER_EVIDENCE_TRANSACTION_UNAVAILABLE),
)

#: ``protocol`` is optional on ``ShadowGatewayPorts``: its absence is a documented
#: behaviour (exact protocol-version comparison), not a missing dependency.
OPTIONAL_FIELDS: Final[frozenset[str]] = frozenset({"protocol"})

#: Distinguishes "argument omitted, derive it" from an explicit ``None``, which means
#: "this host deliberately has no such port" and must produce a blocker.
_AUTO: Final[Any] = object()


# ---------------------------------------------------------------------------
# Plan
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ShadowPortsPlan:
    """The ports a host could state, plus every one it could not.

    ``ports`` is ``None`` unless *every* required port was stated, and a stated
    set has already been accepted by ``build_shadow_gateway`` — so
    :attr:`complete` means "this graph constructs", not "nothing looked missing".

    ``built`` names the ports really constructed (work done); ``blockers`` holds
    codes only, in discovery order.  Neither field carries an object, a DSN or an
    exception message, so a plan can be logged and serialised verbatim.
    """

    ports: ShadowGatewayPorts | None
    built: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()

    @property
    def complete(self) -> bool:
        """True only when the full port set was built and accepted."""

        return self.ports is not None and not self.blockers

    def blocker_for(self, code: str) -> str | None:
        for blocker in self.blockers:
            if blocker == code:
                return blocker
        return None

    def snapshot(self) -> dict[str, Any]:
        """JSON-safe diagnostics: names and codes only."""

        return {
            "complete": self.complete,
            "built": list(self.built),
            "blockers": list(self.blockers),
        }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dedupe(codes: list[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    ordered: list[str] = []
    for code in codes:
        if code and code not in seen:
            seen.add(code)
            ordered.append(code)
    return tuple(ordered)


def _partition_tenant(partition: object) -> tuple[str, str]:
    """``(tenant_id, app_definition_id)`` from a key or a plain pair.

    Accepts ``PartitionChainKey`` without importing ``partition_chain`` (that module
    pulls the whole chain assembly); anything else must be a two-element sequence of
    non-empty strings.  A malformed partition raises ``ValueError`` — the caller
    converts it into :data:`BLOCKER_PARTITION_INVALID`, because a partition that
    cannot be read is a stated input defect, not an exceptional state.
    """

    tenant_id = getattr(partition, "tenant_id", None)
    definition_id = getattr(partition, "app_definition_id", None)
    if isinstance(tenant_id, str) and isinstance(definition_id, str):
        pair = (tenant_id, definition_id)
    elif isinstance(partition, (tuple, list)) and len(partition) == 2:
        pair = (partition[0], partition[1])
    else:
        raise ValueError("a partition is (tenant_id, app_definition_id) or a partition key")
    for name, value in zip(("tenant_id", "app_definition_id"), pair):
        if not isinstance(value, str) or not value.strip() or value != value.strip():
            raise ValueError(f"partition {name} must be a non-empty unpadded string")
    return (pair[0], pair[1])


def _positive_generation(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= 1


def _method_pair(
    servers: object, versions: object
) -> tuple[dict[str, str], dict[str, str], str | None]:
    """Validate the method server/version configuration as **one** decision.

    ``ApprovedMethodOperationAdapter`` refuses a mapping without exactly one server
    and one tool version, so an empty or lopsided pair is refused here with its own
    code instead of surfacing later as "every method is unapproved" — which would
    look like a policy answer rather than the configuration hole it is.
    """

    if not isinstance(servers, dict) or not isinstance(versions, dict):
        return {}, {}, BLOCKER_METHOD_CONFIGURATION_MISSING
    if not servers and not versions:
        return {}, {}, BLOCKER_METHOD_CONFIGURATION_MISSING
    if set(servers) != set(versions):
        return {}, {}, BLOCKER_METHOD_CONFIGURATION_INCONSISTENT
    for mapping in (servers, versions):
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in mapping.items()
        ):
            return {}, {}, BLOCKER_METHOD_CONFIGURATION_INCONSISTENT
    return dict(servers), dict(versions), None


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_shadow_ports(
    *,
    session_factory: SessionFactory | None = None,
    partition: object | None = None,
    writer_generation: int | None = None,
    m04_tenant_context: RlsTenantScopeContextAdapter | None = None,
    m05_tenant_context: RlsTrustedTenantContextAdapter | None = None,
    m04e_definition_versions: ServerDefinitionVersionRepository | None = None,
    m05_upstream: M05UpstreamEvidence | None = None,
    handoff_partitions: SqlPartitionHandoffReadStore | None = None,
    reviewed_m04_rls: ReviewedM04RlsEvidence | None = None,
    legacy_ports: object | None = None,
    legacy_quota: CapabilityQuotaPort | None = None,
    method_mappings: CapabilityMethodMappingReadPort | None = None,
    method_servers: dict[str, str] | None = None,
    method_versions: dict[str, str] | None = None,
    protocol: ProtocolCompatibilityReadPort | None = None,
    company: AuthenticatedCompanyPort | None = _AUTO,
    canonical_grants: CanonicalGrantReadPort | None = _AUTO,
    evidence_read: EvidenceReadPort | None = _AUTO,
    evidence_transaction: TrustedEvidenceTransactionPort | None = _AUTO,
) -> ShadowPortsPlan:
    """Compose ``ShadowGatewayPorts`` from the adapters that implement them.

    Every keyword is either a *derivation input* or a *stated collaborator*; none is
    defaulted to a substitute.  The sentinel-valued keywords (``company``,
    ``canonical_grants``, ``evidence_read``, ``evidence_transaction``) are derived
    from ``session_factory`` when omitted, and an explicit ``None`` states absence
    and yields the field's own blocker.

    ``legacy_ports`` accepts the :class:`LegacyGatewayPorts` bundle the process
    already builds (``build_legacy_gateway_ports``), so repository and scope are
    taken from the one object a host composed rather than rebuilt independently.
    ``legacy_quota`` is separate on purpose: the bundle's quota is typed ``None``
    because no table can back it, and a host that *does* own a real quota port
    supplies it here.

    Returns a :class:`ShadowPortsPlan`; never raises for a missing port, never
    invents one, and never executes a port.
    """

    built: list[str] = []
    blockers: list[str] = []

    if session_factory is None:
        blockers.append(BLOCKER_SESSION_FACTORY_MISSING)

    # -- legacy owners ------------------------------------------------------
    legacy_repository: DynamicPluginRepository | None = None
    legacy_scope: CapabilityScopePort | None = None
    if legacy_ports is not None:
        legacy_repository = getattr(legacy_ports, "repository", None)
        legacy_scope = getattr(legacy_ports, "scope", None)
    if legacy_repository is None and session_factory is not None:
        from prodavan.infrastructure.persistence.dynamic_app_gateway import (
            SqlDynamicPluginRepository,
        )

        legacy_repository = SqlDynamicPluginRepository(session_factory=session_factory)
    if legacy_scope is None and session_factory is not None:
        from prodavan.infrastructure.persistence.dynamic_app_gateway import (
            SqlCapabilityScopePort,
        )

        legacy_scope = SqlCapabilityScopePort(session_factory=session_factory)

    # -- process-scope canonical ports -------------------------------------
    resolved_company = company
    if resolved_company is _AUTO:
        resolved_company = None
        if session_factory is not None:
            from prodavan.infrastructure.persistence.canonical_grants_read import (
                SqlAuthenticatedCompanyPort,
            )

            resolved_company = SqlAuthenticatedCompanyPort(session_factory=session_factory)

    resolved_grants = canonical_grants
    if resolved_grants is _AUTO:
        resolved_grants = None
        if session_factory is not None:
            from prodavan.infrastructure.persistence.canonical_grants_read import (
                SqlCanonicalGrantReadPort,
            )

            resolved_grants = SqlCanonicalGrantReadPort(session_factory=session_factory)

    resolved_evidence_read = evidence_read
    resolved_evidence_transaction = evidence_transaction
    if resolved_evidence_read is _AUTO or resolved_evidence_transaction is _AUTO:
        store = None
        if session_factory is not None:
            from .evidence_store import SqlCanonicalEvidenceStore

            store = SqlCanonicalEvidenceStore(session_factory=session_factory)
        if resolved_evidence_read is _AUTO:
            resolved_evidence_read = store
        if resolved_evidence_transaction is _AUTO:
            resolved_evidence_transaction = store

    # -- per-partition / per-host ports ------------------------------------
    resolved_protocol = protocol if protocol is not None else None

    # ``m04_scope``: the RLS-binder-backed resolver, derived only from a real
    # tenant-context port.  Without one the adapter refuses at construction
    # (``SqlAlchemyScopeBackfillAdapter`` raises), which is why it is derived
    # behind an explicit check rather than attempted and caught.
    m04_scope: ResolveScopePort | None = None
    if m04_tenant_context is not None and session_factory is not None:
        from prodavan.application.migration.m04_scope.scope_persistence import (
            SqlAlchemyScopeBackfillAdapter,
        )

        m04_scope = SqlAlchemyScopeBackfillAdapter(
            session_factory=session_factory, tenant_context=m04_tenant_context
        )

    m05_instances: FailClosedResolvePluginInstance | None = None
    if (
        m05_tenant_context is not None
        and session_factory is not None
        and m05_upstream is not None
    ):
        from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
            SqlAlchemyM05IdentityEvidence,
            SqlAlchemyM05UnitOfWork,
            SqlAlchemyRedactedAuditSink,
            build_sql_resolve_plugin_instance_port,
        )

        uow = SqlAlchemyM05UnitOfWork(
            session_factory=session_factory, tenant_context=m05_tenant_context
        )

        m05_instances = build_sql_resolve_plugin_instance_port(
            uow=uow,
            identity_evidence=SqlAlchemyM05IdentityEvidence(uow),
            audit=SqlAlchemyRedactedAuditSink(session_factory=session_factory),
            upstream=m05_upstream,
        )

    # ``handoff_partitions``: the one M06 collaborator that *is* per-partition.
    # ``SqlPartitionHandoffReadStore`` binds a ``TrustedEvidenceScope``, so it is
    # derived only when a partition and a positive writer generation were stated.
    resolved_handoff = handoff_partitions
    if resolved_handoff is None and session_factory is not None and partition is not None:
        tenant_id, _definition_id = "", ""
        try:
            tenant_id, _definition_id = _partition_tenant(partition)
        except ValueError:
            blockers.append(BLOCKER_PARTITION_INVALID)
        else:
            if not _positive_generation(writer_generation):
                blockers.append(BLOCKER_PARTITION_INVALID)
            else:
                from prodavan.application.migration.evidence_ports import (
                    SqlPartitionHandoffReadStore,
                    TrustedEvidenceScope,
                )

                resolved_handoff = SqlPartitionHandoffReadStore(
                    session_factory=session_factory,
                    scope=TrustedEvidenceScope(
                        tenant_id=tenant_id, writer_generation=writer_generation
                    ),
                    reviewed_m04_rls=reviewed_m04_rls,
                )

    servers, versions, method_code = _method_pair(method_servers, method_versions)
    if method_code is not None:
        blockers.append(method_code)

    # -- collect: one code per absent required field ------------------------
    values: dict[str, object] = {
        "company": resolved_company,
        "m04_scope": m04_scope,
        "m04e_definition_versions": m04e_definition_versions,
        "m05_instances": m05_instances,
        "m05_upstream": m05_upstream,
        "handoff_partitions": resolved_handoff,
        "legacy_repository": legacy_repository,
        "legacy_scope": legacy_scope,
        "legacy_quota": legacy_quota,
        "canonical_grants": resolved_grants,
        "method_mappings": method_mappings,
        "evidence_read": resolved_evidence_read,
        "evidence_transaction": resolved_evidence_transaction,
    }
    for field_name, code in FIELD_BLOCKERS:
        if values[field_name] is None:
            blockers.append(code)
        else:
            built.append(field_name)
    if resolved_protocol is not None:
        built.append("protocol")

    if blockers:
        return ShadowPortsPlan(ports=None, built=tuple(built), blockers=_dedupe(blockers))

    ports = ShadowGatewayPorts(
        company=resolved_company,  # type: ignore[arg-type]
        m04_scope=m04_scope,  # type: ignore[arg-type]
        m04e_definition_versions=m04e_definition_versions,  # type: ignore[arg-type]
        m05_instances=m05_instances,  # type: ignore[arg-type]
        m05_upstream=m05_upstream,  # type: ignore[arg-type]
        handoff_partitions=resolved_handoff,  # type: ignore[arg-type]
        legacy_repository=legacy_repository,  # type: ignore[arg-type]
        legacy_scope=legacy_scope,
        legacy_quota=legacy_quota,
        canonical_grants=resolved_grants,  # type: ignore[arg-type]
        method_mappings=method_mappings,  # type: ignore[arg-type]
        method_servers=servers,
        method_versions=versions,
        evidence_read=resolved_evidence_read,  # type: ignore[arg-type]
        evidence_transaction=resolved_evidence_transaction,  # type: ignore[arg-type]
        protocol=resolved_protocol,
    )

    # A plan that says "complete" must *be* constructible: run the reviewed
    # builder now, so "all ports stated" and "the facade accepts them" can never
    # drift apart.  Construction is pure (it stores ports and validates scalars).
    try:
        gateway = build_shadow_gateway(ports)
    except ValueError:
        return ShadowPortsPlan(
            ports=None,
            built=tuple(built),
            blockers=_dedupe([*blockers, BLOCKER_NOT_CONSTRUCTIBLE]),
        )

    if (
        gateway.DECISION_OWNER != "legacy"
        or gateway.CANONICAL_READ_OWNER != "legacy"
        or gateway.CANONICAL_POSITIVE_WRITES is not False
    ):
        # The shadow contract is structural, not advisory: a graph that changed an
        # owner or enabled positive writes is refused, not returned with a warning.
        return ShadowPortsPlan(
            ports=None,
            built=tuple(built),
            blockers=_dedupe([*blockers, BLOCKER_OWNERSHIP_CHANGED]),
        )

    return ShadowPortsPlan(ports=ports, built=tuple(built), blockers=())


__all__ = [
    "BLOCKER_CANONICAL_GRANTS_UNAVAILABLE",
    "BLOCKER_COMPANY_UNAVAILABLE",
    "BLOCKER_EVIDENCE_READ_UNAVAILABLE",
    "BLOCKER_EVIDENCE_TRANSACTION_UNAVAILABLE",
    "BLOCKER_HANDOFF_PARTITIONS_MISSING",
    "BLOCKER_LEGACY_QUOTA_UNAVAILABLE",
    "BLOCKER_LEGACY_REPOSITORY_UNAVAILABLE",
    "BLOCKER_LEGACY_SCOPE_UNAVAILABLE",
    "BLOCKER_M04E_DEFINITION_VERSIONS_MISSING",
    "BLOCKER_M04_SCOPE_UNAVAILABLE",
    "BLOCKER_M05_INSTANCES_UNAVAILABLE",
    "BLOCKER_M05_UPSTREAM_MISSING",
    "BLOCKER_METHOD_CONFIGURATION_INCONSISTENT",
    "BLOCKER_METHOD_CONFIGURATION_MISSING",
    "BLOCKER_METHOD_MAPPINGS_MISSING",
    "BLOCKER_NOT_CONSTRUCTIBLE",
    "BLOCKER_OWNERSHIP_CHANGED",
    "BLOCKER_PARTITION_INVALID",
    "BLOCKER_SESSION_FACTORY_MISSING",
    "FIELD_BLOCKERS",
    "OPTIONAL_FIELDS",
    "ShadowPortsPlan",
    "build_shadow_ports",
]
