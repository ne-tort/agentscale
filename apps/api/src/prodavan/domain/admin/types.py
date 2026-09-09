"""Company quotas and agent runtime policy (L04)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal

TOOL_PRESETS = frozenset({"chat_readonly", "workspace_dev", "workspace_full"})


@dataclass(frozen=True)
class CompanyCabinetQuota:
    max_cabinets: int = 10
    max_packages_per_cabinet: int = 20
    max_bundle_import_mb: int = 50

    def validate(self) -> None:
        if self.max_cabinets < 1:
            raise ValueError("max_cabinets must be >= 1")
        if self.max_packages_per_cabinet < 0:
            raise ValueError("max_packages_per_cabinet must be >= 0")
        if self.max_bundle_import_mb < 1:
            raise ValueError("max_bundle_import_mb must be >= 1")


DEFAULT_CABINET_QUOTA = CompanyCabinetQuota()


@dataclass(frozen=True)
class CompanyTenantInfraQuota:
    """Per-company caps for Pod Tenant Infra Gateway planes."""

    cache_ops_per_minute: int = 120
    cache_max_keys: int = 500
    cache_max_value_bytes: int = 65536
    cache_default_ttl_sec: int = 3600
    cache_max_ttl_sec: int = 86400 * 7
    docs_ops_per_minute: int = 120
    docs_max_collections: int = 20
    docs_max_docs_per_collection: int = 5000
    docs_max_doc_bytes: int = 262144
    userdb_ops_per_minute: int = 60
    userdb_max_tables: int = 20
    userdb_max_rows_per_table: int = 10000
    userdb_max_row_bytes: int = 65536
    kafka_ops_per_minute: int = 120
    kafka_max_payload_bytes: int = 65536
    kafka_max_backlog: int = 1000
    kafka_retention_sec: int = 86400 * 2
    objects_ops_per_minute: int = 60
    objects_max_per_project: int = 100
    objects_max_bytes: int = 50 * 1024 * 1024

    def validate(self) -> None:
        pairs = (
            ("cache_ops_per_minute", self.cache_ops_per_minute, 1),
            ("cache_max_keys", self.cache_max_keys, 1),
            ("cache_max_value_bytes", self.cache_max_value_bytes, 1),
            ("cache_default_ttl_sec", self.cache_default_ttl_sec, 1),
            ("cache_max_ttl_sec", self.cache_max_ttl_sec, 1),
            ("docs_ops_per_minute", self.docs_ops_per_minute, 1),
            ("docs_max_collections", self.docs_max_collections, 1),
            ("docs_max_docs_per_collection", self.docs_max_docs_per_collection, 1),
            ("docs_max_doc_bytes", self.docs_max_doc_bytes, 1),
            ("userdb_ops_per_minute", self.userdb_ops_per_minute, 1),
            ("userdb_max_tables", self.userdb_max_tables, 1),
            ("userdb_max_rows_per_table", self.userdb_max_rows_per_table, 1),
            ("userdb_max_row_bytes", self.userdb_max_row_bytes, 1),
            ("kafka_ops_per_minute", self.kafka_ops_per_minute, 1),
            ("kafka_max_payload_bytes", self.kafka_max_payload_bytes, 1),
            ("kafka_max_backlog", self.kafka_max_backlog, 1),
            ("kafka_retention_sec", self.kafka_retention_sec, 60),
            ("objects_ops_per_minute", self.objects_ops_per_minute, 1),
            ("objects_max_per_project", self.objects_max_per_project, 1),
            ("objects_max_bytes", self.objects_max_bytes, 1),
        )
        for name, value, minimum in pairs:
            if int(value) < minimum:
                raise ValueError(f"{name} must be >= {minimum}")
        if self.cache_default_ttl_sec > self.cache_max_ttl_sec:
            raise ValueError("cache_default_ttl_sec must be <= cache_max_ttl_sec")


DEFAULT_TENANT_INFRA_QUOTA = CompanyTenantInfraQuota()

DEFAULT_MAX_ATTACHMENT_MB = 20
PLATFORM_MAX_ATTACHMENT_MB = 500


@dataclass
class CompanyAgentRuntimePolicy:
    tool_preset: str = "workspace_dev"
    preferred_provider: str | None = None
    platform_fallback: bool = True
    model_allowlist: list[str] = field(default_factory=list)
    max_agent_tokens_month: int | None = None
    max_tokens_per_run: int | None = None
    max_cost_usd_month: Decimal | None = None
    max_attachment_mb: int = DEFAULT_MAX_ATTACHMENT_MB
    webhook_hmac_secret: str | None = None
    telegram_hmac_secret: str | None = None
    # None or 0 = disabled (L09 idle pause; default off).
    idle_pause_after_hours: int | None = None

    def validate(self) -> None:
        if self.tool_preset not in TOOL_PRESETS:
            raise ValueError(f"tool_preset must be one of {sorted(TOOL_PRESETS)}")
        if self.max_agent_tokens_month is not None and self.max_agent_tokens_month < 1:
            raise ValueError("max_agent_tokens_month must be >= 1")
        if self.max_tokens_per_run is not None and self.max_tokens_per_run < 1:
            raise ValueError("max_tokens_per_run must be >= 1")
        if self.max_cost_usd_month is not None and self.max_cost_usd_month <= 0:
            raise ValueError("max_cost_usd_month must be > 0")
        if self.max_attachment_mb < 1 or self.max_attachment_mb > PLATFORM_MAX_ATTACHMENT_MB:
            raise ValueError(f"max_attachment_mb must be 1..{PLATFORM_MAX_ATTACHMENT_MB}")
        if self.webhook_hmac_secret is not None and len(self.webhook_hmac_secret) > 256:
            raise ValueError("webhook_hmac_secret too long")
        if self.telegram_hmac_secret is not None and len(self.telegram_hmac_secret) > 256:
            raise ValueError("telegram_hmac_secret too long")
        if self.idle_pause_after_hours is not None and (
            self.idle_pause_after_hours < 0 or self.idle_pause_after_hours > 8760
        ):
            raise ValueError("idle_pause_after_hours must be 0..8760 (0/None = off)")

    def idle_pause_enabled(self) -> bool:
        return bool(self.idle_pause_after_hours and self.idle_pause_after_hours > 0)


def attachment_max_bytes(policy: CompanyAgentRuntimePolicy) -> int:
    """Effective upload ceiling for project chat attachments (L07 / C-ATTACH)."""
    return policy.max_attachment_mb * 1024 * 1024


SUBSCRIPTION_EXPIRING_SOON_DAYS = 30


def subscription_read_model(
    *,
    ends_at: datetime | None,
    lifetime: bool,
    now: datetime,
    expiring_days: int = SUBSCRIPTION_EXPIRING_SOON_DAYS,
) -> dict[str, object]:
    """Metrics DTO fields for company subscription (L04 / metrics.md)."""
    if lifetime:
        return {
            "subscription_ends_at": None,
            "subscription_lifetime": True,
            "subscription_expiring_soon": False,
            "subscription_expired": False,
        }
    if ends_at is None:
        return {
            "subscription_ends_at": None,
            "subscription_lifetime": False,
            "subscription_expiring_soon": False,
            "subscription_expired": False,
        }
    if ends_at.tzinfo is None:
        ends_at = ends_at.replace(tzinfo=now.tzinfo)
    expired = ends_at < now
    soon_limit = now + timedelta(days=expiring_days)
    expiring_soon = not expired and ends_at <= soon_limit
    return {
        "subscription_ends_at": ends_at.isoformat(),
        "subscription_lifetime": False,
        "subscription_expiring_soon": expiring_soon,
        "subscription_expired": expired,
    }
