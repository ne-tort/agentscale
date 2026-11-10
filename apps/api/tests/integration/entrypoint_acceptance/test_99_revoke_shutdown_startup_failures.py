"""Durable revoke convergence, owned shutdown and fail-closed cold startup."""
from __future__ import annotations

import httpx
import pytest

from .durable import revoke_primary
from .http_support import request
from .processes import (
    ProcessSupervisor,
    ephemeral_localhost_port,
    uvicorn_process,
)
from .world import EntrypointWorld

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.entrypoint_acceptance,
]


async def _expect_startup_failure(
    owner: ProcessSupervisor,
    world: EntrypointWorld,
    *,
    name: str,
    module: str,
    mutation: str,
) -> None:
    env = dict(world.environment)
    if mutation == "declarations":
        env.pop("PLUGIN_JOB_DECLARATIONS")
    elif mutation == "event-key":
        env["PLUGIN_EVENT_SIGNING_SECRET_REF"] = "file://ai_keys/missing-event-key.secret"
    elif mutation == "postgres":
        env["DATABASE_URL"] = (
            "postgresql+asyncpg://acceptance:acceptance@127.0.0.1:1/acceptance"
        )
    elif mutation == "redis":
        env["REDIS_URL"] = f"redis://127.0.0.1:1/{world.config.redis_db}"
        env["CELERY_BROKER_URL"] = env["REDIS_URL"]
        env["CELERY_RESULT_BACKEND"] = env["REDIS_URL"]
    else:
        raise AssertionError(mutation)
    process = owner.add(uvicorn_process(
        name=name,
        module=module,
        port=ephemeral_localhost_port(),
        env=env,
        shutdown_timeout=world.config.shutdown_timeout_seconds,
        sensitive=world.sensitive,
    ))
    await process.start()
    code = await process.wait_exit(
        timeout=world.config.startup_timeout_seconds,
        expected=None,
    )
    assert code != 0, f"{name}: missing/unavailable {mutation} unexpectedly started"
    expected_markers = {
        "declarations": "plugin_job_declarations_required",
        "event-key": "MISSING_DEPENDENCY",
        "postgres": "plugin_job_publication_load_failed",
        "redis": "redis: ping failed on startup",
    }
    tail = process.tail()
    assert expected_markers[mutation] in tail, (
        f"{name}: mutation {mutation} did not emit its sanitized startup marker\n{tail}"
    )
    for secret in world.sensitive:
        assert secret not in tail, f"{name}: sensitive value leaked into startup logs"


async def test_revoke_shutdown_and_missing_required_inputs_fail_closed(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    for base_url in (world.api_url, world.gateway_url):
        await request(
            http_client, "GET", base_url + "/capabilities/v1/health",
            token=world.state.token,
        )
    await revoke_primary(
        config=world.config,
        artifact=world.artifact,
        state=world.state,
        session_factory=world.postgres.session_factory,
    )
    for base_url in (world.api_url, world.gateway_url):
        response = await http_client.get(
            base_url + "/capabilities/v1/health",
            headers={"Authorization": f"Bearer {world.state.token}"},
        )
        assert response.status_code in {401, 403}, response.text[:800]
        assert response.json().get("decision") != "allow"

    # Worker -> gateway -> API reverse ownership order. Reader tasks are joined
    # too; fixture teardown repeats close to prove idempotence.
    await world.processes.aclose()
    world.processes.assert_all_stopped()

    negative = ProcessSupervisor(shutdown_timeout=world.config.shutdown_timeout_seconds)
    try:
        for name, module, mutation in (
            ("missing-declarations", "prodavan.main", "declarations"),
            ("missing-event-key", "prodavan.main_plugin_gateway", "event-key"),
            ("missing-postgres", "prodavan.main", "postgres"),
            ("missing-redis", "prodavan.main_plugin_gateway", "redis"),
        ):
            await _expect_startup_failure(
                negative, world, name=name, module=module, mutation=mutation
            )
    finally:
        await negative.aclose()
    negative.assert_all_stopped()
