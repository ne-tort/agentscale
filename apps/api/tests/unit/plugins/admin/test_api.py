"""Unit-тесты HTTP-контракта ``api/v1/plugin_admin.py``.

Проверяют: префикс роутера, набор эндпоинтов, типизированные Pydantic-тела с
``extra="forbid"`` (никаких произвольных ``dict``/``Any``), маппинг контрактов в
ответы и PlatformAdmin-зависимость через порт (fail-closed при отсутствии адаптера).

Тесты НЕ запускались (verification freeze).
"""

from __future__ import annotations

from typing import get_args

import pytest
from pydantic import BaseModel, ValidationError

from prodavan.api.v1 import plugin_admin as api
from prodavan.application.plugins.admin.contracts import (
    CapabilitySet,
    EgressMode,
    EgressPolicy,
    QuotaSet,
)
from prodavan.application.plugins.admin.errors import PluginAdminError
from prodavan.domain.errors import AppError

from .fakes import DIGEST_A, PLUGIN_X, Harness


# --------------------------------------------------------------------------- роутер


def test_router_prefix_and_operations() -> None:
    assert api.router.prefix == "/admin/plugins"
    expected = {
        "/admin/plugins/grant": "POST",
        "/admin/plugins/revoke": "POST",
        "/admin/plugins/uninstall": "POST",
        "/admin/plugins/wake": "POST",
        "/admin/plugins/": "GET",
        "/admin/plugins/enabled-modules": "PUT",
    }
    actual = {route.path: set(route.methods or ()) for route in api.router.routes}
    assert set(actual) == set(expected)
    for path, method in expected.items():
        # Starlette добавляет HEAD к GET-маршрутам; проверяем наличие основного метода.
        assert method in actual[path], (path, actual[path])


def test_every_request_body_is_strict_typed_model() -> None:
    """Все тела — Pydantic BaseModel с extra=forbid; ни одного ``dict``/``Any``."""

    bodies = (
        api.GrantBody,
        api.RevokeBody,
        api.UninstallBody,
        api.WakeBody,
        api.EnabledModulesBody,
        api.CapabilitiesBody,
        api.EgressPolicyBody,
        api.QuotaSetBody,
    )
    for model in bodies:
        assert issubclass(model, BaseModel)
        assert model.model_config.get("extra") == "forbid"
        for field in model.model_fields.values():
            annotation = field.annotation
            # Раскрываем Optional/Union/tuple и проверяем, что нет «произвольных» dict/Any.
            args = get_args(annotation)
            candidates = (annotation, *args)
            assert not any(c is dict for c in candidates), f"{model.__name__}.{field}"
            assert not any(
                getattr(c, "__name__", "") == "dict" for c in candidates if c is not None
            )


def test_bodies_reject_extra_fields() -> None:
    with pytest.raises(ValidationError):
        api.RevokeBody(company_id="co_a", plugin_id=PLUGIN_X, admin=True)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        api.GrantBody(
            company_id="co_a",
            plugin_id=PLUGIN_X,
            manifest_version="1.2.0",
            artifact_digest=DIGEST_A,
            capabilities={"free_form": "anything"},  # type: ignore[arg-type]
        )


def test_grant_body_validates_identifiers_and_digest() -> None:
    with pytest.raises(ValidationError):
        api.GrantBody(
            company_id="../prodavan",
            plugin_id=PLUGIN_X,
            manifest_version="1.2.0",
            artifact_digest=DIGEST_A,
            capabilities=api.CapabilitiesBody(capabilities=("cache",)),
        )
    with pytest.raises(ValidationError):
        api.GrantBody(
            company_id="co_a",
            plugin_id=PLUGIN_X,
            manifest_version="1.2.0",
            artifact_digest="sha256:deadbeef",
            capabilities=api.CapabilitiesBody(capabilities=("cache",)),
        )


def test_capabilities_body_maps_to_typed_contract() -> None:
    body = api.CapabilitiesBody(
        capabilities=("cache", "objects"),
        egress_policy=api.EgressPolicyBody(
            mode=EgressMode.WEBHOOKS_ONLY,
            allowed_hosts=("example.com",),
            allowed_paths=("/hook",),
        ),
        quotas=api.QuotaSetBody(ops_per_minute=5),
    )
    contract = body.to_contract()
    assert isinstance(contract, CapabilitySet)
    assert isinstance(contract.egress_policy, EgressPolicy)
    assert isinstance(contract.quotas, QuotaSet)
    assert contract.capabilities == ("cache", "objects")
    assert contract.quotas.ops_per_minute == 5


def test_egress_policy_body_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="webhooks-only требует"):
        api.EgressPolicyBody(
            mode=EgressMode.WEBHOOKS_ONLY, allowed_hosts=("example.com",)
        ).to_contract()
    with pytest.raises(ValueError, match="approval"):
        api.EgressPolicyBody(mode=EgressMode.FULL).to_contract()


# --------------------------------------------------------------------------- deps / auth


class _State:
    def __init__(self, **kwargs: object) -> None:
        for key, value in kwargs.items():
            setattr(self, key, value)


class _App:
    def __init__(self, state: _State) -> None:
        self.state = state


class _Request:
    def __init__(self, app: _App, headers: dict[str, str] | None = None) -> None:
        self.app = app
        self.headers = headers or {}


def test_ports_missing_is_fail_closed() -> None:
    """Нет подключённого ``PluginAdminPorts`` → 503, а не «пустить всех»."""

    request = _Request(_App(_State()))
    with pytest.raises(AppError) as caught:
        api._ports(request)  # type: ignore[arg-type]
    assert caught.value.code == "PLUGIN_ADMIN_NOT_CONFIGURED"
    assert caught.value.status == 503


def test_service_missing_is_fail_closed() -> None:
    request = _Request(_App(_State()))
    with pytest.raises(AppError) as caught:
        api._service(request)  # type: ignore[arg-type]
    assert caught.value.code == "PLUGIN_ADMIN_NOT_CONFIGURED"


async def test_platform_admin_goes_through_port() -> None:
    harness = Harness()
    request = _Request(_App(_State(plugin_admin_ports=harness.ports())))
    actor = await api.require_platform_admin(request, authorization="Bearer platform-token")  # type: ignore[arg-type]
    assert actor == "platform-admin"
    assert any(call.method == "require_platform_admin" for call in harness.platform_admin.calls)


async def test_platform_admin_denied_when_port_says_no() -> None:
    harness = Harness()
    harness.platform_admin.is_admin = False
    request = _Request(_App(_State(plugin_admin_ports=harness.ports())))
    with pytest.raises(AppError) as caught:
        await api.require_platform_admin(request, authorization=None)  # type: ignore[arg-type]
    assert caught.value.code == "FORBIDDEN"
    assert caught.value.status == 403


# --------------------------------------------------------------------------- маппинг ошибок


def test_service_error_maps_plugin_admin_error_as_is() -> None:
    error = PluginAdminError(code="GRANT_NOT_FOUND", detail="нет grant")
    assert api._service_error(error) is error


def test_service_error_maps_validation_errors_to_422() -> None:
    mapped = api._service_error(ValueError("недопустимый company_id"))
    assert mapped.code == "VALIDATION_ERROR"
    assert mapped.status == 422


def test_service_error_propagates_unexpected_error() -> None:
    original = RuntimeError("audit append failed")
    with pytest.raises(RuntimeError):
        api._service_error(original)


# --------------------------------------------------------------------------- ответы


def test_response_models_have_no_free_form_dict_fields() -> None:
    responses = (
        api.GrantResponse,
        api.RevokeResponse,
        api.UninstallResponse,
        api.WakeResponse,
        api.PluginListResponse,
        api.EnabledModulesResponse,
        api.InstalledPluginResponse,
        api.ClaimsResponse,
    )
    for model in responses:
        assert model.model_config.get("extra") == "forbid"
        for field in model.model_fields.values():
            assert field.annotation is not dict


def test_uninstall_response_exposes_snapshot_flag() -> None:
    """Ответ uninstall явно говорит, что snapshot не делался (§5.5)."""

    assert api.UninstallResponse.model_fields["snapshot_taken"].default is False
    assert api.EnabledModulesResponse.model_fields["data_migration_performed"].default is False
    assert api.GrantResponse.model_fields["egress_in_token"].default is False
    assert api.GrantResponse.model_fields["quotas_in_token"].default is False
