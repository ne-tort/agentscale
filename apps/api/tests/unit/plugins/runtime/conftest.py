"""Общие фейки для unit-тестов runtime-адаптеров плагинов.

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (verification freeze, план миграции §0.2,
дизайн v4 §9). Ни один фейк не делает IO: cosign-CLI, k8s API, HTTP-пробы и
httpx подменяются объектами в памяти.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from prodavan.application.plugins.activation.ports import MintedClaims
from prodavan.application.plugins.activation.state import ActivationState
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sApiConflict,
    K8sApiNotFound,
    K8sApiUnavailable,
    K8sRawResponse,
    ProbeResult,
)

TEST_DIGEST = "sha256:" + ("ab12cd34" * 8)
TEST_REF = f"ghcr.io/ne-tort/plugins/mod_equipment@{TEST_DIGEST}"
TEST_IDENTITY = (
    "https://github.com/ne-tort/agentscale/.github/workflows/"
    "plugin-artifacts.yml@refs/heads/main"
)
TEST_ISSUER = "https://token.actions.githubusercontent.com"
TEST_INSTANCE = "inst_equipment_001"
TEST_DEPLOYMENT = "plugin-mod-equipment-inst-equipment-001"
TEST_KMS_REF = "gcpkms://projects/p/locations/l/keyRings/r/cryptoKeys/k/versions/1"


@pytest.fixture
def config() -> RuntimeConfig:
    return RuntimeConfig(namespace="agentscale-dev")


# --------------------------------------------------------------------------- cosign


@dataclass
class FakeCosignRunner:
    """Инъекция ``runner(argv, *, timeout)``: управляет ответами без subprocess."""

    calls: list[list[str]] = field(default_factory=list)
    timeouts: list[float] = field(default_factory=list)
    stdout: str = json.dumps([{"verified": True}])
    stdout_by_label: dict[str, str] = field(default_factory=dict)
    raise_on: str | None = None
    exception: Exception | None = None

    def __call__(self, argv: list[str], *, timeout: float) -> Any:
        self.calls.append(list(argv))
        self.timeouts.append(timeout)
        if self.raise_on is not None and self.raise_on in " ".join(argv):
            raise self.exception or RuntimeError("cosign failed")
        label = "verify-attestation" if "verify-attestation" in argv else "verify"
        if "--type" in argv:
            label = argv[argv.index("--type") + 1]
        stdout = self.stdout_by_label.get(label, self.stdout)
        return type("Completed", (), {"returncode": 0, "stdout": stdout, "stderr": ""})()


def cosign_json(payload: Any = None) -> str:
    return json.dumps([{"ok": True}] if payload is None else payload)


# --------------------------------------------------------------------------- k8s API


class FakeK8sApi:
    """In-memory scoped k8s REST: журнал запросов + ответы/сбои без IO."""

    scoped_identity = True

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.handlers: dict[tuple[str, str], Any] = {}
        self.default_error: Exception | None = None
        self.configmaps: dict[str, dict[str, Any]] = {}

    def on(self, method: str, path: str, handler: Any) -> None:
        self.handlers[(method.upper(), path)] = handler

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
    ) -> K8sRawResponse:
        self.calls.append(
            {"method": method.upper(), "path": path, "body": body, "content_type": content_type}
        )
        if self.default_error is not None:
            raise self.default_error
        handler = self.handlers.get((method.upper(), path))
        if handler is None:
            raise K8sApiNotFound(f"no handler for {method} {path}")
        result = handler(body) if callable(handler) else handler
        if isinstance(result, Exception):
            raise result
        return result


def scale_response(replicas: int, *, resource_version: str = "7") -> K8sRawResponse:
    return K8sRawResponse(
        status_code=200,
        body={
            "kind": "Scale",
            "metadata": {"name": TEST_DEPLOYMENT, "resourceVersion": resource_version},
            "spec": {"replicas": replicas},
        },
    )


def deployment_response(ready_replicas: int | None, *, resource_version: str = "9") -> K8sRawResponse:
    status: dict[str, Any] = {}
    if ready_replicas is not None:
        status["readyReplicas"] = ready_replicas
    return K8sRawResponse(
        status_code=200,
        body={
            "kind": "Deployment",
            "metadata": {"name": TEST_DEPLOYMENT, "resourceVersion": resource_version},
            "status": status,
        },
    )


def configmap_response(data: dict[str, str], *, resource_version: str = "42") -> K8sRawResponse:
    return K8sRawResponse(
        status_code=200,
        body={
            "kind": "ConfigMap",
            "metadata": {"name": "plugin-activation-state", "resourceVersion": resource_version},
            "data": dict(data),
        },
    )


# --------------------------------------------------------------------------- probe / backend http


class FakeProbe:
    """Очередь ответов HTTP-пробы ``/health``."""

    def __init__(self, *, results: list[ProbeResult] | None = None) -> None:
        self.urls: list[str] = []
        self.results = list(results or [ProbeResult(status_code=200)])

    async def probe(self, url: str) -> ProbeResult:
        self.urls.append(url)
        if self.results:
            return self.results.pop(0)
        return ProbeResult(status_code=200)


class FakeBackendSender:
    """``BackendHttpPort`` без сети: сохраняет запрос, возвращает живой SSE."""

    def __init__(self) -> None:
        self.sent: list[Any] = []
        self.closed = False

    async def send(self, request: Any) -> Any:
        self.sent.append(request)

        async def chunks():  # type: ignore[no-untyped-def]
            yield b"data: one\n\n"
            yield b"data: two\n\n"

        from prodavan.application.plugins.gateway.ports import BackendResponse

        return BackendResponse(
            status_code=200,
            headers={"content-type": "text/event-stream"},
            body=None,
            stream=chunks(),
        )

    async def aclose(self) -> None:
        self.closed = True


def fresh_claims(token: str = "fresh.jwt.token", *, ttl: float = 300.0) -> MintedClaims:
    """Свежий короткий токен с проверенным payload ``PluginClaims``."""
    now = datetime.now(UTC)
    return MintedClaims(
        token=token,
        jti="jti_" + token,
        issued_at=now,
        expires_at=now + timedelta(seconds=ttl),
        claims={
            "iss": "agentscale-plugins",
            "aud": "capability-gateway",
            "sub": "plugin:mod_equipment:co_123:inst_equipment_001",
            "plugin_id": "mod_equipment",
            "company_id": "co_123",
            "project_id": "proj_1",
            "plugin_instance_id": TEST_INSTANCE,
            "grant_id": "grant_01",
            "artifact_digest": TEST_DIGEST,
        },
    )


def default_state(instance: str = TEST_INSTANCE) -> ActivationState:
    return ActivationState(plugin_instance_id=instance)


__all__ = [
    "TEST_DEPLOYMENT",
    "TEST_DIGEST",
    "TEST_IDENTITY",
    "TEST_INSTANCE",
    "TEST_ISSUER",
    "TEST_KMS_REF",
    "TEST_REF",
    "FakeBackendSender",
    "FakeCosignRunner",
    "FakeK8sApi",
    "FakeProbe",
    "K8sApiConflict",
    "K8sApiNotFound",
    "K8sApiUnavailable",
    "configmap_response",
    "config",
    "cosign_json",
    "default_state",
    "deployment_response",
    "fresh_claims",
    "scale_response",
]
