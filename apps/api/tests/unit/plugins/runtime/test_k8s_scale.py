"""Unit-тесты ``K8sScalePort``.

ТЗ: ``scale_to(1)`` патчит именно ``deployments/scale``; ``is_ready`` False при
``readyReplicas=0``; k8s недоступен -> ``ScaleUnavailable``. Дополнительно:
404 Deployment, RBAC 403, ``/health`` через Service DNS, режимы Ready,
динамические имена Deployment.
"""

from __future__ import annotations

import re

import pytest

from prodavan.application.plugins.activation.errors import NotReady, ScaleUnavailable
from prodavan.application.plugins.activation.ports import ReadinessPort, ScalePort
from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    K8sRawResponse,
    K8sScalePort,
    ReadinessMode,
    default_deployment_name,
    normalize_k8s_name,
    plugin_deployment_name,
    plugin_resource_name,
    plugin_service_name,
)
from tests.unit.plugins.runtime.conftest import (
    TEST_DEPLOYMENT,
    TEST_INSTANCE,
    FakeK8sApi,
    FakeProbe,
    K8sApiNotFound,
    K8sApiUnavailable,
    ProbeResult,
    deployment_response,
    scale_response,
)


def _port(config, api: FakeK8sApi, probe: FakeProbe | None = None, **over):  # type: ignore[no-untyped-def]
    over.setdefault(
        "deployment_name_resolver",
        lambda instance: plugin_deployment_name("mod_equipment", instance),
    )
    return K8sScalePort(config=config, api=api, probe=probe or FakeProbe(), **over)


def _scale_path(name: str = TEST_DEPLOYMENT) -> str:
    return f"/apis/apps/v1/namespaces/agentscale-dev/deployments/{name}/scale"


def _deployment_path(name: str = TEST_DEPLOYMENT) -> str:
    return f"/apis/apps/v1/namespaces/agentscale-dev/deployments/{name}"


# --------------------------------------------------------------------------- имена


def test_dynamic_deployment_names() -> None:
    assert normalize_k8s_name("inst_equipment_001") == "inst-equipment-001"
    expected = "plugin-mod-equipment-inst-equipment-001"
    assert plugin_deployment_name("mod_equipment", "inst_equipment_001") == expected
    assert plugin_service_name("mod_equipment", "inst_equipment_001") == expected


def test_long_colliding_ids_get_distinct_dns1035_names() -> None:
    shared = "mod_" + ("Equipment_" * 12)
    first_id = shared + "Alpha"
    second_id = shared + "Beta"
    instance = "inst_" + ("VeryLongCompany_" * 10)
    first = plugin_resource_name(first_id, instance)
    second = plugin_resource_name(second_id, instance)

    assert first != second, "plain truncation схлопнул бы общий префикс"
    assert len(first) <= 63 and len(second) <= 63
    assert re.fullmatch(r"[a-z](?:[-a-z0-9]*[a-z0-9])?", first)
    assert re.fullmatch(r"[a-z](?:[-a-z0-9]*[a-z0-9])?", second)
    assert re.search(r"-[0-9a-f]{16}$", first)
    assert re.search(r"-[0-9a-f]{16}$", second)
    assert first == plugin_deployment_name(first_id, instance)
    assert first == plugin_service_name(first_id, instance)


def test_naming_is_lowercase_and_prefix_has_one_dash() -> None:
    name = plugin_resource_name("MOD_Equipment", "INST_One", prefix="plugin")
    assert name == "plugin-mod-equipment-inst-one"
    assert name == name.lower()


def test_instance_only_default_name_is_rejected(config) -> None:  # type: ignore[no-untyped-def]
    with pytest.raises(RuntimeConfigError):
        default_deployment_name(TEST_INSTANCE)
    port = K8sScalePort(config=config, api=FakeK8sApi(), probe=FakeProbe())
    with pytest.raises(RuntimeConfigError):
        port.deployment_name(TEST_INSTANCE)


def test_explicit_deployment_name_mapping(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    port = _port(config, api, deployment_names={TEST_INSTANCE: "plugin-custom"})
    assert port.deployment_name(TEST_INSTANCE) == "plugin-custom"
    assert "/deployments/plugin-custom/scale" in port._scale_path("plugin-custom")


# --------------------------------------------------------------------------- current_replicas


async def test_current_replicas_reads_scale_subresource(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _scale_path(), scale_response(0))
    port = _port(config, api)
    assert isinstance(port, ScalePort)
    assert await port.current_replicas(TEST_INSTANCE) == 0
    assert api.calls[0]["method"] == "GET"
    assert api.calls[0]["path"] == _scale_path()


async def test_current_replicas_not_found_is_scale_unavailable(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _scale_path(), K8sApiNotFound("404"))
    with pytest.raises(ScaleUnavailable):
        await _port(config, api).current_replicas(TEST_INSTANCE)


async def test_current_replicas_api_down_is_scale_unavailable(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.default_error = K8sApiUnavailable("k8s API is down")
    with pytest.raises(ScaleUnavailable):
        await _port(config, api).current_replicas(TEST_INSTANCE)


# --------------------------------------------------------------------------- scale_to


async def test_scale_to_one_patches_deployments_scale(config) -> None:  # type: ignore[no-untyped-def]
    """КЛЮЧЕВОЕ: PATCH именно подресурса scale, тело меняет только replicas."""
    api = FakeK8sApi()
    api.on("PATCH", _scale_path(), scale_response(1))
    port = _port(config, api)
    await port.scale_to(TEST_INSTANCE, 1)

    assert len(api.calls) == 1
    call = api.calls[0]
    assert call["method"] == "PATCH"
    assert call["path"] == _scale_path()
    assert "/scale" in call["path"]
    # ``deployments/scale`` — это имя РЕСУРСА в RBAC (resource + subresource),
    # а не фрагмент REST-пути: между ними обязательно стоит имя Deployment
    # (/apis/apps/v1/namespaces/<ns>/deployments/<name>/scale). Подстрока
    # "deployments/scale" в URL недостижима — проверяем форму подресурса.
    assert re.search(r"/deployments/[^/]+/scale$", call["path"]) is not None
    assert call["body"]["metadata"]["name"] == TEST_DEPLOYMENT
    assert call["content_type"] == "application/merge-patch+json"
    body = call["body"]
    assert body["kind"] == "Scale"
    assert body["spec"] == {"replicas": 1}
    assert set(body["spec"]) == {"replicas"}
    # НИКАКОГО template/image/env/DSN: RBAC выдаёт только подресурс.
    assert "template" not in body
    assert "image" not in str(body)


async def test_scale_to_zero(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("PATCH", _scale_path(), scale_response(0))
    await _port(config, api).scale_to(TEST_INSTANCE, 0)
    assert api.calls[0]["body"]["spec"]["replicas"] == 0


@pytest.mark.parametrize("bad", [-1, 2, 99])
async def test_invalid_replicas_rejected_before_api(config, bad: int) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    with pytest.raises(ScaleUnavailable):
        await _port(config, api).scale_to(TEST_INSTANCE, bad)
    assert api.calls == []


async def test_scale_to_not_found_is_scale_unavailable(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("PATCH", _scale_path(), K8sApiNotFound("deployment absent"))
    with pytest.raises(ScaleUnavailable):
        await _port(config, api).scale_to(TEST_INSTANCE, 1)


async def test_scale_to_api_down_is_scale_unavailable(config) -> None:  # type: ignore[no-untyped-def]
    """k8s API недоступен -> ЯВНЫЙ отказ, не «считаем что уже 1»."""
    api = FakeK8sApi()
    api.default_error = K8sApiUnavailable("connection refused")
    with pytest.raises(ScaleUnavailable):
        await _port(config, api).scale_to(TEST_INSTANCE, 1)


async def test_scale_response_not_confirming_replicas_is_failure(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("PATCH", _scale_path(), scale_response(0))  # попросили 1, вернули 0
    with pytest.raises(ScaleUnavailable):
        await _port(config, api).scale_to(TEST_INSTANCE, 1)


async def test_scale_to_zero_accepts_omitted_replicas_field(config) -> None:  # type: ignore[no-untyped-def]
    """autoscaling/v1 не сериализует spec.replicas=0: omitted = ноль, не сбой."""
    from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sRawResponse

    api = FakeK8sApi()
    api.on(
        "PATCH",
        _scale_path(),
        K8sRawResponse(status_code=200, body={"kind": "Scale", "spec": {}}),
    )
    await _port(config, api).scale_to(TEST_INSTANCE, 0)


async def test_scale_to_one_rejects_omitted_replicas_field(config) -> None:  # type: ignore[no-untyped-def]
    """Omitted spec.replicas означает ТОЛЬКО ноль: wake без подтверждения запрещён."""
    from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sRawResponse

    api = FakeK8sApi()
    api.on(
        "PATCH",
        _scale_path(),
        K8sRawResponse(status_code=200, body={"kind": "Scale", "spec": {}}),
    )
    with pytest.raises(ScaleUnavailable, match="не подтвердил replicas=1"):
        await _port(config, api).scale_to(TEST_INSTANCE, 1)


async def test_current_replicas_zero_accepts_omitted_replicas_field(config) -> None:  # type: ignore[no-untyped-def]
    from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sRawResponse

    api = FakeK8sApi()
    api.on(
        "GET",
        _scale_path(),
        K8sRawResponse(status_code=200, body={"kind": "Scale", "spec": {}}),
    )
    assert await _port(config, api).current_replicas(TEST_INSTANCE) == 0


async def test_scale_response_wrong_kind_is_failure(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on(
        "PATCH",
        _scale_path(),
        K8sRawResponse(status_code=200, body={"kind": "Deployment", "spec": {"replicas": 1}}),
    )
    with pytest.raises(ScaleUnavailable, match="не объект Scale"):
        await _port(config, api).scale_to(TEST_INSTANCE, 1)


# --------------------------------------------------------------------------- readiness


async def test_is_ready_false_when_ready_replicas_zero(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), deployment_response(0))
    port = _port(config, api)
    assert isinstance(port, ReadinessPort)
    assert await port.is_ready(TEST_INSTANCE) is False
    # Проба НЕ дёргается до k8s-Ready (холодный старт — ожидаемое «не готово»).
    assert port._probe.urls == []  # type: ignore[attr-defined]


async def test_is_ready_false_when_status_field_missing(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), deployment_response(None))
    assert await _port(config, api).is_ready(TEST_INSTANCE) is False


async def test_is_ready_true_status_and_probe(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), deployment_response(1))
    probe = FakeProbe(results=[ProbeResult(status_code=200)])
    assert await _port(config, api, probe).is_ready(TEST_INSTANCE) is True
    assert probe.urls == [
        "http://plugin-mod-equipment-inst-equipment-001.agentscale-dev.svc:8080/health"
    ]


async def test_not_ready_when_probe_does_not_answer(config) -> None:  # type: ignore[no-untyped-def]
    """readyReplicas >= 1, но /health не отвечает -> NotReady, не «успех»."""
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), deployment_response(1))
    probe = FakeProbe(results=[ProbeResult(error="ConnectError")])
    with pytest.raises(NotReady):
        await _port(config, api, probe).is_ready(TEST_INSTANCE)


async def test_not_ready_when_probe_returns_5xx(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), deployment_response(1))
    probe = FakeProbe(results=[ProbeResult(status_code=503)])
    with pytest.raises(NotReady):
        await _port(config, api, probe).is_ready(TEST_INSTANCE)


async def test_not_ready_when_deployment_disappeared(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), K8sApiNotFound("gone"))
    with pytest.raises(NotReady):
        await _port(config, api).is_ready(TEST_INSTANCE)


async def test_not_ready_when_api_unavailable(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.default_error = K8sApiUnavailable("k8s down")
    with pytest.raises(NotReady):
        await _port(config, api).is_ready(TEST_INSTANCE)


async def test_status_only_mode_ignores_probe(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), deployment_response(1))
    probe = FakeProbe(results=[ProbeResult(error="ConnectError")])
    port = _port(config, api, probe, readiness_mode=ReadinessMode.STATUS)
    assert await port.is_ready(TEST_INSTANCE) is True
    assert probe.urls == []


async def test_either_mode_accepts_status_or_probe(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", _deployment_path(), deployment_response(1))
    probe = FakeProbe(results=[ProbeResult(error="ConnectError")])
    port = _port(config, api, probe, readiness_mode=ReadinessMode.EITHER)
    assert await port.is_ready(TEST_INSTANCE) is True  # статус достаточен
    assert probe.urls == []


async def test_probe_only_mode(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    probe = FakeProbe(results=[ProbeResult(status_code=200)])
    port = _port(config, api, probe, readiness_mode=ReadinessMode.PROBE)
    assert await port.is_ready(TEST_INSTANCE) is True
    assert api.calls == []  # deployments не читаем


async def test_bad_ready_replicas_type_is_explicit_error(config) -> None:  # type: ignore[no-untyped-def]
    """Нечисловой readyReplicas = отказ, а не «готово»."""
    from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sRawResponse

    api = FakeK8sApi()
    api.on(
        "GET",
        _deployment_path(),
        K8sRawResponse(
            status_code=200,
            body={"kind": "Deployment", "status": {"readyReplicas": "yes"}},
        ),
    )
    with pytest.raises(NotReady):
        await _port(config, api).is_ready(TEST_INSTANCE)
