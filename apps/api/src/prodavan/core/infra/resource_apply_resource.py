"""Publishes (and withdraws) the durable Resource API apply port at process start.

Why a lifespan resource: ``application/resources/adapters/factory.py`` resolves the
apply boundary through a process-level seam,
``install_materialization_apply_port(provider)``, because a request-scoped session
cannot own the materialization transaction. Until somebody published a provider the
factory returned ``apply_port=None`` and every
``POST .../materialization/apply`` answered ``503 RESOURCE_APPLY_UNAVAILABLE``
(MIG-RES-APPLYPORT-01). This resource is that publisher, and the only one:

* ``startup`` installs a provider that returns the durable
  ``AtomicMaterializationApplyPort`` built by
  ``application.resource_materialization.apply_adapter`` — real tables, real
  executor, no in-memory stub;
* ``shutdown`` withdraws it again (``install_materialization_apply_port(None)``), so
  a torn-down process cannot keep serving an apply path with dead collaborators;
* when the feature flag is off — the default — nothing is installed. Apply keeps
  answering the documented ``503 RESOURCE_APPLY_UNAVAILABLE`` while every resource
  CRUD path continues to work against real tables.

Effects (MIG-RES-EFFECT-WIRING-01)
==================================

The durable executor refuses a **populated** plan when it has no
``ResourceOperationEffectPort`` (``BLOCKED resource_effect_port_missing``), and until
this wiring landed the composer here never passed one, so only empty plans could be
applied. ``startup`` now composes the production Pod/workspace adapter
(``durable.workspace_effects.build_pod_workspace_effect_port(session_factory=...)``)
and hands it to ``build_resource_materialization_apply_port(effects=...)``. Nothing
about the port changes; the executor simply has a boundary to write through.

That composition is fail-closed in two directions:

* if the adapter cannot be composed, the process keeps serving CRUD and every apply
  is answered with the *named* refusal ``resource_effect_port_unavailable`` by
  :class:`UnavailableEffectPort` — it is never replaced by ``None`` (which would
  read as "this build has no adapter", ``resource_effect_port_missing``) and never
  by a no-op that claims the write happened;
* ``env``/``mcp``/``scope`` operations stay deliberately unsupported inside the
  adapter (``resource_effect_<kind>_unsupported``): no workspace write location for
  them exists in this tree, and inventing one here would create a second, competing
  layout for state the Pod runtime materialization zone owns.

The flag ``resource_apply_enabled`` defaults to **off** and is intentionally
fail-closed: an unset environment variable must never switch on a write path that
claims runtime generations. Failures are not papered over either — when the flag is
on and the composition cannot be built, startup refuses (``strict``) instead of
silently leaving the API in the 503 state it was explicitly asked to leave.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Protocol

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.core.lifespan.resource import LifespanResource

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from prodavan.application.materialization_adapter import ResourceOperation
    from prodavan.application.resource_materialization import (
        AtomicMaterializationApplyPort,
    )
    from prodavan.application.resource_materialization.durable.effects import (
        OperationEffectReceipt,
        OperationFence,
        ResourceOperationEffectPort,
    )

logger = logging.getLogger(__name__)

_RESOURCE_NAME = "resource_apply"

#: Named refusal of an apply whose effect boundary could not be composed at
#: startup. It is a code the ledger accepts verbatim (``^[a-z][a-z0-9_.-]{0,63}$``)
#: and it says exactly what is broken, unlike the generic
#: ``resource_effect_port_missing`` which means "this build has no adapter at all".
REASON_EFFECT_PORT_UNAVAILABLE = "resource_effect_port_unavailable"


class ResourceApplySettings(BaseSettings):
    """Feature flag of the Resource API apply path.

    Declared here rather than in ``config/settings.py`` because this task's file zone
    does not include that module; the canonical field should be promoted there by its
    owner and will then take precedence automatically (see
    :func:`resource_apply_enabled`). The env names follow the repository
    ``SCREAMING_SNAKE`` convention and accept the migration-era spelling as an alias,
    exactly like the neighbouring feature flags.
    """

    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    resource_apply_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "RESOURCE_APPLY_ENABLED",
            "RESOURCE_MATERIALIZATION_APPLY_ENABLED",
        ),
    )
    resource_apply_require_install: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "RESOURCE_APPLY_REQUIRE_INSTALL",
            "RESOURCE_MATERIALIZATION_APPLY_REQUIRE_INSTALL",
        ),
    )


class ApplyPortProviderProtocol(Protocol):
    """The provider shape ``install_materialization_apply_port`` accepts."""

    def __call__(
        self, session: AsyncSession
    ) -> "AtomicMaterializationApplyPort | None": ...


def _settings_flag(name: str, default: bool) -> bool:
    """Read ``name`` from the canonical settings first, then from this module's flag.

    ``config/settings.py`` may not define the field yet. Reading it with
    ``getattr`` means the canonical value wins the day its owner adds it, without
    this module importing a field that does not exist.
    """

    try:
        from prodavan.config.settings import settings as canonical
    except Exception:  # noqa: BLE001 - settings import must not break lifespan
        canonical = None
    if canonical is not None:
        value = getattr(canonical, name, None)
        if isinstance(value, bool):
            return value
    try:
        configured = ResourceApplySettings()
    except Exception:  # noqa: BLE001 - a malformed flag is not a crash
        logger.warning("resource_apply: flag %s is unreadable; using default", name)
        return default
    value = getattr(configured, name, default)
    return value if isinstance(value, bool) else default


def resource_apply_enabled() -> bool:
    """Default **off**: the apply port is published only on an explicit opt-in."""

    return _settings_flag("resource_apply_enabled", False)


def resource_apply_require_install() -> bool:
    """Default **on**: an enabled flag that cannot install must fail loudly."""

    return _settings_flag("resource_apply_require_install", True)


class UnavailableEffectPort:
    """Typed-refusal stand-in for an effect boundary that failed to compose.

    This is the only "fallback" in the apply path, and it is not a no-op. Every
    operation — required or optional — answers ``succeeded=False`` with
    :data:`REASON_EFFECT_PORT_UNAVAILABLE` **and** ``effect_unknown=True``:

    * ``effect_unknown`` makes the executor's effect loop fatal even for an
      ``required=False`` operation, so a plan can never settle as ``APPLIED`` while
      an effect was silently skipped;
    * the receipt echoes the exact ``fence_token``/``fence_generation`` and the
      real ``resource_operation_digest(operation)``, so the ledger records *this
      operation's* named failure instead of ``operation_receipt_or_fence_mismatch``
      (a mismatch would blame the fence for a wiring problem);
    * ``compensate_operation`` refuses with the same code rather than reporting a
      compensation that never ran.
    """

    reason_code = REASON_EFFECT_PORT_UNAVAILABLE

    async def apply_operation(
        self,
        *,
        project_id: str,
        operation: "ResourceOperation",
        fence: "OperationFence",
    ) -> "OperationEffectReceipt":
        logger.warning(
            "resource_apply.effect_port_unavailable kind=%s generation=%d",
            _operation_kind(operation),
            fence.generation,
        )
        return _unavailable_receipt(operation=operation, fence=fence)

    async def compensate_operation(
        self,
        *,
        project_id: str,
        operation: "ResourceOperation",
        fence: "OperationFence",
    ) -> "OperationEffectReceipt":
        logger.warning(
            "resource_apply.effect_port_unavailable phase=compensate kind=%s generation=%d",
            _operation_kind(operation),
            fence.generation,
        )
        return _unavailable_receipt(operation=operation, fence=fence)


def _unavailable_receipt(
    *, operation: "ResourceOperation", fence: "OperationFence"
) -> "OperationEffectReceipt":
    """A refusal receipt bound to this operation and this fence (never a match)."""
    from prodavan.application.resource_materialization.durable.effects import (
        OperationEffectReceipt,
        resource_operation_digest,
    )

    return OperationEffectReceipt(
        operation_digest=resource_operation_digest(operation),
        fence_token=fence.fence_token,
        fence_generation=fence.generation,
        succeeded=False,
        reason_code=REASON_EFFECT_PORT_UNAVAILABLE,
        effect_unknown=True,
    )


def _operation_kind(operation: object) -> str:
    """Kind value for logs only; never the target or the payload."""
    kind = getattr(operation, "kind", "")
    return str(getattr(kind, "value", kind))


def build_effect_port(
    *, session_factory: async_sessionmaker[AsyncSession]
) -> "ResourceOperationEffectPort":
    """Compose the production Pod/workspace effect boundary.

    Raises whatever the composition raises; :meth:`ResourceApplyResource.startup`
    is the caller that turns a failure into :class:`UnavailableEffectPort` (a named
    per-apply refusal) instead of silently applying without effects.
    """
    from prodavan.application.resource_materialization.durable.workspace_effects import (
        build_pod_workspace_effect_port,
    )

    return build_pod_workspace_effect_port(session_factory=session_factory)


def build_apply_port_provider(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    port: "AtomicMaterializationApplyPort | None" = None,
    effects: "ResourceOperationEffectPort | None" = None,
) -> ApplyPortProviderProtocol:
    """Compose the durable port once and hand out a request-safe provider.

    The provider returns the *same* process-level port for every request: the ledger
    owns its own sessions (head CAS, attempt, audit and snapshot must share one
    transaction), so a request session cannot be borrowed. A non-ORM session is
    answered with ``None`` — the factory's documented "not ready" value → ``503``
    — rather than a port bound to an unexpected session type.

    ``effects`` is the Pod/workspace effect boundary. When it is omitted the real
    adapter is composed here, so no caller can accidentally publish an apply port
    that refuses every populated plan. A pre-built ``port`` already carries its own
    collaborator set, so ``effects`` is ignored for it rather than half-rebinding a
    composition that was assembled elsewhere.
    """
    from prodavan.application.resource_materialization.apply_adapter import (
        build_resource_materialization_apply_port,
    )

    if port is not None:
        durable = port
    else:
        durable = build_resource_materialization_apply_port(
            session_factory=session_factory,
            effects=(
                effects
                if effects is not None
                else build_effect_port(session_factory=session_factory)
            ),
        )

    def provider(session: AsyncSession) -> "AtomicMaterializationApplyPort | None":
        for_session = getattr(durable, "for_session", None)
        if for_session is None:
            return durable
        return for_session(session)

    return provider


class ResourceApplyResource(LifespanResource):
    """Publish/withdraw the durable resource-materialization apply port."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        strict: bool | None = None,
        provider: ApplyPortProviderProtocol | None = None,
        effects: "ResourceOperationEffectPort | None" = None,
    ) -> None:
        self._enabled = resource_apply_enabled() if enabled is None else bool(enabled)
        self._strict = (
            resource_apply_require_install() if strict is None else bool(strict)
        )
        self._session_factory = session_factory
        self._provider = provider
        self._effects = effects
        self._effects_degraded = False
        self._effects_error: str | None = None
        self._installed = False
        self._install_error: str | None = None

    @property
    def name(self) -> str:
        return _RESOURCE_NAME

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def installed(self) -> bool:
        return self._installed

    @property
    def effects_degraded(self) -> bool:
        """True when applies are served by the typed-refusal effect boundary."""
        return self._effects_degraded

    @property
    def effects_error(self) -> str | None:
        """Redacted reason code (an exception *type*) of the last effect failure."""
        return self._effects_error

    async def startup(self) -> None:
        if not self._enabled:
            # Explicitly clear instead of trusting the initial module state: a
            # previous install in the same process must not survive a disabled flag.
            self._install(None)
            self._installed = False
            logger.info(
                "resource_apply: disabled; POST materialization/apply stays 503 "
                "RESOURCE_APPLY_UNAVAILABLE and CRUD is unaffected"
            )
            return
        try:
            provider = self._provider
            if provider is None:
                session_factory = self._session_factory or _default_session_factory()
                provider = build_apply_port_provider(
                    session_factory=session_factory,
                    effects=self._resolve_effects(session_factory),
                )
        except Exception as exc:  # noqa: BLE001 - the refusal below is explicit
            self._install(None)
            self._installed = False
            self._install_error = _safe_reason(exc)
            logger.exception("resource_apply: durable apply port could not be composed")
            if self._strict:
                raise RuntimeError(
                    "resource_apply_enabled is on but the durable apply port could not "
                    "be composed; refusing to start with a degraded apply path"
                ) from exc
            return
        self._install(provider)
        self._installed = True
        self._install_error = None
        logger.info(
            "resource_apply: durable apply port published%s",
            " (effect boundary degraded)" if self._effects_degraded else "",
        )

    async def shutdown(self) -> None:
        self._install(None)
        self._installed = False
        self._provider = None
        logger.info("resource_apply: durable apply port withdrawn")

    async def health(self) -> bool | None:
        if not self._enabled:
            return None
        return self._installed

    # -- internals -----------------------------------------------------

    def _resolve_effects(
        self, session_factory: async_sessionmaker[AsyncSession]
    ) -> "ResourceOperationEffectPort":
        """The effect boundary to publish with; never ``None``.

        A composition failure must not degrade to "no effects": passing ``None``
        makes the executor answer ``resource_effect_port_missing``, which is
        indistinguishable from a build that has no adapter at all, and it would let
        an operator believe the wiring is simply absent. The typed refusal keeps the
        broken composition visible in the apply receipt and in ``effects_degraded``.
        """
        if self._effects is not None:
            self._effects_degraded = False
            self._effects_error = None
            return self._effects
        try:
            effects = build_effect_port(session_factory=session_factory)
        except Exception as exc:  # noqa: BLE001 - the refusal below is explicit
            self._effects_degraded = True
            self._effects_error = _safe_reason(exc)
            logger.exception(
                "resource_apply: Pod/workspace effect port could not be composed; "
                "every apply will refuse with %s",
                REASON_EFFECT_PORT_UNAVAILABLE,
            )
            return UnavailableEffectPort()
        self._effects_degraded = False
        self._effects_error = None
        return effects

    @staticmethod
    def _install(provider: ApplyPortProviderProtocol | None) -> None:
        """Attach or clear the seam; importing the owner keeps lifespan imports light."""
        from prodavan.application.resources.adapters.factory import (
            install_materialization_apply_port,
        )

        install_materialization_apply_port(provider)


def _default_session_factory() -> async_sessionmaker[AsyncSession]:
    from prodavan.infrastructure.persistence.database import get_session_factory

    return get_session_factory()


def _safe_reason(exc: BaseException) -> str:
    """Redacted reason for logs/health: an exception type, never a message.

    Composition errors can embed DSNs and other connection material; the reason code
    must stay a code.
    """

    name = type(exc).__name__
    cleaned = "".join(character.lower() if character.isalnum() else "_" for character in name)
    return cleaned.strip("_") or "composition_error"


def resource_apply_resource_from_settings() -> ResourceApplyResource:
    """Register-time builder used by ``core/wiring.py``."""
    return ResourceApplyResource(
        enabled=resource_apply_enabled(),
        strict=resource_apply_require_install(),
    )


__all__ = [
    "ApplyPortProviderProtocol",
    "REASON_EFFECT_PORT_UNAVAILABLE",
    "ResourceApplyResource",
    "ResourceApplySettings",
    "UnavailableEffectPort",
    "build_apply_port_provider",
    "build_effect_port",
    "resource_apply_enabled",
    "resource_apply_require_install",
    "resource_apply_resource_from_settings",
]
