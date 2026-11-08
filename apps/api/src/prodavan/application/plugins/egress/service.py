"""Проверка egress-запроса (v4, §4.5, §6.3, §10, критерии 8/22).

``EgressService.check`` — единая точка принятия решения:

1. ``grant_id`` из claims -> ``GrantPolicyResolver`` -> ``EgressPolicy`` из БД
   (политика НЕ зависит от срока жизни JWT, критерий 22);
2. проверка исходного ``Host`` и правил (``match``);
3. **R-10**: проверка ФИНАЛЬНОГО resolved host после всех редиректов и
   DNS-rebinding — на КАЖДОМ хопе, а не только на первом;
4. квота ``max_egress_bytes``;
5. запись audit-события единой схемы (append failure = deny).

Риск R-10 (документируемое требование, §6.3 «проверка redirect и TLS на каждом
хопе», §13.1, §10 «DoS через egress»)
------------------------------------------------------------------
Атака: allowlist содержит ``hooks.slack.com``. Плагин стучится на разрешённый
хост, тот отвечает ``302 Location: http://169.254.169.254/latest/meta-data/``;
либо DNS разрешается в публичный IP при проверке, а к моменту коннекта
перепривязывается (DNS-rebinding) на ``127.0.0.1`` / ``10.0.0.5``. Если
проверять только первый ``Host``, прокси пропустит запрос к metadata/внутренней
сети. Поэтому:

* ТРЕБОВАНИЕ: контроллер/прокси ОБЯЗАН проверять хост, путь, метод и квоту на
  КАЖДОМ хопе (каждый редирект, каждый повторный DNS-резолв, каждый
  TLS-upgrade), а не один раз на входе. Список пройденных хостов — ``hops``.
* ТРЕБОВАНИЕ: финальный resolved host (фактический адрес upstream, к которому
  будет установлено соединение) проверяется отдельно и ЯВНО — ``resolved_host``.
  Он должен входить в allowlist И быть валидным публичным DNS-именем; IP-литералы,
  loopback, private, link-local и metadata отклоняются.
* ТРЕБОВАНИЕ: DNS-резолв идёт через отдельный egress-прокси, прямой выход
  плагина в интернет закрыт NetworkPolicy (§6.2), TLS/SNI/SAN проверяются на
  каждом хопе (см. ``generator.build_envoy_routes``).
* Ограничение этого модуля: он принимает решения по ПЕРЕДАННОМУ контроллером
  значению ``resolved_host``/``hops``. Фактический резолв, перехват редиректов и
  re-check на каждом TCP-соединении выполняет egress-proxy (задача 14 /
  Envoy-контроллер); ``EgressService`` не делает сетевых вызовов и НЕ подменяет
  эту проверку «доверием» к первому ``Host``. Отсутствие ``resolved_host`` при
  включённой проверке = отказ (fail-closed).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Mapping, Protocol, Sequence, runtime_checkable
from uuid import uuid4

from prodavan.application.plugins.egress.errors import EgressDenied, EgressPolicyError
from prodavan.application.plugins.egress.generator import EgressScope, build_audit_event
from prodavan.application.plugins.egress.policy import (
    EgressMode,
    EgressPolicy,
    is_host_allowed,
    match,
    normalize_host,
    normalize_path,
    validate_host,
)
from prodavan.application.plugins.egress.resolver import GrantPolicyResolver, GrantStorePort


class EgressDecisionCode(StrEnum):
    """Стабильные коды решения для аудита и problem+json."""

    ALLOWED = "allowed"
    DENIED = "denied"
    MODE_NONE = "mode_none"
    HOST_NOT_ALLOWED = "host_not_allowed"
    PATH_NOT_ALLOWED = "path_not_allowed"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    RESOLVED_HOST_DENIED = "resolved_host_denied"
    QUOTA_EXCEEDED = "quota_exceeded"
    GRANT_MISSING = "grant_missing"
    GRANT_REVOKED = "grant_revoked"
    POLICY_INVALID = "policy_invalid"
    AUDIT_FAILED = "audit_failed"


@dataclass(frozen=True, slots=True)
class EgressDecision:
    """Результат проверки egress-запроса."""

    allowed: bool
    decision: str  # "allow" | "deny"
    code: EgressDecisionCode
    status: int
    reason: str
    policy: EgressPolicy | None = None
    grant_id: str | None = None
    request_id: str = field(default_factory=lambda: str(uuid4()))
    occurred_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    audit_event: dict[str, Any] | None = None

    @property
    def event(self) -> dict[str, Any] | None:
        """Совместимый алиас для записанного audit-события."""
        return self.audit_event


@runtime_checkable
class AuditSinkPort(Protocol):
    """Append-only приёмник audit-событий (реализация — вне этого каталога)."""

    def record(self, event: Mapping[str, Any]) -> None: ...


@runtime_checkable
class HopInspectorPort(Protocol):
    """Порт контроллера egress-proxy: факты о КАЖДОМ хопе (R-10).

    Реализация (Envoy ext_authz / controller) обязана сообщить:
    * все хосты редиректов в порядке следования (КРОМЕ исходного: ``check`` сам
      добавляет исходный ``Host`` в цепочку);
    * финальный resolved host (фактический upstream);
    * признаки, что redirect и TLS проверены на каждом хопе.

    Отсутствие порта или неполные данные = отказ (fail-closed), потому что
    «проверить только первый Host» недостаточно.
    """

    def inspect(self, *, host: str, path: str, method: str) -> "HopFacts": ...


@dataclass(frozen=True, slots=True)
class HopFacts:
    """Факты о цепочке переходов, предоставленные прокси-контроллером."""

    hop_hosts: tuple[str, ...] = ()
    resolved_host: str | None = None
    redirects_checked: bool = False
    tls_verified: bool = False

    @property
    def complete(self) -> bool:
        """Достаточно ли данных, чтобы доказать проверку на каждом хопе."""
        return (
            self.resolved_host is not None
            and bool(self.resolved_host.strip())
            and self.redirects_checked
            and self.tls_verified
        )


def _claims_value(claims: Any, name: str, default: str = "") -> str:
    """Безопасное чтение строкового поля claims (без ``egress``/``exp``)."""
    raw: Any = None
    if isinstance(claims, Mapping):
        raw = claims.get(name)
    else:
        raw = getattr(claims, name, None)
    return raw.strip() if isinstance(raw, str) and raw.strip() else default


class EgressService:
    """Fail-closed проверка egress для плагинов и агентов.

    ``store`` обязателен: без адаптера ``CapabilityGrant`` сервис не создаётся
    (отсутствие интеграции = явная ошибка, §0 плана миграции). ``audit`` и
    ``hop_inspector`` — опциональные порты, но при их отсутствии запрос НЕ
    считается автоматически разрешённым: аудит пишется во встроенный in-memory
    sink (для unit-тестов), а hop-факты должны быть переданы явно в ``check``.
    """

    def __init__(
        self,
        *,
        store: GrantStorePort,
        audit: AuditSinkPort | None = None,
        hop_inspector: HopInspectorPort | None = None,
        scope: EgressScope | str = EgressScope.PLUGIN,
    ) -> None:
        if store is None:
            raise EgressDenied(
                "GrantStorePort is not configured; egress is fail-closed",
                reason="store_missing",
                status=503,
            )
        self._resolver = GrantPolicyResolver(store)
        self._audit = audit
        self._hop_inspector = hop_inspector
        parsed_scope = EgressScope(scope) if not isinstance(scope, EgressScope) else scope
        if parsed_scope not in (EgressScope.PLUGIN, EgressScope.AGENT):
            raise EgressPolicyError(f"unknown egress scope: {scope!r}", reason="scope_unknown")
        self._scope = parsed_scope
        self._events: list[dict[str, Any]] = []

    @property
    def events(self) -> tuple[dict[str, Any], ...]:
        """События встроенного sink (для тестов/диагностики, не для прода)."""
        return tuple(self._events)

    def check(
        self,
        claims: Any,
        host: str,
        path: str,
        method: str,
        bytes: int,
        *,
        resolved_host: str | None = None,
        hops: Sequence[str] | None = None,
        redirect_checked: bool | None = None,
        tls_verified: bool | None = None,
    ) -> EgressDecision:
        """Проверить запрос и вернуть ``EgressDecision`` (+ записать аудит).

        Решение НИКОГДА не «allow по умолчанию»: ``EgressDenied`` из резолвера
        превращается в ``EgressDecision(allowed=False)`` с кодом причины и
        событием аудита. Исключение не пробрасывается наружу как «неизвестно» —
        неизвестность зафиксирована как отказ.

        ``bytes`` — объём исходящего запроса; отрицательный/не-int = отказ.
        ``hops``/``resolved_host``/``redirect_checked``/``tls_verified`` — факты
        R-10 от прокси-контроллера. Если задан ``hop_inspector``, он вызывается
        на исходном запросе и его данные имеют приоритет.
        """
        request_id = str(uuid4())
        occurred_at = datetime.now(UTC)
        tenant = _claims_value(claims, "company_id", "unknown")
        subject = _claims_value(claims, "sub", "unknown")

        # Нормализуем (снимаем ``:port``/скобки IPv6) и СТРОГО валидируем вход
        # ДО ветвлений: ``validate_host`` отсекает ``user@``, IP-литералы,
        # private/metadata и прочие формы подмены Host, а ``normalize_path`` —
        # обход префикса через ``..``. Аудит получает канонические значения.
        try:
            safe_host = validate_host(normalize_host(host), allow_wildcard=False)
            safe_path = normalize_path(path)
        except EgressPolicyError as exc:
            return self._deny(
                request_id=request_id,
                occurred_at=occurred_at,
                host=str(host),
                path=str(path),
                method=str(method),
                bytes_count=self._safe_bytes(bytes),
                code=EgressDecisionCode.HOST_NOT_ALLOWED,
                reason=exc.reason,
                tenant=tenant,
                subject=subject,
            )

        safe_method = method.strip().upper() if isinstance(method, str) else ""
        bytes_count = self._safe_bytes(bytes)
        if not safe_method or bytes_count < 0:
            return self._deny(
                request_id=request_id,
                occurred_at=occurred_at,
                host=safe_host,
                path=safe_path,
                method=safe_method,
                bytes_count=max(bytes_count, 0),
                code=EgressDecisionCode.POLICY_INVALID,
                reason="invalid_request",
                tenant=tenant,
                subject=subject,
            )

        # --- 1. Политика ТОЛЬКО из grant в БД (не из токена, критерий 22).
        try:
            policy = self._resolver.resolve_claims(claims)
        except EgressDenied as exc:
            # Битая политика grant'а (в т.ч. ``full`` без approval) — это ошибка
            # конфигурации, а не «grant не найден»; оба варианта = отказ.
            if isinstance(exc, EgressPolicyError):
                code = EgressDecisionCode.POLICY_INVALID
            elif exc.reason == "grant_revoked":
                code = EgressDecisionCode.GRANT_REVOKED
            else:
                code = EgressDecisionCode.GRANT_MISSING
            return self._deny(
                request_id=request_id,
                occurred_at=occurred_at,
                host=safe_host,
                path=safe_path,
                method=safe_method,
                bytes_count=bytes_count,
                code=code,
                reason=exc.reason,
                tenant=tenant,
                subject=subject,
                status=exc.status,
            )

        # --- 2. none запрещает всё (§6.3).
        if policy.mode is EgressMode.NONE or not policy.allows_any:
            return self._deny(
                request_id=request_id,
                occurred_at=occurred_at,
                host=safe_host,
                path=safe_path,
                method=safe_method,
                bytes_count=bytes_count,
                code=EgressDecisionCode.MODE_NONE,
                reason="egress mode 'none' denies all requests",
                tenant=tenant,
                subject=subject,
                policy=policy,
            )

        # --- 3. Исходный Host + match (хост/путь/метод).
        if not any(match(rule, safe_host, safe_path, safe_method) for rule in policy.rules):
            return self._deny(
                request_id=request_id,
                occurred_at=occurred_at,
                host=safe_host,
                path=safe_path,
                method=safe_method,
                bytes_count=bytes_count,
                code=EgressDecisionCode.HOST_NOT_ALLOWED,
                reason="host/path/method does not match any CapabilityGrant rule",
                tenant=tenant,
                subject=subject,
                policy=policy,
            )

        # --- 4. R-10: КАЖДЫЙ хоп + финальный resolved host.
        facts = self._hop_facts(
            host=safe_host,
            path=safe_path,
            method=safe_method,
            resolved_host=resolved_host,
            hops=hops,
            redirect_checked=redirect_checked,
            tls_verified=tls_verified,
        )
        if not facts.complete:
            return self._deny(
                request_id=request_id,
                occurred_at=occurred_at,
                host=safe_host,
                path=safe_path,
                method=safe_method,
                bytes_count=bytes_count,
                code=EgressDecisionCode.RESOLVED_HOST_DENIED,
                reason="R-10: per-hop redirect/TLS checks or final resolved host are missing",
                tenant=tenant,
                subject=subject,
                policy=policy,
            )

        # Все хосты цепочки (включая редиректы) должны быть в allowlist и быть
        # валидными публичными DNS-именами. Проверяем не только первый Host.
        chain = tuple(facts.hop_hosts) + (safe_host, facts.resolved_host or "")
        for hop in chain:
            if not self._hop_allowed(policy, hop):
                return self._deny(
                    request_id=request_id,
                    occurred_at=occurred_at,
                    host=safe_host,
                    path=safe_path,
                    method=safe_method,
                    bytes_count=bytes_count,
                    code=EgressDecisionCode.RESOLVED_HOST_DENIED,
                    reason="R-10: final resolved host or redirect hop is not allowed",
                    tenant=tenant,
                    subject=subject,
                    policy=policy,
                )

        # --- 5. Квота байт (§4.4 max_egress_bytes, §6.3).
        if bytes_count > policy.max_egress_bytes:
            return self._deny(
                request_id=request_id,
                occurred_at=occurred_at,
                host=safe_host,
                path=safe_path,
                method=safe_method,
                bytes_count=bytes_count,
                code=EgressDecisionCode.QUOTA_EXCEEDED,
                reason="max_egress_bytes quota exceeded",
                tenant=tenant,
                subject=subject,
                policy=policy,
                status=429,
            )

        # --- 6. ALLOW + append-only аудит. Ошибка записи = deny (§4.5).
        return self._allow(
            request_id=request_id,
            occurred_at=occurred_at,
            host=safe_host,
            path=safe_path,
            method=safe_method,
            bytes_count=bytes_count,
            tenant=tenant,
            subject=subject,
            policy=policy,
        )

    # ------------------------------------------------------------------ R-10

    def _hop_facts(
        self,
        *,
        host: str,
        path: str,
        method: str,
        resolved_host: str | None,
        hops: Sequence[str] | None,
        redirect_checked: bool | None,
        tls_verified: bool | None,
    ) -> HopFacts:
        """Собрать факты о хопах: из порта или из явных аргументов.

        Явные аргументы нужны, чтобы контроллер мог передать результаты проверки
        КАЖДОГО редиректа без дополнительного вызова. Если задан порт, он
        вызывается и его неполный ответ НЕ дополняется «оптимистично».
        """
        if self._hop_inspector is not None:
            inspected = self._hop_inspector.inspect(host=host, path=path, method=method)
            if not isinstance(inspected, HopFacts):
                return HopFacts()  # битый контракт -> неполные факты -> отказ
            return inspected

        hop_hosts: list[str] = []
        for item in hops or ():
            if isinstance(item, str) and item.strip():
                hop_hosts.append(item.strip())
        return HopFacts(
            hop_hosts=tuple(hop_hosts),
            resolved_host=resolved_host,
            redirects_checked=redirect_checked is True,
            tls_verified=tls_verified is True,
        )

    @staticmethod
    def _hop_allowed(policy: EgressPolicy, hop: str) -> bool:
        """Проверить ОДИН хост: allowlist + валидный публичный DNS (SSRF).

        ``validate_host`` отклоняет IP-литералы, private/loopback/link-local,
        metadata, wildcard и внутренние имена — именно то, чем заканчиваются
        DNS-rebinding и редирект на ``169.254.169.254``.
        """
        try:
            candidate = normalize_host(hop)
            validate_host(candidate, allow_wildcard=False)
        except EgressPolicyError:
            return False
        return is_host_allowed(policy, candidate)

    # --------------------------------------------------------------- Решение

    def _deny(
        self,
        *,
        request_id: str,
        occurred_at: datetime,
        host: str,
        path: str,
        method: str,
        bytes_count: int,
        code: EgressDecisionCode,
        reason: str,
        tenant: str,
        subject: str,
        status: int = 403,
        policy: EgressPolicy | None = None,
    ) -> EgressDecision:
        # Даже если append-only приёмник недоступен, ОТКАЗ обязан быть выражен
        # решением, а не неперехваченным исключением: иначе вызывающий слой может
        # трактовать «упало» как «неизвестно». Событие строим best-effort.
        try:
            event = self._record(
                request_id=request_id,
                occurred_at=occurred_at,
                host=host,
                path=path,
                method=method,
                bytes_count=bytes_count,
                status=status,
                decision="deny",
                code=code,
                reason=reason,
                tenant=tenant,
                subject=subject,
                policy=policy,
            )
        except Exception:
            event = None
        return EgressDecision(
            allowed=False,
            decision="deny",
            code=code,
            status=status,
            reason=reason,
            policy=policy,
            grant_id=policy.grant_id if policy else None,
            request_id=request_id,
            occurred_at=occurred_at,
            audit_event=event,
        )

    def _allow(
        self,
        *,
        request_id: str,
        occurred_at: datetime,
        host: str,
        path: str,
        method: str,
        bytes_count: int,
        tenant: str,
        subject: str,
        policy: EgressPolicy,
    ) -> EgressDecision:
        try:
            event = self._record(
                request_id=request_id,
                occurred_at=occurred_at,
                host=host,
                path=path,
                method=method,
                bytes_count=bytes_count,
                status=200,
                decision="allow",
                code=EgressDecisionCode.ALLOWED,
                reason="matched CapabilityGrant egress rule",
                tenant=tenant,
                subject=subject,
                policy=policy,
            )
        except Exception:
            # §4.5: append failure для позитивной мутации = deny/system failure.
            # НЕ вызываем ``_deny`` (он снова попытается писать в тот же сломанный
            # sink), а возвращаем отказ напрямую: решение выражено явно.
            return EgressDecision(
                allowed=False,
                decision="deny",
                code=EgressDecisionCode.AUDIT_FAILED,
                status=503,
                reason="audit append failed; request is denied",
                policy=policy,
                grant_id=policy.grant_id,
                request_id=request_id,
                occurred_at=occurred_at,
                audit_event=None,
            )
        return EgressDecision(
            allowed=True,
            decision="allow",
            code=EgressDecisionCode.ALLOWED,
            status=200,
            reason="matched CapabilityGrant egress rule",
            policy=policy,
            grant_id=policy.grant_id,
            request_id=request_id,
            occurred_at=occurred_at,
            audit_event=event,
        )

    def _record(
        self,
        *,
        request_id: str,
        occurred_at: datetime,
        host: str,
        path: str,
        method: str,
        bytes_count: int,
        status: int,
        decision: str,
        code: EgressDecisionCode,
        reason: str,
        tenant: str,
        subject: str,
        policy: EgressPolicy | None,
    ) -> dict[str, Any]:
        """Построить И записать событие единой схемы (§4.5, §6.3)."""
        event = build_audit_event(
            scope=self._scope,
            host=host,
            path=path,
            method=method,
            status=status,
            # Отрицательный/не-int объём уже означает отказ; в аудит пишем 0,
            # чтобы схема события осталась валидной и отказ был зафиксирован.
            bytes=max(bytes_count, 0),
            decision=decision,
            tenant=tenant,
            subject=subject,
            occurred_at=occurred_at.isoformat(),
            grant_id=policy.grant_id if policy else None,
            request_id=request_id,
            code=code.value,
            reason=reason,
        )
        if self._audit is not None:
            self._audit.record(event)
        else:
            # Встроенный sink: аудит всё равно существует (тесты/диагностика).
            self._events.append(dict(event))
        return event

    @staticmethod
    def _safe_bytes(value: Any) -> int:
        """Нормализовать объём; не-int/отрицательный -> -1 (дальше отказ)."""
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return -1
        return value


__all__ = [
    "AuditSinkPort",
    "EgressDecision",
    "EgressDecisionCode",
    "EgressService",
    "HopFacts",
    "HopInspectorPort",
]
