"""Platform Admin company control plane (L04)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.admin.quota_service import CompanyQuotaService
from prodavan.application.metrics.aggregator import CompanyMetricsAggregator
from prodavan.application.metrics.read_service import MetricsReadService
from prodavan.application.tenant_infra.quota import TenantInfraQuotaService
from prodavan.config.settings import settings
from dataclasses import asdict

from prodavan.domain.admin import (
    CompanyAgentRuntimePolicy,
    CompanyCabinetQuota,
    CompanyTenantInfraQuota,
    attachment_max_bytes,
    subscription_read_model,
)
from prodavan.domain.cabinets import CabinetStatus
from prodavan.domain.cabinets.types import CabinetAssignmentStatus, CabinetGrantStatus
from prodavan.domain.companies.login import company_effective_login
from prodavan.domain.employees.login import employee_effective_login
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.domain.projects import ProjectStatus
from prodavan.infrastructure.persistence.models.admin import (
    CompanyAgentRuntimePolicyRow,
    CompanyCabinetQuotaRow,
)
from prodavan.infrastructure.persistence.models.cabinets import (
    CabinetCompanyGrantRow,
    CabinetEmployeeAssignmentRow,
    CabinetInstanceRow,
)
from prodavan.infrastructure.persistence.models.identity import CompanyRow, EmployeeRow, MembershipRow
from prodavan.infrastructure.persistence.models.projects import ProjectRow


def _quota_public(quota: CompanyCabinetQuota) -> dict:
    return {
        "max_cabinets": quota.max_cabinets,
        "max_packages_per_cabinet": quota.max_packages_per_cabinet,
        "max_bundle_import_mb": quota.max_bundle_import_mb,
    }


def _tenant_infra_quota_public(quota: CompanyTenantInfraQuota) -> dict:
    return asdict(quota)


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
        companies = list(q.scalars().all())
        login_keys = [company_effective_login(c) for c in companies]
        online_map = await MetricsReadService().batch_company_online(login_keys)
        out: list[dict] = []
        for company in companies:
            quota = await self._quotas.get_quota(company.id)
            active_cabinets = await self._quotas.count_active_cabinets(company.id)
            running_cabinets = await self._count_running_cabinets(company.id)
            employees_total = await self._count_employees(company.id)
            login_key = company_effective_login(company)
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
                    "online": online_map.get(login_key, False),
                    "status": company.status,
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
            new_name = raw_name.strip()
            if new_name.lower() != company.name.lower():
                clash = await self._session.execute(
                    select(CompanyRow.id).where(
                        func.lower(CompanyRow.name) == new_name.lower(),
                        CompanyRow.id != company.id,
                        CompanyRow.deleted_at.is_(None),
                    )
                )
                if clash.scalar_one_or_none() is not None:
                    raise AppError(
                        code="CONFLICT",
                        title="Conflict",
                        status=409,
                        detail="company name already exists",
                    )
            company.name = new_name
            # login_slug stays as-is: the login handle is a stable identifier
            # (renames do not re-issue credentials).
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
            "username": company_effective_login(company),
            "password_set": company.keycloak_sub is not None,
            "keycloak_sub": company.keycloak_sub,
            "created_at": company.created_at.isoformat() if company.created_at else None,
            "cabinet_quota": _quota_public(quota),
            "tenant_infra_quota": _tenant_infra_quota_public(
                await TenantInfraQuotaService(self._session).get_quota(company_id)
            ),
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

    async def set_tenant_infra_quotas(self, company_id: str, quota: CompanyTenantInfraQuota) -> dict:
        await self._require_company(company_id)
        saved = await TenantInfraQuotaService(self._session).set_quota(company_id, quota)
        return _tenant_infra_quota_public(saved)

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

    async def get_metrics(self, company_id: str) -> dict:
        aggregator = CompanyMetricsAggregator(self._session)
        metrics = await aggregator.aggregate(company_id)
        employee_ids = await aggregator.membership_employee_ids(company_id)
        metrics["employees_online"] = await MetricsReadService().employees_online(employee_ids)
        return metrics

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
        from prodavan.application.admin.quota_service import CompanyQuotaService
        from prodavan.application.cabinets.grant_service import CabinetGrantService
        from prodavan.application.cabinets.instance_service import CabinetInstanceService
        from prodavan.application.modules.module_binding_service import ModuleBindingService
        from prodavan.domain.cabinets import CabinetOwnerScope, CabinetStatus
        from prodavan.domain.cabinets.types import CabinetCompanyGrantScope
        from prodavan.domain.ownership import CompanyViewFlags, EntitySource, company_view_flags
        from prodavan.infrastructure.persistence.models.cabinets import (
            CabinetCompanyGrantRow,
            CabinetInstanceRow,
        )

        cabinets_svc = CabinetInstanceService(self._session)
        grant_q = await self._session.execute(
            select(CabinetInstanceRow)
            .join(
                CabinetCompanyGrantRow,
                (CabinetCompanyGrantRow.cabinet_id == CabinetInstanceRow.id)
                & (CabinetCompanyGrantRow.company_id == company_id)
                & (CabinetCompanyGrantRow.status == "active"),
            )
            .where(
                CabinetInstanceRow.owner_scope == CabinetOwnerScope.PLATFORM,
                CabinetInstanceRow.status != CabinetStatus.DELETED,
            )
        )
        for template in grant_q.scalars().unique().all():
            await cabinets_svc.provision_company_copy_from_template(
                template_id=template.id, company_id=company_id
            )

        all_scope_q = await self._session.execute(
            select(CabinetInstanceRow).where(
                CabinetInstanceRow.owner_scope == CabinetOwnerScope.PLATFORM,
                CabinetInstanceRow.company_grant_scope == CabinetCompanyGrantScope.ALL,
                CabinetInstanceRow.status != CabinetStatus.DELETED,
            )
        )
        for template in all_scope_q.scalars().all():
            await cabinets_svc.provision_company_copy_from_template(
                template_id=template.id, company_id=company_id
            )

        grants = CabinetGrantService(self._session)
        bindings = ModuleBindingService(self._session)
        quotas = CompanyQuotaService(self._session)
        q = await self._session.execute(
            select(CabinetInstanceRow)
            .where(
                CabinetInstanceRow.owner_company_id == company_id,
                CabinetInstanceRow.owner_scope == CabinetOwnerScope.COMPANY,
                CabinetInstanceRow.status != CabinetStatus.DELETED,
            )
            .order_by(CabinetInstanceRow.created_at.desc())
        )
        out: list[dict] = []
        for inst in q.scalars().all():
            assignments_count = await grants.assignment_count(inst.id)
            module_ids = await bindings.list_module_ids_for_cabinet(inst.id)
            projects_count = await quotas.count_active_projects_in_cabinet(inst.id)
            if inst.template_cabinet_id:
                flags = CompanyViewFlags(
                    writable=False,
                    source=EntitySource.PLATFORM_ASSIGNED,
                )
            else:
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
                    "template_cabinet_id": inst.template_cabinet_id,
                    "max_projects": inst.max_projects,
                    "projects_count": projects_count,
                    "assignments_count": assignments_count,
                    "module_bindings_count": len(module_ids),
                    **flags,
                    "created_at": inst.created_at.isoformat() if inst.created_at else None,
                }
            )
        await self._session.commit()
        return out

    async def _require_company_employee(self, company_id: str, employee_id: str) -> EmployeeRow:
        await self._require_company(company_id)
        q = await self._session.execute(
            select(EmployeeRow)
            .join(MembershipRow, MembershipRow.employee_id == EmployeeRow.id)
            .where(
                MembershipRow.company_id == company_id,
                EmployeeRow.id == employee_id,
                EmployeeRow.deleted_at.is_(None),
            )
        )
        emp = q.scalar_one_or_none()
        if emp is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="Employee not found")
        return emp

    async def list_company_employees(self, company_id: str) -> list[dict]:
        await self._require_company(company_id)
        q = await self._session.execute(
            select(EmployeeRow, MembershipRow.role)
            .join(MembershipRow, MembershipRow.employee_id == EmployeeRow.id)
            .where(
                MembershipRow.company_id == company_id,
                EmployeeRow.deleted_at.is_(None),
            )
            .order_by(EmployeeRow.login)
        )
        rows = q.all()
        if not rows:
            return []

        emp_ids = [emp.id for emp, _ in rows]

        proj_q = await self._session.execute(
            select(ProjectRow.owner_employee_id, func.count())
            .where(
                ProjectRow.company_id == company_id,
                ProjectRow.owner_employee_id.in_(emp_ids),
                ProjectRow.status != ProjectStatus.DELETED,
            )
            .group_by(ProjectRow.owner_employee_id)
        )
        projects_by_emp = dict(proj_q.all())

        cab_q = await self._session.execute(
            select(
                CabinetEmployeeAssignmentRow.employee_id,
                func.count(func.distinct(CabinetEmployeeAssignmentRow.cabinet_id)),
            )
            .join(
                CabinetCompanyGrantRow,
                CabinetCompanyGrantRow.cabinet_id == CabinetEmployeeAssignmentRow.cabinet_id,
            )
            .where(
                CabinetCompanyGrantRow.company_id == company_id,
                CabinetEmployeeAssignmentRow.employee_id.in_(emp_ids),
                CabinetEmployeeAssignmentRow.status == CabinetAssignmentStatus.ACTIVE,
                CabinetCompanyGrantRow.status == CabinetGrantStatus.ACTIVE,
            )
            .group_by(CabinetEmployeeAssignmentRow.employee_id)
        )
        cabinets_by_emp = dict(cab_q.all())
        online_map = await MetricsReadService().batch_employee_online(emp_ids)

        return [
            {
                "id": emp.id,
                "login": emp.login,
                "email": emp.email,
                "contact_email": emp.contact_email,
                "display_name": emp.display_name,
                "status": emp.status,
                "role": role,
                "projects_count": projects_by_emp.get(emp.id, 0),
                "cabinets_count": cabinets_by_emp.get(emp.id, 0),
                "online": online_map.get(emp.id, False),
            }
            for emp, role in rows
        ]

    async def set_employee_password(
        self, company_id: str, employee_id: str, *, password: str
    ) -> dict:
        await self._require_company_employee(company_id, employee_id)
        from prodavan.application.employees.service import EmployeesCommandService

        emp = await EmployeesCommandService(self._session).set_password(
            employee_id=employee_id, password=password
        )
        return {
            "id": emp.id,
            "login": employee_effective_login(emp),
            "password_set": True,
            "identity_pending": emp.keycloak_sub is None,
        }

    async def update_employee_contact_email(
        self, company_id: str, employee_id: str, *, contact_email: str | None
    ) -> dict:
        await self._require_company_employee(company_id, employee_id)
        from prodavan.application.employees.service import EmployeesCommandService

        emp = await EmployeesCommandService(self._session).update_contact_email(
            employee_id=employee_id, contact_email=contact_email
        )
        return {"id": emp.id, "contact_email": emp.contact_email}

    async def get_company_summary(self, company_id: str) -> dict:
        """Read-only org metrics for company.admin contour (L04)."""
        company = await self._require_company(company_id)
        metrics = await self.get_metrics(company_id)
        quota = await self._quotas.get_quota(company_id)
        return {
            "company_id": company_id,
            "username": company_effective_login(company),
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
        login = company_effective_login(company)
        if company.keycloak_sub is None:
            from prodavan.application.auth.register import publish_register_command
            from prodavan.domain.identity import ROLE_COMPANY

            await publish_register_command(
                client_ref=f"company:{company.id}",
                username=login,
                email=login,
                password=pwd,
                realm_roles=[ROLE_COMPANY],
                display_name=company.name,
            )
            await self._session.refresh(company)
            return {
                "id": company.id,
                "username": company_effective_login(company),
                "password_set": company.keycloak_sub is not None,
                "identity_pending": company.keycloak_sub is None,
            }

        from prodavan.infrastructure.keycloak.provisioning import get_provisioning

        await get_provisioning().set_company_password(username=login, password=pwd)
        return {
            "id": company.id,
            "username": company_effective_login(company),
            "password_set": True,
        }

    async def delete_company(self, company_id: str, *, principal: Principal) -> dict:
        """Soft-delete company; cascade projects/cabinets/employees via Celery (Auth via Kafka)."""
        from prodavan.application.companies.service import CompaniesCommandService

        return await CompaniesCommandService(self._session).soft_delete(
            company_id, principal=principal
        )
