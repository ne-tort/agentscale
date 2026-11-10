"""Local fixtures for the real-Redis plugin acceptance harness.

This conftest is deliberately scoped to this directory.  It never selects an
application/dev/prod Redis endpoint and never flushes a Redis database.
"""
from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

import pytest
import pytest_asyncio
import redis.asyncio as redis

from .invocation_guard import validate_acceptance_invocation

REDIS_URL_ENV = "REDIS_PLUGIN_ACCEPTANCE_REDIS_URL"
REDIS_DB_ENV = "REDIS_PLUGIN_ACCEPTANCE_REDIS_DB"
REQUIRED_ENV = "REDIS_PLUGIN_ACCEPTANCE_REQUIRED"
CONFIRM_ENV = "REDIS_PLUGIN_ACCEPTANCE_CONFIRM_ISOLATED_TEST_ENV"
ISOLATED_CONFIRMATION = "I_OWN_ISOLATED_TEST_RESOURCES"


def _acceptance_required() -> bool:
    return os.getenv(REQUIRED_ENV, "").strip().lower() in {"1", "true", "yes", "on"}


def _missing_endpoint_env() -> list[str]:
    return [name for name in (REDIS_URL_ENV, REDIS_DB_ENV) if not os.getenv(name, "").strip()]


class NamespacedRedis:
    """Transparent key-prefixing proxy; all commands still execute in real Redis.

    Production stores have fixed logical prefixes.  Prefixing only their key and
    channel arguments gives every test a random namespace without changing the
    production adapter, Lua script, NX response, or CAS behavior under test.
    """

    def __init__(self, client: redis.Redis, namespace: str) -> None:
        self._client = client
        self._namespace = namespace
        self.nx_results: list[Any] = []
        self.initial_get_barriers: list[asyncio.Barrier] = []
        self.set_entry_barrier: asyncio.Barrier | None = None
        self.set_exit_barrier: asyncio.Barrier | None = None
        self.nx_miss_reread_values: list[tuple[str, Any]] = []
        self._last_nx_result: dict[str, Any] = {}

    def _key(self, key: str | bytes) -> str | bytes:
        if isinstance(key, bytes):
            return self._namespace.encode("utf-8") + b":" + key
        return f"{self._namespace}:{key}"

    @staticmethod
    async def _wait(barrier: asyncio.Barrier | None) -> None:
        if barrier is not None:
            await barrier.wait()

    async def get(self, key: str | bytes) -> Any:
        value = await self._client.get(self._key(key))
        task_name = asyncio.current_task().get_name()
        if self.initial_get_barriers:
            await self.initial_get_barriers.pop(0).wait()
        if (
            task_name in self._last_nx_result
            and (
                self._last_nx_result[task_name] is None
                or self._last_nx_result[task_name] is False
            )
        ):
            # Only a concrete None/False NX attempt arms one real re-read.
            self.nx_miss_reread_values.append((task_name, value))
            self._last_nx_result.pop(task_name, None)
        return value

    async def set(self, key: str | bytes, *args: Any, **kwargs: Any) -> Any:
        await self._wait(self.set_entry_barrier)
        result = await self._client.set(self._key(key), *args, **kwargs)
        task_name = asyncio.current_task().get_name()
        if kwargs.get("nx") is True:
            # Observability only: the unmodified redis.asyncio result is returned.
            self.nx_results.append(result)
            self._last_nx_result[task_name] = result
        else:
            self._last_nx_result.pop(task_name, None)
        await self._wait(self.set_exit_barrier)
        return result

    async def delete(self, *keys: str | bytes) -> Any:
        return await self._client.delete(*(self._key(key) for key in keys))

    async def eval(self, script: str, numkeys: int, *keys_and_args: Any) -> Any:
        if not isinstance(numkeys, int) or numkeys < 0 or numkeys > len(keys_and_args):
            raise ValueError("invalid Redis eval key count")
        keys = tuple(self._key(key) for key in keys_and_args[:numkeys])
        return await self._client.eval(script, numkeys, *keys, *keys_and_args[numkeys:])

    async def zcard(self, key: str | bytes) -> Any:
        return await self._client.zcard(self._key(key))

    async def pttl(self, key: str | bytes) -> Any:
        return await self._client.pttl(self._key(key))

    async def exists(self, *keys: str | bytes) -> Any:
        return await self._client.exists(*(self._key(key) for key in keys))

    async def publish(self, channel: str | bytes, message: str | bytes) -> Any:
        return await self._client.publish(self._key(channel), message)


@dataclass(slots=True)
class RedisPair:
    """Independent API/Gateway clients sharing one real Redis namespace."""

    api: NamespacedRedis
    gateway: NamespacedRedis
    namespace: str
    clients: tuple[redis.Redis, redis.Redis]

    async def mcp_registration_keys(self) -> list[bytes]:
        pattern = f"{self.namespace}:plugin:mcp:registration:*"
        return [key async for key in self.clients[0].scan_iter(match=pattern, count=100)]


async def _delete_own_namespace(client: redis.Redis, namespace: str) -> None:
    """Delete only this test's keys; FLUSHDB/FLUSHALL are intentionally absent."""

    pending: list[bytes] = []
    async for key in client.scan_iter(match=f"{namespace}:*", count=200):
        pending.append(key)
        if len(pending) == 200:
            await client.delete(*pending)
            pending.clear()
    if pending:
        await client.delete(*pending)


@pytest_asyncio.fixture
async def redis_pair() -> AsyncIterator[RedisPair]:
    missing = _missing_endpoint_env()
    if missing:
        pytest.skip("local-only skip: explicit test Redis endpoint is not configured")

    if os.getenv(CONFIRM_ENV) != ISOLATED_CONFIRMATION:
        pytest.fail(
            f"{CONFIRM_ENV} must exactly confirm an isolated test environment",
            pytrace=False,
        )
    try:
        parsed = urlsplit(os.environ[REDIS_URL_ENV])
        if parsed.scheme not in {"redis", "rediss"} or not parsed.netloc:
            raise ValueError("invalid Redis test URL")
    except Exception:
        pytest.fail(
            f"{REDIS_URL_ENV} must be a valid redis:// or rediss:// test URL",
            pytrace=False,
        )

    raw_db = os.environ[REDIS_DB_ENV].strip()
    try:
        database = int(raw_db)
    except ValueError:
        pytest.fail(f"{REDIS_DB_ENV} must be a non-negative integer", pytrace=False)
    if database < 0:
        pytest.fail(f"{REDIS_DB_ENV} must be a non-negative integer", pytrace=False)

    # Do not print the URL: it may contain test-only credentials.
    clients = tuple(
        redis.from_url(
            os.environ[REDIS_URL_ENV],
            db=database,
            decode_responses=False,
            socket_connect_timeout=3.0,
            socket_timeout=5.0,
        )
        for _ in range(2)
    )
    namespace = f"agentscale:redis-plugin-acceptance:{uuid4().hex}"
    pair = RedisPair(
        api=NamespacedRedis(clients[0], namespace),
        gateway=NamespacedRedis(clients[1], namespace),
        namespace=namespace,
        clients=clients,  # type: ignore[arg-type]
    )
    try:
        # A configured but unreachable endpoint is a failure, never a skip.
        await asyncio.gather(*(client.ping() for client in clients))
        yield pair
    finally:
        try:
            await _delete_own_namespace(clients[0], namespace)
        finally:
            await asyncio.gather(*(client.aclose() for client in clients))


def pytest_configure(config: pytest.Config) -> None:
    validate_acceptance_invocation(config)
    config.addinivalue_line(
        "markers", "redis_acceptance: requires the explicit isolated test Redis endpoint"
    )


def pytest_collection_finish(session: pytest.Session) -> None:
    if _acceptance_required() and (
        _missing_endpoint_env() or os.getenv(CONFIRM_ENV) != ISOLATED_CONFIRMATION
    ):
        raise pytest.UsageError(
            "acceptance requires REDIS_PLUGIN_ACCEPTANCE_REDIS_URL, "
            "REDIS_PLUGIN_ACCEPTANCE_REDIS_DB and explicit isolated-test confirmation; "
            "refusing a skipped run"
        )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """A required acceptance run is invalid when it collected or ran nothing."""

    if not _acceptance_required():
        return
    reporter = session.config.pluginmanager.get_plugin("terminalreporter")
    skipped = len(reporter.stats.get("skipped", ())) if reporter is not None else 0
    problems: list[str] = []
    if session.testscollected == 0:
        problems.append("collected=0")
    if skipped:
        problems.append(f"skipped={skipped}")
    if problems:
        session.exitstatus = 1
        if reporter is not None:
            reporter.write_sep("!", "invalid acceptance run: " + ", ".join(problems))
