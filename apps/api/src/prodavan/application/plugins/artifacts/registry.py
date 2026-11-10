"""Реестр артефактов плагинов (F4, §5.1, §13.3, критерий 18).

Переиспользует доменные типы БЕЗ копирования:

* ``AppVersion`` (``domain.dynamic_apps.types``) — версия с digest/semver;
* ``RegistryOutcome`` / ``RegistryReason`` / ``RegistryResult`` /
  ``QuarantineEvidence`` / ``RollbackCommand``
  (``application.dynamic_app_registry.contracts``).

ПРОЕКЦИЯ (решение родителя от 2026-11-08): ``AppVersion.validate_manifest`` —
legacy-контракт declarative-app (``protocol == "1.0"``, ``appId`` без
подчёркиваний, ``entrypoints``), поэтому подписанный plugin-manifest НЕЛЬЗЯ
положить в ``AppVersion.manifest`` напрямую. Реестр хранит нормализованную
legacy-обёртку в ``AppVersion.manifest`` + исходный подписанный plugin-manifest
под ключом ``plugin_manifest``; канонический manifest живёт в
``PluginArtifactRecord.manifest``. Доменный тип не правится.

Fail-closed:
* ``publish`` возможен ТОЛЬКО после успешного ``ArtifactVerifier.verify``
  (проверяется печать и привязка evidence);
* повторная публикация того же digest с другим manifest →
  ``ARTIFACT_DIGEST_CONFLICT``;
* ``rollback`` — только на PUBLISHED, подписанный, не-QUARANTINED digest;
  неподписанный target → ``ROLLBACK_INVALID``.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any, Protocol

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
    RegistryResult,
    RollbackCommand,
    RollbackConflictError,
    rollback_command_fingerprint,
)
from prodavan.domain.dynamic_apps import AppLifecycleStatus, AppVersion

from .errors import ArtifactError, ArtifactErrorCode
from .oci import OciArtifactRef, is_valid_digest
from .verification import (
    ArtifactVerifier,
    VerificationEvidence,
    VerificationOutcome,
    manifest_fingerprint,
)

COMPATIBILITY_ERROR_CODE = "INCOMPATIBLE"
"""Стабильный код, который реестр отдаёт при отказе compatibility gate."""

DIGEST_CONFLICT_ERROR_CODE = "ARTIFACT_DIGEST_CONFLICT"
"""Код повторной публикации digest с другим manifest (доменный RegistryReason)."""


@dataclass(frozen=True, slots=True)
class _GrantSnapshot:
    """Снимок ``capabilities`` из evidence (структурный контракт гранта)."""

    capabilities: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PublicationMetadata:
    """CI publication provenance required for every verified publication."""

    source_id: str
    commit_sha: str
    backend_digest: str
    backend_image_ref: str
    run_url: str

    def __post_init__(self) -> None:
        for name in ("source_id", "commit_sha", "backend_digest", "backend_image_ref", "run_url"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(name + " must be a non-empty string")
            if any(char in value for char in ("\x00", "\n", "\r")):
                raise ValueError(name + " must not contain control characters")


@dataclass(frozen=True, slots=True)
class PluginArtifactRecord:
    """Каноническая запись реестра (исходный подписанный manifest)."""

    plugin_id: str
    version: str
    digest: str
    image_ref: str
    manifest: Mapping[str, Any]
    manifest_fingerprint: str
    status: AppLifecycleStatus
    outcome: RegistryOutcome
    quarantine: QuarantineEvidence | None
    published_at: datetime | None
    created_at: datetime
    # Real evidence from ArtifactVerifier; never synthesized by a store.
    verification: VerificationEvidence | None = None
    publication: PublicationMetadata | None = None
    lifecycle_version: int | None = None

    @property
    def published(self) -> bool:
        return self.status is AppLifecycleStatus.PUBLISHED


class RegistryStorePort(Protocol):
    """Порт хранилища реестра. Реализация — БД/ORM; для тестов — in-memory."""

    def add(self, record: PluginArtifactRecord) -> None: ...

    def replace(self, record: PluginArtifactRecord) -> None: ...

    def rollback(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord: ...

    def by_digest(self, digest: str) -> PluginArtifactRecord | None: ...

    def versions(self, plugin_id: str) -> Sequence[PluginArtifactRecord]: ...

    def get(self, plugin_id: str, version: str) -> PluginArtifactRecord | None: ...

    def current(self, plugin_id: str) -> PluginArtifactRecord | None: ...

    def set_current(self, plugin_id: str, version: str) -> None: ...

    def append_quarantine(self, evidence: QuarantineEvidence) -> None: ...

    def append_lifecycle(self, *, command: RollbackCommand | None, actor: str, detail: str) -> None: ...


    async def add_async(
        self, record: PluginArtifactRecord, *, evidence: VerificationEvidence | None = None
    ) -> None: ...
    async def publish_async(
        self,
        record: PluginArtifactRecord,
        *,
        evidence: VerificationEvidence | None = None,
        actor: str = "system",
        detail: str | None = None,
        expected_version: str | None = None,
    ) -> PluginArtifactRecord: ...
    async def promote_async(
        self,
        record: PluginArtifactRecord,
        *,
        evidence: VerificationEvidence | None = None,
        actor: str = "system",
        detail: str | None = None,
        expected_version: str | None = None,
    ) -> PluginArtifactRecord: ...
    async def replace_async(self, record: PluginArtifactRecord) -> None: ...
    async def rollback_async(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord: ...
    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None: ...
    async def get_async(self, plugin_id: str, version: str) -> PluginArtifactRecord | None: ...
    async def current_async(self, plugin_id: str) -> PluginArtifactRecord | None: ...
    async def set_current_async(self, plugin_id: str, version: str) -> None: ...
    async def append_quarantine_async(self, evidence: QuarantineEvidence) -> None: ...
    async def append_lifecycle_async(
        self, *, command: RollbackCommand | None, actor: str, detail: str
    ) -> None: ...



class InMemoryRegistryStore:
    """Простая in-memory реализация ``RegistryStorePort`` для unit-тестов.

    НЕ предназначена для прода: нет транзакций/блокировок. Реестр поверх неё
    сохраняет fail-closed-семантику за счёт проверок до записи.
    """

    def __init__(self) -> None:
        self._records: list[PluginArtifactRecord] = []
        self._current: dict[str, str] = {}
        self._quarantine: list[QuarantineEvidence] = []
        self._lifecycle: list[tuple[str, str]] = []
        self._lifecycle_versions: dict[tuple[str, str], int] = {}
        self._rollback_facts: dict[str, tuple[str, str, str, datetime]] = {}

    def add(self, record: PluginArtifactRecord) -> None:
        self._records.append(record)
        key = (record.plugin_id, record.version)
        self._lifecycle_versions.setdefault(key, record.lifecycle_version or 0)

    def replace(self, record: PluginArtifactRecord) -> None:
        for index, existing in enumerate(self._records):
            if existing.plugin_id == record.plugin_id and existing.version == record.version:
                self._records[index] = record
                return
        raise KeyError(f"no record for {record.plugin_id}@{record.version}")

    def by_digest(self, digest: str) -> PluginArtifactRecord | None:
        return next((record for record in self._records if record.digest == digest), None)

    def versions(self, plugin_id: str) -> Sequence[PluginArtifactRecord]:
        return tuple(record for record in self._records if record.plugin_id == plugin_id)

    def get(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        return next(
            (
                record
                for record in self._records
                if record.plugin_id == plugin_id and record.version == version
            ),
            None,
        )

    def current(self, plugin_id: str) -> PluginArtifactRecord | None:
        version = self._current.get(plugin_id)
        if version is None:
            return None
        return self.get(plugin_id, version)

    def set_current(self, plugin_id: str, version: str) -> None:
        record = self.get(plugin_id, version)
        if record is not None:
            key = (plugin_id, version)
            generation = self._generation(record) + 1
            self._lifecycle_versions[key] = generation
            self.replace(replace(record, lifecycle_version=generation))
        self._current[plugin_id] = version

    def _generation(self, record: PluginArtifactRecord) -> int:
        value = self._lifecycle_versions.get(
            (record.plugin_id, record.version), record.lifecycle_version
        )
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise ValueError("invalid artifact lifecycle generation")
        return value

    def append_quarantine(self, evidence: QuarantineEvidence) -> None:
        self._quarantine.append(evidence)

    def append_lifecycle(self, *, command: RollbackCommand | None, actor: str, detail: str) -> None:
        self._lifecycle.append((actor, detail))

    async def add_async(
        self, record: PluginArtifactRecord, *, evidence: VerificationEvidence | None = None
    ) -> None:
        if evidence is not None and record.verification is not evidence:
            raise ValueError("record must carry the exact verified evidence")
        self.add(record)

    async def replace_async(self, record: PluginArtifactRecord) -> None:
        self.replace(record)

    def rollback(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord:
        return self._rollback_atomic(
            target=target, command=command, actor=actor, detail=detail
        )

    async def rollback_async(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord:
        return self._rollback_atomic(
            target=target, command=command, actor=actor, detail=detail
        )

    def _rollback_atomic(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord:
        records_before = list(self._records)
        current_before = dict(self._current)
        lifecycle_before = list(self._lifecycle)
        generations_before = dict(self._lifecycle_versions)
        facts_before = dict(self._rollback_facts)
        try:
            if not isinstance(command, RollbackCommand):
                raise TypeError("rollback expects RollbackCommand")
            if actor != command.actor:
                raise RollbackConflictError("rollback command actor binding conflict")
            stored = self.get(target.plugin_id, target.version)
            current = self.current(target.plugin_id)
            if (
                stored is None
                or stored.digest != target.digest
                or not stored.published
                or stored.outcome is not RegistryOutcome.APPLIED
                or stored.quarantine is not None
                or stored.verification is None
            ):
                raise ValueError("rollback target is not a verified published artifact")
            if current is None:
                raise ValueError("rollback requires a current artifact")
            expected_source = command.expected_current_version_id
            expected_generation = command.expected_current_lifecycle_version
            artifact_identity = stored.digest

            if current.digest == stored.digest:
                if expected_source is None:
                    raise RollbackConflictError("rollback target is already current")
                source = self.get(stored.plugin_id, expected_source)
                if source is None:
                    raise RollbackConflictError("rollback replay source is missing")
                source_generation = self._generation(source)
                if (
                    expected_generation is not None
                    and source_generation != expected_generation
                ):
                    raise RollbackConflictError("rollback replay source generation conflict")
                identity = (
                    artifact_identity, expected_source, source_generation
                )
                token = "rollback:" + hashlib.sha256(
                    "|".join(str(item) for item in identity).encode("utf-8")
                ).hexdigest()
                fact = self._rollback_facts.get(token)
                if fact is None:
                    raise RollbackConflictError("rollback replay evidence is missing")
                normalized = replace(
                    command, expected_current_lifecycle_version=source_generation
                )
                fingerprint = rollback_command_fingerprint(normalized)
                if (fact[0], fact[1], fact[2]) != (actor, detail, fingerprint):
                    raise RollbackConflictError("rollback replay semantic conflict")
                result = self.get(stored.plugin_id, stored.version)
                if result is None:
                    raise ValueError("rollback stored artifact is missing")
                return result

            source_version = current.version
            if expected_source is not None and source_version != expected_source:
                raise RollbackConflictError("rollback current version conflict")
            source_generation = self._generation(current)
            if expected_generation is not None and source_generation != expected_generation:
                raise RollbackConflictError("rollback current lifecycle generation conflict")
            identity = (artifact_identity, source_version, source_generation)
            token = "rollback:" + hashlib.sha256(
                "|".join(str(item) for item in identity).encode("utf-8")
            ).hexdigest()
            if token in self._rollback_facts:
                raise RollbackConflictError("rollback lifecycle replay does not point to its target")

            self.set_current(stored.plugin_id, stored.version)
            self.append_lifecycle(command=command, actor=actor, detail=detail)
            fingerprint = rollback_command_fingerprint(
                replace(command, expected_current_lifecycle_version=source_generation)
            )
            self._rollback_facts[token] = (
                actor, detail, fingerprint, datetime.now(UTC)
            )
            result = self.get(stored.plugin_id, stored.version)
            if result is None:
                raise ValueError("rollback stored artifact is missing")
            return result
        except Exception:
            self._records[:] = records_before
            self._current.clear()
            self._current.update(current_before)
            self._lifecycle[:] = lifecycle_before
            self._lifecycle_versions.clear()
            self._lifecycle_versions.update(generations_before)
            self._rollback_facts.clear()
            self._rollback_facts.update(facts_before)
            raise

    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None:
        return self.by_digest(digest)

    async def get_async(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        return self.get(plugin_id, version)

    async def current_async(self, plugin_id: str) -> PluginArtifactRecord | None:
        return self.current(plugin_id)

    async def set_current_async(self, plugin_id: str, version: str) -> None:
        record = self.get(plugin_id, version)
        if record is None or not record.published or record.verification is None:
            raise ValueError("verified published evidence is required for set_current")
        self.set_current(plugin_id, version)

    async def append_lifecycle_async(
        self, *, command: RollbackCommand | None, actor: str, detail: str
    ) -> None:
        self.append_lifecycle(command=command, actor=actor, detail=detail)

    async def append_quarantine_async(self, evidence: QuarantineEvidence) -> None:
        self.append_quarantine(evidence)

    async def publish_async(
        self,
        record: PluginArtifactRecord,
        *,
        evidence: VerificationEvidence | None = None,
        actor: str = "system",
        detail: str | None = None,
        expected_version: str | None = None,
    ) -> PluginArtifactRecord:
        """Atomically insert, pin and journal a publication in memory."""
        return await self._publish_atomic(
            record,
            evidence=evidence,
            actor=actor,
            detail=detail,
            expected_version=expected_version,
            promote=False,
        )

    async def promote_async(
        self,
        record: PluginArtifactRecord,
        *,
        evidence: VerificationEvidence | None = None,
        actor: str = "system",
        detail: str | None = None,
        expected_version: str | None = None,
    ) -> PluginArtifactRecord:
        """Atomically update, pin and journal a verified promotion in memory."""
        return await self._publish_atomic(
            record,
            evidence=evidence,
            actor=actor,
            detail=detail,
            expected_version=expected_version,
            promote=True,
        )

    async def _publish_atomic(
        self,
        record: PluginArtifactRecord,
        *,
        evidence: VerificationEvidence | None,
        actor: str,
        detail: str | None,
        expected_version: str | None,
        promote: bool,
    ) -> PluginArtifactRecord:
        # There is no await in this critical section: asyncio tasks cannot
        # interleave the state mutation.  Snapshots also make failure injection
        # behave like a transaction rollback for this non-production store.
        records_before = list(self._records)
        current_before = dict(self._current)
        lifecycle_before = list(self._lifecycle)
        generations_before = dict(self._lifecycle_versions)
        try:
            if evidence is not None and record.verification is not evidence:
                raise ValueError("record must carry the exact verified evidence")
            if expected_version is not None:
                current = self.current(record.plugin_id)
                if current is not None and current.version != expected_version:
                    raise ValueError("current version conflict")
            by_digest = self.by_digest(record.digest)
            by_version = self.get(record.plugin_id, record.version)
            existing = by_digest or by_version
            if existing is not None:
                if (
                    existing.plugin_id != record.plugin_id
                    or existing.version != record.version
                    or existing.digest != record.digest
                    or existing.manifest_fingerprint != record.manifest_fingerprint
                ):
                    raise ValueError("artifact digest/version conflict")
                if existing.published:
                    # Exact replay is a no-op; never rewrite immutable evidence
                    # or reactivate a legacy row as a side effect of a retry.
                    return existing
                if existing.status is AppLifecycleStatus.REVOKED:
                    raise ValueError("revoked artifact cannot be promoted")
            elif promote:
                raise ValueError("promote target does not exist")

            if existing is None:
                self.add(record)
            else:
                self.replace(record)
            pinned = self.get(record.plugin_id, record.version)
            if pinned is None or not pinned.published or pinned.verification is None:
                raise ValueError("verified published evidence is required for publish")
            self.set_current(record.plugin_id, record.version)
            self.append_lifecycle(
                command=None,
                actor=actor,
                detail=detail or f"{'promote' if promote else 'publish'} {record.image_ref}",
            )
            stored = self.get(record.plugin_id, record.version)
            if stored is None:
                raise ValueError("published artifact disappeared during atomic write")
            return stored
        except Exception:
            self._records[:] = records_before
            self._current.clear()
            self._current.update(current_before)
            self._lifecycle[:] = lifecycle_before
            self._lifecycle_versions.clear()
            self._lifecycle_versions.update(generations_before)
            raise

    # Отладочные/тестовые акцессоры (не часть порта).
    @property
    def quarantine_log(self) -> tuple[QuarantineEvidence, ...]:
        return tuple(self._quarantine)

    @property
    def lifecycle_log(self) -> tuple[tuple[str, str], ...]:
        return tuple(self._lifecycle)


class PluginArtifactRegistry:
    """Реестр: publish / get / current / quarantine / rollback."""

    def __init__(
        self,
        *,
        store: RegistryStorePort,
        verifier: ArtifactVerifier,
        clock: Any = None,
    ) -> None:
        self._store = store
        self._verifier = verifier
        self._clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ API

    async def publish_async(
        self,
        ref: OciArtifactRef,
        manifest: Mapping[str, Any],
        evidence: object,
        *,
        metadata: PublicationMetadata,
        actor: str = "system",
        grant: object | None = None,
        scan_verified: bool | None = None,
        csp_verified: bool | None = None,
    ) -> RegistryResult:
        """Опубликовать артефакт ТОЛЬКО после успешного verify.

        ``metadata`` — обязательный CI ``PublicationMetadata``; production fallback
        без source/commit/backend/run запрещён. ``evidence`` — либо
        ``VerificationEvidence``, либо ``VerificationOutcome`` от
        ``ArtifactVerifier.verify``. Проверяется печать verifier'а, привязка
        к ref/manifest и повторно compatibility gate. Любая неудача →
        ``QUARANTINED`` + ``QuarantineEvidence``, запись НЕ публикуется.

        Повтор того же digest с другим manifest → ``ARTIFACT_DIGEST_CONFLICT``.
        ``scan_verified`` / ``csp_verified`` — внешние подтверждения (вариант A),
        по умолчанию берутся из снимка в evidence; ``None`` = «использовать
        снимок», а ЯВНЫЙ ``False`` = принудительный отказ (fail-closed).
        """
        if not isinstance(metadata, PublicationMetadata):
            raise ArtifactError(
                ArtifactErrorCode.QUARANTINED,
                "publish_async requires PublicationMetadata(source_id, commit_sha, backend_digest, backend_image_ref, run_url)",
            )
        if not isinstance(actor, str) or not actor.strip():
            raise ArtifactError(ArtifactErrorCode.QUARANTINED, "publish требует actor")

        try:
            bound = self._coerce_evidence(evidence)
            verified = self._verifier.validate_evidence(bound, ref=ref, manifest=manifest)
            # Повторная проверка совместимости: evidence не подменяет gate.
            # По умолчанию используем снимок входов из evidence (чтобы вызов
            # publish(ref, manifest, evidence) без доп. аргументов был полным).
            effective_grant = grant if grant is not None else _GrantSnapshot(verified.granted_capabilities)
            effective_scan = verified.scan_verified if scan_verified is None else scan_verified
            effective_csp = verified.csp_verified if csp_verified is None else csp_verified
            verdict = self._verifier.recheck_compatibility(
                manifest,
                grant=effective_grant,
                scan_verified=effective_scan,
                csp_verified=effective_csp,
            )
            if not verdict.ok:
                return await self._quarantine_async(
                    ref=ref,
                    code=verdict.code or ArtifactErrorCode.INCOMPATIBLE,
                    reason=verdict.reason,
                    actor=actor,
                )
            if verified.scan_verified is not True:
                return await self._quarantine_async(
                    ref=ref,
                    code=ArtifactErrorCode.INCOMPATIBLE,
                    reason="evidence не подтверждает сканирование образа",
                    actor=actor,
                )
            if verified.csp_verified is not True:
                return await self._quarantine_async(
                    ref=ref,
                    code=ArtifactErrorCode.INCOMPATIBLE,
                    reason="evidence не подтверждает CSP-заголовок static-origin",
                    actor=actor,
                )
            plugin_id = self._require_plugin_id(manifest)
            version = self._require_version(manifest)
            fingerprint = manifest_fingerprint(manifest)
        except ArtifactError as exc:
            return await self._quarantine_async(ref=ref, code=exc.code, reason=exc.reason, actor=actor)

        # Запрет повторной публикации того же digest с другим manifest.
        by_digest = await self._store.by_digest_async(ref.digest)
        if by_digest is not None:
            if by_digest.manifest_fingerprint != fingerprint:
                return await self._conflict_async(ref, plugin_id, version, fingerprint, actor)
            if by_digest.published:
                # Let the store validate durable replay semantics and return
                # the authoritative row (including persisted timestamps).
                replay = replace(by_digest, verification=verified, publication=metadata)
                stored = await self._store.publish_async(
                    replay,
                    evidence=verified,
                    actor=actor,
                    detail=f"publish {ref.image_ref()}",
                )
                return RegistryResult(
                    outcome=RegistryOutcome.APPLIED,
                    version=self._to_app_version(stored, actor),
                )
            if by_digest.status is AppLifecycleStatus.REVOKED:
                return RegistryResult(
                    outcome=RegistryOutcome.CONFLICT,
                    reason=RegistryReason.VERSION_ALREADY_PUBLISHED,
                )
            # Идентичный черновик: идемпотентно переводим в PUBLISHED.
            return await self._promote_async(
                by_digest, actor, evidence=verified, metadata=metadata
            )

        existing_version = await self._store.get_async(plugin_id, version)
        if existing_version is not None and existing_version.digest != ref.digest:
            return await self._conflict_async(ref, plugin_id, version, fingerprint, actor)

        now = self._clock()
        record = PluginArtifactRecord(
            plugin_id=plugin_id,
            version=version,
            digest=ref.digest,
            image_ref=ref.image_ref(),
            manifest=dict(manifest),
            manifest_fingerprint=fingerprint,
            status=AppLifecycleStatus.PUBLISHED,
            outcome=RegistryOutcome.APPLIED,
            quarantine=None,
            published_at=now,
            created_at=now,
            verification=verified,
            publication=metadata,
        )
        stored = await self._store.publish_async(
            record,
            evidence=verified,
            actor=actor,
            detail=f"publish {ref.image_ref()}",
        )
        # Проекция в доменный AppVersion (переиспользуем тип, не копируем).
        app_version = self._to_app_version(stored, actor)
        return RegistryResult(outcome=RegistryOutcome.APPLIED, version=app_version)

    def get(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        """Вернуть запись по (plugin_id, semver) для синхронных legacy callers."""
        return self._store.get(plugin_id, version)

    async def get_async(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        """Read an artifact without bridging sync SQL onto the event loop."""
        return await self._store.get_async(plugin_id, version)

    def current(self, plugin_id: str) -> PluginArtifactRecord | None:
        """Sync legacy current lookup; async callers must use current_async."""
        record = self._store.current(plugin_id)
        if record is None or not record.published:
            return None
        return record

    async def current_async(self, plugin_id: str) -> PluginArtifactRecord | None:
        """Return current through the async store without sync SQL bridging."""
        record = await self._store.current_async(plugin_id)
        if record is None or not record.published:
            return None
        return record

    def quarantine(
        self,
        ref: OciArtifactRef,
        manifest: Mapping[str, Any],
        *,
        reason: str,
        actor: str = "system",
        code: ArtifactErrorCode = ArtifactErrorCode.QUARANTINED,
    ) -> RegistryResult:
        """Явный карантин артефакта (не публикация).

        Снимает ``current`` pointer, если карантинируемый digest был текущим,
        и пишет ``QuarantineEvidence`` (переиспользуемый доменный тип).
        """
        result = self._quarantine(ref=ref, code=code, reason=reason, actor=actor)
        record = self._store.by_digest(ref.digest)
        if record is not None and not record.published:
            # Уже в карантине — идемпотентно.
            return result
        if record is not None:
            quarantined = replace(
                record,
                status=AppLifecycleStatus.REVOKED,
                outcome=RegistryOutcome.QUARANTINED,
                quarantine=result.quarantine,
                published_at=None,
            )
            self._store.replace(quarantined)
        current = self._store.current(manifest.get("plugin_id", "") if isinstance(manifest, Mapping) else "")
        if current is not None and current.digest == ref.digest:
            # Переводим указатель на предыдущий опубликованный digest, если есть.
            previous = self._previous_published(manifest, exclude_digest=ref.digest)
            if previous is not None:
                self._store.set_current(previous.plugin_id, previous.version)
        return result

    def rollback(
        self,
        plugin_id: str,
        target_digest: str,
        *,
        actor: str = "system",
        reason: str = "rollback to previous immutable digest",
        expected_current_version: str | None = None,
        expected_current_lifecycle_version: int | None = None,
        cosign_verified: bool = False,
    ) -> RegistryResult:
        """Откат на предыдущий immutable digest через ``RollbackCommand``.

        Fail-closed (критерий 18): target обязан существовать, быть PUBLISHED,
        не в карантине и ИМЕТЬ ПОДТВЕРЖДЁННУЮ cosign-подпись. Откат на
        неподписанный digest → ``ROLLBACK_INVALID``.
        """
        if not is_valid_digest(target_digest):
            return self._rollback_invalid(plugin_id, target_digest, "невалидный target_digest")
        target = self._store.by_digest(target_digest)
        if target is None or target.plugin_id != plugin_id:
            return self._rollback_invalid(plugin_id, target_digest, "целевая версия не найдена")
        if not target.published or target.outcome is RegistryOutcome.QUARANTINED:
            return self._rollback_invalid(plugin_id, target_digest, "целевая версия не опубликована")
        # Fail-closed: откат на неподписанный digest запрещён (критерий 18).
        # Подпись подтверждается вызывающей стороной (cosign verify по digest),
        # но отсутствие подтверждения никогда не трактуется как «успех».
        if not isinstance(cosign_verified, bool) or cosign_verified is not True:
            return self._rollback_invalid(
                plugin_id, target_digest, "откат на неподписанный digest запрещён"
            )
        current = self._store.current(plugin_id)
        if current is None:
            return self._rollback_invalid(plugin_id, target_digest, "нет текущей версии")
        if current.digest == target_digest and expected_current_version is None:
            return self._rollback_invalid(plugin_id, target_digest, "цель уже является текущей")
        if (
            current.digest != target_digest
            and expected_current_version is not None
            and current.version != expected_current_version
        ):
            return RegistryResult(
                outcome=RegistryOutcome.CONFLICT,
                reason=RegistryReason.CURRENT_VERSION_CONFLICT,
            )

        command_generation = expected_current_lifecycle_version
        if command_generation is None and current.digest != target_digest:
            command_generation = current.lifecycle_version
        command = RollbackCommand(
            definition_id=plugin_id,
            target_version_id=target.version,
            actor=actor,
            reason=reason,
            expected_current_version_id=expected_current_version,
            expected_artifact_digest=target_digest,
            expected_current_lifecycle_version=command_generation,
        )
        stored = self._store.rollback(
            target=target,
            command=command,
            actor=actor,
            detail=f"rollback {plugin_id}@{target.version}",
        )
        return RegistryResult(
            outcome=RegistryOutcome.APPLIED,
            version=self._to_app_version(stored, actor),
        )

    async def rollback_async(
        self,
        plugin_id: str,
        target_digest: str,
        *,
        actor: str = "system",
        reason: str = "rollback to previous immutable digest",
        expected_current_version: str | None = None,
        expected_current_lifecycle_version: int | None = None,
        cosign_verified: bool = False,
    ) -> RegistryResult:
        """Async variant of rollback; never bridges sync SQL in a running loop."""
        if not is_valid_digest(target_digest):
            return await self._rollback_invalid_async(
                plugin_id, target_digest, "невалидный target_digest"
            )
        target = await self._store.by_digest_async(target_digest)
        if target is None or target.plugin_id != plugin_id:
            return await self._rollback_invalid_async(
                plugin_id, target_digest, "целевая версия не найдена"
            )
        if (
            not target.published
            or target.outcome is RegistryOutcome.QUARANTINED
            or target.status is AppLifecycleStatus.REVOKED
        ):
            return await self._rollback_invalid_async(
                plugin_id, target_digest, "целевая версия не опубликована"
            )
        if not isinstance(cosign_verified, bool) or cosign_verified is not True:
            return await self._rollback_invalid_async(
                plugin_id, target_digest, "откат на неподписанный digest запрещён"
            )
        current = await self._store.current_async(plugin_id)
        if current is None:
            return await self._rollback_invalid_async(
                plugin_id, target_digest, "нет текущей версии"
            )
        if current.digest == target_digest and expected_current_version is None:
            return await self._rollback_invalid_async(
                plugin_id, target_digest, "цель уже является текущей"
            )
        if (
            current.digest != target_digest
            and expected_current_version is not None
            and current.version != expected_current_version
        ):
            return RegistryResult(
                outcome=RegistryOutcome.CONFLICT,
                reason=RegistryReason.CURRENT_VERSION_CONFLICT,
            )

        command_generation = expected_current_lifecycle_version
        if command_generation is None and current.digest != target_digest:
            command_generation = current.lifecycle_version
        command = RollbackCommand(
            definition_id=plugin_id,
            target_version_id=target.version,
            actor=actor,
            reason=reason,
            expected_current_version_id=expected_current_version,
            expected_artifact_digest=target_digest,
            expected_current_lifecycle_version=command_generation,
        )
        stored = await self._store.rollback_async(
            target=target,
            command=command,
            actor=actor,
            detail=f"rollback {plugin_id}@{target.version}",
        )
        return RegistryResult(
            outcome=RegistryOutcome.APPLIED,
            version=self._to_app_version(stored, actor),
        )

    @staticmethod
    def compatibility_error_code() -> str:
        """Стабильный код ошибки compatibility gate (для API/CI)."""
        return COMPATIBILITY_ERROR_CODE

    # --------------------------------------------------------------- helpers

    def _coerce_evidence(self, evidence: object) -> VerificationEvidence:
        if isinstance(evidence, VerificationOutcome):
            return evidence.evidence
        if isinstance(evidence, VerificationEvidence):
            return evidence
        raise ArtifactError(
            ArtifactErrorCode.QUARANTINED,
            "publish требует VerificationEvidence/VerificationOutcome",
        )

    @staticmethod
    def _require_plugin_id(manifest: Mapping[str, Any]) -> str:
        value = manifest.get("plugin_id")
        if not isinstance(value, str) or not value.strip():
            raise ArtifactError(
                ArtifactErrorCode.INCOMPATIBLE, "manifest.plugin_id обязателен"
            )
        return value

    @staticmethod
    def _require_version(manifest: Mapping[str, Any]) -> str:
        value = manifest.get("version") or manifest.get("manifest_version")
        if not isinstance(value, str) or not value.strip():
            raise ArtifactError(
                ArtifactErrorCode.INCOMPATIBLE, "manifest.version (semver) обязателен"
            )
        return value

    def _conflict(
        self,
        ref: OciArtifactRef,
        plugin_id: str,
        version: str,
        fingerprint: str,
        actor: str,
    ) -> RegistryResult:
        """ARTIFACT_DIGEST_CONFLICT: тот же digest / версия, другой manifest."""
        result = self._quarantine(
            ref=ref,
            code=ArtifactErrorCode.DIGEST_CONFLICT,
            reason="повторная публикация digest с другим manifest (ARTIFACT_DIGEST_CONFLICT)",
            actor=actor,
            registry_reason=RegistryReason.ARTIFACT_DIGEST_CONFLICT,
        )
        # Фиксируем конфликт как запись-черновик (не PUBLISHED, не current).
        existing = self._store.get(plugin_id, version)
        if existing is None:
            now = self._clock()
            self._store.add(
                PluginArtifactRecord(
                    plugin_id=plugin_id,
                    version=version,
                    digest=ref.digest,
                    image_ref=ref.image_ref(),
                    manifest={},
                    manifest_fingerprint=fingerprint,
                    status=AppLifecycleStatus.DRAFT,
                    outcome=RegistryOutcome.QUARANTINED,
                    quarantine=result.quarantine,
                    published_at=None,
                    created_at=now,
                )
            )
        return result

    def _promote(self, record: PluginArtifactRecord, actor: str) -> RegistryResult:
        """Reject the legacy non-atomic sync promote path unconditionally."""
        del record, actor
        raise ArtifactError(
            ArtifactErrorCode.QUARANTINED,
            "sync promote is disabled; use publish_async with the atomic store",
        )

    def _quarantine(
        self,
        *,
        ref: OciArtifactRef,
        code: ArtifactErrorCode,
        reason: str,
        actor: str,
        registry_reason: RegistryReason | None = None,
    ) -> RegistryResult:
        mapped = registry_reason or _reason_for(code)
        evidence = QuarantineEvidence(
            reason=mapped,
            source_kind="plugin_artifact_registry",
            source_id=ref.image_ref(),
            source_digest=ref.digest,
            observed_at=self._clock(),
            metadata={
                "artifact_error_code": code.value,
                "detail": reason,
                "actor": actor,
            },
        )
        self._store.append_quarantine(evidence)
        return RegistryResult(
            outcome=RegistryOutcome.QUARANTINED,
            reason=mapped,
            quarantine=evidence,
        )

    async def _promote_async(
        self,
        record: PluginArtifactRecord,
        actor: str,
        *,
        evidence: VerificationEvidence,
        metadata: PublicationMetadata,
    ) -> RegistryResult:
        promoted = replace(
            record,
            status=AppLifecycleStatus.PUBLISHED,
            outcome=RegistryOutcome.APPLIED,
            quarantine=None,
            published_at=self._clock(),
            verification=evidence,
            publication=metadata,
        )
        stored = await self._store.promote_async(
            promoted,
            evidence=evidence,
            actor=actor,
            detail=f"promote {promoted.image_ref}",
        )
        return RegistryResult(
            outcome=RegistryOutcome.APPLIED,
            version=self._to_app_version(stored, actor),
        )

    async def _conflict_async(
        self,
        ref: OciArtifactRef,
        plugin_id: str,
        version: str,
        fingerprint: str,
        actor: str,
    ) -> RegistryResult:
        result = await self._quarantine_async(
            ref=ref,
            code=ArtifactErrorCode.DIGEST_CONFLICT,
            reason="repeated digest with another manifest",
            actor=actor,
            registry_reason=RegistryReason.ARTIFACT_DIGEST_CONFLICT,
        )
        existing = await self._store.get_async(plugin_id, version)
        if existing is None:
            await self._store.add_async(PluginArtifactRecord(
                plugin_id=plugin_id,
                version=version,
                digest=ref.digest,
                image_ref=ref.image_ref(),
                manifest={},
                manifest_fingerprint=fingerprint,
                status=AppLifecycleStatus.DRAFT,
                outcome=RegistryOutcome.QUARANTINED,
                quarantine=result.quarantine,
                published_at=None,
                created_at=self._clock(),
            ))
        return result

    async def _quarantine_async(
        self,
        *,
        ref: OciArtifactRef,
        code: ArtifactErrorCode,
        reason: str,
        actor: str,
        registry_reason: RegistryReason | None = None,
    ) -> RegistryResult:
        mapped = registry_reason or _reason_for(code)
        evidence = QuarantineEvidence(
            reason=mapped,
            source_kind="plugin_artifact_registry",
            source_id=ref.image_ref(),
            source_digest=ref.digest,
            observed_at=self._clock(),
            metadata={
                "artifact_error_code": code.value,
                "detail": reason,
                "actor": actor,
            },
        )
        await self._store.append_quarantine_async(evidence)
        return RegistryResult(
            outcome=RegistryOutcome.QUARANTINED,
            reason=mapped,
            quarantine=evidence,
        )

    def _rollback_invalid(
        self, plugin_id: str, target_digest: str, reason: str
    ) -> RegistryResult:
        evidence = QuarantineEvidence(
            reason=RegistryReason.ROLLBACK_TARGET_INVALID,
            source_kind="plugin_artifact_registry",
            source_id=f"{plugin_id}@{target_digest}",
            source_digest=target_digest,
            observed_at=self._clock(),
            metadata={
                "artifact_error_code": ArtifactErrorCode.ROLLBACK_INVALID.value,
                "detail": reason,
            },
        )
        self._store.append_quarantine(evidence)
        return RegistryResult(
            outcome=RegistryOutcome.QUARANTINED,
            reason=RegistryReason.ROLLBACK_TARGET_INVALID,
            quarantine=evidence,
        )

    async def _rollback_invalid_async(
        self, plugin_id: str, target_digest: str, reason: str
    ) -> RegistryResult:
        evidence = QuarantineEvidence(
            reason=RegistryReason.ROLLBACK_TARGET_INVALID,
            source_kind="plugin_artifact_registry",
            source_id=f"{plugin_id}@{target_digest}",
            source_digest=target_digest,
            observed_at=self._clock(),
            metadata={
                "artifact_error_code": ArtifactErrorCode.ROLLBACK_INVALID.value,
                "detail": reason,
            },
        )
        await self._store.append_quarantine_async(evidence)
        return RegistryResult(
            outcome=RegistryOutcome.QUARANTINED,
            reason=RegistryReason.ROLLBACK_TARGET_INVALID,
            quarantine=evidence,
        )

    def _previous_published(
        self, manifest: Mapping[str, Any], *, exclude_digest: str
    ) -> PluginArtifactRecord | None:
        plugin_id = manifest.get("plugin_id") if isinstance(manifest, Mapping) else None
        if not isinstance(plugin_id, str):
            return None
        candidates = [
            record
            for record in self._store.versions(plugin_id)
            if record.published and record.digest != exclude_digest
        ]
        return max(candidates, key=lambda record: record.published_at or record.created_at, default=None)

    @staticmethod
    def _to_app_version(record: PluginArtifactRecord, actor: str) -> AppVersion:
        """Нормализованная legacy-проекция (решение родителя, см. docstring модуля).

        Исходный подписанный plugin-manifest НЕ копируется в поля ``AppVersion``,
        а вкладывается под ключом ``plugin_manifest``; обёртка удовлетворяет
        ``AppVersion.validate_manifest`` (protocol 1.0, appId, displayName,
        entrypoints, version). Доменный тип не правится.
        """
        now = record.published_at or record.created_at
        safe_app_id = _normalize_app_id(record.plugin_id)
        # Вариант A: capability берём из поля ``capabilities`` закрытого
        # whitelist подписанного manifest (задача 01), а не из запрещённого
        # схемой ``requestedCapabilities``. Legacy-обёртка AppVersion хранит
        # их под своим историческим ключом (контракт доменного типа).
        raw_caps = record.manifest.get("capabilities", ())
        capabilities = tuple(str(item) for item in raw_caps) if isinstance(raw_caps, (list, tuple)) else ()
        legacy_manifest: dict[str, Any] = {
            "protocol": "1.0",
            "appId": safe_app_id,
            "displayName": record.plugin_id,
            "version": record.version,
            "requestedCapabilities": list(capabilities),
            "entrypoints": {
                "plugin": {
                    "artifact": {"sha256": record.digest.removeprefix("sha256:")},
                    "image_ref": record.image_ref,
                    "published_by": actor,
                }
            },
            "plugin_manifest": dict(record.manifest),
        }
        return AppVersion(
            id=f"{record.plugin_id}:{record.version}",
            app_definition_id=record.plugin_id,
            manifest_version=record.version,
            artifact_digest=record.digest,
            manifest=legacy_manifest,
            requested_capabilities=(),
            status=record.status,
            created_at=now,
            published_at=record.published_at,
        )


def _normalize_app_id(plugin_id: str) -> str:
    """Привести plugin_id к ``^[a-z][a-z0-9-]{2,63}$`` без потери смысла."""
    normalized = "".join(
        char.lower() if char.isalnum() else "-" for char in plugin_id
    ).strip("-")
    if not normalized or not normalized[0].isalpha():
        normalized = "plugin-" + normalized
    return normalized[:64]


def _reason_for(code: ArtifactErrorCode) -> RegistryReason:
    if code is ArtifactErrorCode.DIGEST_CONFLICT:
        return RegistryReason.ARTIFACT_DIGEST_CONFLICT
    if code is ArtifactErrorCode.ROLLBACK_INVALID:
        return RegistryReason.ROLLBACK_TARGET_INVALID
    return RegistryReason.INVALID_TRANSITION


__all__ = [
    "COMPATIBILITY_ERROR_CODE",
    "DIGEST_CONFLICT_ERROR_CODE",
    "InMemoryRegistryStore",
    "PluginArtifactRecord",
    "PublicationMetadata",
    "PluginArtifactRegistry",
    "RegistryStorePort",
]
