"""Сервис capability-запросов плагинов (F2).

Это ОБЁРТКА над существующим ядром ``CapabilityGatewayService``: мы импортируем
и вызываем его ``authorize()`` / ``invoke()``, НЕ копируем логику проверок и НЕ
редактируем ``application/capability_gateway_service.py``.

Pipeline (§4.1-4.5, план миграции F2)::

    PluginClaims
      -> проверка grant_id (GrantRevocationPort задачи 02, на КАЖДОМ запросе)
      -> scope (IdentityPort/ScopePort ядра: scopes из claims)
      -> grant (CapabilityGrant ядра, проверяется ядром)
      -> quota (QuotaPort ядра -> наш per-instance QuotaPort)
      -> typed adapter (AdapterRegistry + dispatch, без generic dispatch)
      -> AuditEvent (ядро пишет AUTHORIZATION, мы дописываем INVOCATION отдельно)

---------------------------------------------------------------------------
ОСОЗНАННЫЙ АДАПТЕР: CAPABILITY_BRIDGE (согласовано с родителем)
---------------------------------------------------------------------------
Ядро ``authorize()`` жёстко проверяет ``request.capability in
DECLARATIVE_CAPABILITIES`` — frozenset имён declarative-app (``storage.kv``,
``storage.files``, ``platform.commands`` и т.д.). Плагиновые имена (``cache``,
``objects``, ...) в него не входят, поэтому прямой вызов дал бы
``CAPABILITY_UNKNOWN``. По правилу «ядро не редактируем, логику не копируем»
здесь введён детерминированный инъективный мост ``plugin capability ->
declarative capability``: во входное ``CapabilityRequest``/``CapabilityGrant``
ядра подаётся внутренний идентификатор из этого frozenset, а плагин и весь
внешний контракт оперируют ТОЛЬКО своими typed именами.

Это ровно «переиспользуем код и контракты, заменяем точки привязки» (§1, §13.3),
а не костыль: мост не подменяет и не монкипатчит ``DECLARATIVE_CAPABILITIES``,
не дублирует ни одной проверки и не создаёт второй gateway. Все решения
(identity -> scope -> grant -> quota -> adapter -> audit) принимает САМО ядро.

``db.direct`` через gateway НЕ проходит: БД плагина — прямой DSN к своей БД
компании, граница = привилегии PostgreSQL + отдельная БД + NetworkPolicy
(§2, §4.3, ADR-007). Имя отсутствует в мосте и в ``AdapterRegistry``.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Final, Protocol
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
    authorization_event,
    invocation_event,
)
from prodavan.application.plugins.capabilities.contracts import (
    DB_DIRECT,
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
    CAPABILITY_OPERATIONS,
    AdapterMissingError,
    AdapterRegistry,
    GrantRevocationPort,
    PluginClaims,
    UnknownCapabilityError,
    dispatch,
)
from prodavan.application.plugins.capabilities.quotas import (
    InMemoryQuota,
    PluginQuota,
    PluginQuotaKey,
    QuotaDecision,
    QuotaOp,
    QuotaPort,
    quota_key_from_claims,
)

# ---------------------------------------------------------------------------
# Мост имён: plugin capability -> внутренний идентификатор declarative-app.
# Инъективен, стабилен, покрывает ровно 9 адаптеров. «identity.read» ядра
# намеренно не используется. Комментарий выше фиксирует осознанность решения.
# ---------------------------------------------------------------------------

CAPABILITY_BRIDGE: Final[dict[str, str]] = {
    "cache": "storage.kv",
    "objects": "storage.files",
    "docs": "platform.queries",
    "events": "events.subscribe",
    "jobs": "platform.commands",
    "pods": "ui.render",
    "mcp": "ui.navigation",
    "secrets": "secrets.read",
    "network": "network.fetch",
}

BRIDGE_TO_PLUGIN: Final[dict[str, str]] = {value: key for key, value in CAPABILITY_BRIDGE.items()}

assert set(CAPABILITY_BRIDGE) == PLUGIN_CAPABILITIES
assert len(set(CAPABILITY_BRIDGE.values())) == len(CAPABILITY_BRIDGE)


# ---------------------------------------------------------------------------
# Результат
# ---------------------------------------------------------------------------


class PluginGatewayDecision(GatewayDecision):
    """Типизированный результат: наследник ``GatewayDecision`` ЯДРА + ``data``.

    Ядро НЕ редактируется. ``GatewayDecision`` — frozen slots dataclass с
    обязательными ``decision``/``code``; наследник добавляет только НОВЫЕ поля
    со значениями по умолчанию, поэтому сигнатура ядра остаётся совместимой
    (порядок positional-аргументов ``decision, code, reason, grant_id``
    сохраняется). Дополнительные ``@dataclass`` здесь не навешивается.

    ``.decision`` — ``CapabilityDecision`` (StrEnum, сравнивается со строками
    ``"allow"``/``"deny"``), ``.code`` — ``DecisionCode`` ядра, ``.data`` —
    типизированный Response адаптера (или ``None`` при отказе). Стык с задачей 04:
    ``invoke(...)`` возвращает объект с ``.decision`` и ``.data``; ``.code`` нужен
    для маппинга HTTP-статусов (429/403/503/422/401).
    """

    data: TypedResponse | None = None
    request_id: str = ""
    correlation_id: str | None = None
    audit_event_id: str | None = None

    @property
    def http_status(self) -> int:
        """Проекция кода ядра в HTTP-статус для задачи 04 (кр. 10: 429)."""
        if self.allowed:
            return 200
        return {
            DecisionCode.QUOTA_EXCEEDED: 429,
            DecisionCode.GRANT_REVOKED: 403,
            DecisionCode.GRANT_EXPIRED: 403,
            DecisionCode.IDENTITY_DENIED: 401,
            DecisionCode.ADAPTER_MISSING: 503,
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
        # Ядро остаётся неизменным: все четыре его positional-поля передаём
        # как есть, а новые плагиновые поля задаём keyword-only.
        super().__init__(decision, code, reason, grant_id)
        object.__setattr__(self, "data", data)
        object.__setattr__(self, "request_id", request_id)
        object.__setattr__(self, "correlation_id", correlation_id)
        object.__setattr__(self, "audit_event_id", audit_event_id)


# ---------------------------------------------------------------------------
# Дополнительные порты сервиса
# ---------------------------------------------------------------------------


class EgressPolicyPort(Protocol):
    """Разрешение egress из ``CapabilityGrant`` в БД, НЕ из JWT (§13.3, кр. 22).

    Реализация — задача F5/родитель. Токен несёт только ``grant_id``; список
    ``allowed_hosts`` живёт в grant'е и не пересобирается при истечении TTL.
    """

    def allows(self, *, grant_id: str, host: str, port: int, path: str) -> bool: ...


# ---------------------------------------------------------------------------
# Мосты к протоколам ядра (identity / scope / quota / audit / adapter)
# ---------------------------------------------------------------------------


class _CoreIdentity:
    """Проверка серверной идентичности claims; ядро вызывает ``allows(context)``."""

    def __init__(self, claims: PluginClaims, envelope: CapabilityEnvelope) -> None:
        self._claims = claims
        self._envelope = envelope

    def allows(self, context: RequestContext) -> bool:
        # Все обязательные поля уже проверены при построении envelope.
        return bool(
            context.company_id == self._envelope.company_id
            and context.subject_id == self._claims.sub
            and context.request_id == self._envelope.request_id
            and self._envelope.grant_id == self._claims.grant_id
            and self._envelope.artifact_digest == self._claims.artifact_digest
        )


class _CoreScope:
    """Scope: capability должен быть явно объявлен в ``claims.scopes``.

    Операция должна принадлежать capability; ``db.direct`` в scopes игнорируется
    (он не является gateway capability). Egress-политика НЕ читается из токена:
    для ``network`` требуется отдельный ``EgressPolicyPort`` из grant'а в БД.
    """

    def __init__(
        self,
        claims: PluginClaims,
        capability: str,
        operation: str,
        egress_policy: EgressPolicyPort | None,
        request: PluginCapabilityRequest,
    ) -> None:
        self._claims = claims
        self._capability = capability
        self._operation = operation
        self._egress_policy = egress_policy
        self._request = request

    def allows(self, request: CapabilityRequest) -> bool:
        scopes = frozenset(self._claims.scopes)
        if self._capability not in scopes:
            return False
        if self._operation not in CAPABILITY_OPERATIONS.get(self._capability, frozenset()):
            return False
        if request.capability != CAPABILITY_BRIDGE[self._capability]:
            return False
        if self._capability == "network":
            if self._egress_policy is None:
                # Fail-closed: без политики из grant'а egress запрещён.
                return False
            if not isinstance(self._request, NetworkEgressRequest):
                return False
            return self._egress_policy.allows(
                grant_id=self._claims.grant_id,
                host=self._request.host,
                port=self._request.port,
                path=self._request.path,
            )
        return True


class _CoreQuota:
    """Мост ``QuotaPort`` ядра (``allows(request) -> bool``) в наш ``enforce``.

    Ключ ВСЕГДА ``(company, plugin_instance, artifact_digest)`` — см. quotas.py.
    Счётчик операций + жёсткие лимиты байт там, где typed контракт объявляет
    размер ДО вызова адаптера.
    """

    def __init__(self, quota: QuotaPort, key: PluginQuotaKey, request: PluginCapabilityRequest) -> None:
        self._quota = quota
        self._key = key
        self._request = request
        self.decisions: list[QuotaDecision] = []

    def allows(self, request: CapabilityRequest) -> bool:
        ops: list[tuple[QuotaOp, int]] = [(QuotaOp.OPS, 1)]
        if hasattr(self._request, "value") and isinstance(getattr(self._request, "value"), str):
            ops.append((QuotaOp.BYTES, len(getattr(self._request, "value").encode("utf-8"))))
        if hasattr(self._request, "size_bytes"):
            ops.append((QuotaOp.OBJECT_BYTES, int(getattr(self._request, "size_bytes"))))
        allowed = True
        for op, amount in ops:
            decision = self._quota.enforce(self._key, op, amount)
            self.decisions.append(decision)
            if not decision.allowed:
                allowed = False
        return allowed


class _CoreAudit:
    """Мост ``AuditEvent`` ядра -> наш ``PluginAuditEvent`` (AUTHORIZATION).

    Ядро формирует ``AuditEvent`` на КАЖДОМ решении; мы расширяем его полной
    плагиновой идентичностью и пишем через append-only ``AuditPort``.
    """

    def __init__(self, audit: AuditPort, envelope: CapabilityEnvelope, capability: str, operation: str) -> None:
        self._audit = audit
        self._envelope = envelope
        self._capability = capability
        self._operation = operation
        self.last_event_id: str | None = None

    def record(self, event: AuditEvent) -> None:
        built = authorization_event(
            envelope=self._envelope,
            capability=self._capability,
            operation=self._operation,
            resource=event.resource,
            decision=event.decision,
            code=event.code,
            grant_id=event.grant_id,
            reason=event.reason,
            occurred_at=event.occurred_at,
        )
        self._audit.append(built)
        self.last_event_id = built.event_id


class _CoreAdapter:
    """Мост ``CapabilityAdapter`` ядра -> typed адаптер из ``AdapterRegistry``.

    Ядро вызывает ``invoke(CapabilityRequest)``; мы вызываем ``registry.resolve``
    и ``dispatch`` со СТРОГО типизированным Request. Никакого generic
    ``{"command": ..., "args": ...}``: операция из закрытого множества.
    """

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
        self.capability: str = CAPABILITY_BRIDGE[capability]

    def invoke(self, request: CapabilityRequest) -> TypedResponse:
        adapter = self._registry.resolve(self._capability)
        return dispatch(adapter, self._capability, self._operation, self._request)


# ---------------------------------------------------------------------------
# Сервис
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Invocation:
    """Внутренний неизменяемый контекст одного вызова (не покидает сервис)."""

    claims: PluginClaims
    capability: str
    operation: str
    request: PluginCapabilityRequest
    envelope: CapabilityEnvelope
    now: datetime
    quota_key: PluginQuotaKey


class PluginCapabilityService:
    """Fail-closed обёртка над ``CapabilityGatewayService`` для плагинов.

    Обязательные зависимости (``registry``, ``quota``, ``audit``,
    ``revocation``) задаются явно: отсутствие интеграции = отказ, а не
    молчаливый успех (правило §0.5 плана миграции). ``egress_policy``
    обязателен только для capability ``network`` (кр. 22).
    """

    def __init__(
        self,
        *,
        registry: AdapterRegistry,
        quota: QuotaPort | None = None,
        audit: AuditPort,
        revocation: GrantRevocationPort | None = None,
        egress_policy: EgressPolicyPort | None = None,
    ) -> None:
        self._registry = registry
        # InMemoryQuota — только как детерминированный дефолт для тестов/дева.
        # В проде ожидается Redis-реализация с тем же per-instance ключом.
        self._quota: QuotaPort = quota if quota is not None else InMemoryQuota(PluginQuota())
        self._audit = audit
        self._revocation = revocation
        self._egress_policy = egress_policy

    # -- публичный контракт (стык с задачей 04) -----------------------------

    def invoke(
        self,
        *,
        claims: PluginClaims,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
        now: datetime | None = None,
    ) -> PluginGatewayDecision:
        """Выполнить typed capability-запрос через ядро gateway.

        Возвращает ``PluginGatewayDecision`` (наследник ``GatewayDecision``
        ядра) с ``.decision``/``.code``/``.data``. Исключения штатного отказа
        не бросаются: deny кодируется в ``code`` для HTTP-маппинга задачи 04.
        """
        checked_at = now or datetime.now(UTC)
        try:
            invocation = self._prepare(claims, capability, operation, request, checked_at)
        except (TypedContractError, UnknownCapabilityError, ValueError) as exc:
            # Невалидный контракт/identity — отказ ДО ядра (fail-closed).
            return self._invalid(exc, capability, operation, checked_at)

        # 1) Проверка grant_id на КАЖДОМ запросе: отзыв = мгновенный deny.
        #    Отсутствие порта или любая его ошибка = НЕ активен (fail-closed).
        if not self._grant_is_active(invocation, checked_at):
            return self._deny(
                invocation,
                DecisionCode.GRANT_REVOKED,
                reason="grant_revoked_or_revocation_unavailable",
            )

        # 2) Явно: db.direct не проходит через gateway (БД = прямой DSN).
        #    Проверка дублирует ports.resolve, но даёт понятный deny, а не 500.
        if invocation.capability == DB_DIRECT:
            return self._deny(
                invocation,
                DecisionCode.CAPABILITY_UNKNOWN,
                reason="db.direct is a direct DSN, not a gateway capability",
            )

        core_request, core_adapter, core_audit = self._build_core(invocation)

        # Если адаптер не зарегистрирован — ядро всё равно выполняет полный
        # pipeline authorize (identity/scope/grant/quota + AUTHORIZATION audit),
        # а затем мы фиксируем отдельный факт ADAPTER_MISSING. Так «allow» не
        # превращается в «success» (§4.5).
        adapter_present = invocation.capability in self._registry.registered
        adapters_for_core = {core_adapter.capability: core_adapter} if adapter_present else {}

        gateway = CapabilityGatewayService(
            requested_capabilities=tuple(
                CAPABILITY_BRIDGE[name]
                for name in frozenset(invocation.claims.scopes)
                if name in CAPABILITY_BRIDGE
            ),
            grants=self._build_grants(invocation),
            identity=_CoreIdentity(invocation.claims, invocation.envelope),
            scope=_CoreScope(
                invocation.claims,
                invocation.capability,
                invocation.operation,
                self._egress_policy,
                invocation.request,
            ),
            quota=_CoreQuota(self._quota, invocation.quota_key, invocation.request),
            audit=core_audit,
            adapters=adapters_for_core,
        )

        try:
            if adapter_present:
                # Ядро: authorize() + invoke() + adapter + audit в одном вызове.
                decision, result = gateway.invoke(core_request, now=checked_at)
            else:
                # Ядро: только authorize() (без повторного списания квоты).
                decision = gateway.authorize(core_request, now=checked_at)
                result = None
        except Exception:  # audit append / adapter boundary failure -> system deny
            return self._deny(
                invocation,
                DecisionCode.ADAPTER_FAILED,
                reason="gateway_or_audit_boundary_failed",
            )

        if not decision.allowed:
            return PluginGatewayDecision(
                decision=decision.decision,
                code=decision.code,
                reason=decision.reason,
                grant_id=decision.grant_id,
                request_id=invocation.envelope.request_id,
                correlation_id=invocation.envelope.correlation_id,
                audit_event_id=core_audit.last_event_id,
            )

        if not adapter_present:
            return self._adapter_missing(invocation, core_audit.last_event_id)

        # 3) УСПЕХ ИСПОЛНЕНИЯ — ОТДЕЛЬНОЕ событие, не заменяет AUTHORIZATION.
        #    Append failure позитивной мутации = deny (§4.5), а не allow.
        if not isinstance(result, TypedResponse):
            return self._deny(
                invocation,
                DecisionCode.ADAPTER_FAILED,
                reason="adapter_returned_untyped_result",
            )
        success = invocation_event(
            envelope=invocation.envelope,
            capability=invocation.capability,
            operation=f"{invocation.capability}.{invocation.operation}",
            resource=core_request.resource,
            decision=AuditDecision.SUCCESS,
            code=DecisionCode.ALLOWED,
            grant_id=decision.grant_id,
            reason="adapter_invocation_succeeded",
            occurred_at=checked_at,
            linked_event_id=core_audit.last_event_id or "",
            result_digest=result.digest,
        )
        if not self._emit(success):
            return self._deny(
                invocation,
                DecisionCode.ADAPTER_FAILED,
                reason="audit_append_failed",
            )

        return PluginGatewayDecision(
            decision=CapabilityDecision.ALLOW,
            code=DecisionCode.ALLOWED,
            grant_id=decision.grant_id,
            data=result,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=success.event_id,
        )

    def authorize(
        self,
        *,
        claims: PluginClaims,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
        now: datetime | None = None,
    ) -> PluginGatewayDecision:
        """Только проверка решения (без вызова адаптера) — для тестов/диагностики."""
        checked_at = now or datetime.now(UTC)
        invocation = self._prepare(claims, capability, operation, request, checked_at)
        if not self._grant_is_active(invocation, checked_at):
            return self._deny(invocation, DecisionCode.GRANT_REVOKED, reason="grant_revoked")
        core_request, _adapter, core_audit = self._build_core(invocation)
        gateway = CapabilityGatewayService(
            requested_capabilities=(CAPABILITY_BRIDGE[invocation.capability],),
            grants=self._build_grants(invocation),
            identity=_CoreIdentity(invocation.claims, invocation.envelope),
            scope=_CoreScope(
                invocation.claims, invocation.capability, invocation.operation,
                self._egress_policy, invocation.request,
            ),
            quota=_CoreQuota(self._quota, invocation.quota_key, invocation.request),
            audit=core_audit,
        )
        try:
            decision = gateway.authorize(core_request, now=checked_at)
        except Exception:
            return self._deny(invocation, DecisionCode.ADAPTER_FAILED, reason="gateway_boundary_failed")
        return PluginGatewayDecision(
            decision=decision.decision,
            code=decision.code,
            reason=decision.reason,
            grant_id=decision.grant_id,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=core_audit.last_event_id,
        )

    # -- внутренние этапы ---------------------------------------------------

    @staticmethod
    def _prepare(
        claims: PluginClaims,
        capability: str,
        operation: str,
        request: PluginCapabilityRequest,
        now: datetime,
    ) -> _Invocation:
        """Построить СЕРВЕРНУЮ идентичность; подменить envelope в запросе.

        Плагин НЕ может задать company_id / subject_id / plugin_instance_id /
        artifact_digest / grant_id через тело: все эти поля берутся из
        проверенных ``PluginClaims``. Пересборка через ``dataclasses.replace``
        сохраняет тип запроса и повторно запускает его валидацию.
        """
        if now.tzinfo is None or now.utcoffset() is None:
            raise TypedContractError("now must be timezone-aware")
        if not isinstance(capability, str) or capability not in PLUGIN_CAPABILITIES:
            raise UnknownCapabilityError(str(capability))
        # КАНОНИЧЕСКИЙ ФОРМАТ НА ВХОДЕ — ТОЧЕЧНЫЙ (``cache.get``, §4.4,
        # contracts.OPERATION_TO_CAPABILITY); задача 04 передаёт именно его.
        # ВНУТРИ слоя используем голую часть (``get``) — это имя метода
        # адаптера в CAPABILITY_OPERATIONS / _CoreScope / dispatch. Выбран
        # вариант с нормализацией (не переименование публичного реестра):
        # один формат снаружи, один внутри, без расхождений.
        bare_operation = operation.split(".", 1)[-1]
        if OPERATION_TO_CAPABILITY.get(f"{capability}.{bare_operation}") != capability:
            # Отклоняем и ``cache.get`` для чужого capability, и ``get`` без
            # префикса, если он не описан в публичном реестре.
            if OPERATION_TO_CAPABILITY.get(operation) != capability:
                raise TypedContractError(
                    f"operation {operation!r} does not belong to {capability!r}"
                )
        if capability not in CAPABILITY_BRIDGE:
            raise UnknownCapabilityError(capability)

        required = ("sub", "company_id", "plugin_instance_id", "artifact_digest", "grant_id", "scopes")
        for name in required:
            value = getattr(claims, name, None)
            if value is None or (isinstance(value, str) and not value.strip()):
                raise TypedContractError(f"claims.{name} is required")

        client_envelope: CapabilityEnvelope | None = getattr(request, "envelope", None)
        envelope = CapabilityEnvelope(
            request_id=(getattr(client_envelope, "request_id", "") or str(uuid4())),
            correlation_id=getattr(client_envelope, "correlation_id", None),
            run_id=getattr(client_envelope, "run_id", None),
            company_id=claims.company_id,
            cabinet_id=getattr(claims, "cabinet_id", None),
            project_id=getattr(claims, "project_id", None),
            subject_id=claims.sub,
            plugin_instance_id=claims.plugin_instance_id,
            artifact_digest=claims.artifact_digest,
            grant_id=claims.grant_id,
        )
        typed_request = replace(request, envelope=envelope)  # type: ignore[type-var]
        quota_key = quota_key_from_claims(
            company_id=claims.company_id,
            plugin_instance_id=claims.plugin_instance_id,
            artifact_digest=claims.artifact_digest,
        )
        return _Invocation(
            claims=claims,
            capability=capability,
            operation=bare_operation,
            request=typed_request,
            envelope=envelope,
            now=now,
            quota_key=quota_key,
        )

    def _grant_is_active(self, invocation: _Invocation, now: datetime) -> bool:
        """Отзыв по ``grant_id``; отсутствие порта/ошибка = НЕ активен."""
        if self._revocation is None:
            return False
        try:
            return bool(self._revocation.is_active(invocation.claims.grant_id, now=now))
        except Exception:
            return False

    @staticmethod
    def _identity_strings(invocation: _Invocation) -> tuple[str, str]:
        """app_id / manifest_version для ``RequestContext`` ядра.

        Оба поля строятся ТОЛЬКО из серверной identity: semver берётся из
        claims, а при его отсутствии версией служит ``artifact_digest``
        (канонический идентификатор артефакта, всегда присутствует).
        """
        claims = invocation.claims
        app_id = getattr(claims, "plugin_id", None) or claims.plugin_instance_id
        version = getattr(claims, "manifest_version", None) or claims.artifact_digest
        return str(app_id), str(version)

    @staticmethod
    def _resource(invocation: _Invocation) -> str:
        """Канонический resource БЕЗ данных запроса (не ключ, не SQL, не PII)."""
        return f"plugin/{invocation.claims.plugin_instance_id}/{invocation.capability}"

    def _build_core(
        self, invocation: _Invocation
    ) -> tuple[CapabilityRequest, _CoreAdapter, _CoreAudit]:
        app_id, manifest_version = self._identity_strings(invocation)
        context = RequestContext(
            app_id=app_id,
            manifest_version=manifest_version,
            company_id=invocation.envelope.company_id,
            subject_id=invocation.envelope.subject_id,
            project_id=invocation.envelope.project_id,
            cabinet_id=invocation.envelope.cabinet_id,
            request_id=invocation.envelope.request_id,
        )
        core_request = CapabilityRequest(
            context=context,
            capability=CAPABILITY_BRIDGE[invocation.capability],
            operation=invocation.operation,
            resource=self._resource(invocation),
            domain=invocation.capability,
        )
        adapter = _CoreAdapter(
            self._registry, invocation.capability, invocation.operation, invocation.request
        )
        audit = _CoreAudit(
            self._audit, invocation.envelope, invocation.capability, invocation.operation
        )
        return core_request, adapter, audit

    def _build_grants(self, invocation: _Invocation) -> tuple[CapabilityGrant, ...]:
        """Грант ядра: проверку issued/expired/revoked выполняет САМО ядро.

        Мы не копируем ``CapabilityGrant.allows`` — лишь подаём в ядро точный
        грант с ``grant_id`` из claims. Egress/квоты в грант НЕ кладутся:
        они живут в БД (§13.3, кр. 22), а не в JWT.
        """
        app_id, manifest_version = self._identity_strings(invocation)
        exp = getattr(invocation.claims, "exp", None)
        expires_at = None
        if isinstance(exp, int) and not isinstance(exp, bool):
            expires_at = datetime.fromtimestamp(exp, UTC)
        return (
            CapabilityGrant(
                app_id=app_id,
                manifest_version=manifest_version,
                subject_id=invocation.envelope.subject_id,
                capability=CAPABILITY_BRIDGE[invocation.capability],
                operations=frozenset(CAPABILITY_OPERATIONS[invocation.capability]),
                resources=frozenset({self._resource(invocation)}),
                domains=frozenset({invocation.capability}),
                issued_at=invocation.now - timedelta(seconds=1),
                expires_at=expires_at,
                grant_id=invocation.claims.grant_id,
            ),
        )

    # -- отказы и аудит -----------------------------------------------------

    def _emit(self, event: PluginAuditEvent) -> bool:
        """Append-only запись; ``False`` = append failure (позитивная мутация deny)."""
        try:
            self._audit.append(event)
            return True
        except Exception:
            return False

    def _deny(
        self,
        invocation: _Invocation,
        code: DecisionCode,
        *,
        reason: str | None = None,
    ) -> PluginGatewayDecision:
        """Единый отказ + отдельное событие аудита (не «тихий» пропуск)."""
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
            operation=f"{invocation.capability}.{invocation.operation}",
            resource=self._resource(invocation),
            decision=CapabilityDecision.DENY,
            code=code,
            reason=reason,
            occurred_at=invocation.now,
        )
        self._emit(event)
        return PluginGatewayDecision(
            decision=CapabilityDecision.DENY,
            code=code,
            reason=reason,
            grant_id=invocation.claims.grant_id,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=event.event_id,
        )

    def _adapter_missing(
        self, invocation: _Invocation, linked_event_id: str | None
    ) -> PluginGatewayDecision:
        """Факт «авторизация прошла, адаптер отсутствует» — отдельное событие."""
        event = invocation_event(
            envelope=invocation.envelope,
            capability=invocation.capability,
            operation=f"{invocation.capability}.{invocation.operation}",
            resource=self._resource(invocation),
            decision=AuditDecision.ERROR,
            code=DecisionCode.ADAPTER_MISSING,
            grant_id=invocation.claims.grant_id,
            reason="adapter_missing",
            occurred_at=invocation.now,
            linked_event_id=linked_event_id or "",
            result_digest=empty_digest(),
        )
        self._emit(event)
        return PluginGatewayDecision(
            decision=CapabilityDecision.DENY,
            code=DecisionCode.ADAPTER_MISSING,
            reason="adapter_missing",
            grant_id=invocation.claims.grant_id,
            request_id=invocation.envelope.request_id,
            correlation_id=invocation.envelope.correlation_id,
            audit_event_id=event.event_id,
        )

    @staticmethod
    def _invalid(
        exc: Exception, capability: str, operation: str, now: datetime
    ) -> PluginGatewayDecision:
        """Отказ до построения identity: нет достоверного контура, аудит без PII."""
        code = DecisionCode.CAPABILITY_UNKNOWN if isinstance(exc, UnknownCapabilityError) else DecisionCode.INVALID_REQUEST
        return PluginGatewayDecision(
            decision=CapabilityDecision.DENY,
            code=code,
            reason=type(exc).__name__,
            request_id=str(uuid4()),
            correlation_id=None,
            audit_event_id=None,
        )


__all__ = [
    "BRIDGE_TO_PLUGIN",
    "CAPABILITY_BRIDGE",
    "EgressPolicyPort",
    "PluginCapabilityService",
    "PluginGatewayDecision",
]
