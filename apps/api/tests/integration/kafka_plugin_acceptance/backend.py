"""Real local backend and the test-only K8s-free wake fixture.

The HTTP path uses production HttpProxyPort plus BackendHttpAdapter. The backend
verifies the independently signed JWT and the independently signed event before
returning 2xx. It records only ids and digests and overrides HTTP logging.

Real Kubernetes scale/Ready is intentionally absent: the small activator below
is a test-only wake fixture, not a production K8s acceptance result.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import threading
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from prodavan.application.plugins.admin.contracts import MintRequest
from prodavan.application.plugins.activation.state import WakeResult
from prodavan.application.plugins.adapters.identity_adapter import IdentityMintAdapter
from prodavan.application.plugins.events.dispatch import HmacPluginEventSigner
from prodavan.application.plugins.events.errors import PluginEventError, PluginEventErrorCode
from prodavan.application.plugins.identity.keys import keyring_from_secret
from prodavan.application.plugins.identity.mint import verify_plugin_token
from prodavan.infrastructure.plugins.adapters.gateway_adapters import BackendHttpAdapter
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.proxy import HttpProxyPort

from .resources import AcceptanceIdentity


@dataclass(frozen=True, slots=True)
class DeliveryObservation:
    request_no: int
    event_id: str
    body_digest: str
    auth_ok: bool
    event_signature_ok: bool
    accepted: bool
    transient_error: bool = False


class AcceptanceBackend:
    """Bounded local HTTP service used by the real production proxy."""

    def __init__(
        self,
        *,
        identity: AcceptanceIdentity,
        event_signer: HmacPluginEventSigner,
        claims_secret: str,
        topic: str,
        artifact_digest: str,
        timeout_seconds: float,
    ) -> None:
        self._identity = identity
        self._event_signer = event_signer
        self._claims_keyring = keyring_from_secret(claims_secret)
        self._topic = topic
        self._artifact_digest = artifact_digest
        self._timeout_seconds = timeout_seconds
        self._lock = threading.Lock()
        self._next_request = 1
        self._fail_remaining = 0
        self._gate: dict[str, threading.Event] | None = None
        self.attempts = 0
        self.rejected = 0
        self.observations: list[DeliveryObservation] = []
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler_class())
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_port}"

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="kafka-plugin-acceptance-backend",
            daemon=True,
        )
        self._thread.start()

    async def stop(self) -> None:
        with self._lock:
            gate = self._gate
            self._gate = None
        if gate is not None:
            gate["release"].set()
        server = self._server
        self._server = None
        thread = self._thread
        self._thread = None
        if server is not None:
            if thread is not None:
                await asyncio.wait_for(asyncio.to_thread(server.shutdown), timeout=self._timeout_seconds)
            server.server_close()
        if thread is not None:
            await asyncio.wait_for(asyncio.to_thread(thread.join), timeout=self._timeout_seconds)

    def fail_next(self, count: int) -> None:
        if count < 1 or count > 3:
            raise ValueError("acceptance retry injection accepts 1..3 failures")
        with self._lock:
            self._fail_remaining += count

    def gate_next_request(self) -> None:
        with self._lock:
            if self._gate is not None:
                raise RuntimeError("only one backend request may be gated")
            self._gate = {"entered": threading.Event(), "release": threading.Event()}

    async def wait_for_gated_request(self) -> None:
        gate = self._gate
        if gate is None:
            raise RuntimeError("backend request was not gated")
        if not await asyncio.wait_for(asyncio.to_thread(gate["entered"].wait), timeout=self._timeout_seconds):
            raise TimeoutError("backend did not receive the gated request")

    async def release_gated_request(self) -> None:
        with self._lock:
            gate = self._gate
            self._gate = None
        if gate is None:
            raise RuntimeError("backend request was not gated")
        gate["release"].set()

    def _handler_class(self) -> type[BaseHTTPRequestHandler]:
        outer = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                return

            def log_request(self, code: int | str = "-", size: str | int = "-") -> None:
                return

            def do_POST(self) -> None:
                observation = outer._accept(self)
                if observation.accepted:
                    status = 204
                elif observation.transient_error:
                    status = 503
                else:
                    status = 403
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.send_header("Connection", "close")
                self.end_headers()
                with outer._lock:
                    outer.observations.append(observation)

        return Handler

    def _accept(self, handler: BaseHTTPRequestHandler) -> DeliveryObservation:
        with self._lock:
            request_no = self._next_request
            self._next_request += 1
            self.attempts += 1
            fail = self._fail_remaining > 0
            if fail:
                self._fail_remaining -= 1
            gate = self._gate
            if gate is not None:
                gate["entered"].set()
        if gate is not None:
            if not gate["release"].wait(timeout=self._timeout_seconds):
                with self._lock:
                    self.rejected += 1
                return DeliveryObservation(request_no, "", "", False, False, False, True)
        if fail:
            return DeliveryObservation(request_no, "", "", False, False, False, True)

        try:
            length = int(handler.headers.get("Content-Length", "0"))
            if length <= 0 or length > 65536:
                raise ValueError
            body = handler.rfile.read(length)
            if handler.path != "/events/acceptance.created":
                raise ValueError
            if handler.headers.get("Content-Type", "").split(";", 1)[0] != "application/json":
                raise ValueError
            if not self._verify_claims(handler.headers):
                raise ValueError
            decoded = json.loads(body.decode("utf-8"))
            event_id = decoded.get("event_id")
            signature = decoded.get("event_signature")
            from prodavan.application.plugins.events.contracts import PluginEvent

            event = PluginEvent.from_wire(decoded)
            signature_ok = (
                isinstance(event_id, str)
                and isinstance(signature, str)
                and self._event_signer.verify(event, topic=self._topic, signature=signature)
                and event.plugin_id == self._identity.plugin_id
                and event.company_id == self._identity.company_id
            )
            accepted = bool(signature_ok)
            body_digest = "sha256:" + hashlib.sha256(body).hexdigest()
        except Exception:
            with self._lock:
                self.rejected += 1
            return DeliveryObservation(request_no, "", "", False, False, False)
        return DeliveryObservation(
            request_no=request_no,
            event_id=event_id if isinstance(event_id, str) else "",
            body_digest=body_digest,
            auth_ok=True,
            event_signature_ok=bool(signature_ok),
            accepted=accepted,
        )

    def _verify_claims(self, headers: Any) -> bool:
        token = _header(headers, "Authorization")
        if not token.startswith("Bearer "):
            return False
        raw_token = token[7:]
        if not hmac.compare_digest(_header(headers, "X-Plugin-Capability-Token"), raw_token):
            return False
        expected_headers = {
            "X-Plugin-ID": self._identity.plugin_id,
            "X-Plugin-Instance-ID": self._identity.plugin_instance_id,
            "X-Plugin-Artifact-Digest": self._artifact_digest,
            "X-Company-ID": self._identity.company_id,
            "X-Plugin-Grant-ID": self._identity.grant_id,
        }
        if any(_header(headers, name) != value for name, value in expected_headers.items()):
            return False
        try:
            claims = verify_plugin_token(raw_token, keyring=self._claims_keyring)
        except Exception:
            return False
        expected = {
            "company_id": self._identity.company_id,
            "plugin_id": self._identity.plugin_id,
            "plugin_instance_id": self._identity.plugin_instance_id,
            "manifest_version": "1.0.0",
            "artifact_digest": self._artifact_digest,
            "grant_id": self._identity.grant_id,
            "sub": f"plugin:{self._identity.plugin_id}:{self._identity.company_id}:{self._identity.plugin_instance_id}",
        }
        actual = asdict(claims)
        return all(actual.get(name) == value for name, value in expected.items())


class TestOnlyWakeFixture:
    """Wake fixture backed by real SQL grant + real JWT mint, no Kubernetes."""

    def __init__(
        self,
        *,
        grants: Any,
        claims_secret: str,
        identity: AcceptanceIdentity,
        sensitive: Any,
        timeout_seconds: float,
    ) -> None:
        self._grants = grants
        self._mint = IdentityMintAdapter(secret=claims_secret)
        self._claims_keyring = keyring_from_secret(claims_secret)
        self._identity = identity
        self._sensitive = sensitive
        self._timeout_seconds = timeout_seconds
        self._wake_gates: list[dict[str, Any]] = []
        self.wake_calls = 0
        self.wake_denials = 0

    def gate_next_wake(self, *, after_result: bool = False) -> None:
        self._wake_gates.append({
            "entered": asyncio.Event(),
            "release": asyncio.Event(),
            "after_result": after_result,
        })

    def gate_after_wake(self) -> None:
        """Pause after a real WakeResult is built, before dispatcher renew."""

        self.gate_next_wake(after_result=True)

    async def wait_for_gated_wake(self) -> None:
        if not self._wake_gates:
            raise RuntimeError("wake was not gated")
        gate = self._wake_gates[0]
        await asyncio.wait_for(gate["entered"].wait(), timeout=self._timeout_seconds)

    async def release_gated_wake(self) -> None:
        gate = self._pop_gate()
        gate["release"].set()

    def _pop_gate(self) -> dict[str, asyncio.Event]:
        if not self._wake_gates:
            raise RuntimeError("wake was not gated")
        return self._wake_gates.pop(0)

    async def _wait_wake_gate(self, gate: dict[str, Any], timeout: float | None) -> None:
        gate["entered"].set()
        try:
            await asyncio.wait_for(
                gate["release"].wait(),
                timeout=min(timeout or self._timeout_seconds, self._timeout_seconds),
            )
        except TimeoutError as exc:
            self.wake_denials += 1
            raise PluginEventError(
                PluginEventErrorCode.WAKE_FAILED,
                "gated acceptance wake timed out",
            ) from exc

    async def wake(self, plugin_instance_id: str, *, timeout: float | None = None) -> WakeResult:
        gate = self._wake_gates[0] if self._wake_gates else None
        if gate is not None and not gate["after_result"]:
            await self._wait_wake_gate(gate, timeout)

        grant = await self._grants.get_by_instance_id(plugin_instance_id=plugin_instance_id)
        if (
            plugin_instance_id != self._identity.plugin_instance_id
            or grant is None
            or grant.plugin_instance_id != plugin_instance_id
            or not grant.is_active
            or grant.revoked_at is not None
        ):
            self.wake_denials += 1
            raise PluginEventError(PluginEventErrorCode.WAKE_FAILED, "acceptance wake grant is inactive or mismatched")

        issued = await self._mint.mint(request=MintRequest(
            company_id=grant.company_id,
            plugin_id=grant.plugin_id,
            plugin_instance_id=grant.plugin_instance_id,
            manifest_version=grant.manifest_version,
            artifact_digest=grant.artifact_digest,
            grant_id=grant.grant_id,
            scopes=("events",),
            quota_ref=f"{grant.company_id}/acceptance",
            ttl_seconds=60,
        ))
        verified = verify_plugin_token(issued.token, keyring=self._claims_keyring)
        now = datetime.now(UTC)
        result = WakeResult(
            plugin_instance_id=grant.plugin_instance_id,
            token=issued.token,
            jti=issued.jti,
            claims=asdict(verified),
            issued_at=datetime.fromtimestamp(verified.iat, tz=UTC),
            expires_at=datetime.fromtimestamp(verified.exp, tz=UTC),
            ready_at=now,
            waited_seconds=max(0.0, (now - datetime.fromtimestamp(verified.iat, tz=UTC)).total_seconds()),
            previous_replicas=0,
        )
        self._sensitive.add(issued.token)
        self._sensitive.add(issued.jti)
        self.wake_calls += 1
        if gate is not None and gate["after_result"]:
            await self._wait_wake_gate(gate, timeout)
        return result


def build_production_http_proxy(
    *,
    backend: AcceptanceBackend,
    timeout_seconds: float,
) -> HttpProxyPort:
    config = RuntimeConfig(
        namespace="acceptance-local",
        backend_timeout_seconds=timeout_seconds,
        stream_timeout_seconds=max(timeout_seconds, 30.0),
    )
    adapter = BackendHttpAdapter(
        base_url=backend.base_url,
        max_timeout_seconds=timeout_seconds,
        inject_trusted_bearer=True,
    )
    return HttpProxyPort(config=config, backend_http=adapter)


def _header(headers: Any, name: str) -> str:
    value = headers.get(name)
    return value if isinstance(value, str) else ""


__all__ = [
    "AcceptanceBackend",
    "DeliveryObservation",
    "TestOnlyWakeFixture",
    "build_production_http_proxy",
]
