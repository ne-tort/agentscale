"""``SqlArtifactRegistryStore`` — ``RegistryStorePort`` (задача 07) поверх БД A10.

Параллельная публикация защищена двумя уровнями: legacy add/set_current
остаются доступны для старых callers, а новый publish_async/promote_async
выполняет artifact + current CAS + durable lifecycle в одном AsyncSession/UoW.
Любая ошибка до commit откатывает всю публикацию, не оставляя PUBLISHED/current.

Здесь (РЕАЛЬНАЯ модель ``PluginArtifactRow``):

* Atomic publish/promote inserts PluginArtifactLifecycleRow in the same SQL
  transaction as artifact/current. The legacy process-local lifecycle_log is
  retained only for old append_lifecycle callers and is not a publication fact.

Маппинг ``PluginArtifactRow`` ↔ ``PluginArtifactRecord``
-------------------------------------------------------
* ``digest`` ↔ ``oci_digest``; ``image_ref`` есть в A10 (аддитивно);
* ``manifest`` — исходный подписанный plugin-manifest; ``app_version_projection``
  — legacy-обёртка из ``build_app_version_projection`` (CHECK в БД требует
  ``manifest <> app_version_projection``);
* ``manifest_fingerprint`` НЕ хранится — вычисляется ``manifest_fingerprint()``
  задачи 07 при чтении (детерминированно);
* ``status`` A10 = ``published|quarantined|revoked`` ↔ ``AppLifecycleStatus``
  задачи 07 = ``draft|published|disabled|revoked`` — явный маппинг ниже;
* ``quarantine_reason`` (короткий код) вместо JSONB ``quarantine``;
* ``outcome`` не хранится — восстанавливается из ``status``.

Порт ``RegistryStorePort`` СИНХРОННЫЙ, ``AsyncSession`` — асинхронный. Класс
держит собственный фоновый event loop и мостит sync-методы через
``asyncio.run_coroutine_threadsafe`` (честная БД-реализация, без «отложенной
записи»). Async-вызывающие могут использовать :meth:`*_async` напрямую.
"""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import threading
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, Sequence

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from prodavan.application.dynamic_app_registry.contracts import (
    QuarantineEvidence,
    RegistryOutcome,
    RegistryReason,
    RollbackCommand,
    RollbackConflictError,
    rollback_command_fingerprint,
)
from prodavan.application.plugins.artifacts.registry import PluginArtifactRecord
from prodavan.application.plugins.artifacts.verification import VerificationEvidence
from prodavan.domain.dynamic_apps import AppLifecycleStatus
from prodavan.infrastructure.persistence.models.artifact_lifecycle import (
    PluginArtifactLifecycleRow,
)

from ._schema import (
    ARTIFACT_COLUMNS,
    ARTIFACT_ROW,
    ARTIFACT_STATUS_PUBLISHED,
    ARTIFACT_STATUS_QUARANTINED,
    ARTIFACT_STATUS_REVOKED,
    ColumnMap,
    PluginRepositoryError,
    PluginRepositorySchemaError,
    PluginRepositoryUnavailable,
    aware_utc,
    column_names,
    json_mapping,
    load_row,
    text,
)

#: Кандидаты колонки-указателя текущей версии.
CURRENT_COLUMN_CANDIDATES: tuple[str, ...] = ("is_current", "current", "current_version")
#: Кандидаты CAS-колонки (целочисленной).
VERSION_COLUMN_CANDIDATES: tuple[str, ...] = (
    "lifecycle_version", "revision", "version_number",
)


class ConcurrentPublishError(PluginRepositoryError, RollbackConflictError):
    """Параллельная публикация: CAS не подтверждён, вторая запись отклонена."""


def _find_col(row: type, candidates: Sequence[str], *, integer_only: bool = False) -> str | None:
    table = getattr(row, "__table__", None)
    if table is None:
        return None
    for name in candidates:
        column = table.columns.get(name)
        if column is None:
            continue
        if integer_only:
            try:
                if column.type.python_type is not int:
                    continue
            except NotImplementedError:
                continue
        return name
    return None


def _status_to_domain(value: str) -> AppLifecycleStatus:
    """A10 status → доменный ``AppLifecycleStatus`` (fail-closed)."""
    mapping = {
        ARTIFACT_STATUS_PUBLISHED: AppLifecycleStatus.PUBLISHED,
        ARTIFACT_STATUS_QUARANTINED: AppLifecycleStatus.DISABLED,
        ARTIFACT_STATUS_REVOKED: AppLifecycleStatus.REVOKED,
    }
    try:
        return mapping[value]
    except KeyError as exc:
        raise PluginRepositorySchemaError(f"неизвестный status артефакта {value!r}") from exc


def _status_to_db(status: AppLifecycleStatus) -> str:
    mapping = {
        AppLifecycleStatus.PUBLISHED: ARTIFACT_STATUS_PUBLISHED,
        AppLifecycleStatus.DISABLED: ARTIFACT_STATUS_QUARANTINED,
        AppLifecycleStatus.DRAFT: ARTIFACT_STATUS_QUARANTINED,
        AppLifecycleStatus.REVOKED: ARTIFACT_STATUS_REVOKED,
    }
    try:
        return mapping[status]
    except KeyError as exc:
        raise PluginRepositorySchemaError(f"неизвестный AppLifecycleStatus {status!r}") from exc


def _fingerprint(manifest: Any) -> str:
    """Детерминированный fingerprint задачи 07 (голый hex, БЕЗ ``sha256:``).

    ``registry.PluginArtifactRegistry.publish`` сравнивает
    ``by_digest.manifest_fingerprint`` с ``manifest_fingerprint(manifest)``;
    формат ДОЛЖЕН совпадать, иначе повторная публикация того же digest
    ложно даст ``ARTIFACT_DIGEST_CONFLICT``.
    """
    from prodavan.application.plugins.artifacts.verification import manifest_fingerprint

    try:
        return manifest_fingerprint(manifest)
    except Exception:  # noqa: BLE001 — fallback без external-зависимости
        encoded = json.dumps(manifest, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def _artifact_id_for(record: PluginArtifactRecord) -> str:
    """Stable identity for a not-yet-inserted artifact and its logical retries."""
    identity = f"{record.plugin_id}\x00{record.version}\x00{record.digest}".encode("utf-8")
    return "part_" + hashlib.sha256(identity).hexdigest()


def _rollback_operation_token(
    artifact_id: str, source_version: str, source_generation: int
) -> str:
    """Stable token for one source generation to target-artifact rollback."""
    identity = f"{artifact_id}|{source_version}|{source_generation}".encode("utf-8")
    return "rollback:" + hashlib.sha256(identity).hexdigest()


_VERIFICATION_KEY = "verification_evidence"


def _validated_publish_evidence(
    record: PluginArtifactRecord, evidence: VerificationEvidence | None
) -> VerificationEvidence | None:
    """Bind a published row to real verification evidence, never to a flag."""
    candidate = evidence if evidence is not None else record.verification
    if not record.published:
        return None
    if record.outcome is not RegistryOutcome.APPLIED or record.quarantine is not None:
        raise PluginRepositoryError("published artifact has inconsistent outcome/quarantine")
    if record.published_at is None:
        raise PluginRepositoryError("published artifact requires published_at evidence")
    if record.publication is None:
        raise PluginRepositoryError("published artifact requires PublicationMetadata")
    if not isinstance(candidate, VerificationEvidence):
        raise PluginRepositoryError("published artifact requires VerificationEvidence")
    if not candidate.verified or candidate.outcome is not RegistryOutcome.APPLIED:
        raise PluginRepositoryError("artifact evidence is not verified")
    if not candidate.identity.strip() or not candidate.issuer.strip():
        raise PluginRepositoryError("artifact evidence requires real identity and issuer")
    if candidate.artifact_digest != record.digest or candidate.image_ref != record.image_ref:
        raise PluginRepositoryError("artifact evidence does not match digest/image_ref")
    if candidate.manifest_fingerprint != _fingerprint(record.manifest):
        raise PluginRepositoryError("artifact evidence does not match signed manifest")
    if not all((
        candidate.sbom_verified,
        candidate.provenance_verified,
        candidate.scan_verified,
        candidate.csp_verified,
    )):
        raise PluginRepositoryError("artifact evidence misses SBOM/provenance/scan/CSP verification")
    observed = aware_utc(candidate.observed_at)
    if observed is None:
        raise PluginRepositoryError("artifact evidence requires aware observed_at")
    return candidate


def _evidence_projection(evidence: VerificationEvidence | None) -> dict[str, Any]:
    if evidence is None:
        return {}
    return {
        _VERIFICATION_KEY: {
            "image_ref": evidence.image_ref,
            "artifact_digest": evidence.artifact_digest,
            "manifest_fingerprint": evidence.manifest_fingerprint,
            "identity": evidence.identity,
            "issuer": evidence.issuer,
            "sbom_verified": evidence.sbom_verified,
            "provenance_verified": evidence.provenance_verified,
            "scan_verified": evidence.scan_verified,
            "csp_verified": evidence.csp_verified,
            "observed_at": evidence.observed_at.astimezone(UTC).isoformat(),
            "granted_capabilities": list(evidence.granted_capabilities),
        }
    }


def _evidence_from_projection(projection: Any) -> VerificationEvidence | None:
    if not isinstance(projection, dict):
        return None
    if _VERIFICATION_KEY in projection:
        raw = projection[_VERIFICATION_KEY]
    elif "artifact_digest" in projection and "manifest_fingerprint" in projection:
        raw = projection
    else:
        return None
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PluginRepositorySchemaError("verification_evidence must be an object")
    if not raw:
        return None
    try:
        observed_raw = raw["observed_at"]
        observed = (
            observed_raw if isinstance(observed_raw, datetime)
            else datetime.fromisoformat(str(observed_raw))
        )
        return VerificationEvidence(
            image_ref=text(raw["image_ref"], field="evidence.image_ref"),
            artifact_digest=text(raw["artifact_digest"], field="evidence.artifact_digest"),
            manifest_fingerprint=text(
                raw["manifest_fingerprint"], field="evidence.manifest_fingerprint"
            ),
            identity=text(raw["identity"], field="evidence.identity"),
            issuer=text(raw["issuer"], field="evidence.issuer"),
            sbom_verified=raw["sbom_verified"] is True,
            provenance_verified=raw["provenance_verified"] is True,
            scan_verified=raw["scan_verified"] is True,
            csp_verified=raw["csp_verified"] is True,
            observed_at=aware_utc(observed),
            outcome=RegistryOutcome.APPLIED,
            quarantine=None,
            # A process-local verifier seal cannot and must not be persisted.
            seal=None,
            granted_capabilities=tuple(str(item) for item in raw.get("granted_capabilities", ())),
        )
    except (KeyError, TypeError, ValueError, PluginRepositoryError) as exc:
        raise PluginRepositorySchemaError("invalid persisted verification evidence") from exc


class SqlArtifactRegistryStore:
    """Настоящий ``RegistryStorePort`` с транзакцией и CAS-блокировкой."""

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
        session: AsyncSession | None = None,
        columns: ColumnMap | None = None,
        current_column: str | None = None,
        lock_column: str | None = None,
        clock: Any = None,
    ) -> None:
        if session_factory is None and session is None:
            raise PluginRepositoryError(
                "SqlArtifactRegistryStore требует session_factory или session (fail-closed)"
            )
        self._session_factory = session_factory
        self._session = session
        self._columns = columns or ColumnMap()
        self._explicit_current = current_column
        self._explicit_lock = lock_column
        self._clock = clock or (lambda: datetime.now(UTC))
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._lifecycle: list[tuple[str, str, Any]] = []
        self._quarantine: list[QuarantineEvidence] = []

    # ------------------------------------------------------------------
    # Мост sync → async
    # ------------------------------------------------------------------

    def _get_loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None and not self._loop.is_closed():
                return self._loop
            loop = asyncio.new_event_loop()
            thread = threading.Thread(
                target=_run_loop, args=(loop,), name="plugin-artifact-store", daemon=True
            )
            thread.start()
            self._loop = loop
            self._loop_thread = thread
            return loop

    def _run(self, coro: Any) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            future = asyncio.run_coroutine_threadsafe(coro, self._get_loop())
            return future.result()
        raise PluginRepositoryError(
            "SqlArtifactRegistryStore: sync-метод вызван из работающего event loop; "
            "используйте *_async-варианты"
        )

    def close(self) -> None:
        with self._lock:
            loop, thread = self._loop, self._loop_thread
            self._loop = None
            self._loop_thread = None
        if loop is not None and not loop.is_closed():
            loop.call_soon_threadsafe(loop.stop)
        if thread is not None:
            thread.join(timeout=2.0)

    # ------------------------------------------------------------------
    # Модель / колонки / сессия
    # ------------------------------------------------------------------

    def _row(self) -> type:
        row = load_row(ARTIFACT_ROW)
        self._columns.require(row, *ARTIFACT_COLUMNS)
        return row

    def _col(self, name: str) -> str:
        return self._columns.col(name)

    def _current_col(self, row: type) -> str:
        if self._explicit_current:
            return self._explicit_current
        found = _find_col(row, CURRENT_COLUMN_CANDIDATES)
        if found is None:
            raise PluginRepositorySchemaError(
                f"{row.__name__}: нет колонки is_current {list(CURRENT_COLUMN_CANDIDATES)}; "
                f"без CAS/partial unique публикация небезопасна. Добавьте 'is_current' в A10 "
                f"или передайте current_column=."
            )
        return found

    def _lock_col(self, row: type) -> str | None:
        if self._explicit_lock:
            return self._explicit_lock
        return _find_col(row, VERSION_COLUMN_CANDIDATES, integer_only=True)

    def _now(self) -> datetime:
        value = self._clock()
        if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
            raise PluginRepositoryError("часы реестра обязаны возвращать aware datetime")
        return value.astimezone(UTC)

    def _session_ctx(self):
        if self._session is not None:
            return _BorrowedSession(self._session)
        assert self._session_factory is not None
        return self._session_factory()

    async def _rollback_atomic(self, session: AsyncSession | None) -> None:
        """Rollback even a borrowed session after a failed atomic operation."""
        if session is None:
            return
        try:
            await session.rollback()
        except Exception:
            # Preserve the original typed publication failure. A failed
            # rollback is still fail-closed for the caller and is surfaced by
            # the driver on the next use of the session.
            return

    # ------------------------------------------------------------------
    # ORM ↔ PluginArtifactRecord
    # ------------------------------------------------------------------

    def _to_record(self, row_obj: Any) -> PluginArtifactRecord:
        row = type(row_obj)
        c = self._col
        get = lambda name: getattr(row_obj, c(name), None)  # noqa: E731
        status = _status_to_domain(str(get("status") or ""))
        outcome = (
            RegistryOutcome.APPLIED
            if status is AppLifecycleStatus.PUBLISHED
            else RegistryOutcome.QUARANTINED
        )
        created = aware_utc(get("created_at"))
        if not isinstance(created, datetime):
            raise PluginRepositorySchemaError("created_at артефакта отсутствует")
        published = aware_utc(get("published_at"))
        manifest = json_mapping(get("manifest"), field="manifest")
        image_ref = str(get("image_ref") or "") if self._columns.has(row, "image_ref") else ""
        verification = _evidence_from_projection(get("verification_evidence"))
        app_version_projection = get("app_version_projection")
        if (
            verification is None
            and isinstance(app_version_projection, dict)
            and _VERIFICATION_KEY in app_version_projection
        ):
            verification = _evidence_from_projection(app_version_projection)
        publication_values = {
            name: get(name)
            for name in ("source_id", "commit_sha", "backend_digest", "backend_image_ref", "run_url")
        }
        publication = None
        if all(isinstance(value, str) and value.strip() for value in publication_values.values()):
            from prodavan.application.plugins.artifacts.registry import PublicationMetadata

            publication = PublicationMetadata(**publication_values)
        quarantine = None
        reason = get("quarantine_reason")
        if reason and self._columns.has(row, "quarantine_reason"):
            quarantine = QuarantineEvidence(
                reason=RegistryReason.INVALID_TRANSITION,
                source_kind="plugin_artifact_registry",
                source_id=str(get("digest")),
                source_digest=str(get("digest")),
                observed_at=published if isinstance(published, datetime) else created,
                metadata={"detail": str(reason)[:200]},
            )
        try:
            return PluginArtifactRecord(
                plugin_id=text(get("plugin_id"), field="plugin_id"),
                version=text(get("version"), field="version"),
                digest=text(get("digest"), field="digest"),
                image_ref=image_ref,
                manifest=manifest,
                manifest_fingerprint=_fingerprint(manifest),
                status=status,
                outcome=outcome,
                quarantine=quarantine,
                published_at=published if isinstance(published, datetime) else None,
                created_at=created,
                verification=verification,
                publication=publication,
                lifecycle_version=(
                    get("lifecycle_version")
                    if isinstance(get("lifecycle_version"), int)
                    and not isinstance(get("lifecycle_version"), bool)
                    else None
                ),
            )
        except (ValueError, PluginRepositoryError) as exc:
            raise PluginRepositorySchemaError(f"строка артефакта невалидна: {exc}") from exc

    def _values(
        self,
        record: PluginArtifactRecord,
        row: type,
        *,
        evidence: VerificationEvidence | None = None,
    ) -> dict[str, Any]:
        """Доменный контракт → РЕАЛЬНЫЕ колонки A10."""
        c = self._col
        manifest = dict(record.manifest)
        # CHECK ``manifest <> app_version_projection``: проекция ВСЕГДА отлична.
        from prodavan.infrastructure.persistence.models.plugin_platform import (
            build_app_version_projection,
        )

        verified = _validated_publish_evidence(record, evidence)
        publication = record.publication
        projection = build_app_version_projection(manifest)
        projection.update(_evidence_projection(verified))

        values: dict[str, Any] = {
            c("plugin_id"): record.plugin_id,
            c("version"): record.version,
            c("digest"): record.digest,
            c("manifest"): manifest,
            c("app_version_projection"): projection,
            c("status"): _status_to_db(record.status),
            c("created_at"): record.created_at,
            c("published_at"): record.published_at,
            c("image_ref"): record.image_ref or None,
            c("cosign_verified"): verified is not None,
            c("source_id"): publication.source_id if publication is not None else None,
            c("commit_sha"): publication.commit_sha if publication is not None else None,
            c("backend_digest"): publication.backend_digest if publication is not None else None,
            c("backend_image_ref"): publication.backend_image_ref if publication is not None else None,
            c("signer_identity"): verified.identity if verified is not None else None,
            c("issuer"): verified.issuer if verified is not None else None,
            c("run_url"): publication.run_url if publication is not None else None,
            c("verification_evidence"): (
                _evidence_projection(verified).get(_VERIFICATION_KEY)
                if verified is not None else {}
            ),
            c("lifecycle_version"): 0,
            c("quarantine_reason"): (
                record.quarantine.reason.value if record.quarantine is not None else None
            ),
            c("is_current"): False,
        }
        existing = column_names(row)
        return {key: value for key, value in values.items() if key in existing}

    # ------------------------------------------------------------------
    # RegistryStorePort (sync)
    # ------------------------------------------------------------------

    def add(
        self, record: PluginArtifactRecord, *, evidence: VerificationEvidence | None = None
    ) -> None:
        self._run(self.add_async(record, evidence=evidence))

    def replace(self, record: PluginArtifactRecord) -> None:
        self._run(self.replace_async(record))

    def rollback(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord:
        return self._run(
            self.rollback_async(
                target=target, command=command, actor=actor, detail=detail
            )
        )

    def by_digest(self, digest: str) -> PluginArtifactRecord | None:
        return self._run(self.by_digest_async(digest))

    def versions(self, plugin_id: str) -> Sequence[PluginArtifactRecord]:
        return self._run(self.versions_async(plugin_id))

    def get(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        return self._run(self.get_async(plugin_id, version))

    def current(self, plugin_id: str) -> PluginArtifactRecord | None:
        return self._run(self.current_async(plugin_id))

    def set_current(self, plugin_id: str, version: str) -> None:
        self._run(self.set_current_async(plugin_id, version))

    def append_quarantine(self, evidence: QuarantineEvidence) -> None:
        self._run(self.append_quarantine_async(evidence))

    def append_lifecycle(self, *, command: RollbackCommand | None, actor: str, detail: str) -> None:
        self._run(self.append_lifecycle_async(command=command, actor=actor, detail=detail))

    # ------------------------------------------------------------------
    # Авторитетные async-методы
    # ------------------------------------------------------------------

    async def add_async(
        self, record: PluginArtifactRecord, *, evidence: VerificationEvidence | None = None
    ) -> None:
        """Вставка с уникальностью: гонка публикации → ``ConcurrentPublishError``."""
        if not isinstance(record, PluginArtifactRecord):
            raise PluginRepositoryError("add ожидает PluginArtifactRecord")
        text(record.plugin_id, field="plugin_id")
        text(record.version, field="version")
        text(record.digest, field="digest")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                existing = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("plugin_id")] == record.plugin_id,
                            table.columns[self._col("version")] == record.version,
                        )
                    )
                ).scalars().first()
                if existing is not None:
                    raise ConcurrentPublishError(
                        f"артефакт {record.plugin_id}@{record.version} уже существует"
                    )
                session.add(row(**self._values(record, row, evidence=evidence)))
                await _commit_if_owned(session, self._session is None)
        except ConcurrentPublishError:
            raise
        except IntegrityError as exc:
            raise ConcurrentPublishError(
                f"гонка публикации {record.plugin_id}@{record.version}: "
                f"нарушена уникальность (plugin_id,version) или oci_digest"
            ) from exc
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"add artifact failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — позитивная мутация = явный deny
            raise PluginRepositoryUnavailable(
                f"add artifact failed: {type(exc).__name__}"
            ) from exc

    async def publish_async(
        self,
        record: PluginArtifactRecord,
        *,
        evidence: VerificationEvidence | None = None,
        actor: str = "system",
        detail: str | None = None,
        expected_version: str | None = None,
    ) -> PluginArtifactRecord:
        """Insert, pin and journal one publication in a single UoW.

        The legacy add_async/set_current_async methods remain available for old
        callers. New publication code uses this method so no legacy method can
        commit a partial publication.
        """
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
        """Update, pin and journal a verified promotion in one UoW."""
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
        if not isinstance(record, PluginArtifactRecord):
            raise PluginRepositoryError("publish expects PluginArtifactRecord")
        text(record.plugin_id, field="plugin_id")
        text(record.version, field="version")
        text(record.digest, field="digest")
        text(actor, field="actor")
        operation = "promote" if promote else "publish"
        operation_detail = detail or f"{operation} {record.image_ref}"
        text(operation_detail, field="detail")
        session: AsyncSession | None = None
        try:
            row = self._row()
            table = row.__table__
            plugin_col = table.columns[self._col("plugin_id")]
            version_col = table.columns[self._col("version")]
            digest_col = table.columns[self._col("digest")]
            async with self._session_ctx() as session:
                # Both reads and every write below use this exact session. Do
                # not call get_async/current_async here: those public helpers
                # open their own session for factory-backed stores.
                by_digest_row = (
                    await session.execute(
                        select(row).where(digest_col == record.digest).with_for_update()
                    )
                ).scalars().first()
                by_version_row = (
                    await session.execute(
                        select(row)
                        .where(plugin_col == record.plugin_id, version_col == record.version)
                        .with_for_update()
                    )
                ).scalars().first()
                by_digest = self._to_record(by_digest_row) if by_digest_row is not None else None
                by_version = self._to_record(by_version_row) if by_version_row is not None else None
                if (
                    by_digest is not None
                    and by_version is not None
                    and (by_digest.plugin_id, by_digest.version)
                    != (by_version.plugin_id, by_version.version)
                ):
                    raise ConcurrentPublishError(
                        "artifact digest and plugin/version identify different rows"
                    )
                existing = by_digest or by_version
                if existing is not None:
                    if (
                        existing.plugin_id != record.plugin_id
                        or existing.version != record.version
                        or existing.digest != record.digest
                        or existing.manifest_fingerprint != record.manifest_fingerprint
                    ):
                        raise ConcurrentPublishError(
                            "artifact digest/version conflict: immutable manifest differs"
                        )
                    if existing.status is AppLifecycleStatus.REVOKED:
                        raise ConcurrentPublishError(
                            "revoked artifact cannot be promoted or reactivated"
                        )

                candidate_row = by_digest_row or by_version_row
                artifact_id = getattr(candidate_row, "id", None) or _artifact_id_for(record)
                text(artifact_id, field="artifact_id")
                operation_token = f"{operation}:{artifact_id}"
                replayed = await self._lifecycle_replay_in_session(
                    session,
                    artifact_id=artifact_id,
                    operation=operation,
                    actor=actor,
                    detail=operation_detail,
                    operation_token=operation_token,
                )
                if replayed:
                    # The existing durable row, including occurred_at, is
                    # authoritative. Never replace it or mint a retry token.
                    if existing is None or not existing.published:
                        raise ConcurrentPublishError(
                            "durable lifecycle replay has no published artifact"
                        )
                    return existing
                if existing is not None and existing.published:
                    # Legacy published rows may predate the lifecycle table;
                    # their exact replay remains a no-op and cannot reactivate them.
                    return existing
                if existing is None and promote:
                    raise ConcurrentPublishError(
                        f"promote target {record.plugin_id}@{record.version} does not exist"
                    )

                async with _nested_transaction(session):
                    if existing is None:
                        candidate = row(**self._values(record, row, evidence=evidence))
                        candidate.id = artifact_id
                        session.add(candidate)
                        await _flush_if_supported(session)
                    else:
                        values = self._values(record, row, evidence=evidence)
                        values.pop(self._col("plugin_id"), None)
                        values.pop(self._col("version"), None)
                        values.pop(self._col("is_current"), None)
                        values.pop(self._col("lifecycle_version"), None)
                        result = await session.execute(
                            update(row)
                            .where(plugin_col == record.plugin_id, version_col == record.version)
                            .values(**values)
                        )
                        if result.rowcount != 1:
                            raise ConcurrentPublishError(
                                f"publication update was not applied for {record.plugin_id}@{record.version}"
                            )
                        candidate = candidate_row
                        if not getattr(candidate, "id", None):
                            candidate.id = artifact_id

                    # The candidate row is already locked by SELECT FOR UPDATE (or
                    # by INSERT). The override is needed after updating a draft:
                    # its ORM attributes still describe the pre-update state.
                    await self._set_current_in_session(
                        session,
                        row=row,
                        plugin_id=record.plugin_id,
                        version=record.version,
                        expected_version=expected_version,
                        candidate=candidate,
                        record_override=record,
                    )
                    await self._append_lifecycle_in_session(
                        session,
                        artifact_id=artifact_id,
                        operation=operation,
                        actor=actor,
                        detail=operation_detail,
                        operation_token=operation_token,
                    )
                await _commit_if_owned(session, self._session is None)
                return record
        except ConcurrentPublishError:
            await self._rollback_atomic(session)
            raise
        except IntegrityError as exc:
            await self._rollback_atomic(session)
            replay = await self._recover_publication_replay(
                record=record,
                actor=actor,
                detail=operation_detail,
                promote=promote,
            )
            if replay is not None:
                return replay
            raise ConcurrentPublishError(
                f"publication race for {record.plugin_id}@{record.version}: "
                "unique digest/version/current constraint violated"
            ) from exc
        except PluginRepositoryError:
            await self._rollback_atomic(session)
            raise
        except SQLAlchemyError as exc:
            await self._rollback_atomic(session)
            raise PluginRepositoryUnavailable(f"atomic publication failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — no partial PUBLISHED/current state
            await self._rollback_atomic(session)
            raise PluginRepositoryUnavailable(
                f"atomic publication failed: {type(exc).__name__}"
            ) from exc

    async def _recover_publication_replay(
        self,
        *,
        record: PluginArtifactRecord,
        actor: str,
        detail: str,
        promote: bool,
    ) -> PluginArtifactRecord | None:
        """Re-read a possible unique-race winner in a fresh transaction.

        No write is retried here. Success is possible only when the complete
        artifact/current/lifecycle state is an exact semantic replay; every
        mismatch returns None (or a typed lifecycle conflict) for the caller's
        original ConcurrentPublishError.
        """
        operation = "promote" if promote else "publish"
        try:
            row = self._row()
            table = row.__table__
            plugin_col = table.columns[self._col("plugin_id")]
            version_col = table.columns[self._col("version")]
            digest_col = table.columns[self._col("digest")]
            current_col = self._current_col(row)
            async with self._session_ctx() as session:
                by_digest_row = (
                    await session.execute(
                        select(row).where(digest_col == record.digest).with_for_update()
                    )
                ).scalars().first()
                by_version_row = (
                    await session.execute(
                        select(row)
                        .where(plugin_col == record.plugin_id, version_col == record.version)
                        .with_for_update()
                    )
                ).scalars().first()
                by_digest = self._to_record(by_digest_row) if by_digest_row is not None else None
                by_version = self._to_record(by_version_row) if by_version_row is not None else None
                if by_digest is None or by_version is None:
                    return None
                if (
                    (by_digest.plugin_id, by_digest.version)
                    != (by_version.plugin_id, by_version.version)
                ):
                    return None
                existing = by_digest
                if (
                    existing.plugin_id != record.plugin_id
                    or existing.version != record.version
                    or existing.digest != record.digest
                    or existing.manifest_fingerprint != record.manifest_fingerprint
                    or existing.image_ref != record.image_ref
                    or existing.publication != record.publication
                    or not existing.published
                    or existing.status is AppLifecycleStatus.REVOKED
                ):
                    return None
                _validated_publish_evidence(existing, existing.verification)
                incoming_evidence = record.verification
                persisted_evidence = existing.verification
                if incoming_evidence is None or persisted_evidence is None:
                    return None
                if any(
                    getattr(incoming_evidence, name) != getattr(persisted_evidence, name)
                    for name in (
                        "image_ref",
                        "artifact_digest",
                        "manifest_fingerprint",
                        "identity",
                        "issuer",
                        "sbom_verified",
                        "provenance_verified",
                        "scan_verified",
                        "csp_verified",
                        # observed_at is an exact replay field: a new verification
                        # timestamp is not silently accepted, and the authoritative
                        # stored timestamp remains untouched in the returned row.
                        "observed_at",
                    )
                ):
                    return None
                # Capability order is not semantic; duplicates/order cannot turn
                # an exact replay into a conflict, but a different set must fail.
                if frozenset(incoming_evidence.granted_capabilities) != frozenset(
                    persisted_evidence.granted_capabilities
                ):
                    return None

                artifact_id = getattr(by_digest_row, "id", None)
                if not isinstance(artifact_id, str) or not artifact_id:
                    return None
                lifecycle = await self._lifecycle_replay_in_session(
                    session,
                    artifact_id=artifact_id,
                    operation=operation,
                    actor=actor,
                    detail=detail,
                    operation_token=f"{operation}:{artifact_id}",
                )
                if lifecycle is None or aware_utc(lifecycle.occurred_at) is None:
                    return None
                current_row = (
                    await session.execute(
                        select(row)
                        .where(plugin_col == record.plugin_id, table.columns[current_col].is_(True))
                        .with_for_update()
                    )
                ).scalars().first()
                current = self._to_record(current_row) if current_row is not None else None
                if (
                    current is None
                    or current.plugin_id != record.plugin_id
                    or current.version != record.version
                    or current.digest != record.digest
                ):
                    return None
                return existing
        except ConcurrentPublishError:
            raise
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"publication replay recovery failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — recovery must never imply success
            raise PluginRepositoryUnavailable(
                f"publication replay recovery failed: {type(exc).__name__}"
            ) from exc

    async def replace_async(self, record: PluginArtifactRecord) -> None:
        """Обновить запись (quarantine/rollback/promote) по (plugin_id, version)."""
        try:
            row = self._row()
            table = row.__table__
            values = self._values(record, row)
            values.pop(self._col("plugin_id"), None)
            values.pop(self._col("version"), None)
            values.pop(self._col("is_current"), None)  # указатель меняет только set_current
            values.pop(self._col("lifecycle_version"), None)  # CAS-счётчик не сбрасываем
            async with self._session_ctx() as session:
                result = await session.execute(
                    update(row)
                    .where(table.columns[self._col("plugin_id")] == record.plugin_id)
                    .where(table.columns[self._col("version")] == record.version)
                    .values(**values)
                )
                if result.rowcount != 1:
                    raise PluginRepositoryUnavailable(
                        f"replace: нет записи {record.plugin_id}@{record.version}"
                    )
                await _commit_if_owned(session, self._session is None)
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"replace artifact failed: {exc}") from exc

    async def rollback_async(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord:
        """Atomically move current and append a durable rollback fact."""
        if not isinstance(target, PluginArtifactRecord):
            raise PluginRepositoryError("rollback expects PluginArtifactRecord")
        if not isinstance(command, RollbackCommand):
            raise PluginRepositoryError("rollback expects RollbackCommand")
        text(actor, field="actor")
        text(detail, field="detail")
        if (
            command.definition_id != target.plugin_id
            or command.target_version_id != target.version
            or command.expected_artifact_digest != target.digest
        ):
            raise PluginRepositoryError("rollback command does not match target artifact")
        if actor != command.actor:
            raise ConcurrentPublishError("rollback command actor binding conflict")
        session: AsyncSession | None = None
        try:
            row = self._row()
            table = row.__table__
            plugin_col = table.columns[self._col("plugin_id")]
            version_col = table.columns[self._col("version")]
            digest_col = table.columns[self._col("digest")]
            current_col = self._current_col(row)
            lock_col = self._lock_col(row)
            if lock_col is None:
                raise ConcurrentPublishError("rollback lifecycle CAS column is missing")
            async with self._session_ctx() as session:
                candidate = (
                    await session.execute(
                        select(row)
                        .where(
                            plugin_col == target.plugin_id,
                            version_col == target.version,
                            digest_col == target.digest,
                        )
                        .with_for_update()
                    )
                ).scalars().first()
                if candidate is None:
                    raise PluginRepositoryUnavailable("rollback target artifact is missing")
                persisted = self._to_record(candidate)
                if (
                    not persisted.published
                    or persisted.outcome is not RegistryOutcome.APPLIED
                    or persisted.quarantine is not None
                ):
                    raise PluginRepositoryUnavailable("rollback target is not published")
                _validated_publish_evidence(persisted, persisted.verification)
                artifact_id = text(getattr(candidate, "id", None), field="artifact_id")
                current_row = (
                    await session.execute(
                        select(row)
                        .where(plugin_col == target.plugin_id, table.columns[current_col].is_(True))
                        .with_for_update()
                    )
                ).scalars().first()
                current = self._to_record(current_row) if current_row is not None else None
                if current is None:
                    raise PluginRepositoryUnavailable("rollback requires a current artifact")
                expected_source = command.expected_current_version_id
                expected_generation = command.expected_current_lifecycle_version

                if current.digest == target.digest:
                    if expected_source is None:
                        raise ConcurrentPublishError("rollback target is already current")
                    source_row = (
                        await session.execute(
                            select(row)
                            .where(plugin_col == target.plugin_id, version_col == expected_source)
                            .with_for_update()
                        )
                    ).scalars().first()
                    if source_row is None:
                        raise ConcurrentPublishError("rollback replay source is missing")
                    source_generation = getattr(source_row, lock_col, None)
                    if not isinstance(source_generation, int) or isinstance(source_generation, bool):
                        raise ConcurrentPublishError("rollback replay source generation is invalid")
                    if (
                        expected_generation is not None
                        and source_generation != expected_generation
                    ):
                        raise ConcurrentPublishError(
                            "rollback replay source generation conflict"
                        )
                    replay = await self._lifecycle_replay_in_session(
                        session,
                        artifact_id=artifact_id,
                        operation="rollback",
                        actor=actor,
                        detail=detail,
                        operation_token=_rollback_operation_token(
                            artifact_id, expected_source, source_generation
                        ),
                        command_fingerprint=rollback_command_fingerprint(
                            replace(
                                command,
                                expected_current_lifecycle_version=source_generation,
                            )
                        ),
                    )
                    if replay is not None:
                        return persisted
                    raise ConcurrentPublishError("rollback replay evidence is missing")

                source_version = current.version
                if expected_source is not None and source_version != expected_source:
                    raise ConcurrentPublishError("rollback current version conflict")
                source_generation = getattr(current_row, lock_col, None)
                if not isinstance(source_generation, int) or isinstance(source_generation, bool):
                    raise ConcurrentPublishError("rollback source generation is invalid")
                if (
                    expected_generation is not None
                    and source_generation != expected_generation
                ):
                    raise ConcurrentPublishError(
                        "rollback current lifecycle generation conflict"
                    )
                operation_token = _rollback_operation_token(
                    artifact_id, source_version, source_generation
                )
                command_fingerprint = rollback_command_fingerprint(
                    replace(
                        command,
                        expected_current_lifecycle_version=source_generation,
                    )
                )
                replay = await self._lifecycle_replay_in_session(
                    session,
                    artifact_id=artifact_id,
                    operation="rollback",
                    actor=actor,
                    detail=detail,
                    operation_token=operation_token,
                    command_fingerprint=command_fingerprint,
                )
                if replay is not None:
                    raise ConcurrentPublishError(
                        "rollback lifecycle replay does not point to its target"
                    )
                target_generation = getattr(candidate, lock_col, None)
                if not isinstance(target_generation, int) or isinstance(target_generation, bool):
                    raise ConcurrentPublishError("rollback target generation is invalid")

                async with _nested_transaction(session):
                    await self._set_current_in_session(
                        session,
                        row=row,
                        plugin_id=target.plugin_id,
                        version=target.version,
                        expected_version=source_version,
                        expected_current_lifecycle_version=source_generation,
                        candidate=candidate,
                        record_override=persisted,
                    )
                    await self._append_lifecycle_in_session(
                        session,
                        artifact_id=artifact_id,
                        operation="rollback",
                        actor=actor,
                        detail=detail,
                        operation_token=operation_token,
                        command_fingerprint=command_fingerprint,
                    )
                persisted = replace(persisted, lifecycle_version=target_generation + 1)
                await _commit_if_owned(session, self._session is None)
                return persisted
        except ConcurrentPublishError:
            await self._rollback_atomic(session)
            replay = await self._recover_rollback_replay(
                target=target, command=command, actor=actor, detail=detail
            )
            if replay is not None:
                return replay
            raise
        except IntegrityError as exc:
            await self._rollback_atomic(session)
            replay = await self._recover_rollback_replay(
                target=target, command=command, actor=actor, detail=detail
            )
            if replay is not None:
                return replay
            raise ConcurrentPublishError("rollback uniqueness/current conflict") from exc
        except PluginRepositoryError:
            await self._rollback_atomic(session)
            raise
        except SQLAlchemyError as exc:
            await self._rollback_atomic(session)
            raise PluginRepositoryUnavailable(f"atomic rollback failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — never leave a partial rollback
            await self._rollback_atomic(session)
            raise PluginRepositoryUnavailable(
                f"atomic rollback failed: {type(exc).__name__}"
            ) from exc

    async def _recover_rollback_replay(
        self,
        *,
        target: PluginArtifactRecord,
        command: RollbackCommand,
        actor: str,
        detail: str,
    ) -> PluginArtifactRecord | None:
        """Read-only exact replay check after a failed concurrent rollback."""
        expected_source = command.expected_current_version_id
        if expected_source is None:
            return None
        try:
            row = self._row()
            table = row.__table__
            plugin_col = table.columns[self._col("plugin_id")]
            version_col = table.columns[self._col("version")]
            digest_col = table.columns[self._col("digest")]
            current_col = self._current_col(row)
            lock_col = self._lock_col(row)
            if lock_col is None:
                return None
            async with self._session_ctx() as session:
                candidate = (
                    await session.execute(
                        select(row)
                        .where(
                            plugin_col == target.plugin_id,
                            version_col == target.version,
                            digest_col == target.digest,
                        )
                        .with_for_update()
                    )
                ).scalars().first()
                if candidate is None:
                    return None
                persisted = self._to_record(candidate)
                if (
                    not persisted.published
                    or persisted.outcome is not RegistryOutcome.APPLIED
                    or persisted.quarantine is not None
                ):
                    return None
                _validated_publish_evidence(persisted, persisted.verification)
                current_row = (
                    await session.execute(
                        select(row)
                        .where(plugin_col == target.plugin_id, table.columns[current_col].is_(True))
                        .with_for_update()
                    )
                ).scalars().first()
                current = self._to_record(current_row) if current_row is not None else None
                if current is None or current.digest != target.digest:
                    return None
                source_row = (
                    await session.execute(
                        select(row)
                        .where(plugin_col == target.plugin_id, version_col == expected_source)
                        .with_for_update()
                    )
                ).scalars().first()
                if source_row is None:
                    return None
                source_generation = getattr(source_row, lock_col, None)
                expected_generation = command.expected_current_lifecycle_version
                if (
                    not isinstance(source_generation, int)
                    or isinstance(source_generation, bool)
                    or (
                        expected_generation is not None
                        and source_generation != expected_generation
                    )
                ):
                    return None
                artifact_id = getattr(candidate, "id", None)
                if not isinstance(artifact_id, str) or not artifact_id:
                    return None
                replay = await self._lifecycle_replay_in_session(
                    session,
                    artifact_id=artifact_id,
                    operation="rollback",
                    actor=actor,
                    detail=detail,
                    operation_token=_rollback_operation_token(
                        artifact_id, expected_source, source_generation
                    ),
                    command_fingerprint=rollback_command_fingerprint(
                        replace(
                            command,
                            expected_current_lifecycle_version=source_generation,
                        )
                    ),
                )
                if replay is None or aware_utc(replay.occurred_at) is None:
                    return None
                return persisted
        except ConcurrentPublishError:
            raise
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"rollback replay recovery failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — never imply replay on weak evidence
            raise PluginRepositoryUnavailable(
                f"rollback replay recovery failed: {type(exc).__name__}"
            ) from exc

    async def by_digest_async(self, digest: str) -> PluginArtifactRecord | None:
        key = text(digest, field="digest")
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(table.columns[self._col("digest")] == key)
                    )
                ).scalars().first()
            return self._to_record(found) if found is not None else None
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"by_digest failed: {exc}") from exc

    async def versions_async(self, plugin_id: str) -> Sequence[PluginArtifactRecord]:
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row)
                        .where(table.columns[self._col("plugin_id")] == text(plugin_id, field="plugin_id"))
                        .order_by(table.columns[self._col("created_at")])
                    )
                ).scalars().all()
            return tuple(self._to_record(item) for item in found)
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"versions failed: {exc}") from exc

    async def get_async(self, plugin_id: str, version: str) -> PluginArtifactRecord | None:
        try:
            row = self._row()
            table = row.__table__
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("plugin_id")] == text(plugin_id, field="plugin_id"),
                            table.columns[self._col("version")] == text(version, field="version"),
                        )
                    )
                ).scalars().first()
            return self._to_record(found) if found is not None else None
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"get artifact failed: {exc}") from exc

    async def current_async(self, plugin_id: str) -> PluginArtifactRecord | None:
        """Текущая версия: строго ``is_current = true`` (не «первая попавшаяся»)."""
        try:
            row = self._row()
            table = row.__table__
            current_col = self._current_col(row)
            async with self._session_ctx() as session:
                found = (
                    await session.execute(
                        select(row).where(
                            table.columns[self._col("plugin_id")] == text(plugin_id, field="plugin_id"),
                            table.columns[current_col].is_(True),
                        )
                    )
                ).scalars().first()
            return self._to_record(found) if found is not None else None
        except PluginRepositoryError:
            raise
        except SQLAlchemyError as exc:
            raise PluginRepositoryUnavailable(f"current failed: {exc}") from exc

    async def set_current_async(
        self, plugin_id: str, version: str, *, expected_version: str | None = None
    ) -> None:
        """Legacy standalone CAS publication; new writes use publish_async."""
        text(plugin_id, field="plugin_id")
        text(version, field="version")
        session: AsyncSession | None = None
        try:
            row = self._row()
            async with self._session_ctx() as session:
                await self._set_current_in_session(
                    session,
                    row=row,
                    plugin_id=plugin_id,
                    version=version,
                    expected_version=expected_version,
                )
                await _commit_if_owned(session, self._session is None)
        except (ConcurrentPublishError, PluginRepositoryError):
            if session is not None:
                await _rollback_if_owned(session, self._session is None)
            raise
        except SQLAlchemyError as exc:
            if session is not None:
                await _rollback_if_owned(session, self._session is None)
            raise PluginRepositoryUnavailable(f"set_current failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001 — preserve fail-closed boundary
            if session is not None:
                await _rollback_if_owned(session, self._session is None)
            raise PluginRepositoryUnavailable(
                f"set_current failed: {type(exc).__name__}"
            ) from exc

    async def _set_current_in_session(
        self,
        session: AsyncSession,
        *,
        row: type,
        plugin_id: str,
        version: str,
        expected_version: str | None = None,
        expected_current_lifecycle_version: int | None = None,
        candidate: Any = None,
        record_override: PluginArtifactRecord | None = None,
    ) -> None:
        """Run candidate validation and current CAS without opening a session."""
        table = row.__table__
        current_col = self._current_col(row)
        lock_col = self._lock_col(row)
        plugin_col = table.columns[self._col("plugin_id")]
        version_col = table.columns[self._col("version")]
        if candidate is None:
            candidate = (
                await session.execute(
                    select(row)
                    .where(plugin_col == plugin_id, version_col == version)
                    .with_for_update()
                )
            ).scalars().first()
        if candidate is None:
            raise PluginRepositoryUnavailable(
                f"set_current: версия {plugin_id}@{version} не найдена"
            )
        record = record_override or self._to_record(candidate)
        if not record.published:
            raise PluginRepositoryUnavailable(
                f"set_current: {plugin_id}@{version} не PUBLISHED → отказ публикации"
            )
        if record_override is None and self._columns.has(row, "cosign_verified"):
            if getattr(candidate, self._col("cosign_verified"), False) is not True:
                raise PluginRepositoryUnavailable(
                    "set_current: cosign-подпись не подтверждена → QUARANTINED"
                )

        # cosign_verified is only a projection: persisted evidence must match
        # digest, image, manifest and all CI gates before current can change.
        _validated_publish_evidence(record, record.verification)

        if expected_version is not None or expected_current_lifecycle_version is not None:
            current_row = (
                await session.execute(
                    select(row)
                    .where(plugin_col == plugin_id, table.columns[current_col].is_(True))
                    .with_for_update()
                )
            ).scalars().first()
            current_record = self._to_record(current_row) if current_row is not None else None
            if expected_version is not None:
                if current_record is not None and current_record.version != expected_version:
                    raise ConcurrentPublishError(
                        f"гонка публикации: ожидался current={expected_version}, "
                        f"фактически {current_record.version}"
                    )
            if expected_current_lifecycle_version is not None:
                if lock_col is None or current_row is None:
                    raise ConcurrentPublishError("current lifecycle CAS row is missing")
                actual_generation = getattr(current_row, lock_col, None)
                if (
                    not isinstance(actual_generation, int)
                    or isinstance(actual_generation, bool)
                    or actual_generation != expected_current_lifecycle_version
                ):
                    raise ConcurrentPublishError(
                        "current lifecycle generation conflict: "
                        f"expected {expected_current_lifecycle_version}, "
                        f"actual {actual_generation}"
                    )

        # Clear the old pointer and set the target with a lifecycle CAS in the
        # same transaction as the artifact insert/update and lifecycle append.
        clear_stmt = (
            update(row)
            .where(plugin_col == plugin_id, table.columns[current_col].is_(True))
            .values(**{current_col: False})
        )
        if expected_current_lifecycle_version is not None:
            if lock_col is None:
                raise ConcurrentPublishError("current lifecycle CAS column is missing")
            clear_stmt = clear_stmt.where(
                table.columns[lock_col] == expected_current_lifecycle_version
            )
        cleared = await session.execute(clear_stmt)
        if expected_current_lifecycle_version is not None and cleared.rowcount != 1:
            raise ConcurrentPublishError(
                "current lifecycle generation changed before rollback CAS"
            )
        target_stmt = (
            update(row)
            .where(plugin_col == plugin_id, version_col == version)
            .values(**{current_col: True})
        )
        cas_value: int | None = None
        if lock_col is not None:
            raw = getattr(candidate, lock_col, None)
            cas_value = int(raw) if isinstance(raw, int) else None
        if lock_col is not None and cas_value is not None:
            target_stmt = target_stmt.where(
                table.columns[lock_col] == cas_value
            ).values(**{current_col: True, lock_col: cas_value + 1})
        result = await session.execute(target_stmt)
        if result.rowcount != 1:
            raise ConcurrentPublishError(
                f"CAS не подтверждён для {plugin_id}@{version}: "
                "параллельная публикация изменила lifecycle_version"
            )

    async def _lifecycle_replay_in_session(
        self,
        session: AsyncSession,
        *,
        artifact_id: str,
        operation: str,
        actor: str,
        detail: str,
        operation_token: str,
        command_fingerprint: str | None = None,
    ) -> PluginArtifactLifecycleRow | None:
        """Return the authoritative row only for an exact immutable replay."""
        found = (
            await session.execute(
                select(PluginArtifactLifecycleRow)
                .where(
                    PluginArtifactLifecycleRow.artifact_id == artifact_id,
                    PluginArtifactLifecycleRow.operation_token == operation_token,
                )
                .with_for_update()
            )
        ).scalars().first()
        if found is None:
            return None
        if (
            found.operation != operation
            or found.actor != actor
            or found.detail != detail
        ):
            raise ConcurrentPublishError(
                "artifact lifecycle semantic conflict: immutable operation differs"
            )
        if operation == "rollback":
            if (
                not isinstance(command_fingerprint, str)
                or len(command_fingerprint) != 64
                or getattr(found, "command_fingerprint", None) != command_fingerprint
            ):
                raise ConcurrentPublishError(
                    "rollback command semantic conflict: fingerprint mismatch"
                )
        # occurred_at and every other persisted lifecycle field stay untouched.
        return found

    async def _append_lifecycle_in_session(
        self,
        session: AsyncSession,
        *,
        artifact_id: str | None,
        operation: str,
        actor: str,
        detail: str,
        operation_token: str,
        command_fingerprint: str | None = None,
    ) -> None:
        """Insert the durable lifecycle fact in the caller's one UoW."""
        text(artifact_id, field="artifact_id")
        text(operation, field="operation")
        text(actor, field="actor")
        text(detail, field="detail")
        text(operation_token, field="operation_token")
        if operation not in {"publish", "promote", "rollback"}:
            raise PluginRepositoryError("unsupported artifact lifecycle operation")
        if operation == "rollback" and (
            not isinstance(command_fingerprint, str) or len(command_fingerprint) != 64
        ):
            raise PluginRepositoryError("rollback command fingerprint is required")
        if len(operation_token) > 128 or len(actor) > 160:
            raise PluginRepositoryError("lifecycle field exceeds its bounded column")
        session.add(
            PluginArtifactLifecycleRow(
                artifact_id=artifact_id,
                operation=operation,
                actor=actor,
                detail=detail,
                occurred_at=self._now(),
                operation_token=operation_token,
                command_fingerprint=command_fingerprint,
            )
        )
        # Flush before commit so FK/unique/driver failures take the same
        # rollback path as artifact/current failures.
        await _flush_if_supported(session)

    async def append_lifecycle_async(
        self, *, command: RollbackCommand | None, actor: str, detail: str
    ) -> None:
        """Legacy diagnostic log; atomic publication uses the durable row."""
        text(actor, field="actor")
        with self._lock:
            self._lifecycle.append((actor, detail, command))

    async def append_quarantine_async(self, evidence: QuarantineEvidence) -> None:
        if not isinstance(evidence, QuarantineEvidence):
            raise PluginRepositoryError("append_quarantine ожидает QuarantineEvidence")
        with self._lock:
            self._quarantine.append(evidence)

    @property
    def quarantine_log(self) -> tuple[QuarantineEvidence, ...]:
        with self._lock:
            return tuple(self._quarantine)

    @property
    def lifecycle_log(self) -> tuple[tuple[str, str], ...]:
        with self._lock:
            return tuple((actor, detail) for actor, detail, _ in self._lifecycle)


def _run_loop(loop: asyncio.AbstractEventLoop) -> None:
    asyncio.set_event_loop(loop)
    loop.run_forever()


class _BorrowedSession:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def __aenter__(self) -> AsyncSession:
        return self._session

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


class _NoopNestedTransaction:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *exc_info: Any) -> None:
        return None


def _nested_transaction(session: AsyncSession) -> Any:
    """Use SAVEPOINT on real sessions and a no-op on lightweight doubles."""
    begin_nested = getattr(session, "begin_nested", None)
    if callable(begin_nested):
        return begin_nested()
    return _NoopNestedTransaction()


async def _flush_if_supported(session: AsyncSession) -> None:
    """Flush INSERT before the subsequent CAS UPDATE on real AsyncSession."""
    flush = getattr(session, "flush", None)
    if callable(flush):
        result = flush()
        if inspect.isawaitable(result):
            await result


async def _commit_if_owned(session: AsyncSession, owned: bool) -> None:
    if owned:
        await session.commit()


async def _rollback_if_owned(session: AsyncSession, owned: bool) -> None:
    if owned:
        await session.rollback()


__all__ = [
    "CURRENT_COLUMN_CANDIDATES",
    "ConcurrentPublishError",
    "SqlArtifactRegistryStore",
    "VERSION_COLUMN_CANDIDATES",
]
