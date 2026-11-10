"""Offline fake-client tests for per-instance tenant Secret projection.

Written under the migration verification freeze. These tests perform no network,
subprocess, Docker, kubectl, Git, filesystem-secret, Vault, or cluster operations.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import logging
import re

import pytest

from prodavan.application.plugins.tenantdb.service import StoredSecret
from prodavan.infrastructure.plugins.runtime.config import RuntimeConfig
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sApiConflict,
    K8sApiNotFound,
    K8sRawResponse,
    plugin_resource_name,
)
from prodavan.infrastructure.plugins.runtime.secret_projection import (
    COMPANY_LABEL,
    DSN_REF_ANNOTATION,
    INSTANCE_LABEL,
    MANAGED_BY,
    MANAGED_BY_VALUE,
    PLUGIN_LABEL,
    BASE_SECRET_KEYS,
    SECRET_KEYS,
    WEBHOOK_SECRET_KEY,
    K8sSecretProjection,
    SecretDriftError,
    SecretOwnershipError,
    SecretProjectionConfigError,
    SecretProjectionPort,
    SecretReference,
    SecretUnavailableError,
    TenantSecretResolutionError,
    WebhookSecretResolutionError,
)

NAMESPACE = "agentscale-dev"
COMPANY = "co_123"
PLUGIN = "mod_equipment"
INSTANCE = "inst_equipment_001"
OTHER_INSTANCE = "inst_equipment_002"
KEY = f"plugins/tenantdb/{COMPANY}/{PLUGIN}/TENANT_DB_DSN"
DSN_REF = f"file://ai_keys/{KEY}.secret"
ROTATED_DSN_REF = f"vault://ai_keys/{KEY}"
DSN_SENTINEL = "unit-test-tenant-dsn-sentinel"
ROTATED_DSN_SENTINEL = "unit-test-rotated-tenant-dsn-sentinel"
GATEWAY_URL = "http://plugin-gateway:8080"
WEBHOOK_REF = (
    f"file://ai_keys/plugins/webhooks/{COMPANY}/{PLUGIN}/{INSTANCE}/WEBHOOK_SECRET.secret"
)
WEBHOOK_SENTINEL = "unit-test-webhook-secret-sentinel"
ROTATED_WEBHOOK_SENTINEL = "unit-test-rotated-webhook-secret-sentinel"
K8S_DSN_REF = "k8s-secret://tenant-dsn-co-123-mod-equipment/TENANT_DB_DSN"
K8S_WEBHOOK_REF = (
    "k8s-secret://webhook-co-123-mod-equipment-inst-equipment-001/WEBHOOK_SECRET"
)


def _tenant_key(company_id: str, plugin_id: str) -> str:
    return f"plugins/tenantdb/{company_id}/{plugin_id}/TENANT_DB_DSN"


def _response(body: dict, status_code: int = 200) -> K8sRawResponse:
    return K8sRawResponse(status_code=status_code, body=copy.deepcopy(body))


def _encoded(value: str) -> str:
    return base64.b64encode(value.encode("utf-8")).decode("ascii")


def _decoded(secret: dict, key: str) -> str:
    return base64.b64decode(secret["data"][key], validate=True).decode("utf-8")


class FakeTenantSecrets:
    def __init__(self) -> None:
        self.records: dict[str, StoredSecret] = {}
        self.get_calls: list[str] = []

    def set(self, company_id: str, plugin_id: str, dsn_ref: str, value: str) -> None:
        self.records[_tenant_key(company_id, plugin_id)] = StoredSecret(
            secret_ref=dsn_ref,
            value=value,
        )

    async def get(self, *, key: str) -> StoredSecret | None:
        self.get_calls.append(key)
        return self.records.get(key)


class FakeWebhookResolver:
    def __init__(self) -> None:
        self.value = WEBHOOK_SENTINEL
        self.calls: list[tuple[str, str, str, str]] = []

    async def resolve(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> str:
        self.calls.append((company_id, plugin_id, plugin_instance_id, secret_ref))
        if secret_ref not in {WEBHOOK_REF, K8S_WEBHOOK_REF}:
            raise LookupError("unknown test webhook reference")
        return self.value


class StatefulFakeK8s:
    """Minimal core/v1 Secret REST model with resourceVersion CAS."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], dict] = {}
        self.calls: list[dict] = []
        self.errors: dict[tuple[str, str], Exception] = {}
        self.conflict_once: set[tuple[str, str]] = set()

    @staticmethod
    def _parse(path: str) -> tuple[str, str | None]:
        match = re.fullmatch(
            r"/api/v1/namespaces/([^/]+)/secrets(?:/([^/]+))?",
            path,
        )
        if match is None:
            raise AssertionError("unexpected fake Secret API path")
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
        namespace, name = self._parse(path)
        self.calls.append(
            {
                "method": method,
                "path": path,
                "body": copy.deepcopy(body),
                "content_type": content_type,
            }
        )
        error = self.errors.get((method, path))
        if error is not None:
            raise error
        key = (namespace, name or "")
        existing = self.objects.get(key)
        if (method, path) in self.conflict_once:
            self.conflict_once.remove((method, path))
            if existing is not None:
                metadata = existing["metadata"]
                metadata["resourceVersion"] = str(
                    int(metadata["resourceVersion"]) + 1
                )
            raise K8sApiConflict("injected resourceVersion conflict")

        if method == "GET":
            if name is None or existing is None:
                raise K8sApiNotFound("Secret absent")
            return _response(existing)

        if method == "POST":
            if name is not None or existing is not None or body is None:
                raise K8sApiConflict("Secret already exists or invalid POST")
            created = copy.deepcopy(body)
            created["metadata"]["resourceVersion"] = "1"
            created_name = created["metadata"]["name"]
            self.objects[(namespace, created_name)] = created
            return _response(created, status_code=201)

        if method == "PATCH":
            if name is None or existing is None or body is None:
                raise K8sApiNotFound("Secret absent")
            actual_version = existing["metadata"]["resourceVersion"]
            expected_version = body["metadata"]["resourceVersion"]
            if expected_version != actual_version:
                raise K8sApiConflict("resourceVersion mismatch")
            updated = copy.deepcopy(existing)
            updated["type"] = body["type"]
            updated["data"] = copy.deepcopy(body["data"])
            updated["metadata"]["labels"] = copy.deepcopy(body["metadata"]["labels"])
            updated["metadata"]["annotations"] = copy.deepcopy(
                body["metadata"]["annotations"]
            )
            updated["metadata"]["resourceVersion"] = str(int(actual_version) + 1)
            self.objects[key] = updated
            return _response(updated)

        raise AssertionError("projection must not delete or use non-Secret operations")


def _labels(company_id: str, plugin_id: str, instance_id: str) -> dict[str, str]:
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:40]

    return {
        MANAGED_BY: MANAGED_BY_VALUE,
        COMPANY_LABEL: digest(company_id),
        PLUGIN_LABEL: digest(plugin_id),
        INSTANCE_LABEL: digest(f"{company_id}\0{plugin_id}\0{instance_id}"),
    }


def _harness():
    api = StatefulFakeK8s()
    tenant_secrets = FakeTenantSecrets()
    tenant_secrets.set(COMPANY, PLUGIN, DSN_REF, DSN_SENTINEL)
    projection = K8sSecretProjection(
        config=RuntimeConfig(namespace=NAMESPACE),
        tenant_secrets=tenant_secrets,
        webhook_resolver=FakeWebhookResolver(),
        gateway_url=GATEWAY_URL,
        api=api,
    )
    return projection, api, tenant_secrets


async def _ensure(
    projection: K8sSecretProjection,
    *,
    company_id: str = COMPANY,
    plugin_id: str = PLUGIN,
    instance_id: str = INSTANCE,
    dsn_ref: str = DSN_REF,
    gateway_url: str | None = None,
    webhook_secret_ref: str | None = WEBHOOK_REF,
) -> SecretReference:
    return await projection.ensure_instance_secret(
        company_id=company_id,
        plugin_id=plugin_id,
        plugin_instance_id=instance_id,
        dsn_ref=dsn_ref,
        gateway_url=gateway_url,
        webhook_secret_ref=webhook_secret_ref,
    )


def _secret(api: StatefulFakeK8s, reference: SecretReference) -> dict:
    return api.objects[(reference.namespace, reference.name)]


def _methods(api: StatefulFakeK8s) -> list[str]:
    return [call["method"] for call in api.calls]


async def test_create_projects_only_canonical_keys_and_typed_reference():
    projection, api, tenant_secrets = _harness()

    reference = await _ensure(projection)

    assert isinstance(projection, SecretProjectionPort)
    assert isinstance(reference, SecretReference)
    assert reference.name == plugin_resource_name(PLUGIN, INSTANCE)
    assert reference.namespace == NAMESPACE
    assert dict(reference.labels) == _labels(COMPANY, PLUGIN, INSTANCE)
    assert reference.dsn_ref_sha256 == hashlib.sha256(DSN_REF.encode()).hexdigest()
    assert tenant_secrets.get_calls == [KEY]
    assert _methods(api) == ["GET", "POST"]

    secret = _secret(api, reference)
    assert secret["kind"] == "Secret"
    assert secret["type"] == "Opaque"
    assert set(secret["data"]) == set(SECRET_KEYS)
    assert set(secret["data"]) == {"GATEWAY_URL", "PLUGIN_ID", "TENANT_DB_DSN", "WEBHOOK_SECRET"}
    assert "PLUGIN_CLAIMS" not in secret["data"]
    assert "stringData" not in secret
    assert _decoded(secret, "TENANT_DB_DSN") == DSN_SENTINEL
    assert _decoded(secret, "GATEWAY_URL") == GATEWAY_URL
    assert _decoded(secret, "PLUGIN_ID") == PLUGIN
    assert _decoded(secret, WEBHOOK_SECRET_KEY) == WEBHOOK_SENTINEL
    assert secret["metadata"]["annotations"][DSN_REF_ANNOTATION] == reference.dsn_ref_sha256
    assert secret["metadata"]["resourceVersion"] == "1"
    assert DSN_SENTINEL not in repr(reference)
    assert DSN_REF not in repr(reference)


async def test_repeat_with_same_ref_and_data_is_read_only():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    original = copy.deepcopy(_secret(api, reference))
    api.calls.clear()

    repeated = await _ensure(projection)

    assert repeated == reference
    assert _methods(api) == ["GET"]
    assert _secret(api, reference) == original


async def test_rotation_patches_same_instance_with_resource_version():
    projection, api, tenant_secrets = _harness()
    first = await _ensure(projection)
    first_name = first.name
    api.calls.clear()
    tenant_secrets.set(COMPANY, PLUGIN, ROTATED_DSN_REF, ROTATED_DSN_SENTINEL)

    rotated = await _ensure(projection, dsn_ref=ROTATED_DSN_REF)

    assert rotated.name == first_name
    assert rotated.dsn_ref_sha256 == hashlib.sha256(
        ROTATED_DSN_REF.encode()
    ).hexdigest()
    assert rotated.dsn_ref_sha256 != first.dsn_ref_sha256
    assert _methods(api) == ["GET", "PATCH"]
    patch = api.calls[-1]
    assert patch["body"]["metadata"]["resourceVersion"] == "1"
    assert patch["content_type"] == "application/merge-patch+json"
    secret = _secret(api, rotated)
    assert secret["metadata"]["resourceVersion"] == "2"
    assert _decoded(secret, "TENANT_DB_DSN") == ROTATED_DSN_SENTINEL
    assert secret["metadata"]["annotations"][DSN_REF_ANNOTATION] == rotated.dsn_ref_sha256

    api.calls.clear()
    repeated = await _ensure(projection, dsn_ref=ROTATED_DSN_REF)
    assert repeated == rotated
    assert _methods(api) == ["GET"]


async def test_rotation_conflict_retries_with_fresh_resource_version():
    projection, api, tenant_secrets = _harness()
    reference = await _ensure(projection)
    tenant_secrets.set(COMPANY, PLUGIN, ROTATED_DSN_REF, ROTATED_DSN_SENTINEL)
    path = f"/api/v1/namespaces/{NAMESPACE}/secrets/{reference.name}"
    api.conflict_once.add(("PATCH", path))
    api.calls.clear()

    rotated = await _ensure(projection, dsn_ref=ROTATED_DSN_REF)

    assert _methods(api) == ["GET", "PATCH", "GET", "PATCH"]
    patches = [call for call in api.calls if call["method"] == "PATCH"]
    assert patches[0]["body"]["metadata"]["resourceVersion"] == "1"
    assert patches[1]["body"]["metadata"]["resourceVersion"] == "2"
    assert _secret(api, rotated)["metadata"]["resourceVersion"] == "3"


async def test_wrong_owner_is_rejected_without_mutation_or_delete():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    secret = _secret(api, reference)
    secret["metadata"]["labels"][COMPANY_LABEL] = "0" * 40
    original = copy.deepcopy(secret)
    api.calls.clear()

    with pytest.raises(SecretOwnershipError):
        await _ensure(projection)

    assert _methods(api) == ["GET"]
    assert _secret(api, reference) == original


async def test_missing_tenant_secret_is_rejected_before_k8s_access():
    projection, api, tenant_secrets = _harness()
    tenant_secrets.records.clear()

    with pytest.raises(TenantSecretResolutionError) as caught:
        await _ensure(projection)

    assert api.calls == []
    assert DSN_SENTINEL not in str(caught.value)


async def test_missing_k8s_secret_is_created_again():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    del api.objects[(reference.namespace, reference.name)]
    api.calls.clear()

    recreated = await _ensure(projection)

    assert recreated == reference
    assert _methods(api) == ["GET", "POST"]
    assert _secret(api, reference)["metadata"]["resourceVersion"] == "1"


async def test_same_ref_dsn_rotation_patches_same_instance():
    projection, api, tenant_secrets = _harness()
    reference = await _ensure(projection)
    tenant_secrets.set(COMPANY, PLUGIN, DSN_REF, ROTATED_DSN_SENTINEL)
    api.calls.clear()

    rotated = await _ensure(projection)

    assert rotated.name == reference.name
    assert rotated.dsn_ref_sha256 == reference.dsn_ref_sha256
    assert _methods(api) == ["GET", "PATCH"]
    secret = _secret(api, rotated)
    assert secret["metadata"]["resourceVersion"] == "2"
    assert _decoded(secret, "TENANT_DB_DSN") == ROTATED_DSN_SENTINEL

    api.calls.clear()
    repeated = await _ensure(projection)
    assert repeated == rotated
    assert _methods(api) == ["GET"]


async def test_gateway_change_with_same_ref_is_managed_patch():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    api.calls.clear()

    updated = await _ensure(projection, gateway_url="http://plugin-gateway:9090")

    assert updated.name == reference.name
    assert updated.dsn_ref_sha256 == reference.dsn_ref_sha256
    assert _methods(api) == ["GET", "PATCH"]
    assert _decoded(_secret(api, updated), "GATEWAY_URL") == "http://plugin-gateway:9090"
    assert _secret(api, updated)["metadata"]["resourceVersion"] == "2"


async def test_unexpected_key_is_rejected_without_deleting_secret():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    secret = _secret(api, reference)
    secret["data"]["PLUGIN_CLAIMS"] = _encoded("unexpected")
    original = copy.deepcopy(secret)
    api.calls.clear()

    with pytest.raises(SecretDriftError):
        await _ensure(projection)

    assert _methods(api) == ["GET"]
    assert _secret(api, reference) == original


async def test_empty_existing_dsn_is_rejected():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    _secret(api, reference)["data"]["TENANT_DB_DSN"] = ""
    api.calls.clear()

    with pytest.raises(SecretDriftError):
        await _ensure(projection)

    assert _methods(api) == ["GET"]


async def test_empty_source_dsn_is_rejected_before_k8s_access():
    projection, api, tenant_secrets = _harness()
    tenant_secrets.set(COMPANY, PLUGIN, DSN_REF, "  ")

    with pytest.raises(TenantSecretResolutionError):
        await _ensure(projection)

    assert api.calls == []


async def test_cross_tenant_ref_is_rejected_before_store_or_k8s_access():
    projection, api, tenant_secrets = _harness()
    foreign_ref = DSN_REF.replace(COMPANY, "co_other")

    with pytest.raises(SecretProjectionConfigError):
        await _ensure(projection, dsn_ref=foreign_ref)

    assert tenant_secrets.get_calls == []
    assert api.calls == []


async def test_no_plaintext_in_logs_result_or_sanitized_exceptions(caplog):
    caplog.set_level(logging.DEBUG)
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    path = f"/api/v1/namespaces/{NAMESPACE}/secrets/{reference.name}"
    api.errors[("GET", path)] = RuntimeError(
        f"transport accidentally included {DSN_SENTINEL}"
    )

    with pytest.raises(SecretUnavailableError) as caught:
        await _ensure(projection)

    encoded_sentinel = _encoded(DSN_SENTINEL)
    for surface in (
        caplog.text,
        str(caught.value),
        repr(caught.value),
        str(reference),
        repr(reference),
    ):
        assert DSN_SENTINEL not in surface
        assert ROTATED_DSN_SENTINEL not in surface
        assert WEBHOOK_SENTINEL not in surface
        assert ROTATED_WEBHOOK_SENTINEL not in surface
        assert encoded_sentinel not in surface


async def test_instance_isolation_during_rotation():
    projection, api, tenant_secrets = _harness()
    first = await _ensure(projection, instance_id=INSTANCE)
    second = await _ensure(projection, instance_id=OTHER_INSTANCE)
    second_before = copy.deepcopy(_secret(api, second))
    tenant_secrets.set(COMPANY, PLUGIN, ROTATED_DSN_REF, ROTATED_DSN_SENTINEL)

    rotated_first = await _ensure(projection, instance_id=INSTANCE, dsn_ref=ROTATED_DSN_REF)

    assert first.name != second.name
    assert first.labels[INSTANCE_LABEL] != second.labels[INSTANCE_LABEL]
    assert rotated_first.name == first.name
    assert _decoded(_secret(api, rotated_first), "TENANT_DB_DSN") == ROTATED_DSN_SENTINEL
    assert _secret(api, second) == second_before


async def test_long_identity_uses_bounded_hashed_canonical_name():
    projection, api, tenant_secrets = _harness()
    long_plugin = "mod_" + "x" * 100
    long_instance = "inst_" + "y" * 100
    long_ref = (
        f"file://ai_keys/{_tenant_key(COMPANY, long_plugin)}.secret"
    )
    tenant_secrets.set(COMPANY, long_plugin, long_ref, DSN_SENTINEL)

    reference = await _ensure(
        projection,
        plugin_id=long_plugin,
        instance_id=long_instance,
        dsn_ref=long_ref,
    )

    assert len(reference.name) <= 63
    assert re.fullmatch(r"[a-z](?:[-a-z0-9]*[a-z0-9])?", reference.name)
    assert reference.name == plugin_resource_name(long_plugin, long_instance)
    assert _decoded(_secret(api, reference), "PLUGIN_ID") == long_plugin


async def test_legacy_key_shape_and_managed_webhook_key_addition():
    projection, api, _tenant_secrets = _harness()
    legacy = await _ensure(projection, webhook_secret_ref=None)
    assert set(_secret(api, legacy)["data"]) == set(BASE_SECRET_KEYS)

    upgraded = await _ensure(projection)

    assert upgraded.name == legacy.name
    assert _methods(api) == ["GET", "POST", "GET", "PATCH"]
    assert set(_secret(api, upgraded)["data"]) == set(SECRET_KEYS)
    assert _decoded(_secret(api, upgraded), WEBHOOK_SECRET_KEY) == WEBHOOK_SENTINEL


async def test_same_ref_webhook_rotation_patches_and_then_is_read_only():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    projection._webhook_resolver.value = ROTATED_WEBHOOK_SENTINEL
    api.calls.clear()

    rotated = await _ensure(projection)

    assert rotated == reference
    assert _methods(api) == ["GET", "PATCH"]
    assert _decoded(_secret(api, rotated), WEBHOOK_SECRET_KEY) == ROTATED_WEBHOOK_SENTINEL
    assert _decoded(_secret(api, rotated), "TENANT_DB_DSN") == DSN_SENTINEL

    api.calls.clear()
    repeated = await _ensure(projection)
    assert repeated == rotated
    assert _methods(api) == ["GET"]


async def test_webhook_resolution_is_fail_closed_before_k8s_write():
    projection, api, _tenant_secrets = _harness()
    foreign_ref = WEBHOOK_REF.replace(COMPANY, "co_other")

    with pytest.raises(WebhookSecretResolutionError) as caught:
        await _ensure(projection, webhook_secret_ref=foreign_ref)

    assert api.calls == []
    assert WEBHOOK_SENTINEL not in str(caught.value)
    assert ROTATED_WEBHOOK_SENTINEL not in str(caught.value)


async def test_legacy_call_does_not_remove_projected_webhook_key():
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)
    original = copy.deepcopy(_secret(api, reference))
    api.calls.clear()

    with pytest.raises(SecretDriftError):
        await _ensure(projection, webhook_secret_ref=None)

    assert _methods(api) == ["GET"]
    assert _secret(api, reference) == original


async def test_webhook_plaintext_is_absent_from_logs_urls_and_reference(caplog):
    caplog.set_level(logging.DEBUG)
    projection, api, _tenant_secrets = _harness()
    reference = await _ensure(projection)

    surfaces = [caplog.text, str(reference), repr(reference)]
    surfaces.extend(call["path"] for call in api.calls)
    for surface in surfaces:
        assert WEBHOOK_SENTINEL not in surface
        assert ROTATED_WEBHOOK_SENTINEL not in surface
        assert _encoded(WEBHOOK_SENTINEL) not in surface


async def test_k8s_secret_refs_keep_exact_resolution_and_four_key_projection():
    projection, api, tenant_secrets = _harness()

    # An opaque k8s ref must still match the trusted record for this tenant key.
    with pytest.raises(TenantSecretResolutionError):
        await _ensure(
            projection,
            dsn_ref=K8S_DSN_REF,
            webhook_secret_ref=K8S_WEBHOOK_REF,
        )
    assert api.calls == []

    tenant_secrets.set(COMPANY, PLUGIN, K8S_DSN_REF, ROTATED_DSN_SENTINEL)
    reference = await _ensure(
        projection,
        dsn_ref=K8S_DSN_REF,
        webhook_secret_ref=K8S_WEBHOOK_REF,
    )

    secret = _secret(api, reference)
    assert set(secret["data"]) == {"GATEWAY_URL", "PLUGIN_ID", "TENANT_DB_DSN", "WEBHOOK_SECRET"}
    assert _decoded(secret, "TENANT_DB_DSN") == ROTATED_DSN_SENTINEL
    assert _decoded(secret, WEBHOOK_SECRET_KEY) == WEBHOOK_SENTINEL
    assert reference.dsn_ref_sha256 == hashlib.sha256(K8S_DSN_REF.encode()).hexdigest()
    assert projection._webhook_resolver.calls[-1] == (
        COMPANY,
        PLUGIN,
        INSTANCE,
        K8S_WEBHOOK_REF,
    )
