"""Trusted durable installation-parent scope."""

from .contracts import InstallationScope
from .repository import SqlInstallationScopeRepository

__all__ = ["InstallationScope", "SqlInstallationScopeRepository"]
