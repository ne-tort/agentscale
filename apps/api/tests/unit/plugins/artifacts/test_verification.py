"""Unit-тесты verification pipeline (§5.1, критерий 18).

Проверяется: cosign verify → digest → validate_manifest → compatibility gate
→ decision. Любая неудача → QUARANTINED + QuarantineEvidence, НЕ публикация.
"""

from __future__ import annotations

import pytest

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
)
from prodavan.application.plugins.artifacts import (
    ArtifactVerifier,
    CompatibilityGate,
    VerificationResult,
)
from .conftest import (
    DIGEST_A,
    DIGEST_B,
    FakeCosign,
    FakeGrant,
    full_grant,
    ref,
    valid_manifest,
)


def _assert_quarantined(outcome, code: str) -> None:
    assert outcome.outcome is RegistryOutcome.QUARANTINED
    assert outcome.result.outcome is RegistryOutcome.QUARANTINED
    evidence = outcome.result.quarantine
    assert isinstance(evidence, QuarantineEvidence)
    assert evidence.metadata["artifact_error_code"] == code
    assert outcome.evidence.verified is False
    assert outcome.evidence.outcome is RegistryOutcome.QUARANTINED


def test_happy_path_is_applied(verifier: ArtifactVerifier, cosign: FakeCosign) -> None:
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=True)
    assert outcome.outcome is RegistryOutcome.APPLIED
    assert outcome.quarantine is None
    assert outcome.evidence.verified is True
    assert cosign.calls[0][0] == ref().image_ref()
    assert "@" in cosign.calls[0][0] and ":latest" not in cosign.calls[0][0]
    assert outcome.evidence.artifact_digest == DIGEST_A


def test_cosign_verify_failed_is_quarantined(cosign: FakeCosign, validator, clock) -> None:
    """cosign verify упал → QUARANTINED, publish не происходит."""
    verifier = ArtifactVerifier(
        FakeCosign(verified=False),
        validator=validator,
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=True)
    _assert_quarantined(outcome, "UNSIGNED")


def test_cosign_exception_is_quarantined(cosign: FakeCosign, validator, clock) -> None:
    verifier = ArtifactVerifier(
        FakeCosign(raises=RuntimeError("cli missing")),
        validator=validator,
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=True)
    _assert_quarantined(outcome, "VERIFY_FAILED")


def test_missing_sbom_or_provenance_is_quarantined(validator, clock) -> None:
    for sbom, provenance in ((False, True), (True, False), (False, False)):
        verifier = ArtifactVerifier(
            FakeCosign(sbom=sbom, provenance=provenance),
            validator=validator,
            identity="id",
            issuer="iss",
            clock=clock,
        )
        outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=True)
        _assert_quarantined(outcome, "VERIFY_FAILED")


def test_signed_digest_mismatch_is_quarantined(validator, clock) -> None:
    verifier = ArtifactVerifier(
        FakeCosign(verified=True, signed_digest=DIGEST_B),
        validator=validator,
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(DIGEST_A), valid_manifest(), grant=full_grant(), scan_verified=True)
    _assert_quarantined(outcome, "DIGEST_CONFLICT")


def test_self_reference_in_manifest_is_quarantined(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(
        ref(),
        valid_manifest(artifact_digest="sha256:" + "a" * 64),
        grant=full_grant(),
        scan_verified=True,
    )
    _assert_quarantined(outcome, "DIGEST_CONFLICT")


def test_manifest_validator_rejection_is_quarantined(cosign: FakeCosign, clock) -> None:
    class Rejecting:
        def __call__(self, data):
            raise ValueError("PluginValidationError: bad manifest")

    verifier = ArtifactVerifier(
        cosign,
        validator=Rejecting(),
        identity="id",
        issuer="iss",
        clock=clock,
    )
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=True)
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_unknown_capability_is_incompatible(verifier: ArtifactVerifier) -> None:
    manifest = valid_manifest(requestedCapabilities=["cache", "unknown_capability"])
    outcome = verifier.verify(ref(), manifest, grant=full_grant(), scan_verified=True)
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_requested_capabilities_wider_than_grant_is_denied(verifier: ArtifactVerifier) -> None:
    """requestedCapabilities шире CapabilityGrant.capabilities → отказ."""
    narrow = FakeGrant(("cache", "objects"))
    outcome = verifier.verify(ref(), valid_manifest(), grant=narrow, scan_verified=True)
    _assert_quarantined(outcome, "INCOMPATIBLE")
    assert "docs" in outcome.result.quarantine.metadata["detail"]


@pytest.mark.parametrize("version", ["1.2", "v1.2.0", "1.2.0.3", "", "1.2.x", None])
def test_invalid_semver_is_rejected(verifier: ArtifactVerifier, version) -> None:
    outcome = verifier.verify(
        ref(), valid_manifest(version=version), grant=full_grant(), scan_verified=True
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


@pytest.mark.parametrize("protocol", ["1.0", "plugin-rpc/2", "", None])
def test_protocol_must_be_plugin_rpc_1(verifier: ArtifactVerifier, protocol) -> None:
    outcome = verifier.verify(
        ref(), valid_manifest(protocol=protocol), grant=full_grant(), scan_verified=True
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_min_platform_version_above_platform_is_rejected(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(
        ref(),
        valid_manifest(min_platform_version="5.0.0"),
        grant=full_grant(),
        scan_verified=True,
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


@pytest.mark.parametrize("min_version", ["4.0.0", "3.9.9", "1.0.0", "4.0.0-rc.1"])
def test_min_platform_version_at_or_below_platform_is_ok(
    verifier: ArtifactVerifier, min_version: str
) -> None:
    outcome = verifier.verify(
        ref(),
        valid_manifest(min_platform_version=min_version),
        grant=full_grant(),
        scan_verified=True,
    )
    assert outcome.outcome is RegistryOutcome.APPLIED


def test_missing_scan_is_incompatible(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=False)
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_missing_csp_is_incompatible(verifier: ArtifactVerifier) -> None:
    outcome = verifier.verify(
        ref(),
        valid_manifest(frontend={"entry": "index.html"}),
        grant=full_grant(),
        scan_verified=True,
    )
    _assert_quarantined(outcome, "INCOMPATIBLE")


def test_compatibility_gate_direct_checks() -> None:
    gate = CompatibilityGate(platform_version="4.0.0")
    assert gate.check(valid_manifest(), grant=full_grant(), scan_verified=True).ok is True
    verdict = gate.check(
        valid_manifest(requestedCapabilities=["nope"]),
        grant=full_grant(),
        scan_verified=True,
    )
    assert verdict.ok is False
    assert verdict.code is not None
    assert verdict.reason


def test_verified_result_requires_digest() -> None:
    with pytest.raises(ValueError):
        VerificationResult(verified=True, signed_digest=None)


def test_validate_evidence_rejects_foreign_verifier(verifier: ArtifactVerifier, cosign, validator, clock) -> None:
    from prodavan.application.plugins.artifacts import ArtifactError

    other = ArtifactVerifier(
        cosign, validator=validator, identity="id", issuer="iss", clock=clock
    )
    outcome = other.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=True)
    with pytest.raises(ArtifactError):
        verifier.validate_evidence(outcome.evidence, ref=ref(), manifest=valid_manifest())


def test_validate_evidence_rejects_changed_manifest(verifier: ArtifactVerifier) -> None:
    from prodavan.application.plugins.artifacts import ArtifactError

    outcome = verifier.verify(ref(), valid_manifest(), grant=full_grant(), scan_verified=True)
    with pytest.raises(ArtifactError):
        verifier.validate_evidence(
            outcome.evidence, ref=ref(), manifest=valid_manifest(version="9.9.9")
        )
