"""Exclusive shared-identity handoff boundary for installation, M05, and M06."""

from .contracts import AuditEvidenceLink, IdentityHandoff
from .service import IdentityHandoffAdapter

__all__ = ["AuditEvidenceLink", "IdentityHandoff", "IdentityHandoffAdapter"]
