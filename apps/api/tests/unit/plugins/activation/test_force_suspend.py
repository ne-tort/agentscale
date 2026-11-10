"""Forced admin revoke и async audit sink. Не запускать (verification freeze)."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.activation import (
    ActivationError,
    ActivationState,
    DesiredState,
    ScaleUnavailable,
)

from .fakes import FakeAudit, FakeScale
from .test_activator import INSTANCE, Harness


async def test_force_suspend_ignores_idle_ttl_and_active_jobs() -> None:
    h = Harness(scale=FakeScale(replicas=1))
    await h.store.save(ActivationState(plugin_instance_id=INSTANCE, desired=DesiredState.ACTIVE))
    await h.activator.register_job(INSTANCE, "job-long-running")
    h.clock.advance(1)

    result = await h.activator.force_suspend(INSTANCE)

    assert result is True
    assert h.scale.calls == [0]
    assert await h.scale.current_replicas(INSTANCE) == 0
    assert (await h.state()).desired is DesiredState.SUSPENDED
    assert any(
        event.reason == "forced revoke confirmed replicas=0"
        for event in h.audit.events
    )


async def test_force_suspend_requires_actual_zero_replicas() -> None:
    class NonConfirmingScale(FakeScale):
        async def scale_to(self, plugin_instance_id: str, replicas: int) -> None:
            self.calls.append(replicas)
            # Намеренно не меняем фактическое состояние.

    h = Harness(scale=NonConfirmingScale(replicas=1))
    await h.store.save(ActivationState(plugin_instance_id=INSTANCE, desired=DesiredState.ACTIVE))
    with pytest.raises(ScaleUnavailable, match="replicas=0"):
        await h.activator.force_suspend(INSTANCE)
    assert (await h.state()).desired is not DesiredState.SUSPENDED


async def test_normal_idle_suspend_still_respects_active_jobs() -> None:
    h = Harness(scale=FakeScale(replicas=1))
    await h.store.save(ActivationState(plugin_instance_id=INSTANCE, desired=DesiredState.ACTIVE))
    await h.activator.register_job(INSTANCE, "job-still-running", lease_ttl_seconds=7200)
    await h.activator.touch(INSTANCE)
    h.clock.advance(3600)

    assert await h.activator.suspend(INSTANCE) is False
    assert h.scale.calls == []


async def test_async_audit_failure_precedes_forced_scale() -> None:
    h = Harness(scale=FakeScale(replicas=1), audit=FakeAudit(fail=True))
    with pytest.raises(ActivationError):
        await h.activator.force_suspend(INSTANCE)
    assert h.scale.calls == []
