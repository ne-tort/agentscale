"""Cold two-entrypoint composition and health/identity acceptance."""
from __future__ import annotations

import httpx
import pytest

from prodavan.application.plugins.wiring.adapters import WIRED_CAPABILITIES

from .http_support import request
from .world import EntrypointWorld

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.entrypoint_acceptance,
]


async def test_cold_entrypoints_have_nine_real_adapters_and_deferred_worker(
    world: EntrypointWorld,
) -> None:
    expected = {
        "cache": "RedisCacheAdapter",
        "objects": "MinioObjectsAdapter",
        "docs": "DocumentStoreDocsAdapter",
        "events": "KafkaEventsAdapter",
        "jobs": "CeleryJobsAdapter",
        "pods": "ScopedPodsAdapter",
        "mcp": "DurableMcpAdapter",
        "secrets": "SecretStoreSecretsAdapter",
        "network": "K8sEgressNetworkAdapter",
    }
    declarations = set()
    for entrypoint in ("main", "main_plugin_gateway"):
        report = await world.run_helper("composition", entrypoint)
        declarations.add(report["declarations"])
        assert report["repositories"] == {
            "grants": "SqlCapabilityGrantRepository",
            "artifacts": "SqlArtifactRegistryStore",
        }
        assert report["entrypoint"] == entrypoint
        assert report["adapters"] == expected
        assert set(report["adapters"]) == set(WIRED_CAPABILITIES)
        assert report["payload_store"] == "DigestPayloadStore"
        assert report["manifest_tasks"] == "ManifestJobTasks"
        assert report["worker_wired"] is True
        assert report["unwired"] == [] and report["in_memory"] == []
    deferred = await world.run_helper("worker-deferred")
    declarations.add(deferred["declarations"])
    assert len(declarations) == 1
    assert deferred["ok"] is True
    assert deferred["deferred"] is True
    assert deferred["factory"] == "DeferredStandalonePluginJobComposition"
    assert deferred["allocated"] is False


async def test_health_ready_and_exact_claim_identity_ignore_spoofed_headers(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    api_live = await http_client.get(world.api_url + "/health/live")
    api_ready = await http_client.get(world.api_url + "/health/ready")
    gateway_live = await http_client.get(world.gateway_url + "/health/live")
    gateway_ready = await http_client.get(world.gateway_url + "/health/ready")
    assert api_live.status_code == 200 and api_live.json() == {"status": "alive"}
    assert api_ready.status_code == 200 and api_ready.json()["status"] == "ok"
    assert api_ready.json()["checks"]["database"] == "ok"
    assert api_ready.json()["checks"]["redis"] == "ok"
    assert api_ready.json()["checks"]["mongodb"] == "ok"
    assert gateway_live.status_code == 200 and gateway_live.json() == {"status": "alive"}
    assert gateway_ready.status_code == 200 and gateway_ready.json() == {"status": "ready"}
    for base_url in (world.api_url, world.gateway_url):
        root = await http_client.get(base_url + "/health")
        assert root.status_code == 200
    spoof = {
        "X-Plugin-ID": "attacker_plugin",
        "X-Plugin-Instance-ID": world.config.foreign_plugin_instance_id,
        "X-Plugin-Artifact-Digest": "sha256:" + "f" * 64,
        "X-Plugin-Grant-ID": world.config.foreign_grant_id,
        "X-Company-ID": world.config.foreign_company_id,
        "X-Project-ID": "attacker_project",
    }
    for base_url in (world.api_url, world.gateway_url):
        identity = await request(
            http_client,
            "GET",
            base_url + "/capabilities/v1/health",
            token=world.state.token,
            spoof=spoof,
        )
        assert identity["plugin_id"] == world.config.plugin_id
        assert identity["company_id"] == world.config.company_id
        assert identity["grant_id"] == world.config.grant_id
        assert identity["artifact_digest"] == world.artifact.record.digest
        assert identity["project_id"] == world.config.project_id
        assert set(identity["scopes"]) == set(WIRED_CAPABILITIES)
        assert identity["exp"] > 0
