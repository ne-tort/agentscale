"""Unit-тесты ``CliCosignPort``.

ТЗ: ``:latest`` отклоняется ДО вызова cosign; rc=1/timeout/пустой вывод ->
VERIFY_FAILED; digest формат; KMS fallback не отключает проверку.
Дополнительно: SBOM/provenance, запрет флагов ослабления, аудит без секретов.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from prodavan.application.plugins.artifacts.verification import (
    CosignPort,
    VerificationResult,
)
from prodavan.infrastructure.plugins.runtime.cosign_adapter import (
    AttestationEvidence,
    CliCosignPort,
    assert_verification_enabled,
    attestation_command,
    audit_fields,
    keyless_verify_command,
    kms_verify_command,
    prepare_ref,
)
from prodavan.infrastructure.plugins.runtime.errors import RuntimeConfigError
from tests.unit.plugins.runtime.conftest import (
    TEST_DIGEST,
    TEST_IDENTITY,
    TEST_ISSUER,
    TEST_KMS_REF,
    TEST_REF,
    FakeCosignRunner,
    cosign_json,
)


def _port(runner: FakeCosignRunner, **over: Any) -> CliCosignPort:
    return CliCosignPort(runner=runner, timeout_seconds=12.0, **over)


# --------------------------------------------------------------------------- формат / :latest


@pytest.mark.parametrize(
    "bad",
    [
        "ghcr.io/x/p:latest",
        "ghcr.io/x/p:latest@" + TEST_DIGEST,
        "ghcr.io/x/latest@" + TEST_DIGEST,
        "ghcr.io/x/p",                                 # нет @digest
        "ghcr.io/x/p@sha256:ABC",                     # короткий digest
        "ghcr.io/x/p@sha256:" + "A" * 64,             # верхний регистр
        "ghcr.io/x/p@md5:" + "a" * 64,                # другой алгоритм
        TEST_REF + "?tag=latest",                     # мусор после digest
        "",
    ],
)
def test_invalid_ref_rejected_before_cosign(bad: str) -> None:
    """Невалидная ссылка / ':latest' -> отказ ДО единого вызова subprocess."""
    runner = FakeCosignRunner()
    port = _port(runner)
    result = port.verify(bad, TEST_IDENTITY, TEST_ISSUER)
    assert isinstance(result, VerificationResult)
    assert result.verified is False
    assert result.signed_digest is None
    assert runner.calls == []  # КЛЮЧЕВОЕ: cosign НЕ вызывался


def test_prepare_ref_returns_valid_digest() -> None:
    assert prepare_ref(TEST_REF) == TEST_DIGEST


def test_latest_in_identity_does_not_skip_digest_check() -> None:
    """':latest' в identity НЕ отключает проверку формата самой ссылки."""
    runner = FakeCosignRunner()
    result = _port(runner).verify(TEST_REF, "https://ci/latest", TEST_ISSUER)
    # identity со '/latest' не запрещён (это URL воркфлоу), но команда всё
    # равно строится с валидным digest-пином: подмена тега невозможна.
    assert runner.calls
    assert all(TEST_REF in argv for argv in runner.calls)


# --------------------------------------------------------------------------- успешный путь


def test_success_requires_signature_sbom_and_provenance() -> None:
    runner = FakeCosignRunner()
    port = _port(runner)
    result = port.verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is True
    assert result.signed_digest == TEST_DIGEST
    assert result.sbom_verified is True
    assert result.provenance_verified is True
    assert result.identity == TEST_IDENTITY
    assert result.issuer == TEST_ISSUER
    # 1 основная подпись + SBOM + provenance.
    assert len(runner.calls) == 3
    assert runner.calls[0][:2] == ["cosign", "verify"]
    assert "--certificate-identity" in runner.calls[0]
    assert "--certificate-oidc-issuer" in runner.calls[0]
    assert runner.calls[0][-1] == TEST_REF
    assert all(timeout == 12.0 for timeout in runner.timeouts)
    # Позиционно-совместимая сигнатура (ArtifactVerifier вызывает так).
    assert isinstance(port, CosignPort)


def test_keyword_call_also_supported() -> None:
    runner = FakeCosignRunner()
    result = _port(runner).verify(TEST_REF, identity=TEST_IDENTITY, issuer=TEST_ISSUER)
    assert result.verified is True


# --------------------------------------------------------------------------- отказы cosign


@pytest.mark.parametrize(
    "exception",
    [
        RuntimeError("binary missing"),
        FileNotFoundError("cosign not found"),
        PermissionError("cosign not executable"),
    ],
)
def test_runner_exception_is_verify_failed(exception: Exception) -> None:
    runner = FakeCosignRunner(raise_on="verify", exception=exception)
    result = _port(runner).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False
    assert "VERIFY_FAILED" in result.detail


def test_rc_nonzero_is_verify_failed() -> None:
    class RcRunner(FakeCosignRunner):
        def __call__(self, argv: list[str], *, timeout: float) -> Any:
            self.calls.append(list(argv))
            return type("Completed", (), {"returncode": 1, "stdout": cosign_json(), "stderr": ""})()

    result = _port(RcRunner()).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False
    assert "VERIFY_FAILED" in result.detail


def test_timeout_is_verify_failed() -> None:
    """TimeoutExpired из subprocess -> VERIFY_FAILED, а не «успех»."""
    class TimeoutRunner(FakeCosignRunner):
        def __call__(self, argv: list[str], *, timeout: float) -> Any:
            self.calls.append(list(argv))
            raise TimeoutError("timed out")

    result = _port(TimeoutRunner()).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False
    assert "VERIFY_FAILED" in result.detail


@pytest.mark.parametrize("stdout", ["", "   ", "\n"])
def test_empty_stdout_is_verify_failed(stdout: str) -> None:
    runner = FakeCosignRunner(stdout=stdout)
    result = _port(runner).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False
    assert "пустой вывод" in result.detail or "VERIFY_FAILED" in result.detail


@pytest.mark.parametrize(
    "stdout",
    ["not-json", "{", "[]", "{}", "null", "false", '"a string"'],
)
def test_invalid_or_empty_json_is_failure(stdout: str) -> None:
    runner = FakeCosignRunner(stdout=stdout)
    result = _port(runner).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False


def test_missing_sbom_attestation_is_failure() -> None:
    runner = FakeCosignRunner(stdout_by_label={"spdxjson": "[]"})
    result = _port(runner).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False
    assert result.sbom_verified is False
    assert result.provenance_verified is False


def test_missing_provenance_attestation_is_failure() -> None:
    runner = FakeCosignRunner(stdout_by_label={"slsaprovenance": ""})
    result = _port(runner).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False


def test_no_secrets_in_detail_or_audit_fields() -> None:
    """В аудите РОВНО digest + identity_digest; в detail нет stdout/подписи."""
    fields = audit_fields(digest=TEST_DIGEST, identity=TEST_IDENTITY)
    assert set(fields) == {"artifact_digest", "identity_digest"}
    assert fields["artifact_digest"] == TEST_DIGEST
    assert fields["identity_digest"] == hashlib.sha256(TEST_IDENTITY.encode()).hexdigest()
    assert TEST_IDENTITY not in json.dumps(fields)

    runner = FakeCosignRunner(stdout="SECRET-SIGNATURE-MATERIAL")
    result = _port(runner).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert "SECRET-SIGNATURE-MATERIAL" not in result.detail
    assert TEST_IDENTITY not in result.detail


# --------------------------------------------------------------------------- KMS fallback


def test_kms_fallback_uses_fixed_key_and_keeps_verification() -> None:
    """KMS = ЗАФИКСИРОВАННЫЙ trust root, БЕЗ keyless, но проверка НЕ отключена."""
    runner = FakeCosignRunner()
    port = _port(runner, kms_key_ref=TEST_KMS_REF)
    assert port.kms_mode is True
    assert port.signature_verification_enabled is True
    assert port.attestation_verification_enabled is True

    result = port.verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is True
    assert len(runner.calls) == 3
    for argv in runner.calls:
        assert "--key" in argv
        assert TEST_KMS_REF in argv
        # Ни keyless-флагов, ни флагов ослабления проверки.
        assert "--certificate-identity" not in argv
        assert "--certificate-oidc-issuer" not in argv
        assert "--insecure-ignore-signature" not in argv


def test_kms_fallback_still_verifies_sbom_and_provenance() -> None:
    runner = FakeCosignRunner(stdout_by_label={"slsaprovenance": "[]"})
    result = _port(runner, kms_key_ref=TEST_KMS_REF).verify(
        TEST_REF, TEST_IDENTITY, TEST_ISSUER
    )
    assert result.verified is False
    assert len(runner.calls) == 3


def test_kms_disable_words_rejected_on_construction() -> None:
    """'off'/'none'/'false' — НЕ trust root, а попытка отключить проверку."""
    for word in ("off", "none", "false", "disabled", "0", "no", "skip"):
        with pytest.raises(RuntimeConfigError):
            CliCosignPort(runner=FakeCosignRunner(), kms_key_ref=word)


def test_kms_failed_runner_is_not_success() -> None:
    runner = FakeCosignRunner(raise_on="verify", exception=FileNotFoundError("no cosign"))
    result = _port(runner, kms_key_ref=TEST_KMS_REF).verify(
        TEST_REF, TEST_IDENTITY, TEST_ISSUER
    )
    assert result.verified is False


def test_forbidden_flags_are_rejected() -> None:
    for flag in (
        "--insecure-ignore-signature",
        "--insecure-ignore-tlog",
        "--skip-signature-verification",
    ):
        with pytest.raises(Exception):
            assert_verification_enabled(["cosign", "verify", flag, TEST_REF])


# --------------------------------------------------------------------------- команды


def test_keyless_command_shape() -> None:
    argv = keyless_verify_command("cosign", TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert argv == [
        "cosign", "verify",
        "--certificate-identity", TEST_IDENTITY,
        "--certificate-oidc-issuer", TEST_ISSUER,
        TEST_REF,
    ]


def test_kms_command_shape() -> None:
    argv = kms_verify_command("cosign", TEST_REF, TEST_KMS_REF)
    assert argv == ["cosign", "verify", "--key", TEST_KMS_REF, TEST_REF]
    with pytest.raises(Exception):
        kms_verify_command("cosign", TEST_REF, "")


def test_attestation_command_keeps_trust_root() -> None:
    keyless = attestation_command(
        "cosign", TEST_REF, "spdxjson", identity=TEST_IDENTITY, issuer=TEST_ISSUER
    )
    assert "--type" in keyless and "spdxjson" in keyless
    assert "--certificate-identity" in keyless
    kms = attestation_command(
        "cosign",
        TEST_REF,
        "slsaprovenance",
        identity=TEST_IDENTITY,
        issuer=TEST_ISSUER,
        kms_key_ref=TEST_KMS_REF,
    )
    assert "--key" in kms and TEST_KMS_REF in kms
    assert "--certificate-identity" not in kms


def test_missing_identity_is_unsigned() -> None:
    runner = FakeCosignRunner()
    result = _port(runner).verify(TEST_REF, "", TEST_ISSUER)
    assert result.verified is False
    assert runner.calls == []


# --------------------------------------------------------------------------- инъекция attestation


def test_injected_attestation_verifier_is_used() -> None:
    """Альтернативный ИСТОЧНИК подтверждения (не «отключение проверки»)."""
    seen: dict = {}

    def verifier(ref, identity, issuer, **kwargs):  # type: ignore[no-untyped-def]
        seen.update(ref=ref, identity=identity, issuer=issuer, kwargs=kwargs)
        return AttestationEvidence(
            sbom_verified=True, provenance_verified=True, detail="OCI annotations"
        )

    runner = FakeCosignRunner()
    result = CliCosignPort(
        runner=runner, attestation_verifier=verifier
    ).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is True
    assert result.sbom_verified is True and result.provenance_verified is True
    assert seen["ref"] == TEST_REF
    assert len(runner.calls) == 1  # только подпись; attestation — из инъекции
    assert seen["kwargs"]["kms_key_ref"] == ""


def test_incomplete_attestation_evidence_is_quarantined() -> None:
    """Пустое/неполное подтверждение = отказ, а не success (fail-closed)."""
    def verifier(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        return AttestationEvidence(sbom_verified=True, provenance_verified=False)

    result = CliCosignPort(
        runner=FakeCosignRunner(), attestation_verifier=verifier
    ).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False
    assert "SBOM/provenance" in result.detail


def test_attestation_verifier_exception_is_quarantined() -> None:
    """Сбой источника подтверждения = QUARANTINED, не «пропустим проверку»."""
    def broken(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("attestation store unavailable")

    result = CliCosignPort(
        runner=FakeCosignRunner(), attestation_verifier=broken
    ).verify(TEST_REF, TEST_IDENTITY, TEST_ISSUER)
    assert result.verified is False
    assert "RuntimeError" in result.detail


def test_attestation_verifier_must_be_callable() -> None:
    with pytest.raises(RuntimeConfigError):
        CliCosignPort(runner=FakeCosignRunner(), attestation_verifier=object())  # type: ignore[arg-type]
