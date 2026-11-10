"""Shared fake durable/K8s boundaries for canonical backend tests; never executed live."""
from __future__ import annotations

import copy
import hashlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import (
    PluginInstanceRecord,
    PluginInstanceStatus,
)
from prodavan.application.plugins.activation.state import WakeResult
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PublicationMetadata,
)
from prodavan.application.plugins.artifacts.verification import VerificationEvidence
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.deployment_admin import (
    ARTIFACT_ANNOTATION,
    BACKEND_ANNOTATION,
    COMPANY_LABEL,
    INSTANCE_LABEL,
    MANAGED_BY,
    MANAGED_BY_VALUE,
    PLUGIN_LABEL,
)
from prodavan.infrastructure.plugins.runtime.k8s_scale import plugin_resource_name

COMPANY = "co_123"
PLUGIN = "mod_x"
INSTANCE = "inst_abc"
PROJECT = "project_1"
JOB = "job_1"
ARTIFACT_METADATA = b'{"verified":"metadata"}'
BACKEND_METADATA = b'{"verified":"backend!"}'
ARTIFACT_DIGEST = "sha256:" + hashlib.sha256(ARTIFACT_METADATA).hexdigest()
BACKEND_DIGEST = "sha256:" + hashlib.sha256(BACKEND_METADATA).hexdigest()
CONTENT_DIGEST = "sha256:" + "c" * 64
EMPTY_DIGEST = "sha256:" + "0" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def canonical_digest(value: Any) -> str:
    raw = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def canonical_size(value: Any) -> int:
    return len(
        json.dumps(
            value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        ).encode("utf-8")
    )


def labels() -> dict[str, str]:
    def hashed(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]

    return {
        MANAGED_BY: MANAGED_BY_VALUE,
        COMPANY_LABEL: hashed(COMPANY),
        PLUGIN_LABEL: hashed(PLUGIN),
        INSTANCE_LABEL: hashed(f"{COMPANY}\0{PLUGIN}\0{INSTANCE}"),
    }


def resource_name() -> str:
    return plugin_resource_name(PLUGIN, INSTANCE)


def instance_record(**changes: Any) -> PluginInstanceRecord:
    values = {
        "plugin_instance_id": INSTANCE,
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "manifest_version": "1.2.3",
        "artifact_digest": ARTIFACT_DIGEST,
        "status": PluginInstanceStatus.ACTIVE,
        "tenant_db_name": "tenant_co_123",
        "bucket_name": "tenant-co_123",
        "created_at": NOW,
        "updated_at": NOW,
    }
    values.update(changes)
    return PluginInstanceRecord(**values)


def artifact_record(**changes: Any) -> PluginArtifactRecord:
    manifest = {
        "plugin_id": PLUGIN,
        "version": "1.2.3",
        "backend": {"port": 8080, "health": "/health"},
        "pods": {
            "allowlist": [
                {
                    "plugin_instance_id": INSTANCE,
                    "project_id": PROJECT,
                    "job_id": JOB,
                }
            ]
        },
    }
    evidence = VerificationEvidence(
        image_ref=f"ghcr.io/ne-tort/mod_x-bundle@{ARTIFACT_DIGEST}",
        artifact_digest=ARTIFACT_DIGEST,
        manifest_fingerprint="f" * 64,
        identity="ci@example.com",
        issuer="https://issuer.example.com",
        sbom_verified=True,
        provenance_verified=True,
        observed_at=NOW,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        seal=object(),
        scan_verified=True,
        csp_verified=True,
    )
    values = {
        "plugin_id": PLUGIN,
        "version": "1.2.3",
        "digest": ARTIFACT_DIGEST,
        "image_ref": f"ghcr.io/ne-tort/mod_x-bundle@{ARTIFACT_DIGEST}",
        "manifest": manifest,
        "manifest_fingerprint": "f" * 64,
        "status": AppLifecycleStatus.PUBLISHED,
        "outcome": RegistryOutcome.APPLIED,
        "quarantine": None,
        "published_at": NOW,
        "created_at": NOW,
        "verification": evidence,
        "publication": PublicationMetadata(
            source_id="source-1",
            commit_sha="a" * 40,
            backend_digest=BACKEND_DIGEST,
            backend_image_ref=f"ghcr.io/ne-tort/mod_x@{BACKEND_DIGEST}",
            run_url="https://ci.example.com/runs/1",
        ),
    }
    values.update(changes)
    return PluginArtifactRecord(**values)


def service_manifest() -> dict[str, Any]:
    ownership = labels()
    return {
        "apiVersion": "v1",
        "kind": "Service",
        "metadata": {
            "name": resource_name(),
            "namespace": "plugins",
            "labels": ownership,
        },
        "spec": {
            "type": "ClusterIP",
            "selector": {INSTANCE_LABEL: ownership[INSTANCE_LABEL]},
            "ports": [
                {"name": "http", "port": 8080, "targetPort": "http", "protocol": "TCP"}
            ],
        },
    }


def deployment_manifest() -> dict[str, Any]:
    ownership = labels()
    annotations = {
        ARTIFACT_ANNOTATION: ARTIFACT_DIGEST,
        BACKEND_ANNOTATION: BACKEND_DIGEST,
    }
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": resource_name(),
            "namespace": "plugins",
            "labels": ownership,
            "annotations": annotations,
        },
        "spec": {
            "template": {
                "metadata": {"labels": ownership, "annotations": annotations},
                "spec": {
                    "automountServiceAccountToken": False,
                    "containers": [
                        {
                            "name": "backend",
                            "image": f"ghcr.io/ne-tort/mod_x@{BACKEND_DIGEST}",
                        }
                    ],
                },
            }
        },
    }


class FakeInstances:
    def __init__(self, record: PluginInstanceRecord | None = None) -> None:
        self.record = record or instance_record()

    async def get_by_id(self, *, plugin_instance_id: str) -> PluginInstanceRecord | None:
        if self.record is None or self.record.plugin_instance_id != plugin_instance_id:
            return None
        return self.record


class FakeArtifacts:
    def __init__(self, record: PluginArtifactRecord | None = None) -> None:
        self.record = record or artifact_record()

    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None:
        if self.record is None or self.record.digest != digest:
            return None
        return self.record


class FakeTokenProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def get_token(self) -> str:
        self.calls += 1
        return "scoped-lifecycle-token-test-only"


class FakeActivator:
    def __init__(
        self,
        *,
        token_prefix: str = "wake-token-test-only",
        lease_instance_id: str | None = None,
        fail_with: Exception | None = None,
        **claim_changes: Any,
    ) -> None:
        self.calls: list[tuple[str, float | None]] = []
        self.token_prefix = token_prefix
        self.lease_instance_id = lease_instance_id
        self.fail_with = fail_with
        self.claim_changes = claim_changes

    async def wake(self, instance_id: str, *, timeout: float | None = None) -> WakeResult:
        self.calls.append((instance_id, timeout))
        if self.fail_with is not None:
            raise self.fail_with
        claims = {
            "sub": f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
            "company_id": COMPANY,
            "project_id": PROJECT,
            "plugin_id": PLUGIN,
            "plugin_instance_id": INSTANCE,
            "artifact_digest": ARTIFACT_DIGEST,
            "grant_id": "grant_1",
            "scopes": ["pods", "network"],
        }
        claims.update(self.claim_changes)
        return WakeResult(
            plugin_instance_id=self.lease_instance_id or instance_id,
            token=f"{self.token_prefix}-{len(self.calls)}",
            jti=f"wake-jti-{len(self.calls)}",
            claims=claims,
            issued_at=NOW,
            expires_at=NOW + timedelta(minutes=5),
            ready_at=NOW,
            waited_seconds=0.0,
            previous_replicas=1,
        )


class FakeK8sApi:
    def __init__(self, token_provider: FakeTokenProvider) -> None:
        self._token_provider = token_provider
        self.service = service_manifest()
        self.deployment = deployment_manifest()
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.headers_by_call: list[dict[str, str] | None] = []
        self.proxy_handler: Any = None
        self.close_calls = 0
        self.fail_with: Exception | None = None

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        content_type: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        del content_type
        self.calls.append((method, path, copy.deepcopy(body)))
        self.headers_by_call.append(copy.deepcopy(headers))
        if self.fail_with is not None:
            raise self.fail_with
        if method == "GET" and path.endswith(f"/services/{resource_name()}"):
            return SimpleNamespace(body=copy.deepcopy(self.service))
        if method == "GET" and path.endswith(f"/deployments/{resource_name()}"):
            return SimpleNamespace(body=copy.deepcopy(self.deployment))
        if "/proxy/" in path:
            if self.proxy_handler is None:
                raise AssertionError("unexpected proxy call")
            return SimpleNamespace(body=self.proxy_handler(method, path, body))
        raise AssertionError(f"unexpected K8s request: {method} {path}")

    async def aclose(self) -> None:
        self.close_calls += 1

    @property
    def proxy_calls(self) -> list[tuple[str, str, dict[str, Any] | None]]:
        return [call for call in self.calls if "/proxy/" in call[1]]

    @property
    def proxy_headers(self) -> list[dict[str, str] | None]:
        return [
            headers
            for call, headers in zip(self.calls, self.headers_by_call, strict=True)
            if "/proxy/" in call[1]
        ]


def runtime_config() -> RuntimeConfig:
    return RuntimeConfig(namespace="plugins")
