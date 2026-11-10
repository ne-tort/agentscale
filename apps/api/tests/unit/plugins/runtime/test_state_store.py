"""Unit-тесты ``ConfigMapStateStore``.

ТЗ: CAS ConfigMap по ``resourceVersion`` (межпроцессный, в отличие от
``InMemoryStateStore`` задачи 09). Дополнительно: create при отсутствии
ConfigMap, 409 -> False, 403/API-сбой -> явная ошибка, канонический JSON,
fail-closed на битых данных.
"""

from __future__ import annotations

import json

import pytest

from prodavan.application.plugins.activation.ports import StateStorePort
from prodavan.application.plugins.activation.state import DesiredState
from prodavan.infrastructure.plugins.runtime.k8s_scale import (
    ConfigMapStateStore,
    K8sApiConflict,
    K8sApiNotFound,
    K8sApiUnavailable,
    K8sRawResponse,
    StateStoreUnavailable,
    decode_state,
    encode_state,
    state_key,
)
from tests.unit.plugins.runtime.conftest import (
    TEST_INSTANCE,
    FakeK8sApi,
    configmap_response,
    default_state,
)


OBJECT_PATH = "/api/v1/namespaces/agentscale-dev/configmaps/plugin-activation-state"
COLLECTION_PATH = "/api/v1/namespaces/agentscale-dev/configmaps"


def _store(config, api: FakeK8sApi) -> ConfigMapStateStore:  # type: ignore[no-untyped-def]
    return ConfigMapStateStore(config=config, api=api)


def test_state_key_is_deterministic() -> None:
    assert state_key("inst_1") == "inst_1"
    assert state_key("a/b:c") == state_key("a/b:c")
    assert state_key("a/b:c") != state_key("other")


def test_encode_decode_roundtrip() -> None:
    state = default_state().with_wake_in_flight(True)
    raw = encode_state(state)
    assert json.loads(raw)["wake_in_flight"] is True
    restored = decode_state(raw, plugin_instance_id=TEST_INSTANCE)
    assert restored.wake_in_flight is True
    assert restored.desired is DesiredState.SUSPENDED
    # Канонический JSON: одинаковое состояние -> одинаковая строка (CAS).
    assert encode_state(state) == encode_state(restored)


def test_canonical_json_is_stable_across_processes() -> None:
    """Два «процесса» читают одно состояние -> идентичная строка для CAS."""
    state = default_state().with_desired(DesiredState.ACTIVE)
    first = decode_state(encode_state(state), plugin_instance_id=TEST_INSTANCE)
    second = decode_state(encode_state(state), plugin_instance_id=TEST_INSTANCE)
    assert first is not second  # разные объекты, как у двух реплик Gateway
    assert encode_state(first) == encode_state(second)


@pytest.mark.parametrize("raw", ["", "not-json", "{}", '{"schema": 999}', "[]"])
def test_corrupt_state_is_explicit_failure(raw: str) -> None:
    with pytest.raises(StateStoreUnavailable):
        decode_state(raw, plugin_instance_id=TEST_INSTANCE)


def test_foreign_instance_state_is_rejected() -> None:
    raw = encode_state(default_state("another_instance"))
    with pytest.raises(StateStoreUnavailable):
        decode_state(raw, plugin_instance_id=TEST_INSTANCE)


async def test_load_missing_configmap_returns_default(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", OBJECT_PATH, K8sApiNotFound("absent"))
    store = _store(config, api)
    assert isinstance(store, StateStorePort)
    state = await store.load(TEST_INSTANCE)
    assert state.plugin_instance_id == TEST_INSTANCE
    assert state.desired is DesiredState.SUSPENDED
    assert state.wake_in_flight is False


async def test_compare_and_swap_success_uses_resource_version(config) -> None:  # type: ignore[no-untyped-def]
    """КЛЮЧЕВОЕ: PATCH несёт metadata.resourceVersion прочитанного объекта."""
    original = default_state()
    raw = encode_state(original)
    api = FakeK8sApi()
    api.on("GET", OBJECT_PATH, configmap_response({state_key(TEST_INSTANCE): raw}, resource_version="42"))
    captured: dict = {}

    def patch_handler(body):  # type: ignore[no-untyped-def]
        captured.update(body)
        return configmap_response({}, resource_version="43")

    api.on("PATCH", OBJECT_PATH, patch_handler)
    store = _store(config, api)

    changed = original.with_wake_in_flight(True)
    assert await store.compare_and_swap(expected=original, new=changed) is True

    methods = [call["method"] for call in api.calls]
    assert methods == ["GET", "PATCH"]
    assert captured["metadata"]["resourceVersion"] == "42"
    assert captured["kind"] == "ConfigMap"
    assert captured["data"][state_key(TEST_INSTANCE)] == encode_state(changed)
    assert api.calls[1]["content_type"] == "application/merge-patch+json"
    # Точечный patch: соседние ключи не перечисляются (не затираем другие инстансы).
    assert list(captured["data"]) == [state_key(TEST_INSTANCE)]


async def test_cas_returns_false_when_state_changed(config) -> None:  # type: ignore[no-untyped-def]
    """Другой процесс уже изменил состояние -> CAS False, БЕЗ записи."""
    original = default_state()
    actual = original.with_wake_in_flight(True)  # «конкурент» опередил
    api = FakeK8sApi()
    api.on(
        "GET",
        OBJECT_PATH,
        configmap_response(
            {state_key(TEST_INSTANCE): encode_state(actual)}, resource_version="43"
        ),
    )
    store = _store(config, api)
    result = await store.compare_and_swap(
        expected=original, new=original.with_desired(DesiredState.ACTIVE)
    )
    assert result is False
    assert [call["method"] for call in api.calls] == ["GET"]  # PATCH не было


async def test_cas_returns_false_on_409_conflict(config) -> None:  # type: ignore[no-untyped-def]
    original = default_state()
    api = FakeK8sApi()
    api.on(
        "GET",
        OBJECT_PATH,
        configmap_response({state_key(TEST_INSTANCE): encode_state(original)}, resource_version="42"),
    )
    api.on("PATCH", OBJECT_PATH, K8sApiConflict("resourceVersion stale"))
    store = _store(config, api)
    assert await store.compare_and_swap(
        expected=original, new=original.with_wake_in_flight(True)
    ) is False


async def test_cas_creates_configmap_when_absent(config) -> None:  # type: ignore[no-untyped-def]
    """Первый wake: ConfigMap ещё нет -> POST create, без resourceVersion."""
    original = default_state()
    api = FakeK8sApi()
    api.on("GET", OBJECT_PATH, K8sApiNotFound("absent"))
    captured: dict = {}

    def create_handler(body):  # type: ignore[no-untyped-def]
        captured.update(body)
        return configmap_response({}, resource_version="1")

    api.on("POST", COLLECTION_PATH, create_handler)
    store = _store(config, api)
    assert await store.compare_and_swap(
        expected=original, new=original.with_wake_in_flight(True)
    ) is True
    assert [call["method"] for call in api.calls] == ["GET", "POST"]
    assert captured["metadata"]["name"] == "plugin-activation-state"
    assert "resourceVersion" not in captured["metadata"]


async def test_parallel_create_conflict_returns_false(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", OBJECT_PATH, K8sApiNotFound("absent"))
    api.on("POST", COLLECTION_PATH, K8sApiConflict("already exists"))
    store = _store(config, api)
    state = default_state()
    assert await store.compare_and_swap(expected=state, new=state) is False


async def test_cas_mismatched_instances_returns_false(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    store = _store(config, api)
    assert await store.compare_and_swap(
        expected=default_state("a"), new=default_state("b")
    ) is False
    assert api.calls == []


async def test_api_unavailable_is_explicit_failure(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.default_error = K8sApiUnavailable("k8s down")
    with pytest.raises(StateStoreUnavailable):
        await _store(config, api).load(TEST_INSTANCE)


async def test_forbidden_configmap_is_explicit_failure(config) -> None:  # type: ignore[no-untyped-def]
    """403 (RBAC не выдал configmaps) -> system failure, не «пустое состояние»."""
    from prodavan.infrastructure.plugins.runtime.k8s_scale import K8sApiError

    api = FakeK8sApi()
    api.on("GET", OBJECT_PATH, K8sApiError("RBAC forbidden"))
    with pytest.raises(StateStoreUnavailable):
        await _store(config, api).load(TEST_INSTANCE)


async def test_missing_resource_version_is_explicit_failure(config) -> None:  # type: ignore[no-untyped-def]
    """Без resourceVersion оптимистичный CAS невозможен — отказ, не «повезёт»."""
    api = FakeK8sApi()
    response = configmap_response({})
    response.body["metadata"].pop("resourceVersion", None)
    api.on("GET", OBJECT_PATH, response)
    with pytest.raises(StateStoreUnavailable):
        await _store(config, api).load(TEST_INSTANCE)


async def test_save_retries_on_conflict_then_succeeds(config) -> None:  # type: ignore[no-untyped-def]
    state = default_state().with_wake_in_flight(True)
    api = FakeK8sApi()
    reads = [0]

    def get_handler(_body):  # type: ignore[no-untyped-def]
        reads[0] += 1
        return configmap_response({}, resource_version=str(reads[0]))

    patches = [0]

    def patch_handler(_body):  # type: ignore[no-untyped-def]
        patches[0] += 1
        if patches[0] == 1:
            raise K8sApiConflict("stale version")
        return configmap_response({}, resource_version="3")

    api.on("GET", OBJECT_PATH, get_handler)
    api.on("PATCH", OBJECT_PATH, patch_handler)
    await _store(config, api).save(state)
    assert patches[0] == 2  # первый CAS проиграл, второй прошёл


async def test_save_exhausted_retries_is_explicit_failure(config) -> None:  # type: ignore[no-untyped-def]
    api = FakeK8sApi()
    api.on("GET", OBJECT_PATH, configmap_response({}, resource_version="1"))
    api.on("PATCH", OBJECT_PATH, K8sApiConflict("always stale"))
    with pytest.raises(StateStoreUnavailable):
        await _store(config, api).save(default_state())
