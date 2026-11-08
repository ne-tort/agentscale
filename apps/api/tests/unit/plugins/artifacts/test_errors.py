"""Unit-тесты codes/errors зоны артефактов."""

from __future__ import annotations

import pytest

from prodavan.application.plugins.artifacts import (
    ARTIFACT_ERROR_CODES,
    ArtifactError,
    ArtifactErrorCode,
)


def test_error_codes_are_complete() -> None:
    assert set(ARTIFACT_ERROR_CODES) == {
        "UNSIGNED",
        "VERIFY_FAILED",
        "DIGEST_CONFLICT",
        "INCOMPATIBLE",
        "ROLLBACK_INVALID",
        "QUARANTINED",
    }


def test_artifact_error_carries_code_and_reason() -> None:
    error = ArtifactError(ArtifactErrorCode.UNSIGNED, "подпись отсутствует")
    assert error.code is ArtifactErrorCode.UNSIGNED
    assert error.code_value == "UNSIGNED"
    assert "подпись отсутствует" in str(error)


def test_artifact_error_fail_closed_on_bad_args() -> None:
    with pytest.raises(TypeError):
        ArtifactError("UNSIGNED", "причина")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        ArtifactError(ArtifactErrorCode.INCOMPATIBLE, "   ")
