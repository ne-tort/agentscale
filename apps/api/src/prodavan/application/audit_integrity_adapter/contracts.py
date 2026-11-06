"""Contracts for the exclusive audit integrity orchestration boundary.

This zone coordinates the existing integrity and outcome-adapter services.  It
carries linked, persistence-neutral events and never exposes a persistence
model or a redacted append object as a trusted result.
"""
from __future__ import annotations

from dataclasses import dataclass

from prodavan.application.audit_integrity import AuditLinkedEvent
from prodavan.application.audit_outcomes import AuditOutcome


@dataclass(frozen=True, slots=True)
class AuditIntegrityAppendRequest:
    """Outcome together with the authorization and final-result events it needs."""

    outcome: AuditOutcome
    auth_event: AuditLinkedEvent
    final_result_event: AuditLinkedEvent


__all__ = ["AuditIntegrityAppendRequest"]
