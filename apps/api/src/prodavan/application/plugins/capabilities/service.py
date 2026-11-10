"""Asynchronous, fail-closed capability pipeline for plugin trust.

The declarative application gateway remains synchronous.  This module reuses its
additive async variant with real plugin capability names; there is no mapping to
ui.render, MCP, or other declarative capabilities.

Every invocation loads the authoritative grant by claims.grant_id, validates its
identity and lifetime, intersects database capabilities with token scopes, then
runs identity -> scope -> grant -> quota -> typed adapter -> append-only audit.
Quota limits come only from the bound durable grant; a typed pre-invoke meter
supplies all six units. A dry-run authorize performs policy checks without
metering or reserving quota.
"""
from __future__ import annotations

import inspect
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol
from uuid import uuid4

from prodavan.application.capability_gateway_service import (
    AuditEvent,
    CapabilityDecision,
    CapabilityGatewayService,
    CapabilityGrant,
    CapabilityRequest,
    DecisionCode,
    GatewayDecision,
    RequestContext,
)
from prodavan.application.plugins.capabilities.audit import (
    AuditDecision,
    AuditEventKind,
    AuditPort,
    PluginAuditEvent,
    invocation_event,
)
from prodavan.application.plugins.capabilities.contracts import (
    OPERATION_TO_CAPABILITY,
    PLUGIN_CAPABILITIES,
    CapabilityEnvelope,
    NetworkEgressRequest,
    PluginCapabilityRequest,
    TypedContractError,
    TypedResponse,
    empty_digest,
)
from prodavan.application.plugins.capabilities.ports import (
    CAPABILITY_DOTTED_OPERATIONS,
    CAPABILITY_OPERATIONS,
    OPERATION_REQUEST_TYPES,
    AdapterRegistry,
    GrantRevocationPort,
    PluginClaims,
    UnknownCapabilityError,
    dispatch,
)
from prodavan.application.plugins.capabilities.quotas import (
    PluginQuota,
    PluginQuotaKey,
    QuotaDecision,
    QuotaOp,
    QuotaPort,
    QuotaUsage,
    quota_key_from_claims,
)


async def _maybe_await(value: Any) -> Any:
    """Compatibility with synchronous unit fakes, not a production alias."""
    if inspect.isawaitable(value):
        return await value
    return value


def _attr(source: Any, name: str, default: Any = None) -> Any:
    if isinstance(source, dict):
        return source.get(name, default)
    return getattr(source, name, default)


def _aware(value: Any, field: str) -> datetime | None:
    if value is None:
        return None
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(field + " must be a timezone-aware datetime")
    return value.astimezone(UTC)


def _status(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "").strip().lower()


class PluginGatewayDecision(GatewayDecision):
    """Gateway decision plus typed adapter data and safe correlation fields."""

    data: TypedResponse | None = None
    request_id: str = ""
    correlation_id: str | None = None
    audit_event_id: str | None = None

    @property
    def http_status(self) -> int:
        if self.allowed:
            return 200
        return {
            DecisionCode.QUOTA_EXCEEDED: 429,
            DecisionCode.GRANT_REVOKED: 403,
            DecisionCode.GRANT_EXPIRED: 403,
            DecisionCode.IDENTITY_DENIED: 401,
            DecisionCode.ADAPTER_MISSING: 503,
            DecisionCode.ADAPTER_FAILED: 503,
            DecisionCode.OUTCOME_UNKNOWN: 503,
            DecisionCode.CAPABILITY_UNKNOWN: 503,
            DecisionCode.CAPABILITY_NOT_REQUESTED: 403,
            DecisionCode.INVALID_REQUEST: 422,
        }.get(self.code, 403)

    def __init__(
        self,
        decision: CapabilityDecision,
        code: DecisionCode,
        reason: str | None = None,
        grant_id: str | None = None,
        *,
        data: TypedResponse | None = None,
        request_id: str = "",
        correlation_id: str | None = None,
        audit_event_id: str | None = None,
    ) -> None:
        super().__init__(decision, code, reason, grant_id)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "audit_event_id", audit_event_id)


class EgressPolicyPort(Protocol):
    async def allows(self, *, grant_id: str, host: str, port: int, path: str) -> bool: ...


class GrantRecordStore(Protocol):
    """Authoritative grant lookup supplied by repositories.grant_store."""

    async def get_by_grant_id(self, *, grant_id: str) -> Any | None: ...


class _CoreIdentity:
    def __init__(self, claims: PluginClaims, envelope: CapabilityEnvelope) -> None:
        self._claims = claims
        self._envelope = envelope

    def allows(self, context: RequestContext) -> bool:
        return bool(
            context.company_id == self._envelope.company_id
            and context.subject_id == self._claims.sub
            and context.request_id == self._envelope.request_id
            and self._envelope.grant_id == self._claims.grant_id
            and self._envelope.artifact_digest == self._claims.artifact_digest
        )


class _CoreScope:
    def __init__(
        self,
        invocation: _Invocation,
        egress_policy: EgressPolicyPort | None,
    ) -> None:
        self._invocation = invocation
        self._egress_policy = egress_policy

    async def allows(self, request: CapabilityRequest) -> bool:
        invocation = self._invocation
        if request.capability != invocation.capability:
            return False
        if invocation.capability not in invocation.effective_capabilities:
            return False
        allowed_operations = (
            CAPABILITY_OPERATIONS.get(invocation.capability, frozenset())
            | CAPABILITY_DOTTED_OPERATIONS.get(invocation.capability, frozenset())
        )
        if invocation.operation not in allowed_operations:
            return False
        if invocation.capability != "network":
            return True
        if self._egress_policy is None or not isinstance(invocation.request, NetworkEgressRequest):
            return False
        return bool(await _maybe_await(self._egress_policy.allows(
            grant_id=invocation.claims.grant_id,
            host=invocation.request.host,
            port=invocation.request.port,
            path=invocation.request.path,
        )))


class _CoreQuota:
    def __init__(
        self,
        quota: QuotaPort,
        key: PluginQuotaKey,
        limits: PluginQuota | None,
        registry: AdapterRegistry,
        capability: str,
        request: PluginCapabilityRequest,
        adapter_present: bool,
    ) -> None:
        self._quota = quota
        self._key = key
        self._limits = limits
        self._registry = registry
        self._capability = capability
        self._request = request
        self._adapter_present = adapter_present
        self.decisions: list[QuotaDecision] = []

    async def _usage(self) -> QuotaUsage | None:
        if self._limits is None:
            return None
        if not self._adapter_present:
            # Нет side effect и result-dependent единиц; сохраняем существующий
            # ADAPTER_MISSING путь, но OPS остаётся учтённым ровно один раз.
            return QuotaUsage(
                ops=1, rows=0, bytes=0, keys=0, object_bytes=0, egress_bytes=0
            )
        try:
            adapter = self._registry.resolve(self._capability)
            meter = getattr(adapter, "quota_usage", None)
            if not callable(meter):
                return None
            usage = await _maybe_await(meter(self._request))
        except Exception:
            return None
        if not isinstance(usage, QuotaUsage) or usage.ops != 1:
            return None
        return usage

    async def allows(self, request: CapabilityRequest) -> bool:
        usage = await self._usage()
        if usage is None or self._limits is None:
            return False
        for op in QuotaOp:
            amount = usage.amount_for(op)
            if op is not QuotaOp.OPS and amount == 0:
                continue
            try:
                decision = await _maybe_await(
                    self._quota.enforce(self._key, op, amount, self._limits)
                )
            except Exception:
                return False
            if not isinstance(decision, QuotaDecision):
                return False
            self.decisions.append(decision)
            if not decision.allowed:
                return False
        return True


class _CoreAudit:
    def __init__(self, audit: AuditPort, envelope: CapabilityEnvelope, capability: str, operation: str) -> None:
        self._audit = audit
        self._envelope = envelope
        self._capability = capability
        self._operation = operation if "." in operation else capability + "." + operation
        self.last_event_id: str | None = None

    async def record(self, event: AuditEvent) -> None:
        built = PluginAuditEvent(
            event_kind=AuditEventKind.AUTHORIZATION,
            request_id=self._envelope.request_id,
            run_id=self._envelope.run_id,
            correlation_id=self._envelope.correlation_id,
            company_id=self._envelope.company_id,
            cabinet_id=self._envelope.cabinet_id,
            project_id=self._envelope.project_id,
            subject_id=self._envelope.subject_id,
            plugin_instance_id=self._envelope.plugin_instance_id,
            artifact_digest=self._envelope.artifact_digest,
            grant_id=event.grant_id,
            capability=self._capability,
            operation=self._operation,
            resource=event.resource,
            decision=event.decision,
            code=event.code,
            reason=event.reason,
            occurred_at=event.occurred_at,
        )
        await _maybe_await(self._audit.append(built))
        self.last_event_id = built.event_id


class _CoreAdapter:
    def __init__(
        self,
        registry: AdapterRegistry,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
    ) -> None:
        self._registry = registry
        self._capability = capability
        self._operation = operation
        self._request = request
        self.capability = capability

    async def invoke(self, request: CapabilityRequest) -> TypedResponse:
        adapter = self._registry.resolve(self._capability)
        return await dispatch(adapter, self._capability, self._operation, self._request)


@dataclass(frozen=True, slots=True)
class _Invocation:
    claims: PluginClaims
    capability: str
    operation: str
    request: PluginCapabilityRequest
    envelope: CapabilityEnvelope
    now: datetime
    quota_key: PluginQuotaKey
    grant: Any = None
    effective_capabilities: frozenset[str] = frozenset()
    quota_limits: PluginQuota | None = None


class _GrantRejected(Exception):
    def __init__(self, code: DecisionCode, reason: str) -> None:
        self.code = code
        self.reason = reason
        super().__init__(reason)


class PluginCapabilityService:
    """Canonical async plugin authorize/invoke pipeline.

    ``quota`` — ОБЯЗАТЕЛЬНЫЙ явный порт: production default ``InMemoryQuota``
    удалён, молчаливых process-local квот больше нет. Отсутствие аргумента или
    явный ``None`` = ``TypeError`` при construction, до любого invoke.
    ``InMemoryQuota`` допустим ТОЛЬКО как явно переданный test/dev constructor
    override. ``grant_store`` (durable источник grant'ов) и ``audit``
    (append-only sink) обязательны, как и раньше; ``revocation``/``egress_policy``
    опциональны и НЕ имеют in-memory подмен.
    """

    def __init__(
        self,
        *,
        registry: AdapterRegistry,
        grant_store: GrantRecordStore | None,
        quota: QuotaPort,
        audit: AuditPort,
        revocation: GrantRevocationPort | None = None,
        egress_policy: EgressPolicyPort | None = None,
    ) -> None:
        if quota is None:
            # Fail-closed: молчаливый process-local default InMemoryQuota удалён.
            raise TypeError(
                "PluginCapabilityService requires an explicit quota= (QuotaPort); "
                "no in-memory production default: InMemoryQuota is accepted only "
                "as an explicit test/dev override"
            )
        self._registry = registry
        self._grant_store = grant_store
        self._quota: QuotaPort = quota
        self._audit = audit
        self._revocation = revocation
        self._egress_policy = egress_policy

    async def invoke(
        self,
        *,
        claims: PluginClaims,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
        now: datetime | None = None,
    ) -> PluginGatewayDecision:
        checked_at = now or datetime.now(UTC)
        try:
            invocation = await self._authorized_invocation(
                claims, capability, operation, request, checked_at
            )
        except _GrantRejected as exc:
            partial = getattr(exc, "invocation", None)
            if partial is not None:
                return await self._deny(partial, exc.code, reason=exc.reason)
            return self._invalid(exc, capability, operation, checked_at)
        except (TypedContractError, UnknownCapabilityError, ValueError) as exc:
            return self._invalid(exc, capability, operation, checked_at)

        core_request, core_adapter, core_audit = self._build_core(invocation)
        adapter_present = invocation.capability in self._registry.registered
        gateway = self._gateway(invocation, core_adapter, core_audit, adapter_present)
        try:
            if adapter_present:
                decision, result = await gateway.invoke_async(core_request, now=checked_at)
            else:
                decision = await gateway.authorize_async(core_request, now=checked_at)
                result = None
        except Exception:
            return await self._deny(
                invocation, DecisionCode.ADAPTER_FAILED, reason="gateway_or_audit_boundary_failed"
            )

        if not decision.allowed:
            return self._from_core(invocation, decision, core_audit.last_event_id)
        if not adapter_present:
            return await self._adapter_missing(invocation, core_audit.last_event_id)
        if not isinstance(result, TypedResponse):
            return self._outcome_unknown(invocation, "adapter_returned_untyped_result")

        success = invocation_event(
            envelope=invocation.envelope,
            capability=invocation.capability,
            operation=invocation.capability + "." + invocation.operation,
            resource=core_request.resource,
            decision=AuditDecision.SUCCESS,
            code=DecisionCode.ALLOWED,
            grant_id=decision.grant_id,
            reason="adapter_invocation_succeeded",
            occurred_at=checked_at,
            linked_event_id=core_audit.last_event_id or "",
            result_digest=result.digest,
        )
        if not await self._emit(success):
            # The adapter already ran.  A denial must not imply that its side
            # effect was rolled back or never happened.
            return self._outcome_unknown(invocation, "outcome_unknown_audit_append_failed")
        return PluginGatewayDecision(
            decision=CapabilityDecision.ALLOW,
            code=DecisionCode.ALLOWED,
            grant_id=decision.grant_id,
            data=result,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=success.event_id,
        )

    async def authorize(
        self,
        *,
        claims: PluginClaims,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
        now: datetime | None = None,
    ) -> PluginGatewayDecision:
        """Dry-run policy decision; never reserves quota or invokes an adapter."""
        checked_at = now or datetime.now(UTC)
        try:
            invocation = await self._authorized_invocation(
                claims, capability, operation, request, checked_at
            )
        except _GrantRejected as exc:
            partial = getattr(exc, "invocation", None)
            if partial is not None:
                return await self._deny(partial, exc.code, reason=exc.reason)
            return self._invalid(exc, capability, operation, checked_at)
        except (TypedContractError, UnknownCapabilityError, ValueError) as exc:
            return self._invalid(exc, capability, operation, checked_at)
        core_request, core_adapter, core_audit = self._build_core(invocation)
        gateway = self._gateway(invocation, core_adapter, core_audit, False)
        try:
            decision = await gateway.authorize_async(
                core_request, now=checked_at, reserve_quota=False
            )
        except Exception:
            return await self._deny(
                invocation, DecisionCode.ADAPTER_FAILED, reason="gateway_boundary_failed"
            )
        return self._from_core(invocation, decision, core_audit.last_event_id)

    async def _authorized_invocation(
        self,
        claims: PluginClaims,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
        now: datetime,
    ) -> _Invocation:
        invocation = self._prepare(claims, capability, operation, request, now)
        try:
            invocation = await self._bind_database_grant(invocation)
            if not await self._grant_is_active(invocation, now):
                raise self._reject(
                    invocation, DecisionCode.GRANT_REVOKED, "grant_revoked_or_revocation_unavailable"
                )
            return invocation
        except _GrantRejected:
            raise
        except Exception:
            raise self._reject(
                invocation, DecisionCode.GRANT_MISSING, "grant_store_unavailable"
            ) from None

    @staticmethod
    def _reject(invocation: _Invocation, code: DecisionCode, reason: str) -> _GrantRejected:
        error = _GrantRejected(code, reason)
        object.__setattr__(error, "invocation", invocation)
        return error

    async def _bind_database_grant(self, invocation: _Invocation) -> _Invocation:
        if self._grant_store is None:
            raise self._reject(invocation, DecisionCode.GRANT_MISSING, "grant_store_not_configured")
        claims = invocation.claims
        record = await _maybe_await(self._grant_store.get_by_grant_id(grant_id=claims.grant_id))
        if record is None:
            raise self._reject(invocation, DecisionCode.GRANT_MISSING, "grant_not_found")

        expected = {
            "grant_id": claims.grant_id,
            "company_id": claims.company_id,
            "plugin_instance_id": claims.plugin_instance_id,
            "plugin_id": getattr(claims, "plugin_id", ""),
            "manifest_version": getattr(claims, "manifest_version", ""),
            "artifact_digest": claims.artifact_digest,
        }
        if any(_attr(record, name) != value for name, value in expected.items()):
            raise self._reject(invocation, DecisionCode.GRANT_MISSING, "grant_identity_mismatch")

        status = _status(_attr(record, "status"))
        revoked_at = _aware(_attr(record, "revoked_at"), "revoked_at")
        if status == "revoked" or revoked_at is not None:
            raise self._reject(invocation, DecisionCode.GRANT_REVOKED, "grant_revoked")
        if status != "active":
            raise self._reject(invocation, DecisionCode.GRANT_MISSING, "grant_not_active")

        issued_at = _aware(
            _attr(record, "issued_at", _attr(record, "created_at")), "issued_at"
        )
        if issued_at is not None and invocation.now < issued_at:
            raise self._reject(
                invocation, DecisionCode.GRANT_NOT_YET_ISSUED, "grant_not_yet_issued"
            )
        expires_at = _aware(_attr(record, "expires_at"), "expires_at")
        if expires_at is not None and invocation.now >= expires_at:
            raise self._reject(invocation, DecisionCode.GRANT_EXPIRED, "grant_expired")

        raw_db = _attr(record, "capabilities")
        raw_claims = claims.scopes
        if isinstance(raw_db, (str, bytes)) or isinstance(raw_claims, (str, bytes)):
            raise self._reject(invocation, DecisionCode.GRANT_MISSING, "grant_capabilities_invalid")
        try:
            db_capabilities = frozenset(str(item) for item in raw_db) & PLUGIN_CAPABILITIES
            claim_capabilities = frozenset(str(item) for item in raw_claims) & PLUGIN_CAPABILITIES
        except TypeError:
            raise self._reject(
                invocation, DecisionCode.GRANT_MISSING, "grant_capabilities_invalid"
            ) from None
        try:
            quota_limits = PluginQuota.from_grant(_attr(record, "quotas"))
        except (TypedContractError, TypeError, ValueError):
            raise self._reject(
                invocation, DecisionCode.GRANT_MISSING, "grant_quotas_invalid"
            ) from None

        effective = db_capabilities & claim_capabilities
        if invocation.capability not in effective:
            raise self._reject(
                invocation, DecisionCode.CAPABILITY_NOT_REQUESTED, "capability_not_granted"
            )
        return replace(
            invocation,
            grant=record,
            effective_capabilities=effective,
            quota_limits=quota_limits,
        )

    @staticmethod
    def _prepare(
        claims: PluginClaims,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
        now: datetime,
    ) -> _Invocation:
        if now.tzinfo is None or now.utcoffset() is None:
            raise TypedContractError("now must be timezone-aware")
        if not isinstance(capability, str) or capability not in PLUGIN_CAPABILITIES:
            raise UnknownCapabilityError(str(capability))
        dotted = operation if "." in operation else capability + "." + operation
        bare = dotted.split(".", 1)[-1]
        if OPERATION_TO_CAPABILITY.get(dotted) != capability:
            raise TypedContractError("operation does not belong to capability")
        expected_request = OPERATION_REQUEST_TYPES[dotted]
        if not isinstance(request, expected_request):
            raise TypedContractError(
                f"{dotted} requires {expected_request.__name__}"
            )

        required = (
            "sub", "company_id", "plugin_instance_id", "artifact_digest",
            "grant_id", "scopes", "plugin_id", "manifest_version", "exp",
        )
        for name in required:
            value = getattr(claims, name, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise TypedContractError("claims." + name + " is required")
        if not isinstance(claims.exp, int) or isinstance(claims.exp, bool):
            raise TypedContractError("claims.exp must be an integer")

        client = getattr(request, "envelope", None)
        envelope = CapabilityEnvelope(
            request_id=(getattr(client, "request_id", "") or str(uuid4())),
            correlation_id=getattr(client, "correlation_id", None),
            run_id=getattr(client, "run_id", None),
            company_id=claims.company_id,
            cabinet_id=getattr(claims, "cabinet_id", None),
            project_id=getattr(claims, "project_id", None),
            subject_id=claims.sub,
            plugin_instance_id=claims.plugin_instance_id,
            artifact_digest=claims.artifact_digest,
            grant_id=claims.grant_id,
        )
        typed_request = replace(request, envelope=envelope)  # type: ignore[type-var]
        key = quota_key_from_claims(
            company_id=claims.company_id,
            plugin_instance_id=claims.plugin_instance_id,
            artifact_digest=claims.artifact_digest,
        )
        return _Invocation(claims, capability, bare, typed_request, envelope, now, key)

    async def _grant_is_active(self, invocation: _Invocation, now: datetime) -> bool:
        if self._revocation is None:
            return False
        try:
            return bool(await _maybe_await(
                self._revocation.is_active(invocation.claims.grant_id, now=now)
            ))
        except Exception:
            return False

    @staticmethod
    def _resource(invocation: _Invocation) -> str:
        return "plugin/" + invocation.claims.plugin_instance_id + "/" + invocation.capability

    def _build_core(self, invocation: _Invocation) -> tuple[CapabilityRequest, _CoreAdapter, _CoreAudit]:
        record = invocation.grant
        context = RequestContext(
            app_id=str(_attr(record, "plugin_id")),
            manifest_version=str(_attr(record, "manifest_version")),
            company_id=invocation.envelope.company_id,
            subject_id=invocation.envelope.subject_id,
            project_id=invocation.envelope.project_id,
            cabinet_id=invocation.envelope.cabinet_id,
            request_id=invocation.envelope.request_id,
        )
        request = CapabilityRequest(
            context=context,
            capability=invocation.capability,
            operation=invocation.capability + "." + invocation.operation,
            resource=self._resource(invocation),
            domain=invocation.capability,
        )
        adapter = _CoreAdapter(
            self._registry, invocation.capability, invocation.operation, invocation.request
        )
        audit = _CoreAudit(
            self._audit, invocation.envelope, invocation.capability, invocation.operation
        )
        return request, adapter, audit

    def _build_grants(self, invocation: _Invocation) -> tuple[CapabilityGrant, ...]:
        record = invocation.grant
        expires_at = _aware(_attr(record, "expires_at"), "expires_at")
        token_expiry = datetime.fromtimestamp(invocation.claims.exp, UTC)
        effective_expiry = token_expiry if expires_at is None else min(expires_at, token_expiry)
        issued_at = _aware(_attr(record, "issued_at", _attr(record, "created_at")), "issued_at")
        return (
            CapabilityGrant(
                app_id=str(_attr(record, "plugin_id")),
                manifest_version=str(_attr(record, "manifest_version")),
                subject_id=invocation.envelope.subject_id,
                capability=invocation.capability,
                operations=frozenset({invocation.capability + "." + invocation.operation}),
                resources=frozenset({self._resource(invocation)}),
                domains=frozenset({invocation.capability}),
                issued_at=issued_at,
                expires_at=effective_expiry,
                revoked_at=_aware(_attr(record, "revoked_at"), "revoked_at"),
                grant_id=invocation.claims.grant_id,
            ),
        )

    def _gateway(
        self,
        invocation: _Invocation,
        adapter: _CoreAdapter,
        audit: _CoreAudit,
        adapter_present: bool,
    ) -> CapabilityGatewayService:
        return CapabilityGatewayService(
            requested_capabilities=tuple(sorted(invocation.effective_capabilities)),
            grants=self._build_grants(invocation),
            identity=_CoreIdentity(invocation.claims, invocation.envelope),
            scope=_CoreScope(invocation, self._egress_policy),
            quota=_CoreQuota(
                self._quota,
                invocation.quota_key,
                invocation.quota_limits,
                self._registry,
                invocation.capability,
                invocation.request,
                adapter_present,
            ),
            audit=audit,
            adapters={invocation.capability: adapter} if adapter_present else {},
            known_capabilities=PLUGIN_CAPABILITIES,
        )

    @staticmethod
    def _from_core(
        invocation: _Invocation, decision: GatewayDecision, audit_event_id: str | None
    ) -> PluginGatewayDecision:
        return PluginGatewayDecision(
            decision=decision.decision,
            code=decision.code,
            reason=decision.reason,
            grant_id=decision.grant_id,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=audit_event_id,
        )

    async def _emit(self, event: PluginAuditEvent) -> bool:
        try:
            await _maybe_await(self._audit.append(event))
            return True
        except Exception:
            return False

    async def _deny(
        self, invocation: _Invocation, code: DecisionCode, *, reason: str | None = None
    ) -> PluginGatewayDecision:
        event = PluginAuditEvent(
            event_kind=AuditEventKind.AUTHORIZATION,
            request_id=invocation.envelope.request_id,
            run_id=invocation.envelope.run_id,
            correlation_id=invocation.envelope.correlation_id,
            company_id=invocation.envelope.company_id,
            cabinet_id=invocation.envelope.cabinet_id,
            project_id=invocation.envelope.project_id,
            subject_id=invocation.envelope.subject_id,
            plugin_instance_id=invocation.envelope.plugin_instance_id,
            artifact_digest=invocation.envelope.artifact_digest,
            grant_id=invocation.claims.grant_id,
            capability=invocation.capability,
            operation=invocation.capability + "." + invocation.operation,
            resource=self._resource(invocation),
            decision=CapabilityDecision.DENY,
            code=code,
            reason=reason,
            occurred_at=invocation.now,
        )
        appended = await self._emit(event)
        return PluginGatewayDecision(
            decision=CapabilityDecision.DENY,
            code=code,
            reason=reason,
            grant_id=invocation.claims.grant_id,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=event.event_id if appended else None,
        )

    def _outcome_unknown(self, invocation: _Invocation, reason: str) -> PluginGatewayDecision:
        return PluginGatewayDecision(
            decision=CapabilityDecision.DENY,
            code=DecisionCode.OUTCOME_UNKNOWN,
            reason=reason,
            grant_id=invocation.claims.grant_id,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=None,
        )

    async def _adapter_missing(
        self, invocation: _Invocation, linked_event_id: str | None
    ) -> PluginGatewayDecision:
        event = invocation_event(
            envelope=invocation.envelope,
            capability=invocation.capability,
            operation=invocation.capability + "." + invocation.operation,
            resource=self._resource(invocation),
            decision=AuditDecision.ERROR,
            code=DecisionCode.ADAPTER_MISSING,
            grant_id=invocation.claims.grant_id,
            reason="adapter_missing",
            occurred_at=invocation.now,
            linked_event_id=linked_event_id or "",
            result_digest=empty_digest(),
        )
        appended = await self._emit(event)
        return PluginGatewayDecision(
            decision=CapabilityDecision.DENY,
            code=DecisionCode.ADAPTER_MISSING,
            reason="adapter_missing",
            grant_id=invocation.claims.grant_id,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=event.event_id if appended else None,
        )

    @staticmethod
    def _invalid(
        exc: Exception, capability: str, operation: str, now: datetime
    ) -> PluginGatewayDecision:
        code = (
            DecisionCode.CAPABILITY_UNKNOWN
            if isinstance(exc, UnknownCapabilityError)
            else DecisionCode.INVALID_REQUEST
        )
        return PluginGatewayDecision(
            decision=CapabilityDecision.DENY,
            code=code,
            reason=type(exc).__name__,
            request_id=str(uuid4()),
        )


__all__ = [
    "EgressPolicyPort",
    "GrantRecordStore",
    "PluginCapabilityService",
    "PluginGatewayDecision",
]
