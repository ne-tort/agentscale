"""Offline per-instance delivery Secret tests; intentionally not run under freeze."""
from __future__ import annotations

import base64
import copy
import hashlib
import json
import logging
import re
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters.secrets import (
    SecretStoreSecretsAdapter,
    SecretsDeliveryRequest,
)
from prodavan.application.plugins.capabilities.contracts import CapabilityEnvelope
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sApiConflict,
    K8sApiNotFound,
    K8sRawResponse,
)
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    COMPANY_LABEL,
    DELIVERY_VERSIONS_ANNOTATION,
    INSTANCE_LABEL,
    PLUGIN_LABEL,
    K8sSecretProjection,
    SecretDeliveryProjectionPort,
    SecretOwnershipError,
    SecretProjectionConfigError,
)

NAMESPACE = "agentscale-dev"
COMPANY = "co_123"
PLUGIN = "mod_x"
INSTANCE = "inst_abc"
OTHER_INSTANCE = "inst_other"
REF = f"vault://ai_keys/plugins/capabilities/secrets/{COMPANY}/{PLUGIN}/{INSTANCE}/api-key"
OTHER_REF = f"vault://ai_keys/plugins/capabilities/secrets/{COMPANY}/{PLUGIN}/{INSTANCE}/other"
OTHER_INSTANCE_REF = f"vault://ai_keys/plugins/capabilities/secrets/{COMPANY}/{PLUGIN}/{OTHER_INSTANCE}/api-key"
PLAINTEXT = "unit-test-k8s-only-plaintext"
ROTATED = "unit-test-rotated-k8s-only-plaintext"
VAULT_TOKEN = "unit-test-vault-token-never-downstream"
DIGEST = "sha256:" + "a" * 64


def response(body: dict, status_code: int = 200) -> K8sRawResponse:
    return K8sRawResponse(status_code=status_code, body=copy.deepcopy(body))


def encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def decoded(secret: dict, key: str) -> str:
    return base64.b64decode(secret["data"][key], validate=True).decode("utf-8")


def delivery_key(secret_ref: str) -> str:
    return "SECRET_" + hashlib.sha256(secret_ref.encode("utf-8")).hexdigest().upper()


def identity_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]


class FakeK8s:
    """Core Secret REST fake with JSON merge-patch and resourceVersion CAS."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.calls: list[dict[str, Any]] = []
        self.conflict_once: set[tuple[str, str]] = set()

    @staticmethod
    def parse(path: str) -> tuple[str, str | None]:
        match = re.fullmatch(
            r"/api/v1/namespaces/([^/]+)/secrets(?:/([^/]+))?", path
        )
        if match is None:
            raise AssertionError("unexpected Secret API path")
        return match.group(1), match.group(2)

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: dict | None = None,
        content_type: str | None = None,
    ) -> K8sRawResponse:
        method = method.upper()
        namespace, name = self.parse(path)
        self.calls.append({
            "method": method,
            "path": path,
            "body": copy.deepcopy(body),
            "content_type": content_type,
        })
        key = (namespace, name or "")
        existing = self.objects.get(key)
        if (method, path) in self.conflict_once:
            self.conflict_once.remove((method, path))
            if existing is not None:
                existing["metadata"]["resourceVersion"] = str(
                    int(existing["metadata"]["resourceVersion"]) + 1
                )
            raise K8sApiConflict("injected CAS conflict")

        if method == "GET":
            if name is None or existing is None:
                raise K8sApiNotFound("Secret absent")
            return response(existing)
        if method == "POST":
            if name is not None or body is None:
                raise AssertionError("invalid Secret create")
            created = copy.deepcopy(body)
            created_name = created["metadata"]["name"]
            created_key = (namespace, created_name)
            if created_key in self.objects:
                raise K8sApiConflict("Secret already exists")
            created["metadata"]["resourceVersion"] = "1"
            self.objects[created_key] = created
            return response(created, status_code=201)
        if method == "PATCH":
            if name is None or existing is None or body is None:
                raise K8sApiNotFound("Secret absent")
            actual = existing["metadata"]["resourceVersion"]
            if body["metadata"]["resourceVersion"] != actual:
                raise K8sApiConflict("resourceVersion mismatch")
            updated = copy.deepcopy(existing)
            metadata = body["metadata"]
            updated["metadata"]["labels"] = copy.deepcopy(metadata["labels"])
            updated["metadata"].setdefault("annotations", {}).update(
                copy.deepcopy(metadata.get("annotations", {}))
            )
            updated["type"] = body["type"]
            updated.setdefault("data", {})
            for data_key, data_value in body["data"].items():
                if data_value is None:
                    updated["data"].pop(data_key, None)
                else:
                    updated["data"][data_key] = data_value
            updated["metadata"]["resourceVersion"] = str(int(actual) + 1)
            self.objects[key] = updated
            return response(updated)
        raise AssertionError("projection must use only Secret GET/POST/PATCH")


class TenantSecrets:
    async def get(self, *, key: str) -> None:
        return None


class Store:
    def __init__(self, values: dict[str, str]) -> None:
        self.values = dict(values)

    def get(self, secret_ref: str) -> str:
        return self.values[secret_ref]


class ApprovedPolicy:
    async def authorize(self, **kwargs: Any) -> None:
        return None


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-projection-delivery",
        company_id=COMPANY,
        subject_id=f"plugin:{PLUGIN}:{COMPANY}:{INSTANCE}",
        plugin_instance_id=INSTANCE,
        artifact_digest=DIGEST,
        grant_id="grant_01",
    )


def harness(*, max_delivery_secret_bytes: int = 1024) -> tuple[K8sSecretProjection, FakeK8s]:
    api = FakeK8s()
    projection = K8sSecretProjection(
        config=RuntimeConfig(namespace=NAMESPACE, gateway_url="http://plugin-gateway:8080"),
        tenant_secrets=TenantSecrets(),
        api=api,
        max_delivery_secret_bytes=max_delivery_secret_bytes,
    )
    return projection, api


def methods(api: FakeK8s) -> list[str]:
    return [call["method"] for call in api.calls]


def only_secret(api: FakeK8s) -> dict:
    assert len(api.objects) == 1
    return next(iter(api.objects.values()))


@pytest.mark.asyncio
async def test_adapter_plaintext_is_only_in_k8s_payload_not_response_urls_manifest_or_logs(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    projection, api = harness()
    store = Store({REF: PLAINTEXT})
    secrets = SecretStoreSecretsAdapter(
        store=store,
        projection=projection,
        delivery_policy=ApprovedPolicy(),
    )
    manifest = {"secret_delivery": {"mode": "k8s_secret", "allowlist": [REF]}}

    result = await secrets.deliver(SecretsDeliveryRequest(secret_ref=REF, envelope=envelope()))

    assert isinstance(projection, SecretDeliveryProjectionPort)
    secret = only_secret(api)
    key = delivery_key(REF)
    assert set(secret["data"]) == {key}
    assert decoded(secret, key) == PLAINTEXT
    versions = json.loads(secret["metadata"]["annotations"][DELIVERY_VERSIONS_ANNOTATION])
    assert versions == {key: result.version}
    assert result.value_digest == result.version
    assert result.delivered is True
    labels = secret["metadata"]["labels"]
    assert labels[COMPANY_LABEL] == identity_hash(COMPANY)
    assert labels[PLUGIN_LABEL] == identity_hash(PLUGIN)
    assert labels[INSTANCE_LABEL] == identity_hash(f"{COMPANY}\0{PLUGIN}\0{INSTANCE}")
    assert "VAULT_TOKEN" not in secret["data"]
    assert VAULT_TOKEN not in json.dumps(secret)
    surfaces = [
        repr(result), str(result), caplog.text, json.dumps(manifest),
        "agent prompt: " + repr(result),
        *(call["path"] for call in api.calls),
    ]
    for surface in surfaces:
        assert PLAINTEXT not in surface
        assert ROTATED not in surface
        assert VAULT_TOKEN not in surface
        assert encoded(PLAINTEXT) not in surface


@pytest.mark.asyncio
async def test_same_ref_rotation_uses_cas_then_repeat_is_read_only() -> None:
    projection, api = harness()
    first = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=PLAINTEXT,
    )
    assert first.delivered is True
    api.calls.clear()

    rotated = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=ROTATED,
    )

    assert rotated.name == first.name
    assert rotated.key == first.key
    assert rotated.version != first.version
    assert rotated.delivered is True
    assert methods(api) == ["GET", "PATCH"]
    patch = api.calls[-1]
    assert patch["body"]["metadata"]["resourceVersion"] == "1"
    assert patch["content_type"] == "application/merge-patch+json"
    secret = api.objects[(rotated.namespace, rotated.name)]
    assert secret["metadata"]["resourceVersion"] == "2"
    assert decoded(secret, rotated.key) == ROTATED
    assert PLAINTEXT not in json.dumps(secret)

    api.calls.clear()
    repeated = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=ROTATED,
    )
    assert repeated == rotated
    assert repeated.delivered is True
    assert methods(api) == ["GET"]


@pytest.mark.asyncio
async def test_rotation_conflict_retries_with_fresh_resource_version() -> None:
    projection, api = harness()
    reference = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=PLAINTEXT,
    )
    path = f"/api/v1/namespaces/{NAMESPACE}/secrets/{reference.name}"
    api.conflict_once.add(("PATCH", path))
    api.calls.clear()

    rotated = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=ROTATED,
    )

    assert methods(api) == ["GET", "PATCH", "GET", "PATCH"]
    patches = [call for call in api.calls if call["method"] == "PATCH"]
    assert patches[0]["body"]["metadata"]["resourceVersion"] == "1"
    assert patches[1]["body"]["metadata"]["resourceVersion"] == "2"
    assert api.objects[(rotated.namespace, rotated.name)]["metadata"]["resourceVersion"] == "3"


@pytest.mark.asyncio
async def test_revoke_removes_exact_key_preserves_siblings_and_is_idempotent() -> None:
    projection, api = harness()
    first = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=PLAINTEXT,
    )
    other = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=OTHER_REF, value="other-secret",
    )
    assert first.name == other.name
    api.calls.clear()

    revoked = await projection.revoke_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF,
    )

    assert revoked.delivered is False
    assert revoked.value_digest == first.value_digest
    assert methods(api) == ["GET", "PATCH"]
    patch = api.calls[-1]
    assert patch["body"]["data"] == {first.key: None}
    secret = api.objects[(first.namespace, first.name)]
    assert set(secret["data"]) == {other.key}
    assert decoded(secret, other.key) == "other-secret"
    versions = json.loads(secret["metadata"]["annotations"][DELIVERY_VERSIONS_ANNOTATION])
    assert versions == {other.key: other.value_digest}

    api.calls.clear()
    repeated = await projection.revoke_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF,
    )
    assert repeated.delivered is False
    assert methods(api) == ["GET"]
    assert decoded(secret, other.key) == "other-secret"


@pytest.mark.asyncio
async def test_wrong_tenant_size_cap_and_foreign_ownership_have_no_mutation() -> None:
    projection, api = harness(max_delivery_secret_bytes=8)
    with pytest.raises(SecretProjectionConfigError):
        await projection.deliver_secret(
            company_id="co_other", plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
            secret_ref=REF, value="small",
        )
    with pytest.raises(SecretProjectionConfigError):
        await projection.deliver_secret(
            company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
            secret_ref=REF, value=PLAINTEXT,
        )
    assert api.calls == []

    projection, api = harness()
    reference = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=PLAINTEXT,
    )
    secret = api.objects[(reference.namespace, reference.name)]
    secret["metadata"]["labels"][COMPANY_LABEL] = "0" * 40
    original = copy.deepcopy(secret)
    api.calls.clear()
    with pytest.raises(SecretOwnershipError):
        await projection.deliver_secret(
            company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
            secret_ref=REF, value=ROTATED,
        )
    assert methods(api) == ["GET"]
    assert secret == original


@pytest.mark.asyncio
async def test_instance_revoke_does_not_touch_other_instance_secret() -> None:
    projection, api = harness()
    first = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF, value=PLAINTEXT,
    )
    second = await projection.deliver_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=OTHER_INSTANCE,
        secret_ref=OTHER_INSTANCE_REF, value=ROTATED,
    )
    assert first.name != second.name
    second_before = copy.deepcopy(api.objects[(second.namespace, second.name)])

    await projection.revoke_secret(
        company_id=COMPANY, plugin_id=PLUGIN, plugin_instance_id=INSTANCE,
        secret_ref=REF,
    )
    assert api.objects[(second.namespace, second.name)] == second_before


@pytest.mark.asyncio
async def test_control_reconcile_exact_set_removes_stale_keys_and_retries_cas() -> None:
    projection, api = harness()
    first = await projection.reconcile_secret_values(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        values={REF: PLAINTEXT, OTHER_REF: "other-before"},
    )
    assert len(first) == 2
    first_name = first[0].name
    stale_key = first[0].key
    retained_key = first[1].key
    path = f"/api/v1/namespaces/{NAMESPACE}/secrets/{first_name}"
    api.conflict_once.add(("PATCH", path))
    api.calls.clear()

    rotated = await projection.reconcile_secret_values(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        values={OTHER_REF: ROTATED},
    )

    assert methods(api) == ["GET", "PATCH", "GET", "PATCH"]
    patches = [call for call in api.calls if call["method"] == "PATCH"]
    assert patches[0]["body"]["metadata"]["resourceVersion"] == "1"
    assert patches[1]["body"]["metadata"]["resourceVersion"] == "2"
    assert all(call["body"]["data"][stale_key] is None for call in patches)
    assert len(rotated) == 1
    assert rotated[0].key == retained_key
    assert rotated[0].version == "sha256:" + hashlib.sha256(ROTATED.encode()).hexdigest()
    secret = api.objects[(NAMESPACE, first_name)]
    assert set(secret["data"]) == {retained_key}
    assert decoded(secret, retained_key) == ROTATED
    versions = json.loads(secret["metadata"]["annotations"][DELIVERY_VERSIONS_ANNOTATION])
    assert versions == {retained_key: rotated[0].version}
    assert PLAINTEXT not in repr(rotated)

    api.calls.clear()
    repeated = await projection.reconcile_secret_values(
        company_id=COMPANY,
        plugin_id=PLUGIN,
        plugin_instance_id=INSTANCE,
        values={OTHER_REF: ROTATED},
    )
    assert repeated == rotated
    assert methods(api) == ["GET"]
