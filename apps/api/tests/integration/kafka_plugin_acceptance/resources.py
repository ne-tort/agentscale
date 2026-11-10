"""Random topic/group identity and bounded admin lifecycle helpers.

Every acceptance test owns a unique namespace. Topics and DLQ topics are
created through the production KafkaTopicAdminAdapter; deletion and consumer-
group deletion use the real Kafka admin API. Cleanup is bounded and fails the
test when its own resources survive.
"""

from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from typing import Any

from prodavan.infrastructure.plugins.adapters.kafka_adapters import KafkaTopicAdminAdapter

_SAFE_SUFFIX = re.compile(r"^[a-z0-9]+$")
_DELETE_ATTEMPTS = 12
_DELETE_DELAY = 0.25


@dataclass(frozen=True, slots=True)
class AcceptanceIdentity:
    run_id: str
    company_id: str
    plugin_id: str
    plugin_instance_id: str
    grant_id: str
    subscription_id: str
    consumer_group: str
    observer_group: str
    topic: str
    dlq_topic: str
    unknown_topic: str
    schema_name: str


def new_identity(label: str) -> AcceptanceIdentity:
    suffix = uuid.uuid4().hex[:12]
    if not _SAFE_SUFFIX.fullmatch(label.replace("-", "").replace("_", "")):
        label = "case"
    label = label.replace("-", "").replace("_", "")[:18].lower() or "case"
    run = f"acc{suffix}"
    company = f"coacc{suffix[:12]}"
    plugin = f"modacc{suffix[:12]}"
    instance = f"instacc{suffix[:12]}"
    grant = f"grantacc{suffix}"
    subscription = f"{company}:{instance}:{label}{suffix[:6]}"
    group = f"tenant:{company}:plugin:{plugin}:{instance}:group:{label}{suffix[:6]}"
    return AcceptanceIdentity(
        run_id=run,
        company_id=company,
        plugin_id=plugin,
        plugin_instance_id=instance,
        grant_id=grant,
        subscription_id=subscription,
        consumer_group=group,
        observer_group=f"observer:{company}:{plugin}:{instance}:{label}{suffix[:6]}",
        topic=f"agentscale.plugin.acceptance.{run}.{label}",
        dlq_topic=f"agentscale.plugin.acceptance.{run}.{label}.dlq",
        unknown_topic=f"agentscale.plugin.acceptance.{run}.{label}.unknown",
        schema_name=f"kafka_acceptance_{run}_{label[:16]}",
    )


class KafkaResourceCleanup:
    """Own real admin deletion for exactly the topics and groups we created."""

    def __init__(self, *, admin: Any) -> None:
        self._admin = admin
        self._topics: set[str] = set()
        self._groups: set[str] = set()

    def track_topics(self, *topics: str) -> None:
        self._topics.update(topics)

    def track_group(self, group: str) -> None:
        self._groups.add(group)

    async def delete_topics(self, timeout_seconds: float) -> None:
        if not self._topics:
            return
        try:
            await self._admin.delete_topics(list(sorted(self._topics)), timeout_ms=int(timeout_seconds * 1000))
        except Exception as exc:
            if type(exc).__name__ not in {"UnknownTopicOrPartition", "UnknownTopicOrPartitionError"}:
                raise RuntimeError("Kafka admin rejected deletion of acceptance topics") from exc
        await self._wait_absent(self._topics, timeout_seconds=timeout_seconds)

    async def delete_consumer_groups(self, timeout_seconds: float) -> None:
        if not self._groups:
            return
        # aiokafka 0.11/0.12 has no public delete_consumer_groups method, but
        # its real admin transport supports DeleteGroupsRequest (API key 42).
        # A group can linger briefly after close(); retry only owned group ids
        # and confirm their absence instead of hiding teardown failures.
        last_error: Exception | None = None
        for _ in range(_DELETE_ATTEMPTS):
            try:
                async with asyncio.timeout(timeout_seconds):
                    await self._delete_groups_once()
                    await self._wait_groups_absent(timeout_seconds=timeout_seconds)
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                await asyncio.sleep(_DELETE_DELAY)
        if last_error is not None:
            raise RuntimeError("Kafka admin could not delete the acceptance consumer groups") from last_error

    async def _delete_groups_once(self) -> None:
        from aiokafka.errors import for_code
        from aiokafka.protocol.admin import DeleteGroupsRequest

        version = self._admin._matching_api_version(DeleteGroupsRequest)
        request = DeleteGroupsRequest[version](list(sorted(self._groups)))
        response = await self._admin._send_request(request)
        for _group_id, error_code in response.results:
            if error_code in {0, 69}:  # success or idempotent GROUP_ID_NOT_FOUND
                continue
            raise for_code(error_code)()

    async def _wait_groups_absent(self, *, timeout_seconds: float) -> None:
        remaining = set(self._groups)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while remaining and asyncio.get_running_loop().time() < deadline:
            listed = await self._admin.list_consumer_groups()
            existing = {item[0] if isinstance(item, tuple) else item for item in listed}
            remaining &= existing
            if remaining:
                await asyncio.sleep(_DELETE_DELAY)
        if remaining:
            raise RuntimeError("acceptance consumer groups were not deleted within the bounded deadline")

    async def _wait_absent(self, topics: set[str], *, timeout_seconds: float) -> None:
        remaining = set(topics)
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while remaining and asyncio.get_running_loop().time() < deadline:
            try:
                existing = set(await self._admin.list_topics())
            except Exception as exc:
                raise RuntimeError("Kafka admin could not confirm topic deletion") from exc
            remaining &= existing
            if remaining:
                await asyncio.sleep(_DELETE_DELAY)
        if remaining:
            raise RuntimeError("acceptance topics were not deleted within the bounded deadline")


async def prepare_random_topics(
    admin_adapter: KafkaTopicAdminAdapter,
    identity: AcceptanceIdentity,
) -> tuple[str, ...]:
    created = await admin_adapter.ensure_plugin_topics(identity.topic)
    await admin_adapter.start()
    await admin_adapter.create_topic(
        identity.unknown_topic,
        num_partitions=1,
        replication_factor=1,
    )
    if not await admin_adapter.topic_exists(identity.unknown_topic):
        raise RuntimeError("unknown-topic fixture was not confirmed through the Kafka admin API")
    return (*created, identity.unknown_topic)


__all__ = [
    "AcceptanceIdentity",
    "KafkaResourceCleanup",
    "new_identity",
    "prepare_random_topics",
]
