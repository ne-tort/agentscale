"""KeyRing плагинов: ``kid``, HS256/EdDSA, JWKS, ротация.

Ключевое отличие от ``application/pod_identity/bridge.py:111-128``: **никаких
dev-фолбэков**. Там при пустом ``pod_identity_bridge_secret`` в нестрогом режиме
подставлялись ``pod_agent_bridge_auth_token`` / ``auth_test_secret``. Здесь
отсутствие ключа = ``UNKNOWN_KID`` / ``INVALID_SIGNATURE``, т.е. отказ, а не
«продолжить на чём-нибудь» (правило fail-closed плана миграции §0.5).

PyJWT[crypto] уже используется в репозитории (``pyproject.toml:21``,
``infrastructure/auth/jwt.py``, ``pod_identity/bridge.py``), поэтому HS256 и
EdDSA реализованы через ``jwt.encode``/``jwt.decode``; самописный
``hmac``+``base64`` не нужен. EdDSA требует ``cryptography`` (ставится экстрой
``PyJWT[crypto]``); при её отсутствии конструктор Ed25519-ключа явно падает,
а не молча деградирует до HS256.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import Any, Final, Mapping
from uuid import uuid4

import jwt

from .types import PluginTokenError, PluginTokenErrorCode

SUPPORTED_ALGORITHMS: Final[frozenset[str]] = frozenset({"HS256", "EdDSA"})
KID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9._\-]{1,128}$")


def peek_plugin_token(token: str) -> dict[str, Any] | None:
    """Декодировать JWT **без проверки подписи** — только для разветвления middleware.

    Аналог ``application/pod_identity/bridge.py::peek_pod_bridge_token``: родитель
    подключает plugin-ветку в ``core/middleware.py`` и по полю ``typ`` отличает
    ``plugin_capability`` от ``pod_bridge``. Результат **никогда** не использовать
    для авторизации — проверку делает ``verify_plugin_token``.

    На битом/пустом/не-JWT токене возвращает ``None``, исключений не бросает:
    middleware должен просто не выбрать plugin-ветку, а не упасть 500.
    """
    if not isinstance(token, str) or not token.strip():
        return None
    try:
        payload = jwt.decode(
            token,
            options={"verify_signature": False, "verify_aud": False, "verify_exp": False},
        )
    except Exception:
        return None
    return dict(payload) if isinstance(payload, Mapping) else None


def generate_kid(algorithm: str = "HS256") -> str:
    """Сгенерировать уникальный ``kid`` с префиксом алгоритма (для JWKS)."""
    alg = (algorithm or "").strip()
    return f"{alg.lower()}-{uuid4().hex}"


def kid_from_secret(secret: str | bytes, algorithm: str = "HS256") -> str:
    """Детерминированный ``kid`` из отпечатка секрета (без утечки самого секрета).

    Нужна для ``mint_plugin_claims(..., secret=...)``: у mint нет готового
    ``KeyRing``, поэтому токен подписывается разовым ключом, чей ``kid``
    вычисляется из секрета. Verify с таким же секретом (собранный через
    ``keyring_from_secret``) получит тот же ``kid`` и проверит подпись.
    """
    raw = secret.encode("utf-8") if isinstance(secret, str) else secret
    return f"{algorithm.lower()}-{hashlib.sha256(raw).hexdigest()[:32]}"


@dataclass(frozen=True, slots=True)
class KeyEntry:
    """Один ключ подписи.

    ``secret`` для HS256 — строка/bytes общего секрета; для EdDSA — PEM
    приватного ключа (Ed25519). ``public`` — PEM публичного ключа (только
    EdDSA); для HS256 в JWKS ключ **не** публикуется вообще (симметричный
    секрет нельзя отдавать наружу).
    """

    kid: str
    algorithm: str
    secret: str | bytes
    public: str | bytes | None = None

    def __post_init__(self) -> None:
        if not self.kid or not KID_PATTERN.fullmatch(self.kid):
            raise ValueError("kid must be 1..128 url-safe chars")
        if self.algorithm not in SUPPORTED_ALGORITHMS:
            raise ValueError(f"unsupported plugin key algorithm: {self.algorithm!r}")
        if not self.secret:
            raise ValueError("plugin signing secret must not be empty")
        if self.algorithm == "EdDSA" and not self.public:
            raise ValueError("EdDSA key entry requires a public PEM for verification")


class KeyRing:
    """Набор ключей с ``kid``, активным ключом и ротацией.

    ``__init__`` **обязан** получить хотя бы один явно переданный ключ; пустой
    ring и «возьми секрет из settings, если свой не дали» запрещены. Родитель
    при интеграции сам читает ``config/settings.py`` и передаёт ключи сюда —
    этот модуль про config ничего не знает.
    """

    def __init__(self, entries: list[KeyEntry] | tuple[KeyEntry, ...], *, active_kid: str | None = None) -> None:
        if not entries:
            raise PluginTokenError(
                code=PluginTokenErrorCode.UNKNOWN_KID,
                detail="KeyRing requires an explicitly configured signing key; no dev fallback",
            )
        self._entries: dict[str, KeyEntry] = {}
        for entry in entries:
            if entry.kid in self._entries:
                raise ValueError(f"duplicate plugin key kid: {entry.kid!r}")
            self._entries[entry.kid] = entry
        if active_kid is None:
            # Последний переданный ключ считается активным: при ротации это
            # новый ключ, старые остаются только для проверки in-flight токенов.
            active_kid = entries[-1].kid
        if active_kid not in self._entries:
            raise PluginTokenError(
                code=PluginTokenErrorCode.UNKNOWN_KID,
                detail=f"active kid {active_kid!r} is not present in KeyRing",
            )
        self._active_kid = active_kid

    # --- Свойства ------------------------------------------------------------
    @property
    def active_kid(self) -> str:
        return self._active_kid

    @property
    def active(self) -> KeyEntry:
        return self._entries[self._active_kid]

    def get(self, kid: str) -> KeyEntry:
        """Вернуть ключ по ``kid``; отсутствие = ошибка, а не фолбэк."""
        entry = self._entries.get(kid)
        if entry is None:
            raise PluginTokenError(
                code=PluginTokenErrorCode.UNKNOWN_KID,
                detail=f"no plugin signing key for kid {kid!r}",
            )
        return entry

    # --- Подпись / проверка --------------------------------------------------
    def sign(self, payload: dict[str, Any], *, kid: str | None = None) -> str:
        """Подписать payload активным ключом, проставив ``kid`` в заголовок."""
        entry = self.get(kid) if kid is not None else self.active
        return jwt.encode(payload, entry.secret, algorithm=entry.algorithm, headers={"kid": entry.kid})

    def decode(self, token: str) -> dict[str, Any]:
        """Проверить подпись по ``kid`` из заголовка и вернуть payload.

        Сначала читается **только** заголовок (без проверки), затем из ring
        берётся ключ с этим ``kid``. Неизвестный ``kid`` → ``UNKNOWN_KID`` (а не
        попытка перебрать все ключи / подставить dev-секрет). Алгоритм берётся
        строго из записи ключа, поэтому «algorithm confusion» (HS256 вместо
        EdDSA и наоборот) исключён.
        """
        try:
            header = jwt.get_unverified_header(token)
        except (jwt.PyJWTError, TypeError, ValueError) as exc:
            raise PluginTokenError(
                code=PluginTokenErrorCode.MALFORMED,
                detail="cannot parse plugin token header",
            ) from exc

        kid = str(header.get("kid") or "").strip()
        if not kid:
            raise PluginTokenError(
                code=PluginTokenErrorCode.UNKNOWN_KID,
                detail="plugin token header missing kid",
            )
        entry = self.get(kid)
        verify_key = entry.public if entry.algorithm == "EdDSA" else entry.secret
        try:
            payload = jwt.decode(
                token,
                verify_key,
                algorithms=[entry.algorithm],
                # iss/aud/typ/exp/jti проверяются отдельно в mint.verify_*,
                # с собственными кодами ошибок; здесь только подпись и структура.
                options={
                    "verify_signature": True,
                    "verify_aud": False,
                    "verify_iss": False,
                    "verify_exp": False,
                    "verify_iat": False,
                    "verify_nbf": False,
                },
            )
        except jwt.PyJWTError as exc:
            raise PluginTokenError(
                code=PluginTokenErrorCode.INVALID_SIGNATURE,
                detail="plugin token signature verification failed",
            ) from exc
        if not isinstance(payload, dict):
            raise PluginTokenError(
                code=PluginTokenErrorCode.MALFORMED,
                detail="plugin token payload is not an object",
            )
        return payload

    # --- Ротация -------------------------------------------------------------
    def rotate(self, entry: KeyEntry, *, activate: bool = True) -> str:
        """Добавить новый ключ; старые остаются для проверки in-flight токенов.

        Возвращает ``kid`` активного ключа. При ``activate=False`` ключ лишь
        регистрируется (например, готовится к запланированной ротации).
        """
        if entry.kid in self._entries:
            raise ValueError(f"plugin key kid already registered: {entry.kid!r}")
        self._entries[entry.kid] = entry
        if activate:
            self._active_kid = entry.kid
        return self._active_kid

    def retire(self, kid: str) -> None:
        """Убрать ключ после истечения всех выпущенных им токенов.

        Активный ключ удалить нельзя — это сделало бы ring пустым/невалидным
        и нарушило fail-closed (лучше явная ошибка, чем тихий fallback).
        """
        if kid == self._active_kid:
            raise ValueError("cannot retire the active plugin key; rotate first")
        if kid not in self._entries:
            raise PluginTokenError(
                code=PluginTokenErrorCode.UNKNOWN_KID,
                detail=f"cannot retire unknown kid {kid!r}",
            )
        del self._entries[kid]

    # --- JWKS ----------------------------------------------------------------
    def jwks(self) -> dict[str, Any]:
        """Публичное JWKS-представление **без приватных/симметричных ключей**.

        * HS256-ключи не публикуются вообще: симметричный секрет нельзя
          выдавать наружу (тест ``test_jwks_never_contains_private_key``).
        * EdDSA-ключи публикуются как OKP/Ed25519 JWK (только ``x`` — публичные
          координаты), приватная PEM-часть ``d`` не попадает в ответ.
        """
        keys: list[dict[str, Any]] = []
        for entry in self._entries.values():
            if entry.algorithm == "HS256":
                continue
            jwk = _public_okp_jwk(entry)
            keys.append({**jwk, "kid": entry.kid, "alg": "EdDSA", "use": "sig"})
        return {"keys": keys}


def _public_okp_jwk(entry: KeyEntry) -> dict[str, Any]:
    """Построить публичный OKP-JWK из Ed25519 PEM через ``cryptography``."""
    try:
        from cryptography.hazmat.primitives import serialization
        from jwt.utils import base64url_encode
    except ImportError as exc:  # pragma: no cover - зависит от окружения
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="EdDSA JWKS requires PyJWT[crypto]/cryptography to be installed",
        ) from exc

    try:
        pem = entry.public
        if isinstance(pem, str):
            pem = pem.encode("utf-8")
        public_key = serialization.load_pem_public_key(pem)
        raw = public_key.public_bytes(
            encoding=serialization.Encoding.Raw,
            format=serialization.PublicFormat.Raw,
        )
    except (TypeError, ValueError) as exc:
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="EdDSA plugin public key must be a valid Ed25519 PEM",
        ) from exc
    return {"kty": "OKP", "crv": "Ed25519", "x": base64url_encode(raw).decode("ascii")}


def hs256_entry(secret: str | bytes, *, kid: str | None = None) -> KeyEntry:
    """Удобный конструктор HS256-ключа (секрет передаётся **явно**)."""
    if not secret:
        raise PluginTokenError(
            code=PluginTokenErrorCode.INVALID_SIGNATURE,
            detail="HS256 plugin key requires an explicit secret; no dev fallback",
        )
    return KeyEntry(kid=kid or generate_kid("HS256"), algorithm="HS256", secret=secret)


def keyring_from_secret(secret: str | bytes, *, algorithm: str = "HS256") -> KeyRing:
    """Собрать ``KeyRing`` из одного явно переданного симметричного секрета.

    ``kid`` детерминирован (см. ``kid_from_secret``), поэтому токены, выпущенные
    ``mint_plugin_claims(secret=...)``, проверяются этим ring без дополнительной
    конфигурации. Пустой секрет = ``PluginTokenError``, а не dev-фолбэк.
    """
    if algorithm != "HS256":
        raise PluginTokenError(
            code=PluginTokenErrorCode.MALFORMED,
            detail="keyring_from_secret supports HS256 only; EdDSA needs a PEM key pair",
        )
    entry = hs256_entry(secret, kid=kid_from_secret(secret, algorithm))
    return KeyRing([entry])


def eddsa_entry(private_pem: str | bytes, public_pem: str | bytes, *, kid: str | None = None) -> KeyEntry:
    """Удобный конструктор EdDSA-ключа (приватный PEM + публичный PEM)."""
    return KeyEntry(
        kid=kid or generate_kid("EdDSA"),
        algorithm="EdDSA",
        secret=private_pem,
        public=public_pem,
    )
