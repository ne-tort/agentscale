"""Тестовые двойники портов Plugin Gateway (F6).

Тесты НЕ запускаются в рамках миграции (верификационный freeze), но полностью
самодостаточны: ни БД, ни Redis, ни k3s, ни сеть. Все порты — in-memory фейки,
композиция повторяет то, что сделает родитель в F11.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from prodavan.api.v1.plugin_gateway import (
    PluginGatewayRuntime,
    install_plugin_gateway_runtime,
    router,
)
from prodavan.application.plugins.gateway.auth import (
    GatewayAuth,
    GatewayLimits,
    Sha256HmacVerifier,
)
from prodavan.application.plugins.gateway.ports import (
    BackendRequest,
    BackendResponse,
    DeliveryStatus,
    DeliveryVerdict,
    GatewayAuditEvent,
    GatewayGrant,
    GatewayOperation,
    GatewaySession,
    StaticResponsePolicy,
    WakeLease,
)
from prodavan.application.plugins.gateway.proxy import GatewayProxy

WEBHOOK_SECRET = "unit-test-webhook-secret-never-in-db"
ARTIFACT_DIGEST = "sha256:" + "a" * 64
SHELL_ORIGIN = "https://agentscale.example.test"
_USE_GRANT_DIGEST_AS_PUBLISHED = object()


class FakeSessionPort:
    """Разрешает сессию только при наличии валидного маркера."""

    def __init__(
        self,
        *,
        valid_tokens: set[str] | None = None,
        memberships: dict[str, set[str]] | None = None,
    ) -> None:
        self.valid_tokens = valid_tokens or {"good-session"}
        self.memberships = memberships or {"emp-1": {"co-123"}}
        self.resolve_calls: list[str | None] = []
        self.membership_calls: list[tuple[str, str]] = []

    async def resolve(self, *, session_token: str | None) -> GatewaySession | None:
        self.resolve_calls.append(session_token)
        if not session_token or session_token not in self.valid_tokens:
            return None
        return GatewaySession(
            session_id="sess-1",
            employee_id="emp-1",
            subject_id="employee:emp-1",
            company_ids=frozenset(self.memberships.get("emp-1", set())),
        )

    async def employee_in_company(self, *, employee_id: str, company_id: str) -> bool:
        self.membership_calls.append((employee_id, company_id))
        return company_id in self.memberships.get(employee_id, set())


class FakeGrantPort:
    def __init__(
        self,
        *,
        active: bool = True,
        public_pages: frozenset[str] = frozenset(),
        grant_id: str = "grant-1",
        plugin_instance_id: str = "inst-alpha-1",
        artifact_digest: str | None = ARTIFACT_DIGEST,
        published_artifact_digest: Any = _USE_GRANT_DIGEST_AS_PUBLISHED,
        webhook_routes: frozenset[str] | None = None,
        rpc_operations: tuple[GatewayOperation, ...] = (),
    ) -> None:
        self.active = active
        self._public_pages = public_pages
        self._webhook_routes = webhook_routes
        self._rpc_operations = rpc_operations
        self.grant_id = grant_id
        self.plugin_instance_id = plugin_instance_id
        self.artifact_digest = artifact_digest
        self.published_artifact_digest = (
            artifact_digest
            if published_artifact_digest is _USE_GRANT_DIGEST_AS_PUBLISHED
            else published_artifact_digest
        )
        self.grant_calls: list[tuple[str, str]] = []
        self.public_calls: list[tuple[str, str]] = []

    async def active_grant(self, *, plugin_id: str, company_id: str) -> GatewayGrant | None:
        self.grant_calls.append((plugin_id, company_id))
        if not self.active:
            return None
        return GatewayGrant(
            grant_id=self.grant_id,
            plugin_instance_id=self.plugin_instance_id,
            active=True,
            capability_token="stale-token-from-grant",
            operations=frozenset({"read", "write"}),
            artifact_digest=self.artifact_digest,
            published_artifact_digest=self.published_artifact_digest,
        )

    async def public_pages(self, *, plugin_id: str, company_id: str) -> frozenset[str]:
        self.public_calls.append((plugin_id, company_id))
        return self._public_pages

    async def webhook_routes(
        self, *, plugin_id: str, company_id: str
    ) -> frozenset[str] | None:
        return self._webhook_routes

    async def rpc_operations(
        self, *, plugin_id: str, company_id: str
    ) -> tuple[GatewayOperation, ...]:
        return self._rpc_operations


class FakeSecretPort:
    def __init__(self, secret: str | None = WEBHOOK_SECRET) -> None:
        self.secret = secret
        self.calls: list[tuple[str, str]] = []

    async def webhook_secret(self, *, plugin_id: str, company_id: str) -> str | None:
        self.calls.append((plugin_id, company_id))
        return self.secret


class FakeRateLimitPort:
    """In-memory аналог Redis SET NX + TTL / INCR + EXPIRE."""

    def __init__(
        self, *, allow: bool = True, deny_bucket_contains: str | None = None
    ) -> None:
        self.allow_result = allow
        self.deny_bucket_contains = deny_bucket_contains
        self.allow_calls: list[dict[str, Any]] = []
        self.deliveries: dict[str, str] = {}
        self.processing: dict[str, str] = {}
        self.done: dict[str, str] = {}
        self.claim_calls: list[dict[str, Any]] = []
        self.commit_calls: list[dict[str, Any]] = []
        self.release_calls: list[dict[str, Any]] = []

    async def allow(self, *, bucket: str, limit: int, window_seconds: int) -> bool:
        self.allow_calls.append(
            {"bucket": bucket, "limit": limit, "window_seconds": window_seconds}
        )
        if self.deny_bucket_contains and self.deny_bucket_contains in bucket:
            return False
        return self.allow_result

    async def claim_delivery(
        self, *, key: str, ttl_seconds: int, token: str | None = None
    ) -> DeliveryVerdict:
        self.claim_calls.append({"key": key, "ttl_seconds": ttl_seconds, "token": token})
        if key in self.deliveries:
            return DeliveryVerdict(
                status=DeliveryStatus.DUPLICATE,
                first_seen_at=1.0,
                previous_token=self.deliveries[key],
            )
        self.deliveries[key] = token or ""
        return DeliveryVerdict(status=DeliveryStatus.FIRST, first_seen_at=1.0)

    async def claim_delivery_lease(
        self, *, key: str, ttl_seconds: int, lease_seconds: int, token: str
    ) -> DeliveryVerdict:
        self.claim_calls.append({
            "key": key,
            "ttl_seconds": ttl_seconds,
            "lease_seconds": lease_seconds,
            "token": token,
        })
        if key in self.done:
            return DeliveryVerdict(
                status=DeliveryStatus.DUPLICATE, previous_token=self.done[key]
            )
        if key in self.processing:
            return DeliveryVerdict(
                status=DeliveryStatus.IN_PROGRESS,
                previous_token=self.processing[key],
                retry_after_seconds=3,
            )
        self.processing[key] = token
        return DeliveryVerdict(status=DeliveryStatus.FIRST, first_seen_at=1.0)

    async def commit_delivery(self, *, key: str, ttl_seconds: int, token: str) -> bool:
        self.commit_calls.append({"key": key, "ttl_seconds": ttl_seconds, "token": token})
        if self.processing.get(key) != token:
            return self.done.get(key) == token
        self.processing.pop(key, None)
        self.done[key] = token
        self.deliveries[key] = token
        return True

    async def release_delivery(self, *, key: str, token: str) -> None:
        self.release_calls.append({"key": key, "token": token})
        if self.processing.get(key) == token:
            self.processing.pop(key, None)


class FakeAuditPort:
    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[GatewayAuditEvent] = []
        self.fail = fail

    async def record(self, event: GatewayAuditEvent) -> None:
        if self.fail:
            raise RuntimeError("audit sink down")
        self.events.append(event)


class FakeActivationPort:
    def __init__(
        self, *, ready: bool = True, artifact_digest: str | None = ARTIFACT_DIGEST
    ) -> None:
        self.ready = ready
        self.artifact_digest = artifact_digest
        self.grant_id = "grant-1"
        self.wake_calls: list[tuple[str, str, str | None]] = []
        self.wait_calls: list[tuple[str, float]] = []

    async def wake(
        self,
        *,
        plugin_id: str,
        company_id: str,
        plugin_instance_id: str | None = None,
    ) -> WakeLease:
        self.wake_calls.append((plugin_id, company_id, plugin_instance_id))
        return WakeLease(
            plugin_id=plugin_id,
            company_id=company_id,
            plugin_instance_id=plugin_instance_id,
            capability_token="fresh-capability-token-from-lease",
            artifact_digest=self.artifact_digest,
            grant_id=self.grant_id,
            expires_at=1_000_000.0,
            ready=True,
        )

    async def wait_ready(self, lease: WakeLease, *, timeout_seconds: float) -> bool:
        self.wait_calls.append((lease.plugin_id, timeout_seconds))
        return self.ready


@dataclass
class RecordingHttpPort:
    """Записывает запросы и возвращает заданный ответ (в т.ч. потоковый)."""

    response: BackendResponse | None = None
    requests: list[BackendRequest] = field(default_factory=list)
    #: ВАЖНО для проверки «wake до forward»: порядок вызовов общий для портов.
    timeline: list[str] = field(default_factory=list)
    #: Если задан — ответ отдаётся как НЕБУФЕРИЗОВАННЫЙ async-генератор.
    chunks: list[bytes] | None = None

    async def send(self, request: BackendRequest) -> BackendResponse:
        self.requests.append(request)
        self.timeline.append("send")
        if self.chunks is not None:
            async def _gen() -> AsyncIterator[bytes]:
                for chunk in self.chunks or []:
                    yield chunk

            return BackendResponse(
                status_code=200,
                headers={"Content-Type": "text/event-stream"},
                stream=_gen(),
            )
        return self.response or BackendResponse(
            status_code=200, headers={"Content-Type": "application/json"}, body=b'{"ok":true}'
        )


@dataclass
class Harness:
    """Собранное приложение + все фейки для assertions."""

    app: FastAPI
    client: AsyncClient
    sessions: FakeSessionPort
    grants: FakeGrantPort
    secrets: FakeSecretPort
    rate_limit: FakeRateLimitPort
    audit: FakeAuditPort
    activation: FakeActivationPort
    backend_http: RecordingHttpPort
    static_http: RecordingHttpPort
    auth: GatewayAuth
    proxy: GatewayProxy


def build_harness(
    *,
    public_pages: frozenset[str] = frozenset(),
    session_active: bool = True,
    grant_active: bool = True,
    plugin_instance_id: str = "inst-alpha-1",
    artifact_digest: str | None = ARTIFACT_DIGEST,
    published_artifact_digest: Any = _USE_GRANT_DIGEST_AS_PUBLISHED,
    secret: str | None = WEBHOOK_SECRET,
    rate_allow: bool = True,
    wake_ready: bool = True,
    backend_response: BackendResponse | None = None,
    chunks: list[bytes] | None = None,
    timeline: list[str] | None = None,
    audit_fail: bool = False,
    static_http: RecordingHttpPort | None = None,
    limits: GatewayLimits | None = None,
    webhook_routes: frozenset[str] | None = None,
    rpc_operations: tuple[GatewayOperation, ...] = (),
    rate_deny_bucket: str | None = None,
) -> Harness:
    """Собрать FastAPI-приложение ТОЛЬКО с router шлюза (без create_app/БД)."""
    sessions = FakeSessionPort(
        valid_tokens={"good-session"} if session_active else set(),
    )
    grants = FakeGrantPort(
        active=grant_active,
        public_pages=public_pages,
        plugin_instance_id=plugin_instance_id,
        artifact_digest=artifact_digest,
        published_artifact_digest=published_artifact_digest,
        webhook_routes=webhook_routes,
        rpc_operations=rpc_operations,
    )
    secrets = FakeSecretPort(secret=secret)
    rate_limit = FakeRateLimitPort(
        allow=rate_allow, deny_bucket_contains=rate_deny_bucket
    )
    audit = FakeAuditPort(fail=audit_fail)
    activation = FakeActivationPort(
        ready=wake_ready, artifact_digest=artifact_digest
    )
    shared_timeline = timeline if timeline is not None else []
    backend_http = RecordingHttpPort(
        response=backend_response, chunks=chunks, timeline=shared_timeline
    )
    static = static_http or RecordingHttpPort(
        response=BackendResponse(
            status_code=200,
            headers={"Content-Type": "text/html; charset=utf-8"},
            body=b"<html>plugin</html>",
        ),
        timeline=shared_timeline,
    )
    # Порядок wake → send фиксируется в ОБЩЕМ timeline.
    original_wake = activation.wake

    async def _wake(
        *,
        plugin_id: str,
        company_id: str,
        plugin_instance_id: str | None = None,
    ) -> WakeLease:
        shared_timeline.append("wake")
        return await original_wake(
            plugin_id=plugin_id,
            company_id=company_id,
            plugin_instance_id=plugin_instance_id,
        )

    activation.wake = _wake  # type: ignore[method-assign]

    effective_limits = limits or GatewayLimits()
    auth = GatewayAuth(
        sessions=sessions,
        grants=grants,
        secrets=secrets,
        rate_limit=rate_limit,
        audit=audit,
        hmac_verifier=Sha256HmacVerifier(),
        limits=effective_limits,
    )
    proxy = GatewayProxy(
        activation=activation,
        backend_http=backend_http,
        static_http=static,
        audit=audit,
        limits=effective_limits,
        static_response_policy=StaticResponsePolicy(shell_origin=SHELL_ORIGIN),
    )
    # redirect_slashes=False: «голые» префиксы должны давать ЯВНЫЙ 400, а не
    # 307-редирект на вариант со слэшем (иначе критерий №9 не проверяем).
    app = FastAPI(redirect_slashes=False)
    app.include_router(router)
    install_plugin_gateway_runtime(app, PluginGatewayRuntime(auth=auth, proxy=proxy))
    transport = ASGITransport(app=app)
    client = AsyncClient(transport=transport, base_url="http://test")
    return Harness(
        app=app,
        client=client,
        sessions=sessions,
        grants=grants,
        secrets=secrets,
        rate_limit=rate_limit,
        audit=audit,
        activation=activation,
        backend_http=backend_http,
        static_http=static,
        auth=auth,
        proxy=proxy,
    )
