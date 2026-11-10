"""Offline stateful fake-client tests for K8sDeploymentAdmin.

Written under the migration verification freeze; no test in this module performs
network, subprocess, Docker, kubectl, Git, or cluster operations.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import re
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from urllib.parse import parse_qs

import pytest

from prodavan.application.dynamic_app_registry.contracts import RegistryOutcome
from prodavan.application.plugins.admin.contracts import (
    PluginInstanceStatus,
    PluginRuntimeStatus,
)
from prodavan.application.plugins.artifacts.registry import (
    PluginArtifactRecord,
    PublicationMetadata,
)
from prodavan.application.plugins.artifacts.verification import VerificationEvidence
from prodavan.application.plugins.wiring.install import _deployment_admin
from prodavan.domain.dynamic_apps.types import AppLifecycleStatus
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.deployment_admin import (
    ARTIFACT_ANNOTATION,
    BACKEND_ANNOTATION,
    COMPANY_LABEL,
    DELIVERY_DEFAULT_MODE,
    DELIVERY_MOUNT_PATH,
    DELIVERY_VOLUME_PREFIX,
    DSN_REF_ANNOTATION,
    INSTANCE_LABEL,
    PLUGIN_INSTANCE_ANNOTATION,
    RUNTIME_IDENTITY_MOUNT_PATH,
    RUNTIME_IDENTITY_VOLUME_NAME,
    RUNTIME_TMP_VOLUME_NAME,
    UPDATE_OWNER_ANNOTATION,
    ArtifactProvenanceError,
    DeploymentArtifactDigestConflictError,
    DeploymentConfigurationError,
    DeploymentOwnershipError,
    DeploymentSecretError,
    DeploymentStateError,
    DeploymentUnavailableError,
    K8sDeploymentAdmin,
    _labels,
)
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    DELIVERY_SECRET_PREFIX,
    SecretDeliveryReference,
    SecretReference,
)
from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sApiConflict,
    K8sApiNotFound,
    K8sRawResponse,
    K8sApiUnavailable,
    K8sScalePort,
    ReadinessMode,
    plugin_resource_name,
)

NAMESPACE = "agentscale-dev"
COMPANY = "co_123"
PLUGIN = "mod_equipment"
INSTANCE = "inst_equipment_001"
BUNDLE_DIGEST = "sha256:" + "11" * 32
BACKEND_DIGEST = "sha256:" + "22" * 32
NEXT_BUNDLE_DIGEST = "sha256:" + "33" * 32
NEXT_BACKEND_DIGEST = "sha256:" + "44" * 32
UPDATE_OWNER = "update-owner-a"
BUNDLE_REF = f"ghcr.io/ne-tort/plugins/mod-equipment@{BUNDLE_DIGEST}"
BACKEND_REF = f"ghcr.io/ne-tort/plugins/mod-equipment-backend@{BACKEND_DIGEST}"
NEXT_BACKEND_REF = f"ghcr.io/ne-tort/plugins/mod-equipment-backend@{NEXT_BACKEND_DIGEST}"
DSN_REF = "file://ai_keys/plugins/tenantdb/co_123/mod_equipment.secret"
DSN_SENTINEL = "dsn-value-sentinel"
GATEWAY_SENTINEL = "gateway-value-sentinel"
WEBHOOK_SENTINEL = "webhook-value-sentinel"
WEBHOOK_REF = "file://ai_keys/plugins/webhooks/co_123/mod_equipment/inst_equipment_001.secret"
CAPABILITY_REF = (
    "vault://ai_keys/plugins/capabilities/secrets/"
    f"{COMPANY}/{PLUGIN}/{INSTANCE}/api-key"
)
CAPABILITY_OTHER_REF = (
    "vault://ai_keys/plugins/capabilities/secrets/"
    f"{COMPANY}/{PLUGIN}/{INSTANCE}/other-key"
)
CAPABILITY_PLAINTEXT = "capability-plaintext-sentinel"
CAPABILITY_VALUE_DIGEST = "sha256:" + hashlib.sha256(
    CAPABILITY_PLAINTEXT.encode("utf-8")
).hexdigest()


def _response(body: dict, status_code: int = 200) -> K8sRawResponse:
    return K8sRawResponse(status_code=status_code, body=copy.deepcopy(body))


def _merge(current: dict, patch: dict) -> dict:
    result = copy.deepcopy(current)
    for key, value in patch.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


class StatefulFakeK8s:
    """Small scoped Kubernetes REST model with conflict/error injection."""

    scoped_identity = True

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str, str], dict] = {}
        self.pods: list[dict] = []
        self.calls: list[dict] = []
        self.conflicts: dict[tuple[str, str], int] = {}
        self.errors: dict[tuple[str, str], Exception] = {}
        self.race_posts: set[str] = set()

    def put(self, plural: str, body: dict) -> None:
        metadata = body["metadata"]
        self.objects[(plural, metadata["namespace"], metadata["name"])] = copy.deepcopy(body)

    def get(self, plural: str, name: str, namespace: str = NAMESPACE) -> dict:
        return self.objects[(plural, namespace, name)]

    @staticmethod
    def _parse(path: str) -> tuple[str, str, str | None, bool, str]:
        raw_path, _, query = path.partition("?")
        scale = raw_path.endswith("/scale")
        if scale:
            raw_path = raw_path.removesuffix("/scale")
        match = re.fullmatch(
            r"/(?:apis/apps/v1|api/v1)/namespaces/([^/]+)/([^/]+)(?:/([^/]+))?",
            raw_path,
        )
        if match is None:
            raise AssertionError(f"unexpected fake k8s path: {path}")
        namespace, plural, name = match.groups()
        return namespace, plural, name, scale, query

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        content_type: str | None = None,
    ) -> K8sRawResponse:
        method = method.upper()
        namespace, plural, name, scale, query = self._parse(path)
        raw_path = path.partition("?")[0]
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": copy.deepcopy(body),
                "content_type": content_type,
            }
        )
        error = self.errors.get((method, raw_path))
        if error is not None:
            raise error
        key = (plural, namespace, name or "")
        conflict_key = (method, raw_path)
        if self.conflicts.get(conflict_key, 0) > 0:
            self.conflicts[conflict_key] -= 1
            existing = self.objects.get(key)
            if existing is not None:
                metadata = existing.setdefault("metadata", {})
                metadata["resourceVersion"] = str(int(metadata.get("resourceVersion", "1")) + 1)
            raise K8sApiConflict("injected conflict")

        if method == "GET" and plural == "pods" and name is None:
            selector = parse_qs(query).get("labelSelector", [""])[0]
            expected_key, _, expected_value = selector.partition("=")
            items = [
                copy.deepcopy(pod)
                for pod in self.pods
                if pod.get("metadata", {}).get("namespace") == namespace
                and pod.get("metadata", {}).get("labels", {}).get(expected_key) == expected_value
            ]
            return _response({"apiVersion": "v1", "kind": "PodList", "items": items})

        existing = self.objects.get(key)
        if method == "GET":
            if scale:
                if existing is None:
                    raise K8sApiNotFound("scale absent")
                return _response(
                    {
                        "apiVersion": "autoscaling/v1",
                        "kind": "Scale",
                        "metadata": copy.deepcopy(existing["metadata"]),
                        "spec": {"replicas": existing["spec"]["replicas"]},
                        "status": {"replicas": existing.get("status", {}).get("replicas", 0)},
                    }
                )
            if existing is None:
                raise K8sApiNotFound("object absent")
            return _response(existing)

        if method == "POST":
            if name is not None or body is None:
                raise AssertionError("invalid fake create")
            resource_name = body["metadata"]["name"]
            key = (plural, namespace, resource_name)
            if key in self.objects:
                raise K8sApiConflict("already exists")
            created = copy.deepcopy(body)
            metadata = created.setdefault("metadata", {})
            metadata["namespace"] = namespace
            metadata["resourceVersion"] = "1"
            if plural == "deployments":
                created.setdefault("status", {})
            self.objects[key] = created
            if plural in self.race_posts:
                raise K8sApiConflict("parallel create won")
            return _response(created, 201)

        if existing is None:
            raise K8sApiNotFound("object absent")
        if method == "PATCH" and body is not None:
            if scale:
                expected_version = body.get("metadata", {}).get("resourceVersion")
                actual_version = existing.get("metadata", {}).get("resourceVersion")
                if expected_version is not None and expected_version != actual_version:
                    raise K8sApiConflict("scale resourceVersion mismatch")
                existing["spec"]["replicas"] = body["spec"]["replicas"]
                return _response(
                    {
                        "apiVersion": "autoscaling/v1",
                        "kind": "Scale",
                        "metadata": copy.deepcopy(existing["metadata"]),
                        "spec": {"replicas": existing["spec"]["replicas"]},
                        "status": {"replicas": existing.get("status", {}).get("replicas", 0)},
                    }
                )
            expected_version = body.get("metadata", {}).get("resourceVersion")
            actual_version = existing.get("metadata", {}).get("resourceVersion")
            if expected_version is not None and expected_version != actual_version:
                raise K8sApiConflict("resourceVersion mismatch")
            updated = _merge(existing, body)
            metadata = updated.setdefault("metadata", {})
            metadata["resourceVersion"] = str(int(actual_version or "1") + 1)
            self.objects[key] = updated
            return _response(updated)

        if method == "DELETE":
            expected_version = (body or {}).get("preconditions", {}).get("resourceVersion")
            actual_version = existing.get("metadata", {}).get("resourceVersion")
            if expected_version is not None and expected_version != actual_version:
                raise K8sApiConflict("delete resourceVersion mismatch")
            del self.objects[key]
            return _response({"apiVersion": "v1", "kind": "Status", "status": "Success"})
        raise AssertionError(f"unsupported fake operation: {method} {path}")


class FakeArtifactStore:
    def __init__(self) -> None:
        self.records: dict[str, PluginArtifactRecord] = {}

    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None:
        return self.records.get(digest)


class FakeInstanceStore:
    def __init__(self) -> None:
        self.records: dict[str, SimpleNamespace] = {}

    def add(
        self,
        instance_id: str = INSTANCE,
        *,
        company_id: str = COMPANY,
        artifact_digest: str = BUNDLE_DIGEST,
    ) -> SimpleNamespace:
        record = SimpleNamespace(
            plugin_instance_id=instance_id,
            company_id=company_id,
            plugin_id=PLUGIN,
            manifest_version="1.2.3",
            artifact_digest=artifact_digest,
            status=PluginInstanceStatus.ACTIVE,
        )
        self.records[instance_id] = record
        return record

    async def get(self, *, company_id: str, plugin_id: str) -> SimpleNamespace | None:
        return next(
            (
                record
                for record in self.records.values()
                if record.company_id == company_id and record.plugin_id == plugin_id
            ),
            None,
        )

    async def get_by_id(self, *, plugin_instance_id: str) -> SimpleNamespace | None:
        return self.records.get(plugin_instance_id)


def _artifact(
    bundle_digest: str = BUNDLE_DIGEST,
    backend_digest: str = BACKEND_DIGEST,
    backend_ref: str = BACKEND_REF,
    *,
    manifest: dict | None = None,
) -> PluginArtifactRecord:
    now = datetime.now(UTC)
    bundle_ref = (
        BUNDLE_REF
        if bundle_digest == BUNDLE_DIGEST
        else f"ghcr.io/ne-tort/plugins/mod-equipment@{bundle_digest}"
    )
    fingerprint = hashlib.sha256(bundle_digest.encode()).hexdigest()
    return PluginArtifactRecord(
        plugin_id=PLUGIN,
        version="1.2.3",
        digest=bundle_digest,
        image_ref=bundle_ref,
        manifest=(
            manifest
            if manifest is not None
            else {"backend": {"port": 8080, "health": "/health"}}
        ),
        manifest_fingerprint=fingerprint,
        status=AppLifecycleStatus.PUBLISHED,
        outcome=RegistryOutcome.APPLIED,
        quarantine=None,
        published_at=now,
        created_at=now,
        verification=VerificationEvidence(
            image_ref=bundle_ref,
            artifact_digest=bundle_digest,
            manifest_fingerprint=fingerprint,
            identity="test-identity",
            issuer="test-issuer",
            sbom_verified=True,
            provenance_verified=True,
            observed_at=now,
            outcome=RegistryOutcome.APPLIED,
            quarantine=None,
            seal=object(),
            scan_verified=True,
            csp_verified=True,
        ),
        publication=PublicationMetadata(
            source_id="agentscale/plugins",
            commit_sha="a" * 40,
            backend_digest=backend_digest,
            backend_image_ref=backend_ref,
            run_url="https://example.invalid/run/1",
        ),
    )


def _secret(
    *,
    company_id: str = COMPANY,
    instance_id: str = INSTANCE,
    dsn_ref: str = DSN_REF,
    plugin_id: str = PLUGIN,
) -> dict:
    name = plugin_resource_name(plugin_id, instance_id)
    values = {
        "TENANT_DB_DSN": DSN_SENTINEL,
        "GATEWAY_URL": GATEWAY_SENTINEL,
        "PLUGIN_ID": plugin_id,
        "WEBHOOK_SECRET": WEBHOOK_SENTINEL,
    }
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "resourceVersion": "1",
            "labels": _labels(company_id, plugin_id, instance_id),
            "annotations": {
                DSN_REF_ANNOTATION: hashlib.sha256(dsn_ref.encode()).hexdigest()
            },
        },
        "data": {
            key: base64.b64encode(value.encode()).decode()
            for key, value in values.items()
        },
    }


def _delivery_manifest(
    refs: tuple[str, ...] = (CAPABILITY_REF, CAPABILITY_OTHER_REF),
) -> dict:
    return {
        "backend": {"port": 8080, "health": "/health"},
        "secret_delivery": {"mode": "k8s_secret", "allowlist": list(refs)},
    }


def _delivery_secret(
    *,
    company_id: str = COMPANY,
    instance_id: str = INSTANCE,
    refs: tuple[str, ...] = (CAPABILITY_REF, CAPABILITY_OTHER_REF),
) -> dict:
    name = plugin_resource_name(
        PLUGIN, instance_id, prefix="plugin-" + DELIVERY_SECRET_PREFIX
    )
    values = {
        ref: CAPABILITY_PLAINTEXT if index == 0 else f"other-capability-value-{index}"
        for index, ref in enumerate(refs)
    }
    keys = {
        ref: "SECRET_" + hashlib.sha256(ref.encode()).hexdigest().upper()
        for ref in refs
    }
    return {
        "apiVersion": "v1",
        "kind": "Secret",
        "metadata": {
            "name": name,
            "namespace": NAMESPACE,
            "resourceVersion": "1",
            "labels": _labels(company_id, PLUGIN, instance_id),
            "annotations": {
                "agentscale.ne-tort/delivered-secret-versions": json.dumps({
                    keys[ref]: "sha256:" + hashlib.sha256(
                        values[ref].encode("utf-8")
                    ).hexdigest()
                    for ref in refs
                }),
            },
        },
        "type": "Opaque",
        "data": {
            keys[ref]: base64.b64encode(value.encode()).decode()
            for ref, value in values.items()
        },
    }


class FakeSecretProjection:
    """Delegated projection fake; DeploymentAdmin never reads source DSN."""

    def __init__(self, api: StatefulFakeK8s) -> None:
        self.api = api
        self.calls: list[dict] = []
        self.enabled = True
        self.delivery_calls: list[dict] = []
        self.delivery_api_positions: list[int] = []
        self.delivery_enabled = True
        self.foreign_revoke_labels = False

    async def ensure_instance_secret(self, **kwargs) -> SecretReference:
        self.calls.append(copy.deepcopy(kwargs))
        if self.enabled:
            self.api.put(
                "secrets",
                _secret(
                    company_id=kwargs["company_id"],
                    instance_id=kwargs["plugin_instance_id"],
                    dsn_ref=kwargs["dsn_ref"],
                    plugin_id=kwargs["plugin_id"],
                ),
            )
        instance_id = kwargs["plugin_instance_id"]
        return SecretReference(
            name=plugin_resource_name(kwargs["plugin_id"], instance_id),
            namespace=NAMESPACE,
            labels=_labels(kwargs["company_id"], kwargs["plugin_id"], instance_id),
            dsn_ref_sha256=hashlib.sha256(kwargs["dsn_ref"].encode()).hexdigest(),
        )

    def _delivery_name(self, plugin_id: str, instance_id: str) -> str:
        return plugin_resource_name(
            plugin_id,
            instance_id,
            prefix="plugin-" + DELIVERY_SECRET_PREFIX,
        )

    async def revoke_secret(self, **kwargs) -> SecretDeliveryReference:
        self.delivery_calls.append(copy.deepcopy(kwargs))
        self.delivery_api_positions.append(len(self.api.calls))
        plugin_id = kwargs["plugin_id"]
        instance_id = kwargs["plugin_instance_id"]
        name = self._delivery_name(plugin_id, instance_id)
        key = "SECRET_" + hashlib.sha256(kwargs["secret_ref"].encode()).hexdigest().upper()
        labels = _labels(kwargs["company_id"], plugin_id, instance_id)
        if self.foreign_revoke_labels:
            labels = {**labels, COMPANY_LABEL: "f" * 40}
        if self.delivery_enabled:
            existing = self.api.objects.get(("secrets", NAMESPACE, name))
            if existing is not None:
                existing.setdefault("data", {}).pop(key, None)
                annotations = existing["metadata"].setdefault("annotations", {})
                annotation = "agentscale.ne-tort/delivered-secret-versions"
                versions = json.loads(annotations[annotation])
                versions.pop(key, None)
                annotations[annotation] = json.dumps(versions)
        return SecretDeliveryReference(
            name=name,
            namespace=NAMESPACE,
            key=key,
            labels=labels,
            value_digest=CAPABILITY_VALUE_DIGEST,
            version=CAPABILITY_VALUE_DIGEST,
            delivered=False,
        )


def _harness(*, image_pull_secret: str | None = None):
    api = StatefulFakeK8s()
    artifacts = FakeArtifactStore()
    artifacts.records[BUNDLE_DIGEST] = _artifact()
    instances = FakeInstanceStore()
    instance = instances.add()
    api.put("secrets", _secret())
    config = RuntimeConfig(
        namespace=NAMESPACE,
        cpu_request="50m",
        memory_request="96Mi",
        cpu_limit="400m",
        memory_limit="384Mi",
        image_pull_secret=image_pull_secret,
    )
    projection = FakeSecretProjection(api)
    admin = K8sDeploymentAdmin(
        config=config,
        artifact_store=artifacts,
        instance_store=instances,
        secret_projection=projection,
        api=api,
    )
    return admin, api, artifacts, instances, instance, config


def _kwargs(digest: str = BUNDLE_DIGEST, instance_id: str = INSTANCE) -> dict:
    return {
        "company_id": COMPANY,
        "plugin_id": PLUGIN,
        "plugin_instance_id": instance_id,
        "artifact_digest": digest,
        "dsn_ref": DSN_REF,
        "webhook_secret_ref": WEBHOOK_REF,
    }


def _update_kwargs(
    digest: str = NEXT_BUNDLE_DIGEST,
    *,
    expected_current: str = BUNDLE_DIGEST,
    update_owner: str | None = UPDATE_OWNER,
    expected_owner: str | None = None,
) -> dict:
    return {
        **_kwargs(digest),
        "expected_current_artifact_digest": expected_current,
        "update_owner_token": update_owner,
        "expected_current_update_owner_token": expected_owner,
    }


def _identity_volume() -> dict:
    return {
        "name": RUNTIME_IDENTITY_VOLUME_NAME,
        "downwardAPI": {
            "items": [
                {
                    "path": "plugin_instance_id",
                    "fieldRef": {
                        "fieldPath": (
                            f"metadata.annotations['{PLUGIN_INSTANCE_ANNOTATION}']"
                        )
                    },
                },
                {
                    "path": "backend_digest",
                    "fieldRef": {
                        "fieldPath": f"metadata.annotations['{BACKEND_ANNOTATION}']"
                    },
                },
            ]
        },
    }


def _identity_mount() -> dict:
    return {
        "name": RUNTIME_IDENTITY_VOLUME_NAME,
        "mountPath": RUNTIME_IDENTITY_MOUNT_PATH,
        "readOnly": True,
    }


def _tmp_volume() -> dict:
    return {"name": RUNTIME_TMP_VOLUME_NAME, "emptyDir": {}}


def _tmp_mount() -> dict:
    return {
        "name": RUNTIME_TMP_VOLUME_NAME,
        "mountPath": "/tmp",
        "readOnly": False,
    }


def _projected_identity(deployment: dict) -> dict[str, str]:
    """Resolve the declared DownwardAPI items against the fake pod template."""

    template = deployment["spec"]["template"]
    annotations = template["metadata"]["annotations"]
    volume = next(
        volume
        for volume in template["spec"]["volumes"]
        if volume["name"] == RUNTIME_IDENTITY_VOLUME_NAME
    )
    projected = {}
    for item in volume["downwardAPI"]["items"]:
        match = re.fullmatch(
            r"metadata\.annotations\['([^']+)'\]",
            item["fieldRef"]["fieldPath"],
        )
        assert match is not None
        projected[item["path"]] = annotations[match.group(1)]
    return projected


async def test_ensure_creates_verified_backend_and_instance_scoped_resources() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    service = api.get("services", name)
    container = deployment["spec"]["template"]["spec"]["containers"][0]

    assert name == plugin_resource_name(PLUGIN, INSTANCE)
    assert deployment["spec"]["replicas"] == 0
    assert container["image"] == BACKEND_REF
    assert BUNDLE_DIGEST not in container["image"]
    assert deployment["metadata"]["annotations"][BACKEND_ANNOTATION] == BACKEND_DIGEST
    assert service["spec"]["selector"] == {
        INSTANCE_LABEL: deployment["spec"]["selector"]["matchLabels"][INSTANCE_LABEL]
    }
    assert len(service["spec"]["selector"]) == 1
    assert container["resources"] == {
        "requests": {"cpu": "50m", "memory": "96Mi"},
        "limits": {"cpu": "400m", "memory": "384Mi"},
    }
    assert container["readinessProbe"]["httpGet"] == {
        "path": "/health/ready", "port": "http", "scheme": "HTTP"
    }
    assert container["livenessProbe"]["httpGet"] == {
        "path": "/health/live", "port": "http", "scheme": "HTTP"
    }
    assert container["securityContext"] == {
        "allowPrivilegeEscalation": False,
        "readOnlyRootFilesystem": True,
        "capabilities": {"drop": ["ALL"]},
    }
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["automountServiceAccountToken"] is False
    assert "imagePullSecrets" not in pod_spec
    assert pod_spec["volumes"] == [_identity_volume(), _tmp_volume()]
    assert container["volumeMounts"] == [_identity_mount(), _tmp_mount()]
    assert "initContainers" not in pod_spec
    assert {item["name"] for item in container["env"]} == {
        "TENANT_DB_DSN",
        "GATEWAY_URL",
        "PLUGIN_ID",
        "WEBHOOK_SECRET",
        "HOME",
        "TMPDIR",
        "PLUGIN_ARTIFACT_DIGEST",
    }
    assert all(
        "value" not in item
        for item in container["env"]
        if item["name"] not in {"HOME", "TMPDIR"}
    )
    secret_env = [
        item for item in container["env"]
        if "valueFrom" in item and "secretKeyRef" in item["valueFrom"]
    ]
    assert len(secret_env) == 4
    assert all(
        item["valueFrom"]["secretKeyRef"]["name"] == name
        for item in secret_env
    )
    assert set(admin.secret_projection.calls[0]) == {
        "company_id", "plugin_id", "plugin_instance_id", "dsn_ref", "webhook_secret_ref"
    }
    assert admin.secret_projection.calls[0]["webhook_secret_ref"] == WEBHOOK_REF
    serialized_calls = repr(api.calls)
    assert DSN_SENTINEL not in serialized_calls
    assert GATEWAY_SENTINEL not in serialized_calls
    assert WEBHOOK_SENTINEL not in serialized_calls


async def test_backend_tmp_writable_mount_and_migration_startup_probe() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]

    assert container["startupProbe"] == {
        "httpGet": {"path": "/health/live", "port": "http", "scheme": "HTTP"},
        "timeoutSeconds": 2,
        "periodSeconds": 5,
        "failureThreshold": 60,
    }
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["livenessProbe"]["initialDelaySeconds"] == 10
    assert container["readinessProbe"]["periodSeconds"] == 5
    assert container["readinessProbe"]["timeoutSeconds"] <= 5
    assert pod_spec["volumes"] == [_identity_volume(), _tmp_volume()]
    assert container["volumeMounts"] == [_identity_mount(), _tmp_mount()]
    assert container["volumeMounts"][1]["readOnly"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert pod_spec["securityContext"] == {
        "runAsNonRoot": True,
        "runAsUser": 10001,
        "runAsGroup": 10001,
        "fsGroup": 10001,
        "seccompProfile": {"type": "RuntimeDefault"},
    }
    environment = {item["name"]: item for item in container["env"]}
    assert environment["HOME"] == {"name": "HOME", "value": "/tmp"}
    assert environment["TMPDIR"] == {"name": "TMPDIR", "value": "/tmp"}
    assert pod_spec["automountServiceAccountToken"] is False
    assert "initContainers" not in pod_spec
    assert "imagePullSecrets" not in pod_spec


async def test_artifact_digest_env_is_downward_ref_to_template_annotation() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    pod_template = deployment["spec"]["template"]
    annotations = pod_template["metadata"]["annotations"]
    container = pod_template["spec"]["containers"][0]
    digest_env = next(
        item for item in container["env"] if item["name"] == "PLUGIN_ARTIFACT_DIGEST"
    )

    assert digest_env == {
        "name": "PLUGIN_ARTIFACT_DIGEST",
        "valueFrom": {
            "fieldRef": {
                "fieldPath": f"metadata.annotations['{ARTIFACT_ANNOTATION}']"
            }
        },
    }
    # The env contains only a pointer, not a second digest literal that could
    # diverge from the artifact annotation during old→new updates.
    assert BUNDLE_DIGEST not in repr(digest_env)
    assert annotations[ARTIFACT_ANNOTATION] == BUNDLE_DIGEST
    assert annotations[BACKEND_ANNOTATION] == BACKEND_DIGEST
    assert annotations[ARTIFACT_ANNOTATION] != annotations[BACKEND_ANNOTATION]
    assert container["image"] == BACKEND_REF
    assert pod_template["spec"]["automountServiceAccountToken"] is False
    secret = api.get("secrets", name)
    assert "PLUGIN_ARTIFACT_DIGEST" not in secret["data"]


async def test_runtime_identity_projection_is_exact_non_secret_and_fail_closed() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    template = deployment["spec"]["template"]
    pod_spec = template["spec"]
    container = pod_spec["containers"][0]
    annotations = template["metadata"]["annotations"]

    assert PLUGIN_INSTANCE_ANNOTATION == "agentscale.ne-tort/plugin-instance-id"
    assert RUNTIME_IDENTITY_VOLUME_NAME == "plugin-runtime-identity"
    assert RUNTIME_IDENTITY_MOUNT_PATH == "/etc/agentscale/plugin-runtime"
    assert annotations == {
        ARTIFACT_ANNOTATION: BUNDLE_DIGEST,
        BACKEND_ANNOTATION: BACKEND_DIGEST,
        PLUGIN_INSTANCE_ANNOTATION: INSTANCE,
    }
    assert pod_spec["volumes"] == [_identity_volume(), _tmp_volume()]
    assert container["volumeMounts"] == [_identity_mount(), _tmp_mount()]
    assert _projected_identity(deployment) == {
        "plugin_instance_id": INSTANCE,
        "backend_digest": BACKEND_DIGEST,
    }
    assert BACKEND_DIGEST != BUNDLE_DIGEST
    assert pod_spec["automountServiceAccountToken"] is False
    assert "initContainers" not in pod_spec
    assert "command" not in container and "args" not in container
    secret_env = [
        item for item in container["env"]
        if "valueFrom" in item and "secretKeyRef" in item["valueFrom"]
    ]
    assert len(secret_env) == 4
    assert {item["name"] for item in secret_env} == {
        "TENANT_DB_DSN", "GATEWAY_URL", "PLUGIN_ID", "WEBHOOK_SECRET"
    }
    secret_objects = [
        body for (plural, _, _), body in api.objects.items() if plural == "secrets"
    ]
    assert [body["metadata"]["name"] for body in secret_objects] == [name]
    assert set(secret_objects[0]["data"]) == {
        "TENANT_DB_DSN", "GATEWAY_URL", "PLUGIN_ID", "WEBHOOK_SECRET"
    }
    assert DSN_SENTINEL not in repr(deployment)


async def test_absent_runtime_identity_projection_is_typed_manifest_failure(
    monkeypatch,
) -> None:
    admin, api, _, _, _, _ = _harness()
    monkeypatch.setattr(admin, "_runtime_identity_projection", lambda: None)

    with pytest.raises(DeploymentConfigurationError) as caught:
        await admin.ensure_deployment(**_kwargs())

    assert caught.value.code == "DEPLOYMENT_CONFIG_INVALID"
    assert caught.value.operation == "ensure"
    assert "runtime identity projection" in str(caught.value)
    assert not any(plural == "deployments" for plural, _, _ in api.objects)


async def test_delivery_declaration_mounts_only_canonical_secret_without_plaintext(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())

    name = await admin.ensure_deployment(**_kwargs())

    assert admin.secret_delivery_projection is admin.secret_projection
    deployment = api.get("deployments", name)
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    secret_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    assert secret_name == plugin_resource_name(
        PLUGIN, INSTANCE, prefix="plugin-" + DELIVERY_SECRET_PREFIX
    )
    assert len(pod_spec["volumes"]) == 3
    assert pod_spec["volumes"][0] == _identity_volume()
    assert pod_spec["volumes"][1] == _tmp_volume()
    volume = pod_spec["volumes"][2]
    assert volume["secret"] == {
        "secretName": secret_name,
        "defaultMode": DELIVERY_DEFAULT_MODE,
    }
    assert volume["secret"]["defaultMode"] == 0o400
    assert "items" not in volume["secret"]
    expected_volume_name = DELIVERY_VOLUME_PREFIX + hashlib.sha256(
        f"{COMPANY}\0{PLUGIN}\0{INSTANCE}".encode()
    ).hexdigest()[:24]
    assert volume["name"] == expected_volume_name
    assert volume["name"].startswith(DELIVERY_VOLUME_PREFIX)
    assert len(volume["name"]) <= 63
    assert re.fullmatch(r"[a-z](?:[-a-z0-9]*[a-z0-9])?", volume["name"])
    assert container["volumeMounts"] == [
        _identity_mount(),
        _tmp_mount(),
        {
            "name": volume["name"],
            "mountPath": DELIVERY_MOUNT_PATH,
            "readOnly": True,
        },
    ]
    secret_env = [
        item for item in container["env"]
        if "valueFrom" in item and "secretKeyRef" in item["valueFrom"]
    ]
    assert {item["name"] for item in secret_env} == {
        "TENANT_DB_DSN", "GATEWAY_URL", "PLUGIN_ID", "WEBHOOK_SECRET"
    }
    assert len(secret_env) == 4
    assert all(
        item["valueFrom"]["secretKeyRef"]["name"] == name
        for item in secret_env
    )
    assert all(item["valueFrom"]["secretKeyRef"]["key"] in {
        "TENANT_DB_DSN", "GATEWAY_URL", "PLUGIN_ID", "WEBHOOK_SECRET"
    } for item in secret_env)
    assert all(
        "value" not in item
        for item in container["env"]
        if item["name"] not in {"HOME", "TMPDIR"}
    )
    surfaces = [repr(deployment), repr(api.calls), caplog.text]
    for surface in surfaces:
        assert CAPABILITY_PLAINTEXT not in surface
        assert base64.b64encode(CAPABILITY_PLAINTEXT.encode()).decode() not in surface


async def test_delivery_mount_requires_present_owned_secret() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    with pytest.raises(DeploymentSecretError, match="delivery Secret is absent"):
        await admin.ensure_deployment(**_kwargs())
    assert not any(plural == "deployments" for plural, _, _ in api.objects)

    foreign = _delivery_secret()
    foreign["metadata"]["labels"][COMPANY_LABEL] = "f" * 40
    api.put("secrets", foreign)
    with pytest.raises(DeploymentOwnershipError):
        await admin.ensure_deployment(**_kwargs())
    assert not any(plural == "deployments" for plural, _, _ in api.objects)


async def test_delivery_mount_rejects_value_digest_mismatch() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    api.put("secrets", _delivery_secret())
    secret = api.get("secrets", delivery_name)
    annotations = secret["metadata"]["annotations"]
    versions = json.loads(annotations["agentscale.ne-tort/delivered-secret-versions"])
    versions[admin._delivery_key(CAPABILITY_REF)] = (
        "sha256:" + hashlib.sha256(b"not-the-projected-value").hexdigest()
    )
    annotations["agentscale.ne-tort/delivered-secret-versions"] = json.dumps(versions)

    with pytest.raises(DeploymentSecretError, match="digest does not match"):
        await admin.ensure_deployment(**_kwargs())
    assert not any(plural == "deployments" for plural, _, _ in api.objects)


async def test_delivery_mount_rejects_stale_unapproved_keys() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    api.put("secrets", _delivery_secret())
    secret = api.get("secrets", delivery_name)
    stale_key = "SECRET_" + "F" * 64
    secret["data"][stale_key] = base64.b64encode(b"stale-value").decode()
    annotations = secret["metadata"]["annotations"]
    versions = json.loads(annotations["agentscale.ne-tort/delivered-secret-versions"])
    versions[stale_key] = "sha256:" + hashlib.sha256(b"stale-value").hexdigest()
    annotations["agentscale.ne-tort/delivered-secret-versions"] = json.dumps(versions)

    with pytest.raises(DeploymentSecretError, match="does not match approved refs"):
        await admin.ensure_deployment(**_kwargs())
    assert not any(plural == "deployments" for plural, _, _ in api.objects)


async def test_configured_image_pull_secret_is_only_a_local_object_name() -> None:
    admin, api, _, _, _, _ = _harness(image_pull_secret="plugin-ghcr-pull")
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    pod_spec = deployment["spec"]["template"]["spec"]

    assert pod_spec["imagePullSecrets"] == [{"name": "plugin-ghcr-pull"}]
    assert set(pod_spec["imagePullSecrets"][0]) == {"name"}
    serialized = repr(deployment)
    assert "dockerconfigjson" not in serialized
    assert "registry_read" not in serialized
    assert all(
        env.get("name") != "PLUGIN_RUNTIME_IMAGE_PULL_SECRET"
        for container in pod_spec["containers"]
        for env in container.get("env", [])
    )


async def test_grant_replay_is_read_only_and_preserves_active_replicas() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    api.get("deployments", name)["spec"]["replicas"] = 1
    api.calls.clear()

    assert await admin.ensure_deployment(**_kwargs()) == name
    assert api.get("deployments", name)["spec"]["replicas"] == 1
    assert len(admin.secret_projection.calls) == 2
    assert [call for call in api.calls if call["method"] != "GET"] == []


async def test_replay_removes_service_selector_broadening() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    service = api.get("services", name)
    service["spec"]["selector"]["agentscale.ne-tort/other-instance"] = "foreign"

    assert await admin.ensure_deployment(**_kwargs()) == name
    service = api.get("services", name)
    assert service["spec"]["selector"] == {
        INSTANCE_LABEL: service["spec"]["selector"][INSTANCE_LABEL]
    }


async def test_update_patches_backend_digest_without_scaling_down() -> None:
    admin, api, artifacts, _, instance, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    deployment["spec"]["replicas"] = 1
    assert _projected_identity(deployment) == {
        "plugin_instance_id": INSTANCE,
        "backend_digest": BACKEND_DIGEST,
    }
    artifacts.records[NEXT_BUNDLE_DIGEST] = replace(
        _artifact(NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF),
        version="2.0.0",
    )

    assert await admin.update_deployment(**_update_kwargs()) == name
    deployment = api.get("deployments", name)
    template = deployment["spec"]["template"]
    container = template["spec"]["containers"][0]
    annotations = template["metadata"]["annotations"]
    assert container["image"] == NEXT_BACKEND_REF
    assert deployment["spec"]["replicas"] == 1
    assert annotations[BACKEND_ANNOTATION] == NEXT_BACKEND_DIGEST
    assert annotations[ARTIFACT_ANNOTATION] == NEXT_BUNDLE_DIGEST
    assert annotations[PLUGIN_INSTANCE_ANNOTATION] == INSTANCE
    assert _projected_identity(deployment) == {
        "plugin_instance_id": INSTANCE,
        "backend_digest": NEXT_BACKEND_DIGEST,
    }
    assert _projected_identity(deployment)["backend_digest"] != NEXT_BUNDLE_DIGEST
    assert instance.artifact_digest == BUNDLE_DIGEST
    assert container["readinessProbe"]["httpGet"]["path"] == "/health/ready"
    assert container["livenessProbe"]["httpGet"]["path"] == "/health/live"
    assert container["startupProbe"]["failureThreshold"] == 60


async def test_update_without_declaration_removes_only_delivery_volume_and_mount() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    name = await admin.ensure_deployment(**_kwargs())
    pod_spec = api.get("deployments", name)["spec"]["template"]["spec"]
    pod_spec["volumes"].append({"name": "workspace", "emptyDir": {}})
    pod_spec["containers"][0]["volumeMounts"].append({
        "name": "workspace", "mountPath": "/workspace"
    })
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )

    assert await admin.update_deployment(**_update_kwargs()) == name

    pod_spec = api.get("deployments", name)["spec"]["template"]["spec"]
    assert pod_spec["volumes"] == [
        _identity_volume(),
        _tmp_volume(),
        {"name": "workspace", "emptyDir": {}},
    ]
    assert pod_spec["containers"][0]["volumeMounts"] == [
        _identity_mount(),
        _tmp_mount(),
        {"name": "workspace", "mountPath": "/workspace"},
    ]
    assert not any(
        volume["name"].startswith(DELIVERY_VOLUME_PREFIX)
        for volume in pod_spec["volumes"]
    )


async def test_update_preserves_identity_delivery_and_sibling_mounts() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    name = await admin.ensure_deployment(**_kwargs())
    pod_spec = api.get("deployments", name)["spec"]["template"]["spec"]
    delivery_volume = copy.deepcopy(pod_spec["volumes"][2])
    delivery_mount = copy.deepcopy(pod_spec["containers"][0]["volumeMounts"][2])
    workspace_volume = {"name": "workspace", "emptyDir": {}}
    workspace_mount = {"name": "workspace", "mountPath": "/workspace"}
    pod_spec["volumes"].append(workspace_volume)
    pod_spec["containers"][0]["volumeMounts"].append(workspace_mount)
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST,
        NEXT_BACKEND_DIGEST,
        NEXT_BACKEND_REF,
        manifest=_delivery_manifest(),
    )

    assert await admin.update_deployment(**_update_kwargs()) == name

    deployment = api.get("deployments", name)
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["volumes"] == [
        _identity_volume(),
        _tmp_volume(),
        delivery_volume,
        workspace_volume,
    ]
    assert pod_spec["containers"][0]["volumeMounts"] == [
        _identity_mount(),
        _tmp_mount(),
        delivery_mount,
        workspace_mount,
    ]
    assert _projected_identity(deployment)["backend_digest"] == NEXT_BACKEND_DIGEST


async def test_update_rejects_wrong_expected_current_pin_before_mutation() -> None:
    admin, api, artifacts, _, instance, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    api.calls.clear()
    admin.secret_projection.calls.clear()

    with pytest.raises(DeploymentOwnershipError):
        await admin.update_deployment(
            **_update_kwargs(expected_current=NEXT_BUNDLE_DIGEST)
        )
    assert api.calls == []
    assert admin.secret_projection.calls == []
    assert instance.artifact_digest == BUNDLE_DIGEST
    assert api.get("deployments", name)["spec"]["template"]["spec"]["containers"][0]["image"] == BACKEND_REF


async def test_update_rejects_backend_bundle_mismatch_before_mutation() -> None:
    admin, api, artifacts, _, instance, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    new_record = _artifact(NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF)
    assert new_record.publication is not None
    artifacts.records[NEXT_BUNDLE_DIGEST] = replace(
        new_record,
        publication=replace(new_record.publication, backend_image_ref=BUNDLE_REF),
    )
    api.calls.clear()
    admin.secret_projection.calls.clear()

    with pytest.raises(ArtifactProvenanceError):
        await admin.update_deployment(**_update_kwargs())

    assert instance.artifact_digest == BUNDLE_DIGEST
    assert admin.secret_projection.calls == []
    assert [call for call in api.calls if call["method"] != "GET"] == []
    deployment = api.get("deployments", name)
    assert deployment["spec"]["template"]["spec"]["containers"][0]["image"] == BACKEND_REF


@pytest.mark.parametrize("mode", ["missing", "mismatch", "bundle_alias"])
async def test_invalid_backend_provenance_is_rejected(mode: str) -> None:
    admin, api, artifacts, _, _, _ = _harness()
    if mode == "missing":
        artifacts.records.clear()
    else:
        record = artifacts.records[BUNDLE_DIGEST]
        publication = record.publication
        assert publication is not None
        if mode == "mismatch":
            publication = replace(publication, backend_image_ref=NEXT_BACKEND_REF)
        else:
            publication = replace(
                publication,
                backend_digest=BUNDLE_DIGEST,
                backend_image_ref=BUNDLE_REF,
            )
        artifacts.records[BUNDLE_DIGEST] = replace(record, publication=publication)

    with pytest.raises(ArtifactProvenanceError):
        await admin.ensure_deployment(**_kwargs())
    assert not any(plural in {"deployments", "services"} for plural, _, _ in api.objects)


@pytest.mark.parametrize(
    "backend",
    [
        {"port": 8081, "health": "/health"},
        {"port": 8080, "health": "/ready"},
        {"port": 8080, "health": "/health", "resources": {"cpu": "9"}},
    ],
)
async def test_unsupported_manifest_backend_override_is_rejected(backend: dict) -> None:
    admin, api, artifacts, _, _, _ = _harness()
    record = artifacts.records[BUNDLE_DIGEST]
    artifacts.records[BUNDLE_DIGEST] = replace(
        record, manifest={"backend": backend}
    )
    with pytest.raises(DeploymentConfigurationError):
        await admin.ensure_deployment(**_kwargs())
    assert not any(plural == "deployments" for plural, _, _ in api.objects)


async def test_plaintext_dsn_and_invalid_instance_secret_fail_closed(caplog) -> None:
    admin, api, _, _, _, _ = _harness()
    caplog.set_level(logging.DEBUG)
    with pytest.raises(DeploymentSecretError):
        await admin.ensure_deployment(
            **{**_kwargs(), "dsn_ref": "postgresql://user:password@example.invalid/db"}
        )
    assert not api.calls
    assert "password" not in caplog.text

    secret = api.get("secrets", plugin_resource_name(PLUGIN, INSTANCE))
    secret["data"]["PLUGIN_ID"] = base64.b64encode(b"mod_other").decode()
    admin.secret_projection.enabled = False
    with pytest.raises(DeploymentSecretError):
        await admin.ensure_deployment(**_kwargs())
    assert DSN_SENTINEL not in caplog.text
    assert GATEWAY_SENTINEL not in caplog.text


async def test_missing_webhook_secret_ref_fails_before_projection_or_k8s() -> None:
    admin, api, _, _, _, _ = _harness()
    with pytest.raises(DeploymentSecretError):
        await admin.ensure_deployment(**{**_kwargs(), "webhook_secret_ref": ""})
    assert api.calls == []
    assert admin.secret_projection.calls == []


@pytest.mark.parametrize("mode", ["missing", "invalid", "empty"])
async def test_missing_or_invalid_webhook_secret_fails_closed(mode: str, caplog) -> None:
    admin, api, _, _, _, _ = _harness()
    admin.secret_projection.enabled = False
    secret = api.get("secrets", plugin_resource_name(PLUGIN, INSTANCE))
    if mode == "missing":
        del secret["data"]["WEBHOOK_SECRET"]
    elif mode == "invalid":
        secret["data"]["WEBHOOK_SECRET"] = "not/base64!"
    else:
        secret["data"]["WEBHOOK_SECRET"] = ""
    caplog.set_level(logging.DEBUG)

    with pytest.raises(DeploymentSecretError):
        await admin.ensure_deployment(**_kwargs())
    assert not any(plural == "deployments" for plural, _, _ in api.objects)
    assert WEBHOOK_SENTINEL not in caplog.text
    assert DSN_SENTINEL not in caplog.text


async def test_runtime_status_uses_actual_deployment_and_durable_revoke_state() -> None:
    admin, api, _, _, instance, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    assert await admin.runtime_status(company_id=COMPANY, plugin_id=PLUGIN) is PluginRuntimeStatus.IDLE

    deployment["spec"]["replicas"] = 1
    deployment["status"] = {"readyReplicas": 1, "availableReplicas": 1}
    assert await admin.status(company_id=COMPANY, plugin_id=PLUGIN) is PluginRuntimeStatus.RUNNING

    deployment["spec"]["replicas"] = 0
    deployment["status"] = {}
    instance.status = PluginInstanceStatus.REVOKED
    assert await admin.runtime_status(company_id=COMPANY, plugin_id=PLUGIN) is PluginRuntimeStatus.SUSPENDED

    del api.objects[("deployments", NAMESPACE, name)]
    assert await admin.runtime_status(company_id=COMPANY, plugin_id=PLUGIN) is PluginRuntimeStatus.ABSENT


async def test_suspend_requires_zero_pods_and_resume_scales_to_activator_replica() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    assert await admin.resume(company_id=COMPANY, plugin_id=PLUGIN) == name
    deployment = api.get("deployments", name)
    assert deployment["spec"]["replicas"] == 1

    selector = deployment["spec"]["selector"]["matchLabels"]
    api.pods.append(
        {
            "metadata": {
                "name": "plugin-pod",
                "namespace": NAMESPACE,
                "labels": selector,
            }
        }
    )
    with pytest.raises(DeploymentUnavailableError, match="Pods still exist"):
        await admin.suspend(company_id=COMPANY, plugin_id=PLUGIN)
    assert deployment["spec"]["replicas"] == 0

    api.pods.clear()
    assert await admin.suspend(company_id=COMPANY, plugin_id=PLUGIN) is True
    patch_count = len([call for call in api.calls if call["method"] == "PATCH"])
    assert await admin.suspend(company_id=COMPANY, plugin_id=PLUGIN) is True
    assert len([call for call in api.calls if call["method"] == "PATCH"]) == patch_count


async def test_idle_suspend_preserves_delivery_projection_for_resume_without_redelivery(
    caplog,
) -> None:
    caplog.set_level(logging.DEBUG)
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    other_instance = "inst_equipment_other"
    sibling = _delivery_secret(instance_id=other_instance)
    api.put("secrets", sibling)
    name = await admin.ensure_deployment(**_kwargs())

    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    sibling_name = admin.canonical_delivery_secret_name(PLUGIN, other_instance)
    delivery_before = copy.deepcopy(api.get("secrets", delivery_name))
    sibling_before = copy.deepcopy(api.get("secrets", sibling_name))
    deployment = api.get("deployments", name)
    pod_spec = deployment["spec"]["template"]["spec"]
    container = pod_spec["containers"][0]
    volume = next(
        item for item in pod_spec["volumes"] if item["name"].startswith(DELIVERY_VOLUME_PREFIX)
    )
    mount = next(
        item
        for item in container["volumeMounts"]
        if item["mountPath"] == DELIVERY_MOUNT_PATH
    )
    volume_before = copy.deepcopy(volume)
    mount_before = copy.deepcopy(mount)
    env_before = copy.deepcopy(container["env"])
    projection_calls_before = copy.deepcopy(admin.secret_projection.calls)
    assert volume["secret"] == {
        "secretName": delivery_name,
        "defaultMode": DELIVERY_DEFAULT_MODE,
    }
    assert mount == {
        "name": volume["name"],
        "mountPath": DELIVERY_MOUNT_PATH,
        "readOnly": True,
    }

    deployment["spec"]["replicas"] = 1
    api.calls.clear()
    assert await admin.suspend(company_id=COMPANY, plugin_id=PLUGIN) is True
    assert deployment["spec"]["replicas"] == 0
    assert api.get("secrets", delivery_name) == delivery_before
    assert api.get("secrets", sibling_name) == sibling_before
    assert volume == volume_before
    assert mount == mount_before
    assert admin.secret_projection.delivery_calls == []
    assert admin.secret_projection.calls == projection_calls_before
    assert not any(call["method"] == "DELETE" for call in api.calls)

    assert await admin.resume(company_id=COMPANY, plugin_id=PLUGIN) == name
    assert deployment["spec"]["replicas"] == 1
    assert api.get("secrets", delivery_name) == delivery_before
    assert api.get("secrets", sibling_name) == sibling_before
    assert volume == volume_before
    assert mount == mount_before
    assert container["env"] == env_before
    assert admin.secret_projection.delivery_calls == []
    assert admin.secret_projection.calls == projection_calls_before
    assert not any(call["method"] == "DELETE" for call in api.calls)

    surfaces = [repr(deployment), repr(api.calls), caplog.text]
    encoded_plaintext = base64.b64encode(CAPABILITY_PLAINTEXT.encode()).decode()
    for surface in surfaces:
        assert CAPABILITY_PLAINTEXT not in surface
        assert encoded_plaintext not in surface


async def test_resume_rejects_revoked_instance_before_scale() -> None:
    admin, api, _, _, instance, _ = _harness()
    await admin.ensure_deployment(**_kwargs())
    instance.status = PluginInstanceStatus.REVOKED
    api.calls.clear()

    with pytest.raises(DeploymentStateError):
        await admin.resume(company_id=COMPANY, plugin_id=PLUGIN)
    assert api.calls == []


async def test_uninstall_revokes_exact_refs_before_deleting_only_owned_delivery_secret() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    other_instance = "inst_equipment_other"
    sibling = _delivery_secret(instance_id=other_instance)
    sibling_before = copy.deepcopy(sibling)
    api.put("secrets", sibling)
    name = await admin.ensure_deployment(**_kwargs())
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    sibling_name = admin.canonical_delivery_secret_name(PLUGIN, other_instance)

    assert await admin.uninstall(company_id=COMPANY, plugin_id=PLUGIN) is True

    assert [call["secret_ref"] for call in admin.secret_projection.delivery_calls] == [
        CAPABILITY_REF, CAPABILITY_OTHER_REF
    ]
    assert all(
        call["company_id"] == COMPANY
        and call["plugin_id"] == PLUGIN
        and call["plugin_instance_id"] == INSTANCE
        for call in admin.secret_projection.delivery_calls
    )
    assert ("secrets", NAMESPACE, delivery_name) not in api.objects
    assert api.get("secrets", sibling_name) == sibling_before
    assert ("secrets", NAMESPACE, name) not in api.objects
    delivery_delete = next(
        index for index, call in enumerate(api.calls)
        if call["method"] == "DELETE" and call["path"].endswith(f"/secrets/{delivery_name}")
    )
    assert all(position < delivery_delete for position in admin.secret_projection.delivery_api_positions)
    deletes = [call for call in api.calls if call["method"] == "DELETE"]
    assert len(deletes) == 4
    assert sum(call["path"].endswith(f"/secrets/{delivery_name}") for call in deletes) == 1
    assert all(not call["path"].endswith(f"/secrets/{sibling_name}") for call in deletes)


async def test_revoke_scales_to_zero_then_revokes_and_deletes_only_own_delivery_secret(
    caplog,
) -> None:
    caplog.set_level(logging.DEBUG)
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    other_instance = "inst_equipment_other"
    sibling = _delivery_secret(instance_id=other_instance)
    sibling_before = copy.deepcopy(sibling)
    api.put("secrets", sibling)
    name = await admin.ensure_deployment(**_kwargs())
    api.get("deployments", name)["spec"]["replicas"] = 1
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    sibling_name = admin.canonical_delivery_secret_name(PLUGIN, other_instance)

    assert (
        await admin.suspend(
            company_id=COMPANY,
            plugin_id=PLUGIN,
            cleanup_delivery=True,
        )
        is True
    )

    assert api.get("deployments", name)["spec"]["replicas"] == 0
    assert ("secrets", NAMESPACE, delivery_name) not in api.objects
    assert api.get("secrets", sibling_name) == sibling_before
    assert ("secrets", NAMESPACE, name) in api.objects
    assert [call["secret_ref"] for call in admin.secret_projection.delivery_calls] == [
        CAPABILITY_REF, CAPABILITY_OTHER_REF
    ]
    deletes = [call for call in api.calls if call["method"] == "DELETE"]
    assert len(deletes) == 1
    assert deletes[0]["path"].endswith(f"/secrets/{delivery_name}")
    assert not deletes[0]["path"].endswith(f"/secrets/{sibling_name}")
    surfaces = [repr(api.calls), caplog.text]
    encoded_plaintext = base64.b64encode(CAPABILITY_PLAINTEXT.encode()).decode()
    for surface in surfaces:
        assert CAPABILITY_PLAINTEXT not in surface
        assert encoded_plaintext not in surface


async def test_cleanup_without_delivery_projection_fails_closed_before_any_delete() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    name = await admin.ensure_deployment(**_kwargs())
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    admin._secret_delivery_projection = object()

    with pytest.raises(DeploymentSecretError, match="projection is not wired"):
        await admin.uninstall(company_id=COMPANY, plugin_id=PLUGIN)

    assert ("deployments", NAMESPACE, name) in api.objects
    assert ("secrets", NAMESPACE, name) in api.objects
    assert ("secrets", NAMESPACE, delivery_name) in api.objects
    assert not any(call["method"] == "DELETE" for call in api.calls)


async def test_cleanup_rejects_foreign_delivery_secret_without_delete_or_adoption() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    await admin.ensure_deployment(**_kwargs())
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    foreign = api.get("secrets", delivery_name)
    foreign["metadata"]["labels"][COMPANY_LABEL] = "f" * 40
    foreign_before = copy.deepcopy(foreign)
    admin.secret_projection.delivery_enabled = False

    with pytest.raises(DeploymentOwnershipError):
        await admin.uninstall(company_id=COMPANY, plugin_id=PLUGIN)

    assert api.get("secrets", delivery_name) == foreign_before
    assert not any(call["method"] == "DELETE" for call in api.calls)


async def test_cleanup_rejects_out_of_scope_projection_reference_before_delete() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[BUNDLE_DIGEST] = _artifact(manifest=_delivery_manifest())
    api.put("secrets", _delivery_secret())
    await admin.ensure_deployment(**_kwargs())
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    admin.secret_projection.foreign_revoke_labels = True
    admin.secret_projection.delivery_enabled = False

    with pytest.raises(DeploymentSecretError, match="out-of-scope revoke"):
        await admin.uninstall(company_id=COMPANY, plugin_id=PLUGIN)

    assert ("secrets", NAMESPACE, delivery_name) in api.objects
    assert not any(call["method"] == "DELETE" for call in api.calls)


async def test_cleanup_without_declaration_does_not_blindly_delete_delivery_secret() -> None:
    admin, api, _, _, _, _ = _harness()
    api.put("secrets", _delivery_secret())
    delivery_name = admin.canonical_delivery_secret_name(PLUGIN, INSTANCE)
    before = copy.deepcopy(api.get("secrets", delivery_name))

    with pytest.raises(DeploymentSecretError, match="without an active"):
        await admin.uninstall(company_id=COMPANY, plugin_id=PLUGIN)

    assert api.get("secrets", delivery_name) == before
    assert admin.secret_projection.delivery_calls == []
    assert not any(call["method"] == "DELETE" for call in api.calls)


async def test_uninstall_is_instance_scoped_and_leaves_foreign_resources() -> None:
    admin, api, _, instances, _, _ = _harness()
    first = await admin.ensure_deployment(**_kwargs())
    other_company = "co_other"
    other_instance = "inst_equipment_other"
    other_dsn_ref = "file://ai_keys/plugins/tenantdb/co_other/mod_equipment.secret"
    instances.add(other_instance, company_id=other_company)
    api.put(
        "secrets",
        _secret(
            company_id=other_company,
            instance_id=other_instance,
            dsn_ref=other_dsn_ref,
        ),
    )
    second = await admin.ensure_deployment(
        **{
            **_kwargs(instance_id=other_instance),
            "company_id": other_company,
            "dsn_ref": other_dsn_ref,
        }
    )
    foreign = copy.deepcopy(api.get("deployments", first))
    foreign["metadata"]["namespace"] = "other-namespace"
    api.put("deployments", foreign)

    assert await admin.uninstall(company_id=COMPANY, plugin_id=PLUGIN) is True
    for plural in ("deployments", "services", "secrets"):
        assert (plural, NAMESPACE, first) not in api.objects
        assert (plural, NAMESPACE, second) in api.objects
    assert ("deployments", "other-namespace", first) in api.objects
    assert not any("serviceaccounts" in call["path"] for call in api.calls)
    assert not any("customresourcedefinitions" in call["path"] for call in api.calls)
    assert not any("networkpolicies" in call["path"] for call in api.calls)
    deletes = [call for call in api.calls if call["method"] == "DELETE"]
    assert len(deletes) == 3
    assert all(call["body"]["kind"] == "DeleteOptions" for call in deletes)
    assert all(call["body"]["preconditions"]["resourceVersion"] for call in deletes)


async def test_uninstall_rejects_foreign_same_name_resource_without_deleting_it() -> None:
    admin, api, _, _, _, _ = _harness()
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    deployment["metadata"]["labels"][COMPANY_LABEL] = "f" * 40

    with pytest.raises(DeploymentOwnershipError):
        await admin.purge_infra(company_id=COMPANY, plugin_id=PLUGIN)
    assert api.get("deployments", name) is deployment
    assert not any(call["method"] == "DELETE" for call in api.calls)


async def test_create_race_and_patch_conflict_are_replayed() -> None:
    admin, api, artifacts, _, instance, _ = _harness()
    api.race_posts.add("services")
    name = await admin.ensure_deployment(**_kwargs())
    assert api.get("services", name)["kind"] == "Service"

    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    deployment_path = f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{name}"
    api.conflicts[("PATCH", deployment_path)] = 1
    assert await admin.update_deployment(**_update_kwargs()) == name
    assert api.get("deployments", name)["spec"]["template"]["spec"]["containers"][0]["image"] == NEXT_BACKEND_REF
    assert instance.artifact_digest == BUNDLE_DIGEST


async def test_same_owner_retry_is_read_only_and_other_owner_is_denied() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    name = await admin.ensure_deployment(**_kwargs())
    assert await admin.update_deployment(**_update_kwargs()) == name
    deployment = api.get("deployments", name)
    assert deployment["metadata"]["annotations"][UPDATE_OWNER_ANNOTATION] == UPDATE_OWNER
    template_annotations = deployment["spec"]["template"]["metadata"]["annotations"]
    assert template_annotations[UPDATE_OWNER_ANNOTATION] == UPDATE_OWNER
    assert template_annotations[BACKEND_ANNOTATION] == NEXT_BACKEND_DIGEST
    assert template_annotations[BACKEND_ANNOTATION] != NEXT_BUNDLE_DIGEST
    api.calls.clear()
    admin.secret_projection.calls.clear()

    # A lost response after a successful PATCH may retry only with the same owner.
    assert await admin.update_deployment(**_update_kwargs()) == name
    assert [call for call in api.calls if call["method"] != "GET"] == []
    with pytest.raises(DeploymentArtifactDigestConflictError):
        await admin.update_deployment(**_update_kwargs(update_owner="foreign-owner"))
    assert [call for call in api.calls if call["method"] != "GET"] == []


async def test_pre_projection_canonical_update_is_read_only() -> None:
    """Canonical replay fence accepts the desired state without a NameError.

    Regression for the pre-projection replay fence: the comparison must use the
    canonical image/digest inputs, not the artifact descriptor. Idempotent
    projection reconciliation is still allowed; Kubernetes writes are not.
    """
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    name = await admin.ensure_deployment(**_kwargs())
    assert await admin.update_deployment(**_update_kwargs()) == name
    api.calls.clear()
    admin.secret_projection.calls.clear()

    assert await admin.update_deployment(**_update_kwargs()) == name
    assert [call for call in api.calls if call["method"] != "GET"] == []


async def test_pre_projection_stale_digest_denied_before_secret_mutation() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    # Foreign live pin: differs from both the expected and the desired digest.
    deployment["metadata"]["annotations"][ARTIFACT_ANNOTATION] = NEXT_BUNDLE_DIGEST
    deployment["spec"]["template"]["metadata"]["annotations"][ARTIFACT_ANNOTATION] = (
        NEXT_BUNDLE_DIGEST
    )
    api.calls.clear()
    admin.secret_projection.calls.clear()

    with pytest.raises(DeploymentArtifactDigestConflictError):
        await admin.update_deployment(**_update_kwargs())
    assert [call for call in api.calls if call["method"] != "GET"] == []
    assert admin.secret_projection.calls == []


async def test_suspend_accepts_scale_response_with_omitted_zero_replicas() -> None:
    """autoscaling/v1 omits Scale.spec.replicas for zero: suspend must succeed."""
    admin, api, _, _, _, _ = _harness()
    await admin.ensure_deployment(**_kwargs())
    original_request = api.request

    async def omitted_zero(method, path, **kwargs):
        response = await original_request(method, path, **kwargs)
        if method == "GET" and path.endswith("/scale"):
            response = K8sRawResponse(
                status_code=response.status_code,
                body={**response.body, "spec": {}},
            )
        return response

    api.request = omitted_zero
    assert await admin.suspend(company_id=COMPANY, plugin_id=PLUGIN)


async def test_resume_rejects_scale_response_with_omitted_nonzero_replicas() -> None:
    """An omitted replicas field is zero ONLY: never a silent wake to one."""
    admin, api, _, _, _, _ = _harness()
    await admin.ensure_deployment(**_kwargs())
    original_request = api.request

    async def omitted_replicas(method, path, **kwargs):
        response = await original_request(method, path, **kwargs)
        if method == "GET" and path.endswith("/scale"):
            response = K8sRawResponse(
                status_code=response.status_code,
                body={**response.body, "spec": {}},
            )
        return response

    api.request = omitted_replicas
    with pytest.raises(DeploymentUnavailableError, match="did not confirm replicas=1"):
        await admin.resume(company_id=COMPANY, plugin_id=PLUGIN)


async def test_first_update_rollback_restores_old_digest_and_removes_owner() -> None:
    admin, api, artifacts, _, instance, _ = _harness()
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    name = await admin.ensure_deployment(**_kwargs())
    await admin.update_deployment(**_update_kwargs())
    assert instance.artifact_digest == BUNDLE_DIGEST
    pod_spec = api.get("deployments", name)["spec"]["template"]["spec"]
    pod_spec["volumes"].append({"name": "workspace", "emptyDir": {}})
    pod_spec["containers"][0]["volumeMounts"].append({
        "name": "workspace", "mountPath": "/workspace"
    })

    await admin.update_deployment(
        **_update_kwargs(
            digest=BUNDLE_DIGEST,
            expected_current=NEXT_BUNDLE_DIGEST,
            update_owner=None,
            expected_owner=UPDATE_OWNER,
        )
    )
    binding = await admin.runtime_binding(company_id=COMPANY, plugin_id=PLUGIN)
    assert binding.artifact_digest == BUNDLE_DIGEST
    assert binding.update_owner_token is None
    deployment = api.get("deployments", name)
    assert UPDATE_OWNER_ANNOTATION not in deployment["metadata"]["annotations"]
    assert UPDATE_OWNER_ANNOTATION not in (
        deployment["spec"]["template"]["metadata"]["annotations"]
    )
    pod_spec = deployment["spec"]["template"]["spec"]
    assert pod_spec["volumes"] == [
        _identity_volume(),
        _tmp_volume(),
        {"name": "workspace", "emptyDir": {}},
    ]
    assert pod_spec["containers"][0]["volumeMounts"] == [
        _identity_mount(),
        _tmp_mount(),
        {"name": "workspace", "mountPath": "/workspace"},
    ]


async def test_live_annotation_mismatch_denies_every_mutation() -> None:
    admin, api, artifacts, _, instance, _ = _harness()
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    name = await admin.ensure_deployment(**_kwargs())
    deployment = api.get("deployments", name)
    deployment["metadata"]["annotations"][ARTIFACT_ANNOTATION] = NEXT_BUNDLE_DIGEST
    deployment["spec"]["template"]["metadata"]["annotations"][ARTIFACT_ANNOTATION] = (
        NEXT_BUNDLE_DIGEST
    )
    api.calls.clear()
    admin.secret_projection.calls.clear()

    with pytest.raises(DeploymentArtifactDigestConflictError) as caught:
        await admin.update_deployment(**_update_kwargs())
    assert caught.value.code == "DEPLOYMENT_ARTIFACT_DIGEST_CONFLICT"
    assert [call for call in api.calls if call["method"] != "GET"] == []
    assert admin.secret_projection.calls == []
    assert instance.artifact_digest == BUNDLE_DIGEST


async def test_retry_rechecks_live_digest_after_conflict() -> None:
    admin, api, artifacts, _, _, _ = _harness()
    artifacts.records[NEXT_BUNDLE_DIGEST] = _artifact(
        NEXT_BUNDLE_DIGEST, NEXT_BACKEND_DIGEST, NEXT_BACKEND_REF
    )
    name = await admin.ensure_deployment(**_kwargs())
    original_request = api.request
    injected = False

    async def conflict_then_move_owner(method, path, **kwargs):
        nonlocal injected
        if method == "PATCH" and path.endswith(f"/deployments/{name}") and not injected:
            injected = True
            try:
                return await original_request(method, path, **kwargs)
            finally:
                live = api.get("deployments", name)
                live["metadata"]["annotations"][ARTIFACT_ANNOTATION] = NEXT_BUNDLE_DIGEST
                live["spec"]["template"]["metadata"]["annotations"][ARTIFACT_ANNOTATION] = (
                    NEXT_BUNDLE_DIGEST
                )
                live["metadata"]["annotations"][UPDATE_OWNER_ANNOTATION] = "foreign-owner"
                live["spec"]["template"]["metadata"]["annotations"][UPDATE_OWNER_ANNOTATION] = (
                    "foreign-owner"
                )
        return await original_request(method, path, **kwargs)

    deployment_path = f"/apis/apps/v1/namespaces/{NAMESPACE}/deployments/{name}"
    api.conflicts[("PATCH", deployment_path)] = 1
    api.request = conflict_then_move_owner
    with pytest.raises(DeploymentArtifactDigestConflictError):
        await admin.update_deployment(**_update_kwargs())
    patches = [
        call for call in api.calls
        if call["method"] == "PATCH" and call["path"].endswith(f"/deployments/{name}")
    ]
    assert len(patches) == 1


async def test_api_error_does_not_leak_secret_values(caplog) -> None:
    admin, api, _, _, _, _ = _harness()
    caplog.set_level(logging.DEBUG)
    name = plugin_resource_name(PLUGIN, INSTANCE)
    path = f"/api/v1/namespaces/{NAMESPACE}/services/{name}"
    api.errors[("GET", path)] = K8sApiUnavailable(DSN_SENTINEL)

    with pytest.raises(DeploymentUnavailableError) as caught:
        await admin.ensure_deployment(**_kwargs())
    assert DSN_SENTINEL not in str(caught.value)
    assert DSN_SENTINEL not in caplog.text
    assert GATEWAY_SENTINEL not in caplog.text


async def test_projection_failure_is_typed_and_redacted(caplog) -> None:
    admin, api, _, _, _, _ = _harness()
    caplog.set_level(logging.DEBUG)

    class BrokenProjection:
        async def ensure_instance_secret(self, **kwargs):
            raise RuntimeError(DSN_SENTINEL)

    admin._secret_projection = BrokenProjection()
    with pytest.raises(DeploymentSecretError) as caught:
        await admin.ensure_deployment(**_kwargs())
    assert DSN_SENTINEL not in str(caught.value)
    assert DSN_SENTINEL not in caplog.text
    assert not any(plural == "deployments" for plural, _, _ in api.objects)


async def test_async_durable_name_resolver_restores_scale_after_restart() -> None:
    admin, api, _, instances, _, config = _harness()
    name = await admin.ensure_deployment(**_kwargs())

    async def resolve(instance_id: str) -> str:
        record = await instances.get_by_id(plugin_instance_id=instance_id)
        assert record is not None
        return plugin_resource_name(record.plugin_id, instance_id)

    scale = K8sScalePort(
        config=config,
        api=api,
        readiness_mode=ReadinessMode.STATUS,
        deployment_name_resolver=resolve,
    )
    assert await scale.current_replicas(INSTANCE) == 0
    await scale.scale_to(INSTANCE, 1)
    deployment = api.get("deployments", name)
    deployment["status"] = {"readyReplicas": 1, "availableReplicas": 1}
    assert await scale.is_ready(INSTANCE) is True


def test_long_canonical_names_are_bounded_and_collision_resistant() -> None:
    admin, _, _, _, _, _ = _harness()
    shared = "mod_" + "very-long-prefix-" * 8
    first = admin.resource_name(shared + "a", "inst_" + "x" * 100)
    second = admin.resource_name(shared + "b", "inst_" + "x" * 100)
    assert first != second
    assert len(first) <= 63 and len(second) <= 63
    assert re.fullmatch(r"[a-z](?:[-a-z0-9]*[a-z0-9])?", first)


def test_installer_uses_shared_k8s_client_and_fails_fast_without_config() -> None:
    admin, api, artifacts, instances, _, config = _harness()
    runtime = SimpleNamespace(config=config, lifecycle_api=api)
    repos = SimpleNamespace(artifact_store=artifacts)
    wired = _deployment_admin(
        runtime,
        repos,
        instances,
        secret_projection=admin.secret_projection,
    )
    assert isinstance(wired, K8sDeploymentAdmin)
    assert wired.api is api

    with pytest.raises(RuntimeConfigError):
        _deployment_admin(SimpleNamespace(config=None), repos, instances)
    with pytest.raises(RuntimeConfigError):
        _deployment_admin(
            SimpleNamespace(config=config, scale=SimpleNamespace(api=api)),
            repos,
            instances,
        )
    with pytest.raises(RuntimeConfigError):
        RuntimeConfig(namespace=NAMESPACE, cpu_limit="0")
