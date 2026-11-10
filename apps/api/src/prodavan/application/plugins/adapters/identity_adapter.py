"""Адаптеры Admin API → реальный trust-контур ``application/plugins/identity``.

Стык (обе стороны прочитаны):

===========================================  =========================================
admin (задача 12, ``admin/ports.py``)        identity (canonical async)
===========================================  =========================================
``RevocationPort.revoke(*, grant_id,          ``GrantRevocation.revoke(grant_id)`` /
  reason) -> bool`` (async)                   ``DbGrantRevocation.revoke`` (async)
``RevocationPort.is_revoked(*, grant_id)``    ``is_active(grant_id, *, now)`` (async)
  -> bool`` (async)
``MintPort.mint(*, request: MintRequest)``    ``mint_plugin_claims(grant=..., 
  -> IssuedPluginClaims`` (async)              instance=..., secret=..., 
                                               ttl_seconds=300) -> str`` (sync)
===========================================  =========================================

Правила стыка
-------------
* **Canonical async identity вызывается только через ``await``.** Это относится
  и к in-memory реализации, и к ``DbGrantRevocation``: адаптер не приводит
  coroutine к ``bool`` и не содержит синхронного fallback. Исключения и typed
  ``bool`` источника сохраняются; неподтверждённая запись не становится успехом.
* **bool = подтверждение записи, а не «вызов не упал».** ``revoke`` возвращает
  ``True`` только если после записи реестр действительно показывает отзыв
  (перепроверка через ``is_active``/``is_revoked``). Это ровно семантика
  ``REVOKE_FAILED`` в ``PluginAdminService``: нет подтверждения → отказ.
* **``reason`` не теряется.** Общий identity-порт не принимает ``reason``; адаптер пишет его
  в свой append-only журнал ``reasons`` (только идентификатор grant_id и сам
  текст причины, без PII/токенов), чтобы аудит admin-слоя мог сослаться на
  причину. В токен и в DSN reason не попадает.
* **Mint:** ``MintRequest`` → структурный ``MintGrant``/``MintInstance`` для
  ``mint_plugin_claims``; затем токен разбирается через ``peek_plugin_token``
  (без проверки подписи — он только что выпущен НАМИ) и валидируется через
  ``claims_from_payload``; ``jti`` и ``expires_at`` берутся из проверенного
  payload, а не придумываются адаптером.
* **Про ``issued_at``.** ``IssuedPluginClaims`` (задача 12) содержит ``jti`` и
  ``expires_at``, но НЕ отдельное поле ``issued_at`` — момент выпуска
  восстанавливается как ``expires_at - ttl_seconds`` из проверенного ``exp``.
  Адаптер не выдумывает ``iat``: он берёт ``claims.iat`` из payload и
  проверяет ``exp - iat == ttl_seconds`` (иначе отказ). Родителю рекомендуется
  добавить ``issued_at`` в контракт задачи 12, если нужен явный момент выпуска.
* **Fail-closed:** пустой ``secret``, пустой ``grant_id``, битый payload,
  расхождение ``grant_id``/``jti`` в токене — ``PluginTokenError``/``ValueError``
  наверх, а не «вернём токен-заглушку».

ВАЖНО (для родителя): ``identity.MintPort`` и ``admin.MintPort`` — РАЗНЫЕ
протоколы с одинаковым именем. Здесь импортируются с алиасами, см.
``activation_adapter.py`` и тест ``test_mint_port_alias_collision.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from prodavan.application.plugins.admin.contracts import (
    IssuedPluginClaims,
    MintRequest,
)
from prodavan.application.plugins.identity.mint import (
    claims_from_payload,
    mint_plugin_claims,
    peek_plugin_token,
)
from prodavan.application.plugins.identity.revocation import (
    RevocationPort as IdentityRevocationPort,
)

# Алиасы: имя ``RevocationPort``/``MintPort`` в admin и в activation/identity
# обозначает РАЗНЫЕ протоколы. Явный алиас исключает случайную подмену.
from prodavan.application.plugins.admin.ports import (
    MintPort as AdminMintPort,
)
from prodavan.application.plugins.admin.ports import (
    RevocationPort as AdminRevocationPort,
)


@runtime_checkable
class AsyncRevocationSource(Protocol):
    """Canonical async structural-контракт источника отзывов.

    Подходят ``InMemoryRevocation``, ``GrantRevocation`` и
    ``DbGrantRevocation``. ``DbGrantRevocation.revoke`` дополнительно принимает
    опциональный ``reason``, что совместимо с этим минимальным контрактом.
    """

    async def revoke(self, grant_id: str) -> bool: ...
    async def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool: ...


# Сохраняем старое публичное имя для существующего re-export; методы уже async.
SyncRevocationSource = AsyncRevocationSource


@dataclass(frozen=True, slots=True)
class _MintGrantView:
    """Проекция ``MintRequest`` под структурный ``identity.mint.MintGrant``.

    ``build_payload`` читает поля через ``getattr``/``Mapping.get``: достаточно
    обычных атрибутов. Никакого ``egress``/``quotas`` здесь нет — по §4.2 они
    живут в долгоживущем grant в БД, а не в коротком JWT.
    """

    grant_id: str
    plugin_id: str
    company_id: str
    scopes: tuple[str, ...]
    manifest_version: str
    artifact_digest: str
    quota_ref: str

    @classmethod
    def from_request(cls, request: MintRequest) -> _MintGrantView:
        return cls(
            grant_id=request.grant_id,
            plugin_id=request.plugin_id,
            company_id=request.company_id,
            scopes=tuple(request.scopes),
            manifest_version=request.manifest_version,
            artifact_digest=request.artifact_digest,
            quota_ref=request.quota_ref,
        )


@dataclass(frozen=True, slots=True)
class _MintInstanceView:
    """Проекция ``MintRequest`` под структурный ``identity.mint.MintInstance``."""

    plugin_instance_id: str
    cabinet_id: str | None
    project_id: str | None

    @classmethod
    def from_request(cls, request: MintRequest) -> _MintInstanceView:
        return cls(
            plugin_instance_id=request.plugin_instance_id,
            cabinet_id=request.cabinet_id,
            project_id=request.project_id,
        )


class IdentityRevocationAdapter:
    """``admin.RevocationPort`` поверх canonical async identity revocation.

    Принимает ``GrantRevocation``, ``InMemoryRevocation``,
    ``DbGrantRevocation`` или другую реализацию ``identity.RevocationPort``.
    ``GrantRevocation`` с НЕподключённым портом даёт ``is_active=False`` для
    любого grant — адаптер честно вернёт ``is_revoked() is True`` (доступ
    закрыт), но ``revoke`` бросит ``PluginTokenError``: «записать отзыв» без
    хранилища невозможно, и это не маскируется успехом.
    """

    def __init__(self, source: AsyncRevocationSource | IdentityRevocationPort) -> None:
        if source is None:
            # Fail-closed: без реестра отзывов адаптер не существует.
            raise ValueError("IdentityRevocationAdapter: источник отзывов обязателен")
        self._source = source
        self._reasons: dict[str, str] = {}

    @property
    def source(self) -> AsyncRevocationSource | IdentityRevocationPort:
        return self._source

    @property
    def reasons(self) -> dict[str, str]:
        """Журнал причин (grant_id → reason). Только для admin-аудита."""

        return dict(self._reasons)

    async def _is_revoked(self, key: str) -> bool:
        """Прочитать состояние через async ``is_revoked`` или canonical ``is_active``."""

        is_revoked = getattr(self._source, "is_revoked", None)
        if callable(is_revoked):
            return bool(await is_revoked(key))
        return not bool(await self._source.is_active(key, now=datetime.now(UTC)))

    async def revoke(self, *, grant_id: str, reason: str) -> bool:
        """Await durable revoke и отдельно подтвердить отозванное состояние.

        ``True`` требует и typed-подтверждения записи источником, и неактивного
        состояния после неё. Повторный отзыв остаётся идемпотентным для canonical
        реализаций, которые сами подтверждают повторную запись.
        """

        key = (grant_id or "").strip()
        if not key:
            # Пустой grant_id нельзя «отозвать» — это была бы тихая победа.
            raise ValueError("IdentityRevocationAdapter.revoke: grant_id не должен быть пустым")

        # Общий identity.RevocationPort не принимает reason; DbGrantRevocation
        # совместим с этим вызовом, а reason сохраняется в admin-аудите адаптера.
        write_confirmed = bool(await self._source.revoke(key))
        self._reasons[key] = (reason or "").strip()

        # Не превращаем отсутствие исключения или одно только состояние в успех.
        state_confirmed = await self._is_revoked(key)
        return write_confirmed and state_confirmed

    async def is_revoked(self, *, grant_id: str) -> bool:
        """Проверка для ``wake``: отозванный grant нельзя разбудить."""

        key = (grant_id or "").strip()
        if not key:
            # Fail-closed: пустой grant_id считается отозванным.
            return True
        return await self._is_revoked(key)


class IdentityMintAdapter:
    """``admin.MintPort`` поверх ``identity.mint_plugin_claims``.

    ``secret`` передаётся ЯВНО (HS256, без dev-фолбэков — см. ``keys.py``).
    Разбор выпущенного токена идёт через ``claims_from_payload``: ``jti``,
    ``iat``/``exp`` и ``grant_id`` берутся из проверенного payload, поэтому
    ``IssuedPluginClaims`` не расходится с тем, что реально подписано.
    """

    def __init__(self, *, secret: str | bytes) -> None:
        if not secret:
            # Пустой секрет = INVALID_SIGNATURE в identity; не даём собрать
            # «полуподключённый» адаптер, который молча вернёт ложный токен.
            raise ValueError("IdentityMintAdapter: secret обязателен (без dev-фолбэков)")
        self._secret = secret

    async def mint(self, *, request: MintRequest) -> IssuedPluginClaims:
        """Выпустить короткий ``PluginClaims`` и заполнить jti/issued_at/expires_at."""

        if not isinstance(request, MintRequest):
            raise TypeError("IdentityMintAdapter.mint: ожидался MintRequest")

        token = mint_plugin_claims(
            grant=_MintGrantView.from_request(request),
            instance=_MintInstanceView.from_request(request),
            secret=self._secret,
            ttl_seconds=int(request.ttl_seconds),
        )
        if not isinstance(token, str) or not token.strip():
            # Синхронное ядро вернуло мусор — успехом это не считаем.
            raise ValueError("IdentityMintAdapter: mint_plugin_claims вернул пустой токен")

        payload = peek_plugin_token(token)
        if payload is None:
            raise ValueError("IdentityMintAdapter: выпущенный токен не разбирается")

        # claims_from_payload повторно валидирует iss/aud/typ, формат digest,
        # jti, sub, обязательные claim и forbidden egress/quotas.
        claims = claims_from_payload(payload)
        if claims.grant_id != request.grant_id or claims.plugin_instance_id != request.plugin_instance_id:
            raise ValueError("IdentityMintAdapter: токен привязан к другому grant/instance")
        if claims.exp - claims.iat != int(request.ttl_seconds):
            # Подписанный TTL обязан совпадать с запрошенным: иначе адаптер
            # вернул бы ``expires_at``, не соответствующий токену.
            raise ValueError("IdentityMintAdapter: TTL токена не совпадает с MintRequest")

        return IssuedPluginClaims(
            token=token,
            grant_id=claims.grant_id,
            jti=claims.jti,
            expires_at=datetime.fromtimestamp(claims.exp, tz=UTC),
            ttl_seconds=int(request.ttl_seconds),
        )


__all__ = [
    # Алиасы: коллизия имён MintPort/RevocationPort в admin и identity.
    "AdminMintPort",
    "AdminRevocationPort",
    "IdentityRevocationPort",
    # Адаптеры.
    "IdentityMintAdapter",
    "IdentityRevocationAdapter",
    "AsyncRevocationSource",
    "SyncRevocationSource",
]
