"""``CliCosignPort`` — CLI-адаптер ``CosignPort`` (§5.1, критерий №18, F4/CI).

Контракт и почему он именно такой
----------------------------------
``ArtifactVerifier.verify`` (``artifacts/verification.py:379-451``) вызывает
порт **позиционно**::

    verification = self._cosign.verify(ref.image_ref(), self._identity, self._issuer)

Поэтому ``verify(image_ref, identity, issuer)`` намеренно НЕ делает identity и
issuer keyword-only: keyword-only сломал бы КАЖДУЮ публикацию (TypeError
попадает в ``except Exception`` -> ``VERIFY_FAILED`` -> QUARANTINED). Сигнатура
positional-or-keyword совместима с обоими способами вызова.

Fail-closed (без «тихого успеха»)
---------------------------------
* ``:latest`` и ЛЮБОЙ тег запрещены ЯВНО, ДО запуска cosign;
* digest — строго ``^sha256:[0-9a-f]{64}$``, ссылка — ``repo@sha256:<64 hex>``;
* бинарник отсутствует / rc != 0 / timeout / пустой stdout / невалидный JSON /
  пустой JSON / отсутствие identity или issuer -> ``verified=False`` с кодом
  ``UNSIGNED`` или ``VERIFY_FAILED``. Никакого «раз Fallback — значит успех»;
* ``VerificationResult`` требует ``sbom_verified`` и ``provenance_verified``
  (строки 425-431 verifier'а), иначе артефакт ВСЁ РАВНО уйдёт в карантин.
  Один ``cosign verify`` этих фактов не даёт, поэтому по умолчанию дополнительно
  вызываются ``cosign verify-attestation --type spdxjson`` и
  ``--type slsaprovenance``. Альтернативный источник подтверждения можно
  инъектить через ``attestation_verifier=``, но ОТКЛЮЧИТЬ проверку НЕЛЬЗЯ:
  сбой источника или неполный ``AttestationEvidence`` = QUARANTINED.

  ВАЖНО ДЛЯ РОДИТЕЛЯ: текущий CI (задача 16,
  ``.github/workflows/plugin-artifacts.yml``) делает только ``cosign sign`` и
  НЕ делает ``cosign attest``. До добавления attest'ов в CI (или подключения
  альтернативного проверяемого источника SBOM/provenance) КАЖДЫЙ артефакт
  будет честно уходить в QUARANTINED. Это fail-closed по дизайну, а не баг.

KMS-fallback (self-hosted runners без keyless OIDC)
---------------------------------------------------
``.github/workflows/plugin-artifacts.yml:8-22`` фиксирует риск: на self-hosted
раннере OIDC-токен может быть недоступен. Штатный обход — ЗАФИКСИРОВАННЫЙ
trust root (``--key <pub|kms-uri>``) вместо ``--certificate-identity`` /
``--certificate-oidc-issuer``. Режим задаётся параметром конструктора
``kms_key_ref``; он НЕ отключает проверку подписи, а меняет ИСТОЧНИК доверия.
Значения-пустышки (``off`` / ``none`` / ``false`` / ``disabled`` / ``0`` / ``no``)
отвергаются на этапе конструирования: «временно отключить проверку подписи»
запрещено критерием №18.

Логи и аудит
------------
В логи и аудит уходят ТОЛЬКО ``artifact_digest`` и ``identity_digest``
(sha256-hex от identity). Ни самого identity/issuer, ни ref, ни stdout/stderr
cosign, ни подписи, ни токенов, ни DSN — stdout cosign содержит подпись и
сертификат, поэтому он НИКОГДА не логируется и не попадает в ``detail``.

``subprocess`` импортируется ЛОКАЛЬНО, внутри ``_subprocess_runner``: импорт
модуля не запускает ни одного процесса и не ходит в сеть.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Callable

from prodavan.application.plugins.artifacts.errors import ArtifactErrorCode
from prodavan.application.plugins.artifacts.oci import is_valid_digest
from prodavan.application.plugins.artifacts.verification import VerificationResult

from .errors import RuntimeConfigError

logger = logging.getLogger(__name__)

# Формат закреплён §5.1 / CI ``plugin-artifacts.yml:316``: только нижний регистр.
_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_FULL_REF_RE = re.compile(
    r"^(?P<repository>[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?"
    r"/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*)"
    r"@(?P<digest>sha256:[0-9a-f]{64})$"
)

#: Флаги, ослабляющие/отключающие проверку подписи. Ни один не должен появиться
#: в команде: это была бы «тихая деградация» вместо QUARANTINED.
FORBIDDEN_COSIGN_FLAGS: frozenset[str] = frozenset(
    {
        "--insecure-ignore-signature",
        "--insecure-ignore-tlog",
        "--insecure-ignore-sct",
        "--allow-insecure-registry",
        "--skip-signature-verification",
        "--no-verify",
    }
)

#: Значения ``kms_key_ref``, которые означали бы попытку ОТКЛЮЧИТЬ проверку.
_DISABLE_WORDS: frozenset[str] = frozenset(
    {"off", "none", "false", "disabled", "0", "no", "skip", "insecure"}
)

_SBOM_TYPE = "spdxjson"
_PROVENANCE_TYPE = "slsaprovenance"

CosignRunner = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class AttestationEvidence:
    """Подтверждение SBOM/provenance для ``VerificationResult``.

    Оба флага обязаны быть ``True``, иначе ``ArtifactVerifier`` (строки 425-431)
    отправит артефакт в QUARANTINED. «Пустое подтверждение» = отказ.
    """

    sbom_verified: bool
    provenance_verified: bool
    detail: str = ""

    @property
    def complete(self) -> bool:
        return self.sbom_verified is True and self.provenance_verified is True


#: Инъекция источника SBOM/provenance. НЕ «флаг отключения проверки»: любой
#: отказ/исключение источника обязан привести к QUARANTINED, а не к успеху.
AttestationVerifier = Callable[..., AttestationEvidence]


class _CosignFailure(Exception):
    """Внутренний отказ одного шага. Наружу превращается в ``verified=False``."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True, slots=True)
class _RunResult:
    """Нормализованный результат одного вызова cosign.

    ``stdout`` хранится только в памяти для разбора JSON; в логи и ``detail``
    он НЕ уходит (там подпись/сертификат).
    """

    returncode: int
    stdout: str
    stderr: str = ""


def _identity_digest(identity: str) -> str:
    """sha256-hex identity: в аудите вместо plaintext-идентификатора."""
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def audit_fields(*, digest: str, identity: str) -> dict[str, str]:
    """Безопасная проекция для аудита: РОВНО digest + identity_digest.

    Ни ref, ни issuer, ни подпись, ни токен, ни stdout cosign. Список полей
    намеренно не расширяется (§4.5 v4, критерий №11).
    """
    return {
        "artifact_digest": digest,
        "identity_digest": _identity_digest(identity),
    }


def _safe_arg(value: str, *, label: str) -> str:
    """Запрет CRLF/NUL в argv: иначе возможна инъекция аргументов/логов."""
    if not isinstance(value, str) or not value.strip():
        raise _CosignFailure(ArtifactErrorCode.UNSIGNED.value, f"{label} отсутствует")
    if any(ch in value for ch in ("\n", "\r", "\x00")):
        raise _CosignFailure(ArtifactErrorCode.UNSIGNED.value, f"{label} содержит управляющие символы")
    return value


def prepare_ref(image_ref: str) -> str:
    """Проверить ссылку и вернуть digest ДО любого вызова cosign (fail-closed).

    Запрещены: ``:latest`` в любой части, ``/latest``, теги, отсутствие
    ``@sha256:``, верхний регистр hex, мусор после digest. Возвращается только
    валидный digest; ``image_ref`` целиком наружу не отдаётся.
    """
    if not isinstance(image_ref, str) or not image_ref.strip():
        raise _CosignFailure(ArtifactErrorCode.UNSIGNED.value, "OCI-ссылка отсутствует")
    candidate = image_ref.strip()
    # ЯВНЫЙ запрет ':latest' (§5.1, критерий 18) — ДО subprocess.
    if ":latest" in candidate or "/latest" in candidate:
        raise _CosignFailure(
            ArtifactErrorCode.UNSIGNED.value,
            "тег ':latest' запрещён для плагинов: пин только по @sha256",
        )
    match = _FULL_REF_RE.fullmatch(candidate)
    if match is None:
        raise _CosignFailure(
            ArtifactErrorCode.UNSIGNED.value,
            "ожидалась ссылка <registry>/<repo>@sha256:<64 lowercase hex>",
        )
    digest = match.group("digest")
    if _DIGEST_RE.fullmatch(digest) is None or not is_valid_digest(digest):
        raise _CosignFailure(
            ArtifactErrorCode.UNSIGNED.value,
            "digest не соответствует ^sha256:[0-9a-f]{64}$",
        )
    return digest


def keyless_verify_command(binary: str, image_ref: str, identity: str, issuer: str) -> list[str]:
    """``cosign verify`` с ЖЁСТКОЙ привязкой keyless-идентичности (§5.1)."""
    return [
        binary,
        "verify",
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        issuer,
        image_ref,
    ]


def kms_verify_command(binary: str, image_ref: str, kms_key_ref: str) -> list[str]:
    """``cosign verify --key <trust root>`` БЕЗ keyless (fallback self-hosted).

    Это НЕ отключение проверки: вместо Fulcio/OIDC используется зафиксированный
    публичный ключ или KMS-_uri. ``--key`` обязателен и непуст.
    """
    if not kms_key_ref.strip():
        raise _CosignFailure(
            ArtifactErrorCode.VERIFY_FAILED.value,
            "KMS trust root не задан: проверить подпись нечем",
        )
    return [binary, "verify", "--key", kms_key_ref, image_ref]


def _identity_flags(identity: str, issuer: str) -> list[str]:
    return [
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        issuer,
    ]


def attestation_command(
    binary: str,
    image_ref: str,
    attestation_type: str,
    *,
    identity: str,
    issuer: str,
    kms_key_ref: str = "",
) -> list[str]:
    """Команда проверки SBOM/provenance attestation.

    ``--type spdxjson`` / ``--type slsaprovenance``. Проверка подписи attestation
    идёт по тому же trust root, что и основная: keyless identity/issuer либо
    ``--key``. Отключить проверку нельзя ни в одном режиме.
    """
    flags = (
        ["--key", kms_key_ref]
        if kms_key_ref
        else _identity_flags(identity, issuer)
    )
    return [
        binary,
        "verify-attestation",
        "--type",
        attestation_type,
        *flags,
        image_ref,
    ]


def assert_verification_enabled(argv: list[str]) -> None:
    """Явная страховка: в команде нет флагов ослабления проверки подписи."""
    for arg in argv:
        lowered = arg.strip().lower()
        token = lowered.split("=", 1)[0]
        if token in FORBIDDEN_COSIGN_FLAGS or lowered in FORBIDDEN_COSIGN_FLAGS:
            raise _CosignFailure(
                ArtifactErrorCode.VERIFY_FAILED.value,
                "попытка отключить проверку подписи запрещена (критерий №18)",
            )


def attestations_from_cosign(
    image_ref: str,
    identity: str,
    issuer: str,
    *,
    runner: CosignRunner,
    binary: str,
    timeout: float,
    kms_key_ref: str = "",
) -> AttestationEvidence:
    """Проверить SBOM/provenance через ``cosign verify-attestation``.

    Используется по умолчанию. Требует, чтобы артефакт был подписан И
    ``cosign attest``-ован (в CI задачи 16 пока только ``cosign sign`` — см.
    риск в отчёте родителю). Любая ошибка -> оба флага ``False``, что честно
    уводит артефакт в QUARANTINED, а не «пропускает» проверку.
    """
    evidence = AttestationEvidence(sbom_verified=False, provenance_verified=False)
    for attestation_type, field in ((_SBOM_TYPE, "sbom"), (_PROVENANCE_TYPE, "provenance")):
        argv = attestation_command(
            binary,
            image_ref,
            attestation_type,
            identity=identity,
            issuer=issuer,
            kms_key_ref=kms_key_ref,
        )
        try:
            verify_cosign_stdout(runner, argv, timeout=timeout, label=field)
        except _CosignFailure:
            return evidence
        evidence = AttestationEvidence(
            sbom_verified=evidence.sbom_verified or field == "sbom",
            provenance_verified=evidence.provenance_verified or field == "provenance",
            detail="cosign verify-attestation: spdxjson + slsaprovenance",
        )
    return evidence


def verify_cosign_stdout(
    runner: CosignRunner,
    argv: list[str],
    *,
    timeout: float,
    label: str,
) -> Any:
    """Один вызов cosign с жёсткой проверкой результата (fail-closed).

    Вынесено из ``CliCosignPort._invoke``, чтобы его мог переиспользовать
    ``attestations_from_cosign`` (без дублирования парсинга JSON).
    """
    assert_verification_enabled(argv)
    try:
        raw = runner(argv, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — FileNotFoundError/Timeout/CalledProcessError
        raise _CosignFailure(
            ArtifactErrorCode.VERIFY_FAILED.value,
            f"cosign {label} недоступен или упал: {type(exc).__name__}",
        ) from exc

    returncode = getattr(raw, "returncode", None)
    if returncode is None and isinstance(raw, dict):
        returncode = raw.get("returncode")
    if not isinstance(returncode, int) or returncode != 0:
        raise _CosignFailure(
            ArtifactErrorCode.VERIFY_FAILED.value,
            f"cosign {label} завершился с rc={returncode!r}",
        )
    stdout = getattr(raw, "stdout", None)
    if stdout is None and isinstance(raw, dict):
        stdout = raw.get("stdout")
    if not isinstance(stdout, str) or not stdout.strip():
        raise _CosignFailure(
            ArtifactErrorCode.VERIFY_FAILED.value,
            f"cosign {label} вернул пустой вывод: проверка не состоялась",
        )
    try:
        payload = json.loads(stdout)
    except (ValueError, TypeError) as exc:
        raise _CosignFailure(
            ArtifactErrorCode.VERIFY_FAILED.value,
            f"cosign {label} вернул невалидный JSON",
        ) from exc
    # cosign verify/verify-attestation отдают JSON-объект или массив результатов.
    # Скаляр (число/строка/true) — «проверка не состоялась», а не успех: иначе
    # подделанный stdout дал бы verified=True.
    if not isinstance(payload, (dict, list)):
        raise _CosignFailure(
            ArtifactErrorCode.VERIFY_FAILED.value,
            f"cosign {label} вернул JSON не объект/массив",
        )
    if payload == [] or payload == {}:
        raise _CosignFailure(
            ArtifactErrorCode.UNSIGNED.value,
            f"cosign {label} не подтвердил ни одной подписи/attestation",
        )
    return payload


def _subprocess_runner(argv: list[str], *, timeout: float) -> _RunResult:
    """Реальный cosign: ``subprocess`` импортируется ТОЛЬКО здесь, при вызове.

    ``check=True`` + ``capture_output=True`` + ``text=True`` + ``timeout`` —
    ровно как в CI (``.github/scripts/plugin_verify.py:101-125``). Никакого
    ``shell=True``: argv собирается этим модулем из проверенных строк.
    """
    import subprocess  # локальный импорт: import модуля процессов не запускает

    completed = subprocess.run(  # noqa: S603 — argv собран этим модулем, без shell
        argv,
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return _RunResult(
        returncode=int(completed.returncode),
        stdout=completed.stdout or "",
        stderr=completed.stderr or "",
    )


class CliCosignPort:
    """``CosignPort`` (Protocol, структурно): проверка подписи через cosign CLI.

    ``runner`` — инъекция для тестов: ``runner(argv, *, timeout)`` возвращает
    объект с ``returncode`` / ``stdout`` / ``stderr`` (или ``CompletedProcess``).
    По умолчанию используется ``_subprocess_runner`` с локальным импортом
    ``subprocess``. Тесты НЕ запускают cosign.

    ``kms_key_ref`` включает KMS-fallback с ЗАФИКСИРОВАННЫМ trust root. Пустое
    значение = keyless (OIDC/Fulcio). Ни тот, ни другой режим не отключает
    проверку подписи; ``signature_verification_enabled`` всегда ``True``.

    ``attestation_verifier`` — инъекция ДРУГОГО ИСТОЧНИКА подтверждения
    SBOM/provenance (например, проверка OCI-аннотаций реестра). По умолчанию
    ``attestations_from_cosign`` вызывает ``cosign verify-attestation``. Это НЕ
    «флаг отключения»: сбой источника, исключение или неполный
    ``AttestationEvidence`` всегда дают ``verified=False`` (карантин).
    """

    def __init__(
        self,
        *,
        binary: str = "cosign",
        timeout_seconds: float = 60.0,
        kms_key_ref: str | None = None,
        runner: CosignRunner | None = None,
        attestation_verifier: AttestationVerifier | None = None,
    ) -> None:
        if not isinstance(binary, str) or not binary.strip() or any(
            ch.isspace() or ch == "\x00" for ch in binary
        ):
            raise RuntimeConfigError("некорректный путь/имя бинарника cosign")
        timeout = float(timeout_seconds)
        if timeout <= 0 or timeout != timeout:
            raise RuntimeConfigError("таймаут cosign должен быть положительным конечным числом")
        trust_root = "" if kms_key_ref is None else str(kms_key_ref).strip()
        # «Временно отключить проверку подписи» запрещено критерием 18: такие
        # значения — не trust root, а попытка обойти verify. Отказ на старте.
        if trust_root.lower() in _DISABLE_WORDS:
            raise RuntimeConfigError(
                "KMS-fallback не может отключать проверку подписи; укажите реальный "
                "--key <pub|kms-uri> или '' для keyless"
            )
        if trust_root and any(ch in trust_root for ch in ("\n", "\r", "\x00")):
            raise RuntimeConfigError("KMS trust root содержит управляющие символы")
        self._binary = binary
        self._timeout = timeout
        self._kms_key_ref = trust_root
        self._runner: CosignRunner = runner or _subprocess_runner
        # Источник SBOM/provenance подменяется ТОЛЬКО другой ПРОВЕРКОЙ, а не
        # «флагом пропустить». Любой сбой источника = QUARANTINED.
        if attestation_verifier is not None and not callable(attestation_verifier):
            raise RuntimeConfigError("attestation_verifier обязан быть вызываемым")
        self._attestation_verifier: AttestationVerifier = (
            attestation_verifier or attestations_from_cosign
        )
        # Инвариант зафиксирован на уровне объекта: сеттера нет, значения
        # ``False`` не существует ни в одном конструкторе/оверрайде.
        self._signature_verification_enabled = True
        self._attestation_verification_enabled = True

    # -- неизменяемые гарантии (для тестов и ревью) -------------------------

    @property
    def signature_verification_enabled(self) -> bool:
        """ВСЕГДА ``True``: проверить подпись нельзя отключить ни в одном режиме."""
        return self._signature_verification_enabled is True

    @property
    def attestation_verification_enabled(self) -> bool:
        """ВСЕГДА ``True``: SBOM/provenance проверяются всегда."""
        return self._attestation_verification_enabled is True

    @property
    def kms_mode(self) -> bool:
        """Используется ли зафиксированный KMS trust root вместо keyless."""
        return bool(self._kms_key_ref)

    @property
    def kms_key_ref(self) -> str:
        """Зафиксированный trust root (пусто = keyless). НЕ секрет."""
        return self._kms_key_ref

    # -- CosignPort ---------------------------------------------------------

    def verify(
        self,
        image_ref: str,
        identity: str,
        issuer: str,
    ) -> VerificationResult:
        """Проверить keyless/KMS-подпись и вернуть ``VerificationResult``.

        ВАЖНО: identity/issuer positional-or-keyword (см. docstring модуля):
        ``ArtifactVerifier`` вызывает порт позиционно.

        Любая ошибка -> ``VerificationResult(verified=False, ...)`` с кодом
        ``UNSIGNED`` (невалидные входные данные) или ``VERIFY_FAILED`` (сбой
        cosign/окружения). Исключение наружу НЕ пробрасывается: verifier сам
        строит QUARANTINED + ``QuarantineEvidence`` (§5.1).
        """
        digest = ""
        canonical_ref = image_ref.strip() if isinstance(image_ref, str) else ""
        try:
            # 1) Валидация ДО subprocess. Ни одного вызова при ':latest'/битом digest.
            digest = prepare_ref(image_ref)
            identity = _safe_arg(identity, label="certificate-identity")
            issuer = _safe_arg(issuer, label="certificate-oidc-issuer")

            # 2) Основная проверка подписи. KMS-режим меняет источник доверия,
            #    но НЕ отключает саму проверку. В команду уходит КАНОНИЧЕСКАЯ
            #    ссылка (без пробелов по краям), прошедшая ту же валидацию.
            if self._kms_key_ref:
                main = kms_verify_command(self._binary, canonical_ref, self._kms_key_ref)
            else:
                main = keyless_verify_command(self._binary, canonical_ref, identity, issuer)
            self._invoke(main, label="verify")

            # 3) SBOM и provenance: verifier требует ОБА флага (§5.1). Источник
            #    подтверждения инъектится, но НЕ отключается: сбой/исключение
            #    источника -> отказ, пустые флаги -> отказ.
            evidence = self._attestation_verifier(
                canonical_ref,
                identity,
                issuer,
                runner=self._runner,
                binary=self._binary,
                timeout=self._timeout,
                kms_key_ref=self._kms_key_ref,
            )
            if not isinstance(evidence, AttestationEvidence) or not evidence.complete:
                raise _CosignFailure(
                    ArtifactErrorCode.VERIFY_FAILED.value,
                    "SBOM/provenance не подтверждены: публикация запрещена",
                )
        except _CosignFailure as exc:
            self._audit(digest, identity if isinstance(identity, str) else "", outcome="denied")
            return VerificationResult(verified=False, detail=f"{exc.code}: {exc.reason}")
        except Exception as exc:  # noqa: BLE001 — fail-closed: ЛЮБОЕ исключение = отказ
            self._audit(digest, identity if isinstance(identity, str) else "", outcome="denied")
            # В detail только тип исключения: ни stdout, ни stderr, ни ref.
            return VerificationResult(
                verified=False,
                detail=f"{ArtifactErrorCode.VERIFY_FAILED.value}: "
                f"cosign verify завершился ошибкой: {type(exc).__name__}",
            )

        self._audit(digest, identity, outcome="verified")
        return VerificationResult(
            verified=True,
            signed_digest=digest,
            identity=identity,
            issuer=issuer,
            sbom_verified=True,
            provenance_verified=True,
            detail="cosign verify + SBOM + provenance подтверждены",
        )

    # -- внутреннее ---------------------------------------------------------

    def _invoke(self, argv: list[str], *, label: str) -> Any:
        """Один вызов cosign: check=True, таймаут, JSON-разбор, fail-closed."""
        return verify_cosign_stdout(
            self._runner, argv, timeout=self._timeout, label=label
        )

    def _audit(self, digest: str, identity: str, *, outcome: str) -> None:
        """Запись в лог: ТОЛЬКО digest + identity_digest, без секретов/подписи.

        ``outcome`` участвует лишь в самом сообщении (не в значениях полей):
        в аудите остаются ровно два поля из ``audit_fields``.
        """
        fields = audit_fields(digest=digest or "", identity=identity or "")
        logger.info(
            "cosign outcome=%s artifact_digest=%s identity_digest=%s",
            outcome,
            fields["artifact_digest"],
            fields["identity_digest"],
        )


__all__ = [
    "FORBIDDEN_COSIGN_FLAGS",
    "AttestationEvidence",
    "AttestationVerifier",
    "CliCosignPort",
    "assert_verification_enabled",
    "attestation_command",
    "attestations_from_cosign",
    "audit_fields",
    "keyless_verify_command",
    "kms_verify_command",
    "prepare_ref",
    "verify_cosign_stdout",
]
