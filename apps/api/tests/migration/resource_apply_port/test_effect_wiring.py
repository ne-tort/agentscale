"""MIG-RES-EFFECT-WIRING-01 acceptance — the effect boundary is actually connected.

``PodWorkspaceOperationEffectPort`` existed (``durable/workspace_effects.py``) but
nothing passed it to ``build_resource_materialization_apply_port``, so every
**populated** plan still answered ``BLOCKED resource_effect_port_missing`` and only
empty plans could be applied. This artifact covers the wiring itself:

=====================================================  ===================================
Claim                                                  Test
=====================================================  ===================================
Startup passes a real Pod/workspace effect port        ``test_startup_*``
The low-level composer refuses a populated plan        ``test_provider_*`` / ``test_the_*
without an effect boundary (unchanged)                 composer_*``
Composition failure ⇒ named per-apply refusal          ``test_effect_composition_failure_*``
The fallback port is a refusal, not a no-op            ``test_unavailable_effect_port_*``
The wired boundary still refuses env/mcp/scope         ``test_the_wired_boundary_*``
Flag off publishes nothing                             ``test_flag_off_*``
Shutdown withdraws the wired port                      ``test_shutdown_*``
The stale "no adapter exists" claim is gone            ``test_the_stale_no_adapter_claim_*``
=====================================================  ===================================

Every check here is DB-free and Pod-free: composition opens no session (the session
factory double raises if it is ever called) and the refusals asserted below are
decided *before* a runtime lookup, a workspace write or a transaction.
"""

from __future__ import annotations

import pytest

from prodavan.application.materialization_adapter import ResourceOperation
from prodavan.application.resource_materialization.apply_adapter import (
    EVIDENCE_AUTHORITY_BLOCKERS,
    ResourceMaterializationApplyPort,
    build_resource_materialization_apply_port,
)
from prodavan.application.resource_materialization.durable.contracts import (
    RedactedDiagnostic,
    resource_key_for_project,
)
from prodavan.application.resource_materialization.durable.effects import (
    OperationFence,
    resource_operation_digest,
)
from prodavan.application.resource_materialization.durable.workspace_effects import (
    PodWorkspaceOperationEffectPort,
    SqlAlchemyProjectRuntimeRefResolver,
)
from prodavan.application.resources.adapters.factory import (
    install_materialization_apply_port,
    materialization_apply_port,
)
from prodavan.core.infra import resource_apply_resource as wiring
from prodavan.core.infra.resource_apply_resource import (
    REASON_EFFECT_PORT_UNAVAILABLE,
    ResourceApplyResource,
    UnavailableEffectPort,
    build_apply_port_provider,
    build_effect_port,
)
from prodavan.domain.materialization import OperationKind

from . import doubles

PROJECT_ID = doubles.PROJECT_ID
GENERATION = 7
FENCE_TOKEN = "fence-token-wiring"
_CODE = __import__("re").compile(r"^[a-z][a-z0-9_.-]{0,63}$")


# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


@pytest.fixture(autouse=True)
def _clean_seam():
    """Every check starts and ends with the process seam cleared."""
    install_materialization_apply_port(None)
    yield
    install_materialization_apply_port(None)


class _SessionFactory:
    """Session-factory stand-in: composition must resolve, never open, sessions."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self):  # pragma: no cover - composition must not reach this
        self.calls += 1
        raise AssertionError("composition must not open a session")


def _fence(generation: int = GENERATION) -> OperationFence:
    return OperationFence(
        resource_key=resource_key_for_project(PROJECT_ID),
        generation=generation,
        fence_token=FENCE_TOKEN,
    )


def _published_port() -> ResourceMaterializationApplyPort:
    """The port the API would resolve for a request (a real ``AsyncSession``).

    ``ResourceMaterializationApplyPort.for_session`` refuses a non-ORM session with
    ``None`` by design (503), so the seam is asked with an unbound ``AsyncSession``:
    constructing one opens no connection and starts no transaction.
    """
    from sqlalchemy.ext.asyncio import AsyncSession

    port = materialization_apply_port(AsyncSession())
    assert port is not None, "the resource_apply resource published nothing"
    assert isinstance(port, ResourceMaterializationApplyPort)
    return port


async def _started_resource(**kwargs) -> ResourceApplyResource:
    resource = ResourceApplyResource(enabled=True, **kwargs)
    await resource.startup()
    return resource


# --------------------------------------------------------------------------- #
# 1. Startup wires the production Pod/workspace effect port
# --------------------------------------------------------------------------- #


async def test_startup_wires_a_real_pod_workspace_effect_port() -> None:
    factory = _SessionFactory()
    resource = await _started_resource(session_factory=factory)

    port = _published_port()
    effects = port.application.executor.effects

    assert effects is not None, (
        "a populated plan must not hit resource_effect_port_missing any more"
    )
    assert isinstance(effects, PodWorkspaceOperationEffectPort)
    assert effects.rollback_supported is False
    assert resource.effects_degraded is False
    assert resource.effects_error is None
    assert factory.calls == 0, "composition must not open a session"
    assert await resource.health() is True
    await resource.shutdown()


async def test_startup_binds_the_project_runtime_resolver_to_the_process_factory() -> None:
    """The adapter resolves the live runtime through this process's own factory."""
    factory = _SessionFactory()
    await _started_resource(session_factory=factory)

    effects = _published_port().application.executor.effects
    resolver = getattr(effects, "_runtime", None)

    assert isinstance(resolver, SqlAlchemyProjectRuntimeRefResolver)
    assert resolver.session_factory is factory


async def test_startup_leaves_rollback_effects_unwired() -> None:
    """No workspace-byte snapshot exists, so no rollback capability is claimed."""
    await _started_resource(session_factory=_SessionFactory())

    executor = _published_port().application.executor

    assert executor.effects is not None
    assert executor.rollback_effects is None, (
        "the Pod/workspace adapter cannot restore a generation; wiring it as a "
        "rollback port would only turn a missing port into a refusal"
    )


async def test_an_injected_effect_port_is_used_verbatim() -> None:
    """A test (or an embedding process) can substitute its own boundary."""
    sentinel = UnavailableEffectPort()
    resource = await _started_resource(
        session_factory=_SessionFactory(), effects=sentinel
    )

    assert _published_port().application.executor.effects is sentinel
    assert resource.effects_degraded is False
    await resource.shutdown()


def test_build_effect_port_composes_the_workspace_adapter_directly() -> None:
    built = build_effect_port(session_factory=_SessionFactory())  # type: ignore[arg-type]

    assert isinstance(built, PodWorkspaceOperationEffectPort)


# --------------------------------------------------------------------------- #
# 2. The provider composes effects when none are injected
# --------------------------------------------------------------------------- #


def test_provider_composes_the_real_effect_port_when_none_is_injected() -> None:
    """``build_apply_port_provider`` alone can no longer publish an effectless port."""
    provider = build_apply_port_provider(session_factory=_SessionFactory())  # type: ignore[arg-type]

    port = provider(None)  # type: ignore[arg-type]

    assert port is not None
    assert isinstance(port.application.executor.effects, PodWorkspaceOperationEffectPort)


def test_provider_keeps_an_injected_effect_port() -> None:
    sentinel = UnavailableEffectPort()
    provider = build_apply_port_provider(
        session_factory=_SessionFactory(),  # type: ignore[arg-type]
        effects=sentinel,
    )

    port = provider(None)  # type: ignore[arg-type]

    assert port is not None
    assert port.application.executor.effects is sentinel


def test_the_low_level_composer_still_refuses_a_populated_plan_without_effects() -> None:
    """Fail-closed default retained: only the lifespan wiring supplies the port."""
    instance = build_resource_materialization_apply_port(
        session_factory=_SessionFactory()  # type: ignore[arg-type]
    )

    assert instance.application.executor.effects is None, (
        "the composer must keep the executor's resource_effect_port_missing refusal "
        "for a caller that passes no effect boundary"
    )


# --------------------------------------------------------------------------- #
# 3. Fail-closed: a broken effect composition is a *named* refusal
# --------------------------------------------------------------------------- #


async def test_effect_composition_failure_publishes_a_named_refusal_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A composition error must not degrade to "no effects" or to a silent skip."""

    def explode(**_: object) -> object:
        raise RuntimeError("workspace transport not configured (dsn=secret)")

    monkeypatch.setattr(wiring, "build_effect_port", explode)
    resource = await _started_resource(session_factory=_SessionFactory())

    effects = _published_port().application.executor.effects

    assert effects is not None, (
        "None would read as 'this build has no adapter' and hide the broken wiring"
    )
    assert isinstance(effects, UnavailableEffectPort)
    assert resource.effects_degraded is True
    # ``_safe_reason`` normalizes the exception *type* only; the message (which here
    # carries a fake DSN) never reaches health, logs or a receipt.
    assert resource.effects_error == "runtimeerror"
    assert "secret" not in str(resource.effects_error)
    assert "dsn" not in str(resource.effects_error)
    assert await resource.health() is True, "CRUD stays up; only apply is refused"
    await resource.shutdown()


async def test_the_refusal_survives_a_restart_of_the_same_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A degraded process must not quietly recover on a later startup attempt."""

    def explode(**_: object) -> object:
        raise RuntimeError("still broken")

    monkeypatch.setattr(wiring, "build_effect_port", explode)
    resource = await _started_resource(session_factory=_SessionFactory())
    await resource.startup()

    assert isinstance(_published_port().application.executor.effects, UnavailableEffectPort)
    assert resource.effects_degraded is True
    await resource.shutdown()


@pytest.mark.parametrize("required", [True, False])
async def test_unavailable_effect_port_refuses_every_operation(
    required: bool,
) -> None:
    """``effect_unknown=True`` makes the refusal fatal even for an optional op."""
    effects = UnavailableEffectPort()
    operation = doubles.operation(required=required)
    fence = _fence()

    receipt = await effects.apply_operation(
        project_id=PROJECT_ID, operation=operation, fence=fence
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_EFFECT_PORT_UNAVAILABLE
    assert receipt.effect_unknown is True
    # Bound to *this* operation and *this* fence, so the ledger records the named
    # refusal instead of blaming the fence (``operation_receipt_or_fence_mismatch``).
    assert receipt.operation_digest == resource_operation_digest(operation)
    assert receipt.matches(operation_digest=resource_operation_digest(operation), fence=fence)
    assert receipt.fence_token == fence.fence_token
    assert receipt.fence_generation == fence.generation


async def test_unavailable_effect_port_refuses_compensation_too() -> None:
    effects = UnavailableEffectPort()
    operation = doubles.operation()
    fence = _fence()

    receipt = await effects.compensate_operation(
        project_id=PROJECT_ID, operation=operation, fence=fence
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == REASON_EFFECT_PORT_UNAVAILABLE
    assert receipt.effect_unknown is True


def test_the_unavailable_reason_code_is_a_ledger_code() -> None:
    """``RedactedDiagnostic`` must keep the code: it is a shape *and* content check."""
    assert _CODE.fullmatch(REASON_EFFECT_PORT_UNAVAILABLE)
    assert RedactedDiagnostic("apply", REASON_EFFECT_PORT_UNAVAILABLE).reason_code == (
        REASON_EFFECT_PORT_UNAVAILABLE
    )


def test_the_unavailable_code_is_not_confused_with_a_missing_port() -> None:
    assert REASON_EFFECT_PORT_UNAVAILABLE != "resource_effect_port_missing"


# --------------------------------------------------------------------------- #
# 4. The wired boundary keeps the deliberate env/mcp/scope gap
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "kind", [OperationKind.ENV, OperationKind.MCP, OperationKind.SCOPE]
)
async def test_the_wired_boundary_still_refuses_unsupported_kinds(kind) -> None:
    """Wiring a port must not turn "unsupported" into "pretended to apply"."""
    await _started_resource(session_factory=_SessionFactory())
    effects = _published_port().application.executor.effects
    operation = ResourceOperation(
        kind=kind, target=f"{kind.value}/wiring-check", payload={"key": "value"}
    )

    receipt = await effects.apply_operation(  # type: ignore[union-attr]
        project_id=PROJECT_ID, operation=operation, fence=_fence()
    )

    assert receipt.succeeded is False
    assert receipt.reason_code == f"resource_effect_{kind.value}_unsupported"
    assert receipt.effect_unknown is False, "nothing was attempted, and the ledger knows"


# --------------------------------------------------------------------------- #
# 5. Flag off / shutdown
# --------------------------------------------------------------------------- #


async def test_flag_off_publishes_nothing_even_with_a_working_effect_port() -> None:
    resource = ResourceApplyResource(enabled=False, session_factory=_SessionFactory())
    await resource.startup()

    assert materialization_apply_port(None) is None  # type: ignore[arg-type]
    assert resource.effects_degraded is False
    assert await resource.health() is None


async def test_shutdown_withdraws_the_wired_port() -> None:
    resource = await _started_resource(session_factory=_SessionFactory())
    assert _published_port() is not None

    await resource.shutdown()

    assert materialization_apply_port(None) is None  # type: ignore[arg-type]
    assert await resource.health() is False


# --------------------------------------------------------------------------- #
# 6. The stale blocker entry is gone, the remaining gap stays documented
# --------------------------------------------------------------------------- #


def test_the_stale_no_adapter_claim_is_gone_from_the_blockers() -> None:
    entry = dict(EVIDENCE_AUTHORITY_BLOCKERS)["resource_effect"]

    assert "no Pod/workspace effect adapter" not in entry
    assert "resource_effect_port_missing" not in entry
    assert "resource_effect_<kind>_unsupported" in entry
    assert "owner:" in entry


def test_the_other_blockers_are_untouched() -> None:
    """Only the effect entry was stale; the rest still describe a real gap."""
    blockers = dict(EVIDENCE_AUTHORITY_BLOCKERS)

    assert set(blockers) == {"bridge_lease", "approval_link", "event_link", "resource_effect"}
    assert "no durable bridge-lease publisher" in blockers["bridge_lease"]
    assert "no approval identity" in blockers["approval_link"]
    assert "platform_events" in blockers["event_link"]
