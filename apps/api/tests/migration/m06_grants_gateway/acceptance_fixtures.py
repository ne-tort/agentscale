"""Unexecuted MIG-M06-02 acceptance fixtures; no DB or service side effects."""

from datetime import UTC, datetime, timedelta

from prodavan.application.migration.m06_grants_gateway.adapter import (
    AuthenticatedGrantContext,
    DependencyGates,
    LegacyGrantSource,
    ResolvedGrantIdentity,
)

NOW = datetime(2026, 11, 1, 12, tzinfo=UTC)
GOOD_GATES = DependencyGates("sha256:" + "1" * 64, "sha256:" + "2" * 64,
                             "sha256:" + "3" * 64, "sha256:" + "4" * 64, 7)
CONTEXT = AuthenticatedGrantContext("tenant-a", "user", "subject-a", "session-a", "cabinet-a",
                                    "project-a", True, "corr-a")
IDENTITY = ResolvedGrantIdentity("tenant-a", "definition-a", "inventory", "version-a", "1.2.0",
                                 "sha256:" + "a" * 64, "project", "project-a", "cabinet-a",
                                 "project-a", "user", "subject-a", "session-a", "instance-a", 3, 7)
GRANTED = {"name": "storage.files", "decision": "granted", "constraints": {
    "methods": ["read", "write"], "resources": ["workspace"], "domains": ["files"],
    "eventTypes": ["created"], "maxBytesPerRequest": 4096}}
DENIED = {"name": "storage.files", "decision": "denied", "constraints": {
    "methods": ["read"], "resources": ["workspace"], "domains": ["files"]}}
SOURCE = LegacyGrantSource("legacy_grant", "grant-a", "grantset-v1", "inventory", "1.2.0",
                           (GRANTED,), NOW, NOW + timedelta(days=1), "tenant-a", "project", "project-a")

CASES = (
    "unknown capability maps to quarantine; no adapter/write path",
    "unsupported method/constraint grammar or absent methods maps to quarantine, never wildcard",
    "missing or ambiguous instance, subject, session, scope, definition or version quarantines",
    "caller company/scope/subject assertion disagreeing with server resolution quarantines",
    "stale runtime_generation or writer_generation denies and audits",
    "legacy allow versus canonical deny and legacy deny versus canonical allow are mismatches",
    "source identity with changed source digest conflicts without overwriting prior mapping",
    "same exact source/schema/digest replay repeats shadow evidence without duplicating mapping identity",
    "duplicate entries for one capability are quarantined as ambiguous rather than first-match selected",
    "fake or malformed dependency evidence digest cannot open a gate",
    "decision binding digest mismatch is a resolver integrity failure, never parity",
    "same allow/deny with different normalized reason codes is recorded as mismatch",
    "equivalent expired or revoked reason aliases normalize to the same parity class",
    "evidence repository/audit failure cannot be translated into an allow response",
    "production integration remains disabled until the decision owner handoff is explicit",
    "cross-tenant replay is quarantined even when source id and digest match another tenant",
    "expired and revoked decisions remain explicit deny parity, never positive parity",
    "expires_at equal to or before issued_at is rejected at source construction",
    "checked_at before issued_at quarantines source_grant_future without identity mapping or positive shadow",
    "checked_at at or after expires_at quarantines source_grant_expired without positive shadow",
    "mapping, quarantine, shadow and audit commit atomically or all roll back on any persistence failure",
    "replay shadow and audit append atomically without rewriting immutable mapping identity",
    "protocol decision=denied is retained as quarantine/audit evidence, never a canonical allow",
    "audit failure cannot authorize; no canonical positive grant interface exists",
)
