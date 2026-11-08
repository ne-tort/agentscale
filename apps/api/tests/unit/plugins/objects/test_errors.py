"""Тесты кодов ошибок: каждый отказ — явный, с кодом и HTTP-статусом.

Fail-closed (план §0.5): отсутствие интеграции = ошибка, а не молчаливый успех.
"""

from __future__ import annotations

import pytest

from prodavan.application.plugins.objects.errors import (
    AuditUnavailable,
    BucketUnavailable,
    InvalidObjectKey,
    InvalidObjectMethod,
    ObjectAccessDenied,
    PluginObjectError,
    StsUnavailable,
)


def test_every_error_has_stable_code_and_status() -> None:
    expected = {
        ObjectAccessDenied: ("OBJECT_ACCESS_DENIED", 403),
        InvalidObjectKey: ("INVALID_OBJECT_KEY", 400),
        InvalidObjectMethod: ("INVALID_OBJECT_METHOD", 400),
        BucketUnavailable: ("BUCKET_UNAVAILABLE", 503),
        StsUnavailable: ("STS_UNAVAILABLE", 503),
        AuditUnavailable: ("AUDIT_UNAVAILABLE", 503),
    }
    for cls, (code, status) in expected.items():
        exc = cls()
        assert isinstance(exc, PluginObjectError)
        assert exc.code == code
        assert exc.status == status
        assert exc.title
        assert exc.detail  # русское сообщение по умолчанию


def test_invalid_object_key_is_access_denied_subclass() -> None:
    """Обход пути — это отказ в доступе, а не «невалидный ввод» извне.

    Обработчик/тесты могут ловить ``ObjectAccessDenied`` единым типом.
    """
    assert issubclass(InvalidObjectKey, ObjectAccessDenied)
    assert issubclass(InvalidObjectMethod, InvalidObjectKey)
    with pytest.raises(ObjectAccessDenied):
        raise InvalidObjectKey("../../other-company/secret")


def test_errors_are_app_errors_for_problem_json() -> None:
    from prodavan.domain.errors import AppError

    exc = StsUnavailable("root-креды запрещены")
    assert isinstance(exc, AppError)
    assert exc.detail == "root-креды запрещены"
    assert exc.extra == {}


def test_codes_are_unique() -> None:
    codes = [
        ObjectAccessDenied.CODE,
        InvalidObjectKey.CODE,
        InvalidObjectMethod.CODE,
        BucketUnavailable.CODE,
        StsUnavailable.CODE,
        AuditUnavailable.CODE,
    ]
    assert len(codes) == len(set(codes))
