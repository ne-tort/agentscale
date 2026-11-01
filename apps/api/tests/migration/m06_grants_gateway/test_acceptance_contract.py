"""Acceptance tests specified for post-freeze execution; intentionally unexecuted."""

from datetime import timedelta

import pytest

from .acceptance_fixtures import CASES, GOOD_GATES, GRANTED, IDENTITY, SOURCE, NOW
from prodavan.application.migration.m06_grants_gateway.adapter import (
    AuthenticatedGrantContext,
    DependencyGates,
    GrantSetAdapter,
    LegacyGrantSource,
    TrustedEvidenceTransactionPort,
    binding_digest,
    digest,
)


def test_fixture_matrix_covers_required_m06_02_cases():
    required = {
        "unknown capability", "unsupported method", "missing or ambiguous instance",
        "stale runtime_generation", "legacy allow versus canonical deny",
        "changed source digest", "cross-tenant replay", "expired and revoked",
        "same exact source/schema/digest replay", "duplicate entries for one capability", "decision binding digest mismatch",
    }
    assert all(any(term in case for case in CASES) for term in required)


def test_trusted_evidence_port_exposes_only_atomic_commit_operation():
    assert set(TrustedEvidenceTransactionPort.__dict__) == {"commit_evidence", "__module__", "__doc__", "__annotations__"}


def test_source_rejects_nonpositive_lifetime():
    for expiry in (NOW, NOW - timedelta(seconds=1)):
        with pytest.raises(ValueError, match="expires_at must be after issued_at"):
            LegacyGrantSource("legacy_grant", "bad-life", "grantset-v1", "inventory", "1.2.0",
                              (GRANTED,), NOW, expiry)


def test_acceptance_matrix_requires_fixed_time_future_expiry_and_atomic_evidence_cases():
    required = (
        "checked_at before issued_at", "checked_at at or after expires_at",
        "mapping, quarantine, shadow and audit commit atomically",
        "replay shadow and audit append atomically",
    )
    assert all(any(term in case for case in CASES) for term in required)


def test_canonical_payload_digest_is_order_independent_for_mapped_arrays():
    left = GrantSetAdapter().adapt(GRANTED)
    reordered = {**GRANTED, "constraints": {**GRANTED["constraints"], "methods": ["write", "read"]}}
    right = GrantSetAdapter().adapt(reordered)
    assert left.payload_digest == right.payload_digest


def test_identity_binding_digest_includes_exact_instance_and_generations():
    original = binding_digest(IDENTITY)
    from dataclasses import replace
    assert binding_digest(replace(IDENTITY, plugin_instance_id="instance-b")) != original
    assert binding_digest(replace(IDENTITY, runtime_generation=4)) != original
    assert binding_digest(replace(IDENTITY, writer_generation=8)) != original


def test_dependency_gate_does_not_infer_missing_handoff():
    assert not DependencyGates(m04_scope_digest="sha256:x").ready
    assert not DependencyGates("invented", "also-invented", "missing", "fake", 7).ready
    assert GOOD_GATES.ready


def test_digests_are_sha256_and_context_is_not_caller_ownership():
    assert digest({"source": SOURCE.source_id}).startswith("sha256:")
    caller_assertion = AuthenticatedGrantContext("tenant-b", "user", "subject-a", "session-a",
                                                  "cabinet-a", "project-a", True, "corr-a")
    assert caller_assertion.tenant_id != IDENTITY.tenant_id
