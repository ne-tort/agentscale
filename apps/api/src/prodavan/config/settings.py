"""Application settings — L00/L01."""

from pathlib import Path

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
        "http://localhost:3000,http://localhost:8080,http://localhost:5173,"
        "http://127.0.0.1:8080,http://prodavan.local,http://api.prodavan.local"
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
    starter_bundles_dir: Path = _REPO_ROOT / "apps" / "api" / "fixtures" / "starter_bundles"

    # L07 local-ws MCP package processes (opt-in; no bubblewrap/k8s yet)
    mcp_sandbox_spawn: bool = False
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
    kafka_required: bool = False
    # Optional consumer: kick Celery drain, or per-id dispatch (PG claim still SoT).
    kafka_consumer_enabled: bool = False
    kafka_consumer_group: str = "prodavan-api-triggers"
    kafka_drain_debounce_sec: float = 1.0
    # kick = debounce → trigger_drain; dispatch = enqueue dispatch_trigger(event_id).
    kafka_consumer_mode: str = "kick"

    # External webhook/telegram ingress rate limit (C-CACHE); 0 = disabled.
    ingress_rate_limit_per_minute: int = 120
    # Admin ops (drain/sweep/gc) and MCP call rate limits; 0 = disabled.
    admin_ops_rate_limit_per_minute: int = 60
    mcp_call_rate_limit_per_minute: int = 180

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


settings = Settings()
