"""ORM — company quotas and agent policy (L04)."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from prodavan.domain.admin import (
    DEFAULT_CABINET_QUOTA,
    CompanyAgentRuntimePolicy,
    CompanyCabinetQuota,
    CompanyTenantInfraQuota,
)
from prodavan.infrastructure.persistence.models.base import Base


class CompanyCabinetQuotaRow(Base):
    __tablename__ = "company_cabinet_quotas"

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    max_cabinets: Mapped[int] = mapped_column(Integer, nullable=False, default=DEFAULT_CABINET_QUOTA.max_cabinets)
    max_packages_per_cabinet: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_CABINET_QUOTA.max_packages_per_cabinet
    )
    max_bundle_import_mb: Mapped[int] = mapped_column(
        Integer, nullable=False, default=DEFAULT_CABINET_QUOTA.max_bundle_import_mb
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> CompanyCabinetQuota:
        return CompanyCabinetQuota(
            max_cabinets=self.max_cabinets,
            max_packages_per_cabinet=self.max_packages_per_cabinet,
            max_bundle_import_mb=self.max_bundle_import_mb,
        )


class CompanyTenantInfraQuotaRow(Base):
    __tablename__ = "company_tenant_infra_quotas"

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    cache_ops_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_max_keys: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_max_value_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_default_ttl_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_max_ttl_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    docs_ops_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    docs_max_collections: Mapped[int] = mapped_column(Integer, nullable=False)
    docs_max_docs_per_collection: Mapped[int] = mapped_column(Integer, nullable=False)
    docs_max_doc_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    userdb_ops_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    userdb_max_tables: Mapped[int] = mapped_column(Integer, nullable=False)
    userdb_max_rows_per_table: Mapped[int] = mapped_column(Integer, nullable=False)
    userdb_max_row_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    kafka_ops_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    kafka_max_payload_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    kafka_max_backlog: Mapped[int] = mapped_column(Integer, nullable=False)
    kafka_retention_sec: Mapped[int] = mapped_column(Integer, nullable=False)
    objects_ops_per_minute: Mapped[int] = mapped_column(Integer, nullable=False)
    objects_max_per_project: Mapped[int] = mapped_column(Integer, nullable=False)
    objects_max_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> CompanyTenantInfraQuota:
        return CompanyTenantInfraQuota(
            cache_ops_per_minute=self.cache_ops_per_minute,
            cache_max_keys=self.cache_max_keys,
            cache_max_value_bytes=self.cache_max_value_bytes,
            cache_default_ttl_sec=self.cache_default_ttl_sec,
            cache_max_ttl_sec=self.cache_max_ttl_sec,
            docs_ops_per_minute=self.docs_ops_per_minute,
            docs_max_collections=self.docs_max_collections,
            docs_max_docs_per_collection=self.docs_max_docs_per_collection,
            docs_max_doc_bytes=self.docs_max_doc_bytes,
            userdb_ops_per_minute=self.userdb_ops_per_minute,
            userdb_max_tables=self.userdb_max_tables,
            userdb_max_rows_per_table=self.userdb_max_rows_per_table,
            userdb_max_row_bytes=self.userdb_max_row_bytes,
            kafka_ops_per_minute=self.kafka_ops_per_minute,
            kafka_max_payload_bytes=self.kafka_max_payload_bytes,
            kafka_max_backlog=self.kafka_max_backlog,
            kafka_retention_sec=self.kafka_retention_sec,
            objects_ops_per_minute=self.objects_ops_per_minute,
            objects_max_per_project=self.objects_max_per_project,
            objects_max_bytes=self.objects_max_bytes,
        )

    @classmethod
    def from_domain(cls, company_id: str, quota: CompanyTenantInfraQuota) -> CompanyTenantInfraQuotaRow:
        return cls(company_id=company_id, **quota.__dict__)


class CompanyAgentRuntimePolicyRow(Base):
    __tablename__ = "company_agent_runtime_policies"

    company_id: Mapped[str] = mapped_column(
        ForeignKey("companies.id", ondelete="CASCADE"),
        primary_key=True,
    )
    tool_preset: Mapped[str] = mapped_column(String(64), nullable=False, default="workspace_dev")
    preferred_provider: Mapped[str | None] = mapped_column(String(32), nullable=True)
    platform_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    model_allowlist: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    max_agent_tokens_month: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_tokens_per_run: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_cost_usd_month: Mapped[Decimal | None] = mapped_column(Numeric(12, 6), nullable=True)
    max_attachment_mb: Mapped[int] = mapped_column(Integer, nullable=False, default=20, server_default="20")
    webhook_hmac_secret: Mapped[str | None] = mapped_column(String(256), nullable=True)
    telegram_hmac_secret: Mapped[str | None] = mapped_column(String(256), nullable=True)
    idle_pause_after_hours: Mapped[int | None] = mapped_column(Integer, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    def to_domain(self) -> CompanyAgentRuntimePolicy:
        return CompanyAgentRuntimePolicy(
            tool_preset=self.tool_preset,
            preferred_provider=self.preferred_provider,
            platform_fallback=self.platform_fallback,
            model_allowlist=list(self.model_allowlist or []),
            max_agent_tokens_month=self.max_agent_tokens_month,
            max_tokens_per_run=self.max_tokens_per_run,
            max_cost_usd_month=self.max_cost_usd_month,
            max_attachment_mb=self.max_attachment_mb,
            webhook_hmac_secret=self.webhook_hmac_secret,
            telegram_hmac_secret=self.telegram_hmac_secret,
            idle_pause_after_hours=self.idle_pause_after_hours,
        )
