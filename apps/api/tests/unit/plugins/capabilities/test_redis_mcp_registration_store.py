"""redis-py-compatible MCP registration NX semantics; not run during freeze."""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

from prodavan.application.plugins.capabilities.adapters._shared import (
    CapabilityAccessDenied,
    CapabilityBackendError,
)
from prodavan.application.plugins.capabilities.adapters.mcp import (
    DurableMcpAdapter,
    digest_bound_server_id,
)
from prodavan.application.plugins.capabilities.contracts import (
    CapabilityEnvelope,
    CapabilityName,
    McpRegisterRequest,
    McpToolDefinition,
)
from prodavan.infrastructure.plugins.capabilities.redis import RedisMcpRegistrationStore

pytestmark = pytest.mark.asyncio

DIGEST = "sha256:" + "a" * 64
SCOPE = {
    "company_id": "co_123",
    "plugin_id": "mod_x",
    "plugin_instance_id": "inst_abc",
    "server_id": digest_bound_server_id("server1", DIGEST),
}


class RedisPyCompatibleFake:
    """Shared backend: True on insert, None on an existing NX key.

    set_calls records attempted NX operations, including misses. Only
    successful_writes records physical durable values. Barriers are optional
    test-only suspension points around the adapter's exact GET/SET/re-read order.
    """

    def __init__(self, *, responses: list[Any] | None = None, error: Exception | None = None) -> None:
        self.values: dict[str, str] = {}
        self.responses = list(responses or [])
        self.error = error
        self.set_calls: list[tuple[str, str, bool | None]] = []
        self.nx_results: list[Any] = []
        self.successful_writes: list[tuple[str, str]] = []
        self.nx_miss_reread_values: list[tuple[str, str | None]] = []
        self.initial_get_barriers: list[asyncio.Barrier] = []
        self.set_entry_barrier: asyncio.Barrier | None = None
        self.set_exit_barrier: asyncio.Barrier | None = None
        self._last_nx_result: dict[str, Any] = {}

    async def _wait(self, barrier: asyncio.Barrier | None) -> None:
        if barrier is not None:
            await barrier.wait()

    async def get(self, key: str) -> str | None:
        value = self.values.get(key)
        task_name = asyncio.current_task().get_name()
        if self.initial_get_barriers:
            # Keep the barrier queued until every concurrent initial GET has
            # entered it; popping before await strands the first caller alone.
            barrier = self.initial_get_barriers[0]
            await barrier.wait()
            if self.initial_get_barriers and self.initial_get_barriers[0] is barrier:
                self.initial_get_barriers.pop(0)
        if (
            task_name in self._last_nx_result
            and (
                self._last_nx_result[task_name] is None
                or self._last_nx_result[task_name] is False
            )
        ):
            # Only this task's concrete None/False NX attempt arms one re-read.
            # A missing map entry is just an unrelated initial GET.
            self.nx_miss_reread_values.append((task_name, value))
            self._last_nx_result.pop(task_name, None)
        return value

    async def set(self, key: str, value: str, *, nx: bool | None = None) -> Any:
        await self._wait(self.set_entry_barrier)
        self.set_calls.append((key, value, nx))
        if self.error is not None:
            raise self.error
        if self.responses:
            result = self.responses.pop(0)
        elif nx and key in self.values:
            result = None
        else:
            result = True
            self.successful_writes.append((key, value))
            self.values[key] = value
        task_name = asyncio.current_task().get_name()
        if nx is True:
            self._last_nx_result[task_name] = result
        else:
            self._last_nx_result.pop(task_name, None)
        if nx is True:
            self.nx_results.append(result)
        await self._wait(self.set_exit_barrier)
        return result


def _request(*, version: str = "1") -> McpRegisterRequest:
    return McpRegisterRequest(
        server_id="server1",
        tools=(McpToolDefinition(
            tool_name="greet",
            tool_version=version,
            capability=CapabilityName.MCP,
            operation="mcp.invoke",
            input_schema_digest=DIGEST,
        ),),
        envelope=CapabilityEnvelope(
            request_id="req1",
            company_id="co_123",
            subject_id="plugin:mod_x:co_123:inst_abc",
            plugin_instance_id="inst_abc",
            artifact_digest=DIGEST,
            grant_id="grant_01",
        ),
    )


async def test_true_none_none_never_overwrites_existing_value() -> None:
    redis = RedisPyCompatibleFake()
    store = RedisMcpRegistrationStore(client=redis)
    first = await store.put_if_absent(**SCOPE, definitions="same-value")
    second = await store.put_if_absent(**SCOPE, definitions="same-value")
    third = await store.put_if_absent(**SCOPE, definitions="same-value")
    assert (first, second, third) == (True, False, False)
    assert len(redis.set_calls) == 3  # three NX attempts, including two misses
    assert len(redis.successful_writes) == 1
    assert list(redis.values.values()) == ["same-value"]


async def test_false_nx_miss_is_not_an_error_and_does_not_write() -> None:
    redis = RedisPyCompatibleFake(responses=[False])
    store = RedisMcpRegistrationStore(client=redis)
    assert await store.put_if_absent(**SCOPE, definitions="value") is False
    assert redis.values == {}


async def test_instrumentation_records_only_concrete_nx_miss_reread_once() -> None:
    redis = RedisPyCompatibleFake()
    assert await redis.get("unrelated-initial-get") is None
    assert redis.nx_miss_reread_values == []
    store = RedisMcpRegistrationStore(client=redis)
    assert await store.put_if_absent(**SCOPE, definitions="value") is True
    assert await store.put_if_absent(**SCOPE, definitions="value") is False
    assert redis.nx_miss_reread_values == []
    assert await store.get(**SCOPE) == "value"
    assert len(redis.nx_miss_reread_values) == 1
    assert redis.nx_miss_reread_values[0][1] == "value"
    assert await store.get(**SCOPE) == "value"
    assert len(redis.nx_miss_reread_values) == 1


async def test_same_registration_replay_is_idempotent_and_digest_stable() -> None:
    redis = RedisPyCompatibleFake()
    adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    request = _request()
    first = await adapter.register(request)
    second = await adapter.register(request)
    assert first.digest == second.digest
    assert first.registered == second.registered == 1
    # The existing value is read before SET, so a replay cannot overwrite it.
    assert len(redis.set_calls) == 1
    assert len(redis.successful_writes) == 1


async def test_foreign_value_replay_conflict_does_not_overwrite() -> None:
    redis = RedisPyCompatibleFake()
    adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    await adapter.register(_request())
    with pytest.raises(CapabilityAccessDenied, match="mcp_registration_conflict"):
        await adapter.register(_request(version="2"))
    assert len(redis.set_calls) == 1
    assert len(redis.values) == 1


@pytest.mark.parametrize("response", [1, 0, "OK", b"OK", [], {}])
async def test_invalid_set_response_is_typed_backend_failure(response: Any) -> None:
    redis = RedisPyCompatibleFake(responses=[response])
    adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    with pytest.raises(CapabilityBackendError, match="mcp_registry_write_failed"):
        await adapter.register(_request())
    assert redis.values == {}


async def test_set_io_error_is_sanitized_backend_failure() -> None:
    secret_dsn = "redis://user:plaintext-password@private-host"
    redis = RedisPyCompatibleFake(error=RuntimeError(secret_dsn))
    adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    with pytest.raises(CapabilityBackendError, match="mcp_registry_write_failed") as caught:
        await adapter.register(_request())
    assert secret_dsn not in str(caught.value)
    assert redis.values == {}


async def test_two_clients_same_registration_concurrent_replay() -> None:
    redis = RedisPyCompatibleFake()
    initial_get = asyncio.Barrier(2)
    redis.initial_get_barriers = [initial_get, initial_get]
    redis.set_entry_barrier = asyncio.Barrier(2)
    redis.set_exit_barrier = asyncio.Barrier(2)
    first_adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    second_adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    request = _request()
    first, second = await asyncio.gather(
        first_adapter.register(request),
        second_adapter.register(request),
    )
    assert first.digest == second.digest
    assert first.registered == second.registered == 1
    # Both initial GETs observed missing. Exactly one NX wins; the loser receives
    # redis-py's None and must re-read the same durable definitions before replay.
    assert any(result is True for result in redis.nx_results)
    assert any(result is None for result in redis.nx_results)
    assert len(redis.nx_results) == 2
    assert len(redis.set_calls) == 2
    assert len(redis.successful_writes) == 1
    assert len(redis.nx_miss_reread_values) == 1
    assert redis.nx_miss_reread_values[0][1] == next(iter(redis.values.values()))
    assert len(redis.values) == 1


async def test_racing_foreign_value_conflicts_only_after_nx_miss_and_reread() -> None:
    redis = RedisPyCompatibleFake()
    initial_get = asyncio.Barrier(2)
    redis.initial_get_barriers = [initial_get, initial_get]
    redis.set_entry_barrier = asyncio.Barrier(2)
    redis.set_exit_barrier = asyncio.Barrier(2)
    first_adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    second_adapter = DurableMcpAdapter(store=RedisMcpRegistrationStore(client=redis))
    winner, loser = await asyncio.gather(
        first_adapter.register(_request()),
        second_adapter.register(_request(version="2")),
        return_exceptions=True,
    )
    outcomes = (winner, loser)
    successful = [item for item in outcomes if not isinstance(item, Exception)]
    conflicts = [item for item in outcomes if isinstance(item, CapabilityAccessDenied)]
    assert len(successful) == 1
    assert len(conflicts) == 1
    assert "mcp_registration_conflict" in str(conflicts[0])
    assert any(result is True for result in redis.nx_results)
    assert any(result is None for result in redis.nx_results)
    # The conflict is decided from the loser's False -> re-read, never by treating
    # the NX miss as success and never by allowing the foreign value to overwrite.
    assert len(redis.nx_miss_reread_values) == 1
    assert redis.nx_miss_reread_values[0][1] == next(iter(redis.values.values()))
    assert len(redis.successful_writes) == 1
    assert len(redis.values) == 1
