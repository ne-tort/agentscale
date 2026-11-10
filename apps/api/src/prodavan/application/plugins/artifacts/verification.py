"""Fail-closed проверка OCI-артефакта плагина перед публикацией (§5.1, кр. 18).

Pipeline (ровно в этом порядке, каждый шаг — обязательный):

    cosign verify (keyless, по digest)
      → digest (совпадение с ожидаемым, без самореференции)
      → validate_manifest (domain.plugins.validation, задача 01)
      → compatibility gate (semver / protocol / capabilities / платформа /
        scan / CSP)
      → decision (APPLIED только при полном успехе)

ЛЮБАЯ неудача → ``RegistryOutcome.QUARANTINED`` + ``QuarantineEvidence``
(типы переиспользуются из ``dynamic_app_registry.contracts``), НЕ публикация.
Ни один «позитивный» результат не строится из непроверенных данных.

Выравнивание gate с задачей 01 (аудит F11, блокер №5, вариант A):
подписанный manifest проходит ``validate_manifest`` со ЗАКРЫТЫМ whitelist
(ровно 11 полей, ``additionalProperties: false``), поэтому gate НЕ читает из
него запрещённые ``requestedCapabilities`` / ``frontend.csp``. Запрошенные
capability берутся из ``manifest["capabilities"]``, сканирование и CSP —
отдельные fail-closed аргументы извне (``scan_verified`` от CI-сканера,
``csp_verified`` от static-origin/OCI-политики), а не поля подписанного JSON.
Иначе один и тот же dict не может пройти оба gate и КАЖДЫЙ артефакт уходит
в QUARANTINED. Детали: ``docs/migration/plugin-manifest-gate-alignment.md``.

Дополнительно verifier выдаёт ``VerificationEvidence`` — привязанный к
конкретным ``ref`` + manifest результат. ``PluginArtifactRegistry.publish``
принимает только такой evidence и проверяет печать verifier'а: подделать
«успешную проверку» конструктором датакласса нельзя.
"""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
import hashlib
import json
import re
from typing import Any, Protocol, runtime_checkable

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
    RegistryResult,
)

from .errors import ArtifactError, ArtifactErrorCode
from .layers import SELF_REFERENCE_KEY
from .oci import PLUGIN_PROTOCOL, OciArtifactRef, is_valid_digest

# Текущая версия платформы. Задаётся снаружи (settings/const); сравнение — semver.
PLATFORM_VERSION = "4.0.0"
_SEMVER_PATTERN = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<pre>(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
    r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)


class VerificationStage(StrEnum):
    """Этап pipeline, на котором принято решение (для evidence/логов)."""

    COSIGN = "cosign"
    DIGEST = "digest"
    MANIFEST = "manifest"
    COMPATIBILITY = "compatibility"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    """Результат ``cosign verify`` (порт ``CosignPort``).

    ``verified`` — подтверждена keyless-подпись с ожидаемыми
    identity/issuer; ``signed_digest`` — digest, который реально подписан.
    ``sbom_verified`` / ``provenance_verified`` — наличие SBOM/provenance (§5.1).
    Fail-closed: отсутствие любого признака = отказ.
    """

    verified: bool
    signed_digest: str | None = None
    identity: str | None = None
    issuer: str | None = None
    sbom_verified: bool = False
    provenance_verified: bool = False
    detail: str = ""

    def __post_init__(self) -> None:
        if self.verified and not is_valid_digest(self.signed_digest):
            raise ValueError("verified result requires a valid signed_digest")


@dataclass(frozen=True, slots=True)
class VerificationEvidence:
    """Привязанное свидетельство проверки (для ``PluginArtifactRegistry.publish``).

    ``manifest_fingerprint`` — sha256 канонического JSON манифеста; ``seal`` —
    неподделываемый объект-печать конкретного экземпляра ``ArtifactVerifier``.
    Создавать evidence вручную запрещено: его выдаёт только
    ``ArtifactVerifier.verify``, а ``publish`` сверяет печать.
    """

    image_ref: str
    artifact_digest: str
    manifest_fingerprint: str
    identity: str
    issuer: str
    sbom_verified: bool
    provenance_verified: bool
    observed_at: datetime
    outcome: RegistryOutcome
    quarantine: QuarantineEvidence | None
    seal: object
    # Снимок входов compatibility gate: позволяет publish() перепроверить
    # решение, не доверяя «голому» флагу успеха. Оба флага приходят ИЗВНЕ
    # подписанного manifest (CI-сканер / static-origin), а не из его полей.
    scan_verified: bool = False
    csp_verified: bool = False
    granted_capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not is_valid_digest(self.artifact_digest):
            raise ValueError("evidence requires a valid artifact digest")
        if not isinstance(self.manifest_fingerprint, str) or len(self.manifest_fingerprint) != 64:
            raise ValueError("evidence requires a sha256 manifest fingerprint")
        if self.outcome is RegistryOutcome.APPLIED and self.quarantine is not None:
            raise ValueError("applied evidence cannot carry quarantine")
        if self.outcome is RegistryOutcome.QUARANTINED and self.quarantine is None:
            raise ValueError("quarantined evidence requires QuarantineEvidence")

    @property
    def verified(self) -> bool:
        """Только APPLIED-свидетельство разрешает публикацию."""
        return self.outcome is RegistryOutcome.APPLIED


@dataclass(frozen=True, slots=True)
class VerificationOutcome:
    """Решение pipeline + evidence (всегда не None, в т.ч. при карантине)."""

    result: RegistryResult
    evidence: VerificationEvidence

    @property
    def outcome(self) -> RegistryOutcome:
        return self.result.outcome

    @property
    def quarantine(self) -> QuarantineEvidence | None:
        return self.result.quarantine


@runtime_checkable
class CosignPort(Protocol):
    """Порт проверки keyless-подписи cosign.

    Реализация (CLI/HTTP-адаптер) вызывается ТОЛЬКО по ``image_ref`` с digest,
    с явными ``identity`` и ``issuer`` (``--certificate-identity`` /
    ``--certificate-oidc-issuer``). Никаких тегов и «доверия по умолчанию».
    """

    def verify(
        self,
        image_ref: str,
        identity: str,
        issuer: str,
    ) -> VerificationResult: ...


@runtime_checkable
class ManifestValidatorPort(Protocol):
    """Порт валидации манифеста (задача 01: ``validate_manifest(data: dict)``).

    Контракт: positional dict, бросает ``PluginValidationError`` при
    невалидности; возвращаемое значение не используется.
    """

    def __call__(self, data: dict[str, Any]) -> object: ...


@runtime_checkable
class CapabilityGrantLike(Protocol):
    """Структурный контракт гранта из задачи 01 (поле ``capabilities``)."""

    @property
    def capabilities(self) -> Collection[str]: ...


@dataclass(frozen=True, slots=True)
class CompatibilityVerdict:
    """Вердикт compatibility gate. ``ok=False`` всегда несёт код и причину."""

    ok: bool
    code: ArtifactErrorCode | None = None
    reason: str = ""
    details: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not self.ok and (self.code is None or not self.reason):
            raise ValueError("negative verdict requires code and reason")
        if self.ok and (self.code is not None or self.reason):
            raise ValueError("positive verdict cannot carry an error")


def _semver_key(value: str) -> tuple[int, int, int, int, tuple[tuple[int, Any], ...]]:
    """Ключ полного SemVer 2.0.0 precedence; build metadata игнорируется."""
    match = _SEMVER_PATTERN.fullmatch(value)
    if match is None:
        raise ValueError("not a semver")
    prerelease = match.group("pre")
    parts: list[tuple[int, Any]] = []
    if prerelease:
        # Числовые идентификаторы сравниваются как int и всегда младше
        # нечисловых. Пары не дают Python сравнивать int со str.
        for chunk in prerelease.split("."):
            parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
        # Префикс кортежа тоже соответствует SemVer: alpha < alpha.1.
        return (
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            0,
            tuple(parts),
        )
    # Отсутствие prerelease старше любого prerelease той же major.minor.patch.
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        1,
        (),
    )


def _is_semver(value: object) -> bool:
    return isinstance(value, str) and _SEMVER_PATTERN.fullmatch(value) is not None


def manifest_fingerprint(manifest: Mapping[str, Any]) -> str:
    """sha256 канонического JSON манифеста (для привязки evidence)."""
    try:
        canonical = json.dumps(
            dict(manifest), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
    except (TypeError, ValueError) as exc:
        raise ArtifactError(
            ArtifactErrorCode.INCOMPATIBLE,
            f"манифест не сериализуется в канонический JSON: {type(exc).__name__}",
        ) from exc
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _grant_capabilities(grant: object) -> frozenset[str]:
    """Извлечь ``capabilities`` из гранта (атрибут либо Mapping-ключ).

    Duck-typing — страховка на случай, пока задача 01 не сдала финальный тип.
    Fail-closed: не удалось извлечь → пустое множество (любой запрос capabilities
    будет шире гранта → отказ).
    """
    if grant is None:
        return frozenset()
    raw: object = None
    if isinstance(grant, Mapping):
        raw = grant.get("capabilities")
    else:
        raw = getattr(grant, "capabilities", None)
    if raw is None or isinstance(raw, (str, bytes)):
        return frozenset()
    if not isinstance(raw, Collection):
        return frozenset()
    return frozenset(str(item) for item in raw if isinstance(item, str) and item.strip())


class CompatibilityGate:
    """Проверки совместимости ДО PUBLISHED (§5.1).

    Вариант A (аудит F11, блокер №5): подписанный manifest уже прошёл
    ``validate_manifest`` задачи 01 с ЗАКРЫТЫМ whitelist (11 полей,
    ``additionalProperties: false``). Поэтому gate НЕ требует и НЕ читает
    запрещённые схемой поля ``requestedCapabilities`` / ``frontend.csp``:
    иначе один и тот же dict не может пройти оба gate и КАЖДЫЙ артефакт
    уходит в QUARANTINED.

    - semver (``version`` и ``min_platform_version``);
    - ``protocol == plugin-rpc/1``;
    - ``capabilities ⊆ CapabilityGrant.capabilities`` — берётся поле
      ``capabilities`` из whitelist задачи 01, а не ``requestedCapabilities``;
    - ``min_platform_version <= PLATFORM_VERSION``;
    - образ просканирован — отдельный аргумент ``scan_verified`` от CI-сканера
      (НЕ поле манифеста);
    - структура ``frontend`` (ровно ``entry`` + ``base_path``, CSP внутри
      подписанного JSON запрещён) и отдельный аргумент ``csp_verified`` —
      подтверждение CSP-заголовка от static-origin / OCI-аннотации
      (задачи 13/14), а не поле манифеста.

    Оба verified-флага fail-closed: отсутствие (default ``False``) = отказ.
    """

    def __init__(self, *, platform_version: str = PLATFORM_VERSION) -> None:
        if not _is_semver(platform_version):
            raise ValueError("platform_version must be semver")
        self._platform_version = platform_version

    def check(
        self,
        manifest: Mapping[str, Any],
        *,
        grant: object | None = None,
        scan_verified: bool = False,
        csp_verified: bool = False,
    ) -> CompatibilityVerdict:
        """Проверить манифест. Любая неудача → ``INCOMPATIBLE``.

        ``scan_verified`` / ``csp_verified`` передаются вызывающей стороной из
        CI (сканер образа) и static-origin (CSP-заголовок), НЕ из подписанного
        manifest. Fail-closed: любой флаг не ``True`` → отказ.
        """
        if not isinstance(manifest, Mapping):
            return self._deny("манифест обязан быть объектом", code=ArtifactErrorCode.INCOMPATIBLE)

        version = manifest.get("version") or manifest.get("manifest_version")
        if not _is_semver(version):
            return self._deny(
                f"version обязана быть semver, получено {version!r}",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )

        protocol = manifest.get("protocol")
        if protocol != PLUGIN_PROTOCOL:
            return self._deny(
                f"protocol обязан быть {PLUGIN_PROTOCOL!r}, получено {protocol!r}",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )

        min_platform = manifest.get("min_platform_version")
        if not _is_semver(min_platform):
            return self._deny(
                f"min_platform_version обязан быть semver, получено {min_platform!r}",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )
        try:
            if _semver_key(str(min_platform)) > _semver_key(self._platform_version):
                return self._deny(
                    f"min_platform_version {min_platform!r} > PLATFORM_VERSION "
                    f"{self._platform_version!r}",
                    code=ArtifactErrorCode.INCOMPATIBLE,
                )
        except ValueError:
            return self._deny("не удалось сравнить semver версии", code=ArtifactErrorCode.INCOMPATIBLE)

        # Запрошенные capability — из ЗАКРЫТОГО whitelist-поля ``capabilities``
        # (задача 01 / schema.json). ``requestedCapabilities`` схемой запрещён
        # и живёт только в OCI-аннотациях CI: gate его не читает и не требует.
        requested = manifest.get("capabilities")
        if not isinstance(requested, (list, tuple)) or not requested or not all(
            isinstance(item, str) and item.strip() for item in requested
        ):
            return self._deny(
                "capabilities обязан быть непустым массивом строк",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )
        granted = _grant_capabilities(grant)
        unknown = frozenset(str(item) for item in requested) - granted
        if unknown:
            return self._deny(
                "capabilities шире CapabilityGrant.capabilities: " + ", ".join(sorted(unknown)),
                code=ArtifactErrorCode.INCOMPATIBLE,
            )

        # Образ просканирован: явный флаг CI-сканера (не поле непроверенного manifest).
        if not isinstance(scan_verified, bool) or scan_verified is not True:
            return self._deny(
                "образ не подтверждён как просканированный (scan_verified)",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )

        # Структура frontend по схеме задачи 01: закрытый объект ровно с
        # ``entry`` + ``base_path``. Поле ``frontend.csp`` НЕ требуется и не
        # читается: CSP — ответственность хоста static-origin (задачи 13/14).
        frontend = manifest.get("frontend")
        if not isinstance(frontend, Mapping):
            return self._deny(
                "frontend обязан быть объектом с entry и base_path",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )
        if set(frontend) != {"entry", "base_path"}:
            return self._deny(
                "frontend обязан содержать ровно entry и base_path; csp — не поле манифеста",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )
        if not all(
            isinstance(frontend[key], str) and frontend[key].strip() for key in ("entry", "base_path")
        ):
            return self._deny(
                "frontend.entry и frontend.base_path обязаны быть непустыми строками",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )

        # CSP подтверждён извне (static-origin / OCI-аннотация): fail-closed,
        # отсутствие флага или любое значение кроме True = отказ.
        if not isinstance(csp_verified, bool) or csp_verified is not True:
            return self._deny(
                "CSP-заголовок static-origin не подтверждён (csp_verified)",
                code=ArtifactErrorCode.INCOMPATIBLE,
            )

        return CompatibilityVerdict(ok=True)

    @staticmethod
    def _deny(reason: str, *, code: ArtifactErrorCode) -> CompatibilityVerdict:
        return CompatibilityVerdict(ok=False, code=code, reason=reason)


class ArtifactVerifier:
    """Pipeline проверки артефакта. Fail-closed, без «тихого успеха».

    ``cosign`` обязателен; ``validator`` — порт ``validate_manifest`` задачи 01.
    Если порт не передан, используется ленивый импорт
    ``prodavan.domain.plugins.validation.validate_manifest``; отсутствие модуля
    или функции → карантин (не публикация).
    """

    def __init__(
        self,
        cosign: CosignPort,
        *,
        validator: ManifestValidatorPort | None = None,
        identity: str,
        issuer: str,
        compatibility: CompatibilityGate | None = None,
        clock: Any = None,
    ) -> None:
        if not isinstance(cosign, CosignPort):
            raise TypeError("cosign must implement CosignPort")
        if not isinstance(identity, str) or not identity.strip():
            raise ValueError("cosign identity is required (keyless --certificate-identity)")
        if not isinstance(issuer, str) or not issuer.strip():
            raise ValueError("cosign issuer is required (--certificate-oidc-issuer)")
        self._cosign = cosign
        self._validator = validator
        self._identity = identity
        self._issuer = issuer
        self._compatibility = compatibility or CompatibilityGate()
        self._clock = clock or (lambda: datetime.now(UTC))
        # Неподделываемая печать экземпляра: evidence выдаётся только этим verifier'ом.
        self._seal = object()

    def verify(
        self,
        ref: OciArtifactRef,
        manifest: Mapping[str, Any],
        *,
        grant: object | None = None,
        scan_verified: bool = False,
        csp_verified: bool = False,
    ) -> VerificationOutcome:
        """Проверить артефакт и вернуть решение + привязанное свидетельство.

        ``result`` — доменный ``RegistryResult`` (APPLIED / QUARANTINED).
        ``evidence`` — выдаётся ВСЕГДА, но публикацию разрешает только
        ``VerificationEvidence.verified is True`` (outcome == APPLIED).

        ``scan_verified`` / ``csp_verified`` — внешние подтверждения CI-сканера
        и static-origin (вариант A), НЕ поля подписанного manifest. Оба
        fail-closed: по умолчанию ``False`` → CompatibilityVerdict(ok=False).
        """
        if not isinstance(ref, OciArtifactRef):
            return self._failure(
                ref=None,
                manifest=manifest,
                stage=VerificationStage.DIGEST,
                code=ArtifactErrorCode.DIGEST_CONFLICT,
                reason="ожидался OciArtifactRef, закреплённый по digest",
            )

        # 1. cosign verify (keyless) — строго по image_ref с digest.
        try:
            verification = self._cosign.verify(
                ref.image_ref(),
                self._identity,
                self._issuer,
            )
        except Exception as exc:  # noqa: BLE001 — fail-closed: любое исключение = отказ
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.COSIGN,
                code=ArtifactErrorCode.VERIFY_FAILED,
                reason=f"cosign verify завершился исключением: {type(exc).__name__}",
            )
        if not isinstance(verification, VerificationResult) or not verification.verified:
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.COSIGN,
                code=ArtifactErrorCode.UNSIGNED,
                reason="keyless-подпись cosign не подтверждена (identity/issuer)",
            )
        if not verification.sbom_verified or not verification.provenance_verified:
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.COSIGN,
                code=ArtifactErrorCode.VERIFY_FAILED,
                reason="отсутствуют SBOM/provenance (§5.1)",
            )

        # 2. digest: подписанный digest должен совпадать с ожидаемым.
        if verification.signed_digest != ref.digest or not is_valid_digest(ref.digest):
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.DIGEST,
                code=ArtifactErrorCode.DIGEST_CONFLICT,
                reason="подписанный digest не совпадает с ожидаемым",
            )
        # Самореференция: artifact_digest не может быть внутри подписываемого manifest.
        if SELF_REFERENCE_KEY in manifest:
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.DIGEST,
                code=ArtifactErrorCode.DIGEST_CONFLICT,
                reason="artifact_digest внутри manifest — самореференция запрещена (§5.1)",
            )

        # 3. validate_manifest (задача 01), ленивый импорт + инъекция.
        try:
            self._run_validator(dict(manifest))
        except ArtifactError as exc:
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.MANIFEST,
                code=exc.code,
                reason=exc.reason,
            )
        except Exception as exc:  # noqa: BLE001 — PluginValidationError и пр. = отказ
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.MANIFEST,
                code=ArtifactErrorCode.INCOMPATIBLE,
                reason=f"validate_manifest отклонил манифест: {type(exc).__name__}",
            )

        # 4. compatibility gate: capability из manifest["capabilities"], а
        # scan/csp — отдельные внешние флаги (вариант A, не поля manifest).
        verdict = self._compatibility.check(
            manifest,
            grant=grant,
            scan_verified=scan_verified,
            csp_verified=csp_verified,
        )
        if not verdict.ok:
            return self._failure(
                ref=ref,
                manifest=manifest,
                stage=VerificationStage.COMPATIBILITY,
                code=verdict.code or ArtifactErrorCode.INCOMPATIBLE,
                reason=verdict.reason,
            )

        # 5. Успех: только проверенные данные; evidence привязан к ref + manifest.
        fingerprint = manifest_fingerprint(manifest)
        evidence = VerificationEvidence(
            image_ref=ref.image_ref(),
            artifact_digest=ref.digest,
            manifest_fingerprint=fingerprint,
            identity=self._identity,
            issuer=self._issuer,
            sbom_verified=verification.sbom_verified,
            provenance_verified=verification.provenance_verified,
            observed_at=self._clock(),
            outcome=RegistryOutcome.APPLIED,
            quarantine=None,
            seal=self._seal,
            scan_verified=scan_verified,
            csp_verified=csp_verified,
            granted_capabilities=tuple(sorted(_grant_capabilities(grant))),
        )
        return VerificationOutcome(
            result=RegistryResult(outcome=RegistryOutcome.APPLIED),
            evidence=evidence,
        )

    def validate_evidence(
        self,
        evidence: object,
        *,
        ref: OciArtifactRef,
        manifest: Mapping[str, Any],
    ) -> VerificationEvidence:
        """Проверить, что evidence выдан ЭТИМ verifier'ом и привязан к ref/manifest.

        Используется ``PluginArtifactRegistry.publish``. Любое расхождение →
        ``ArtifactError`` (fail-closed), публикация не происходит.
        """
        if not isinstance(evidence, VerificationEvidence):
            raise ArtifactError(
                ArtifactErrorCode.QUARANTINED,
                "publish требует VerificationEvidence, выданный ArtifactVerifier.verify",
            )
        if evidence.seal is not self._seal:
            raise ArtifactError(
                ArtifactErrorCode.QUARANTINED,
                "evidence выдан другим экземпляром verifier'а",
            )
        if not evidence.verified:
            raise ArtifactError(
                ArtifactErrorCode.QUARANTINED,
                "evidence не является успешной проверкой: публикация запрещена",
            )
        if evidence.image_ref != ref.image_ref() or evidence.artifact_digest != ref.digest:
            raise ArtifactError(
                ArtifactErrorCode.DIGEST_CONFLICT,
                "evidence привязан к другому OCI-артефакту",
            )
        if evidence.manifest_fingerprint != manifest_fingerprint(manifest):
            raise ArtifactError(
                ArtifactErrorCode.DIGEST_CONFLICT,
                "evidence привязан к другому манифесту",
            )
        if not (evidence.sbom_verified and evidence.provenance_verified):
            raise ArtifactError(
                ArtifactErrorCode.VERIFY_FAILED,
                "evidence не подтверждает SBOM/provenance",
            )
        return evidence

    def recheck_compatibility(
        self,
        manifest: Mapping[str, Any],
        *,
        grant: object | None = None,
        scan_verified: bool = False,
        csp_verified: bool = False,
    ) -> CompatibilityVerdict:
        """Публичный повторный прогон gate (используется реестром при publish)."""
        return self._compatibility.check(
            manifest,
            grant=grant,
            scan_verified=scan_verified,
            csp_verified=csp_verified,
        )

    def _run_validator(self, data: dict[str, Any]) -> None:
        """Вызвать validate_manifest; отсутствие контракта = явный отказ."""
        validator: object | None = self._validator
        if validator is None:
            # Ленивый импорт: доменный модуль задачи 01 может появиться позже.
            from prodavan.domain.plugins.validation import (  # type: ignore[no-redef]
                validate_manifest as validator,
            )

        if not callable(validator):
            raise ArtifactError(
                ArtifactErrorCode.INCOMPATIBLE,
                "validate_manifest недоступен: контракт задачи 01 не подключён",
            )
        validator(data)

    def _failure(
        self,
        *,
        ref: OciArtifactRef | None,
        manifest: Mapping[str, Any],
        stage: VerificationStage,
        code: ArtifactErrorCode,
        reason: str,
    ) -> VerificationOutcome:
        """Построить QUARANTINED + QuarantineEvidence (переиспользуем типы)."""
        digest = ref.digest if ref is not None else ""
        quarantine = QuarantineEvidence(
            reason=self._registry_reason(code),
            source_kind="plugin_artifact_oci",
            source_id=ref.image_ref() if ref is not None else "unknown",
            source_digest=digest,
            observed_at=self._clock(),
            metadata={
                "artifact_error_code": code.value,
                "stage": stage.value,
                "detail": reason,
            },
        )
        result = RegistryResult(
            outcome=RegistryOutcome.QUARANTINED,
            reason=quarantine.reason,
            quarantine=quarantine,
        )
        # Evidence при карантине тоже выдаётся, но НЕ разрешает публикацию.
        evidence = VerificationEvidence(
            image_ref=ref.image_ref() if ref is not None else "unknown",
            artifact_digest=digest or "sha256:" + "0" * 64,
            manifest_fingerprint=_safe_fingerprint(manifest),
            identity=self._identity,
            issuer=self._issuer,
            sbom_verified=False,
            provenance_verified=False,
            observed_at=self._clock(),
            outcome=RegistryOutcome.QUARANTINED,
            quarantine=quarantine,
            seal=self._seal,
        )
        return VerificationOutcome(result=result, evidence=evidence)

    @staticmethod
    def _registry_reason(code: ArtifactErrorCode) -> RegistryReason:
        """Свести код артефакта к доменному ``RegistryReason`` без копирования типа."""
        if code is ArtifactErrorCode.DIGEST_CONFLICT:
            return RegistryReason.ARTIFACT_DIGEST_CONFLICT
        if code is ArtifactErrorCode.ROLLBACK_INVALID:
            return RegistryReason.ROLLBACK_TARGET_INVALID
        # UNSIGNED / VERIFY_FAILED / INCOMPATIBLE / QUARANTINED — конфликт
        # перехода в PUBLISHED (недопустимый переход).
        return RegistryReason.INVALID_TRANSITION


def _safe_fingerprint(manifest: Mapping[str, Any]) -> str:
    """Отпечаток для quarantine-evidence: не должен маскировать причину отказа."""
    try:
        return manifest_fingerprint(manifest)
    except ArtifactError:
        return hashlib.sha256(b"<unserializable-manifest>").hexdigest()


__all__ = [
    "PLATFORM_VERSION",
    "ArtifactVerifier",
    "CapabilityGrantLike",
    "CompatibilityGate",
    "CompatibilityVerdict",
    "CosignPort",
    "ManifestValidatorPort",
    "VerificationEvidence",
    "VerificationOutcome",
    "VerificationResult",
    "VerificationStage",
    "manifest_fingerprint",
]
