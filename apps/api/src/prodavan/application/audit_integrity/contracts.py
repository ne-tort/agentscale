"""Persistence-neutral contracts for audit evidence integrity.

The integrity boundary joins authorization and execution events before an audit
outcome can be trusted. It contains identifiers and digests only; no payloads.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Mapping

from prodavan.application.audit_outcomes import AuditDecision, AuditOutcome, AuditResult


class AuditEventClass(StrEnum):
    AUTH = "auth"
    FINAL_RESULT = "final_result"


class IntegrityDisposition(StrEnum):
    TRUSTED = "trusted"
    QUARANTINED = "quarantined"


@dataclass(frozen=True, slots=True)
class AuditLinkedEvent:
    """Redacted event used to link authorization to final execution."""

    event_id: str
    event_class: AuditEventClass
    scope_kind: str
    scope_id: str
    decision: AuditDecision
    result_status: AuditResult
    correlation_id: str
    run_id: str
    occurred_at: datetime
    identity: Mapping[str, str]
    digests: Mapping[str, str]
    error_code: str | None = None

    def __post_init__(self) -> None:
        for name in ("event_id", "scope_kind", "scope_id", "correlation_id", "run_id"):
            _required(getattr(self, name), name)
        if not isinstance(self.event_class, AuditEventClass):
            raise ValueError("event_class must be AuditEventClass")
        if not isinstance(self.decision, AuditDecision) or not isinstance(self.result_status, AuditResult):
            raise ValueError("decision and result_status must use audit enums")
        if self.occurred_at.tzinfo is None or self.occurred_at.utcoffset() is None:
            raise ValueError("occurred_at must be timezone-aware")
        _mapping(self.identity, "identity")
        _mapping(self.digests, "digests")
        if self.error_code is not None:
            _required(self.error_code, "error_code")


@dataclass(frozen=True, slots=True)
class AuditOutcomeEvidence:
    """Complete linked evidence presented to the trusted append boundary."""

    outcome: AuditOutcome
    scope_kind: str
    scope_id: str
    auth_event: AuditLinkedEvent
    final_result_event: AuditLinkedEvent

    def __post_init__(self) -> None:
        _required(self.scope_kind, "scope_kind")
        _required(self.scope_id, "scope_id")
        if self.auth_event.event_class is not AuditEventClass.AUTH:
            raise ValueError("auth_event must have auth event_class")
        if self.final_result_event.event_class is not AuditEventClass.FINAL_RESULT:
            raise ValueError("final_result_event must have final_result event_class")
        if self.auth_event.event_id == self.final_result_event.event_id:
            raise ValueError("auth and final result event_id must differ")
        if (self.auth_event.scope_kind, self.auth_event.scope_id) != (self.scope_kind, self.scope_id):
            raise ValueError("auth event scope does not match outcome evidence scope")
        if (self.final_result_event.scope_kind, self.final_result_event.scope_id) != (self.scope_kind, self.scope_id):
            raise ValueError("final result event scope does not match outcome evidence scope")

    @property
    def decision(self) -> AuditDecision:
        return self.outcome.decision

    @property
    def quarantine(self) -> bool:
        return self.outcome.decision is AuditDecision.QUARANTINE

    @property
    def event_ids(self) -> tuple[str, str]:
        return self.auth_event.event_id, self.final_result_event.event_id


def _required(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must not be empty")


def _mapping(value: Mapping[str, str], name: str) -> None:
    if not isinstance(value, Mapping) or any(
        not isinstance(k, str) or not k.strip() or not isinstance(v, str) or not v.strip()
        for k, v in value.items()
    ):
        raise ValueError(f"{name} must contain non-empty strings")


__all__ = ["AuditEventClass", "AuditLinkedEvent", "AuditOutcomeEvidence", "IntegrityDisposition"]
