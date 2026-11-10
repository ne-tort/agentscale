"""Cross-process PostgreSQL update lock and Redis execution lease renewal."""
from __future__ import annotations

import pytest

from .world import EntrypointWorld

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.entrypoint_acceptance,
]


async def test_two_processes_share_postgres_update_lock_and_redis_lease(
    world: EntrypointWorld,
) -> None:
    lock = await world.run_helper("update-lock-race")
    assert lock["two_processes"] is True
    assert lock["contender_blocked"] is True
    assert lock["owner_released"] is True

    lease = await world.run_helper("lease-renewal")
    assert lease["renewals"] >= 2
    assert lease["contender_denied"] is True
    assert lease["result_digest"].startswith("sha256:")
