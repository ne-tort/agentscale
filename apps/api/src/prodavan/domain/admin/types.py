"""Company quotas and agent runtime policy (L04)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

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


@dataclass
class CompanyAgentRuntimePolicy:
    tool_preset: str = "workspace_dev"
    preferred_provider: str | None = None
    platform_fallback: bool = True
    model_allowlist: list[str] = field(default_factory=list)
    max_agent_tokens_month: int | None = None
    max_tokens_per_run: int | None = None

    def validate(self) -> None:
        if self.tool_preset not in TOOL_PRESETS:
            raise ValueError(f"tool_preset must be one of {sorted(TOOL_PRESETS)}")
        if self.max_agent_tokens_month is not None and self.max_agent_tokens_month < 1:
            raise ValueError("max_agent_tokens_month must be >= 1")
        if self.max_tokens_per_run is not None and self.max_tokens_per_run < 1:
            raise ValueError("max_tokens_per_run must be >= 1")


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
