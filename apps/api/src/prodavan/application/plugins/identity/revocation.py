"""Отзыв плагинов по ``grant_id`` (F1, критерий №12).

Отзыв = **деактивация ``CapabilityGrant``** в БД + проверка ``grant_id`` на
каждом запросе. Это **не** ``bump_pod_bridge_generation`` из
``application/pod_identity/bridge.py:164``: тот механизм привязан к ``pod_id``
и Redis и относится к другому principal (агентский Pod). Смешивать их нельзя.

Здесь in-memory реализация для тестов + порт ``RevocationPort`` (Protocol)
для будущей БД/Redis-реализации (задача F2/F11). Fail-closed: если порт не
передан или проверка упала — доступ закрыт, а не «продолжить».

Контракт порта согласован с задачей 03
(``application/plugins/capabilities/ports.py::GrantRevocationPort``): основной
метод — ``is_active(grant_id, *, now: datetime) -> bool``, в порт передаётся
aware UTC ``datetime``. ``is_revoked`` сохранён как обратный алиас для
проверок F1.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Protocol, runtime_checkable

from .types import PluginClaims, PluginTokenError, PluginTokenErrorCode


@runtime_checkable
class RevocationPort(Protocol):
    """Порт хранилища отзывов (БД / Redis / in-memory).

    Структурный контракт, объявленный задачей 03: сервис capability проверяет
    grant через ``is_active(grant_id, *, now)`` **без адаптера**. Реализация
    обязана возвращать ``True`` только для точно известного активного grant;
    любая неопределённость/ошибка = ``False`` (fail-closed).

    ``is_revoked(grant_id)`` — обратный алиас (используется ``GrantRevocation``
    и тестами F1). ``revoke`` деактивирует grant. Оба метода объявлены здесь, но
    задача 03 требует от реализации только ``is_active``; адаптер/обёртка
    может не иметь ``revoke`` — см. ``GrantRevocation.bind``.
    """

    def is_active(self, grant_id: str, *, now: datetime) -> bool: ...

    def revoke(self, grant_id: str) -> None: ...


class InMemoryRevocation:
    """Простая реализация для unit-тестов и однопроцессного dev-стенда.

    Не является источником истины в проде: для кластера нужна реализация порта
    поверх БД/Redis. Потокобезопасна (lock), чтобы gateway мог проверять отзывы
    параллельно с admin-операцией revoke. ``now`` принимается для совместимости
    с портом БД (у которого есть ``expires_at``), но in-memory набор отзывов
    от времени не зависит.
    """

    def __init__(self, *, initially_revoked: tuple[str, ...] | list[str] | None = None) -> None:
        self._revoked: set[str] = set(initially_revoked or ())
        self._lock = threading.Lock()

    @staticmethod
    def _normalize(grant_id: str) -> str:
        return (grant_id or "").strip()

    def is_active(self, grant_id: str, *, now: datetime | None = None) -> bool:
        # ``now`` здесь не используется, но сохранён в сигнатуре ради
        # структурного соответствия ``GrantRevocationPort`` задачи 03 (БД-
        # реализация проверяет ``expires_at``). Допускаем ``None`` для вызовов
        # из тестов/``is_revoked``.
        key = self._normalize(grant_id)
        if not key:
            # Пустой grant_id не может быть активным — fail-closed.
            return False
        with self._lock:
            return key not in self._revoked

    def is_revoked(self, grant_id: str) -> bool:
        return not self.is_active(grant_id)

    def revoke(self, grant_id: str) -> None:
        key = self._normalize(grant_id)
        if not key:
            raise ValueError("grant_id must not be empty")
        with self._lock:
            self._revoked.add(key)

    def activate(self, grant_id: str) -> None:
        """Снять отзыв (для тестов/повторного grant); не часть порта."""
        key = self._normalize(grant_id)
        with self._lock:
            self._revoked.discard(key)

    @property
    def revoked_ids(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._revoked)


class GrantRevocation:
    """Проверка ``grant_id`` из ``PluginClaims`` на каждом запросе.

    Оборачивает ``RevocationPort`` и даёт fail-closed ``check(claims)``:

    * порт не задан → ``REVOKED`` (нет интеграции = явная ошибка);
    * ``is_active``/``is_revoked`` бросили исключение → ``REVOKED``;
    * grant неактивен/отозван → ``REVOKED``.

    Возвращает ``claims`` для удобства цепочки ``verify → check → allowlist``.
    """

    def __init__(self, port: RevocationPort | None = None) -> None:
        self._port = port

    @property
    def port(self) -> RevocationPort | None:
        return self._port

    def bind(self, port: RevocationPort) -> None:
        """Подключить реализацию порта (родитель вызовет при интеграции F11)."""
        self._port = port

    def is_active(self, grant_id: str, *, now: int | float | datetime | None = None) -> bool:
        """Fail-closed ``is_active`` поверх порта (контракт задачи 03).

        Порт отсутствует → ``False``; исключение порта → ``False``; пустой
        ``grant_id`` → ``False``. В порт всегда передаётся **aware UTC
        ``datetime``** — ровно так, как объявлено в
        ``capabilities/ports.py::GrantRevocationPort`` (``now: datetime``);
        это важно, чтобы БД-реализация могла сравнивать с ``expires_at``.
        """
        key = (grant_id or "").strip()
        if not key or self._port is None:
            return False
        try:
            # Валидация ``now`` тоже внутри fail-closed блока: naive datetime
            # (и любая иная ошибка приведения к aware UTC) — это ОТКАЗ, а не
            # исключение наружу. Иначе ``is_active`` вместо ``False`` бросал бы
            # PluginTokenError, и ``check``/``is_revoked`` не закрывали доступ.
            moment = _utc_datetime(now)
            return bool(self._port.is_active(key, now=moment))
        except Exception:
            # Хранилище недоступно ≠ «grant активен». Закрываем доступ.
            return False

    def is_revoked(self, grant_id: str) -> bool:
        """Обратный алиас; fail-closed (``not is_active``)."""
        return not self.is_active(grant_id)

    def revoke(self, grant_id: str) -> None:
        """Деактивировать grant (обязателен подключённый порт)."""
        if self._port is None:
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="revocation port is not configured; cannot deactivate grant",
            )
        key = (grant_id or "").strip()
        if not key:
            raise PluginTokenError(
                code=PluginTokenErrorCode.MISSING_CLAIM,
                detail="cannot revoke an empty grant_id",
            )
        self._port.revoke(key)

    def check(self, claims: PluginClaims) -> PluginClaims:
        """Отклонить токен, если его ``grant_id`` деактивирован.

        Критерий №12: «revoke деактивирует CapabilityGrant; старый PluginClaims
        отклоняется по grant_id». Даже ещё не истёкший токен перестаёт работать
        сразу после revoke — короткий TTL не единственный механизм отзыва.
        """
        if not self.is_active(claims.grant_id):
            raise PluginTokenError(
                code=PluginTokenErrorCode.REVOKED,
                detail="capability grant is revoked or revocation source unavailable",
            )
        return claims


def _utc_datetime(now: int | float | datetime | None) -> datetime:
    """Привести ``now`` к aware UTC ``datetime`` для порта задачи 03.

    Naive ``datetime`` считается неоднозначным → ``PluginTokenError``, который
    ``is_active`` перехватывает и трактует как fail-closed ``False``.
    """
    if now is None:
        return datetime.now(UTC)
    if isinstance(now, datetime):
        if now.tzinfo is None:
            raise PluginTokenError(
                code=PluginTokenErrorCode.MALFORMED,
                detail="revocation now datetime must be timezone-aware",
            )
        return now.astimezone(UTC)
    return datetime.fromtimestamp(float(now), tz=UTC)
