"""One owned real-service world for the two entrypoints and standalone worker."""
from __future__ import annotations

import asyncio
import json
import secrets as secret_values
import sys
import uuid
from datetime import UTC, datetime, timedelta

import jwt
from dataclasses import replace
from pathlib import Path
from typing import Any

import redis.asyncio as redis
from motor.motor_asyncio import AsyncIOMotorClient
from sqlalchemy.engine import make_url

from prodavan.application.document_store.adapters.mongo_store import MongoDocumentStore
from prodavan.application.plugins.artifacts.oci import parse_oci_ref
from prodavan.application.plugins.capabilities.adapters.secrets import (
    secret_delivery_allowlist,
    validate_secret_delivery_ref,
)
from prodavan.application.plugins.installation_scope.repository import SqlInstallationScopeRepository
from prodavan.application.plugins.jobs.declarations import published_operations
from prodavan.application.plugins.objects import policy as iam
from prodavan.infrastructure.secrets.file_store import FileSecretStore
from tests.integration.minio_plugin_acceptance.config import (
    AcceptanceConfig as MinioAcceptanceConfig,
)
from tests.integration.minio_plugin_acceptance.resources import (
    AcceptanceIdentity as MinioIdentity,
    ResourceProvisioner,
)

from .artifact import VerifiedTestArtifact, publish_verified_test_artifact
from .config import AcceptanceConfig
from .database import DisposablePostgres
from .durable import BootstrapIdentity, DurableState, seed_bootstrap_identity, seed_durable_state
from .processes import (
    ManagedProcess,
    ProcessSupervisor,
    celery_worker_process,
    ephemeral_localhost_port,
    isolated_environment,
    uvicorn_process,
)


def validated_oci_registry(bundle_ref: str) -> str:
    """Return the production-parsed registry from a schemeless digest-pinned ref."""
    ref = parse_oci_ref(bundle_ref)
    if not ref.registry.strip() or not ref.digest:
        raise RuntimeError("OCI registry and digest are required")
    return ref.registry


def build_minio_acceptance_config(
    *,
    endpoint: str,
    root_access_key: str,
    root_secret_key: str,
    admin_secret_key: str,
    presign_secret_key: str,
    mc_binary: str,
    region: str,
) -> MinioAcceptanceConfig:
    """Require three independent secret values before any real provisioning."""
    secrets = (root_secret_key, admin_secret_key, presign_secret_key)
    if any(not value for value in secrets) or len(set(secrets)) != 3:
        raise RuntimeError("root, MinIO admin and presign secrets must be non-empty and distinct")
    return MinioAcceptanceConfig(
        endpoint=endpoint,
        root_access_key=root_access_key,
        root_secret_key=root_secret_key,
        admin_secret_key=admin_secret_key,
        presign_secret_key=presign_secret_key,
        mc_binary=mc_binary,
        region=region,
    )


class EntrypointWorld:
    def __init__(self, *, config: AcceptanceConfig, workspace: Path) -> None:
        self.config = config
        self.workspace = workspace
        self.api_port = ephemeral_localhost_port()
        self.gateway_port = ephemeral_localhost_port()
        if self.api_port == self.gateway_port:
            raise RuntimeError("OS assigned the same ephemeral port twice")
        self.api_url = f"http://127.0.0.1:{self.api_port}"
        self.gateway_url = f"http://127.0.0.1:{self.gateway_port}"
        self.postgres: DisposablePostgres | None = None
        self.artifact: VerifiedTestArtifact | None = None
        self.state: DurableState | None = None
        self.bootstrap_identity: BootstrapIdentity | None = None
        self.bootstrap_scope: Any = None
        self.employee_auth_secret = secret_values.token_urlsafe(48)
        self.employee_token = ""
        self.minio_provisioner: ResourceProvisioner | None = None
        self.minio: Any = None
        self.mongo_client: Any = None
        self.redis: Any = None
        self.processes = ProcessSupervisor(shutdown_timeout=config.shutdown_timeout_seconds)
        self.environment: dict[str, str] = {}
        self.sensitive: set[str] = set()
        self.claims_secret = secret_values.token_urlsafe(48)
        self.event_secret = secret_values.token_urlsafe(48)
        self.secret_root = workspace / "secrets"
        self.document_id = f"doc_{uuid.uuid4().hex}"
        self.document_collection = "entrypoint_acceptance"
        self.mcp_server_id = ""
        self.mcp_tool_name = ""
        self.job_operation = ""
        self.slow_job_operation = ""
        self.provider_secret_ref = ""
        self._secret_refs: set[str] = set()
        self._closed = False

    def _mint_employee_token(self) -> str:
        assert self.bootstrap_identity is not None
        now = datetime.now(UTC)
        return jwt.encode({
            "sub": self.bootstrap_identity.keycloak_sub,
            "aud": "entrypoint-acceptance",
            "exp": now + timedelta(hours=1),
            "iat": now,
            "email": "employee@entrypoint.test",
        }, self.employee_auth_secret, algorithm="HS256")

    @classmethod
    async def open(cls, *, config: AcceptanceConfig, workspace: Path) -> "EntrypointWorld":
        world = cls(config=config, workspace=workspace)
        try:
            await world._open()
        except Exception:
            await world.aclose()
            raise
        return world

    def _put_secret(self, key: str, value: str) -> str:
        self.secret_root.mkdir(parents=True, exist_ok=True)
        self.sensitive.add(value)
        ref = FileSecretStore(root=self.secret_root).put(key, value)
        self._secret_refs.add(ref)
        return ref

    async def _provision_minio(self) -> None:
        config = self.config
        run = f"a{uuid.uuid4().hex[:20]}"
        admin_secret = "entrypoint-admin-" + secret_values.token_urlsafe(36)
        presign_secret = "entrypoint-presign-" + secret_values.token_urlsafe(48)
        self.sensitive.update({admin_secret, presign_secret})
        minio_config = build_minio_acceptance_config(
            endpoint=config.minio_endpoint,
            root_access_key=config.minio_root_access_key,
            root_secret_key=config.minio_root_secret_key,
            admin_secret_key=admin_secret,
            presign_secret_key=presign_secret,
            mc_binary=config.minio_mc_binary,
            region=config.minio_region,
        )
        workspace = self.workspace / "minio"
        workspace.mkdir(parents=True, exist_ok=True)
        provisioner = ResourceProvisioner(minio_config, workspace)
        self.minio_provisioner = provisioner
        prefix = iam.module_prefix(config.plugin_id)
        foreign_plugin = f"{config.plugin_id}_foreign"
        identity = replace(
            provisioner.identity,
            run_id=run,
            company_id=config.company_id,
            plugin_id=config.plugin_id,
            foreign_plugin_id=foreign_plugin,
            missing_company_id=config.foreign_company_id,
            bucket=iam.bucket_name(config.company_id),
            foreign_bucket=iam.bucket_name(config.foreign_company_id) + "-foreign",
            missing_bucket=iam.bucket_name(config.foreign_company_id),
            prefix=prefix,
            fixed_key=prefix + "__authorization_canary__",
            foreign_key=iam.module_prefix(foreign_plugin) + "__authorization_canary__",
            missing_key=prefix + "missing-" + run,
            upload_key=prefix + "upload-" + run + ".bin",
            admin_user="acc-admin-" + run,
            presign_user="acc-presign-" + run,
            admin_policy="acc-admin-policy-" + run,
            presign_policy="acc-presign-policy-" + run,
        )
        assert isinstance(identity, MinioIdentity)
        provisioner.identity = identity
        self.minio = await asyncio.to_thread(provisioner.provision)

    async def _seed_mongo(self) -> None:
        assert self.state is not None
        self.mongo_client = AsyncIOMotorClient(
            self.config.mongodb_url, serverSelectionTimeoutMS=5000
        )
        db = self.mongo_client[self.config.mongodb_db]
        await db.command("ping")
        store = MongoDocumentStore(db=db)
        for company, instance, marker in (
            (self.config.company_id, self.config.plugin_instance_id, "primary"),
            (self.config.foreign_company_id, self.config.foreign_plugin_instance_id, "foreign"),
        ):
            result = await store.upsert(
                namespace="plugins",
                collection=self.document_collection,
                filter={"id": self.document_id if marker == "primary" else self.document_id + "_foreign"},
                document={
                    "id": self.document_id if marker == "primary" else self.document_id + "_foreign",
                    "company_id": company,
                    "plugin_instance_id": instance,
                    "marker": marker,
                },
            )
            assert result.matched >= 0

    def _select_manifest_operations(self) -> None:
        assert self.artifact is not None
        operations = published_operations(
            (self.artifact.record,), (self.artifact.declaration,)
        )
        get_operations = [
            item for item in operations
            if item.declaration.method == "GET" and "slow" not in item.declaration.operation
        ]
        slow_operations = [
            item for item in operations
            if item.declaration.method == "GET" and "slow" in item.declaration.operation
        ]
        if not get_operations or len(slow_operations) != 1:
            raise RuntimeError(
                "test artifact needs a normal GET job and exactly one signed slow GET lease operation"
            )
        self.job_operation = get_operations[0].declaration.key
        self.slow_job_operation = slow_operations[0].declaration.key
        servers = self.artifact.record.manifest.get("mcp_servers")
        if not isinstance(servers, list) or len(servers) != 1:
            raise RuntimeError("test artifact must declare exactly one MCP server")
        server = servers[0]
        tools = server.get("tools") if isinstance(server, dict) else None
        if not isinstance(server.get("server_id"), str) or not isinstance(tools, list) or not tools:
            raise RuntimeError("test MCP server/tools are absent")
        self.mcp_server_id = server["server_id"]
        self.mcp_tool_name = tools[0].get("name", "")
        if not self.mcp_tool_name:
            raise RuntimeError("test MCP tool name is absent")
        if self.artifact.record.manifest.get("public_pages") != ["/"]:
            raise RuntimeError("test artifact must declare exactly public page / for company-only bootstrap")

    async def _open(self) -> None:
        self.postgres = await DisposablePostgres.open(self.config)
        # ModuleSource and its exact ProjectRow anchors must predate the
        # artifact FK and the source-backed installation scope binding.
        self.bootstrap_identity = await seed_bootstrap_identity(
            config=self.config,
            session_factory=self.postgres.session_factory,
        )
        self.artifact = await publish_verified_test_artifact(
            config=self.config,
            session_factory=self.postgres.session_factory,
        )
        self._select_manifest_operations()
        await self._provision_minio()
        assert self.minio is not None
        self.state = await seed_durable_state(
            config=self.config,
            artifact=self.artifact,
            session_factory=self.postgres.session_factory,
            bucket=self.minio.identity.bucket,
            foreign_bucket=self.minio.identity.foreign_bucket,
            claims_secret=self.claims_secret,
        )
        self.bootstrap_scope = await SqlInstallationScopeRepository(
            session_factory=self.postgres.session_factory
        ).ensure_from_source(
            company_id=self.config.company_id,
            plugin_id=self.config.plugin_id,
            plugin_instance_id=self.config.plugin_instance_id,
        )
        self.employee_token = self._mint_employee_token()
        await self._seed_mongo()

        self.redis = redis.from_url(
            self.config.redis_dsn,
            encoding="utf-8",
            decode_responses=True,
            socket_connect_timeout=5.0,
            socket_timeout=10.0,
        )
        await self.redis.ping()
        # REDIS_DB is explicitly dedicated to this isolated harness. Never flush
        # an ambient/default DB; config requires the explicit isolated contract.
        await self.redis.flushdb()

        event_ref = self._put_secret("entrypoint-event", self.event_secret)
        kafka_user_ref = self._put_secret("entrypoint-kafka-user", self.config.kafka_sasl_username)
        kafka_password_ref = self._put_secret("entrypoint-kafka-password", self.config.kafka_sasl_password)
        minio_config = self.minio.plugin_config
        admin_user_ref = self._put_secret("entrypoint-minio-admin-user", minio_config.admin_access_key)
        admin_password_ref = self._put_secret("entrypoint-minio-admin-password", minio_config.admin_secret_key)
        presign_user_ref = self._put_secret("entrypoint-minio-presign-user", minio_config.presign_access_key)
        presign_password_ref = self._put_secret("entrypoint-minio-presign-password", minio_config.presign_secret_key)
        allowed_secrets = secret_delivery_allowlist(self.artifact.record.manifest)
        if len(allowed_secrets) != 1 or not allowed_secrets[0].startswith("file://ai_keys/"):
            raise RuntimeError("test manifest must declare exactly one file://ai_keys secret ref")
        requested_ref = allowed_secrets[0]
        validate_secret_delivery_ref(
            company_id=self.config.company_id,
            plugin_id=self.config.plugin_id,
            plugin_instance_id=self.config.plugin_instance_id,
            secret_ref=requested_ref,
        )
        key_id = requested_ref.removeprefix("file://ai_keys/").removesuffix(".secret")
        self.provider_secret_ref = self._put_secret(
            key_id, "entrypoint-secret-" + uuid.uuid4().hex
        )
        if self.provider_secret_ref != requested_ref:
            raise RuntimeError("secret store did not preserve the signed allowlist reference")

        admin_url = make_url(self.config.postgres_admin_dsn)
        tenant_admin_url = admin_url.set(
            drivername="postgresql",
            query={**dict(admin_url.query), "sslmode": "require"},
        ).render_as_string(hide_password=False)
        backend_digest = self.artifact.record.publication.backend_digest
        assert isinstance(backend_digest, str)
        explicit: dict[str, str] = {
            "APP_ENVIRONMENT": "dev",
            "APP_NAME": "entrypoint-acceptance-api",
            "APP_VERSION": "acceptance-unexecuted",
            "BUILD_ID": "acceptance-unexecuted",
            "DATABASE_URL": self.postgres.dsn,
            "REDIS_URL": self.config.redis_dsn,
            "REDIS_REQUIRED": "true",
            "MONGODB_URL": self.config.mongodb_url,
            "MONGODB_DB": self.config.mongodb_db,
            "MONGODB_ENABLED": "true",
            "MONGODB_REQUIRED": "true",
            "OPENSEARCH_ENABLED": "false",
            "OPENSEARCH_REQUIRED": "false",
            "OBJECT_STORE_BACKEND": "local",
            "OBJECT_STORE_REQUIRED": "false",
            "STORAGE_ROOT": str(self.workspace / "storage"),
            "SECRETS_DIR": str(self.secret_root),
            "POD_RUNTIME_MODE": "k8s",
            "POD_K8S_REQUIRED": "true",
            "POD_SANDBOX_NAMESPACE": self.config.k8s_namespace,
            "POD_AGENT_RUNTIME_ENABLED": "false",
            "KAFKA_BOOTSTRAP_SERVERS": self.config.kafka_bootstrap_servers,
            # Core dual-write has no SASL_SSL configuration. It is deliberately
            # off; the independent mandatory plugin adapter uses real SASL_SSL.
            "KAFKA_ENABLED": "false",
            "KAFKA_REQUIRED": "false",
            "KAFKA_CONSUMER_ENABLED": "false",
            "CELERY_ENABLED": "true",
            "CELERY_REQUIRED": "true",
            "CELERY_BROKER_URL": self.config.redis_dsn,
            "CELERY_RESULT_BACKEND": self.config.redis_dsn,
            "CELERY_TASK_ALWAYS_EAGER": "false",
            "TRIGGER_WORKER_ENABLED": "false",
            "IDLE_PAUSE_WORKER_ENABLED": "false",
            "AGENT_RUN_WORKER_ENABLED": "false",
            "AGENT_RUN_CONSUMER_ENABLED": "false",
            "INFRA_STARTUP_PING_ATTEMPTS": "3",
            "INFRA_STARTUP_PING_DELAY_SEC": "0.2",
            "PLUGIN_PLATFORM_ENABLED": "true",
            "PLUGIN_JOB_DECLARATIONS": self.artifact.declarations_json,
            "PLUGIN_CLAIMS_SECRET": self.claims_secret,
            "PLUGIN_EVENT_SIGNING_SECRET_REF": event_ref,
            "PLUGIN_CLAIMS_TTL_SECONDS": "300",
            "PLUGIN_CLAIMS_ISSUER": "agentscale-plugins",
            "PLUGIN_CLAIMS_AUDIENCE": "capability-gateway",
            "PLUGIN_STATIC_ORIGIN": "https://static.acceptance.test",
            "PLUGIN_SHELL_ORIGIN": "https://shell.acceptance.test",
            "AUTH_MODE": "test",
            "AUTH_TEST_SECRET": self.employee_auth_secret,
            "OIDC_AUDIENCE": "entrypoint-acceptance",
            "PLUGIN_TENANT_DB_ADMIN_URL": tenant_admin_url,
            "PLUGIN_TENANT_DB_ADMIN_TLS_MODE": "require",
            "PLUGIN_TENANT_DB_HOST": str(admin_url.host or ""),
            "PLUGIN_TENANT_DB_PORT": str(admin_url.port or 5432),
            "PLUGIN_EGRESS_MODE": "webhooks-only",
            "PLUGIN_EGRESS_APPROVED": "false",
            "PLUGIN_IDLE_TTL_SECONDS": "300",
            "PLUGIN_READY_TIMEOUT_SECONDS": str(max(30, int(self.config.operation_timeout_seconds))),
            "PLUGIN_MAX_CONCURRENT_WAKES": "2",
            "PLUGIN_OCI_REGISTRY": validated_oci_registry(self.config.bundle_ref),
            "PLUGIN_COSIGN_IDENTITY": self.config.cosign_identity,
            "PLUGIN_COSIGN_ISSUER": self.config.cosign_issuer,
            "PLUGIN_MIN_PLATFORM_VERSION": "4.0.0",
            "PLUGIN_RUNTIME_NAMESPACE": self.config.k8s_namespace,
            "PLUGIN_RUNTIME_STATE_CONFIGMAP": self.config.k8s_state_configmap,
            "PLUGIN_RUNTIME_GATEWAY_URL": self.config.internal_gateway_url(self.gateway_port),
            "PLUGIN_RUNTIME_COSIGN_BIN": self.config.cosign_binary,
            "PLUGIN_RUNTIME_COSIGN_TIMEOUT": str(self.config.operation_timeout_seconds),
            "PLUGIN_RUNTIME_KMS_KEY_REF": self.config.cosign_kms_key_ref or "",
            "KUBERNETES_SERVICE_HOST": self.config.k8s_service_host,
            "KUBERNETES_SERVICE_PORT": self.config.k8s_service_port,
            "PLUGIN_KAFKA_SASL_USERNAME_REF": kafka_user_ref,
            "PLUGIN_KAFKA_SASL_PASSWORD_REF": kafka_password_ref,
            "PLUGIN_KAFKA_TLS_CA_FILE": self.config.kafka_tls_ca_file,
            "PLUGIN_KAFKA_SASL_MECHANISM": self.config.kafka_sasl_mechanism,
            "PLUGIN_MINIO_ENDPOINT": minio_config.endpoint,
            "PLUGIN_MINIO_PUBLIC_ENDPOINT": minio_config.endpoint,
            "PLUGIN_MINIO_ADMIN_ACCESS_KEY_REF": admin_user_ref,
            "PLUGIN_MINIO_ADMIN_SECRET_KEY_REF": admin_password_ref,
            "PLUGIN_MINIO_PRESIGN_ACCESS_KEY_REF": presign_user_ref,
            "PLUGIN_MINIO_PRESIGN_SECRET_KEY_REF": presign_password_ref,
            "PLUGIN_MINIO_STS_ENABLED": "true",
            "PLUGIN_MINIO_STS_ENDPOINT": minio_config.sts_endpoint_url,
            "PLUGIN_MINIO_STS_SESSION_DURATION_SECONDS": "900",
            "PLUGIN_MINIO_STS_PROBE_ENABLED": "true",
            "PLUGIN_MINIO_STS_PROBE_TIMEOUT_SECONDS": "15",
            "PLUGIN_MINIO_STS_PROBE_REVALIDATE_SECONDS": "60",
            "PLUGIN_MINIO_STS_PROBE_RETRY_SECONDS": "1",
            "PLUGIN_MINIO_STS_PROBE_BUCKET": self.minio.identity.bucket,
            "PLUGIN_MINIO_STS_PROBE_KEY": self.minio.identity.fixed_key,
            "PLUGIN_MINIO_REGION": self.config.minio_region,
            "PLUGIN_MODULE_SOURCE_ENABLED": "false",
            "PLUGIN_CI_PUBLICATION_ENABLED": "false",
            "ENTRYPOINT_TEST_COMPANY_ID": self.config.company_id,
            "ENTRYPOINT_TEST_FOREIGN_COMPANY_ID": self.config.foreign_company_id,
            "ENTRYPOINT_TEST_PLUGIN_ID": self.config.plugin_id,
            "ENTRYPOINT_TEST_INSTANCE_ID": self.config.plugin_instance_id,
            "ENTRYPOINT_TEST_FOREIGN_INSTANCE_ID": self.config.foreign_plugin_instance_id,
            "ENTRYPOINT_TEST_GRANT_ID": self.config.grant_id,
            "ENTRYPOINT_TEST_FOREIGN_GRANT_ID": self.config.foreign_grant_id,
            "ENTRYPOINT_TEST_VERSION": self.config.version,
            "ENTRYPOINT_TEST_ARTIFACT_DIGEST": self.artifact.record.digest,
            "ENTRYPOINT_TEST_BACKEND_DIGEST": backend_digest,
            "ENTRYPOINT_TEST_JOB_OPERATION": self.job_operation,
            "ENTRYPOINT_TEST_SLOW_JOB_OPERATION": self.slow_job_operation,
            "ENTRYPOINT_TEST_MCP_SERVER_ID": self.mcp_server_id,
            "ENTRYPOINT_TEST_MCP_TOOL_NAME": self.mcp_tool_name,
            "ENTRYPOINT_TEST_PROVIDER_JOB_ID": self.config.provider_job_id,
            "ENTRYPOINT_TEST_PROJECT_ID": self.config.project_id,
            "ENTRYPOINT_TEST_PROVIDER_SECRET_REF": self.provider_secret_ref,
            "ENTRYPOINT_TEST_DOCUMENT_ID": self.document_id,
            "ENTRYPOINT_TEST_DOCUMENT_COLLECTION": self.document_collection,
            "ENTRYPOINT_TEST_OBJECT_BUCKET": self.minio.identity.bucket,
            "ENTRYPOINT_TEST_OBJECT_PREFIX": self.minio.identity.prefix,
            "ENTRYPOINT_TEST_OBJECT_KEY": self.minio.identity.fixed_key,
        }
        self.environment = isolated_environment(explicit)
        self.sensitive.update({
            self.postgres.dsn,
            self.config.postgres_admin_dsn,
            self.config.redis_dsn,
            self.config.mongodb_url,
            tenant_admin_url,
            self.claims_secret,
            self.event_secret,
            self.employee_auth_secret,
            self.employee_token,
            self.config.kafka_sasl_username,
            self.config.kafka_sasl_password,
            self.config.minio_root_access_key,
            self.config.minio_root_secret_key,
            minio_config.admin_access_key,
            minio_config.admin_secret_key,
            minio_config.presign_access_key,
            minio_config.presign_secret_key,
        })
        await self._start_processes()

    async def _start_processes(self) -> None:
        api = self.processes.add(uvicorn_process(
            name="api",
            module="prodavan.main",
            port=self.api_port,
            env=self.environment,
            shutdown_timeout=self.config.shutdown_timeout_seconds,
            sensitive=self.sensitive,
        ))
        await api.start()
        await api.wait_http("/health/live", base_url=self.api_url,
                            timeout=self.config.startup_timeout_seconds,
                            expected={"status": "alive"})
        await api.wait_http("/health/ready", base_url=self.api_url,
                            timeout=self.config.startup_timeout_seconds)
        gateway = self.processes.add(uvicorn_process(
            name="gateway",
            module="prodavan.main_plugin_gateway",
            port=self.gateway_port,
            env=self.environment,
            shutdown_timeout=self.config.shutdown_timeout_seconds,
            sensitive=self.sensitive,
        ))
        await gateway.start()
        await gateway.wait_http("/health/live", base_url=self.gateway_url,
                                timeout=self.config.startup_timeout_seconds,
                                expected={"status": "alive"})
        await gateway.wait_http("/health/ready", base_url=self.gateway_url,
                                timeout=self.config.startup_timeout_seconds,
                                expected={"status": "ready"})
        worker = self.processes.add(celery_worker_process(
            env=self.environment,
            shutdown_timeout=self.config.shutdown_timeout_seconds,
            sensitive=self.sensitive,
        ))
        await worker.start()
        await self.run_helper("worker-ping")

    def helper(self, *arguments: str, env: dict[str, str] | None = None) -> ManagedProcess:
        return ManagedProcess(
            name="helper-" + "-".join(arguments),
            argv=[
                sys.executable,
                "-m", "tests.integration.entrypoint_acceptance.probe", *arguments,
            ],
            env=self.environment if env is None else env,
            shutdown_timeout=self.config.shutdown_timeout_seconds,
            sensitive=self.sensitive,
        )

    async def run_helper(self, *arguments: str) -> dict[str, Any]:
        process = self.helper(*arguments)
        self.processes.add(process)
        await process.start()
        await process.wait_exit(timeout=self.config.operation_timeout_seconds)
        return process.json_result()

    async def aclose(self) -> None:
        if self._closed:
            return
        self._closed = True
        first: Exception | None = None
        for action in (
            self.processes.aclose,
            self._close_secrets,
            self._close_redis,
            self._close_mongo,
            self._close_minio,
            self._close_postgres,
        ):
            try:
                await action()
            except Exception as exc:
                if first is None:
                    first = exc
        if first is not None:
            raise first

    async def _close_secrets(self) -> None:
        """Delete every created ref and prove no secret file remains after shutdown."""
        if not self.secret_root.exists():
            self._secret_refs.clear()
            return
        store = FileSecretStore(root=self.secret_root)
        for ref in sorted(self._secret_refs):
            store.delete(ref)
        self._secret_refs.clear()
        remaining_files = [path for path in self.secret_root.rglob("*") if path.is_file()]
        if remaining_files:
            raise RuntimeError(
                f"secret cleanup left {len(remaining_files)} unexpected file(s); values are not logged"
            )
        directories = sorted(
            (path for path in self.secret_root.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
            reverse=True,
        )
        for directory in directories:
            directory.rmdir()
        self.secret_root.rmdir()

    async def _close_redis(self) -> None:
        if self.redis is not None:
            try:
                await self.redis.flushdb()
            finally:
                await self.redis.aclose()

    async def _close_mongo(self) -> None:
        if self.mongo_client is not None:
            try:
                await self.mongo_client.drop_database(self.config.mongodb_db)
            finally:
                self.mongo_client.close()

    async def _close_minio(self) -> None:
        if self.minio_provisioner is not None:
            try:
                await asyncio.to_thread(self.minio_provisioner.cleanup)
            finally:
                await asyncio.to_thread(self.minio_provisioner.close)

    async def _close_postgres(self) -> None:
        if self.postgres is not None:
            await self.postgres.aclose()
