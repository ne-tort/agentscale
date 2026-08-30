"""Unit tests — exec error mapping."""

from __future__ import annotations

import pytest

from prodavan.application.pod_service.adapters.k8s.exec_errors import app_error_from_exec_failure
from prodavan.domain.errors import AppError
from prodavan.infrastructure.k8s.errors import K8sNotFoundError, TransientK8sError


def test_maps_k8s_not_found() -> None:
    err = app_error_from_exec_failure(K8sNotFoundError("missing"))
    assert err.code == "POD_NOT_FOUND"
    assert err.status == 404


def test_maps_transient_k8s_error() -> None:
    err = app_error_from_exec_failure(TransientK8sError("timeout"))
    assert err.code == "POD_EXEC_UNAVAILABLE"
    assert err.status == 502


def test_maps_invalid_status_403() -> None:
    pytest.importorskip("websockets")
    from websockets.exceptions import InvalidStatus

    class _Resp:
        status_code = 403
        body = b'{"message":"cannot get pods/exec"}'

    err = app_error_from_exec_failure(InvalidStatus(_Resp()))
    assert err.code == "POD_EXEC_FORBIDDEN"
    assert err.status == 403
    assert "cannot get pods/exec" in err.detail


def test_raises_app_error_not_generic_exception() -> None:
    err = app_error_from_exec_failure(RuntimeError("boom"))
    assert isinstance(err, AppError)
    assert err.code == "POD_EXEC_UNAVAILABLE"
