"""Тесты typed-портов: TTL-контракт, ``ScopedCredentials``, ``ObjectInfo``.

Проверяем, что контракт не несёт ``bytes``/payload и что все четыре порта —
runtime-checkable Protocol (плагин зависит от контракта, а не от boto3/mc).
"""

from __future__ import annotations

import dataclasses

import pytest

from prodavan.application.plugins.objects import (
    MAX_TTL_SECONDS,
    MAX_STS_TTL_SECONDS,
    MIN_TTL_SECONDS,
    MIN_STS_TTL_SECONDS,
    AuditPort,
    BucketAdminPort,
    InvalidTtl,
    ObjectInfo,
    PresignPort,
    ScopedCredentials,
    StsPort,
    validate_sts_ttl,
    validate_ttl,
)
from tests.unit.plugins.objects.conftest import (  # type: ignore[import-not-found]
    FakeAudit,
    FakeBuckets,
    FakePresigner,
    FakeSts,
)


def test_ttl_bounds_are_60_and_3600() -> None:
    assert MIN_TTL_SECONDS == 60
    assert MAX_TTL_SECONDS == 3600


@pytest.mark.parametrize("ttl", [60, 61, 300, 900, 3599, 3600])
def test_validate_ttl_accepts_in_range(ttl: int) -> None:
    assert validate_ttl(ttl) == ttl


@pytest.mark.parametrize("ttl", [-1, 0, 1, 59, 3601, 7200, 10**9])
def test_validate_ttl_rejects_out_of_range(ttl: int) -> None:
    with pytest.raises(InvalidTtl):
        validate_ttl(ttl)


@pytest.mark.parametrize("ttl", [None, "eternity", object()])
def test_validate_ttl_rejects_non_integer(ttl: object) -> None:
    with pytest.raises(InvalidTtl):
        validate_ttl(ttl)  # type: ignore[arg-type]


@pytest.mark.parametrize("ttl", [900, 901, 1200, 3599, 3600])
def test_validate_sts_ttl_accepts_standard_range(ttl: int) -> None:
    assert validate_sts_ttl(ttl) == ttl


@pytest.mark.parametrize("ttl", [-1, 0, 59, 60, 899, 3601, 7200])
def test_validate_sts_ttl_rejects_out_of_range(ttl: int) -> None:
    with pytest.raises(InvalidTtl):
        validate_sts_ttl(ttl)


def test_presign_and_sts_ttl_contracts_are_independent() -> None:
    assert MIN_TTL_SECONDS == 60
    assert MIN_STS_TTL_SECONDS == 900
    assert MAX_TTL_SECONDS == MAX_STS_TTL_SECONDS == 3600
    assert validate_ttl(60) == 60
    assert validate_sts_ttl(900) == 900
    with pytest.raises(InvalidTtl):
        validate_sts_ttl(60)


def test_all_ports_are_runtime_checkable_protocols() -> None:
    assert isinstance(FakeBuckets(), BucketAdminPort)
    assert isinstance(FakeSts(), StsPort)
    assert isinstance(FakePresigner(), PresignPort)
    assert isinstance(FakeAudit(), AuditPort)


def test_fakes_satisfy_runtime_protocol_with_real_signatures() -> None:
    # ``isinstance`` для runtime_checkable проверяет только наличие атрибутов,
    # поэтому дополнительно убеждаемся, что сигнатуры совпадают с контрактом.
    import inspect

    for name in ("ensure_bucket", "delete_bucket", "delete_prefix", "set_quota", "get_usage", "list_prefix"):
        assert hasattr(FakeBuckets, name), name
    assert "policy" in inspect.signature(FakeBuckets.ensure_bucket).parameters
    assert "quota_bytes" in inspect.signature(FakeBuckets.ensure_bucket).parameters
    assert "session_policy" in inspect.signature(FakeSts.assume_role).parameters
    assert "ttl_seconds" in inspect.signature(FakePresigner.presign_get).parameters
    assert "ttl_seconds" in inspect.signature(FakePresigner.presign_head).parameters


def test_scoped_credentials_carry_explicit_scope_not_policy_variables() -> None:
    creds = ScopedCredentials(
        access_key="ASIA_TEMP",
        secret_key="s",
        session_token="tok",
        bucket="tenant-co_123",
        prefix="module-mod_equipment/",
        expires_at=1_800_000_900.0,
        policy={"Version": "2012-10-17", "Statement": []},
    )
    assert creds.bucket.startswith("tenant-")
    assert creds.prefix.startswith("module-")
    assert "${" not in creds.bucket + creds.prefix
    assert dataclasses.is_dataclass(creds)
    assert creds.policy["Version"] == "2012-10-17"


def test_object_info_is_metadata_only() -> None:
    """В листинге/аудите только ключ/размер/etag — без contents/body/bytes."""
    info = ObjectInfo(key="module-mod_equipment/a.txt", size=42, etag="abc")
    fields = {f.name for f in dataclasses.fields(info)}
    assert fields == {"key", "size", "etag"}
    assert not any("byte" in f or "content" in f or "payload" in f for f in fields)


@pytest.mark.asyncio
async def test_presigner_never_receives_payload() -> None:
    """Контракт presigned: bucket + key + ttl, никаких ``data``/``Body``."""
    presigner = FakePresigner()
    url = await presigner.presign_put(
        bucket="tenant-co_123", key="module-mod_equipment/a.txt", ttl_seconds=900
    )
    assert "X-Amz-Expires=900" in url
    assert set(presigner.calls[0]) == {"method", "bucket", "key", "ttl_seconds"}
