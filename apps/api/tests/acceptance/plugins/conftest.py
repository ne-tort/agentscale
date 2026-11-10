"""Общие фикстуры приёмочных (acceptance) тестов plugin-платформы v4.

НАЗНАЧЕНИЕ
----------
В волне 1 каждый агент написал unit-тесты ТОЛЬКО в своём каталоге
(``tests/unit/plugins/<zone>/**``). Межмодульные контракты («стыки») при этом
не проверял никто: identity выпускает токен, capabilities его проверяет,
gateway проксирует, admin оркестрирует установку, tenantdb/objects/artifacts/
activation/events/egress живут каждый в своём trust-контуре. Этот файл даёт
ЕДИНЫЙ набор in-memory фейков, на котором такой сквозной путь собирается.

ПРАВИЛА (обязательные, из задания и плана миграции §0):
  * Только in-memory состояние. НИКАКИХ БД, PostgreSQL, MinIO, Redpanda,
    k8s, HTTP, сети, ``sleep`` и чтения ``settings`` как источника секретов.
  * Fail-closed: отсутствие адаптера/порта = явный отказ, а не «тихий успех».
    Фейки моделируют это тем же контрактом, что и продовые адаптеры.
  * ЛЕНИВЫЕ ИМПОРТЫ. Модули ``application/plugins/**`` создаются параллельно
    разными агентами и в момент коллекции могут ещё отсутствовать (или
    временно не импортироваться из-за незавершённой правки). Поэтому КАЖДЫЙ
    импорт чужого пакета идёт через :func:`_require_module`: при ``ImportError``
    / ``SyntaxError`` тест получает ``pytest.skip`` с ПОНЯТНЫМ сообщением,
    а НЕ падение коллекции. Это требование задания, а не «смягчение» теста:
    при первом прогоне после завершения миграции skip'ы исчезнут, и ожидаемые
    падения из-за отсутствующих адаптеров станут видимыми СИГНАЛОМ.
  * Комментарии — на русском, идентификаторы — на английском.

ТЕСТЫ НЕ ЗАПУСКАЮТСЯ до явного решения о начале верификации (verification
freeze). См. ``docs/migration/plugin-test-plan.md``.
"""

from __future__ import annotations

import importlib
import itertools
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from prodavan.domain.plugins.helpers import plugin_instance_id_for

# ---------------------------------------------------------------------------
# Константы: детерминированные значения для ВСЕХ стыков
# ---------------------------------------------------------------------------

TEST_SECRET = "plugin-acceptance-explicit-secret-9f3c"
"""Секрет подписи HS256. Передаётся ЯВНО (identity не имеет dev-фолбэков)."""

TEST_EVENT_SECRET_REF = "vault://acceptance/plugin-events"
TEST_EVENT_SIGNING_KEY = "acceptance-test-only-event-signing-key-32-bytes"
"""Event HMAC живёт только в test SecretStore, отдельно от identity HS256."""

TEST_DIGEST = "sha256:" + ("a1b2c3d4" * 8)
"""Канонический ``sha256:<64 lowercase hex>`` — один и тот же во всех зонах."""

TEST_COMPANY = "co_alpha"
TEST_OTHER_COMPANY = "co_beta"
TEST_PLUGIN = "mod_equipment"
TEST_INSTANCE = plugin_instance_id_for(TEST_COMPANY, TEST_PLUGIN)
"""Canonical ``inst_<32 hex>`` из ``(company, plugin)`` (domain.plugins.helpers).

Тот же deterministic id вычисляют ``PluginAdminService._new_instance`` и
``ModuleSource.plugin_instance_id``: source/CI/admin адресуют ОДНУ установку.
Специальные legacy/foreign фикстуры со своими id здесь не переопределяются.
"""
TEST_GRANT_ID = "grant_alpha_1"
TEST_VERSION = "1.2.0"
TEST_QUOTA_VALUES = {
    "ops_per_minute": 7,
    "max_rows": 11,
    "max_bytes": 1024,
    "max_keys": 3,
    "max_object_bytes": 4096,
    "max_egress_bytes": 2048,
}
"""Нестандартные grant limits: acceptance не опирается на admin60/runtime120."""

IDENTITY = (
    "https://github.com/ne-tort/agentscale/.github/workflows/"
    "plugin-artifacts.yml@refs/heads/main"
)
ISSUER = "https://token.actions.githubusercontent.com"

FIXED_NOW = datetime(2026, 11, 8, 9, 0, 0, tzinfo=UTC)
"""Фиксированные часы: тесты детерминированы, реальный ``time`` не нужен."""

FIXED_EPOCH = int(FIXED_NOW.timestamp())

_ID_COUNTER = itertools.count(1)
"""Детерминированный генератор id: повторный запуск даёт те же receipt'ы."""


def _next_id() -> str:
    return f"acceptance-{next(_ID_COUNTER)}"


def _sign_claims(
    *,
    grant: "FakeGrant | None" = None,
    instance: "FakeInstance | None" = None,
    now_epoch: int = FIXED_EPOCH,
    ttl_seconds: int = 300,
) -> str:
    """Детерминированный выпуск ``PluginClaims`` с ЯВНЫМ ``now``.

    ``identity.mint.mint_plugin_claims`` берёт ``iat/exp`` из реального
    ``time.time()``. Если оставить так, а в тестах использовать FIXED_NOW
    (фиксированные часы ``ClockPort``), возникнет рассинхрон: FakeClock может
    оказаться впереди ``exp`` и ``PluginActivator._mint_fresh`` честно откажет
    токен как «уже истёкший», а ядро gateway даст GRANT_EXPIRED на ровном
    месте. Поэтому собираем payload через ``build_payload(now=...)`` и подписываем
    тем же KeyRing'ом: и mint, и verify, и все сервисы видят ОДНО время.
    """

    mint_mod = _require_module("prodavan.application.plugins.identity.mint")
    identity = _require_module("prodavan.application.plugins.identity")
    payload = mint_mod.build_payload(
        grant=grant or FakeGrant(),
        instance=instance or FakeInstance(),
        now=now_epoch,
        ttl_seconds=ttl_seconds,
    )
    return identity.keyring_from_secret(TEST_SECRET).sign(payload)


# ---------------------------------------------------------------------------
# Ленивый импорт: коллекция НЕ падает на отсутствующем/битом модуле
# ---------------------------------------------------------------------------


def _require_module(dotted: str) -> Any:
    """Импортировать модуль зоны или честно пропустить тест.

    ``pytest.skip`` внутри фикстуры/хелпера — это ЯВНЫЙ СИГНАЛ «модуль ещё не
    собран», а не зелёная галочка. Отсутствие коллекции важнее ложного Fall:
    другие агенты продолжают править свои каталоги в реальном времени.

    Ловим ``ImportError`` / ``ModuleNotFoundError`` / ``SyntaxError`` /
    ``AttributeError``: незавершённая правка соседнего файла не должна ронять
    ВЕСЬ acceptance-пакет. Прочие исключения (например, ошибка при инициализации
    модуля) пробрасываются — это уже реальный дефект, который надо увидеть.
    """

    try:
        return importlib.import_module(dotted)
    except (ImportError, ModuleNotFoundError, SyntaxError, AttributeError) as exc:
        pytest.skip(
            f"модуль {dotted!r} ещё не собран или временно не импортируется: "
            f"{type(exc).__name__}: {exc}. Это сигнал для первого прогона "
            f"после решения о верификации, а не баг теста."
        )


def _optional(dotted: str) -> Any | None:
    """То же, но для НЕобязательных зависимостей: вернуть ``None`` вместо skip."""

    try:
        return importlib.import_module(dotted)
    except (ImportError, ModuleNotFoundError, SyntaxError, AttributeError):
        return None


# ---------------------------------------------------------------------------
# Доменные объекты-значения (структурные, без импорта чужого кода)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FakeGrant:
    """Структурный ``CapabilityGrant`` для ``identity.mint.MintGrant``.

    Поля совпадают с ``tests/unit/plugins/identity/conftest.py::FakeGrant``:
    тот же контракт намеренно переиспользуется на стыке, чтобы mint не знал
    о конкретном классе домена. ``replace`` нужен для сценария «grant уже,
    чем requestedCapabilities».
    """

    grant_id: str = TEST_GRANT_ID
    plugin_id: str = TEST_PLUGIN
    company_id: str = TEST_COMPANY
    scopes: tuple[str, ...] = ("cache", "objects", "events", "network")
    manifest_version: str = TEST_VERSION
    artifact_digest: str = TEST_DIGEST
    quota_ref: str = "co_alpha/plan_pro"

    def replace(self, **changes: Any) -> "FakeGrant":
        return FakeGrant(**{**self.__dict__, **changes})


@dataclass(frozen=True)
class FakeInstance:
    """Структурный ``PluginInstance`` для mint (MintInstance)."""

    plugin_instance_id: str = TEST_INSTANCE
    cabinet_id: str | None = None
    project_id: str | None = None


@dataclass
class FakeClock:
    """``ClockPort`` (activation): инжектируемые часы без ``asyncio.sleep``.

    ``advance`` сдвигает время вперёд БЕЗ реального ожидания: им проверяется
    «активный job → should_suspend=False; complete_job + TTL → True» (кр. 14).
    """

    current: datetime = FIXED_NOW
    sleeps: list[float] = field(default_factory=list)

    def now(self) -> datetime:
        # Контракт порта: ТОЛЬКО aware UTC. Нарушение = ошибка, а не «и так».
        if self.current.tzinfo is None or self.current.utcoffset() is None:
            raise ValueError("FakeClock.now() обязан вернуть aware UTC datetime")
        return self.current

    async def sleep(self, seconds: float) -> None:
        # Поллинг Ready не должен блокировать event loop; просто фиксируем вызов.
        self.sleeps.append(float(seconds))

    def advance(self, *, seconds: float) -> None:
        self.current = self.current + timedelta(seconds=seconds)

    def epoch(self) -> int:
        return int(self.current.timestamp())


# ---------------------------------------------------------------------------
# Identity: KeyRing / Revocation
# ---------------------------------------------------------------------------


@dataclass
class FakeRevocation:
    """Единый реестр отзывов по ``grant_id`` для ВСЕХ зон сразу.

    Это ключевой фейк стыка: один и тот же объект подключается и в
    ``identity.GrantRevocation`` (проверка ``check(claims)``), и в
    ``capabilities.PluginCapabilityService`` (``GrantRevocationPort.is_active``),
    и в ``admin.RevocationPort`` (``revoke``/``is_revoked``). Благодаря общему
    состоянию ``revoke`` мгновенно виден и на capabilities, и на gateway.

    Все revocation-методы async; сигнатуры выдержаны под РАЗНЫЕ протоколы зон:
      * ``is_active(grant_id, *, now)`` — capabilities/ports.py::GrantRevocationPort;
      * ``is_revoked(*, grant_id)``     — admin/ports.py::RevocationPort;
      * ``revoke(grant_id)``            — identity/revocation.py::RevocationPort;
      * ``revoke(*, grant_id, reason)`` — admin. Поэтому ``revoke`` принимает
        и positional, и keyword: см. реализацию ниже.
    """

    revoked: set[str] = field(default_factory=set)
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def is_active(self, grant_id: str, *, now: datetime | int | float | None = None) -> bool:
        # ``now`` не используется in-memory реализацией, но сохранён ради
        # структурного соответствия порту БД (там сравнивается expires_at).
        key = (grant_id or "").strip()
        if not key:
            return False  # fail-closed: пустой grant_id не активен
        return key not in self.revoked

    async def is_revoked(self, grant_id: str | None = None, *, grant_id_kw: str | None = None) -> bool:
        key = grant_id_kw if grant_id_kw is not None else grant_id
        return not await self.is_active(key or "")

    async def revoke(
        self,
        grant_id: str | None = None,
        *,
        grant_id_kw: str | None = None,
        reason: str = "acceptance revoke",
    ) -> bool:
        """Записать отзыв. Возвращает ``True`` = подтверждение (admin/port).

        identity запускает ``revoke(grant_id)`` позиционно; admin —
        ``revoke(grant_id=..., reason=...)``. Обрабатываем обе формы, чтобы ОДИН
        объект мог быть подключён к обоим контурам без адаптера-прослойки.
        """

        key = grant_id_kw if grant_id_kw is not None else grant_id
        cleaned = (key or "").strip()
        if not cleaned:
            raise ValueError("grant_id must not be empty")
        self.revoked.add(cleaned)
        self.calls.append((cleaned, reason))
        return True

    # Алиас под admin/ports.py::RevocationPort (keyword-only вызов).
    async def revoke_async(self, *, grant_id: str, reason: str) -> bool:
        return await self.revoke(grant_id_kw=grant_id, reason=reason)

    async def is_revoked_async(self, *, grant_id: str) -> bool:
        return await self.is_revoked(grant_id)

    def activate(self, grant_id: str) -> None:
        """Снять отзыв (для повторного grant в тесте изоляции компаний)."""

        self.revoked.discard((grant_id or "").strip())


@dataclass
class FakeAdminRevocation:
    """Адаптер ``admin.RevocationPort`` (async, keyword-only) поверх FakeRevocation.

    Вынесен отдельно для keyword-only admin-формы поверх canonical async fake.
    """

    inner: FakeRevocation

    async def revoke(self, *, grant_id: str, reason: str) -> bool:
        return await self.inner.revoke(grant_id_kw=grant_id, reason=reason)

    async def is_revoked(self, *, grant_id: str) -> bool:
        return await self.inner.is_revoked(grant_id)


# ---------------------------------------------------------------------------
# AdapterRegistry / Quota / Audit (capabilities)
# ---------------------------------------------------------------------------


@dataclass
class FakeCacheAdapter:
    """Минимальный typed адаптер ``CachePort``: только ``get`` для happy path.

    Реестр требует ВСЕ методы протокола, поэтому остальные добавлены как
    заглушки, возвращающие типизированные Response. Ни один метод не принимает
    ``sql``/``command``/``args``/произвольный ``payload`` (ADR-002).
    """

    calls: list[str] = field(default_factory=list)

    def _response(self, request: Any, cls: Any, **extra: Any) -> Any:
        return cls(
            request_id=getattr(getattr(request, "envelope", None), "request_id", "req-1"),
            ok=True,
            digest="sha256:" + "1" * 64,
            **extra,
        )

    def quota_usage(self, request: Any) -> Any:
        quotas = _require_module("prodavan.application.plugins.capabilities.quotas")
        value = getattr(request, "value", None)
        return quotas.QuotaUsage(
            ops=1,
            rows=0,
            bytes=len(value.encode("utf-8")) if isinstance(value, str) else 0,
            keys=1 if isinstance(getattr(request, "key", None), str) else 0,
            object_bytes=0,
            egress_bytes=0,
        )

    def get(self, request: Any) -> Any:
        contracts = _require_module(
            "prodavan.application.plugins.capabilities.contracts"
        )
        self.calls.append("get")
        return self._response(request, contracts.CacheGetResponse, found=True)

    def set(self, request: Any) -> Any:
        contracts = _require_module(
            "prodavan.application.plugins.capabilities.contracts"
        )
        self.calls.append("set")
        return self._response(request, contracts.CacheSetResponse)

    def delete(self, request: Any) -> Any:
        contracts = _require_module(
            "prodavan.application.plugins.capabilities.contracts"
        )
        self.calls.append("delete")
        return self._response(request, contracts.CacheDeleteResponse)

    def incr(self, request: Any) -> Any:
        contracts = _require_module(
            "prodavan.application.plugins.capabilities.contracts"
        )
        self.calls.append("incr")
        return self._response(request, contracts.CacheIncrResponse)

    def publish(self, request: Any) -> Any:
        contracts = _require_module(
            "prodavan.application.plugins.capabilities.contracts"
        )
        self.calls.append("publish")
        return self._response(request, contracts.CachePublishResponse)


@dataclass
class FakeQuota:
    """``QuotaPort`` capabilities: счётчик per-(company, instance, digest).

    На каждый ключ — ОТДЕЛЬНЫЙ счётчик: один «шумный» плагин не исчерпывает
    квоту компании (кр. 21). Возвращает настоящий ``QuotaDecision``, чтобы
    сервис capabilities мог списать операцию и при превышении дать 429.
    """

    counters: dict[tuple[tuple[str, str, str], Any], int] = field(default_factory=dict)
    decisions: list[Any] = field(default_factory=list)

    def enforce(self, key: Any, op: Any, amount: int, limits: Any) -> Any:
        quotas = _require_module("prodavan.application.plugins.capabilities.quotas")
        if not isinstance(limits, quotas.PluginQuota):
            raise TypeError("exact invocation.grant.quotas are required")
        ident = (key.to_tuple(), op)
        limit = limits.limit_for(op)
        used = self.counters.get(ident, 0)
        allowed = used + amount <= limit
        if allowed:
            self.counters[ident] = used + amount
        decision = quotas.QuotaDecision(
            allowed=allowed,
            code=(
                quotas.QuotaReason.ALLOWED
                if allowed
                else quotas.QuotaReason.QUOTA_EXCEEDED
            ),
            key=key,
            op=op,
            amount=amount,
            limit=limit,
            used=used + amount if allowed else used,
            remaining=max(0, limit - used - amount),
        )
        self.decisions.append(decision)
        return decision


@dataclass
class FakeAudit:
    """Append-only аудит БЕЗ raw данных: хранит только ``redacted()`` проекции.

    Подходит одновременно для ``capabilities.audit.AuditPort.append(event)``,
    ``admin.AuditPort.append(event=...)``, ``objects.AuditPort.record(event)``,
    ``gateway.AuditPort.record(event)``, ``events.AuditPort.append(type, details=)``
    и ``activation.AuditPort.append_event(type, details=)``.
    Именно по ``rows`` проверяется критерий №11 (нет SQL/params/tokens/payload).
    """

    events: list[Any] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    typed: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    fail: bool = False

    # Capabilities использует sync ``append``; async-порты получают отдельные
    # awaitable методы/адаптеры. Activation использует ИМЕННО async
    # ``append_event(event_type, details=...)`` и не делает fallback на record.

    def append(self, event: Any = None, **kwargs: Any) -> bool:
        """``capabilities.AuditPort.append`` и база для admin (через адаптер)."""

        return self._write(event, **kwargs)

    def record(self, event: Any = None, **kwargs: Any) -> bool:
        """Sync-ядро для явных адаптеров gateway/objects/tenantdb, не activation."""

        return self._write(event, **kwargs)

    async def append_event(self, event_type: str, *, details: Any) -> None:
        """``activation.AuditPort.append_event``: полноценный awaitable sink."""

        self._typed(event_type, dict(details))

    def append_async(self, *, event: Any) -> bool:
        """Алиас для читаемости: реальный await делает _AsyncAdminAudit."""

        return self._write(event)

    def _write(self, event: Any, **kwargs: Any) -> bool:
        self._maybe_fail()
        if event is None:
            # tenantdb/objects шлёт dict целиком (уже безопасная проекция).
            event = dict(kwargs) if kwargs else None
        if event is not None:
            self.events.append(event)
            row = self._project(event)
            if row is not None:
                self.rows.append(row)
        return True

    def _typed(self, event_type: str, details: dict[str, Any]) -> bool:
        """``events.AuditPort.append(event_type, *, details)``."""

        self._maybe_fail()
        self.typed.append((event_type, dict(details)))
        self.rows.append(dict(details))
        return True

    def _maybe_fail(self) -> None:
        if self.fail:
            raise RuntimeError("audit sink unavailable (fail-closed)")

    @staticmethod
    def _project(event: Any) -> dict[str, Any] | None:
        if event is None:
            return None
        redacted = getattr(event, "redacted", None)
        if callable(redacted):
            result = redacted()
            return dict(result) if isinstance(result, dict) else {"value": result}
        if isinstance(event, dict):
            return dict(event)
        # frozen slots dataclass (ActivationAuditEvent, GatewayAuditEvent):
        # ``__dict__`` отсутствует, поэтому берём поля через dataclasses.
        try:
            import dataclasses

            if dataclasses.is_dataclass(event) and not isinstance(event, type):
                return {
                    f.name: getattr(event, f.name)
                    for f in dataclasses.fields(event)
                }
        except (TypeError, AttributeError):  # pragma: no cover - защитный путь
            pass
        as_dict = getattr(event, "__dict__", None)
        if isinstance(as_dict, dict):
            return {k: v for k, v in as_dict.items() if not k.startswith("_")}
        return {"event": str(type(event).__name__)}

    def assert_no_raw(self, forbidden: tuple[str, ...] = ()) -> None:
        """Проверка критерия №11: ни ключ, ни значение не несут сырых данных.

        Безопасные digest-поля (``payload_digest``, ``sql_digest``, ...) НЕ
        считаются утечкой: это каноническая замена сырого значения, а не оно
        само. Поэтому проверяем ЗНАЧЕНИЯ на «секретные» маркеры и КЛЮЧИ — на
        точные запрещённые имена, не на подстроку ``payload``.
        """

        value_markers = forbidden or (
            "SELECT ", "INSERT ", "UPDATE ", "DELETE ", "DROP ",
            "postgresql://", "postgres://", "hunter2", "eyJhbGciOi",
            "raw content", TEST_SECRET, "Bearer ",
        )
        forbidden_keys = frozenset(
            {
                "sql", "query", "statement", "params", "parameters",
                "payload", "body", "raw", "content", "password", "token",
                "access_token", "secret", "credentials", "authorization",
                "cookie", "dsn", "connection_string", "raw_sql",
            }
        )
        for row in self.rows:
            for key, value in row.items():
                assert str(key).lower() not in forbidden_keys, (
                    f"аудит содержит запрещённый ключ {key!r}"
                )
                for marker in value_markers:
                    assert marker not in repr(value), (
                        f"аудит содержит сырые данные {marker!r} под ключом {key!r}"
                    )
        for _event_type, details in self.typed:
            for key, value in details.items():
                assert str(key).lower() not in forbidden_keys, (
                    f"аудит событий содержит запрещённый ключ {key!r}"
                )
                for marker in value_markers:
                    assert marker not in repr(value), (
                        f"аудит событий содержит сырые данные {marker!r}"
                    )


# ---------------------------------------------------------------------------
# InstanceStore / GrantStore / TenantDb / ObjectStore / ArtifactRegistry / Routes
# (admin lifecycle: полный путь установки и uninstall)
# ---------------------------------------------------------------------------


@dataclass
class FakeInstanceStore:
    """``admin.InstanceStorePort``: durable saga без fallback-записей."""

    instances: dict[tuple[str, str], Any] = field(default_factory=dict)
    created: list[Any] = field(default_factory=list)

    async def create(self, *, instance: Any) -> Any:
        key = (instance.company_id, instance.plugin_id)
        existing = self.instances.get(key)
        if existing is not None:
            for name in ("plugin_instance_id", "manifest_version", "artifact_digest"):
                if getattr(existing, name) != getattr(instance, name):
                    raise ValueError(f"immutable plugin instance field differs: {name}")
            return existing
        self.instances[key] = instance
        self.created.append(instance)
        return instance

    async def get(self, *, company_id: str, plugin_id: str) -> Any | None:
        return self.instances.get((company_id, plugin_id))

    async def get_by_id(self, *, plugin_instance_id: str) -> Any | None:
        return next(
            (item for item in self.instances.values()
             if item.plugin_instance_id == plugin_instance_id),
            None,
        )

    async def update_status(self, *, plugin_instance_id: str, status: Any) -> Any:
        from dataclasses import replace

        for key, instance in list(self.instances.items()):
            if instance.plugin_instance_id == plugin_instance_id:
                updated = replace(instance, status=status)
                self.instances[key] = updated
                return updated
        raise KeyError(plugin_instance_id)

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        return self.instances.pop((company_id, plugin_id), None) is not None

    async def resolve(self, *, company_id: str, plugin_id: str) -> str | None:
        instance = await self.get(company_id=company_id, plugin_id=plugin_id)
        return instance.plugin_instance_id if instance is not None else None


@dataclass
class FakeGrantStore:
    """``admin.GrantStorePort`` + ``egress.GrantStorePort`` на одном состоянии.

    Один и тот же grant читается и админским lifecycle, и egress-резолвером:
    это и есть стык «политика из CapabilityGrant, а не из JWT» (кр. 22).
    """

    grants: dict[tuple[str, str], Any] = field(default_factory=dict)
    enabled_modules: dict[str, tuple[str, ...]] = field(default_factory=dict)
    created: list[Any] = field(default_factory=list)
    #: Test-only lifecycle evidence: regrant replaces the current pointer without
    #: rewriting the old revoked record, so acceptance can prove no resurrection.
    history: dict[str, Any] = field(default_factory=dict)
    deleted: list[tuple[str, str]] = field(default_factory=list)
    #: Выдан ли уже детерминированный TEST_GRANT_ID основной компании.
    primary_seeded: bool = False

    async def get(self, *, company_id: str, plugin_id: str) -> Any | None:
        return self.grants.get((company_id, plugin_id))

    async def get_by_grant_id(self, *, grant_id: str) -> Any | None:
        return next(
            (g for g in self.grants.values() if g.grant_id == grant_id), None
        )

    async def create(self, *, grant: Any) -> Any:
        from dataclasses import replace

        key = (grant.company_id, grant.plugin_id)
        existing = self.grants.get(key)
        if existing is not None:
            self.history.setdefault(existing.grant_id, existing)
            return existing  # идемпотентно по grant_id/паре
        # Детерминированный grant_id для ОСНОВНОЙ тестовой компании: токен
        # из identity.mint несёт TEST_GRANT_ID, и revoke в admin должен
        # деактивировать ИМЕННО его (иначе стык admin↔capabilities не проверить).
        if grant.company_id == TEST_COMPANY and not self.primary_seeded:
            grant = replace(grant, grant_id=TEST_GRANT_ID)
            self.primary_seeded = True
        self.grants[key] = grant
        self.created.append(grant)
        self.history[grant.grant_id] = grant
        return grant

    async def regrant(
        self, *, previous_grant_id: str, grant: Any
    ) -> Any:
        key = (grant.company_id, grant.plugin_id)
        previous = self.grants.get(key)
        if grant.grant_id == previous_grant_id:
            raise ValueError("regrant requires a fresh grant_id")
        if (
            previous is None
            or previous.grant_id != previous_grant_id
            or previous.is_active
            or previous.revoked_at is None
            or previous.plugin_instance_id != grant.plugin_instance_id
        ):
            raise KeyError(previous_grant_id)
        self.history[previous.grant_id] = previous
        self.grants[key] = grant
        self.created.append(grant)
        self.history[grant.grant_id] = grant
        return grant

    async def deactivate(self, *, grant_id: str) -> Any:
        from dataclasses import replace

        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        for key, grant in list(self.grants.items()):
            if grant.grant_id == grant_id:
                updated = replace(
                    grant,
                    status=contracts.GrantStatus.REVOKED,
                    revoked_at=FIXED_NOW,
                )
                self.grants[key] = updated
                self.history[grant_id] = updated
                return updated
        raise KeyError(f"unknown grant_id: {grant_id}")

    async def delete(self, *, company_id: str, plugin_id: str) -> bool:
        key = (company_id, plugin_id)
        self.deleted.append(key)
        self.grants.pop(key, None)
        return True

    async def list_by_company(self, *, company_id: str) -> list[Any]:
        return [g for (co, _), g in self.grants.items() if co == company_id]

    async def get_enabled_modules(self, *, company_id: str) -> list[str]:
        return list(self.enabled_modules.get(company_id, ()))

    async def set_enabled_modules(
        self, *, company_id: str, plugin_ids: Any
    ) -> list[str]:
        stored = tuple(str(item) for item in plugin_ids)
        self.enabled_modules[company_id] = stored
        return list(stored)

    def seed_active_grant(
        self,
        *,
        company_id: str = TEST_COMPANY,
        plugin_id: str = TEST_PLUGIN,
        grant_id: str = TEST_GRANT_ID,
        capabilities: tuple[str, ...] = ("cache", "network"),
        egress_hosts: tuple[str, ...] = ("hooks.slack.com",),
        egress_paths: tuple[str, ...] = ("/services/",),
        quotas: Any = None,
    ) -> Any:
        """Засеять активный ``CapabilityGrantRecord`` в общий grant_store.

        Используется тестами стыков egress/admin/capabilities, чтобы grant
        существовал в ТОМ ЖЕ хранилище, которое читает ``GrantPolicyResolver``.
        Политика ``webhooks-only`` с явным allowlist: ``full`` требует approval.
        """

        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        effective_quotas = (
            contracts.QuotaSet(**TEST_QUOTA_VALUES) if quotas is None else quotas
        )
        grant = contracts.CapabilityGrantRecord(
            grant_id=grant_id,
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=TEST_INSTANCE,
            manifest_version=TEST_VERSION,
            artifact_digest=TEST_DIGEST,
            capabilities=capabilities,
            egress_policy=contracts.EgressPolicy(
                mode=contracts.EgressMode.WEBHOOKS_ONLY,
                allowed_hosts=egress_hosts,
                allowed_paths=egress_paths,
            ),
            quotas=effective_quotas,
            status=contracts.GrantStatus.ACTIVE,
            created_at=FIXED_NOW,
        )
        self.grants[(company_id, plugin_id)] = grant
        self.history[grant_id] = grant
        if grant_id == TEST_GRANT_ID:
            self.primary_seeded = True
        return grant

    # --- egress.resolver.GrantStorePort (синхронный, transport-neutral) -----

    def get_grant(self, grant_id: str) -> Any | None:
        resolver = _require_module("prodavan.application.plugins.egress.resolver")
        grant = next(
            (g for g in self.grants.values() if g.grant_id == grant_id), None
        )
        if grant is None:
            return None
        # Политика лежит в grant, а НЕ в токене: `raw` — dict с секцией egress.
        raw = {
            "grant_id": grant.grant_id,
            "egress": {
                "mode": grant.egress_policy.mode.value,
                "allowed_hosts": list(grant.egress_policy.allowed_hosts),
                "allowed_paths": list(grant.egress_policy.allowed_paths),
                # Квота берётся из ТОГО ЖЕ durable ``grant.quotas``, который
                # читают admin/capabilities: hardcoded default делал стык
                # «egress enforcement из exact grant» ложно-зелёным.
                "max_egress_bytes": grant.quotas.max_egress_bytes,
            },
        }
        if not grant.is_active:
            return resolver.GrantLookup(raw=raw, active=False, revoked_at=FIXED_NOW)
        return resolver.GrantLookup(raw=raw, active=True)


@dataclass
class FakeInstallationScopeStore:
    scopes: dict[tuple[str, str], Any] = field(default_factory=dict)

    async def ensure_from_source(
        self, *, company_id: str, plugin_id: str, plugin_instance_id: str
    ) -> Any:
        contracts = _require_module(
            "prodavan.application.plugins.installation_scope.contracts"
        )
        scope = contracts.InstallationScope(
            company_id=company_id,
            plugin_instance_id=plugin_instance_id,
            cabinet_id="cab_acceptance",
            project_id="prj_acceptance",
        )
        self.scopes[(company_id, plugin_instance_id)] = scope
        return scope

    async def get(self, *, company_id: str, plugin_instance_id: str) -> Any | None:
        return self.scopes.get((company_id, plugin_instance_id))


@dataclass
class FakeTenantDb:
    """``admin.TenantDbPort`` + слой ``TenantDatabaseService``.

    Ведёт ТОЧНОЕ множество БД/схем/ролей по ``(company_id, plugin_id)``:
    этим проверяется «uninstall не трогает другую компанию» (кр. 15).
    DSN наружу не отдаётся — только ``secret_ref`` (кр. 11).
    """

    databases: dict[str, Any] = field(default_factory=dict)
    schemas: dict[tuple[str, str], Any] = field(default_factory=dict)
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def ensure_company_database(self, *, company_id: str) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        self.calls.append(("ensure_company_database", company_id, ""))
        existing = self.databases.get(company_id)
        if existing is not None:
            return existing
        record = contracts.TenantDatabase(
            company_id=company_id,
            database_name=f"tenant_{company_id}",
            dsn_ref=f"secret_ref:plugins/tenantdb/{company_id}",
            created=True,
        )
        self.databases[company_id] = record
        return record

    async def ensure_plugin_schema(
        self, *, company_id: str, plugin_id: str, dsn_ref: str
    ) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        self.calls.append(("ensure_plugin_schema", company_id, plugin_id))
        key = (company_id, plugin_id)
        existing = self.schemas.get(key)
        if existing is not None:
            return existing
        db = await self.ensure_company_database(company_id=company_id)
        record = contracts.PluginSchema(
            company_id=company_id,
            plugin_id=plugin_id,
            database_name=db.database_name,
            schema_name=f"module_{plugin_id}",
            role_name=f"role_{company_id}_{plugin_id}",
            dsn_ref=db.dsn_ref,
            search_path=f"module_{plugin_id}",
            created=True,
        )
        self.schemas[key] = record
        return record

    async def rotate_plugin_schema(
        self, *, company_id: str, plugin_id: str, dsn_ref: str
    ) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        self.calls.append(("rotate_plugin_schema", company_id, plugin_id))
        key = (company_id, plugin_id)
        existing = self.schemas.get(key)
        if existing is None:
            return await self.ensure_plugin_schema(
                company_id=company_id, plugin_id=plugin_id, dsn_ref=dsn_ref
            )
        db = await self.ensure_company_database(company_id=company_id)
        rotation = 1 + sum(
            call[0] == "rotate_plugin_schema" and call[1] == company_id and call[2] == plugin_id
            for call in self.calls
        )
        record = contracts.PluginSchema(
            company_id=company_id,
            plugin_id=plugin_id,
            database_name=db.database_name,
            schema_name=existing.schema_name,
            role_name=existing.role_name,
            dsn_ref=f"{db.dsn_ref}/rotate-{rotation}",
            search_path=existing.search_path,
            created=True,
        )
        self.schemas[key] = record
        return record

    async def drop_plugin_schema(
        self, *, company_id: str, plugin_id: str
    ) -> tuple[bool, bool]:
        self.calls.append(("drop_plugin_schema", company_id, plugin_id))
        self.schemas.pop((company_id, plugin_id), None)
        # БД компании (и другие компании) НЕ трогаем: только роль + схема.
        return True, True

    async def revoke_plugin(self, *, company_id: str, plugin_id: str) -> bool:
        """``admin.TenantDbPort.revoke_plugin``: NOLOGIN/REVOKE только своей роли.

        Вызывается forced ``admin.revoke`` ДО suspend; без этого метода фейк
        ронял revoke-saga на ``tenant_db_revoke`` (stale constructor).
        """

        self.calls.append(("revoke_plugin", company_id, plugin_id))
        return True

    async def grant_plugin(self, company_id: str, plugin_id: str) -> Any:
        """Прямой стык с ``TenantDatabaseService.grant_plugin`` (задача 05)."""

        self.calls.append(("grant_plugin", company_id, plugin_id))
        return await self.ensure_plugin_schema(
            company_id=company_id, plugin_id=plugin_id, dsn_ref=""
        )

    def assert_other_company_untouched(self, other_company: str) -> None:
        """БД и схемы другой компании должны сохраниться после uninstall."""

        assert other_company in self.databases, (
            "БД другой компании должна была сохраниться после uninstall"
        )
        # Ни один вызов drop_plugin_schema не должен был получить чужую пару.
        for operation, company, plugin in self.calls:
            assert not (operation == "drop_plugin_schema" and company == other_company), (
                f"uninstall задел схему другой компании {other_company!r}"
            )


@dataclass
class FakeObjectStorePort:
    """``admin.ObjectStorePort``: bucket/prefix строго в границах компании."""

    buckets: dict[str, set[str]] = field(default_factory=dict)
    calls: list[tuple[str, str, str]] = field(default_factory=list)

    async def ensure_bucket(self, *, company_id: str, plugin_id: str) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        self.calls.append(("ensure_bucket", company_id, plugin_id))
        bucket = f"tenant-{company_id}"
        prefixes = self.buckets.setdefault(bucket, set())
        prefixes.add(f"module-{plugin_id}/")
        return contracts.ObjectStoreBinding(
            company_id=company_id,
            plugin_id=plugin_id,
            bucket=bucket,
            prefix=f"module-{plugin_id}/",
            created=True,
        )

    async def drop_prefix(self, *, company_id: str, plugin_id: str) -> bool:
        self.calls.append(("drop_prefix", company_id, plugin_id))
        bucket = f"tenant-{company_id}"
        self.buckets.setdefault(bucket, set()).discard(f"module-{plugin_id}/")
        return True

    async def drop_bucket_if_empty(self, *, company_id: str) -> bool:
        self.calls.append(("drop_bucket_if_empty", company_id, ""))
        bucket = f"tenant-{company_id}"
        if self.buckets.get(bucket):
            return False  # другими плагинами ещё используется — НЕ ошибка
        self.buckets.pop(bucket, None)
        return True

    def grant(self, company_id: str, plugin_id: str) -> None:
        """Синхронный хук для теста «общий путь установки» (не порт admin)."""

        self.buckets.setdefault(f"tenant-{company_id}", set()).add(
            f"module-{plugin_id}/"
        )


@dataclass
class FakeArtifactRegistryPort:
    """``admin.ArtifactRegistryPort``: только PUBLISHED разрешает grant."""

    records: dict[tuple[str, str, str], Any] = field(default_factory=dict)
    unlinked: list[tuple[str, str, str]] = field(default_factory=list)

    async def get(
        self, *, plugin_id: str, manifest_version: str, artifact_digest: str
    ) -> Any | None:
        return self.records.get((plugin_id, manifest_version, artifact_digest))

    async def unlink_installation(
        self, *, company_id: str, plugin_id: str, artifact_digest: str
    ) -> bool:
        self.unlinked.append((company_id, plugin_id, artifact_digest))
        return True

    def seed_published(
        self,
        *,
        plugin_id: str = TEST_PLUGIN,
        manifest_version: str = TEST_VERSION,
        artifact_digest: str = TEST_DIGEST,
    ) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        record = contracts.ArtifactRecord(
            plugin_id=plugin_id,
            manifest_version=manifest_version,
            artifact_digest=artifact_digest,
            status=contracts.ArtifactPublicationStatus.PUBLISHED,
            published_at=FIXED_NOW,
        )
        self.records[(plugin_id, manifest_version, artifact_digest)] = record
        return record


@dataclass
class FakePublicationApprovalStore:
    """``admin.PublicationApprovalStorePort``: exact binding + CAS consumption.

    Ключ — ПОЛНЫЙ ``(company, plugin, SemVer, bundle_digest)``: approval для
    другого артефакта/компании здесь не находится, поэтому стык
    ``approve → CI publish → grant`` проверяет exact binding, а не наличие
    «какого-нибудь» approval. ``create`` идемпотентен (INSERT-if-absent),
    ``mark_consumed`` — CAS NULL→timestamp: повтор сохраняет ПЕРВЫЙ timestamp,
    как ``SqlPluginPublicationApprovalRepository`` в production.
    """

    records: dict[tuple[str, str, str, str], Any] = field(default_factory=dict)
    created: list[Any] = field(default_factory=list)
    consumed: list[tuple[str, Any]] = field(default_factory=list)

    @staticmethod
    def _key(
        company_id: str, plugin_id: str, manifest_version: str, artifact_digest: str
    ) -> tuple[str, str, str, str]:
        return (company_id, plugin_id, manifest_version, artifact_digest)

    async def get(
        self,
        *,
        company_id: str,
        plugin_id: str,
        manifest_version: str,
        artifact_digest: str,
    ) -> Any | None:
        return self.records.get(
            self._key(company_id, plugin_id, manifest_version, artifact_digest)
        )

    async def create(self, *, approval: Any) -> Any:
        key = self._key(
            approval.company_id,
            approval.plugin_id,
            approval.manifest_version,
            approval.artifact_digest,
        )
        existing = self.records.get(key)
        if existing is not None:
            return existing
        self.records[key] = approval
        self.created.append(approval)
        return approval

    async def mark_consumed(self, *, approval_id: str, consumed_at: Any) -> Any | None:
        from dataclasses import replace

        for key, approval in list(self.records.items()):
            if approval.approval_id != approval_id:
                continue
            if approval.consumed_at is not None:
                # Повтор НЕ перезаписывает первый timestamp (SQL CAS NULL→ts).
                return approval
            updated = replace(approval, consumed_at=consumed_at)
            self.records[key] = updated
            self.consumed.append((approval_id, consumed_at))
            return updated
        return None


@dataclass
class FakeSecretDeliveryControl:
    """Scoped ``admin.SecretDeliveryControlPort`` для acceptance-манифестов.

    Acceptance-артефакты НЕ объявляют ``secret_delivery.allowlist``, поэтому
    approved control-plane фаза возвращает ПУСТОЙ tuple receipts — это валидный
    ответ ``ControlPlaneSecretDelivery`` для пустого allowlist, а не «заглушка
    успеха». Порт подключён ЯВНО: каждый вызов фиксируется, и тест видит, что
    pre-delivery/pre-revoke действительно входят в grant/revoke/uninstall saga.
    Манифесты с declared secrets обязаны собирать реальный
    ``ControlPlaneSecretDelivery`` + projection, а не этот scoped fake.
    """

    pre_deliver_calls: list[Any] = field(default_factory=list)
    pre_revoke_calls: list[Any] = field(default_factory=list)

    async def pre_deliver(self, request: Any) -> tuple[Any, ...]:
        self.pre_deliver_calls.append(request)
        return ()

    async def pre_revoke(self, request: Any) -> tuple[Any, ...]:
        self.pre_revoke_calls.append(request)
        return ()


@dataclass
class FakeRoutes:
    """``admin.RouteRegistryPort``: маршруты + egress-правила из grant."""

    registered: list[tuple[str, str, str]] = field(default_factory=list)
    unregistered: list[tuple[str, str, str]] = field(default_factory=list)

    async def register(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        self.registered.append((company_id, plugin_id, grant_id))
        return contracts.RouteRegistration(
            company_id=company_id,
            plugin_id=plugin_id,
            grant_id=grant_id,
            webhook_path=f"/webhook/{plugin_id}/{company_id}",
            pluginpage_path=f"/pluginpage/{plugin_id}/{company_id}",
            capability_path="/capabilities/v1",
            egress_rule_count=0,
            deployment=f"plugin-{plugin_id}-{company_id}",
        )

    async def unregister(
        self, *, company_id: str, plugin_id: str, grant_id: str
    ) -> bool:
        self.unregistered.append((company_id, plugin_id, grant_id))
        return True


@dataclass
class FakeWebhookSecretStore:
    records: dict[str, Any] = field(default_factory=dict)

    async def get(self, *, key: str) -> Any | None:
        return self.records.get(key)

    async def put(self, *, key: str, value: str) -> str:
        contracts = _require_module("prodavan.application.plugins.tenantdb.service")
        ref = f"file://ai_keys/{key}.secret"
        self.records[key] = contracts.StoredSecret(secret_ref=ref, value=value)
        return ref

    async def rotate(self, *, key: str, value: str) -> str:
        return await self.put(key=key, value=value)

    async def delete(self, *, secret_ref: str) -> None:
        for key, stored in list(self.records.items()):
            if stored.secret_ref == secret_ref:
                del self.records[key]


@dataclass
class FakeUpdateLock:
    """Stateful owner-bound update lock; contention never succeeds silently."""

    held: dict[tuple[str, str, str], Any] = field(default_factory=dict)
    released: list[Any] = field(default_factory=list)
    counter: int = 0

    async def acquire(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        expected_manifest_version: str,
        expected_artifact_digest: str,
    ) -> Any | None:
        key = (company_id, plugin_id, plugin_instance_id)
        if key in self.held:
            return None
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        self.counter += 1
        lease = contracts.PluginUpdateLockLease(
            company_id=company_id,
            plugin_id=plugin_id,
            plugin_instance_id=plugin_instance_id,
            expected_manifest_version=expected_manifest_version,
            expected_artifact_digest=expected_artifact_digest,
            owner_token=f"acceptance-update-owner-{self.counter}",
        )
        self.held[key] = lease
        return lease

    async def release(self, lease: Any) -> None:
        key = (lease.company_id, lease.plugin_id, lease.plugin_instance_id)
        if self.held.get(key) is not lease:
            raise RuntimeError("update lock release owner/binding mismatch")
        del self.held[key]
        self.released.append(lease)


@dataclass
class FakeActivationPort:
    """Owner-aware admin ActivationPort with live artifact/binding state."""

    deployments: dict[tuple[str, str], str] = field(default_factory=dict)
    deployment_instance_ids: dict[tuple[str, str], str] = field(default_factory=dict)
    artifact_pins: dict[tuple[str, str], str] = field(default_factory=dict)
    update_owner_pins: dict[tuple[str, str], str | None] = field(default_factory=dict)
    wakes: list[tuple[str, str]] = field(default_factory=list)
    suspended: list[tuple[str, str]] = field(default_factory=list)
    purged: list[tuple[str, str]] = field(default_factory=list)

    async def ensure_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> str:
        name = f"plugin-{plugin_id}-{company_id}"
        key = (company_id, plugin_id)
        self.deployments[key] = name
        self.deployment_instance_ids[key] = plugin_instance_id
        self.artifact_pins[key] = artifact_digest
        self.update_owner_pins.setdefault(key, None)
        return name

    async def update_deployment(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        artifact_digest: str,
        expected_current_artifact_digest: str,
        update_owner_token: str | None,
        expected_current_update_owner_token: str | None,
        dsn_ref: str,
        webhook_secret_ref: str,
    ) -> str:
        key = (company_id, plugin_id)
        live_digest = self.artifact_pins.get(key)
        live_owner = self.update_owner_pins.get(key)
        same_owner_replay = (
            live_digest == artifact_digest and live_owner == update_owner_token
        )
        if not same_owner_replay:
            if (
                live_digest != expected_current_artifact_digest
                or (
                    expected_current_update_owner_token is not None
                    and live_owner != expected_current_update_owner_token
                )
            ):
                errors = _require_module("prodavan.application.plugins.admin.errors")
                raise errors.PluginAdminError(
                    code="PLUGIN_UPDATE_CONFLICT",
                    detail="live artifact digest or update owner changed",
                    status=409,
                    extra={"patch_attempted": False},
                )
            self.artifact_pins[key] = artifact_digest
            self.update_owner_pins[key] = update_owner_token
        name = f"plugin-{plugin_id}-{company_id}"
        self.deployments[key] = name
        self.deployment_instance_ids[key] = plugin_instance_id
        return name

    async def suspend(self, *, company_id: str, plugin_id: str) -> bool:
        self.suspended.append((company_id, plugin_id))
        return True

    async def wake(self, *, company_id: str, plugin_id: str) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        self.wakes.append((company_id, plugin_id))
        return contracts.WakeResult(ready=True, replicas=1, waited_ms=1)

    async def runtime_status(self, *, company_id: str, plugin_id: str) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        return contracts.PluginRuntimeStatus.IDLE

    async def runtime_binding(self, *, company_id: str, plugin_id: str) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        key = (company_id, plugin_id)
        if key not in self.deployments or key not in self.artifact_pins:
            raise RuntimeError("live Deployment binding is absent")
        return contracts.PluginRuntimeBinding(
            plugin_instance_id=self.deployment_instance_ids[key],
            artifact_digest=self.artifact_pins[key],
            update_owner_token=self.update_owner_pins.get(key),
        )

    async def purge_infra(self, *, company_id: str, plugin_id: str) -> bool:
        self.purged.append((company_id, plugin_id))
        key = (company_id, plugin_id)
        self.deployments.pop(key, None)
        self.deployment_instance_ids.pop(key, None)
        self.artifact_pins.pop(key, None)
        self.update_owner_pins.pop(key, None)
        return True


@dataclass
class FakeMintPort:
    """``admin.MintPort``: короткий PluginClaims БЕЗ egress/квот в теле.

    Выпускает реальный HS256-токен через ``identity.mint``, чтобы downstream
    (capabilities/transport/gateway) мог его проверить тем же KeyRing'ом.
    """

    secret: str = TEST_SECRET
    now_epoch: int = FIXED_EPOCH
    issued: list[Any] = field(default_factory=list)

    async def mint(self, *, request: Any) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        grant = FakeGrant(
            grant_id=request.grant_id,
            plugin_id=request.plugin_id,
            company_id=request.company_id,
            scopes=tuple(request.scopes),
            manifest_version=request.manifest_version,
            artifact_digest=request.artifact_digest,
            quota_ref=request.quota_ref,
        )
        instance = FakeInstance(plugin_instance_id=request.plugin_instance_id)
        token = _sign_claims(grant=grant, instance=instance, now_epoch=FIXED_EPOCH)
        issued = contracts.IssuedPluginClaims(
            token=token,
            grant_id=request.grant_id,
            jti="acceptance-jti",
            expires_at=FIXED_NOW + timedelta(seconds=int(request.ttl_seconds)),
            ttl_seconds=int(request.ttl_seconds),
        )
        self.issued.append(issued)
        return issued


@dataclass
class FakePlatformAdmin:
    """``admin.PlatformAdminPort``: тестовый PlatformAdmin."""

    async def require_platform_admin(
        self, *, authorization: str | None
    ) -> Any:
        contracts = _require_module("prodavan.application.plugins.admin.contracts")
        return contracts.AdminIdentity(
            actor_id="acceptance-admin", is_platform_admin=True
        )


# ---------------------------------------------------------------------------
# Activation ports (Scale / Readiness / Mint / StateStore / Proxy)
# ---------------------------------------------------------------------------


@dataclass
class FakeScale:
    """``activation.ScalePort``: фиксирует КАЖДЫЙ PATCH ``/spec/replicas``."""

    replicas: dict[str, int] = field(default_factory=dict)
    calls: list[tuple[str, int]] = field(default_factory=list)

    async def current_replicas(self, plugin_instance_id: str) -> int:
        return self.replicas.get(plugin_instance_id, 0)

    async def scale_to(self, plugin_instance_id: str, replicas: int) -> None:
        self.replicas[plugin_instance_id] = replicas
        self.calls.append((plugin_instance_id, replicas))


@dataclass
class FakeReadiness:
    """``activation.ReadinessPort``: Ready наступает по явному флагу теста."""

    ready: dict[str, bool] = field(default_factory=dict)
    polls: list[str] = field(default_factory=list)

    async def is_ready(self, plugin_instance_id: str) -> bool:
        self.polls.append(plugin_instance_id)
        return bool(self.ready.get(plugin_instance_id, False))


@dataclass
class FakeMint:
    """``activation.MintPort``: НОВЫЙ короткий токен при каждом wake (кр. 13)."""

    secret: str = TEST_SECRET
    clock: FakeClock | None = None
    counter: int = 0
    issued: list[Any] = field(default_factory=list)

    async def mint(self, *, plugin_instance_id: str, reason: str) -> Any:
        ports = _require_module("prodavan.application.plugins.activation.ports")
        self.counter += 1
        now = self.clock.now() if self.clock is not None else FIXED_NOW
        grant = FakeGrant(
            grant_id=TEST_GRANT_ID,
            plugin_id=TEST_PLUGIN,
            company_id=TEST_COMPANY,
            scopes=("cache", "objects", "events", "network"),
            manifest_version=TEST_VERSION,
            artifact_digest=TEST_DIGEST,
        )
        instance = FakeInstance(plugin_instance_id=plugin_instance_id)
        # Токен ВСЕГДА новый (свой jti из build_payload) и короткий; время
        # выпуска согласуется с FakeClock, чтобы не дать ложный EXPIRED.
        token = _sign_claims(
            grant=grant,
            instance=instance,
            now_epoch=int(now.timestamp()),
            ttl_seconds=300,
        )
        result = ports.MintedClaims(
            token=token,
            jti=f"wake-jti-{self.counter}",
            issued_at=now,
            expires_at=now + timedelta(seconds=300),
            claims={"grant_id": TEST_GRANT_ID, "typ": "plugin_capability"},
        )
        self.issued.append(result)
        return result


@dataclass
class FakeProxy:
    """``activation.ProxyPort``: проксирование удержанного запроса."""

    forwarded: list[str] = field(default_factory=list)

    async def forward(
        self, *, plugin_instance_id: str, request: Any, claims: Any
    ) -> Any:
        self.forwarded.append(plugin_instance_id)
        return {"status": 200, "token_seen": bool(claims.token)}


# ---------------------------------------------------------------------------
# Objects / Events / Gateway / Cosign / TopicAdmin / BucketAdmin / Sts
# ---------------------------------------------------------------------------


@dataclass
class FakeBucketAdmin:
    """``objects.BucketAdminPort``: in-memory бакеты, без MinIO."""

    buckets: dict[str, dict[str, Any]] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    owns_bucket: bool = True

    async def ensure_bucket(
        self, bucket: str, *, policy: dict, quota_bytes: int
    ) -> None:
        self.buckets[bucket] = {"policy": policy, "quota_bytes": quota_bytes}

    async def delete_bucket(self, bucket: str) -> None:
        self.deleted.append(bucket)
        self.buckets.pop(bucket, None)

    async def delete_prefix(self, bucket: str, prefix: str) -> int:
        return 0

    async def set_quota(self, bucket: str, quota_bytes: int) -> None:
        self.buckets.setdefault(bucket, {})["quota_bytes"] = quota_bytes

    async def get_usage(self, bucket: str) -> int:
        return 0

    async def list_prefix(self, bucket: str, prefix: str) -> list[Any]:
        return []


@dataclass
class FakeSts:
    """``objects.StsPort``: short-lived scoped creds СТРОГО на свой префикс."""

    now_epoch: float = float(FIXED_EPOCH)
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def assume_role(
        self,
        *,
        company_id: str,
        plugin_id: str,
        ttl_seconds: int,
        session_policy: dict,
    ) -> Any:
        ports = _require_module("prodavan.application.plugins.objects.ports")
        iam = _require_module("prodavan.application.plugins.objects.policy")
        self.calls.append(
            {
                "company_id": company_id,
                "plugin_id": plugin_id,
                "ttl_seconds": ttl_seconds,
            }
        )
        return ports.ScopedCredentials(
            access_key="ASIA_ACCEPTANCE",
            secret_key="scoped-secret",
            session_token="scoped-session",
            bucket=iam.bucket_name(company_id),
            prefix=iam.module_prefix(plugin_id),
            expires_at=self.now_epoch + ttl_seconds,
            policy=session_policy,
        )


@dataclass
class FakeCosign:
    """``artifacts.CosignPort``: keyless-подпись подтверждена по digest."""

    verified: bool = True
    sbom: bool = True
    provenance: bool = True
    signed_digest: str | None = None
    calls: list[str] = field(default_factory=list)

    def verify(self, image_ref: str, identity: str, issuer: str) -> Any:
        verification = _require_module(
            "prodavan.application.plugins.artifacts.verification"
        )
        self.calls.append(image_ref)
        digest = self.signed_digest
        if digest is None and "@" in image_ref:
            digest = image_ref.split("@", 1)[1]
        return verification.VerificationResult(
            verified=self.verified,
            signed_digest=digest,
            identity=identity,
            issuer=issuer,
            sbom_verified=self.sbom,
            provenance_verified=self.provenance,
        )


class FakeManifestValidator:
    """``artifacts.ManifestValidatorPort``: позитивный валидатор без сети."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, data: dict[str, Any]) -> None:
        self.calls.append(dict(data))
        return None


@dataclass
class FakeTopicAdmin:
    """``events.TopicAdminPort``: явное создание топика + DLQ (auto-create off)."""

    existing: set[str] = field(default_factory=set)
    created: list[str] = field(default_factory=list)

    async def topic_exists(self, topic: str) -> bool:
        return topic in self.existing

    async def create_topic(
        self,
        topic: str,
        *,
        num_partitions: int,
        replication_factor: int,
    ) -> None:
        self.created.append(topic)
        self.existing.add(topic)


@dataclass
class FakeProducer:
    """``events.ProducerPort``: отправка + подтверждающий flush."""

    sent: list[dict[str, Any]] = field(default_factory=list)
    flushes: int = 0

    async def send(
        self,
        *,
        topic: str,
        key: str,
        value: Any,
        headers: Any = None,
    ) -> None:
        self.sent.append({"topic": topic, "key": key, "value": value})

    async def flush(self) -> None:
        self.flushes += 1


@dataclass
class FakeRecord:
    """``events.ConsumedRecord``: signed wire либо исходные malformed bytes."""

    value: Any
    topic: str = "agentscale.plugin.events"
    partition: int = 0
    offset: int = 17
    key: bytes | None = b"co_alpha"
    raw_value: bytes | None = None


@dataclass
class FakeConsumer:
    """``events.ConsumerPort``: РУЧНОЙ commit/seek, ``poll`` = ``None`` на простое.

    Именно на этом фейке проверяется критерий №20: при ``poll() -> None``
    НЕ должно быть ни одного ``commit``. Событие хранится в ``records`` и
    будет возвращено повторно после ``seek`` (эмулирует ``earliest``).
    """

    records: list[Any] = field(default_factory=list)
    commits: list[int] = field(default_factory=list)
    seeks: list[int] = field(default_factory=list)
    polls: int = 0
    ownership: bool = True

    def delivery_ownership(self, record: Any) -> Any:
        from prodavan.application.plugins.events.contracts import ConsumerAssignment
        return ConsumerAssignment(
            topic=record.topic,
            partition=record.partition,
            owned=self.ownership,
            offset=record.offset,
        )

    async def poll(self, *, timeout_ms: int = 1000) -> Any | None:
        self.polls += 1
        if not self.records:
            return None
        return self.records.pop(0)

    async def commit(self, offset: int) -> None:
        self.commits.append(offset)

    async def seek(self, offset: int) -> None:
        self.seeks.append(offset)

    async def close(self) -> None:
        return None


@dataclass
class FakeDlq:
    """``events.DlqPort``: запись «мёртвых» событий без raw payload."""

    published: list[Any] = field(default_factory=list)

    async def publish(self, record: Any) -> None:
        self.published.append(record)


@dataclass
class FakeEventSecretStore:
    """Минимальный read-only SecretStore для test-only event signer."""

    values: dict[str, str] = field(
        default_factory=lambda: {TEST_EVENT_SECRET_REF: TEST_EVENT_SIGNING_KEY}
    )

    def get(self, ref: str) -> str:
        if ref not in self.values:
            raise KeyError(ref)
        return self.values[ref]


@dataclass
class FakeRawDlq:
    """Durable RawDlq double: bool ack и идемпотентность по natural key."""

    records: list[Any] = field(default_factory=list)
    publish_calls: list[Any] = field(default_factory=list)
    seen_keys: set[Any] = field(default_factory=set)
    fail: bool = False
    fail_after_write: bool = False

    async def publish(self, record: Any) -> bool:
        self.publish_calls.append(record)
        if self.fail:
            raise RuntimeError("raw dlq down")
        key = record.idempotency_key
        if key in self.seen_keys:
            return False
        self.seen_keys.add(key)
        self.records.append(record)
        if self.fail_after_write:
            raise RuntimeError("raw dlq acknowledgement lost")
        return True


@dataclass
class FakeSubscriptions:
    """Подписки и active флаги строго keyed by id, без cross-id fallback."""

    records: dict[str, Any] = field(default_factory=dict)
    active: dict[str, bool] = field(default_factory=dict)

    def register(self, subscription: Any, *, active: bool = True) -> None:
        self.records[subscription.id] = subscription
        self.active[subscription.id] = active

    async def get(self, subscription_id: str) -> Any | None:
        return self.records.get(subscription_id)

    async def is_active(self, subscription_id: str) -> bool:
        return self.active.get(subscription_id, False)


@dataclass
class FakeSeenStore:
    """Одно хранилище и для продюсера (str), и для консьюмера (IdempotencyKey)."""

    seen_keys: set[Any] = field(default_factory=set)
    remembered: list[Any] = field(default_factory=list)

    async def seen(self, key: Any) -> bool:
        return key in self.seen_keys

    async def remember(self, key: Any) -> None:
        self.seen_keys.add(key)
        self.remembered.append(key)


@dataclass
class FakeEventHandler:
    """``events.EventHandlerPort``: успешная обработка по умолчанию."""

    calls: list[Any] = field(default_factory=list)
    fail: bool = False

    async def handle(self, event: Any, *, subscription: Any) -> None:
        self.calls.append(event)
        if self.fail:
            raise RuntimeError("backend unavailable")


@dataclass
class FakeWakeHook:
    """``events.WakeHookPort``: пробуждение backend ДО доставки."""

    calls: list[Any] = field(default_factory=list)

    async def wake(self, event: Any, *, subscription: Any) -> None:
        self.calls.append(event)


@dataclass
class FakeGatewaySessions:
    """``gateway.SessionPort``: сессия сотрудника + employee ∈ company."""

    company_ids: frozenset[str] = frozenset({TEST_COMPANY})
    session: Any | None = None

    async def resolve(self, *, session_token: str | None) -> Any | None:
        if session_token is None:
            return None
        ports = _require_module("prodavan.application.plugins.gateway.ports")
        if self.session is not None:
            return self.session
        return ports.GatewaySession(
            session_id="sess-1",
            employee_id="emp-1",
            subject_id="user-1",
            company_ids=self.company_ids,
        )

    async def employee_in_company(
        self, *, employee_id: str, company_id: str
    ) -> bool:
        return company_id in self.company_ids


@dataclass
class FakeGatewayGrants:
    """``gateway.GrantPort``: активный grant или ``None`` после revoke.

    Тот же ``FakeRevocation`` решает, активен ли grant: стык «revoke → gateway
    отклоняет запрос» проверяется ОДНИМ состоянием на две зоны.
    """

    revocation: FakeRevocation
    plugin_instance_id: str
    grant_id: str = TEST_GRANT_ID
    declared_public_pages: frozenset[str] = frozenset()
    #: Exact deterministic bundle identity shared with the durable FakeGrant.
    artifact_digest: str = TEST_DIGEST
    published_artifact_digest: str = TEST_DIGEST

    async def active_grant(
        self, *, plugin_id: str, company_id: str
    ) -> Any | None:
        ports = _require_module("prodavan.application.plugins.gateway.ports")
        if not await self.revocation.is_active(self.grant_id):
            return None
        return ports.GatewayGrant(
            grant_id=self.grant_id,
            plugin_instance_id=self.plugin_instance_id,
            artifact_digest=self.artifact_digest,
            published_artifact_digest=self.published_artifact_digest,
            active=True,
            # Acceptance models public/static evidence, never backend authority.
            capability_token="",
        )

    async def public_pages(
        self, *, plugin_id: str, company_id: str
    ) -> frozenset[str]:
        return self.declared_public_pages


@dataclass
class FakeGatewaySecrets:
    """``gateway.SecretPort``: HMAC-секрет из «secret store», не из кода."""

    secret: str = "webhook-hmac-secret"

    async def webhook_secret(
        self, *, plugin_id: str, company_id: str
    ) -> str | None:
        return self.secret


@dataclass
class FakeRateLimit:
    """Production-shaped delivery lease/commit/release, без legacy fallback."""

    allowed: bool = True
    processing: dict[str, str] = field(default_factory=dict)
    done: dict[str, str] = field(default_factory=dict)
    claim_calls: list[dict[str, Any]] = field(default_factory=list)
    commit_calls: list[dict[str, Any]] = field(default_factory=list)
    release_calls: list[dict[str, Any]] = field(default_factory=list)

    async def allow(
        self, *, bucket: str, limit: int, window_seconds: int
    ) -> bool:
        return self.allowed

    async def claim_delivery_lease(
        self, *, key: str, ttl_seconds: int, lease_seconds: int, token: str
    ) -> Any:
        if not key or ttl_seconds < 1 or lease_seconds < 1 or not token:
            raise ValueError("invalid delivery lease arguments")
        self.claim_calls.append({
            "key": key,
            "ttl_seconds": ttl_seconds,
            "lease_seconds": lease_seconds,
            "token": token,
        })
        ports = _require_module("prodavan.application.plugins.gateway.ports")
        if key in self.done:
            return ports.DeliveryVerdict(
                status=ports.DeliveryStatus.DUPLICATE,
                previous_token=self.done[key],
            )
        if key in self.processing:
            return ports.DeliveryVerdict(
                status=ports.DeliveryStatus.IN_PROGRESS,
                previous_token=self.processing[key],
                retry_after_seconds=lease_seconds,
            )
        self.processing[key] = token
        return ports.DeliveryVerdict(status=ports.DeliveryStatus.FIRST)

    async def commit_delivery(self, *, key: str, ttl_seconds: int, token: str) -> bool:
        if not key or ttl_seconds < 1 or not token:
            raise ValueError("invalid delivery commit arguments")
        self.commit_calls.append({"key": key, "ttl_seconds": ttl_seconds, "token": token})
        if self.done.get(key) == token:
            return True
        if self.processing.get(key) != token:
            return False
        self.processing.pop(key)
        self.done[key] = token
        return True

    async def release_delivery(self, *, key: str, token: str) -> None:
        if not key or not token:
            raise ValueError("invalid delivery release arguments")
        self.release_calls.append({"key": key, "token": token})
        if self.processing.get(key) == token:
            self.processing.pop(key)


# ---------------------------------------------------------------------------
# «Собранная платформа» (wiring всех сервисов из application/plugins/**)
# ---------------------------------------------------------------------------


@dataclass
class Platform:
    """Связка фейков + реальные сервисы, собранные ЛЕНИВО.

    Каждый сервис создаётся только по требованию (``pytest.skip`` при
    отсутствии модуля). Поле ``capabilities`` доступно как свойство — см. ниже.
    """

    clock: FakeClock
    revocation: FakeRevocation
    audit: FakeAudit
    quota: FakeQuota
    cache_adapter: FakeCacheAdapter
    instance_store: FakeInstanceStore
    grant_store: FakeGrantStore
    installation_scopes: FakeInstallationScopeStore
    webhook_secrets: FakeWebhookSecretStore
    publication_approvals: FakePublicationApprovalStore
    secret_delivery: FakeSecretDeliveryControl
    tenant_db: FakeTenantDb
    objects_admin: FakeObjectStorePort
    artifact_admin: FakeArtifactRegistryPort
    routes: FakeRoutes
    activation_admin: FakeActivationPort
    update_lock: FakeUpdateLock
    mint_admin: FakeMintPort
    platform_admin: FakePlatformAdmin
    scale: FakeScale
    readiness: FakeReadiness
    mint_activation: FakeMint
    proxy: FakeProxy
    bucket_admin: FakeBucketAdmin
    sts: FakeSts
    cosign: FakeCosign
    validator: FakeManifestValidator
    topic_admin: FakeTopicAdmin
    producer: FakeProducer
    consumer: FakeConsumer
    dlq: FakeDlq
    raw_dlq: FakeRawDlq
    event_secrets: FakeEventSecretStore
    subscriptions: FakeSubscriptions
    published_events: FakeSeenStore
    idempotency: FakeSeenStore
    handler: FakeEventHandler
    wake_hook: FakeWakeHook
    gateway_sessions: FakeGatewaySessions
    gateway_grants: FakeGatewayGrants
    gateway_secrets: FakeGatewaySecrets
    rate_limit: FakeRateLimit
    _event_signer: Any = field(default=None, init=False, repr=False)

    # --- ленивые сборщики сервисов ----------------------------------------

    def capability_service(self) -> Any:
        """``PluginCapabilityService`` с ОБЩИМ реестром отзывов и квотой."""

        caps = _require_module("prodavan.application.plugins.capabilities")
        registry = caps.AdapterRegistry()
        registry.register("cache", self.cache_adapter)
        return caps.PluginCapabilityService(
            registry=registry,
            grant_store=self.grant_store,
            quota=self.quota,
            audit=self.audit,
            revocation=self.revocation,
        )

    def admin_service(self, *, artifact_registry: Any = None) -> Any:
        """``PluginAdminService`` со всеми портами (fail-closed по конструкции).

        ``artifact_registry`` позволяет подключить production
        ``ArtifactRegistryAdapter`` поверх РЕАЛЬНОГО CI-реестра вместо legacy
        ``seed_published()``-фейка (полный CI approval/publish/grant contract).
        """

        ports_mod = _require_module("prodavan.application.plugins.admin.ports")
        service_mod = _require_module("prodavan.application.plugins.admin.service")
        ports = ports_mod.PluginAdminPorts(
            tenant_db=self.tenant_db,
            object_store=self.objects_admin,
            artifact_registry=(
                self.artifact_admin if artifact_registry is None else artifact_registry
            ),
            instance_store=self.instance_store,
            grant_store=self.grant_store,
            webhook_secrets=self.webhook_secrets,
            revocation=FakeAdminRevocation(self.revocation),
            mint=self.mint_admin,
            activation=self.activation_admin,
            routes=self.routes,
            audit=_AsyncAdminAudit(self.audit),
            platform_admin=self.platform_admin,
            publication_approvals=self.publication_approvals,
            secret_delivery=self.secret_delivery,
            installation_scopes=self.installation_scopes,
            update_lock=self.update_lock,
        )
        return service_mod.PluginAdminService(
            ports,
            clock=lambda: self.clock.now(),
            id_factory=_next_id,
        )

    def activator(self) -> Any:
        """``PluginActivator`` со Scale/Readiness/Clock/Mint/Audit/Proxy."""

        activation = _require_module("prodavan.application.plugins.activation")
        return activation.PluginActivator(
            scale=self.scale,
            readiness=self.readiness,
            clock=self.clock,
            mint=self.mint_activation,
            audit=self.audit,
            policy=activation.IdlePolicy.defaults(),
            # Явный test/dev double: InMemoryStateStore больше НЕ production default.
            state_store=activation.InMemoryStateStore(),
            proxy=self.proxy,
        )

    def artifact_registry(self) -> Any:
        """``PluginArtifactRegistry`` + ``ArtifactVerifier`` с одним cosign."""

        artifacts = _require_module("prodavan.application.plugins.artifacts")
        verifier = artifacts.ArtifactVerifier(
            self.cosign,
            validator=self.validator,
            identity=IDENTITY,
            issuer=ISSUER,
            compatibility=artifacts.CompatibilityGate(platform_version="4.0.0"),
            clock=lambda: self.clock.now(),
        )
        return artifacts.PluginArtifactRegistry(
            store=artifacts.InMemoryRegistryStore(),
            verifier=verifier,
            clock=lambda: self.clock.now(),
        )

    def event_signer(self) -> Any:
        """Ленивый HMAC signer с ключом только из test SecretStore ref."""

        if self._event_signer is None:
            dispatch = _require_module(
                "prodavan.application.plugins.events.dispatch"
            )
            self._event_signer = dispatch.HmacPluginEventSigner(
                secret_store=self.event_secrets,
                secret_ref=TEST_EVENT_SECRET_REF,
            )
        return self._event_signer

    def event_dispatcher(self) -> Any:
        """``PluginEventDispatcher`` со всеми обязательными портами."""

        events = _require_module("prodavan.application.plugins.events")
        return events.PluginEventDispatcher(
            producer=self.producer,
            consumer=self.consumer,
            topic_admin=self.topic_admin,
            dlq=self.dlq,
            raw_dlq=self.raw_dlq,
            subscriptions=self.subscriptions,
            published_events=self.published_events,
            idempotency=self.idempotency,
            delivery_claims=_EventDeliveryClaimFake(),
            audit=_AsyncEventsAudit(self.audit),
            handler=self.handler,
            wake_hook=self.wake_hook,
            signer=self.event_signer(),
        )

    def egress_resolver(self) -> Any:
        """``GrantPolicyResolver`` читает ТОТ ЖЕ grant_store, что и admin."""

        egress = _require_module("prodavan.application.plugins.egress")
        return egress.GrantPolicyResolver(self.grant_store)

    def gateway_auth(self) -> Any:
        """``GatewayAuth`` с общим реестром отзывов (revoke виден шлюзу)."""

        gateway = _require_module("prodavan.application.plugins.gateway")
        return gateway.GatewayAuth(
            sessions=self.gateway_sessions,
            grants=self.gateway_grants,
            secrets=self.gateway_secrets,
            rate_limit=self.rate_limit,
            audit=_AsyncGatewayAudit(self.audit),
        )

    def keyring(self) -> Any:
        identity = _require_module("prodavan.application.plugins.identity")
        return identity.keyring_from_secret(TEST_SECRET)

    def mint_claims(self, *, grant: FakeGrant | None = None) -> str:
        """Выпустить валидный PluginClaims с правильным ``typ`` (кр. 17/22).

        Время выпуска — FIXED_EPOCH (то же, что и ``FakeClock``): детерминизм
        важнее «реального» ``time.time()``, иначе стык activation↔identity
        даст ложный EXPIRED.
        """

        return _sign_claims(grant=grant, now_epoch=FIXED_EPOCH, ttl_seconds=300)

    def verify_claims(self, token: str) -> Any:
        """Проверить подпись/claim тем же KeyRing'ом и тем же FIXED_EPOCH."""

        identity = _require_module("prodavan.application.plugins.identity")
        return identity.verify_plugin_token(
            token, keyring=self.keyring(), now=FIXED_EPOCH
        )

    def object_store(self) -> Any:
        """``PluginObjectStore`` (задача 06) на in-memory BucketAdmin/Sts."""

        objects = _require_module("prodavan.application.plugins.objects")
        return objects.PluginObjectStore(
            buckets=self.bucket_admin,
            sts=self.sts,
            audit=_AsyncObjectAudit(self.audit),
            clock=_ClockWithTime(self.clock),
        )

    def tenant_db_service(self) -> Any:
        """``TenantDatabaseService`` (задача 05) на fakes admin/secret/audit."""

        tenantdb = _require_module("prodavan.application.plugins.tenantdb")
        return tenantdb.TenantDatabaseService(
            admin=_AsyncAdminConnection(self.tenant_db),
            secrets=_AsyncSecretStore(),
            audit=_AsyncTenantAudit(self.audit),
            dsn_host="tenant-postgres",
            dsn_port=5432,
        )


# --- Тонкие async-адаптеры для портов с record/append -----------------------
# Activation НЕ входит сюда: FakeAudit.append_event сам awaitable и совпадает
# с production AuditPort без fallback на старый sync record.


@dataclass
class _AsyncAdminAudit:
    """``admin.AuditPort.append(*, event)``."""

    inner: FakeAudit

    async def append(self, *, event: Any) -> bool:
        return self.inner.append(event)


class _EventDeliveryClaimFake:
    """Test-only lease fake; production uses the Redis claim adapter."""

    def __init__(self) -> None:
        self.active: dict[Any, Any] = {}
        self.completed: set[Any] = set()
        self.generation = 0

    async def acquire(self, key: Any, *, lease_seconds: float) -> Any:
        if key in self.active and key not in self.completed:
            return None
        self.generation += 1
        contracts = _require_module("prodavan.application.plugins.events.contracts")
        claim = contracts.DeliveryClaim(
            key=key,
            owner_token=f"acceptance-owner-{self.generation}",
            generation=self.generation,
            lease_seconds=lease_seconds,
            completed=key in self.completed,
        )
        self.active[key] = claim
        return claim

    async def renew(self, claim: Any) -> bool:
        return self.active.get(claim.key) == claim

    async def complete(self, claim: Any) -> bool:
        if self.active.get(claim.key) != claim:
            return False
        self.completed.add(claim.key)
        return True

    async def release(self, claim: Any) -> bool:
        if self.active.get(claim.key) != claim:
            return False
        self.active.pop(claim.key, None)
        return True


@dataclass
class _AsyncEventsAudit:
    """``events.AuditPort.append(event_type, *, details)``."""

    inner: FakeAudit

    async def append(self, event_type: str, *, details: Any) -> None:
        self.inner._typed(event_type, dict(details))


@dataclass
class _AsyncGatewayAudit:
    """``gateway.AuditPort.record(event)``."""

    inner: FakeAudit

    async def record(self, event: Any) -> None:
        self.inner.record(event)


@dataclass
class _AsyncObjectAudit:
    """``objects.AuditPort.record(event: dict)``."""

    inner: FakeAudit

    async def record(self, event: dict) -> None:
        self.inner.record(event)


@dataclass
class _AsyncTenantAudit:
    """``tenantdb.AuditPort.record(*, event, company_id, plugin_id, payload)``."""

    inner: FakeAudit

    async def record(
        self, *, event: str, company_id: str, plugin_id: str, payload: Any
    ) -> None:
        self.inner.record(
            {
                "event": event,
                "company_id": company_id,
                "plugin_id": plugin_id,
                **dict(payload),
            }
        )


@dataclass
class _ClockWithTime:
    clock: FakeClock

    def time(self) -> float:
        return float(self.clock.epoch())


@dataclass
class _AsyncSecretStore:
    """``tenantdb.SecretPort``: DSN НЕ покидает хранилище, только secret_ref."""

    values: dict[str, str] = field(default_factory=dict)

    async def put(self, *, key: str, value: str) -> str:
        self.values[key] = value
        return f"secret_ref:{key}"

    async def rotate(self, *, key: str, value: str) -> str:
        self.values[key] = value
        return f"secret_ref:{key}:rotated"

    async def delete(self, *, secret_ref: str) -> None:
        return None


@dataclass
class _AsyncAdminConnection:
    """Stateful ``tenantdb.AdminConnectionPort`` с журналом вызовов.

    Общий unit fake применяет DDL к in-memory объектам, поэтому post-state
    probes видят реальное для fake состояние; ``executed`` сохраняет имена и
    target для проверки изоляции uninstall между компаниями.
    """

    tenant_db: FakeTenantDb
    executed: list[tuple[str, Any]] = field(default_factory=list)
    _state: Any = field(init=False, repr=False)

    def __post_init__(self) -> None:
        from tests.unit.plugins.tenantdb.conftest import FakeAdminConnection

        self._state = FakeAdminConnection()

    async def execute(
        self, text: str, params: Any = None, *, target: Any
    ) -> Any:
        self.executed.append((text, getattr(target, "database", "")))
        return await self._state.execute(text, params, target=target)


# ---------------------------------------------------------------------------
# pytest-фикстуры
# ---------------------------------------------------------------------------


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def revocation() -> FakeRevocation:
    return FakeRevocation()


@pytest.fixture
def audit() -> FakeAudit:
    return FakeAudit()


@pytest.fixture
def grant() -> FakeGrant:
    return FakeGrant()


@pytest.fixture
def instance() -> FakeInstance:
    return FakeInstance()


@pytest.fixture
def valid_claims(platform: "Platform") -> Any:
    """Валидный ``PluginClaims``: mint → verify с правильным ``typ``.

    Это НЕ доменный ``domain.plugins.types.PluginClaims``, а проверенный
    identity-контур (``application/plugins/identity/types.py``): именно его
    ждёт ``PluginCapabilityService.invoke`` и ``transport.deps``.
    """

    return platform.verify_claims(platform.mint_claims())


@pytest.fixture
def valid_manifest() -> dict[str, Any]:
    """Подписанный plugin-manifest v1 (совместим с artifacts/conftest).

    Вариант A выравнивания gate (docs/migration/plugin-manifest-gate-alignment.md):
    в подписанном JSON РАЗРЕШЕНЫ ровно 11 полей whitelist. `capabilities` —
    каноническое поле; `requestedCapabilities` и `frontend.csp` ЗАПРЕЩЕНЫ
    (`validate_manifest` их отвергнет). `scan_verified` / `csp_verified`
    приходят ИЗВНЕ из CI и static-origin, а не из манифеста.
    """

    return {
        "plugin_id": TEST_PLUGIN,
        "version": TEST_VERSION,
        "protocol": "plugin-rpc/1",
        "min_platform_version": "4.0.0",
        "capabilities": ["cache", "objects", "events", "network"],
        "frontend": {
            "entry": "index.html",
            "base_path": "/",
        },
        "backend": {"port": 8080, "health": "/health"},
        "migrations": {
            "engine": "liquibase",
            "path": "migrations/liquibase/changelog.xml",
        },
        "egress_ref": f"grant:{TEST_GRANT_ID}",
        "public_pages": [],
        "webhooks": [],
    }


@pytest.fixture
def oci_ref() -> Any:
    """``OciArtifactRef`` по digest (НЕ ``:latest``), кр. 18."""

    artifacts = _require_module("prodavan.application.plugins.artifacts")
    return artifacts.OciArtifactRef(
        registry="ghcr.io",
        repository=f"ne-tort/plugins/{TEST_PLUGIN}",
        digest=TEST_DIGEST,
    )


@pytest.fixture
def platform(clock: FakeClock, revocation: FakeRevocation, audit: FakeAudit) -> Platform:
    """«Собранная платформа»: ОДИН набор фейков на ВСЕ сервисы.

    Все зоны разделяют ``revocation``, ``grant_store``, ``audit`` и ``clock`` —
    именно так проявляются межмодульные контракты: отзыв в admin мгновенно
    виден capabilities и gateway; egress читает тот же grant, что создал admin.

    Сервисы НЕ создаются в фикстуре (ленивые сборщики): если какой-то модуль
    ещё отсутствует, skip будет ТОЧЕЧНЫМ (в конкретном тесте), а не падением
    коллекции всего acceptance-пакета.
    """

    return Platform(
        clock=clock,
        revocation=revocation,
        audit=audit,
        quota=FakeQuota(),
        cache_adapter=FakeCacheAdapter(),
        instance_store=FakeInstanceStore(),
        grant_store=FakeGrantStore(),
        installation_scopes=FakeInstallationScopeStore(),
        webhook_secrets=FakeWebhookSecretStore(),
        publication_approvals=FakePublicationApprovalStore(),
        secret_delivery=FakeSecretDeliveryControl(),
        tenant_db=FakeTenantDb(),
        objects_admin=FakeObjectStorePort(),
        artifact_admin=FakeArtifactRegistryPort(),
        routes=FakeRoutes(),
        activation_admin=FakeActivationPort(),
        update_lock=FakeUpdateLock(),
        mint_admin=FakeMintPort(),
        platform_admin=FakePlatformAdmin(),
        scale=FakeScale(),
        readiness=FakeReadiness(),
        mint_activation=FakeMint(clock=clock),
        proxy=FakeProxy(),
        bucket_admin=FakeBucketAdmin(),
        sts=FakeSts(),
        cosign=FakeCosign(),
        validator=FakeManifestValidator(),
        topic_admin=FakeTopicAdmin(),
        producer=FakeProducer(),
        consumer=FakeConsumer(),
        dlq=FakeDlq(),
        raw_dlq=FakeRawDlq(),
        event_secrets=FakeEventSecretStore(),
        subscriptions=FakeSubscriptions(),
        published_events=FakeSeenStore(),
        idempotency=FakeSeenStore(),
        handler=FakeEventHandler(),
        wake_hook=FakeWakeHook(),
        gateway_sessions=FakeGatewaySessions(),
        gateway_grants=FakeGatewayGrants(
            revocation, plugin_instance_id=TEST_INSTANCE
        ),
        gateway_secrets=FakeGatewaySecrets(),
        rate_limit=FakeRateLimit(),
    )


# ---------------------------------------------------------------------------
# Вспомогательные builders для тестов
# ---------------------------------------------------------------------------


def make_cache_get_request(*, key: str = "acceptance-key") -> Any:
    """Типизированный ``CacheGetRequest`` с серверным envelope-заглушкой."""

    contracts = _require_module(
        "prodavan.application.plugins.capabilities.contracts"
    )
    return contracts.CacheGetRequest(
        key=key,
        envelope=contracts.CapabilityEnvelope(
            request_id="req-acceptance-1",
            company_id=TEST_COMPANY,
            subject_id=f"plugin:{TEST_PLUGIN}:{TEST_COMPANY}:{TEST_INSTANCE}",
            plugin_instance_id=TEST_INSTANCE,
            artifact_digest=TEST_DIGEST,
            grant_id=TEST_GRANT_ID,
        ),
    )


def make_capability_set(*, capabilities: tuple[str, ...] = ("cache", "objects")) -> Any:
    """``admin.CapabilitySet`` для grant (типизированный, fail-closed)."""

    contracts = _require_module("prodavan.application.plugins.admin.contracts")
    return contracts.CapabilitySet(capabilities=capabilities)


def make_event(*, event_id: str = "evt-acceptance-1") -> Any:
    """Валидное ``PluginEvent`` БЕЗ raw payload (только payload_digest)."""

    events = _require_module("prodavan.application.plugins.events")
    return events.PluginEvent(
        event_id=event_id,
        source_event_id=event_id,
        event_type="offer.created",
        source="offers",
        company_id=TEST_COMPANY,
        subscription_id="sub_acceptance",
        occurred_at=FIXED_NOW,
        payload_digest=TEST_DIGEST,
        run_id="run_acceptance",
        correlation_id="corr_acceptance",
        schema_version=events.PLUGIN_EVENT_SCHEMA_VERSION,
    )


def make_subscription() -> Any:
    events = _require_module("prodavan.application.plugins.events")
    return events.Subscription(
        id="sub_acceptance",
        plugin_id=TEST_PLUGIN,
        event_types=("offer.created",),
        consumer_group="plugin-mod_equipment-sub_acceptance",
    )


__all__ = [
    "FIXED_EPOCH",
    "FIXED_NOW",
    "IDENTITY",
    "ISSUER",
    "Platform",
    "TEST_COMPANY",
    "TEST_DIGEST",
    "TEST_EVENT_SECRET_REF",
    "TEST_GRANT_ID",
    "TEST_INSTANCE",
    "TEST_OTHER_COMPANY",
    "TEST_PLUGIN",
    "TEST_QUOTA_VALUES",
    "TEST_SECRET",
    "TEST_VERSION",
    "FakeActivationPort",
    "FakeAdminRevocation",
    "FakeArtifactRegistryPort",
    "FakeAudit",
    "FakeBucketAdmin",
    "FakeCacheAdapter",
    "FakeClock",
    "FakeConsumer",
    "FakeCosign",
    "FakeDlq",
    "FakeEventHandler",
    "FakeEventSecretStore",
    "FakeGatewayGrants",
    "FakeGatewaySecrets",
    "FakeGatewaySessions",
    "FakeGrant",
    "FakeGrantStore",
    "FakeInstallationScopeStore",
    "FakeInstance",
    "FakeInstanceStore",
    "FakeManifestValidator",
    "FakeMint",
    "FakeMintPort",
    "FakeObjectStorePort",
    "FakePlatformAdmin",
    "FakeProducer",
    "FakeProxy",
    "FakePublicationApprovalStore",
    "FakeQuota",
    "FakeRateLimit",
    "FakeRawDlq",
    "FakeReadiness",
    "FakeRecord",
    "FakeRevocation",
    "FakeRoutes",
    "FakeScale",
    "FakeSecretDeliveryControl",
    "FakeSeenStore",
    "FakeSts",
    "FakeSubscriptions",
    "FakeTenantDb",
    "FakeTopicAdmin",
    "FakeWakeHook",
    "make_cache_get_request",
    "make_capability_set",
    "make_event",
    "make_subscription",
]
