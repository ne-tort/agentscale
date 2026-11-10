"""Exact-key objects.presign acceptance with real HTTP SigV4 operations."""
from __future__ import annotations

import asyncio
import time
from dataclasses import replace
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
import pytest

from prodavan.application.plugins.capabilities.adapters._shared import CapabilityAccessDenied
from prodavan.application.plugins.capabilities.contracts import (
    ObjectPresignMethod,
    ObjectsPresignRequest,
    TypedContractError,
)
from prodavan.application.plugins.objects.errors import BucketUnavailable, InvalidTtl
from prodavan.infrastructure.plugins.minio.adapters import MinioPresigner

from .conftest import ObjectStack
from .support import (
    LeakSentinel,
    http_request,
    presign_request,
    require,
    require_http_denied,
    s3_status_and_code,
    tampered_path,
    validate_signed_response,
    wait_for_signature_expiry,
)

pytestmark = pytest.mark.minio_acceptance
_DENIAL_CODES = frozenset({"AccessDenied", "SignatureDoesNotMatch"})


@pytest.mark.parametrize(
    ("method", "ttl_seconds"),
    [
        (ObjectPresignMethod.GET, 60),
        (ObjectPresignMethod.PUT, 900),
        (ObjectPresignMethod.HEAD, 3600),
    ],
)
async def test_objects_presign_exact_key_has_six_sigv4_fields_and_real_http_io(
    object_stack: ObjectStack,
    http_client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
    isolated_transport_environment: dict[str, str],
    method: ObjectPresignMethod,
    ttl_seconds: int,
) -> None:
    environment = object_stack.environment
    identity = environment.identity
    key = identity.upload_key if method is ObjectPresignMethod.PUT else identity.fixed_key
    request = presign_request(
        environment,
        method=method,
        ttl_seconds=ttl_seconds,
        object_key=key,
    )
    response = await object_stack.objects.presign(request)
    validate_signed_response(response, environment, request)
    sentinel = LeakSentinel.for_environment(environment)
    sentinel.add_url(response.url)

    if method is ObjectPresignMethod.PUT:
        payload = b"real-minio-exact-key-put"
        http_response = await http_request(http_client, "PUT", response.url, content=payload)
        require(http_response.status_code == 200, "presigned PUT did not return HTTP 200")
        head = await asyncio.to_thread(
            environment.root_s3.head_object,
            Bucket=identity.bucket,
            Key=identity.upload_key,
        )
        require(s3_status_and_code(head)[0] == 200, "PUT object HEAD did not return HTTP 200")
        require(head.get("ContentLength") == len(payload), "presigned PUT stored an unexpected length")
    elif method is ObjectPresignMethod.GET:
        http_response = await http_request(http_client, "GET", response.url)
        require(http_response.status_code == 200, "presigned GET did not return HTTP 200")
        require(http_response.content == b"", "fixed canary GET was not zero-byte")
        require(http_response.headers.get("content-length") == "0", "fixed GET ContentLength was not zero")
    else:
        http_response = await http_request(http_client, "HEAD", response.url)
        require(http_response.status_code == 200, "presigned HEAD did not return HTTP 200")
        require(http_response.headers.get("content-length") == "0", "fixed HEAD ContentLength was not zero")

    sentinel.assert_clean(caplog.text)
    for ambient in isolated_transport_environment.values():
        require(ambient not in caplog.text, "ambient proxy/application sentinel leaked")


@pytest.mark.parametrize("ttl_seconds", [59, 3601])
async def test_objects_presign_rejects_out_of_range_ttl_before_signing(
    object_stack: ObjectStack,
    ttl_seconds: int,
) -> None:
    environment = object_stack.environment
    with pytest.raises(TypedContractError):
        presign_request(environment, method=ObjectPresignMethod.GET, ttl_seconds=ttl_seconds)
    with pytest.raises(InvalidTtl):
        await object_stack.presigner.presign_get(
            bucket=environment.identity.bucket,
            key=environment.identity.fixed_key,
            ttl_seconds=ttl_seconds,
        )


@pytest.mark.parametrize(
    ("bucket_override", "key_override", "expected_code"),
    [
        ("foreign_bucket", None, "objects_bucket_tenant_mismatch"),
        (None, "foreign_key", "objects_key_outside_plugin_scope"),
        (None, "wildcard", "objects_key_outside_plugin_scope"),
        (None, "traversal", "objects_key_outside_plugin_scope"),
        (None, "encoded_traversal", "objects_key_outside_plugin_scope"),
        (None, "list_prefix", "objects_key_outside_plugin_scope"),
    ],
)
async def test_objects_presign_denies_cross_scope_wildcard_list_and_traversal(
    object_stack: ObjectStack,
    bucket_override: str | None,
    key_override: str | None,
    expected_code: str,
) -> None:
    environment = object_stack.environment
    identity = environment.identity
    keys = {
        "foreign_key": identity.foreign_key,
        "wildcard": f"{identity.prefix}*",
        "traversal": f"{identity.prefix}../{identity.foreign_key}",
        "encoded_traversal": f"{identity.prefix}%2e%2e%2f{identity.foreign_key}",
        "list_prefix": identity.prefix,
    }
    request = presign_request(
        environment,
        method=ObjectPresignMethod.GET,
        ttl_seconds=120,
        bucket=identity.foreign_bucket if bucket_override == "foreign_bucket" else None,
        object_key=keys.get(key_override or "", identity.fixed_key),
    )
    with pytest.raises(CapabilityAccessDenied) as caught:
        await object_stack.objects.presign(request)
    require(caught.value.code == expected_code, "objects.presign denied for the wrong reason")


@pytest.mark.parametrize("method", ["LIST", "POST", "DELETE"])
async def test_objects_presign_rejects_non_object_methods_at_typed_boundary(
    object_stack: ObjectStack,
    method: str,
) -> None:
    environment = object_stack.environment
    with pytest.raises(TypedContractError):
        ObjectsPresignRequest(
            bucket=environment.identity.bucket,
            object_key=environment.identity.fixed_key,
            method=method,  # type: ignore[arg-type]
            ttl_seconds=120,
            envelope=presign_request(
                environment,
                method=ObjectPresignMethod.GET,
                ttl_seconds=120,
            ).envelope,
        )


async def test_presigned_url_cannot_be_tampered_to_foreign_key_bucket_list_or_method(
    object_stack: ObjectStack,
    http_client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    environment = object_stack.environment
    identity = environment.identity
    request = presign_request(environment, method=ObjectPresignMethod.GET, ttl_seconds=900)
    signed = await object_stack.objects.presign(request)
    validate_signed_response(signed, environment, request)
    sentinel = LeakSentinel.for_environment(environment)
    sentinel.add_url(signed.url)

    attempts = (
        tampered_path(signed.url, bucket=identity.bucket, key=identity.foreign_key),
        tampered_path(signed.url, bucket=identity.foreign_bucket, key=identity.fixed_key),
        tampered_path(signed.url, bucket=identity.bucket, key="")
        + "&list-type=2&prefix=" + quote(identity.prefix, safe=""),
    )
    for url in attempts:
        response = await http_request(http_client, "GET", url)
        require_http_denied(response, codes=_DENIAL_CODES)

    wrong_method = await http_request(http_client, "PUT", signed.url, content=b"")
    require_http_denied(wrong_method, codes=_DENIAL_CODES)
    sentinel.assert_clean(caplog.text)


async def test_presign_rejects_admin_signing_and_endpoint_substitution(
    object_stack: ObjectStack,
    caplog: pytest.LogCaptureFixture,
) -> None:
    environment = object_stack.environment
    identity = environment.identity
    sentinel = LeakSentinel.for_environment(environment)

    admin_client = environment.new_s3_client(
        access_key=identity.admin_user,
        secret_key=identity.admin_secret,
    )
    admin_signer = MinioPresigner(
        config=environment.plugin_config,
        client=admin_client,
        expected_prefix=identity.prefix,
    )
    try:
        with pytest.raises(BucketUnavailable):
            await admin_signer.presign_get(
                bucket=identity.bucket,
                key=identity.fixed_key,
                ttl_seconds=120,
            )
    finally:
        await admin_signer.aclose()

    parsed = urlsplit(environment.config.endpoint)
    wrong_endpoint = urlunsplit((parsed.scheme, "presign-endpoint-mismatch.invalid", "", "", ""))
    mismatched_config = replace(environment.plugin_config, public_endpoint=wrong_endpoint)
    actual_client = environment.new_s3_client(
        access_key=identity.presign_user,
        secret_key=identity.presign_secret,
    )
    mismatched_signer = MinioPresigner(
        config=mismatched_config,
        client=actual_client,
        expected_prefix=identity.prefix,
    )
    try:
        with pytest.raises(BucketUnavailable):
            await mismatched_signer.presign_get(
                bucket=identity.bucket,
                key=identity.fixed_key,
                ttl_seconds=120,
            )
    finally:
        await mismatched_signer.aclose()

    sentinel.assert_clean(caplog.text)


@pytest.mark.slow
async def test_presigned_get_is_denied_after_real_ttl_60_expiry(
    object_stack: ObjectStack,
    http_client: httpx.AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    environment = object_stack.environment
    request = presign_request(environment, method=ObjectPresignMethod.GET, ttl_seconds=60)
    signed = await object_stack.objects.presign(request)
    validate_signed_response(signed, environment, request)
    sentinel = LeakSentinel.for_environment(environment)
    sentinel.add_url(signed.url)

    before = await http_request(http_client, "GET", signed.url)
    require(before.status_code == 200, "TTL=60 URL was not usable before expiry")
    expires_at = await wait_for_signature_expiry(signed.url)

    # MinIO and host clocks can differ slightly. Poll real time only; never fake
    # the adapter clock and never reinterpret NoSuchKey/NoSuchBucket as expiry.
    for _ in range(10):
        after = await http_request(http_client, "GET", signed.url)
        if after.status_code == 200:
            await asyncio.sleep(1.0)
            continue
        require_http_denied(after, codes=frozenset({"AccessDenied"}))
        break
    else:
        raise AssertionError("presigned URL remained usable after its actual signed expiry")
    require(time.time() >= expires_at, "expiry denial happened before the signed deadline")
    sentinel.assert_clean(caplog.text)
