"""Platform Admin company control plane (L04)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.ai_keys.service import AiKeysService
from prodavan.config.settings import settings
from prodavan.domain.admin import (
    CompanyAgentRuntimePolicy,
    CompanyCabinetQuota,
    attachment_max_bytes,
    subscription_read_model,
)
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.errors import AppError
from prodavan.domain.identity import EmployeeStatus, Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.admin import (
    CompanyAgentRuntimePolicyRow,
    CompanyCabinetQuotaRow,
)
from prodavan.infrastructure.persistence.models.agent import AgentEventRow, AgentSessionRow, AgentUsageRow
from prodavan.infrastructure.persistence.models.cabinets import CabinetInstanceRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow
from prodavan.infrastructure.projects.workspace import workspace_tree_bytes


def _quota_public(quota: CompanyCabinetQuota) -> dict:
    return {
        "max_cabinets": quota.max_cabinets,
        "max_packages_per_cabinet": quota.max_packages_per_cabinet,
        "max_bundle_import_mb": quota.max_bundle_import_mb,
    }


def _policy_public(policy: CompanyAgentRuntimePolicy) -> dict:
    return {
        "tool_preset": policy.tool_preset,
        "preferred_provider": policy.preferred_provider,
        "platform_fallback": policy.platform_fallback,
        "model_allowlist": policy.model_allowlist,
        "max_agent_tokens_month": policy.max_agent_tokens_month,
        "max_tokens_per_run": policy.max_tokens_per_run,
        "max_cost_usd_month": float(policy.max_cost_usd_month)
        if policy.max_cost_usd_month is not None
        else None,
        "max_attachment_mb": policy.max_attachment_mb,
        "attachment_max_bytes": attachment_max_bytes(policy),
        "webhook_hmac_configured": bool(policy.webhook_hmac_secret),
        "telegram_hmac_configured": bool(policy.telegram_hmac_secret),
    }


class AdminCompanyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._quotas = CompanyQuotaService(session)

    async def _require_company(self, company_id: str) -> CompanyRow:
        row = await self._session.get(CompanyRow, company_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
        return row

    async def list_companies(self) -> list[dict]:
        q = await self._session.execute(select(CompanyRow).order_by(CompanyRow.created_at.desc()))
        out: list[dict] = []
        for company in q.scalars().all():
            quota = await self._quotas.get_quota(company.id)
            active_cabinets = await self._quotas.count_active_cabinets(company.id)
            out.append(
                {
                    "id": company.id,
                    "name": company.name,
                    "created_at": company.created_at.isoformat() if company.created_at else None,
                    "cabinet_quota": _quota_public(quota),
                    "active_cabinets": active_cabinets,
                }
            )
        return out

    async def get_company(self, company_id: str) -> dict:
        company = await self._require_company(company_id)
        quota = await self._quotas.get_quota(company_id)
        policy = await self.get_agent_policy(company_id)
        metrics = await self.get_metrics(company_id)
        return {
            "id": company.id,
            "name": company.name,
            "created_at": company.created_at.isoformat() if company.created_at else None,
            "cabinet_quota": _quota_public(quota),
            "agent_policy": _policy_public(policy),
            "metrics": metrics,
        }

    async def set_cabinet_quotas(self, company_id: str, quota: CompanyCabinetQuota) -> dict:
        await self._require_company(company_id)
        quota.validate()
        row = await self._session.get(CompanyCabinetQuotaRow, company_id)
        if row is None:
            row = CompanyCabinetQuotaRow(company_id=company_id)
            self._session.add(row)
        row.max_cabinets = quota.max_cabinets
        row.max_packages_per_cabinet = quota.max_packages_per_cabinet
        row.max_bundle_import_mb = quota.max_bundle_import_mb
        await self._session.commit()
        await self._session.refresh(row)
        return _quota_public(row.to_domain())

    async def get_agent_policy(self, company_id: str) -> CompanyAgentRuntimePolicy:
        row = await self._session.get(CompanyAgentRuntimePolicyRow, company_id)
        if row is None:
            return CompanyAgentRuntimePolicy()
        return row.to_domain()

    async def set_agent_policy(
        self,
        company_id: str,
        policy: CompanyAgentRuntimePolicy,
        *,
        update_webhook_secret: bool = False,
        update_telegram_secret: bool = False,
    ) -> dict:
        await self._require_company(company_id)
        policy.validate()
        row = await self._session.get(CompanyAgentRuntimePolicyRow, company_id)
        if row is None:
            row = CompanyAgentRuntimePolicyRow(company_id=company_id)
            self._session.add(row)
        row.tool_preset = policy.tool_preset
        row.preferred_provider = policy.preferred_provider
        row.platform_fallback = policy.platform_fallback
        row.model_allowlist = policy.model_allowlist
        row.max_agent_tokens_month = policy.max_agent_tokens_month
        row.max_tokens_per_run = policy.max_tokens_per_run
        row.max_cost_usd_month = policy.max_cost_usd_month
        row.max_attachment_mb = policy.max_attachment_mb
        if update_webhook_secret:
            secret = policy.webhook_hmac_secret
            row.webhook_hmac_secret = (secret.strip() if secret else "") or None
        if update_telegram_secret:
            secret = policy.telegram_hmac_secret
            row.telegram_hmac_secret = (secret.strip() if secret else "") or None
        await self._session.commit()
        await self._session.refresh(row)
        return _policy_public(row.to_domain())

    async def _last_activity_at(self, company_id: str) -> datetime | None:
        sess_q = await self._session.execute(
            select(func.max(AgentSessionRow.updated_at))
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(ProjectRow.company_id == company_id)
        )
        proj_q = await self._session.execute(
            select(func.max(ProjectRow.updated_at)).where(
                ProjectRow.company_id == company_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        evt_q = await self._session.execute(
            select(func.max(AgentEventRow.created_at))
            .join(AgentSessionRow, AgentSessionRow.id == AgentEventRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(ProjectRow.company_id == company_id)
        )
        candidates = [sess_q.scalar_one(), proj_q.scalar_one(), evt_q.scalar_one()]
        times = [t for t in candidates if t is not None]
        return max(times) if times else None

    async def _storage_bytes(self, company_id: str) -> int:
        keys_q = await self._session.execute(
            select(ProjectRow.workspace_key).where(
                ProjectRow.company_id == company_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        return sum(workspace_tree_bytes(key) for key in keys_q.scalars().all())

    async def get_metrics(self, company_id: str) -> dict:
        company = await self._require_company(company_id)
        emp_q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id))).where(
                MembershipRow.company_id == company_id
            )
        )
        emp_active_q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id)))
            .select_from(MembershipRow)
            .join(EmployeeRow, EmployeeRow.id == MembershipRow.employee_id)
            .where(
                MembershipRow.company_id == company_id,
                EmployeeRow.status != EmployeeStatus.DISABLED,
            )
        )
        cab_q = await self._session.execute(
            select(func.count())
            .select_from(CabinetInstanceRow)
            .where(
                CabinetInstanceRow.company_id == company_id,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        proj_q = await self._session.execute(
            select(func.count())
            .select_from(ProjectRow)
            .where(ProjectRow.company_id == company_id, ProjectRow.status != ProjectStatus.DELETED)
        )
        usage_q = await self._session.execute(
            select(
                func.coalesce(func.sum(AgentUsageRow.input_tokens), 0),
                func.coalesce(func.sum(AgentUsageRow.output_tokens), 0),
            )
            .select_from(AgentUsageRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentUsageRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(ProjectRow.company_id == company_id)
        )
        usage_row = usage_q.one()
        input_tok = int(usage_row[0] or 0)
        output_tok = int(usage_row[1] or 0)
        msg_q = await self._session.execute(
            select(func.count())
            .select_from(AgentEventRow)
            .join(AgentSessionRow, AgentSessionRow.id == AgentEventRow.session_id)
            .join(ProjectRow, ProjectRow.id == AgentSessionRow.project_id)
            .where(
                ProjectRow.company_id == company_id,
                AgentEventRow.event_type == "text_delta",
            )
        )
        quota = await self._quotas.get_quota(company_id)
        active_cabinets = int(cab_q.scalar_one() or 0)
        key_metrics = await AiKeysService(self._session).company_key_metrics(company_id)
        last_activity = await self._last_activity_at(company_id)
        storage_bytes = await self._storage_bytes(company_id)
        tokens_used = input_tok + output_tok
        threshold = settings.admin_metrics_token_alert_threshold
        high_usage = threshold > 0 and tokens_used >= threshold
        sub = subscription_read_model(
            ends_at=company.subscription_ends_at,
            lifetime=company.subscription_lifetime,
            now=datetime.now(UTC),
            expiring_days=settings.admin_metrics_subscription_expiring_days,
        )
        return {
            "employees_total": int(emp_q.scalar_one() or 0),
            "employees_active": int(emp_active_q.scalar_one() or 0),
            "employees": int(emp_q.scalar_one() or 0),
            "active_cabinets": active_cabinets,
            "cabinets_active": active_cabinets,
            "cabinets_quota": quota.max_cabinets,
            "cabinets_quota_used_pct": round(100 * active_cabinets / quota.max_cabinets, 1)
            if quota.max_cabinets
            else 0,
            "projects_total": int(proj_q.scalar_one() or 0),
            "agent_tokens_used": tokens_used,
            "agent_input_tokens": input_tok,
            "agent_output_tokens": output_tok,
            "agent_messages": int(msg_q.scalar_one() or 0),
            "last_activity_at": last_activity.isoformat() if last_activity else None,
            "storage_bytes": storage_bytes,
            "high_agent_usage": high_usage,
            **key_metrics,
            **sub,
        }

    async def set_subscription(
        self,
        company_id: str,
        *,
        subscription_ends_at: datetime | None,
        subscription_lifetime: bool,
        principal: Principal | None = None,
    ) -> dict:
        from prodavan.application.projects.platform_event_service import PlatformEventService

        company = await self._require_company(company_id)
        if subscription_lifetime:
            company.subscription_lifetime = True
            company.subscription_ends_at = None
        else:
            company.subscription_lifetime = False
            company.subscription_ends_at = subscription_ends_at
        sub = subscription_read_model(
            ends_at=company.subscription_ends_at,
            lifetime=company.subscription_lifetime,
            now=datetime.now(UTC),
            expiring_days=settings.admin_metrics_subscription_expiring_days,
        )
        if sub.get("subscription_expired"):
            await PlatformEventService(self._session).emit(
                event_type="company.suspended",
                company_id=company_id,
                principal=principal,
                payload={
                    "subscription_ends_at": sub.get("subscription_ends_at"),
                    "reason": "subscription_expired",
                },
            )
        await self._session.commit()
        await self._session.refresh(company)
        return sub

    async def list_companies_metrics(self) -> list[dict]:
        q = await self._session.execute(select(CompanyRow).order_by(CompanyRow.created_at.desc()))
        out: list[dict] = []
        for company in q.scalars().all():
            metrics = await self.get_metrics(company.id)
            out.append({"company_id": company.id, "name": company.name, **metrics})
        return out

    async def list_org_cabinets(self, company_id: str) -> list[dict]:
        await self._require_company(company_id)
        q = await self._session.execute(
            select(CabinetInstanceRow, EmployeeRow.email)
            .join(EmployeeRow, EmployeeRow.id == CabinetInstanceRow.owner_employee_id)
            .where(CabinetInstanceRow.company_id == company_id)
            .order_by(CabinetInstanceRow.created_at.desc())
        )
        return [
            {
                "id": inst.id,
                "name": inst.name,
                "status": inst.status,
                "owner_employee_id": inst.owner_employee_id,
                "owner_email": owner_email,
                "created_at": inst.created_at.isoformat() if inst.created_at else None,
            }
            for inst, owner_email in q.all()
        ]

    async def list_company_employees(self, company_id: str) -> list[dict]:
        await self._require_company(company_id)
        q = await self._session.execute(
            select(EmployeeRow, MembershipRow.role)
            .join(MembershipRow, MembershipRow.employee_id == EmployeeRow.id)
            .where(MembershipRow.company_id == company_id)
            .order_by(EmployeeRow.email)
        )
        return [
            {
                "id": emp.id,
                "email": emp.email,
                "display_name": emp.display_name,
                "status": emp.status,
                "role": role,
            }
            for emp, role in q.all()
        ]

    async def get_company_summary(self, company_id: str) -> dict:
        """Read-only org metrics for company.admin contour (L04)."""
        metrics = await self.get_metrics(company_id)
        quota = await self._quotas.get_quota(company_id)
        return {
            "company_id": company_id,
            "metrics": metrics,
            "cabinet_quota": _quota_public(quota),
        }
