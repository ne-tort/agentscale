"""Публичный контракт egress-контура плагинов и агентов (v4, задача 08).

Импорты намеренно «жадные», но ацикличные: ``errors`` -> ``policy`` ->
``generator`` -> ``resolver`` -> ``service``. Ни один модуль не тянет
``domain/**``, ``infra/**`` или ``core/middleware.py``.
"""

from __future__ import annotations

from prodavan.application.plugins.egress.errors import (
    EGRESS_DENIED_CODE,
    EGRESS_POLICY_INVALID_CODE,
    EgressDenied,
    EgressPolicyError,
)
from prodavan.application.plugins.egress.generator import (
    AuditEventName,
    EgressConfigGenerator,
    EgressScope,
    build_audit_event,
)
from prodavan.application.plugins.egress.policy import (
    APPROVED_FLAG,
    EgressMode,
    EgressPolicy,
    EgressRule,
    is_host_allowed,
    match,
    normalize_host,
    normalize_path,
    resolve_policy,
    validate_host,
    validate_rule_hosts,
)
from prodavan.application.plugins.egress.resolver import (
    GrantLookup,
    GrantPolicyResolver,
    GrantStorePort,
)
from prodavan.application.plugins.egress.service import (
    AuditSinkPort,
    EgressDecision,
    EgressDecisionCode,
    EgressService,
    HopFacts,
    HopInspectorPort,
)

__all__ = [
    "APPROVED_FLAG",
    "AuditEventName",
    "AuditSinkPort",
    "EGRESS_DENIED_CODE",
    "EGRESS_POLICY_INVALID_CODE",
    "EgressConfigGenerator",
    "EgressDecision",
    "EgressDecisionCode",
    "EgressDenied",
    "EgressMode",
    "EgressPolicy",
    "EgressPolicyError",
    "EgressRule",
    "EgressScope",
    "EgressService",
    "GrantLookup",
    "GrantPolicyResolver",
    "GrantStorePort",
    "HopFacts",
    "HopInspectorPort",
    "build_audit_event",
    "is_host_allowed",
    "match",
    "normalize_host",
    "normalize_path",
    "resolve_policy",
    "validate_host",
    "validate_rule_hosts",
]
