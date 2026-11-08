"""Разрешение egress-политики по ``grant_id`` (v4, §4.2, §6.1, §13.3).

Единственный источник правил — ``CapabilityGrant`` в БД. Краткоживущий
``PluginClaims`` несёт ТОЛЬКО ``grant_id``; здесь нет и не будет чтения поля
``egress`` из токена. Иначе истечение TTL (300 с) пересобирало бы кластеры Envoy
и NetworkPolicy посреди активного запроса (критерий 22).

Fail-closed (обязательное требование): grant не найден, отозван, ещё не выдан,
истёк, хранилище недоступно или вернуло битые данные — ``EgressDenied``, а НЕ
«разрешить по умолчанию» и НЕ «пустая политика == разрешить всё».
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

from prodavan.application.plugins.egress.errors import EgressDenied, EgressPolicyError
from prodavan.application.plugins.egress.policy import EgressPolicy, resolve_policy


@dataclass(frozen=True, slots=True)
class GrantLookup:
    """Результат чтения grant из хранилища (transport-neutral контракт).

    ``raw`` — сам grant (dict / dataclass / ORM-объект с ``.egress``). Поля
    жизненного цикла вынесены явно, чтобы разные адаптеры БД не «прятали»
    отзыв в недрах ORM-маппинга: проверка отзыва — обязанность резолвера,
    а не хранилища.
    """

    raw: Any
    active: bool = True
    revoked_at: datetime | None = None
    issued_at: datetime | None = None
    expires_at: datetime | None = None


@runtime_checkable
class GrantStorePort(Protocol):
    """Порт хранилища ``CapabilityGrant`` (реализация — в ``infra/**``, не здесь).

    Синхронный контракт намеренно: резолвер не делает сетевых вызовов сам и не
    зависит от async-рантайма. Адаптер БД предоставляется задачей F1.
    ``None`` означает «grant не найден» → отказ.
    """

    def get_grant(self, grant_id: str) -> GrantLookup | None: ...


def _claim_grant_id(claims: Any) -> str:
    """Извлечь ``grant_id`` из claims, НЕ читая ``egress`` и не проверяя ``exp``.

    Срок жизни токена проверяется identity-контуром (задача F1) ДО попадания сюда.
    Намеренно НЕ смотрим на ``exp``: политика не зависит от TTL токена
    (критерий 22) — истёкший токен с валидным ``grant_id`` даёт ту же политику,
    что и свежий. Отсутствие ``grant_id`` = отказ.
    """
    grant_id: Any = None
    if isinstance(claims, dict):
        grant_id = claims.get("grant_id")
    else:
        grant_id = getattr(claims, "grant_id", None)
    if isinstance(grant_id, str) and grant_id.strip():
        return grant_id.strip()
    raise EgressDenied(
        "token does not carry a grant_id; egress policy cannot be resolved",
        reason="grant_id_missing",
        status=401,
    )


def _reject_inactive(lookup: GrantLookup, grant_id: str, *, now: datetime) -> None:
    """Проверить жизненный цикл grant (отзыв / выдача / истечение)."""
    if not lookup.active or lookup.revoked_at is not None:
        raise EgressDenied(
            f"capability grant is revoked: {grant_id!r}",
            reason="grant_revoked",
        )
    if lookup.issued_at is not None and now < lookup.issued_at:
        raise EgressDenied(
            f"capability grant is not yet active: {grant_id!r}",
            reason="grant_not_yet_issued",
        )
    if lookup.expires_at is not None and now >= lookup.expires_at:
        raise EgressDenied(
            f"capability grant has expired: {grant_id!r}",
            reason="grant_expired",
        )


class GrantPolicyResolver:
    """``resolve(grant_id) -> EgressPolicy`` с fail-closed семантикой."""

    def __init__(self, store: GrantStorePort) -> None:
        if store is None:
            # Отсутствие интеграции = ЯВНАЯ ошибка, а не «in-memory allow-all».
            raise EgressDenied(
                "GrantStorePort is not configured; egress is fail-closed",
                reason="store_missing",
                status=503,
            )
        self._store = store

    def resolve(self, grant_id: str) -> EgressPolicy:
        """Вернуть политику из grant; любая неопределённость -> ``EgressDenied``."""
        cleaned = grant_id.strip() if isinstance(grant_id, str) else ""
        if not cleaned:
            raise EgressDenied("grant_id must be a non-empty string", reason="grant_id_missing", status=401)
        now = datetime.now(UTC)

        try:
            lookup = self._store.get_grant(cleaned)
        except EgressDenied:
            raise
        except Exception as exc:
            # Хранилище упало — НЕ «разрешить по умолчанию» (§4.5: недоступность
            # зависимости для позитивного решения = deny/system failure).
            raise EgressDenied(
                "grant store is unavailable; egress is fail-closed",
                reason="store_unavailable",
                status=503,
            ) from exc

        if lookup is None:
            raise EgressDenied(
                f"capability grant not found: {cleaned!r}",
                reason="grant_missing",
            )
        if not isinstance(lookup, GrantLookup):
            raise EgressPolicyError(
                "GrantStorePort returned an unsupported lookup object",
                reason="store_contract_violation",
            )
        _reject_inactive(lookup, cleaned, now=now)

        policy = resolve_policy(lookup.raw)
        # Политика строится ТОЛЬКО из БД. Привязываем grant_id для аудита, даже
        # если сам grant не хранит его одноимённым полем.
        if policy.grant_id is None:
            policy = EgressPolicy(
                mode=policy.mode,
                rules=policy.rules,
                allowed_hosts=policy.allowed_hosts,
                allowed_paths=policy.allowed_paths,
                max_egress_bytes=policy.max_egress_bytes,
                grant_id=cleaned,
            )
        return policy

    def resolve_claims(self, claims: Any) -> EgressPolicy:
        """Удобная обёртка: достать ``grant_id`` из claims и резолвить политику.

        Это ЕДИНСТВЕННОЕ, что мы читаем из токена. Поля ``egress``,
        ``allowed_hosts``, ``quotas`` из ``PluginClaims`` игнорируются полностью
        (v4 §4.2: они убраны из токена). Даже если злоумышленник подсунет их в
        payload, они не влияют на решение.
        """
        return self.resolve(_claim_grant_id(claims))


__all__ = [
    "GrantLookup",
    "GrantPolicyResolver",
    "GrantStorePort",
]
