"""Real failure evidence must leave scoped credentials typed-unwired."""
from __future__ import annotations

from dataclasses import replace
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters._shared import CapabilityBackendError
from prodavan.application.plugins.capabilities.adapters.objects import MinioObjectsAdapter
from prodavan.application.plugins.capabilities.contracts import (
    ObjectPermission,
    ObjectsScopedCredentialsRequest,
)
from prodavan.infrastructure.plugins.minio.adapters import MinioSts

from .conftest import ObjectStack
from .resources import MinioAcceptanceEnvironment, new_minio_sts
from .support import (
    MemorySecretStore,
    envelope,
    require,
    s3_status_and_code,
)

pytestmark = pytest.mark.minio_acceptance


class ObservedCanaryClient:
    """Delegate real boto3 IO and retain only safe status/code observations."""

    def __init__(self, client: Any, operations: list[tuple[str, int | None, str]], ambiguous: bool) -> None:
        self._client = client
        self._operations = operations
        self._ambiguous = ambiguous

    def _call(self, name: str, call: Any, **kwargs: Any) -> Any:
        try:
            response = call(**kwargs)
        except Exception as exc:
            status, code = s3_status_and_code(exc)
            self._operations.append((name, status, code))
            raise
        status, code = s3_status_and_code(response)
        if self._ambiguous and name == "head_bucket":
            # The real HeadBucket returned HTTP 200. Remove only its typed status
            # projection to prove that an ambiguous adapter response is not success.
            self._operations.append((name, None, ""))
            projected = dict(response)
            projected.pop("ResponseMetadata", None)
            return projected
        self._operations.append((name, status, code))
        return response

    def head_bucket(self, **kwargs: Any) -> Any:
        return self._call("head_bucket", self._client.head_bucket, **kwargs)

    def head_object(self, **kwargs: Any) -> Any:
        return self._call("head_object", self._client.head_object, **kwargs)

    def list_objects_v2(self, **kwargs: Any) -> Any:
        return self._call("list_objects_v2", self._client.list_objects_v2, **kwargs)

    def close(self) -> None:
        self._client.close()


def canary_factory(
    environment: MinioAcceptanceEnvironment,
    operations: list[tuple[str, int | None, str]],
    *,
    ambiguous: bool,
) -> Any:
    def build(*, credentials: Any, timeout_seconds: int) -> ObservedCanaryClient:
        client = environment.new_s3_client(
            access_key=credentials.access_key,
            secret_key=credentials.secret_key,
            session_token=credentials.session_token,
        )
        return ObservedCanaryClient(client, operations, ambiguous)

    return build


def scoped_request(
    environment: MinioAcceptanceEnvironment,
    *,
    missing_bucket: bool,
) -> ObjectsScopedCredentialsRequest:
    identity = environment.identity
    company = identity.missing_company_id if missing_bucket else identity.company_id
    return ObjectsScopedCredentialsRequest(
        bucket=identity.missing_bucket if missing_bucket else identity.bucket,
        key_prefix=identity.prefix,
        permissions=frozenset({ObjectPermission.READ}),
        ttl_seconds=900,
        envelope=envelope(environment, company_id=company),
    )


@pytest.mark.parametrize("scenario", ["missing_bucket", "missing_key", "ambiguous"])
async def test_missing_or_ambiguous_canary_stays_unwired_without_presign_fallback(
    object_stack: ObjectStack,
    scenario: str,
) -> None:
    environment = object_stack.environment
    identity = environment.identity
    overrides: dict[str, str] = {}
    if scenario == "missing_bucket":
        overrides["sts_probe_bucket"] = identity.missing_bucket
    elif scenario == "missing_key":
        overrides["sts_probe_key"] = identity.missing_key
    config = replace(environment.plugin_config, **overrides)

    operations: list[tuple[str, int | None, str]] = []
    sts = new_minio_sts(
        environment,
        config,
        canary_client_factory=canary_factory(
            environment,
            operations,
            ambiguous=scenario == "ambiguous",
        ),
    )
    store = MemorySecretStore()
    objects = MinioObjectsAdapter(
        buckets=object_stack.buckets,
        sts=sts,
        presigner=object_stack.presigner,
        secret_store=store,
        timeout_seconds=20,
    )
    try:
        require(await sts.probe() is False, "invalid canary became availability evidence")
        require(sts.scoped_credentials_wired is False, "failed canary wired scoped credentials")
        require(operations, "failure happened before the real canary operation")

        if scenario == "missing_bucket":
            name, status, code = operations[0]
            require(name == "head_bucket", "missing bucket did not reach HeadBucket")
            require(status in {403, 404}, "missing bucket returned an unexpected status")
            if status == 404:
                require(code.lower() in {"nosuchbucket", "404", "notfound"}, "missing bucket code was ambiguous")
            else:
                require(code.lower() in {"403", "accessdenied"}, "missing bucket denial was ambiguous")
        elif scenario == "missing_key":
            require([item[0] for item in operations] == ["head_bucket", "head_object"], "missing-key canary path mismatch")
            require(operations[0][1] == 200, "existing canary bucket was not proven first")
            require(operations[1][1] in {403, 404}, "missing key returned an unexpected status")
            if operations[1][1] == 404:
                require(operations[1][2].lower() in {"nosuchkey", "404", "notfound"}, "missing key code was ambiguous")
            else:
                require(operations[1][2].lower() in {"403", "accessdenied"}, "missing key denial was ambiguous")
        else:
            require(operations[0] == ("head_bucket", None, ""), "ambiguous projection was not exercised")

        with pytest.raises(CapabilityBackendError) as caught:
            await objects.scoped_credentials(
                scoped_request(environment, missing_bucket=scenario == "missing_bucket")
            )
        require(caught.value.code == "objects_scoped_credentials_sts_unwired", "wrong typed-unwired error")
        require(not store.values and store.put_calls == 0, "failed canary stored credentials or a presign fallback")
    finally:
        await objects.aclose()
        await sts.aclose()


@pytest.mark.parametrize("legacy_name", ["io_test_sts_available", "minio_sts_available"])
async def test_legacy_availability_boolean_cannot_enable_scoped_credentials(
    object_stack: ObjectStack,
    legacy_name: str,
) -> None:
    environment = object_stack.environment
    with pytest.raises(TypeError):
        MinioSts(config=environment.plugin_config, **{legacy_name: True})

    # Even an enabled/probe-configured adapter has no availability before real IO.
    sts = new_minio_sts(environment, environment.plugin_config)
    store = MemorySecretStore()
    objects = MinioObjectsAdapter(
        buckets=object_stack.buckets,
        sts=sts,
        presigner=object_stack.presigner,
        secret_store=store,
        timeout_seconds=20,
    )
    try:
        with pytest.raises(CapabilityBackendError) as caught:
            await objects.scoped_credentials(scoped_request(environment, missing_bucket=False))
        require(caught.value.code == "objects_scoped_credentials_sts_unwired", "legacy gate became operation evidence")
        require(not store.values and store.put_calls == 0, "unprobed operation persisted a fallback")
    finally:
        await objects.aclose()
        await sts.aclose()
