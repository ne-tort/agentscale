"""Install-time event/job composition tests (not executed during freeze)."""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from prodavan.application.plugins.events.dispatch import (
    ActivatingPluginEventHandler,
    SignedEventHandler,
)
from prodavan.application.plugins.jobs.tasks import JobTaskSpec
from prodavan.application.plugins.wiring.install import (
    PluginCompositionError,
    _event_integration_overrides,
    _job_integration_override,
)
from tests.unit.plugins.events.conftest import TEST_EVENT_SIGNER
from tests.unit.plugins.wiring.conftest import FakeSettings
from tests.unit.plugins.jobs.conftest import (
    BACKEND_DIGEST,
    OPERATION,
    RESULT_DIGEST,
    TASK_NAME,
    ArtifactRegistry,
    ExecutionStore,
    GrantStore,
)


class Handler:
    async def handle(self, event: Any, *, subscription: Any) -> None:
        return None


class Wake:
    async def wake(self, event: Any, *, subscription: Any) -> None:
        return None


class Activator:
    async def wake(self, instance_id: str, *, timeout: float | None = None) -> Any:
        raise AssertionError("composition must not perform wake IO")


class Proxy:
    async def forward(self, **kwargs: Any) -> Any:
        raise AssertionError("composition must not perform backend IO")


class SecretStore:
    def get(self, ref: str) -> str:
        assert ref == "vault://unit/plugin-events"
        return "unit-test-only-event-signing-key-32-bytes"


class Queue:
    async def submit(self, *, identity: dict[str, str]) -> dict[str, Any]:
        raise AssertionError("not used")

    async def get(self, *, identity: dict[str, str]) -> dict[str, Any]:
        raise AssertionError("not used")

    async def cancel(self, *, identity: dict[str, str]) -> dict[str, Any]:
        raise AssertionError("not used")


def repos() -> Any:
    return SimpleNamespace(grant_store=GrantStore())


def test_event_handler_and_wake_require_pair_and_valid_signer(monkeypatch: Any) -> None:
    monkeypatch.setenv(
        "PLUGIN_EVENT_SIGNING_SECRET_REF", "vault://unit/from-environment"
    )
    with pytest.raises(PluginCompositionError, match="typed unwired") as caught:
        _event_integration_overrides(
            ov={},
            repos=repos(),
            runtime=SimpleNamespace(proxy=Proxy()),
            services={"activator": Activator()},
        )
    assert caught.value.step == "event_handler"

    with pytest.raises(PluginCompositionError, match="configured together"):
        _event_integration_overrides(
            ov={"event_handler": Handler(), "event_signer": TEST_EVENT_SIGNER},
            repos=repos(),
            runtime=SimpleNamespace(proxy=Proxy()),
            services={"activator": Activator()},
        )


def test_custom_event_ports_get_explicit_signer_without_secret_fallback() -> None:
    wake = Wake()
    result = _event_integration_overrides(
        ov={
            "event_handler": Handler(),
            "event_wake_hook": wake,
            "event_signer": TEST_EVENT_SIGNER,
        },
        repos=repos(),
        runtime=SimpleNamespace(proxy=Proxy()),
        services={"activator": Activator()},
    )
    assert isinstance(result["event_handler"], SignedEventHandler)
    assert result["event_handler"].event_signer is TEST_EVENT_SIGNER
    assert result["event_wake_hook"] is wake


def test_typed_settings_ref_wins_and_direct_environment_is_not_a_fallback(
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv(
        "PLUGIN_EVENT_SIGNING_SECRET_REF", "vault://unit/from-environment"
    )
    result = _event_integration_overrides(
        ov={"secret_store": SecretStore()},
        repos=repos(),
        runtime=SimpleNamespace(proxy=Proxy()),
        services={"activator": Activator()},
        settings=FakeSettings(
            plugin_platform_enabled=False,
            plugin_event_signing_secret_ref="vault://unit/plugin-events",
        ),
    )
    handler = result["event_handler"]
    assert handler.event_signer._ref == "vault://unit/plugin-events"
    assert handler is result["event_wake_hook"]


def test_default_event_ports_share_one_exact_activating_delivery() -> None:
    result = _event_integration_overrides(
        ov={"event_signer": TEST_EVENT_SIGNER},
        repos=repos(),
        runtime=SimpleNamespace(proxy=Proxy()),
        services={"activator": Activator()},
    )
    assert isinstance(result["event_handler"], ActivatingPluginEventHandler)
    assert result["event_handler"] is result["event_wake_hook"]


def job_overrides() -> dict[str, Any]:
    return {
        "job_tasks": {
            OPERATION: JobTaskSpec(
                operation=OPERATION,
                task_name=TASK_NAME,
                backend_digest=BACKEND_DIGEST,
                handler=lambda context: RESULT_DIGEST,
            )
        },
        "job_queue": Queue(),
        "job_execution_store": ExecutionStore(),
        "job_async_runner": lambda coroutine: RESULT_DIGEST,
    }


def test_job_tasks_require_server_owned_mapping() -> None:
    with pytest.raises(PluginCompositionError, match="typed unwired") as caught:
        _job_integration_override(
            ov={}, repos=repos(), services={"artifact_registry": ArtifactRegistry()}, redis=object()
        )
    assert caught.value.step == "job_tasks"


def test_real_job_queue_requires_separate_worker_registration() -> None:
    overrides = job_overrides()
    del overrides["job_queue"]
    with pytest.raises(PluginCompositionError, match="worker registration hook"):
        _job_integration_override(
            ov=overrides,
            repos=repos(),
            services={"artifact_registry": ArtifactRegistry()},
            redis=object(),
        )


def test_real_job_queue_uses_explicit_worker_registration_hook() -> None:
    overrides = job_overrides()
    del overrides["job_queue"]
    registrations = []
    overrides["job_worker_registration"] = registrations.append
    result = _job_integration_override(
        ov=overrides,
        repos=repos(),
        services={"artifact_registry": ArtifactRegistry()},
        redis=object(),
    )
    assert result["adapter_jobs"]._allowed_tasks in registrations
    assert len(registrations) == 1


@pytest.mark.asyncio
async def test_job_adapter_owns_manifest_registry_and_closes_it_once() -> None:
    result = _job_integration_override(
        ov=job_overrides(),
        repos=repos(),
        services={"artifact_registry": ArtifactRegistry()},
        redis=object(),
    )
    adapter = result["adapter_jobs"]
    registry = adapter._allowed_tasks
    await adapter.aclose()
    await adapter.aclose()
    assert registry._closed is True
