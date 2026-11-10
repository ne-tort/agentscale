"""Build the complete real capability set from validated production dependencies."""
from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from typing import Any

from .cache import RedisCacheAdapter
from .docs import DocumentStoreDocsAdapter
from .events import KafkaEventsAdapter
from .jobs import CeleryJobsAdapter
from .mcp import DurableMcpAdapter
from .network import K8sEgressNetworkAdapter
from .objects import MinioObjectsAdapter
from .pods import ScopedPodsAdapter
from .secrets import SecretStoreSecretsAdapter
from ._shared import CapabilityConfigurationError

_NAMES = ("cache", "objects", "docs", "events", "jobs", "pods", "mcp", "secrets", "network")


class _SqlEgressPolicy:
    """Async SQL preload followed by the canonical synchronous policy checks."""

    def __init__(self, store: Any, hop_inspector: Any = None) -> None:
        self._store = store
        self._hop_inspector = hop_inspector

    @staticmethod
    def _as_mapping(record: Any) -> Mapping[str, Any]:
        """Project the exact slotted grant contract into the resolver shape."""
        from prodavan.application.plugins.admin.contracts import CapabilityGrantRecord
        from prodavan.application.plugins.egress.errors import EgressPolicyError

        if not isinstance(record, CapabilityGrantRecord):
            raise EgressPolicyError(
                "capability grant must be a CapabilityGrantRecord",
                reason="grant_unreadable",
            )
        values = dataclasses.asdict(record)
        policy = values["egress_policy"]
        quotas = values["quotas"]
        if not isinstance(policy, Mapping) or not isinstance(quotas, Mapping):
            raise EgressPolicyError(
                "capability grant egress policy and quotas are required",
                reason="policy_invalid",
            )
        try:
            egress = {
                "mode": policy["mode"].value,
                "allowed_hosts": policy["allowed_hosts"],
                "allowed_paths": policy["allowed_paths"],
                "approved": policy["approved_for_full"],
                "max_egress_bytes": quotas["max_egress_bytes"],
            }
        except (AttributeError, KeyError) as exc:
            raise EgressPolicyError(
                "capability grant egress contract is incomplete",
                reason="policy_invalid",
            ) from exc
        return {**values, "egress": egress}

    async def _lookup(self, grant_id: str) -> Any:
        from prodavan.application.plugins.egress.resolver import GrantLookup

        record = await self._store.get_by_grant_id(grant_id=grant_id)
        if record is None:
            raise LookupError("egress grant not found")
        raw = self._as_mapping(record)
        if raw["grant_id"] != grant_id:
            raise LookupError("egress grant binding mismatch")
        return GrantLookup(
            raw=raw,
            active=bool(getattr(record, "is_active", False)),
            revoked_at=getattr(record, "revoked_at", None),
            issued_at=getattr(record, "issued_at", getattr(record, "created_at", None)),
            expires_at=getattr(record, "expires_at", None),
        )

    @staticmethod
    def _resolver(lookup: Any) -> Any:
        from prodavan.application.plugins.egress.resolver import GrantPolicyResolver

        class StaticStore:
            def get_grant(self, grant_id: str) -> Any:
                return lookup

        return GrantPolicyResolver(StaticStore())

    async def allows(self, *, grant_id: str, host: str, port: int, path: str) -> bool:
        from prodavan.application.plugins.egress.policy import (
            EgressMode, match, normalize_host, normalize_path, validate_host,
        )

        if port != 443:
            return False
        lookup = await self._lookup(grant_id)
        policy = self._resolver(lookup).resolve(grant_id)
        if policy.mode is EgressMode.NONE or not policy.allows_any:
            return False
        safe_host = validate_host(normalize_host(host), allow_wildcard=False)
        safe_path = normalize_path(path)
        return any(
            match(rule, safe_host, safe_path, method)
            for rule in policy.rules
            for method in ("GET", "HEAD", "POST", "PUT", "PATCH", "DELETE")
        )

    async def check(self, *args: Any, **kwargs: Any) -> Any:
        from prodavan.application.plugins.egress.service import EgressService

        grant_id = args[0].get("grant_id") if args and isinstance(args[0], dict) else kwargs.get("grant_id")
        if grant_id is None and args and isinstance(args[0], dict):
            grant_id = args[0].get("grant_id")
        if grant_id is None:
            raise ValueError("egress grant_id is required")
        lookup = await self._lookup(str(grant_id))

        class StaticStore:
            def get_grant(self, requested: str) -> Any:
                return lookup

        # The outer capability pipeline already writes the durable decision audit.
        # This nested policy service must not buffer a second in-memory allow log.
        service = EgressService(store=StaticStore(), hop_inspector=self._hop_inspector)
        return service.check(*args, **kwargs)


def _required(overrides: Mapping[str, Any], name: str) -> Any:
    value = overrides.get(name)
    if value is None:
        raise CapabilityConfigurationError(f"{name}_required")
    return value


def build_capability_adapters(
    *,
    redis: Any,
    repos: Any,
    runtime: Any,
    services: Mapping[str, Any],
    settings: Any,
    overrides: Mapping[str, Any],
) -> dict[str, Any]:
    """Return adapter_<name> entries; every missing secure config fails closed."""
    from prodavan.application.plugins.events.dispatch import PluginEventDispatcher
    from prodavan.infrastructure.plugins.adapters.redis_event_claims import RedisDeliveryClaimStore
    from prodavan.infrastructure.plugins.capabilities import (
        KafkaEventResourceManager,
        KafkaRawDlqAdapter,
        KafkaSecurityConfig,
        KafkaSubscriptionRuntime,
        ManagerRedisClient,
        ProductionDocumentStoreService,
        RedisCeleryJobQueue,
        RedisMcpRegistrationStore,
    )
    from prodavan.infrastructure.plugins.minio.adapters import MinioPresigner, MinioSts
    from prodavan.infrastructure.plugins.minio.config import MinioPluginConfig

    del runtime  # Scoped K8s identity is accepted only through explicit safe ports.
    supplied = dict(overrides.get("capability_adapters") or {})
    unknown = set(supplied) - set(_NAMES)
    if unknown:
        raise CapabilityConfigurationError(f"unknown_capability_adapter:{sorted(unknown)[0]}")
    result: dict[str, Any] = {}
    sql_egress_policy = overrides.get("egress_policy") or _SqlEgressPolicy(
        repos.grant_store,
        hop_inspector=overrides.get("egress_hop_inspector"),
    )
    result["egress_policy"] = sql_egress_policy
    result["egress"] = overrides.get("egress_service") or sql_egress_policy
    for name in _NAMES:
        direct = overrides.get(f"adapter_{name}", supplied.get(name))
        if direct is not None:
            result[f"adapter_{name}"] = direct

    redis_client = overrides.get("redis_client") or ManagerRedisClient(redis)
    if "adapter_cache" not in result:
        result["adapter_cache"] = RedisCacheAdapter(client=redis_client)
    if "adapter_docs" not in result:
        result["adapter_docs"] = DocumentStoreDocsAdapter(
            service=overrides.get("document_store_service") or ProductionDocumentStoreService()
        )
    if "adapter_mcp" not in result:
        result["adapter_mcp"] = DurableMcpAdapter(
            store=RedisMcpRegistrationStore(client=redis_client)
        )
    # Exactly one factory-built adapter owns the shared SecretStore. When the
    # objects adapter is supplied externally, the secrets adapter assumes that
    # ownership; externally supplied adapters manage their own explicit lifecycle.
    owns_object_resources = "adapter_objects" not in result and bool(
        overrides.get("own_object_resources", True)
    )
    if "adapter_secrets" not in result:
        result["adapter_secrets"] = SecretStoreSecretsAdapter(
            store=_required(overrides, "secret_store"),
            owns_store=not owns_object_resources,
        )
    if "adapter_objects" not in result:
        legacy = {"minio_sts_available", "io_test_sts_available"} & set(overrides)
        if legacy:
            raise CapabilityConfigurationError(
                f"legacy_sts_availability_override_forbidden:{sorted(legacy)[0]}"
            )
        secret_store = _required(overrides, "secret_store")
        buckets = services.get("object_buckets") or _required(overrides, "object_buckets")
        sts = services.get("object_sts") or overrides.get("object_sts")
        presigner = services.get("object_presigner") or overrides.get("object_presigner")
        config = overrides.get("minio_config") or getattr(buckets, "config", None)
        if config is None and (sts is None or presigner is None):
            # Existing compositions may provide both ports and no config. In that
            # case STS retains its current construction path; a supplied presigner
            # remains the only way to enable the separate exact-key operation.
            if sts is None:
                config = MinioPluginConfig.from_secret_refs(
                    secret_store=secret_store,
                    settings=settings,
                )
        if sts is None:
            # Construction performs no IO and grants no availability. Only the
            # async lifespan probe can make scoped_credentials wired.
            sts = MinioSts(
                config=config,
                sts_client=overrides.get("minio_sts_client"),
            )
        if presigner is None and config is not None:
            presigner = MinioPresigner(
                config=config,
                client=overrides.get("minio_presign_client"),
            )
        result["adapter_objects"] = MinioObjectsAdapter(
            buckets=buckets,
            sts=sts,
            presigner=presigner,
            secret_store=secret_store,
            owns_resources=owns_object_resources,
            # The factory-built objects adapter is the single owner of the
            # shared SecretStore (the secrets adapter above deliberately gets
            # owns_store=False). CloseOnce keeps it to exactly one close,
            # after buckets/sts/presigner, never at construction time.
            owns_secret_store=owns_object_resources,
        )
    if "adapter_jobs" not in result:
        from prodavan.core.infra.worker_manager import get_celery_app, get_worker_manager

        tasks = frozenset(overrides.get("job_tasks") or ())
        result["adapter_jobs"] = CeleryJobsAdapter(
            queue=RedisCeleryJobQueue(
                redis=redis_client,
                worker_provider=get_worker_manager,
                celery_provider=get_celery_app,
            ),
            allowed_tasks=tasks,
        )
    if "adapter_pods" not in result:
        result["adapter_pods"] = ScopedPodsAdapter(
            executor=_required(overrides, "pod_executor")
        )
    if "adapter_network" not in result:
        transport = _required(overrides, "egress_transport")
        egress = overrides.get("egress_service") or sql_egress_policy
        result["adapter_network"] = K8sEgressNetworkAdapter(
            transport=transport,
            egress=egress,
            initial_policy=sql_egress_policy,
            owns_transport=bool(overrides.get("own_egress_transport", True)),
        )
    if "adapter_events" not in result:
        secret_store = _required(overrides, "secret_store")
        security = overrides.get("kafka_security") or KafkaSecurityConfig.from_secret_refs(
            secret_store=secret_store
        )
        bootstrap = overrides.get("kafka_bootstrap_servers") or getattr(
            settings, "kafka_bootstrap_servers", None
        )
        resources = overrides.get("kafka_resources") or KafkaEventResourceManager(
            bootstrap_servers=bootstrap,
            security=security,
        )
        handler = _required(overrides, "event_handler")
        wake_hook = _required(overrides, "event_wake_hook")
        raw_dlq = overrides.get("event_raw_dlq") or KafkaRawDlqAdapter(
            resources=resources,
            redis=redis_client,
        )
        delivery_claims = overrides.get("event_delivery_claims")
        if delivery_claims is None:
            # One shared, namespaced Redis-backed store is captured by every
            # dispatcher made by this standalone factory. The store never owns
            # or closes the shared Redis client and cannot collide with raw DLQ.
            delivery_claims = RedisDeliveryClaimStore(
                redis=redis_client,
                prefix=overrides.get(
                    "event_delivery_claim_prefix",
                    "agentscale:plugin:events:delivery-claims:",
                ),
            )

        def dispatcher(consumer: Any = None, subscription: Any = None) -> PluginEventDispatcher:
            del subscription
            return PluginEventDispatcher(
                producer=resources.producer,
                consumer=consumer,
                topic_admin=resources.topic_admin,
                dlq=resources,
                raw_dlq=raw_dlq,
                subscriptions=repos.subscription_store,
                published_events=repos.published_event_store,
                idempotency=repos.idempotency_store,
                delivery_claims=delivery_claims,
                audit=repos.audit,
                handler=handler,
                wake_hook=wake_hook,
            )

        subscription_runtime = overrides.get("kafka_subscription_runtime") or KafkaSubscriptionRuntime(
            bootstrap_servers=bootstrap,
            security=security,
            dispatcher_factory=dispatcher,
        )
        shared_dispatcher = dispatcher()
        result["event_dispatcher"] = shared_dispatcher
        result["adapter_events"] = KafkaEventsAdapter(
            dispatcher=shared_dispatcher,
            subscriptions=repos.subscription_store,
            runtime=subscription_runtime,
            lifecycle=resources,
        )

    return result


__all__ = ["build_capability_adapters"]
