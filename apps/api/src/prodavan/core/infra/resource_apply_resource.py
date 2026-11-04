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
    from prodavan.application.resource_materialization import (
        AtomicMaterializationApplyPort,
    )

logger = logging.getLogger(__name__)

_RESOURCE_NAME = "resource_apply"


class ResourceApplySettings(BaseSettings):
    """Feature flag of the Resource API apply path.

    Declared here rather than in ``config/settings.py`` because this task's file zone
    does not include that module; the canonical field should be promoted there by its
    owner and will then take precedence automatically (see
    :func:`resource_apply_enabled`). The env names follow the repository
    ``SCREAMING_SNAKE`` convention and accept the migration-era spelling as an alias,
    exactly like the neighbouring feature flags.
    """

    model_config = SettingsConfigDict(extra="ignore")

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


def build_apply_port_provider(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    port: "AtomicMaterializationApplyPort | None" = None,
) -> ApplyPortProviderProtocol:
    """Compose the durable port once and hand out a request-safe provider.

    The provider returns the *same* process-level port for every request: the ledger
    owns its own sessions (head CAS, attempt, audit and snapshot must share one
    transaction), so a request session cannot be borrowed. A non-ORM session is
    answered with ``None`` — the factory's documented "not ready" value → ``503``
    — rather than a port bound to an unexpected session type.
    """
    from prodavan.application.resource_materialization.apply_adapter import (
        build_resource_materialization_apply_port,
    )

    durable = port or build_resource_materialization_apply_port(
        session_factory=session_factory
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
    ) -> None:
        self._enabled = resource_apply_enabled() if enabled is None else bool(enabled)
        self._strict = (
            resource_apply_require_install() if strict is None else bool(strict)
        )
        self._session_factory = session_factory
        self._provider = provider
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
            provider = self._provider or build_apply_port_provider(
                session_factory=self._session_factory or _default_session_factory()
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
        logger.info("resource_apply: durable apply port published")

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
    "ResourceApplyResource",
    "ResourceApplySettings",
    "build_apply_port_provider",
    "resource_apply_enabled",
    "resource_apply_require_install",
    "resource_apply_resource_from_settings",
]
