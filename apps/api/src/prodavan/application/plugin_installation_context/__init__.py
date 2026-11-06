"""Cross-domain plugin installation anchor/admission boundary."""

from .contracts import (
    InstallationAdmissionDecision,
    InstallationAdmissionReason,
    InstallationAdmissionRequest,
    M05CasContext,
    M06GrantHandoff,
    PluginInstallationContext,
)
from .service import PluginInstallationAdmissionService

__all__ = [
    "InstallationAdmissionDecision", "InstallationAdmissionReason",
    "InstallationAdmissionRequest", "M05CasContext", "M06GrantHandoff",
    "PluginInstallationAdmissionService", "PluginInstallationContext",
]
