"""Коды ошибок зоны артефактов плагинов (F4).

Fail-closed: любой отказ проверки подписи, digest, совместимости или отката
приводит к карантину и запрету публикации. Коды — стабильные строковые
идентификаторы для CI-gate, логов и ``QuarantineEvidence.metadata``.
"""

from __future__ import annotations

from enum import StrEnum


class ArtifactErrorCode(StrEnum):
    """Стабильные коды отказов артефактного контура."""

    UNSIGNED = "UNSIGNED"
    VERIFY_FAILED = "VERIFY_FAILED"
    DIGEST_CONFLICT = "DIGEST_CONFLICT"
    INCOMPATIBLE = "INCOMPATIBLE"
    ROLLBACK_INVALID = "ROLLBACK_INVALID"
    QUARANTINED = "QUARANTINED"


class ArtifactError(Exception):
    """Отказ артефактного контура. Несёт код и непустую причину.

    Fail-closed: отсутствие обязательных данных (digest, evidence, подписи)
    трактуется как ошибка, а не как «можно публиковать».
    """

    def __init__(self, code: ArtifactErrorCode, reason: str) -> None:
        if not isinstance(code, ArtifactErrorCode):
            raise TypeError("code must be an ArtifactErrorCode")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError("ArtifactError requires a non-empty reason")
        super().__init__(f"{code.value}: {reason}")
        self.code = code
        self.reason = reason

    @property
    def code_value(self) -> str:
        """Строковое значение кода для логов/CI."""
        return self.code.value


__all__ = ["ArtifactError", "ArtifactErrorCode"]
