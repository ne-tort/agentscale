"""Unit-тесты маппинга ошибок и typed-тел (задача 04).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ (Verification freeze).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError as PydanticValidationError

from prodavan.application.plugins.capabilities.errors import (
    QuotaExceededError,
    RevokedGrant,
)
from prodavan.application.plugins.capabilities.ports import AdapterMissingError, UnknownCapabilityError
from prodavan.application.plugins.identity.types import PluginTokenError, PluginTokenErrorCode
from prodavan.application.plugins.transport.bodies import (
    CacheGetBody,
    CacheSetBody,
    DocsQueryBody,
    NetworkRequestBody,
    ObjectsCredentialsBody,
    ObjectsListBody,
)
from prodavan.application.plugins.transport.errors import (
    PluginProblem,
    PluginTransportError,
    normalize_code,
    problem_from_code,
    problem_from_decision,
    problem_from_exception,
)


# ---------------------------------------------------------------------------
# normalize_code / problem_from_code
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("QUOTA_EXCEEDED", "quota_exceeded"),
        ("quota_exceeded", "quota_exceeded"),
        ("adapter-missing", "adapter_missing"),
        ("GRANT.REVOKED", "grant_revoked"),
        (None, ""),
    ],
)
def test_normalize_code(raw, expected: str) -> None:
    assert normalize_code(raw) == expected


@pytest.mark.parametrize(
    ("code", "status", "canonical"),
    [
        ("QUOTA_EXCEEDED", 429, "QUOTA_EXCEEDED"),
        ("quota_exceeded", 429, "QUOTA_EXCEEDED"),
        ("GRANT_REVOKED", 403, "GRANT_REVOKED"),
        ("ADAPTER_MISSING", 503, "ADAPTER_MISSING"),
        ("CAPABILITY_UNKNOWN", 503, "CAPABILITY_UNKNOWN"),
        ("INVALID_REQUEST", 422, "VALIDATION_ERROR"),
        ("IDENTITY_DENIED", 401, "UNAUTHORIZED"),
        ("FORBIDDEN_SURFACE", 403, "FORBIDDEN_SURFACE"),
        ("INVALID_SIGNATURE", 401, "INVALID_SIGNATURE"),
    ],
)
def test_problem_from_code(code: str, status: int, canonical: str) -> None:
    problem = problem_from_code(code, "detail")
    assert problem is not None
    assert problem.status == status
    assert problem.code == canonical


def test_unknown_code_returns_none() -> None:
    assert problem_from_code("SOMETHING_TOTALLY_NEW", "x") is None


# ---------------------------------------------------------------------------
# problem_from_decision (штатный deny НЕ исключение)
# ---------------------------------------------------------------------------


def test_allow_decision_returns_none() -> None:
    assert problem_from_decision("allow", "ALLOWED", detail="") is None
    assert problem_from_decision("allow", "allowed", detail="") is None


def test_quota_deny_maps_to_429_by_code() -> None:
    problem = problem_from_decision("deny", "QUOTA_EXCEEDED", detail="ops exceeded")
    assert problem is not None
    assert problem.status == 429
    assert problem.code == "QUOTA_EXCEEDED"


def test_deny_with_allow_flag_is_still_rejected() -> None:
    """Fail-closed: несовпадение decision/code не превращается в 200."""
    problem = problem_from_decision("allow", "QUOTA_EXCEEDED", detail="contradiction")
    assert problem is not None
    assert problem.status == 429


def test_deny_without_code_is_403() -> None:
    problem = problem_from_decision("deny", "", detail="no reason")
    assert problem is not None
    assert problem.status == 403
    assert problem.code == "DECISION_DENIED"


def test_unknown_deny_code_is_403() -> None:
    problem = problem_from_decision("deny", "SOMETHING_NEW", detail="x")
    assert problem is not None
    assert problem.status == 403


# ---------------------------------------------------------------------------
# problem_from_exception (fallback)
# ---------------------------------------------------------------------------


def test_plugin_token_error_maps_401() -> None:
    exc = PluginTokenError(code=PluginTokenErrorCode.INVALID_SIGNATURE, detail="bad signature")
    problem = problem_from_exception(exc)
    assert problem is not None
    assert problem.status == 401
    assert problem.code == "INVALID_SIGNATURE"


def test_plugin_token_forbidden_surface_maps_403() -> None:
    exc = PluginTokenError(code=PluginTokenErrorCode.FORBIDDEN_SURFACE, detail="/agent/events")
    problem = problem_from_exception(exc)
    assert problem is not None
    assert problem.status == 403
    assert problem.code == "FORBIDDEN_SURFACE"


def test_plugin_token_revoked_maps_403() -> None:
    exc = PluginTokenError(code=PluginTokenErrorCode.REVOKED, detail="grant revoked")
    problem = problem_from_exception(exc)
    assert problem is not None and problem.status == 403


def test_adapter_missing_maps_503() -> None:
    problem = problem_from_exception(AdapterMissingError("cache"))
    assert problem is not None
    assert problem.status == 503
    assert problem.code == "ADAPTER_MISSING"


def test_unknown_capability_maps_503() -> None:
    problem = problem_from_exception(UnknownCapabilityError("db.direct"))
    assert problem is not None
    assert problem.status == 503


def test_revoked_grant_maps_403() -> None:
    problem = problem_from_exception(RevokedGrant("revoked"))
    assert problem is not None
    assert problem.status == 403


def test_quota_exceeded_maps_429() -> None:
    problem = problem_from_exception(QuotaExceededError("limit"))
    assert problem is not None
    assert problem.status == 429


def test_plugin_transport_error_passthrough() -> None:
    inner = PluginProblem(429, "QUOTA_EXCEEDED", "Quota Exceeded", "x")
    problem = problem_from_exception(PluginTransportError(inner))
    assert problem == inner


def test_unrelated_exception_is_none() -> None:
    assert problem_from_exception(RuntimeError("boom")) is None


# ---------------------------------------------------------------------------
# bodies: запрет generic dispatch / identity, типизация
# ---------------------------------------------------------------------------


def test_valid_cache_body() -> None:
    body = CacheGetBody(key="k")
    assert body.key == "k"


@pytest.mark.parametrize(
    "extra",
    [
        {"sql": "SELECT 1"},
        {"command": "ls"},
        {"args": ["-rf"]},
        {"raw": "bytes"},
        {"params": {"a": 1}},
        {"payload": {}},
        {"company_id": "evil"},
        {"grant_id": "evil"},
        {"artifact_digest": "evil"},
        {"envelope": {}},
        {"unexpected": 1},
    ],
)
def test_forbidden_fields_rejected(extra: dict) -> None:
    with pytest.raises(PydanticValidationError):
        CacheGetBody.model_validate({"key": "k", **extra})


def test_cache_set_accepts_value() -> None:
    body = CacheSetBody(key="k", value="v")
    assert body.value == "v"


def test_cache_set_rejects_dict_value() -> None:
    with pytest.raises(PydanticValidationError):
        CacheSetBody.model_validate({"key": "k", "value": {"a": 1}})


def test_docs_query_nested_forbidden_field() -> None:
    """Вложенный предикат тоже не может нести sql/command/args/raw."""
    with pytest.raises(PydanticValidationError):
        DocsQueryBody.model_validate(
            {
                "namespace": "ns",
                "collection": "c",
                "predicates": [
                    {"field": "f", "operator": "eq", "values": ["x"], "sql": "SELECT 1"}
                ],
                "limit": 10,
            }
        )


def test_docs_query_range_arity() -> None:
    with pytest.raises(PydanticValidationError):
        DocsQueryBody.model_validate(
            {
                "namespace": "ns",
                "collection": "c",
                "predicates": [
                    {"field": "f", "operator": "range", "values": ["1"]}
                ],
                "limit": 10,
            }
        )


def test_objects_credentials_typed() -> None:
    body = ObjectsCredentialsBody(
        bucket="b", key_prefix="p/", permissions=["read", "write"], ttl_seconds=900
    )
    assert body.permissions == ("read", "write")


def test_objects_credentials_rejects_ttl_over_3600() -> None:
    with pytest.raises(PydanticValidationError):
        ObjectsCredentialsBody.model_validate(
            {"bucket": "b", "key_prefix": "p/", "permissions": ["read"], "ttl_seconds": 99999}
        )


def test_network_body_requires_absolute_path() -> None:
    with pytest.raises(PydanticValidationError):
        NetworkRequestBody.model_validate(
            {
                "host": "api.example.com",
                "port": 443,
                "path": "relative",
                "method": "GET",
            }
        )


def test_digest_pattern_is_strict() -> None:
    """Digest-поля требуют канонический ``sha256:<64 lowercase hex>``."""
    from prodavan.application.plugins.transport.bodies import CachePublishBody

    valid = "sha256:" + "a" * 64
    body = CachePublishBody(channel="c", message_digest=valid)
    assert body.message_digest == valid
    for bad in ("not-a-digest", "sha256:ABC", "sha256:" + "a" * 63, "md5:" + "a" * 64):
        with pytest.raises(PydanticValidationError):
            CachePublishBody.model_validate({"channel": "c", "message_digest": bad})


def test_control_chars_in_key_are_rejected() -> None:
    with pytest.raises(PydanticValidationError):
        CacheGetBody.model_validate({"key": "line\nbreak"})


def test_objects_list_limit_bounds() -> None:
    with pytest.raises(PydanticValidationError):
        ObjectsListBody.model_validate({"bucket": "b", "key_prefix": "p", "limit": 0})
    with pytest.raises(PydanticValidationError):
        ObjectsListBody.model_validate({"bucket": "b", "key_prefix": "p", "limit": 1001})
