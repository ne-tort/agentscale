"""Real cache/docs/objects and trusted runtime provider route acceptance."""
from __future__ import annotations

import hashlib
import uuid

import httpx
import pytest

from .http_support import EMPTY_DIGEST, allowed, request
from .world import EntrypointWorld

pytestmark = [
    pytest.mark.asyncio,
    pytest.mark.integration,
    pytest.mark.entrypoint_acceptance,
]


async def test_redis_mongo_and_minio_adapters_perform_real_io(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    key = "acceptance-" + uuid.uuid4().hex
    value = "real-redis-" + uuid.uuid4().hex
    allowed(await request(
        http_client, "POST", world.api_url + "/capabilities/v1/cache/set",
        token=world.state.token, payload={"key": key, "value": value},
    ))
    cached = allowed(await request(
        http_client, "POST", world.gateway_url + "/capabilities/v1/cache/get",
        token=world.state.token, payload={"key": key},
    ))
    assert cached["found"] is True
    assert cached["value_digest"] == "sha256:" + hashlib.sha256(value.encode()).hexdigest()

    documents = allowed(await request(
        http_client, "POST", world.gateway_url + "/capabilities/v1/docs/query",
        token=world.state.token,
        payload={
            "namespace": "plugins",
            "collection": world.document_collection,
            "predicates": [{"field": "marker", "operator": "eq", "values": ["primary"]}],
            "limit": 10,
        },
    ))
    assert documents["matched"] == 1
    assert [item["document_id"] for item in documents["documents"]] == [world.document_id]
    foreign_documents = allowed(await request(
        http_client, "POST", world.api_url + "/capabilities/v1/docs/query",
        token=world.state.foreign_token,
        payload={
            "namespace": "plugins",
            "collection": world.document_collection,
            "predicates": [],
            "limit": 10,
        },
    ))
    assert foreign_documents["matched"] == 1
    assert [item["document_id"] for item in foreign_documents["documents"]] == [
        world.document_id + "_foreign"
    ]

    listed = allowed(await request(
        http_client, "POST", world.api_url + "/capabilities/v1/objects",
        token=world.state.token,
        payload={
            "bucket": world.minio.identity.bucket,
            "key_prefix": world.minio.identity.prefix,
            "limit": 10,
        },
    ))
    assert world.minio.identity.fixed_key in {
        item["object_key"] for item in listed["objects"]
    }
    credentials = allowed(await request(
        http_client, "POST", world.gateway_url + "/capabilities/v1/objects/credentials",
        token=world.state.token,
        payload={
            "bucket": world.minio.identity.bucket,
            "key_prefix": world.minio.identity.prefix,
            "permissions": ["read"],
            "ttl_seconds": 900,
        },
    ))
    assert credentials["credentials"]["bucket"] == world.minio.identity.bucket
    assert credentials["credentials"]["key_prefix"] == world.minio.identity.prefix
    assert credentials["credentials"]["credentials_ref"]
    assert credentials["credentials"]["expires_at_epoch"] > 0


async def test_trusted_runtime_provider_routes_use_durable_identity_and_ports(
    world: EntrypointWorld,
    http_client: httpx.AsyncClient,
) -> None:
    backend_digest = world.artifact.record.publication.backend_digest
    egress_payload = {
        "backend_digest": backend_digest,
        "request_id": "egress_" + uuid.uuid4().hex,
    }
    egress = await request(
        http_client,
        "POST",
        world.api_url + "/capabilities/v1/runtime/declarations/egress",
        token=world.state.token,
        payload=egress_payload,
    )
    assert egress["company_id"] == world.config.company_id
    assert egress["plugin_id"] == world.config.plugin_id
    assert egress["plugin_instance_id"] == world.config.plugin_instance_id
    assert egress["artifact_digest"] == world.artifact.record.digest
    assert egress["backend_digest"] == backend_digest
    assert egress["grant_id"] == world.config.grant_id
    assert egress["rules"] == [{
        "host": "egress.acceptance.test",
        "path_prefix": "/v1/egress",
        "method": "POST",
    }]
    assert egress["secret_refs"] == []

    metadata = []
    for base_url in (world.api_url, world.gateway_url):
        secret = await request(
            http_client,
            "POST",
            base_url + "/capabilities/v1/runtime/secrets/metadata",
            token=world.state.token,
            payload={
                "backend_digest": backend_digest,
                "request_id": "secret_" + uuid.uuid4().hex,
                "secret_ref": world.provider_secret_ref,
            },
        )
        assert secret["secret_ref"] == world.provider_secret_ref
        assert secret["value_digest"].startswith("sha256:")
        assert "value" not in secret and "secret" not in secret
        metadata.append(secret)
    assert metadata[0]["value_digest"] == metadata[1]["value_digest"]

    artifact = await request(
        http_client,
        "POST",
        world.gateway_url + "/capabilities/v1/runtime/artifacts/metadata",
        token=world.state.token,
        payload={
            "backend_digest": backend_digest,
            "request_id": "artifact_" + uuid.uuid4().hex,
            "job_id": world.config.provider_job_id,
            "target_ref": world.artifact.record.digest,
            "arguments_digest": EMPTY_DIGEST,
            "max_result_bytes": 4 * 1024 * 1024,
        },
    )
    assert artifact["action"] == "read_artifact"
    assert artifact["job_id"] == world.config.provider_job_id
    assert artifact["project_id"] == world.config.project_id
    assert artifact["pod_id"] == world.config.plugin_instance_id
    assert artifact["target_ref"] == world.artifact.record.digest
    assert artifact["byte_count"] > 0
