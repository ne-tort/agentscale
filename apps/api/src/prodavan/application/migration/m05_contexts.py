"""Assemble the four M05 run contexts for **one** partition (task ``MIG-M05-CONTEXT-01``).

Why this module exists
======================

``ChainDependencies`` has four M05 fields that were permanently ``None``:

.. code-block:: text

    m05_tenant_context    TrustedTenantContextPort   -- built from a principal resolver
    m05_runtime_context   TrustedRuntimeContext      -- the instance owner's run identity
    m05_identity_sessions RuntimeSessionOwnershipPort -- prove subject/session ownership
    m05_upstream_evidence M05UpstreamEvidence        -- M04/M04E/M05 gates of one snapshot

The first one was already constructible (``tenant_context.RlsTrustedTenantContextAdapter``),
and the other three were reported as blockers by ``composition``/``partition_chain``.  This
module turns all four into **stated, validated values with one named blocker per missing
input**, so an operator either gets the objects or a code that says exactly what to supply.

It contains no new persistence and no new DDL: every class here either delegates to an
adapter that already exists (``RlsTrustedTenantContextAdapter``,
``SqlAlchemyM05UnitOfWork``, ``SqlAlchemyM05IdentityEvidence``) or is the implementation of a
protocol that had none (``RuntimeSessionOwnershipPort``).

The four builders, and what each one really needs
=================================================

``build_m05_tenant_context(*, principal_resolver, relations=None)``
    The RLS binder.  It refuses to write a GUC unless an auth-boundary lookup turns a
    correlation id into a sealed ``TrustedPrincipal`` (``tenant_context.py``), so the
    resolver is the only collaborator it needs -- and the only one it accepts.  A resolver
    that is not a callable ``resolve(correlation_id=...)`` yields
    ``m05_tenant_context_principal_resolver_missing``; no adapter is built around a hole.

``build_m05_runtime_context(*, tenant_id, subject_id, session_id, correlation_id, authenticated=True)``
    ``TrustedRuntimeContext`` is a five-field value whose every field must be *server
    resolved*: the M05 import proves that the authenticated subject and session own the
    exact instance being imported (``_resolve_instance_binding`` compares
    ``identity.tenant_id/subject_id/session_id`` with the context).  So this builder does not
    consult anything: it **validates** the values the host resolved and refuses a blank,
    padded or anonymous one with a code naming the field.  ``authenticated`` must be an
    explicit ``True``; ``"yes"`` or ``1`` is not an authentication decision.

``build_m05_identity_sessions(*, session_factory)``
    ``RuntimeSessionOwnershipPort`` had no implementation in this repository: the baseline
    has no auth-session relation carrying ``(tenant, subject, session)``.  ``auth_sessions``
    (revision ``2026110317``) is that relation for the *credential* half, so
    :class:`SqlAuthSessionOwnership` proves ownership against it and the live employee /
    membership rows.  The exact predicate is in that class; anything the relation cannot
    prove answers ``False`` (deny), never ``True``.

``build_m05_upstream_evidence(*, tenant_id, app_definition_id, ...)``
    ``M05UpstreamEvidence`` is three gate objects of one snapshot.  Two of them are
    *derivable* from stored evidence and are built here:

    * :func:`build_m04_scope_gate` reads one ``m04_scope_promotion_evidence`` row (the
      M04-02 accepted scope/RLS proof) and turns its own digest columns into
      ``M04ScopeGateEvidence``; a blocked row, a foreign partition or a missing digest column
      is a named blocker.
    * :func:`build_m05_runtime_gate` builds ``M05RuntimeGateEvidence`` from those two
      digests (``m04_scope_digest`` / ``m04e_export_digest`` must be *the same* digests M05
      cross-checks in ``ready_for_import``), a reviewed static manifest of the nine
      security-review digests (:class:`ReviewedM05RuntimeGate`) and the partition's measured
      instance counts (:class:`M05InstanceCounts`).

    The third -- ``M05EvidenceExport`` -- is **not** derivable: it carries the accepted
    M04E ledger rows the marker only counts, and fabricating them would fabricate the
    canonical digest M05 recomputes.  It is therefore an input, and its absence is
    ``m05_upstream_evidence_m04e_export_missing``.

Fail-closed rules honoured here
===============================

* **Absence is data.**  No builder raises for a missing collaborator; each returns
  ``(None, blockers)`` with stable codes an operator greps for, plus ``dependency`` /
  ``detail`` / ``needed`` text.
* **No fabricated evidence.**  Every digest in an assembled gate comes either from a stored
  row, from another gate of the same snapshot, or from the reviewed manifest.  Nothing is
  defaulted to a syntactically valid placeholder.
* **No widening.**  Every builder that receives a partition checks all its inputs against
  it; a gate of another tenant/definition is refused before the bundle exists.
* **Nothing runs.**  Building a context opens no session: ``SqlAuthSessionOwnership`` and
  the evidence readers are lazy, and ``build_partition_m05_contexts`` only constructs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.migration.evidence_ports import (
    backfill_checkpoint_digest,
    is_sha256,
)
from prodavan.application.migration.evidence_ports import digest as evidence_digest
from prodavan.application.migration.m04e_definition_version.backfill import (
    M05EvidenceExport,
)
from prodavan.application.migration.m05_instance_runtime.instance_persistence import (
    RuntimeSessionOwnershipPort,
    SqlAlchemyM05IdentityEvidence,
    SqlAlchemyM05UnitOfWork,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    M04ScopeGateEvidence,
    M05IdentityEvidencePort,
    M05RuntimeGateEvidence,
    M05UpstreamEvidence,
    TrustedRuntimeContext,
)
from prodavan.application.migration.principal_resolver import (
    BearerJwtCredentialSource,
)
from prodavan.application.migration.tenant_context import (
    M05_RUNTIME_RELATIONS,
    RlsTrustedTenantContextAdapter,
    TrustedPrincipalResolver,
)

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from prodavan.application.migration.partition_chain import PartitionChainKey

    SessionFactory = async_sessionmaker[AsyncSession]

__all__ = [
    "M05_INSTANCE_COUNTS_MISSING",
    "M05_M04_SCOPE_EVIDENCE_MISSING",
    "M05_M04_SCOPE_EVIDENCE_NOT_READY",
    "M05_M04_SCOPE_EVIDENCE_PARTITION_MISMATCH",
    "M05_RUNTIME_CONTEXT_FIELD_CODES",
    "M05_RUNTIME_GATE_REVIEW_MISSING",
    "M05_SESSION_OWNERSHIP_SESSION_FACTORY_MISSING",
    "M05_TENANT_CONTEXT_RESOLVER_MISSING",
    "M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING",
    "M05_UPSTREAM_EVIDENCE_M04_SCOPE_MISSING",
    "M05_UPSTREAM_EVIDENCE_M05_RUNTIME_MISSING",
    "M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH",
    "M05ContextBlocker",
    "M05ContextBuild",
    "M05ContextInputs",
    "M05InstanceCounts",
    "M05PartitionContexts",
    "ReviewedM05RuntimeGate",
    "SqlAuthSessionOwnership",
    "build_m04_scope_gate",
    "build_m05_identity_evidence",
    "build_m05_identity_sessions",
    "build_m05_runtime_context",
    "build_m05_runtime_gate",
    "build_m05_tenant_context",
    "build_m05_upstream_evidence",
    "build_partition_m05_contexts",
]

# ---------------------------------------------------------------------------
# Blocker vocabulary (stable codes; an operator greps for these, not for prose)
# ---------------------------------------------------------------------------

#: ``RlsTrustedTenantContextAdapter`` needs the auth-boundary lookup; nothing else can
#: turn a correlation id into a sealed ``TrustedPrincipal``.
M05_TENANT_CONTEXT_RESOLVER_MISSING: Final = "m05_tenant_context_principal_resolver_missing"

#: One code per required ``TrustedRuntimeContext`` field, so a report says which value the
#: host failed to resolve instead of "context missing".
M05_RUNTIME_CONTEXT_TENANT_MISSING: Final = "m05_runtime_context_tenant_missing"
M05_RUNTIME_CONTEXT_SUBJECT_MISSING: Final = "m05_runtime_context_subject_missing"
M05_RUNTIME_CONTEXT_SESSION_MISSING: Final = "m05_runtime_context_session_missing"
M05_RUNTIME_CONTEXT_CORRELATION_MISSING: Final = "m05_runtime_context_correlation_missing"
M05_RUNTIME_CONTEXT_NOT_AUTHENTICATED: Final = "m05_runtime_context_not_authenticated"

M05_RUNTIME_CONTEXT_FIELD_CODES: Final[Mapping[str, str]] = {
    "tenant_id": M05_RUNTIME_CONTEXT_TENANT_MISSING,
    "subject_id": M05_RUNTIME_CONTEXT_SUBJECT_MISSING,
    "session_id": M05_RUNTIME_CONTEXT_SESSION_MISSING,
    "correlation_id": M05_RUNTIME_CONTEXT_CORRELATION_MISSING,
}

#: ``RuntimeSessionOwnershipPort`` is proved against stored rows; without a factory there is
#: no store to ask, and an in-memory "yes" would be the assertion the port exists to replace.
M05_SESSION_OWNERSHIP_SESSION_FACTORY_MISSING: Final = (
    "m05_identity_sessions_session_factory_missing"
)
M05_IDENTITY_EVIDENCE_UNIT_OF_WORK_UNAVAILABLE: Final = (
    "m05_identity_evidence_unit_of_work_unavailable"
)

#: The three inputs of one ``M05UpstreamEvidence`` (see module notes for why the export is an
#: input rather than a derivation).
M05_UPSTREAM_EVIDENCE_M04_SCOPE_MISSING: Final = "m05_upstream_evidence_m04_scope_missing"
M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING: Final = "m05_upstream_evidence_m04e_export_missing"
M05_UPSTREAM_EVIDENCE_M05_RUNTIME_MISSING: Final = "m05_upstream_evidence_m05_runtime_missing"
M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH: Final = (
    "m05_upstream_evidence_partition_mismatch"
)

#: The nine M05 security-review digests and the measured counts are not stored anywhere: a
#: reviewed manifest and an observed inventory are the only truthful sources.
M05_RUNTIME_GATE_REVIEW_MISSING: Final = "m05_runtime_gate_review_missing"
M05_INSTANCE_COUNTS_MISSING: Final = "m05_instance_counts_missing"
M05_RUNTIME_GATE_MANIFEST_INVALID: Final = "m05_runtime_gate_review_manifest_invalid"

#: One stored M04-02 scope row is the only source of the M04 gate digests.
M05_M04_SCOPE_EVIDENCE_MISSING: Final = "m05_m04_scope_evidence_missing"
M05_M04_SCOPE_EVIDENCE_NOT_READY: Final = "m05_m04_scope_evidence_not_ready"
M05_M04_SCOPE_EVIDENCE_PARTITION_MISMATCH: Final = (
    "m05_m04_scope_evidence_partition_mismatch"
)


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class M05ContextBlocker:
    """One concrete reason a context could not be built.

    Same shape as ``partition_chain.PartitionChainBlocker`` (code / dependency / detail /
    needed) so the two reports can be printed and diffed side by side.
    """

    code: str
    dependency: str
    detail: str
    needed: str

    def __str__(self) -> str:  # pragma: no cover - diagnostics only
        return f"{self.code} ({self.dependency}): {self.detail}; needed: {self.needed}"


def _blocker(code: str, dependency: str, detail: str, needed: str) -> M05ContextBlocker:
    return M05ContextBlocker(
        code=code, dependency=dependency, detail=detail, needed=needed
    )


@dataclass(frozen=True, slots=True)
class M05ContextBuild:
    """``(value, blockers)`` -- a built context, or every reason there is none.

    ``__post_init__`` states the contract the builders honour: a value and a blocker for the
    same dependency are mutually exclusive, so a caller can never receive a substitute
    object next to the code that says it is missing.
    """

    value: object | None
    blockers: tuple[M05ContextBlocker, ...] = ()

    def __post_init__(self) -> None:
        if self.value is not None and self.blockers:
            raise ValueError("a built M05 context cannot also carry blockers")

    @property
    def built(self) -> bool:
        return self.value is not None

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(blocker.code for blocker in self.blockers)

    def __iter__(self):
        yield self.value
        yield self.blockers


# ---------------------------------------------------------------------------
# 1. M05 tenant context
# ---------------------------------------------------------------------------


def build_m05_tenant_context(
    *,
    principal_resolver: TrustedPrincipalResolver | None,
    relations: tuple[str, ...] = M05_RUNTIME_RELATIONS,
) -> M05ContextBuild:
    """``TrustedTenantContextPort`` over the reviewed RLS binder, or its absence.

    The adapter is the reviewed one (``tenant_context.py``): it applies the GUCs with
    ``set_config(..., true)`` inside the caller's transaction, reads them back, refuses a
    contradicted assertion and re-measures the policy manifest on every bind.  This builder
    only decides *whether* it may exist, because a binder without a server-side lookup can
    never be production-bound.

    ``relations`` defaults to :data:`tenant_context.M05_RUNTIME_RELATIONS` -- the same
    reviewed relation list the M05 stage reads and writes -- so no caller can widen the
    manifest by passing a shorter one.
    """

    if principal_resolver is None:
        return M05ContextBuild(
            value=None,
            blockers=(
                _blocker(
                    M05_TENANT_CONTEXT_RESOLVER_MISSING,
                    "m05_tenant_context",
                    "the RLS binder refuses to write a GUC unless an auth-boundary lookup "
                    "turns a correlation id into a sealed TrustedPrincipal, so an M05 "
                    "transaction would have no trusted tenant",
                    "a TrustedPrincipalResolver for this run (principal_resolver.py: "
                    "BearerJwtCredentialSource for a request, "
                    "build_durable_principal_resolver for a worker)",
                ),
            ),
        )
    if not callable(getattr(principal_resolver, "resolve", None)):
        return M05ContextBuild(
            value=None,
            blockers=(
                _blocker(
                    M05_TENANT_CONTEXT_RESOLVER_MISSING,
                    "m05_tenant_context",
                    f"{type(principal_resolver).__name__} exposes no resolve(correlation_id=...), "
                    "so it cannot answer the binder's principal lookup",
                    "an object with an async resolve(*, correlation_id) returning "
                    "TrustedPrincipal | None",
                ),
            ),
        )
    return M05ContextBuild(
        value=RlsTrustedTenantContextAdapter(
            principal_resolver=principal_resolver, relations=relations
        )
    )


def principal_source_for_request(*, correlation_id: str, token: str) -> BearerJwtCredentialSource:
    """Convenience re-export: the request-path credential source the builder expects.

    Kept as a named function so a host wires the request path through exactly one place
    (``BearerJwtCredentialSource``) and never through an ad-hoc resolver.
    """

    return BearerJwtCredentialSource(correlation_id=correlation_id, token=token)


# ---------------------------------------------------------------------------
# 2. M05 runtime context
# ---------------------------------------------------------------------------


def _identity_problem(name: str, value: object) -> str | None:
    """Why ``value`` cannot be a server-resolved identity field, or ``None``."""

    if value is None:
        return "absent"
    if not isinstance(value, str):
        return f"is a {type(value).__name__}, not a string"
    if not value.strip():
        return "blank"
    if value != value.strip():
        return "carries surrounding whitespace"
    return None


def build_m05_runtime_context(
    *,
    tenant_id: str | None,
    subject_id: str | None,
    session_id: str | None,
    correlation_id: str | None,
    authenticated: bool | None = None,
) -> M05ContextBuild:
    """Assemble the run's ``TrustedRuntimeContext`` from **server-resolved** values.

    The four identities are the ones the auth boundary resolved for this run (the principal
    of the request or of the stored ``auth_sessions`` row), never values read from a source
    row or a request body: ``_resolve_instance_binding`` compares them with the instance
    identity and refuses on any difference, so a fabricated context would attribute an
    instance to the wrong owner.

    Every field is validated on its own, and each failure has its own code, so the report
    says *which* value the host has to resolve.  ``authenticated`` must be an explicit
    ``True`` -- ``"yes"``, ``1`` and ``None`` are not authentication decisions.
    """

    blockers: list[M05ContextBlocker] = []
    for name, value in (
        ("tenant_id", tenant_id),
        ("subject_id", subject_id),
        ("session_id", session_id),
        ("correlation_id", correlation_id),
    ):
        problem = _identity_problem(name, value)
        if problem is None:
            continue
        blockers.append(
            _blocker(
                M05_RUNTIME_CONTEXT_FIELD_CODES[name],
                "m05_runtime_context",
                f"{name} {problem}, so the M05 import could not bind this run to an "
                "authenticated subject and session",
                f"the {name} the auth boundary resolved for this run (never a value taken "
                "from the source row or the request body)",
            )
        )
    if authenticated is not True:
        blockers.append(
            _blocker(
                M05_RUNTIME_CONTEXT_NOT_AUTHENTICATED,
                "m05_runtime_context",
                f"authenticated={authenticated!r} is not an explicit True, so the context "
                "would describe an anonymous run",
                "an explicit True, set only where the credential was verified",
            )
        )
    if blockers:
        return M05ContextBuild(value=None, blockers=tuple(blockers))
    return M05ContextBuild(
        value=TrustedRuntimeContext(
            resolved_tenant_id=tenant_id,
            subject_id=subject_id,
            session_id=session_id,
            authenticated=True,
            correlation_id=correlation_id,
        )
    )


# ---------------------------------------------------------------------------
# 3. RuntimeSessionOwnershipPort over durable auth sessions
# ---------------------------------------------------------------------------

#: Exact ownership proof of one ``(tenant, subject, session)`` triple.
#:
#: ``auth_sessions`` binds a *correlation id* to the verified OIDC ``sub``; ``employees``
#: turns that ``sub`` into the employee id the M05 identity carries; ``memberships`` proves
#: the employee really belongs to the tenant.  The predicate is the whole proof:
#:
#: * ``s.correlation_id = :session_id``           -- the session this run presents is a
#:   recorded auth session, not an arbitrary string;
#: * ``s.company_id IS NOT DISTINCT FROM :tenant_id`` -- the session selected exactly this
#:   tenant (a NULL selection matches only a NULL assertion, which the caller never makes);
#: * ``e.id = :subject_id``                       -- the resolved employee is the subject;
#: * ``m.company_id = :tenant_id``                -- and it is a member of that tenant;
#: * ``s.revoked_at IS NULL`` and ``s.expires_at > now()`` -- the session is live.
#:
#: ``LIMIT 2`` is deliberate: two matching rows mean the proof is ambiguous, which is a
#: denial, not a True.
_OWNERSHIP_PROOF_SQL: Final = (
    "SELECT s.correlation_id "
    "FROM auth_sessions AS s "
    "JOIN employees AS e ON e.keycloak_sub = s.subject_claim "
    "AND e.deleted_at IS NULL AND e.status = 'active' "
    "JOIN memberships AS m ON m.employee_id = e.id AND m.company_id = :tenant_id "
    "WHERE s.correlation_id = :session_id "
    "AND s.company_id IS NOT DISTINCT FROM :tenant_id "
    "AND e.id = :subject_id "
    "AND s.revoked_at IS NULL "
    "AND s.expires_at > now() "
    "LIMIT 2"
)


class SqlAuthSessionOwnership(RuntimeSessionOwnershipPort):
    """``RuntimeSessionOwnershipPort`` proved against ``auth_sessions`` and the identity rows.

    This closes the blocker "``RuntimeSessionOwnershipPort`` has no implementation": the
    relation exists (``auth_sessions``, revision ``2026110317``), it is written by the auth
    boundary only after the credential was verified, and it carries the tenant the session
    selected.

    Known limitation, stated rather than papered over: the M05 instance identity stores a
    legacy ``session_id``, and the only durable session-shaped record in this baseline is the
    auth-boundary correlation id.  A legacy session id that is **not** an issued correlation
    id therefore answers ``False`` and the import quarantines that row
    (``subject_session_binding_unproven``).  That is fail-closed: it refuses rows whose
    ownership cannot be proved instead of accepting them on the caller's word.

    A database failure **propagates**.  Returning ``False`` on an outage would report
    "session revoked" for a broken connection -- exactly the conflation
    ``principal_resolver`` rejects -- so the exception reaches the stage, which reports
    ``partition_stage_failed`` and commits nothing.
    """

    def __init__(self, *, session_factory: SessionFactory) -> None:
        if session_factory is None:
            raise ValueError("auth-session ownership needs an async session factory")
        self._session_factory = session_factory

    async def prove(self, *, tenant_id: str, subject_id: str, session_id: str) -> bool:
        if _identity_problem("tenant_id", tenant_id) is not None:
            return False
        if _identity_problem("subject_id", subject_id) is not None:
            return False
        if _identity_problem("session_id", session_id) is not None:
            return False
        async with self._session_factory() as session:
            async with session.begin():
                rows = (
                    await session.execute(
                        text(_OWNERSHIP_PROOF_SQL),
                        {
                            "tenant_id": tenant_id,
                            "subject_id": subject_id,
                            "session_id": session_id,
                        },
                    )
                ).all()
        if len(rows) != 1:
            # Zero rows: not proved.  Two: ambiguous, which is not proof either.
            return False
        return str(rows[0][0]) == session_id


def build_m05_identity_sessions(*, session_factory: SessionFactory | None) -> M05ContextBuild:
    """The durable ``RuntimeSessionOwnershipPort``, or the code that says why there is none."""

    if session_factory is None:
        return M05ContextBuild(
            value=None,
            blockers=(
                _blocker(
                    M05_SESSION_OWNERSHIP_SESSION_FACTORY_MISSING,
                    "m05_identity_sessions",
                    "subject/session ownership is proved by reading auth_sessions, employees "
                    "and memberships; without a session factory there is nothing to ask and "
                    "a returning-True stub would be the assertion the port replaces",
                    "an async_sessionmaker over the platform PostgreSQL database",
                ),
            ),
        )
    return M05ContextBuild(value=SqlAuthSessionOwnership(session_factory=session_factory))


def build_m05_identity_evidence(
    *,
    session_factory: SessionFactory | None,
    tenant_context: Any | None,
    sessions: RuntimeSessionOwnershipPort | None,
) -> M05ContextBuild:
    """The full ``M05IdentityEvidencePort``: M04/M04E scope proofs plus ownership.

    ``SqlAlchemyM05IdentityEvidence`` re-proves, per row, the installation scope, the
    reciprocal plugin-instance registry link, the definition/version/artifact identity and
    (through ``sessions``) the subject/session ownership.  It needs a *unit of work*, which
    needs the trusted tenant-context binder, so all three collaborators are mandatory and
    each absence is named.  A missing ``sessions`` is not accepted here even though the
    adapter tolerates ``None``: without it ``prove_subject_session`` always answers ``None``
    and every import would be quarantined, so the builder refuses instead of shipping a
    graph that can only fail.
    """

    blockers: list[M05ContextBlocker] = []
    if session_factory is None:
        blockers.append(
            _blocker(
                M05_SESSION_OWNERSHIP_SESSION_FACTORY_MISSING,
                "m05_identity_evidence",
                "the identity evidence adapter reads definition/version/registry rows, so it "
                "needs a session factory",
                "an async_sessionmaker over the platform PostgreSQL database",
            )
        )
    if tenant_context is None:
        blockers.append(
            _blocker(
                M05_IDENTITY_EVIDENCE_UNIT_OF_WORK_UNAVAILABLE,
                "m05_identity_evidence",
                "SqlAlchemyM05UnitOfWork refuses to open a transaction without a trusted "
                "tenant-context binder (there is no session-level SET fallback)",
                "the TrustedTenantContextPort built by build_m05_tenant_context",
            )
        )
    if sessions is None:
        blockers.append(
            _blocker(
                "m05_identity_evidence_session_ownership_missing",
                "m05_identity_evidence",
                "without a RuntimeSessionOwnershipPort prove_subject_session always answers "
                "None, so every candidate would be quarantined as unproven",
                "the RuntimeSessionOwnershipPort built by build_m05_identity_sessions",
            )
        )
    if blockers:
        return M05ContextBuild(value=None, blockers=tuple(blockers))
    uow = SqlAlchemyM05UnitOfWork(
        session_factory=session_factory, tenant_context=tenant_context
    )
    return M05ContextBuild(
        value=SqlAlchemyM05IdentityEvidence(uow, sessions=sessions)
    )


# ---------------------------------------------------------------------------
# 4. M05 upstream evidence
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ReviewedM05RuntimeGate:
    """The nine security-review digests of the M05 runtime gate, stated by deployment wiring.

    ``M05RuntimeGateEvidence`` attests facts that are not rows: the exact-instance key, the
    two FK directions, artifact validation, the RLS review, writer/runtime CAS review, the
    stale-worker denial and audit atomicity.  None of them is stored anywhere, so the only
    truthful source is a reviewed manifest supplied from outside -- exactly the shape
    ``ReviewedM04RlsEvidence`` already has for the M04 half.

    Every digest must be a real ``sha256:<64 hex>``; a manifest with a placeholder is
    refused at construction, so a "reviewed" gate can never be a formatted string.
    """

    review_id: str
    database_role: str
    exact_instance_key_digest: str
    registry_to_instance_fk_digest: str
    instance_to_registry_fk_digest: str
    artifact_validation_digest: str
    rls_review_digest: str
    writer_cas_review_digest: str
    runtime_cas_review_digest: str
    stale_worker_denial_digest: str
    audit_atomicity_digest: str

    def __post_init__(self) -> None:
        for name in ("review_id", "database_role"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"M05 runtime gate review requires a non-empty {name}")
        for name in (
            "exact_instance_key_digest",
            "registry_to_instance_fk_digest",
            "instance_to_registry_fk_digest",
            "artifact_validation_digest",
            "rls_review_digest",
            "writer_cas_review_digest",
            "runtime_cas_review_digest",
            "stale_worker_denial_digest",
            "audit_atomicity_digest",
        ):
            if not is_sha256(getattr(self, name)):
                raise ValueError(
                    f"M05 runtime gate review {name} must be a sha256 digest, "
                    f"not {getattr(self, name)!r}"
                )

    @property
    def digest(self) -> str:
        """Stable identity of this manifest, so a gate can cite which review it used."""

        return evidence_digest(
            {
                "review_id": self.review_id.strip(),
                "database_role": self.database_role.strip(),
                "exact_instance_key_digest": self.exact_instance_key_digest,
                "registry_to_instance_fk_digest": self.registry_to_instance_fk_digest,
                "instance_to_registry_fk_digest": self.instance_to_registry_fk_digest,
                "artifact_validation_digest": self.artifact_validation_digest,
                "rls_review_digest": self.rls_review_digest,
                "writer_cas_review_digest": self.writer_cas_review_digest,
                "runtime_cas_review_digest": self.runtime_cas_review_digest,
                "stale_worker_denial_digest": self.stale_worker_denial_digest,
                "audit_atomicity_digest": self.audit_atomicity_digest,
            }
        )


@dataclass(frozen=True, slots=True)
class M05InstanceCounts:
    """What the partition's instance inventory currently observes.

    ``M05RuntimeGateEvidence.ready`` is false unless both counts are zero, and they are
    *measurements* (of unresolved required instances and quarantined required instances), so
    they are supplied by whoever read the inventory -- never defaulted here.
    """

    unresolved_required_instances: int
    quarantined_required_instances: int = 0

    def __post_init__(self) -> None:
        for name in ("unresolved_required_instances", "quarantined_required_instances"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")


#: Columns :func:`build_m04_scope_gate` consumes from a stored M04-02 row.  Named here so a
#: test (or a reviewer) can check the reader against the model without reading this file.
M04_SCOPE_GATE_DIGEST_COLUMNS: Final[tuple[str, ...]] = (
    "source_snapshot_digest",
    "canonical_digest",
    "checkpoint_digest",
    "gate_evidence_digest",
    "rls_policy_digest",
)


def _row_value(row: object, name: str) -> object:
    """One column of a stored row, whether it is an ORM object or a mapping."""

    if isinstance(row, Mapping):
        return row.get(name)
    return getattr(row, name, None)


def build_m04_scope_gate(
    *,
    tenant_id: str,
    app_definition_id: str,
    row: object | None,
) -> M05ContextBuild:
    """``M04ScopeGateEvidence`` of one partition, read out of its stored M04-02 row.

    The row is the accepted ``m04_scope_promotion_evidence`` record (or an equivalent
    mapping).  Every digest in the gate is one of the row's own digest columns -- nothing is
    recomputed and nothing is invented:

    ============================  ==================================================
    gate field                    source
    ============================  ==================================================
    ``source_snapshot_digest``    ``row.source_snapshot_digest``
    ``canonical_digest``          ``row.canonical_digest`` (the MIG-M04-02 digest M05
                                  cites as its ``M04_scope`` gate)
    ``checkpoint_digest``         ``row.checkpoint_digest``
    ``parent_validation_digest``  ``row.gate_evidence_digest`` -- the digest of the
                                  redacted envelope that *is* the parent/actor proof
    ``rls_validation_digest``     ``row.rls_policy_digest``
    ``typed_constraint_digests``  ``(row.gate_evidence_digest,)``: the same envelope
                                  attests the typed scope shape, so it is cited once
    ``active_scope_count``        ``gate_evidence.parent.counts.active_registry_matches``
                                  (the proven count), never a literal 1
    ============================  ==================================================

    A row of another tenant/scope, an ``outcome`` that is not ``ready``, a malformed digest
    column or a missing active-scope count is a named blocker: a blocked M04 row records a
    refusal and must never be assembled into an import gate.
    """

    if row is None:
        return M05ContextBuild(
            value=None,
            blockers=(
                _blocker(
                    M05_M04_SCOPE_EVIDENCE_MISSING,
                    "m05_upstream_evidence.m04_scope",
                    f"no m04_scope_promotion_evidence row exists for tenant {tenant_id!r}: "
                    "M04-02 has not accepted this tenant's owner-registry scope",
                    "an accepted (outcome='ready') m04_scope_promotion_evidence row for the "
                    "partition's tenant scope, written by SqlM04ScopeEvidenceWriter",
                ),
            ),
        )

    blockers: list[M05ContextBlocker] = []
    row_tenant = _row_value(row, "tenant_id")
    if row_tenant != tenant_id:
        blockers.append(
            _blocker(
                M05_M04_SCOPE_EVIDENCE_PARTITION_MISMATCH,
                "m05_upstream_evidence.m04_scope",
                f"the stored M04-02 row belongs to tenant {row_tenant!r}, not {tenant_id!r}",
                "the M04-02 row of this exact partition",
            )
        )
    outcome = str(_row_value(row, "outcome") or "").strip().lower()
    if outcome != "ready":
        blockers.append(
            _blocker(
                M05_M04_SCOPE_EVIDENCE_NOT_READY,
                "m05_upstream_evidence.m04_scope",
                f"the stored M04-02 row reads outcome={outcome or '<unset>'!r}, not 'ready': "
                "a blocked/quarantined row is evidence of a refusal, never of acceptance",
                "a 'ready' M04-02 row for this partition at this writer generation",
            )
        )
    digests: dict[str, str] = {}
    envelope = _row_value(row, "gate_evidence")
    for column in M04_SCOPE_GATE_DIGEST_COLUMNS:
        value = _row_value(row, column)
        if not is_sha256(value):
            blockers.append(
                _blocker(
                    "m05_m04_scope_evidence_digest_invalid",
                    "m05_upstream_evidence.m04_scope",
                    f"{column} of the stored M04-02 row is not a sha256 digest "
                    f"({value!r}), so the gate could not cite real evidence",
                    f"a stored row whose {column} is a sha256 digest",
                )
            )
            continue
        digests[column] = str(value)

    active_scope_count = 0
    if isinstance(envelope, Mapping):
        parent = envelope.get("parent")
        counts = parent.get("counts") if isinstance(parent, Mapping) else None
        if isinstance(counts, Mapping):
            raw = counts.get("active_registry_matches")
            if isinstance(raw, int) and not isinstance(raw, bool) and raw > 0:
                active_scope_count = raw
    if active_scope_count <= 0:
        blockers.append(
            _blocker(
                "m05_m04_scope_evidence_active_scope_count_missing",
                "m05_upstream_evidence.m04_scope",
                "the stored M04-02 envelope proves no active registry match "
                "(gate_evidence.parent.counts.active_registry_matches), so the gate would "
                "claim an active scope nobody measured",
                "a stored row whose redacted envelope records at least one active scope",
            )
        )
    if blockers:
        return M05ContextBuild(value=None, blockers=tuple(blockers))

    parent_digest = digests["gate_evidence_digest"]
    return M05ContextBuild(
        value=M04ScopeGateEvidence(
            partition=(tenant_id, app_definition_id),
            source_snapshot_digest=digests["source_snapshot_digest"],
            canonical_digest=digests["canonical_digest"],
            checkpoint_digest=digests["checkpoint_digest"],
            # The redacted envelope digest attests the parent/actor proof; the typed scope
            # shape is part of the same envelope, so it is cited once and marked as such.
            parent_validation_digest=parent_digest,
            rls_validation_digest=digests["rls_policy_digest"],
            typed_constraint_digests=(parent_digest,),
            active_scope_count=active_scope_count,
        )
    )


def build_m05_runtime_gate(
    *,
    tenant_id: str,
    app_definition_id: str,
    m04_scope: M04ScopeGateEvidence | None,
    m04e_export: M05EvidenceExport | None,
    review: ReviewedM05RuntimeGate | None,
    counts: M05InstanceCounts | None,
) -> M05ContextBuild:
    """``M05RuntimeGateEvidence`` of the snapshot the import will read.

    The gate is *bound*, not copied: ``m04_scope_digest`` and ``m04e_export_digest`` are
    exactly the digests ``M05UpstreamEvidence.ready_for_import`` compares against the other
    two gates, and ``checkpoint_digest`` is the digest of the M04E checkpoint
    (``evidence_ports.backfill_checkpoint_digest``), so the runtime gate cannot describe a
    different snapshot than the export it was built from.

    ``source_snapshot_digest`` is the M04E export's own snapshot digest -- the snapshot M05
    imports -- and ``canonical_digest`` is a digest over the gate's own components, computed
    here rather than supplied, so two runs cannot disagree about what the gate says.
    """

    blockers: list[M05ContextBlocker] = []
    if m04_scope is None:
        blockers.append(
            _blocker(
                M05_UPSTREAM_EVIDENCE_M04_SCOPE_MISSING,
                "m05_upstream_evidence.m05_runtime",
                "the M05 runtime gate cites the M04 scope canonical digest; without the M04 "
                "gate it would have nothing real to cite",
                "M04ScopeGateEvidence of this partition (build_m04_scope_gate)",
            )
        )
    if m04e_export is None:
        blockers.append(
            _blocker(
                M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
                "m05_upstream_evidence.m05_runtime",
                "the M05 runtime gate cites the M04E export canonical digest and the export's "
                "checkpoint; without the export both would be invented",
                "the M04E M05EvidenceExport of this partition's snapshot",
            )
        )
    if review is None:
        blockers.append(
            _blocker(
                M05_RUNTIME_GATE_REVIEW_MISSING,
                "m05_upstream_evidence.m05_runtime",
                "nine security-review digests (exact instance key, both FK directions, "
                "artifact validation, RLS review, writer/runtime CAS, stale-worker denial, "
                "audit atomicity) are not stored anywhere, so a reviewed manifest is the "
                "only truthful source",
                "a ReviewedM05RuntimeGate supplied by deployment wiring",
            )
        )
    if counts is None:
        blockers.append(
            _blocker(
                M05_INSTANCE_COUNTS_MISSING,
                "m05_upstream_evidence.m05_runtime",
                "the gate's ready predicate needs the partition's measured unresolved and "
                "quarantined instance counts; defaulting them to zero would claim a clean "
                "inventory nobody observed",
                "an M05InstanceCounts read from this partition's inventory",
            )
        )
    if blockers:
        return M05ContextBuild(value=None, blockers=tuple(blockers))
    assert m04_scope is not None and m04e_export is not None
    assert review is not None and counts is not None

    for name, partition in (
        ("m04_scope", m04_scope.partition),
        ("m04e_export", (m04e_export.tenant_id, m04e_export.app_definition_id)),
    ):
        if tuple(partition) != (tenant_id, app_definition_id):
            return M05ContextBuild(
                value=None,
                blockers=(
                    _blocker(
                        M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH,
                        "m05_upstream_evidence.m05_runtime",
                        f"{name} belongs to {tuple(partition)!r}, not "
                        f"{(tenant_id, app_definition_id)!r}",
                        "gates of this exact partition only",
                    ),
                ),
            )

    try:
        checkpoint_digest = backfill_checkpoint_digest(m04e_export.checkpoint)
    except Exception as exc:  # noqa: BLE001 - an unusable checkpoint is a named refusal
        return M05ContextBuild(
            value=None,
            blockers=(
                _blocker(
                    "m05_runtime_gate_checkpoint_unusable",
                    "m05_upstream_evidence.m05_runtime",
                    f"the M04E export checkpoint could not be digested "
                    f"({type(exc).__name__})",
                    "an export whose checkpoint is a complete version-phase checkpoint",
                ),
            ),
        )

    components = {
        "partition": list((tenant_id, app_definition_id)),
        "source_snapshot_digest": m04e_export.source_snapshot_digest,
        "checkpoint_digest": checkpoint_digest,
        "m04_scope_digest": m04_scope.canonical_digest,
        "m04e_export_digest": m04e_export.canonical_digest,
        "review": review.digest,
        "unresolved": counts.unresolved_required_instances,
        "quarantined": counts.quarantined_required_instances,
    }
    return M05ContextBuild(
        value=M05RuntimeGateEvidence(
            partition=(tenant_id, app_definition_id),
            source_snapshot_digest=m04e_export.source_snapshot_digest,
            canonical_digest=evidence_digest(components),
            checkpoint_digest=checkpoint_digest,
            m04_scope_digest=m04_scope.canonical_digest,
            m04e_export_digest=m04e_export.canonical_digest,
            exact_instance_key_digest=review.exact_instance_key_digest,
            registry_to_instance_fk_digest=review.registry_to_instance_fk_digest,
            instance_to_registry_fk_digest=review.instance_to_registry_fk_digest,
            artifact_validation_digest=review.artifact_validation_digest,
            rls_review_digest=review.rls_review_digest,
            writer_cas_review_digest=review.writer_cas_review_digest,
            runtime_cas_review_digest=review.runtime_cas_review_digest,
            stale_worker_denial_digest=review.stale_worker_denial_digest,
            audit_atomicity_digest=review.audit_atomicity_digest,
            unresolved_required_instances=counts.unresolved_required_instances,
            quarantined_required_instances=counts.quarantined_required_instances,
        )
    )


def build_m05_upstream_evidence(
    *,
    tenant_id: str,
    app_definition_id: str,
    m04_scope: M04ScopeGateEvidence | None,
    m04e_export: M05EvidenceExport | None,
    m05_runtime: M05RuntimeGateEvidence | None = None,
    review: ReviewedM05RuntimeGate | None = None,
    counts: M05InstanceCounts | None = None,
) -> M05ContextBuild:
    """``M05UpstreamEvidence`` for one partition, or one code per missing gate.

    Three gates, one snapshot.  Supplying ``m05_runtime`` states the runtime gate directly
    (a host that already holds it); otherwise it is built from ``m04_scope`` +
    ``m04e_export`` + ``review`` + ``counts``, which is the derived path and the one a
    per-partition operator uses.

    The bundle refuses a mismatched partition rather than trusting the caller: all three
    gates must address ``(tenant_id, app_definition_id)``, because
    ``ready_for_import`` compares them against the partition it is asked about and a
    silently mixed bundle would answer for the wrong one.

    What this builder does **not** do: it never claims the gates are *accepted*.  Whether
    the snapshot may be imported is ``M05UpstreamEvidence.ready_for_import``'s answer, and it
    also honours the static release gate ``UPSTREAM_OWNER_SCOPE_PROOF_CONFIRMED``.  Building
    the bundle is what makes the M05 stage *wired*; readiness is a separate, later verdict.
    """

    blockers: list[M05ContextBlocker] = []
    if m04_scope is None:
        blockers.append(
            _blocker(
                M05_UPSTREAM_EVIDENCE_M04_SCOPE_MISSING,
                "m05_upstream_evidence.m04_scope",
                "the M04 scope gate of this snapshot is absent, so ready_for_import is False "
                "for every row and the import would refuse with "
                "upstream_or_m05_gate_incomplete",
                "M04ScopeGateEvidence of this partition (build_m04_scope_gate over the "
                "stored M04-02 row)",
            )
        )
    if m04e_export is None:
        blockers.append(
            _blocker(
                M05_UPSTREAM_EVIDENCE_M04E_EXPORT_MISSING,
                "m05_upstream_evidence.m04e_export",
                "the M04E export carries the accepted ledger rows, the handoff marker and "
                "the six handoff gates M05 re-checks; it is not reconstructible from a "
                "marker (which stores counts only), and inventing the rows would invent the "
                "canonical digest M05 recomputes",
                "the M04E M05EvidenceExport (with its MIG-M04E-02 marker) of this "
                "partition's snapshot",
            )
        )
    if m05_runtime is None:
        runtime_build = build_m05_runtime_gate(
            tenant_id=tenant_id,
            app_definition_id=app_definition_id,
            m04_scope=m04_scope,
            m04e_export=m04e_export,
            review=review,
            counts=counts,
        )
        m05_runtime = runtime_build.value  # type: ignore[assignment]
        blockers.extend(runtime_build.blockers)
    if blockers:
        return M05ContextBuild(value=None, blockers=tuple(blockers))
    assert m04_scope is not None and m04e_export is not None and m05_runtime is not None

    stated = {
        "m04_scope": m04_scope.partition,
        "m04e_export": (m04e_export.tenant_id, m04e_export.app_definition_id),
        "m05_runtime": m05_runtime.partition,
    }
    mismatched = {
        name: tuple(partition)
        for name, partition in stated.items()
        if tuple(partition) != (tenant_id, app_definition_id)
    }
    if mismatched:
        return M05ContextBuild(
            value=None,
            blockers=tuple(
                _blocker(
                    M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH,
                    f"m05_upstream_evidence.{name}",
                    f"{name} belongs to {partition!r}, not "
                    f"{(tenant_id, app_definition_id)!r}",
                    "gates of this exact partition only",
                )
                for name, partition in mismatched.items()
            ),
        )
    return M05ContextBuild(
        value=M05UpstreamEvidence(
            m04_scope=m04_scope, m04e_export=m04e_export, m05_runtime=m05_runtime
        )
    )


# ---------------------------------------------------------------------------
# Per-partition composition of all four
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class M05ContextInputs:
    """Every per-partition input the four M05 contexts need, stated once.

    Field groups, matching the four builders:

    * ``principal_resolver`` -> ``m05_tenant_context``;
    * ``tenant_id`` / ``subject_id`` / ``session_id`` / ``correlation_id`` /
      ``authenticated`` -> ``m05_runtime_context`` (the instance owner's server-resolved
      run identity);
    * ``session_factory`` -> ``m05_identity_sessions``;
    * ``m04_scope_row`` / ``m04e_export`` / ``review`` / ``instance_counts`` (or an explicit
      ``m05_runtime`` gate) -> ``m05_upstream_evidence``.

    Every field defaults to ``None`` -- absence -- and absence always becomes a named
    blocker, never a substitute.
    """

    principal_resolver: TrustedPrincipalResolver | None = None
    tenant_id: str | None = None
    subject_id: str | None = None
    session_id: str | None = None
    correlation_id: str | None = None
    #: ``None`` is *absence* like every other field: an omitted flag is not an authenticated
    #: run, so a host that forgets it gets ``m05_runtime_context_not_authenticated`` instead
    #: of an implicit ``True``.
    authenticated: bool | None = None
    session_factory: SessionFactory | None = None
    m04_scope_row: object | None = None
    m04e_export: M05EvidenceExport | None = None
    m05_runtime: M05RuntimeGateEvidence | None = None
    review: ReviewedM05RuntimeGate | None = None
    instance_counts: M05InstanceCounts | None = None


@dataclass(frozen=True, slots=True)
class M05PartitionContexts:
    """The four ``ChainDependencies`` M05 contexts of one partition, plus what is missing.

    ``built`` names what was really constructed (so a report shows work done, not only work
    missing); ``blockers`` carries one entry per dependency that stayed ``None``.
    """

    tenant_context: Any | None = None
    runtime_context: TrustedRuntimeContext | None = None
    identity_sessions: RuntimeSessionOwnershipPort | None = None
    upstream_evidence: M05UpstreamEvidence | None = None
    identity_evidence: M05IdentityEvidencePort | None = None
    built: tuple[str, ...] = ()
    blockers: tuple[M05ContextBlocker, ...] = ()

    @property
    def complete(self) -> bool:
        return not self.blockers

    @property
    def blocker_codes(self) -> tuple[str, ...]:
        return tuple(blocker.code for blocker in self.blockers)

    def __iter__(self):
        yield self.tenant_context
        yield self.runtime_context
        yield self.identity_sessions
        yield self.upstream_evidence


def build_partition_m05_contexts(
    *,
    key: PartitionChainKey,
    inputs: M05ContextInputs | None,
    session_factory: SessionFactory | None = None,
) -> M05PartitionContexts:
    """Build all four M05 contexts of **one** partition.

    ``key`` is the authority for the tenant: the runtime context and both upstream gates are
    checked against ``key.tenant_id``, so a host that resolved a different tenant gets
    ``m05_runtime_context_tenant...``/``...partition_mismatch`` instead of a context that
    belongs to somebody else.

    ``session_factory`` is accepted separately so the planner can pass the same factory it
    used for every other port; ``inputs.session_factory`` wins when both are given.
    """

    resolved = inputs or M05ContextInputs()
    factory = resolved.session_factory if resolved.session_factory is not None else session_factory
    blockers: list[M05ContextBlocker] = []
    built: list[str] = []

    tenant_context = build_m05_tenant_context(principal_resolver=resolved.principal_resolver)
    blockers.extend(tenant_context.blockers)
    if tenant_context.value is not None:
        built.append("m05_tenant_context")

    runtime_context = build_m05_runtime_context(
        tenant_id=resolved.tenant_id,
        subject_id=resolved.subject_id,
        session_id=resolved.session_id,
        correlation_id=resolved.correlation_id,
        authenticated=resolved.authenticated,
    )
    blockers.extend(runtime_context.blockers)
    context_value = runtime_context.value
    if context_value is not None and str(context_value.resolved_tenant_id) != key.tenant_id:
        blockers.append(
            _blocker(
                M05_UPSTREAM_EVIDENCE_PARTITION_MISMATCH,
                "m05_runtime_context",
                f"the runtime context resolves tenant {context_value.resolved_tenant_id!r}, "
                f"but this plan is for {key.tenant_id!r}",
                "the instance owner's runtime context for exactly this partition",
            )
        )
        context_value = None
    if context_value is not None:
        built.append("m05_runtime_context")

    sessions = build_m05_identity_sessions(session_factory=factory)
    blockers.extend(sessions.blockers)
    if sessions.value is not None:
        built.append("m05_identity_sessions")

    scope_gate = build_m04_scope_gate(
        tenant_id=key.tenant_id,
        app_definition_id=key.app_definition_id,
        row=resolved.m04_scope_row,
    )
    upstream = build_m05_upstream_evidence(
        tenant_id=key.tenant_id,
        app_definition_id=key.app_definition_id,
        m04_scope=scope_gate.value,  # type: ignore[arg-type]
        m04e_export=resolved.m04e_export,
        m05_runtime=resolved.m05_runtime,
        review=resolved.review,
        counts=resolved.instance_counts,
    )
    blockers.extend(scope_gate.blockers)
    # ``build_m05_upstream_evidence`` names an absent M04 gate as
    # ``m05_upstream_evidence_m04_scope_missing``.  When this builder already knows *why* the
    # gate is absent (no stored row, a blocked row, a foreign partition, a malformed digest),
    # that specific code is the useful one and the generic one is dropped -- one cause, one
    # entry, and never a report that says "a gate is missing" without saying which evidence
    # is missing.
    blockers.extend(
        blocker
        for blocker in upstream.blockers
        if not (
            scope_gate.blockers
            and blocker.code == M05_UPSTREAM_EVIDENCE_M04_SCOPE_MISSING
        )
    )
    if upstream.value is not None:
        built.append("m05_upstream_evidence")

    identity_evidence = build_m05_identity_evidence(
        session_factory=factory,
        tenant_context=tenant_context.value,
        sessions=sessions.value,
    )
    blockers.extend(identity_evidence.blockers)
    if identity_evidence.value is not None:
        built.append("m05_identity_evidence")

    return M05PartitionContexts(
        tenant_context=tenant_context.value,
        runtime_context=context_value,
        identity_sessions=sessions.value,
        upstream_evidence=upstream.value,
        identity_evidence=identity_evidence.value,
        built=tuple(built),
        blockers=tuple(blockers),
    )
