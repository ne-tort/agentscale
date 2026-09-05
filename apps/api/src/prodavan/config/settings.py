"""Application settings — L00/L01."""

from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _default_repo_root() -> Path:
    """Local: prodavan/; Docker image: /app (src lives at /app/src)."""
    here = Path(__file__).resolve()
    parents = here.parents
    if len(parents) > 5 and (parents[5] / "apps" / "api").is_dir():
        return parents[5]
    if len(parents) > 3:
        return parents[3]
    return Path("/app")


_REPO_ROOT = _default_repo_root()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    database_url: str = "postgresql+asyncpg://prodavan_app:prodavan@localhost:5432/prodavan"
    cors_origins: str = (
        "http://localhost:3000,http://localhost:8080,http://localhost:8088,http://localhost:5173,"
        "http://127.0.0.1:8080,http://127.0.0.1:8088"
    )
    api_v1_prefix: str = "/api/v1"
    storage_root: Path = _REPO_ROOT / "data" / "storage"

    # P0 Redis (C-CACHE). Empty = disabled (transitional; prod should set REDIS_URL).
    redis_url: str | None = None
    # When True, failed Redis ping on startup aborts process; readiness always requires Redis.
    redis_required: bool = False

    # P0 object storage (C-OBJECT-STORE). local = keys under storage_root; s3 = MinIO/AWS.
    object_store_backend: str = "local"  # local | s3
    s3_endpoint_url: str | None = None
    s3_access_key: str | None = None
    s3_secret_key: str | None = None
    s3_bucket: str = "prodavan"
    s3_region: str = "us-east-1"
    # When backend=s3, also write/read mirror under storage_root for local-ws agent cwd.
    object_store_mirror_local: bool = True
    object_store_required: bool = False

    app_name: str = "prodavan-api"
    app_version: str = "0.1.0-l08"
    build_id: str = "dev"

    app_env: str = "dev"  # dev | prod — prod forbids AUTH_MODE=test

    # L01 OIDC
    # auth_mode: oidc (JWKS) | test (HS256 AUTH_TEST_SECRET — CI/local only)
    auth_mode: str = "test"
    keycloak_issuer_url: str | None = None
    oidc_jwks_url: str | None = None
    oidc_audience: str = "prodavan-api"
    oidc_flutter_client_id: str = "prodavan-flutter"
    oidc_flutter_redirect_uri: str = "prodavan://oauth/callback"
    oidc_flutter_redirect_uri_desktop: str = "http://127.0.0.1:8765/oauth/callback"
    oidc_jwks_cache_seconds: int = 300
    auth_test_secret: str = "dev-only-test-secret-change-me"

    # L01 Keycloak Admin invite (fake | admin)
    keycloak_invite_mode: str = "fake"
    keycloak_url: str | None = None
    keycloak_realm: str = "prodavan"
    keycloak_admin_client_id: str | None = None
    keycloak_admin_client_secret: str | None = None

    vault_addr: str | None = None
    vault_token: str | None = None
    vault_kv_mount: str = "secret"
    vault_kv_path_prefix: str = "prodavan/ai_keys"
    secrets_dir: Path = _REPO_ROOT / "data" / "secrets"

    # L04 admin metrics alerts (0 = disabled)
    admin_metrics_token_alert_threshold: int = 50_000
    admin_metrics_subscription_expiring_days: int = 30
    # Redis presence TTL for auth.login / token_refreshed (seconds).
    metrics_presence_ttl_sec: int = 900
    kafka_topic_metrics_events: str = "prodavan.metrics.events"
    kafka_metrics_group: str = "prodavan-metrics"
    metrics_sample_ttl_sec: int = 60
    metrics_sample_interval_sec: int = 15
    metrics_storage_sampler_enabled: bool = True
    metrics_storage_sample_interval_sec: int = 300
    metrics_delta_min_cpu_millicores: int = 50
    metrics_delta_min_memory_bytes: int = 32 * 1024 * 1024

    # L07 local-ws MCP package processes (opt-in; no bubblewrap/k8s yet)
    mcp_sandbox_spawn: bool = False
    # I8: API may POST Jobs via in-cluster SA. Off by default — create path stays object-ws.
    sandbox_k8s_jobs: bool = False
    sandbox_k8s_namespace: str = "prodavan"
    # pod_service runtime: stub (object-ws) | k8s (real Pod adapter).
    pod_runtime_mode: str = "k8s"
    pod_reconcile_worker_enabled: bool = False
    pod_sandbox_namespace: str = "prodavan-sandboxes"
    pod_sandbox_image: str = "ghcr.io/ne-tort/prodavan-api:local"
    pod_sandbox_hydrate_image: str = "ghcr.io/ne-tort/prodavan-api:local"
    pod_sandbox_sa: str = "prodavan-project-pod"
    pod_sandbox_minio_secret: str = ""  # e.g. prodavan-minio-hydrate; empty = stub hydrate tree
    pod_sandbox_image_pull_secret: str = "ghcr-pull"
    pod_sandbox_cpu_request: str = "100m"
    pod_sandbox_cpu_limit: str = "1000m"
    pod_sandbox_memory_request: str = "256Mi"
    pod_sandbox_memory_limit: str = "1Gi"
    pod_ready_timeout_sec: int = 75
    # L15 agent-runtime — single container Pod workload (Platform OpenClaw + SDK adapters).
    pod_agent_runtime_enabled: bool = Field(
        default=False,
        validation_alias=AliasChoices("POD_AGENT_RUNTIME_ENABLED", "POD_AGENT_BRIDGE_ENABLED"),
    )
    pod_agent_runtime_image: str = Field(
        default="ghcr.io/ne-tort/prodavan-agent-runtime:local",
        validation_alias=AliasChoices("POD_AGENT_RUNTIME_IMAGE", "POD_AGENT_BRIDGE_IMAGE"),
    )
    pod_agent_runtime_port: int = Field(
        default=3921,
        validation_alias=AliasChoices("POD_AGENT_RUNTIME_PORT", "POD_AGENT_BRIDGE_PORT"),
    )
    pod_agent_runtime_api_base_url: str = Field(
        default="http://prodavan-api.prodavan.svc:8000/api/v1",
        validation_alias=AliasChoices(
            "POD_AGENT_RUNTIME_API_BASE_URL",
            "POD_AGENT_BRIDGE_API_BASE_URL",
        ),
    )
    pod_agent_runtime_auth_secret: str = Field(
        default="",
        validation_alias=AliasChoices(
            "POD_AGENT_RUNTIME_AUTH_SECRET",
            "POD_AGENT_BRIDGE_AUTH_SECRET",
        ),
    )
    pod_agent_runtime_auth_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "POD_AGENT_RUNTIME_AUTH_TOKEN",
            "POD_AGENT_BRIDGE_AUTH_TOKEN",
        ),
    )
    pod_agent_runtime_token: str = Field(
        default="",
        validation_alias=AliasChoices("POD_AGENT_RUNTIME_TOKEN", "POD_AGENT_BRIDGE_TOKEN"),
    )
    pod_agent_runtime_bootstrap_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices(
            "POD_AGENT_RUNTIME_BOOTSTRAP_ENABLED",
            "POD_AGENT_BRIDGE_BOOTSTRAP_ENABLED",
        ),
    )
    # When True, allow FakeAgentAdapter / FixtureCursorAdapter in-process (pytest only).
    agent_inprocess_adapters_enabled: bool = False
    projects_auto_rematerialize_on_cabinet_change: bool = Field(
        default=False,
        validation_alias=AliasChoices(
            "PROJECTS_AUTO_REMATERIALIZE_ON_CABINET_CHANGE",
        ),
    )

    @property
    def pod_agent_bridge_enabled(self) -> bool:
        return self.pod_agent_runtime_enabled

    @pod_agent_bridge_enabled.setter
    def pod_agent_bridge_enabled(self, value: bool) -> None:
        self.pod_agent_runtime_enabled = value

    @property
    def pod_agent_bridge_image(self) -> str:
        return self.pod_agent_runtime_image

    @pod_agent_bridge_image.setter
    def pod_agent_bridge_image(self, value: str) -> None:
        self.pod_agent_runtime_image = value

    @property
    def pod_agent_bridge_port(self) -> int:
        return self.pod_agent_runtime_port

    @pod_agent_bridge_port.setter
    def pod_agent_bridge_port(self, value: int) -> None:
        self.pod_agent_runtime_port = value

    @property
    def pod_agent_bridge_api_base_url(self) -> str:
        return self.pod_agent_runtime_api_base_url

    @pod_agent_bridge_api_base_url.setter
    def pod_agent_bridge_api_base_url(self, value: str) -> None:
        self.pod_agent_runtime_api_base_url = value

    @property
    def pod_agent_bridge_auth_secret(self) -> str:
        return self.pod_agent_runtime_auth_secret

    @pod_agent_bridge_auth_secret.setter
    def pod_agent_bridge_auth_secret(self, value: str) -> None:
        self.pod_agent_runtime_auth_secret = value

    @property
    def pod_agent_bridge_auth_token(self) -> str:
        return self.pod_agent_runtime_auth_token

    @pod_agent_bridge_auth_token.setter
    def pod_agent_bridge_auth_token(self, value: str) -> None:
        self.pod_agent_runtime_auth_token = value

    @property
    def pod_agent_bridge_token(self) -> str:
        return self.pod_agent_runtime_token

    @pod_agent_bridge_token.setter
    def pod_agent_bridge_token(self, value: str) -> None:
        self.pod_agent_runtime_token = value

    @property
    def pod_agent_bridge_bootstrap_enabled(self) -> bool:
        return self.pod_agent_runtime_bootstrap_enabled

    @pod_agent_bridge_bootstrap_enabled.setter
    def pod_agent_bridge_bootstrap_enabled(self, value: bool) -> None:
        self.pod_agent_runtime_bootstrap_enabled = value
    pod_metrics_grace_sec: int = 30
    pod_provisioning_timeout_sec: int = 30
    pod_preparing_timeout_sec: int = 120
    pod_k8s_required: bool = False
    sandbox_k8s_pvc: str = "prodavan-api-storage"
    sandbox_k8s_job_image: str = "ghcr.io/ne-tort/prodavan-api:local"
    # L06/L07 invoke src/on_platform_event.py from package zip on platform_events (opt-in)
    mcp_platform_event_invoke: bool = False

    # L07/L08 background trigger drain (opt-in asyncio loop in API process)
    trigger_worker_enabled: bool = False
    trigger_worker_interval_sec: float = 5.0
    trigger_worker_max_projects: int = 20
    trigger_worker_batch_max: int = 10
    # Row-level outbox lease (crash recovery without external broker)
    trigger_outbox_lease_sec: int = 120
    trigger_outbox_max_attempts: int = 5
    trigger_outbox_backoff_sec: float = 5.0
    # Idle pause sweep shares the trigger worker loop when enabled (default off).
    idle_pause_worker_enabled: bool = False

    # P0 Celery (C-JOBS). When enabled + broker, in-process trigger loop is skipped.
    celery_enabled: bool = False
    celery_broker_url: str | None = None  # default: REDIS_URL
    celery_result_backend: str | None = None  # default: broker
    # Eager mode for unit tests (no broker needed).
    celery_task_always_eager: bool = False
    # Fail /health/ready when Celery enabled but broker unreachable.
    celery_required: bool = False

    # P0 Kafka (C-EVENT-BUS). Dual-write from PG emit/enqueue; consumer cutover later.
    kafka_enabled: bool = False
    kafka_bootstrap_servers: str | None = None  # e.g. localhost:9092
    kafka_client_id: str = "prodavan-api"
    kafka_topic_platform_events: str = "prodavan.platform.events"
    kafka_topic_project_triggers: str = "prodavan.project.triggers"
    kafka_topic_auth_commands: str = "prodavan.auth.commands"
    kafka_topic_auth_events: str = "prodavan.auth.events"
    kafka_topic_relation_events: str = "prodavan.relation.events"
    kafka_required: bool = False
    # Optional consumer: kick Celery drain, or per-id dispatch (PG claim still SoT).
    kafka_consumer_enabled: bool = False
    kafka_consumer_group: str = "prodavan-api-triggers"
    kafka_auth_commands_group: str = "prodavan-auth-commands"
    kafka_auth_events_group: str = "prodavan-auth-events"
    kafka_relation_events_group: str = "prodavan-relation-events"
    kafka_metrics_presence_group: str = "prodavan-metrics-presence"
    kafka_drain_debounce_sec: float = 1.0
    # kick = debounce → trigger_drain; dispatch = enqueue dispatch_trigger(event_id).
    kafka_consumer_mode: str = "kick"
    # When true + consumer enabled: rematerialize scheduler publishes platform bus command
    # instead of direct Celery enqueue; platform consumer enqueues the task.
    kafka_rematerialize_via_bus: bool = False
    kafka_platform_jobs_group: str = "prodavan-platform-jobs"

    # External webhook/telegram ingress rate limit (C-CACHE); 0 = disabled.
    ingress_rate_limit_per_minute: int = 120

    # Content Service: mirror project attachments into content_assets (transitional).
    content_attachments_via_assets: bool = False
    # Admin ops (drain/sweep/gc) and MCP call rate limits; 0 = disabled.
    admin_ops_rate_limit_per_minute: int = 60
    mcp_call_rate_limit_per_minute: int = 180

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
