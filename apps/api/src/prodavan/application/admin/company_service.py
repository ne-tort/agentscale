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
from prodavan.domain.cabinets.types import CabinetCompanyGrantScope
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


def _quota_public(quota: CompanyCabinetQuota) -> dict:
    return {
        "max_cabinets": quota.max_cabinets,
        "max_packages_per_cabinet": quota.max_packages_per_cabinet,
        "max_bundle_import_mb": quota.max_bundle_import_mb,
    }


def _policy_public(
    policy: CompanyAgentRuntimePolicy,
    *,
    webhook_configured: bool | None = None,
    telegram_configured: bool | None = None,
) -> dict:
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
        "webhook_hmac_configured": (
            webhook_configured if webhook_configured is not None else bool(policy.webhook_hmac_secret)
        ),
        "telegram_hmac_configured": (
            telegram_configured if telegram_configured is not None else bool(policy.telegram_hmac_secret)
        ),
        "idle_pause_after_hours": policy.idle_pause_after_hours,
        "idle_pause_enabled": policy.idle_pause_enabled(),
    }


class AdminCompanyService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._quotas = CompanyQuotaService(session)

    async def _require_company(self, company_id: str, *, include_deleted: bool = False) -> CompanyRow:
        row = await self._session.get(CompanyRow, company_id)
        if row is None or (row.deleted_at is not None and not include_deleted):
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Company not found")
        return row

    async def _count_running_cabinets(self, company_id: str) -> int:
        """Distinct ACTIVE cabinets with at least one ACTIVE (non-paused) project."""
        q = await self._session.execute(
            select(func.count(func.distinct(ProjectRow.cabinet_id)))
            .select_from(ProjectRow)
            .join(CabinetInstanceRow, CabinetInstanceRow.id == ProjectRow.cabinet_id)
            .where(
                ProjectRow.company_id == company_id,
                ProjectRow.status == ProjectStatus.ACTIVE,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        return int(q.scalar_one() or 0)

    async def _count_employees(self, company_id: str) -> int:
        q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id))).where(
                MembershipRow.company_id == company_id
            )
        )
        return int(q.scalar_one() or 0)

    async def list_companies(self) -> list[dict]:
        q = await self._session.execute(
            select(CompanyRow)
            .where(CompanyRow.deleted_at.is_(None))
            .order_by(CompanyRow.created_at.desc())
        )
        out: list[dict] = []
        for company in q.scalars().all():
            quota = await self._quotas.get_quota(company.id)
            active_cabinets = await self._quotas.count_active_cabinets(company.id)
            running_cabinets = await self._count_running_cabinets(company.id)
            employees_total = await self._count_employees(company.id)
            out.append(
                {
                    "id": company.id,
                    "name": company.name,
                    "description": company.description,
                    "contact_email": company.contact_email,
                    "phone": company.phone,
                    "created_at": company.created_at.isoformat() if company.created_at else None,
                    "cabinet_quota": _quota_public(quota),
                    "active_cabinets": active_cabinets,
                    "running_cabinets": running_cabinets,
                    "cabinets_quota": quota.max_cabinets,
                    "employees_total": employees_total,
                }
            )
        return out

    async def set_description(self, company_id: str, description: str | None) -> dict:
        return await self.patch_company(company_id, description=description)

    async def patch_company(self, company_id: str, **fields: object) -> dict:
        company = await self._require_company(company_id)
        if "name" in fields:
            raw_name = fields["name"]
            if not isinstance(raw_name, str) or not raw_name.strip():
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="name required",
                )
            company.name = raw_name.strip()
        if "description" in fields:
            description = fields["description"]
            if description is None:
                company.description = None
            elif isinstance(description, str):
                company.description = description.strip() if description.strip() else None
            else:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="invalid description",
                )
        if "contact_email" in fields:
            raw = fields["contact_email"]
            if raw is None:
                company.contact_email = None
            elif isinstance(raw, str):
                email = raw.strip()
                company.contact_email = email if email else None
            else:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="invalid contact_email",
                )
        if "phone" in fields:
            raw = fields["phone"]
            if raw is None:
                company.phone = None
            elif isinstance(raw, str):
                phone = raw.strip()
                company.phone = phone if phone else None
            else:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="invalid phone",
                )
        await self._session.commit()
        await self._session.refresh(company)
        return {
            "id": company.id,
            "name": company.name,
            "description": company.description,
            "contact_email": company.contact_email,
            "phone": company.phone,
        }

    async def get_company(self, company_id: str) -> dict:
        company = await self._require_company(company_id)
        quota = await self._quotas.get_quota(company_id)
        policy = await self.get_agent_policy(company_id)
        webhook_secret, telegram_secret = await self.get_ingress_hmac_secrets(company_id)
        metrics = await self.get_metrics(company_id)
        return {
            "id": company.id,
            "name": company.name,
            "description": company.description,
            "contact_email": company.contact_email,
            "phone": company.phone,
            "username": company.id,
            "password_set": company.keycloak_sub is not None,
            "keycloak_sub": company.keycloak_sub,
            "created_at": company.created_at.isoformat() if company.created_at else None,
            "cabinet_quota": _quota_public(quota),
            "agent_policy": _policy_public(
                policy,
                webhook_configured=bool(webhook_secret),
                telegram_configured=bool(telegram_secret),
            ),
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
        from prodavan.application.admin.company_runtime_cache import invalidate_company_runtime_cache

        await invalidate_company_runtime_cache(company_id)
        return _quota_public(row.to_domain())

    async def get_ingress_hmac_secrets(self, company_id: str) -> tuple[str | None, str | None]:
        """HMAC secrets always from DB — never Redis (C-CACHE harden)."""
        row = await self._session.get(CompanyAgentRuntimePolicyRow, company_id)
        if row is None:
            return None, None
        return row.webhook_hmac_secret, row.telegram_hmac_secret

    async def get_agent_policy(self, company_id: str) -> CompanyAgentRuntimePolicy:
        from prodavan.application.admin.company_runtime_cache import (
            get_cached_agent_policy,
            set_cached_agent_policy,
        )

        cached = await get_cached_agent_policy(company_id)
        if cached is not None:
            return cached
        row = await self._session.get(CompanyAgentRuntimePolicyRow, company_id)
        policy = CompanyAgentRuntimePolicy() if row is None else row.to_domain()
        await set_cached_agent_policy(company_id, policy)
        return policy

    async def get_agent_policy_public(self, company_id: str) -> dict:
        await self._require_company(company_id)
        policy = await self.get_agent_policy(company_id)
        webhook_secret, telegram_secret = await self.get_ingress_hmac_secrets(company_id)
        return _policy_public(
            policy,
            webhook_configured=bool(webhook_secret),
            telegram_configured=bool(telegram_secret),
        )

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
        row.idle_pause_after_hours = policy.idle_pause_after_hours
        if update_webhook_secret:
            secret = policy.webhook_hmac_secret
            row.webhook_hmac_secret = (secret.strip() if secret else "") or None
        if update_telegram_secret:
            secret = policy.telegram_hmac_secret
            row.telegram_hmac_secret = (secret.strip() if secret else "") or None
        await self._session.commit()
        await self._session.refresh(row)
        from prodavan.application.admin.company_runtime_cache import invalidate_company_runtime_cache

        await invalidate_company_runtime_cache(company_id)
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
        from prodavan.application.admin.storage_metrics import company_blob_storage_bytes

        keys_q = await self._session.execute(
            select(ProjectRow.workspace_key).where(
                ProjectRow.company_id == company_id,
                ProjectRow.status != ProjectStatus.DELETED,
            )
        )
        cabinet_q = await self._session.execute(
            select(CabinetInstanceRow.id).where(
                CabinetInstanceRow.company_id == company_id,
                CabinetInstanceRow.status == CabinetStatus.ACTIVE,
            )
        )
        return company_blob_storage_bytes(
            workspace_keys=list(keys_q.scalars().all()),
            cabinet_ids=list(cabinet_q.scalars().all()),
        )

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
        running_cabinets = await self._count_running_cabinets(company_id)
        employees_total = int(emp_q.scalar_one() or 0)
        employees_active = int(emp_active_q.scalar_one() or 0)
        projects_total = int(proj_q.scalar_one() or 0)
        agent_messages = int(msg_q.scalar_one() or 0)
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
        unbound_emp_q = await self._session.execute(
            select(func.count(func.distinct(MembershipRow.employee_id)))
            .select_from(MembershipRow)
            .join(EmployeeRow, EmployeeRow.id == MembershipRow.employee_id)
            .where(
                MembershipRow.company_id == company_id,
                EmployeeRow.keycloak_sub.is_(None),
                EmployeeRow.status != EmployeeStatus.DISABLED,
                EmployeeRow.deleted_at.is_(None),
            )
        )
        employees_keycloak_unbound = int(unbound_emp_q.scalar_one() or 0)
        return {
            "employees_total": employees_total,
            "employees_active": employees_active,
            "employees": employees_total,
            "active_cabinets": active_cabinets,
            "cabinets_active": active_cabinets,
            "running_cabinets": running_cabinets,
            "cabinets_quota": quota.max_cabinets,
            "cabinets_quota_used_pct": round(100 * active_cabinets / quota.max_cabinets, 1)
            if quota.max_cabinets
            else 0,
            "projects_total": projects_total,
            "agent_tokens_used": tokens_used,
            "agent_input_tokens": input_tok,
            "agent_output_tokens": output_tok,
            "agent_messages": agent_messages,
            "last_activity_at": last_activity.isoformat() if last_activity else None,
            "storage_bytes": storage_bytes,
            "high_agent_usage": high_usage,
            "keycloak_unbound": company.keycloak_sub is None,
            "employees_keycloak_unbound": employees_keycloak_unbound,
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
        from prodavan.application.admin.subscription_gate import CompanySubscriptionGate

        company = await self._require_company(company_id)
        now = datetime.now(UTC)
        before = subscription_read_model(
            ends_at=company.subscription_ends_at,
            lifetime=company.subscription_lifetime,
            now=now,
            expiring_days=settings.admin_metrics_subscription_expiring_days,
        )
        was_expired = bool(before.get("subscription_expired"))
        if subscription_lifetime:
            company.subscription_lifetime = True
            company.subscription_ends_at = None
        else:
            company.subscription_lifetime = False
            company.subscription_ends_at = subscription_ends_at
        sub = subscription_read_model(
            ends_at=company.subscription_ends_at,
            lifetime=company.subscription_lifetime,
            now=now,
            expiring_days=settings.admin_metrics_subscription_expiring_days,
        )
        now_expired = bool(sub.get("subscription_expired"))
        gate = CompanySubscriptionGate(self._session)
        await gate.emit_transition_events(
            company_id,
            was_expired=was_expired,
            now_expired=now_expired,
            subscription=sub,
            principal=principal,
        )
        await self._session.commit()
        await self._session.refresh(company)
        from prodavan.application.admin.company_runtime_cache import invalidate_company_runtime_cache

        await invalidate_company_runtime_cache(company_id)
        return sub

    async def list_companies_metrics(self) -> list[dict]:
        q = await self._session.execute(
            select(CompanyRow)
            .where(CompanyRow.deleted_at.is_(None))
            .order_by(CompanyRow.created_at.desc())
        )
        out: list[dict] = []
        for company in q.scalars().all():
            metrics = await self.get_metrics(company.id)
            out.append({"company_id": company.id, "name": company.name, **metrics})
        return out

    async def list_cascade_pending(self) -> list[dict]:
        """Soft-deleted companies still holding live (non-soft-deleted) children."""
        from prodavan.domain.cabinets import CabinetStatus

        q = await self._session.execute(
            select(CompanyRow).where(CompanyRow.deleted_at.is_not(None)).order_by(CompanyRow.deleted_at.desc())
        )
        pending: list[dict] = []
        for company in q.scalars().all():
            cab_q = await self._session.execute(
                select(func.count()).select_from(CabinetInstanceRow).where(
                    CabinetInstanceRow.company_id == company.id,
                    CabinetInstanceRow.status != CabinetStatus.DELETED,
                )
            )
            proj_q = await self._session.execute(
                select(func.count()).select_from(ProjectRow).where(
                    ProjectRow.company_id == company.id,
                    ProjectRow.status != ProjectStatus.DELETED,
                )
            )
            cabinets = int(cab_q.scalar_one() or 0)
            projects = int(proj_q.scalar_one() or 0)
            if cabinets == 0 and projects == 0:
                continue
            pending.append(
                {
                    "company_id": company.id,
                    "name": company.name,
                    "deleted_at": company.deleted_at.isoformat() if company.deleted_at else None,
                    "cabinets_remaining": cabinets,
                    "projects_remaining": projects,
                    "cascade_incomplete": True,
                }
            )
        return pending

    async def list_org_cabinets(self, company_id: str) -> list[dict]:
        await self._require_company(company_id)
        from prodavan.application.cabinets.grant_service import CabinetGrantService
        from prodavan.domain.ownership import company_view_flags
        from prodavan.infrastructure.persistence.models.cabinets import (
            CabinetCompanyGrantRow,
            CabinetInstanceRow,
        )

        grants = CabinetGrantService(self._session)
        q = await self._session.execute(
            select(CabinetInstanceRow)
            .outerjoin(
                CabinetCompanyGrantRow,
                (CabinetCompanyGrantRow.cabinet_id == CabinetInstanceRow.id)
                & (CabinetCompanyGrantRow.company_id == company_id)
                & (CabinetCompanyGrantRow.status == "active"),
            )
            .where(
                CabinetInstanceRow.status != "deleted",
                (
                    (CabinetInstanceRow.company_grant_scope == CabinetCompanyGrantScope.ALL)
                    | (CabinetCompanyGrantRow.id.isnot(None))
                ),
            )
            .order_by(CabinetInstanceRow.created_at.desc())
        )
        out: list[dict] = []
        for inst in q.scalars().unique().all():
            assignments_count = await grants.assignment_count(inst.id)
            flags = company_view_flags(
                owner_scope=inst.owner_scope,
                owner_company_id=inst.owner_company_id,
                company_id=company_id,
            )
            out.append(
                {
                    "id": inst.id,
                    "name": inst.name,
                    "status": inst.status,
                    "owner_scope": inst.owner_scope,
                    "owner_company_id": inst.owner_company_id,
                    "owner_employee_id": inst.owner_employee_id,
                    "assignments_count": assignments_count,
                    **flags,
                    "created_at": inst.created_at.isoformat() if inst.created_at else None,
                }
            )
        return out

    async def list_company_employees(self, company_id: str) -> list[dict]:
        await self._require_company(company_id)
        q = await self._session.execute(
            select(EmployeeRow, MembershipRow.role)
            .join(MembershipRow, MembershipRow.employee_id == EmployeeRow.id)
            .where(
                MembershipRow.company_id == company_id,
                EmployeeRow.deleted_at.is_(None),
            )
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
        company = await self._require_company(company_id)
        metrics = await self.get_metrics(company_id)
        quota = await self._quotas.get_quota(company_id)
        return {
            "company_id": company_id,
            "username": company.id,
            "password_set": company.keycloak_sub is not None,
            "metrics": metrics,
            "cabinet_quota": _quota_public(quota),
        }

    async def set_company_password(self, company_id: str, *, password: str) -> dict:
        """Set/rotate Auth password for company org principal (username = company_id).

        If identity is still unbound, re-publish ``auth.user.register`` so Auth creates/reuses
        the IdP user and Identity binds ``keycloak_sub`` via Kafka events.
        """
        pwd = (password or "").strip()
        if len(pwd) < 8:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="password required (min 8 chars)",
            )
        company = await self._require_company(company_id)
        if company.keycloak_sub is None:
            from prodavan.application.auth.register import publish_register_command
            from prodavan.domain.identity import ROLE_COMPANY

            await publish_register_command(
                client_ref=f"company:{company.id}",
                username=company.id,
                email=f"{company.id}@companies.prodavan.local",
                password=pwd,
                realm_roles=[ROLE_COMPANY],
                display_name=company.name,
            )
            await self._session.refresh(company)
            return {
                "id": company.id,
                "username": company.id,
                "password_set": company.keycloak_sub is not None,
                "identity_pending": company.keycloak_sub is None,
            }

        from prodavan.infrastructure.keycloak.provisioning import get_provisioning

        await get_provisioning().set_company_password(username=company.id, password=pwd)
        return {
            "id": company.id,
            "username": company.id,
            "password_set": True,
        }

    async def delete_company(self, company_id: str, *, principal: Principal) -> dict:
        """Soft-delete company; cascade projects/cabinets/employees via Celery (Auth via Kafka)."""
        from prodavan.application.companies.service import CompaniesCommandService

        return await CompaniesCommandService(self._session).soft_delete(
            company_id, principal=principal
        )
