#!/usr/bin/env python3
"""Verify-before-publish для OCI-артефактов плагинов (§5.1, критерий 18, F4).

Pure-python, только stdlib. Запускается CI ПОСЛЕ ``cosign sign`` и ДО перевода
артефакта в состояние PUBLISHED в реестре (AppVersion / RegistryOutcome).

Порядок (fail-closed, строго по §5.1 «Реестр: cosign verify → digest → manifest →
compatibility gate»):

    1. cosign verify (keyless, по @sha256-digest) backend-образа и OCI-артефакта;
    2. проверка digest (формат, запрет ':latest', совпадение с манифестом);
    3. validate_manifest-подобные проверки: semver / protocol=plugin-rpc/1 /
       capability allowlist / min_platform_version / ровно 3 слоя /
       отсутствие самореференции;
    4. CompatibilityGate (минимальная версия платформы, CapabilityGrant);
    5. решение: ``PUBLISH_ALLOWED`` или ``QUARANTINED``.

ЛЮБАЯ ошибка (исключение, отсутствие данных, «тихий» успех без проверки) даёт
``QUARANTINED`` и НИКОГДА ``publish``. Непроверенный digest не публикуется.

``cosign_runner`` — инъекция для тестов: вызываемая функция ``list[str] -> Any``.
В CI это ``_ci_cosign_runner`` (``subprocess.run(..., check=True)``). Сам
``subprocess`` НЕ импортируется и НЕ запускается на уровне модуля: импорт
скрипта не должен ни подписывать, ни проверять, ни ходить в сеть.

CLI печатает JSON-решение в stdout; ``QUARANTINED`` -> exit code 1 (job падает).
При наличии ``GITHUB_OUTPUT`` / ``GITHUB_STEP_SUMMARY`` пишет туда решение и
digest'ы (это и есть «записать digest в output/summary»).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Константы и коды. Значения совпадают с artifacts/errors.py (F4) и
# plugin_manifest_builder.py, чтобы QuarantineEvidence.metadata был стабильным.
# ---------------------------------------------------------------------------

DECISION_PUBLISH = "PUBLISH_ALLOWED"
DECISION_QUARANTINE = "QUARANTINED"

PLUGIN_PROTOCOL = "plugin-rpc/1"
CAPABILITY_ALLOWLIST: frozenset[str] = frozenset(
    {"cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network"}
)
DEFAULT_OIDC_ISSUER = "https://token.actions.githubusercontent.com"

_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SEMVER_RE = re.compile(
    r"^(?P<major>0|[1-9]\d*)\.(?P<minor>0|[1-9]\d*)\.(?P<patch>0|[1-9]\d*)"
    r"(?:-(?P<prerelease>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+(?P<build>[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?$"
)
_REF_RE = re.compile(
    r"^[a-z0-9]+(?:[.-][a-z0-9]+)*(?::[0-9]+)?"
    r"/[a-z0-9]+(?:[._-][a-z0-9]+)*(?:/[a-z0-9]+(?:[._-][a-z0-9]+)*)*"
    r"@sha256:[0-9a-f]{64}$"
)


class VerifyErrorCode:
    """Стабильные строковые коды решения (общие с ArtifactErrorCode)."""

    OK = "OK"
    UNSIGNED = "UNSIGNED"
    VERIFY_FAILED = "VERIFY_FAILED"
    DIGEST_CONFLICT = "DIGEST_CONFLICT"
    INVALID_DIGEST = "INVALID_DIGEST"
    LATEST_FORBIDDEN = "LATEST_FORBIDDEN"
    INCOMPATIBLE = "INCOMPATIBLE"
    INVALID_MANIFEST = "INVALID_MANIFEST"
    MISSING_INPUT = "MISSING_INPUT"
    QUARANTINED = "QUARANTINED"


class VerifyError(Exception):
    """Внутренний отказ одного из шагов pipeline. Всегда -> QUARANTINED."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


CosignRunner = Callable[[list[str]], Any]


# ---------------------------------------------------------------------------
# cosign: CI-runner и построение команды
# ---------------------------------------------------------------------------


def _ci_cosign_runner(argv: list[str]) -> dict[str, Any]:
    """CI-реализация cosign_runner: subprocess с ``check=True``.

    ВАЖНО: ``subprocess`` импортируется ТОЛЬКО здесь, при вызове. Импорт модуля
    не запускает ни одного процесса. Fail-closed: ненулевой код и любое
    исключение пробрасываются наверх и превращаются в QUARANTINED.
    """
    import subprocess  # локальный импорт: побочных эффектов при import модуля нет

    completed = subprocess.run(  # noqa: S603 — argv собирается только этим модулем
        argv,
        check=True,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:  # check=True уже гарантирует, но проверяем явно
        raise VerifyError(
            VerifyErrorCode.VERIFY_FAILED,
            f"cosign завершился с кодом {completed.returncode}: {completed.stderr.strip()[:500]}",
        )
    return {
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def _cosign_verify_command(ref: str, identity: str, oidc_issuer: str) -> list[str]:
    """Команда ``cosign verify`` с жёсткой привязкой keyless-идентичности.

    §5.1: ``cosign verify --certificate-identity --certificate-oidc-issuer``.
    Без обоих флагов проверка подписи не считается состоявшейся.
    """
    return [
        "cosign",
        "verify",
        "--certificate-identity",
        identity,
        "--certificate-oidc-issuer",
        oidc_issuer,
        ref,
    ]


def _run_cosign(runner: CosignRunner, argv: list[str], label: str) -> dict[str, Any]:
    """Безопасно вызвать инъекцию и трактовать ЛЮБОЙ сомнительный исход как отказ."""
    if not callable(runner):
        raise VerifyError(
            VerifyErrorCode.MISSING_INPUT, "cosign_runner должен быть вызываемой функцией"
        )
    if not isinstance(argv, list) or not argv or argv[0] != "cosign":
        raise VerifyError(
            VerifyErrorCode.MISSING_INPUT, f"{label}: некорректная команда cosign"
        )
    for arg in argv:
        if not isinstance(arg, str) or "\n" in arg or "\0" in arg or "\r" in arg:
            raise VerifyError(
                VerifyErrorCode.MISSING_INPUT, f"{label}: недопустимый аргумент команды"
            )
    try:
        result = runner(argv)
    except VerifyError:
        raise
    except Exception as exc:  # cosign verify упал -> QUARANTINED, не publish
        raise VerifyError(
            VerifyErrorCode.VERIFY_FAILED,
            f"{label}: cosign verify завершился ошибкой: {exc!r}",
        ) from exc

    # Fail-closed: «молчаливый» False / ненулевой returncode / ok=false — отказ.
    if result is False:
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"{label}: cosign вернул False")
    if isinstance(result, int) and result != 0:
        raise VerifyError(VerifyErrorCode.VERIFY_FAILED, f"{label}: cosign rc={result}")
    if isinstance(result, dict) and result.get("ok") is False:
        raise VerifyError(
            VerifyErrorCode.VERIFY_FAILED,
            f"{label}: {str(result.get('detail', 'ok=false'))[:300]}",
        )
    returncode = getattr(result, "returncode", None)
    if returncode is None and isinstance(result, dict):
        returncode = result.get("returncode", 0)
    if isinstance(returncode, int) and returncode != 0:
        raise VerifyError(
            VerifyErrorCode.VERIFY_FAILED, f"{label}: cosign rc={returncode}"
        )
    return {"label": label, "ref": argv[-1], "verified": True}


# ---------------------------------------------------------------------------
# Вспомогательные проверки
# ---------------------------------------------------------------------------


def _require_digest(value: str, field_name: str) -> str:
    """Digest строго ``^sha256:[0-9a-f]{64}$``; ':latest' запрещён явно."""
    if not isinstance(value, str) or not value.strip():
        raise VerifyError(
            VerifyErrorCode.INVALID_DIGEST, f"{field_name}: отсутствует digest (fail-closed)"
        )
    candidate = value.strip()
    if ":latest" in candidate or candidate.endswith("/latest"):
        raise VerifyError(
            VerifyErrorCode.LATEST_FORBIDDEN,
            f"{field_name}: ':latest' для плагинов запрещён (§5.1, критерий 18)",
        )
    if _DIGEST_RE.fullmatch(candidate) is None:
        raise VerifyError(
            VerifyErrorCode.INVALID_DIGEST,
            f"{field_name}: ожидался ^sha256:[0-9a-f]{{64}}$, получено {candidate!r}",
        )
    return candidate


def _require_ref(value: str, field_name: str) -> str:
    """Ссылка обязана быть по digest (``repo@sha256:...``), без тегов."""
    if not isinstance(value, str) or not value.strip():
        raise VerifyError(
            VerifyErrorCode.MISSING_INPUT,
            f"{field_name}: отсутствует OCI-ссылка по digest (fail-closed)",
        )
    candidate = value.strip()
    if ":latest" in candidate or "/latest" in candidate:
        raise VerifyError(
            VerifyErrorCode.LATEST_FORBIDDEN, f"{field_name}: ':latest' запрещён"
        )
    if "@" not in candidate:
        raise VerifyError(
            VerifyErrorCode.MISSING_INPUT,
            f"{field_name}: ссылка без '@sha256:...' — пин по тегу запрещён",
        )
    if _REF_RE.fullmatch(candidate) is None:
        raise VerifyError(
            VerifyErrorCode.INVALID_DIGEST,
            f"{field_name}: некорректная ссылка по digest: {candidate!r}",
        )
    return candidate


def _load_manifest_module() -> Any:
    """Ленивая загрузка соседнего builder'а — единый источник правил валидации.

    При обычном CLI ``python .github/scripts/plugin_verify.py`` каталог скрипта
    уже в ``sys.path[0]``. Fallback нужен для pytest, импортирующего модуль
    из другого каталога. Любая ошибка импорта -> QUARANTINED (не публикуем).
    """
    try:
        import plugin_manifest_builder as module  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import plugin_manifest_builder as module  # type: ignore[import-not-found]
    if not hasattr(module, "validate_signed_manifest"):
        raise VerifyError(
            VerifyErrorCode.INVALID_MANIFEST,
            "plugin_manifest_builder не экспортирует validate_signed_manifest",
        )
    return module


def _read_manifest(manifest_path: str) -> dict[str, Any]:
    """Прочитать подписываемый OCI-манифест. Fail-closed на любом отклонении."""
    if not isinstance(manifest_path, str) or not manifest_path.strip():
        raise VerifyError(
            VerifyErrorCode.MISSING_INPUT, "путь до манифеста не задан (fail-closed)"
        )
    path = manifest_path.strip()
    if not os.path.isfile(path):
        raise VerifyError(
            VerifyErrorCode.MISSING_INPUT, f"файл манифеста не найден: {path!r}"
        )
    try:
        with open(path, "r", encoding="utf-8") as handle:
            raw = handle.read()
    except OSError as exc:
        raise VerifyError(
            VerifyErrorCode.INVALID_MANIFEST, f"не удалось прочитать манифест: {exc!r}"
        ) from exc
    if ":latest" in raw:
        raise VerifyError(
            VerifyErrorCode.LATEST_FORBIDDEN,
            "манифест содержит ':latest' (пин только по digest)",
        )
    try:
        manifest = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise VerifyError(
            VerifyErrorCode.INVALID_MANIFEST, f"манифест не является JSON: {exc!r}"
        ) from exc
    if not isinstance(manifest, dict):
        raise VerifyError(
            VerifyErrorCode.INVALID_MANIFEST, "корень манифеста должен быть объектом"
        )
    return manifest


def validate_manifest(
    manifest: dict[str, Any], *, backend_digest: str
) -> dict[str, Any]:
    """Проверки semver / protocol / capabilities / min_platform / 3 слоя.

    Делегирует ``plugin_manifest_builder.validate_signed_manifest`` (единый
    источник истины). ``ManifestError`` пробрасывается с его кодом.
    """
    module = _load_manifest_module()
    try:
        return module.validate_signed_manifest(manifest, backend_digest=backend_digest)
    except module.ManifestError:
        raise
    except Exception as exc:  # fail-closed на любую непредвиденную ошибку валидации
        raise VerifyError(
            VerifyErrorCode.INVALID_MANIFEST, f"валидация манифеста упала: {exc!r}"
        ) from exc


# ---------------------------------------------------------------------------
# Compatibility gate
# ---------------------------------------------------------------------------


def _semver_key(value: str) -> tuple[int, int, int, int, tuple[Any, ...]]:
    """Ключ сравнения semver 2.0.0: релиз > prerelease, buildmeta игнорируется."""
    match = _SEMVER_RE.fullmatch(value.strip())
    if match is None:
        raise VerifyError(
            VerifyErrorCode.INCOMPATIBLE, f"невалидный semver для gate: {value!r}"
        )
    prerelease = match.group("prerelease")
    parts: list[tuple[int, Any]] = []
    if prerelease:
        # Нормализуем идентификаторы к однотипным парам, чтобы сравнение
        # кортежей никогда не упало на смеси int/str (semver 2.0.0:
        # числовой идентификатор младше буквенно-цифрового).
        for chunk in prerelease.split("."):
            parts.append((0, int(chunk)) if chunk.isdigit() else (1, chunk))
        # (0, parts) < (1, ()) — prerelease младше соответствующего релиза
        return (
            int(match.group("major")),
            int(match.group("minor")),
            int(match.group("patch")),
            0,
            tuple(parts),
        )
    return (
        int(match.group("major")),
        int(match.group("minor")),
        int(match.group("patch")),
        1,
        (),
    )


@dataclass(frozen=True)
class GateResult:
    """Результат compatibility gate."""

    compatible: bool
    code: str
    reason: str
    checks: list[dict[str, Any]] = field(default_factory=list)


class CompatibilityGate:
    """Проверка совместимости ДО PUBLISHED (§5.1).

    Проверяет: semver версии плагина, ``protocol == plugin-rpc/1``,
    ``requestedCapabilities`` против allowlist и против выданного
    ``CapabilityGrant``, ``min_platform_version`` против версии платформы.

    Fail-closed: если версия платформы или grant не переданы, gate считает
    артефакт НЕсовместимым. «Нет данных» != «можно публиковать».
    """

    def __init__(
        self,
        platform_version: str | None = None,
        granted_capabilities: Iterable[str] | None = None,
        *,
        supported_capabilities: Iterable[str] = CAPABILITY_ALLOWLIST,
    ) -> None:
        # Значения могут прийти из окружения CI (см. workflow), но пустое
        # значение — это отсутствие данных, а не «всё разрешено».
        self.platform_version = (
            platform_version or os.environ.get("PLATFORM_VERSION") or ""
        ).strip()
        raw_grants = granted_capabilities
        if raw_grants is None:
            env_grants = os.environ.get("PLUGIN_GRANTED_CAPABILITIES", "")
            raw_grants = [part.strip() for part in env_grants.split(",") if part.strip()]
        self.granted_capabilities: frozenset[str] = frozenset(
            str(cap).strip() for cap in raw_grants if str(cap).strip()
        )
        self.supported_capabilities: frozenset[str] = frozenset(supported_capabilities)

    def evaluate(self, summary: dict[str, Any]) -> GateResult:
        """Вернуть решение gate. Никогда не «доверяет» неполному summary."""
        checks: list[dict[str, Any]] = []

        def check(name: str, ok: bool, detail: str) -> None:
            checks.append({"name": name, "ok": bool(ok), "detail": detail})

        # 1. semver версии плагина (уже проверен в validate_manifest, дублируем).
        version = summary.get("version", "")
        version_ok = isinstance(version, str) and _SEMVER_RE.fullmatch(version) is not None
        check("semver", version_ok, f"version={version!r}")

        # 2. protocol — единственный допустимый handshake.
        protocol = summary.get("protocol")
        protocol_ok = protocol == PLUGIN_PROTOCOL
        check("protocol", protocol_ok, f"protocol={protocol!r}, ожидался {PLUGIN_PROTOCOL!r}")

        # 3. Capability: allowlist платформы + выданный CapabilityGrant.
        capabilities = tuple(summary.get("capabilities", ()))
        unsupported = tuple(cap for cap in capabilities if cap not in self.supported_capabilities)
        check(
            "capability_allowlist",
            not unsupported,
            f"неподдерживаемые capability: {unsupported or 'нет'}",
        )
        if not self.platform_version:
            check("platform_version", False, "версия платформы не предоставлена")
        else:
            check("platform_version", True, f"platform_version={self.platform_version!r}")
        if not self.granted_capabilities:
            check(
                "capability_grant",
                False,
                "CapabilityGrant не предоставлен: публикация без grant запрещена (fail-closed)",
            )
        else:
            missing = tuple(cap for cap in capabilities if cap not in self.granted_capabilities)
            check(
                "capability_grant",
                not missing,
                f"capability без grant: {missing or 'нет'}",
            )

        # 4. min_platform_version <= platform_version.
        min_platform = summary.get("min_platform_version", "")
        if not self.platform_version:
            version_gate_ok = False
            version_detail = "нет версии платформы — сравнение невозможно"
        else:
            try:
                version_gate_ok = _semver_key(min_platform) <= _semver_key(self.platform_version)
                version_detail = (
                    f"min_platform_version={min_platform!r} <= "
                    f"platform_version={self.platform_version!r}"
                )
            except VerifyError as exc:
                version_gate_ok = False
                version_detail = exc.reason
        check("min_platform_version", version_gate_ok, version_detail)

        failed = [item for item in checks if not item["ok"]]
        if failed:
            return GateResult(
                compatible=False,
                code=VerifyErrorCode.INCOMPATIBLE,
                reason="; ".join(f"{item['name']}: {item['detail']}" for item in failed),
                checks=checks,
            )
        return GateResult(
            compatible=True,
            code=VerifyErrorCode.OK,
            reason="compatibility gate пройден",
            checks=checks,
        )


# ---------------------------------------------------------------------------
# Публичный контракт
# ---------------------------------------------------------------------------


def _quarantine(
    *,
    code: str,
    reason: str,
    checks: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Единая форма решения «в карантин». Никакой публикации."""
    return {
        "decision": DECISION_QUARANTINE,
        "publish_allowed": False,
        "code": code,
        "reason": reason,
        "checks": checks,
        "manifest_summary": context.get("manifest_summary"),
        "backend_digest": context.get("backend_digest"),
        "artifact_ref": context.get("artifact_ref"),
        "backend_ref": context.get("backend_ref"),
        "quarantine_evidence": {
            "code": code,
            "reason": reason,
            "ref": context.get("artifact_ref"),
            "backend_digest": context.get("backend_digest"),
            "checks": checks,
        },
    }


def _allowed(
    *,
    checks: list[dict[str, Any]],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Единая форма решения «публикация разрешена» — только после всех проверок."""
    return {
        "decision": DECISION_PUBLISH,
        "publish_allowed": True,
        "code": VerifyErrorCode.OK,
        "reason": "cosign verify + digest + manifest + compatibility gate пройдены",
        "checks": checks,
        "manifest_summary": context.get("manifest_summary"),
        "backend_digest": context.get("backend_digest"),
        "artifact_ref": context.get("artifact_ref"),
        "backend_ref": context.get("backend_ref"),
        "quarantine_evidence": None,
    }


def verify_artifact(
    manifest_path: str,
    backend_digest: str,
    *,
    cosign_runner: CosignRunner,
    artifact_ref: str | None = None,
    backend_ref: str | None = None,
    platform_version: str | None = None,
    granted_capabilities: Iterable[str] | None = None,
    certificate_identity: str | None = None,
    certificate_oidc_issuer: str | None = None,
) -> dict[str, Any]:
    """Проверить артефакт перед публикацией и вернуть структуру решения.

    Args:
        manifest_path: путь до подписанного OCI-манифеста (JSON).
        backend_digest: ``sha256:<64 hex>`` backend-образа из output сборки.
        cosign_runner: вызываемая ``list[str] -> Any`` (инъекция для тестов;
            в CI — subprocess с ``check=True``). Импорт модуля процессов не
            запускает.
        artifact_ref: ``repo@sha256:...`` OCI-артефакта (fallback: env
            ``PLUGIN_ARTIFACT_REF``). Обязателен — иначе QUARANTINED.
        backend_ref: ``repo@sha256:...`` backend-образа (fallback: env
            ``PLUGIN_BACKEND_REF``). Обязателен — иначе QUARANTINED.
        platform_version: версия платформы для gate (fallback: env
            ``PLATFORM_VERSION``). Отсутствие -> QUARANTINED.
        granted_capabilities: выданные capability (fallback: env
            ``PLUGIN_GRANTED_CAPABILITIES``). Отсутствие -> QUARANTINED.
        certificate_identity: OIDC-identity подписанта (fallback: env
            ``COSIGN_CERTIFICATE_IDENTITY``). Отсутствие -> QUARANTINED.
        certificate_oidc_issuer: issuer Fulcio (по умолчанию GitHub Actions).

    Returns:
        Словарь решения: ``decision`` = ``PUBLISH_ALLOWED`` | ``QUARANTINED``,
        ``publish_allowed``, ``code``, ``reason``, ``checks``,
        ``manifest_summary``, ``quarantine_evidence``, digest'ы и ссылки.

    Fail-closed: ЛЮБАЯ ошибка (включая отсутствие обязательных входных
    данных и «тихое» исключение) возвращает ``QUARANTINED``. Функция НЕ
    возбуждает исключений наружу (кроме SystemExit/KeyboardInterrupt).
    """
    checks: list[dict[str, Any]] = []
    context: dict[str, Any] = {}

    def record(name: str, ok: bool, detail: str) -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail})

    try:
        # --- Шаг 0: обязательные входные данные (fail-closed) -----------------
        if not callable(cosign_runner):
            raise VerifyError(
                VerifyErrorCode.MISSING_INPUT,
                "cosign_runner не передан: проверка подписи не может быть пропущена",
            )

        digest = _require_digest(backend_digest, "backend_digest")
        context["backend_digest"] = digest
        record("backend_digest_format", True, f"digest={digest}")

        artifact = _require_ref(
            artifact_ref or os.environ.get("PLUGIN_ARTIFACT_REF", ""), "artifact_ref"
        )
        backend = _require_ref(
            backend_ref or os.environ.get("PLUGIN_BACKEND_REF", ""), "backend_ref"
        )
        context["artifact_ref"] = artifact
        context["backend_ref"] = backend
        record("digest_pinned_refs", True, f"artifact={artifact}; backend={backend}")

        identity = (
            certificate_identity
            or os.environ.get("COSIGN_CERTIFICATE_IDENTITY")
            or ""
        ).strip()
        if not identity:
            raise VerifyError(
                VerifyErrorCode.MISSING_INPUT,
                "COSIGN_CERTIFICATE_IDENTITY не задан: keyless-подпись без привязки "
                "к identity не проверяется (fail-closed)",
            )
        issuer = (
            certificate_oidc_issuer
            or os.environ.get("COSIGN_CERTIFICATE_OIDC_ISSUER")
            or DEFAULT_OIDC_ISSUER
        ).strip()
        if not issuer.startswith("https://"):
            raise VerifyError(
                VerifyErrorCode.MISSING_INPUT, f"некорректный OIDC issuer: {issuer!r}"
            )

        # --- Шаг 1: cosign verify (keyless) по digest ------------------------
        # Проверяем ОБА объекта: и сам OCI-артефакт, и backend-образ, на
        # который он ссылается. Иначе подписанный манифест мог бы указывать на
        # неподписанный образ (ровно та ошибка v3, описанная в §13.3).
        for label, ref in (("oci-artifact", artifact), ("backend-image", backend)):
            command = _cosign_verify_command(ref, identity, issuer)
            _run_cosign(cosign_runner, command, label)
            record(f"cosign_verify:{label}", True, f"ref={ref}; issuer={issuer}")

        # --- Шаг 2: проверка digest ------------------------------------------
        if not backend.endswith("@" + digest):
            raise VerifyError(
                VerifyErrorCode.DIGEST_CONFLICT,
                "backend_ref не закреплён на переданный backend_digest",
            )
        record("digest_match", True, "backend_ref и backend_digest совпадают")

        # --- Шаг 3: validate_manifest-подобные проверки ---------------------
        manifest = _read_manifest(manifest_path)
        summary = validate_manifest(manifest, backend_digest=digest)
        context["manifest_summary"] = summary
        if summary.get("layer_count") != 3:
            raise VerifyError(
                VerifyErrorCode.INVALID_MANIFEST,
                "манифест должен содержать ровно 3 слоя",
            )
        record(
            "validate_manifest",
            True,
            "semver / protocol=plugin-rpc/1 / capability allowlist / "
            "min_platform_version / ровно 3 слоя / без самореференции",
        )

        # --- Шаг 4: CompatibilityGate ---------------------------------------
        gate = CompatibilityGate(
            platform_version=platform_version,
            granted_capabilities=granted_capabilities,
        )
        gate_result = gate.evaluate(summary)
        for item in gate_result.checks:
            record(f"gate:{item['name']}", item["ok"], item["detail"])
        if not gate_result.compatible:
            return _quarantine(
                code=VerifyErrorCode.INCOMPATIBLE,
                reason=gate_result.reason,
                checks=checks,
                context=context,
            )

        # --- Шаг 5: publish allowed ------------------------------------------
        return _allowed(checks=checks, context=context)

    except VerifyError as exc:
        return _quarantine(code=exc.code, reason=exc.reason, checks=checks, context=context)
    except Exception as exc:
        # Абсолютно любая непредвиденная ошибка = карантин, не publish.
        module = None
        code = VerifyErrorCode.QUARANTINED
        try:
            module = _load_manifest_module()
            if isinstance(exc, module.ManifestError):
                code = exc.code
        except Exception:
            pass
        return _quarantine(
            code=code,
            reason=f"{type(exc).__name__}: {exc}",
            checks=checks,
            context=context,
        )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _build_cli_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify-before-publish OCI-артефакта плагина (fail-closed)."
    )
    parser.add_argument("--manifest", required=True, help="путь до OCI-манифеста JSON")
    parser.add_argument(
        "--backend-digest", required=True, help="sha256:<64 hex> из output docker/build-push-action"
    )
    parser.add_argument("--artifact-ref", default="", help="repo@sha256:<64 hex> артефакта")
    parser.add_argument("--backend-ref", default="", help="repo@sha256:<64 hex> backend-образа")
    parser.add_argument("--platform-version", default="", help="semver версия платформы")
    parser.add_argument(
        "--granted-capabilities", default="", help="выданные CapabilityGrant через запятую"
    )
    parser.add_argument("--certificate-identity", default="", help="OIDC identity подписанта")
    parser.add_argument(
        "--certificate-oidc-issuer",
        default=DEFAULT_OIDC_ISSUER,
        help="OIDC issuer Fulcio",
    )
    parser.add_argument("--output", default="", help="дополнительно записать JSON-решение в файл")
    parser.add_argument(
        "--no-fail-on-quarantine",
        action="store_true",
        help="НЕ ронять job при QUARANTINED (только для локальной отладки; в CI не использовать)",
    )
    return parser


def _write_github_files(decision: dict[str, Any]) -> None:
    """Записать digest и решение в ``GITHUB_OUTPUT`` / ``GITHUB_STEP_SUMMARY``."""
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as handle:
            handle.write(f"decision={decision['decision']}\n")
            handle.write(f"code={decision['code']}\n")
            handle.write(f"backend_digest={decision.get('backend_digest') or ''}\n")
            handle.write(f"artifact_ref={decision.get('artifact_ref') or ''}\n")
            handle.write(f"backend_ref={decision.get('backend_ref') or ''}\n")
            handle.write(f"publish_allowed={'true' if decision['publish_allowed'] else 'false'}\n")

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        rows = [
            "| Проверка | Результат |",
            "|---|---|",
            f"| Решение | **{decision['decision']}** (`{decision['code']}`) |",
            f"| Причина | {decision['reason']} |",
            f"| Backend digest | `{decision.get('backend_digest') or '—'}` |",
            f"| Артефакт | `{decision.get('artifact_ref') or '—'}` |",
            f"| Backend ref | `{decision.get('backend_ref') or '—'}` |",
            "",
            "| Шаг | OK | Деталь |",
            "|---|---|---|",
        ]
        for item in decision["checks"]:
            detail = str(item["detail"]).replace("|", "\\|").replace("\n", " ")
            rows.append(f"| {item['name']} | {'✅' if item['ok'] else '❌'} | {detail} |")
        rows.append("")
        rows.append(
            "> Пин ТОЛЬКО по `@sha256:...`; `:latest` запрещён (§5.1, критерий 18). "
            "При `QUARANTINED` публикация запрещена."
        )
        with open(summary_path, "a", encoding="utf-8") as handle:
            handle.write("\n".join(rows) + "\n")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI: JSON-решение в stdout; ``QUARANTINED`` -> exit 1 (fail-closed)."""
    args = _build_cli_parser().parse_args(argv)
    decision = verify_artifact(
        manifest_path=args.manifest,
        backend_digest=args.backend_digest,
        cosign_runner=_ci_cosign_runner,
        artifact_ref=args.artifact_ref or None,
        backend_ref=args.backend_ref or None,
        platform_version=args.platform_version or None,
        granted_capabilities=(
            [part.strip() for part in args.granted_capabilities.split(",") if part.strip()]
            if args.granted_capabilities
            else None
        ),
        certificate_identity=args.certificate_identity or None,
        certificate_oidc_issuer=args.certificate_oidc_issuer or None,
    )
    payload = json.dumps(decision, ensure_ascii=False, indent=2, sort_keys=True)
    print(payload)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as handle:
            handle.write(payload + "\n")
    try:
        _write_github_files(decision)
    except OSError as exc:
        # Не подменяем решение, но не прячем проблему записи output/summary.
        print(f"::warning::не удалось записать GITHUB_OUTPUT/SUMMARY: {exc!r}", file=sys.stderr)
    if decision["decision"] == DECISION_PUBLISH:
        return 0
    if args.no_fail_on_quarantine:
        return 0
    print(f"::error::{decision['code']}: {decision['reason']} — публикация запрещена", file=sys.stderr)
    return 1


# ---------------------------------------------------------------------------
# Unit-тесты (НЕ запускаются в этой сессии; родитель вынесет в pytest)
# ---------------------------------------------------------------------------


def _selftest() -> None:
    """Покрытие: :latest, невалидный digest, самореференция, capability,
    ровно 3 слоя, падение ``cosign verify`` -> QUARANTINED.

    Функция определена, но НЕ вызывается из ``__main__``: CLI всегда делает
    реальную проверку. Родитель перенесёт тело в pytest без изменений логики.
    """
    import tempfile

    # Изолируем тест от переменных окружения CI: иначе проверки «отсутствие
    # обязательных данных» могут случайно пройти за счёт внешних значений.
    # Родитель при переносе в pytest может заменить это на monkeypatch.delenv.
    for _name in (
        "COSIGN_CERTIFICATE_IDENTITY",
        "COSIGN_CERTIFICATE_OIDC_ISSUER",
        "PLATFORM_VERSION",
        "PLUGIN_GRANTED_CAPABILITIES",
        "PLUGIN_ARTIFACT_REF",
        "PLUGIN_BACKEND_REF",
    ):
        os.environ.pop(_name, None)

    good_digest = "sha256:" + "a" * 64
    other_digest = "sha256:" + "b" * 64
    artifact_ref = f"ghcr.io/ne-tort/plugins/mod_equipment@{good_digest}"
    backend_ref = f"ghcr.io/ne-tort/plugins/mod_equipment/backend@{good_digest}"

    def ok_runner(argv: list[str]) -> None:
        assert isinstance(argv, list) and argv[0] == "cosign"
        assert "--certificate-identity" in argv
        assert "--certificate-oidc-issuer" in argv
        return None

    def failing_runner(argv: list[str]) -> None:
        raise RuntimeError("cosign verify: no matching signatures")

    def nonzero_runner(argv: list[str]) -> int:
        return 1

    def false_runner(argv: list[str]) -> bool:
        return False

    with tempfile.TemporaryDirectory() as tmp:
        frontend = os.path.join(tmp, "index.html")
        migrations = os.path.join(tmp, "changelog.xml")
        with open(frontend, "w", encoding="utf-8") as handle:
            handle.write("<html></html>")
        with open(migrations, "w", encoding="utf-8") as handle:
            handle.write("<databaseChangeLog/>")

        module = _load_manifest_module()
        valid = module.build_oci_manifest(
            plugin_id="mod_equipment",
            version="1.2.0",
            backend_digest=good_digest,
            frontend_files=[frontend],
            migrations_files=[migrations],
            requested_capabilities=("cache", "objects"),
            min_platform_version="1.0.0",
        )

        def manifest_file(payload: dict[str, Any], name: str = "m.json") -> str:
            path = os.path.join(tmp, name)
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, ensure_ascii=False)
            return path

        def verify(
            payload: dict[str, Any] | None = None,
            *,
            digest: str = good_digest,
            runner: CosignRunner = ok_runner,
            platform_version: str = "1.5.0",
            grants: tuple[str, ...] = ("cache", "objects"),
            ref: str = artifact_ref,
            bref: str = backend_ref,
            identity: str = "https://github.com/ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main",
            issuer: str = DEFAULT_OIDC_ISSUER,
        ) -> dict[str, Any]:
            path = manifest_file(valid if payload is None else payload)
            return verify_artifact(
                path,
                digest,
                cosign_runner=runner,
                artifact_ref=ref,
                backend_ref=bref,
                platform_version=platform_version,
                granted_capabilities=grants,
                certificate_identity=identity,
                certificate_oidc_issuer=issuer,
            )

        # Базовый случай: все проверки проходят -> PUBLISH_ALLOWED.
        ok = verify()
        assert ok["decision"] == DECISION_PUBLISH, ok
        assert ok["publish_allowed"] is True
        assert ok["code"] == VerifyErrorCode.OK
        assert ok["manifest_summary"]["layer_count"] == 3
        assert ok["quarantine_evidence"] is None

        # 1) ':latest' отклоняется.
        latest = verify(digest="ghcr.io/x/p:latest")
        assert latest["decision"] == DECISION_QUARANTINE, latest
        assert latest["code"] == VerifyErrorCode.LATEST_FORBIDDEN, latest
        latest_ref = verify(ref="ghcr.io/ne-tort/plugins/mod_equipment:latest")
        assert latest_ref["decision"] == DECISION_QUARANTINE
        assert latest_ref["code"] == VerifyErrorCode.LATEST_FORBIDDEN

        # 2) Невалидный digest и DIGEST_CONFLICT.
        for bad in ("sha256:ABC", "sha256:" + "A" * 64, "", "md5:" + "a" * 64):
            res = verify(digest=bad)
            assert res["decision"] == DECISION_QUARANTINE, (bad, res)
        conflict = verify(digest=other_digest)
        assert conflict["decision"] == DECISION_QUARANTINE
        assert conflict["code"] in (
            VerifyErrorCode.DIGEST_CONFLICT,
            VerifyErrorCode.INVALID_DIGEST,
        ), conflict
        bad_ref = verify(bref=f"ghcr.io/ne-tort/plugins/mod_equipment/backend@{other_digest}")
        assert bad_ref["decision"] == DECISION_QUARANTINE

        # 3) Самореференция: artifact_digest / signature в подписываемом теле.
        self_ref = json.loads(json.dumps(valid))
        self_ref["artifact_digest"] = good_digest
        res = verify(self_ref)
        assert res["decision"] == DECISION_QUARANTINE, res
        assert res["code"] == module.ManifestErrorCode.SELF_REFERENCE, res
        nested = json.loads(json.dumps(valid))
        nested["layers"][1]["annotations"]["signature"] = "MEUCI..."
        res = verify(nested)
        assert res["decision"] == DECISION_QUARANTINE
        assert res["code"] == module.ManifestErrorCode.SELF_REFERENCE

        # 4) Неизвестная capability / отсутствующий grant / gate по версии.
        unknown = json.loads(json.dumps(valid))
        unknown["annotations"]["requestedCapabilities"] = "cache,root"
        res = verify(unknown)
        assert res["decision"] == DECISION_QUARANTINE, res
        assert res["code"] == module.ManifestErrorCode.UNKNOWN_CAPABILITY, res
        no_grant = verify(grants=())
        assert no_grant["decision"] == DECISION_QUARANTINE
        assert no_grant["code"] == VerifyErrorCode.INCOMPATIBLE
        old_platform = verify(platform_version="0.9.0")
        assert old_platform["decision"] == DECISION_QUARANTINE
        assert old_platform["code"] == VerifyErrorCode.INCOMPATIBLE
        bad_protocol = json.loads(json.dumps(valid))
        bad_protocol["annotations"]["protocol"] = "plugin-rpc/2"
        res = verify(bad_protocol)
        assert res["decision"] == DECISION_QUARANTINE
        assert res["code"] == module.ManifestErrorCode.INVALID_PROTOCOL

        # 5) Ровно 3 слоя.
        two = json.loads(json.dumps(valid))
        two["layers"].pop()
        res = verify(two)
        assert res["decision"] == DECISION_QUARANTINE, res
        assert res["code"] == module.ManifestErrorCode.INVALID_LAYERS, res
        four = json.loads(json.dumps(valid))
        four["layers"].append(json.loads(json.dumps(valid["layers"][2])))
        assert verify(four)["decision"] == DECISION_QUARANTINE

        # 6) cosign verify упал -> QUARANTINED (и «тихий» отказ тоже).
        for runner in (failing_runner, nonzero_runner, false_runner):
            res = verify(runner=runner)
            assert res["decision"] == DECISION_QUARANTINE, (runner, res)
            assert res["code"] == VerifyErrorCode.VERIFY_FAILED, (runner, res)
            assert any(
                item["name"].startswith("cosign_verify:") for item in res["checks"]
            ) is False  # проверка не записана как успешная

        # 7) Fail-closed на отсутствии обязательных данных.
        assert verify(identity="")["decision"] == DECISION_QUARANTINE
        assert verify(ref="")["decision"] == DECISION_QUARANTINE
        assert verify(bref="")["decision"] == DECISION_QUARANTINE
        assert verify(platform_version="")["decision"] == DECISION_QUARANTINE
        missing = verify_artifact(
            os.path.join(tmp, "absent.json"),
            good_digest,
            cosign_runner=ok_runner,
            artifact_ref=artifact_ref,
            backend_ref=backend_ref,
            platform_version="1.5.0",
            granted_capabilities=("cache", "objects"),
            certificate_identity="id",
        )
        assert missing["decision"] == DECISION_QUARANTINE
        assert missing["code"] == VerifyErrorCode.MISSING_INPUT


if __name__ == "__main__":
    raise SystemExit(main())
