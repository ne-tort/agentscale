"""AI Provider Keys application service (L03)."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.ai_keys.audit_service import AiKeyAuditService
from prodavan.domain.ai_keys import (
    ApiKind,
    KeyStatus,
    ResolvedCredential,
    is_runtime_api_kind,
)
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow, CompanyAiKeyBindingRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.secrets.file_store import new_key_id
from prodavan.infrastructure.secrets.store import SecretStore, get_secret_store

PROVIDERS = frozenset({"cursor", "codex", "claude_code"})
API_KINDS = frozenset(k.value for k in ApiKind)
KEY_EXPIRING_SOON_DAYS = 14
_SYSTEM_PRINCIPAL = Principal(sub="system:lazy-expire")


def _parse_price(value: str | Decimal | None) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


class AiKeysService:
    def __init__(self, session: AsyncSession, secrets: SecretStore | None = None) -> None:
        self._session = session
        self._secrets = secrets or get_secret_store()
        self._audit = AiKeyAuditService(session)

    async def _emit_audit(
        self,
        *,
        event_type: str,
        key_id: str | None,
        principal: Principal | None,
        detail: dict | None = None,
    ) -> None:
        if principal is None:
            return
        try:
            await self._audit.record(
                event_type=event_type,
                key_id=key_id,
                principal=principal,
                detail=detail,
            )
        except Exception:
            pass

    async def _emit_lazy_expire_audits(self, key_ids: list[str]) -> None:
        for key_id in key_ids:
            try:
                await self._audit.record(
                    event_type="ai_key.disabled",
                    key_id=key_id,
                    principal=_SYSTEM_PRINCIPAL,
                    detail={"reason": "next_renewal_at_past"},
                )
            except Exception:
                pass

    def _apply_lazy_expiry(self, row: AiProviderKeyRow) -> tuple[bool, bool]:
        """Returns (still_active, just_disabled_by_expiry).

        Past next_renewal_at → status=disabled (not expired). Caller cascades.
        """
        now = datetime.now(UTC)
        if row.next_renewal_at is not None and row.next_renewal_at <= now:
            if row.status == KeyStatus.ACTIVE:
                row.status = KeyStatus.DISABLED
                return False, True
            return False, False
        if row.status != KeyStatus.ACTIVE:
            return False, False
        if not (row.secret_ref or "").strip():
            return False, False
        return True, False

    def _pick_runtime_rows(
        self,
        rows: list[AiProviderKeyRow],
        *,
        preferred_provider: str | None,
    ) -> tuple[list[AiProviderKeyRow], list[str]]:
        disabled_ids: list[str] = []
        runtime: list[AiProviderKeyRow] = []
        for row in rows:
            active, just_disabled = self._apply_lazy_expiry(row)
            if just_disabled:
                disabled_ids.append(row.id)
            if not active:
                continue
            if is_runtime_api_kind(row.api_kind):
                runtime.append(row)
        if preferred_provider:
            matched = [r for r in runtime if r.provider == preferred_provider]
            if matched:
                runtime = matched
        return runtime, disabled_ids

    async def _finalize_lazy_disabled(
        self, key_ids: list[str], *, principal: Principal | None = None
    ) -> None:
        if not key_ids:
            return
        await self._session.commit()
        await self._emit_lazy_expire_audits(key_ids)
        actor = principal or _SYSTEM_PRINCIPAL
        for key_id in key_ids:
            await self.cascade_key_runtime_stop(key_id, principal=actor)
    async def _unbound_active_keys(self) -> list[AiProviderKeyRow]:
        bound = select(CompanyAiKeyBindingRow.key_id)
        q = await self._session.execute(
            select(AiProviderKeyRow)
            .where(
                AiProviderKeyRow.status == KeyStatus.ACTIVE,
                AiProviderKeyRow.id.not_in(bound),
            )
            .order_by(AiProviderKeyRow.created_at)
        )
        return list(q.scalars().all())

    def _to_public(self, row: AiProviderKeyRow, *, company_ids: list[str] | None = None) -> dict:
        prefix = row.secret_ref[:24] + "…" if len(row.secret_ref) > 24 else row.secret_ref
        return {
            "id": row.id,
            "name": row.name,
            "provider": row.provider,
            "api_kind": row.api_kind,
            "secret_ref_prefix": prefix,
            "status": row.status,
            "next_renewal_at": row.next_renewal_at.isoformat() if row.next_renewal_at else None,
            "renewal_price": str(row.renewal_price) if row.renewal_price is not None else None,
            "currency": row.currency,
            "notes": row.notes,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "updated_at": row.updated_at.isoformat() if row.updated_at else None,
            "company_ids": company_ids,
        }

    async def list_keys(self) -> list[dict]:
        q = await self._session.execute(select(AiProviderKeyRow).order_by(AiProviderKeyRow.created_at))
        disabled_ids: list[str] = []
        rows = list(q.scalars().all())
        for row in rows:
            _, just_disabled = self._apply_lazy_expiry(row)
            if just_disabled:
                disabled_ids.append(row.id)
        await self._finalize_lazy_disabled(disabled_ids)
        out: list[dict] = []
        for row in rows:
            out.append(self._to_public(row, company_ids=await self._company_ids(row.id)))
        return out

    async def company_key_metrics(self, company_id: str) -> dict[str, Any]:
        """Read-model for L04 metrics/alerts — bound keys and upcoming renewals."""
        now = datetime.now(UTC)
        soon = now + timedelta(days=KEY_EXPIRING_SOON_DAYS)
        q = await self._session.execute(
            select(AiProviderKeyRow)
            .join(CompanyAiKeyBindingRow, CompanyAiKeyBindingRow.key_id == AiProviderKeyRow.id)
            .where(CompanyAiKeyBindingRow.company_id == company_id)
        )
        bound = 0
        expiring_soon = 0
        next_renewal: datetime | None = None
        for row in q.scalars().all():
            if row.status == KeyStatus.DISABLED or row.status == KeyStatus.EXPIRED:
                continue
            if row.next_renewal_at is not None and row.next_renewal_at <= now:
                continue
            if row.status != KeyStatus.ACTIVE:
                continue
            bound += 1
            if row.next_renewal_at is not None:
                if next_renewal is None or row.next_renewal_at < next_renewal:
                    next_renewal = row.next_renewal_at
                if row.next_renewal_at <= soon:
                    expiring_soon += 1
        return {
            "ai_keys_bound": bound,
            "ai_keys_expiring_soon": expiring_soon,
            "next_key_renewal_at": next_renewal.isoformat() if next_renewal else None,
        }

    async def get_key(self, key_id: str) -> dict:
        row = await self._get_row(key_id)
        _, just_disabled = self._apply_lazy_expiry(row)
        if just_disabled:
            await self._finalize_lazy_disabled([key_id])
            row = await self._get_row(key_id)
        return self._to_public(row, company_ids=await self._company_ids(key_id))

    async def create_key(
        self,
        *,
        name: str,
        provider: str,
        api_kind: str,
        secret: str | None = None,
        next_renewal_at: datetime | None = None,
        renewal_price: str | Decimal | None = None,
        currency: str | None = None,
        notes: str | None = None,
        company_ids: list[str] | None = None,
        principal: Principal | None = None,
    ) -> dict:
        self._validate_provider_kind(provider, api_kind)
        key_id = new_key_id()
        has_secret = secret is not None and secret.strip() != ""
        secret_ref = self._secrets.put(key_id, secret) if has_secret else ""
        row = AiProviderKeyRow(
            id=key_id,
            name=name.strip(),
            provider=provider,
            api_kind=api_kind,
            secret_ref=secret_ref,
            status=KeyStatus.ACTIVE if has_secret else KeyStatus.DISABLED,
            next_renewal_at=next_renewal_at,
            renewal_price=_parse_price(renewal_price),
            currency=currency,
            notes=notes,
        )
        self._session.add(row)
        await self._session.flush()
        if company_ids:
            await self._replace_bindings(key_id, company_ids)
        await self._session.commit()
        await self._session.refresh(row)
        await self._emit_audit(
            event_type="ai_key.created",
            key_id=row.id,
            principal=principal,
            detail={
                "name": row.name,
                "provider": row.provider,
                "api_kind": row.api_kind,
                "company_ids": list(company_ids or []),
            },
        )
        return self._to_public(row, company_ids=list(company_ids or []))

    async def patch_key(
        self, key_id: str, updates: dict[str, Any], *, principal: Principal | None = None
    ) -> dict:
        row = await self._get_row(key_id)
        old_status = row.status
        if "name" in updates and updates["name"] is not None:
            row.name = str(updates["name"]).strip()
        if "provider" in updates and updates["provider"] is not None:
            provider = str(updates["provider"]).strip()
            self._validate_provider_kind(provider, row.api_kind)
            row.provider = provider
        if "api_kind" in updates and updates["api_kind"] is not None:
            api_kind = str(updates["api_kind"]).strip()
            self._validate_provider_kind(row.provider, api_kind)
            row.api_kind = api_kind
        if "status" in updates and updates["status"] is not None:
            status = updates["status"]
            if status not in {KeyStatus.ACTIVE, KeyStatus.EXPIRED, KeyStatus.DISABLED}:
                raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad status")
            row.status = status
        if "next_renewal_at" in updates:
            row.next_renewal_at = updates["next_renewal_at"]
        if "renewal_price" in updates:
            row.renewal_price = _parse_price(updates["renewal_price"])
        if "currency" in updates:
            row.currency = updates["currency"]
        if "notes" in updates:
            row.notes = updates["notes"]
        await self._session.commit()
        await self._session.refresh(row)
        event_type = (
            "ai_key.disabled"
            if row.status == KeyStatus.DISABLED and old_status != KeyStatus.DISABLED
            else "ai_key.updated"
        )
        await self._emit_audit(
            event_type=event_type,
            key_id=key_id,
            principal=principal,
            detail={"fields": sorted(updates.keys()), "status": row.status},
        )
        cascade: dict[str, Any] = {}
        if row.status == KeyStatus.DISABLED and old_status != KeyStatus.DISABLED:
            cascade = await self.cascade_key_runtime_stop(key_id, principal=principal)
        out = self._to_public(row, company_ids=await self._company_ids(key_id))
        if cascade:
            out["runtime_cascade"] = cascade
        return out

    async def renew(self, key_id: str, months: int, *, principal: Principal | None = None) -> dict:
        if months < 1 or months > 12:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="months 1..12")
        row = await self._get_row(key_id)
        now = datetime.now(UTC)
        base = row.next_renewal_at if row.next_renewal_at and row.next_renewal_at > now else now
        row.next_renewal_at = _add_months(base, months)
        # Never auto-activate: expire/disable stays until explicit resume (PATCH status=active).
        await self._session.commit()
        await self._session.refresh(row)
        renewal_iso = row.next_renewal_at.isoformat() if row.next_renewal_at else None
        await self._emit_audit(
            event_type="ai_key.renewed",
            key_id=key_id,
            principal=principal,
            detail={"months": months, "next_renewal_at": renewal_iso},
        )
        return self._to_public(row, company_ids=await self._company_ids(key_id))

    async def rotate_secret(
        self, key_id: str, secret: str, *, principal: Principal | None = None
    ) -> dict:
        row = await self._get_row(key_id)
        old_ref = row.secret_ref
        row.secret_ref = self._secrets.put(key_id, secret)
        if old_ref and old_ref != row.secret_ref:
            self._secrets.delete(old_ref)
        # Never auto-activate: resume is an explicit PATCH status=active.
        await self._session.commit()
        await self._session.refresh(row)
        await self._emit_audit(
            event_type="ai_key.rotated",
            key_id=key_id,
            principal=principal,
            detail={"secret_ref_prefix": row.secret_ref[:24] + "…" if len(row.secret_ref) > 24 else row.secret_ref},
        )
        return self._to_public(row, company_ids=await self._company_ids(key_id))

    async def set_companies(
        self, key_id: str, company_ids: list[str], *, principal: Principal | None = None
    ) -> dict:
        await self._get_row(key_id)
        await self._replace_bindings(key_id, company_ids)
        await self._session.commit()
        await self._emit_audit(
            event_type="ai_key.companies_set",
            key_id=key_id,
            principal=principal,
            detail={"company_ids": list(company_ids)},
        )
        return await self.get_key(key_id)

    async def delete_key(self, key_id: str, *, principal: Principal | None = None) -> None:
        row = await self._get_row(key_id)
        ref = row.secret_ref
        name = row.name
        cascade = await self.cascade_key_runtime_stop(key_id, principal=principal)
        # Cascade may commit (project.pause); re-load before delete.
        row = await self._get_row(key_id)
        await self._session.delete(row)
        await self._session.commit()
        self._secrets.delete(ref)
        await self._emit_audit(
            event_type="ai_key.deleted",
            key_id=key_id,
            principal=principal,
            detail={"name": name, "runtime_cascade": cascade},
        )

    async def cascade_key_runtime_stop(
        self, key_id: str, *, principal: Principal | None = None
    ) -> dict[str, Any]:
        """Cancel sessions resolved to this key; pause projects that lose last runtime binding.

        Key does not own Project — effect goes through bindings + session snapshot.
        Pause rule: company has no remaining ACTIVE runtime-capable binding for its
        preferred_provider (or any provider if preferred_provider is unset).
        """
        from prodavan.application.agent.session_service import AgentSessionService
        from prodavan.application.projects.project_service import ProjectService
        from prodavan.domain.projects import ProjectStatus
        from prodavan.infrastructure.persistence.models.projects import ProjectRow

        actor = principal or Principal(sub="system:ai-key-cascade")
        sessions_cancelled = await AgentSessionService(self._session).cancel_active_for_key(
            key_id=key_id
        )
        await self._session.flush()

        company_ids = await self._company_ids(key_id)
        projects_paused: list[str] = []
        projects = ProjectService(self._session)
        for company_id in company_ids:
            if not await self._company_lost_runtime_key(company_id, exclude_key_id=key_id):
                continue
            q = await self._session.execute(
                select(ProjectRow.id).where(
                    ProjectRow.company_id == company_id,
                    ProjectRow.status == ProjectStatus.ACTIVE,
                )
            )
            for project_id in q.scalars().all():
                await projects.pause(project_id=project_id, principal=actor, employee=None)
                projects_paused.append(project_id)

        # Ensure session cancels are durable even if no project was paused.
        if sessions_cancelled and not projects_paused:
            await self._session.commit()

        return {
            "sessions_cancelled": sessions_cancelled,
            "companies_considered": list(company_ids),
            "projects_paused": projects_paused,
        }

    async def _company_lost_runtime_key(self, company_id: str, *, exclude_key_id: str) -> bool:
        """True when company has no other ACTIVE runtime-capable binding for preferred_provider."""
        from prodavan.application.admin.company_service import AdminCompanyService

        policy = await AdminCompanyService(self._session).get_agent_policy(company_id)
        preferred = policy.preferred_provider
        q = await self._session.execute(
            select(AiProviderKeyRow)
            .join(CompanyAiKeyBindingRow, CompanyAiKeyBindingRow.key_id == AiProviderKeyRow.id)
            .where(
                CompanyAiKeyBindingRow.company_id == company_id,
                AiProviderKeyRow.id != exclude_key_id,
                AiProviderKeyRow.status == KeyStatus.ACTIVE,
            )
        )
        remaining = [
            row
            for row in q.scalars().all()
            if is_runtime_api_kind(row.api_kind)
            and (row.secret_ref or "").strip()
            and (not preferred or row.provider == preferred)
        ]
        return len(remaining) == 0

    async def resolve_credentials(
        self,
        *,
        company_id: str,
        preferred_provider: str | None = None,
        platform_fallback: bool = False,
    ) -> ResolvedCredential:
        """Runtime path for L08. Never returns cli_subscription."""
        q = await self._session.execute(
            select(AiProviderKeyRow)
            .join(CompanyAiKeyBindingRow, CompanyAiKeyBindingRow.key_id == AiProviderKeyRow.id)
            .where(
                CompanyAiKeyBindingRow.company_id == company_id,
                AiProviderKeyRow.status == KeyStatus.ACTIVE,
            )
            .order_by(AiProviderKeyRow.created_at.desc())
        )
        bound_rows = list(q.scalars().all())
        runtime, disabled_ids = self._pick_runtime_rows(
            bound_rows, preferred_provider=preferred_provider
        )
        await self._finalize_lazy_disabled(disabled_ids)

        chosen = runtime[0] if runtime else None

        if chosen is None and platform_fallback:
            pool_rows = await self._unbound_active_keys()
            pool_runtime, pool_disabled = self._pick_runtime_rows(
                pool_rows, preferred_provider=preferred_provider
            )
            await self._finalize_lazy_disabled(pool_disabled)
            chosen = pool_runtime[0] if pool_runtime else None

        if chosen is None:
            only_cli = bool(bound_rows) and all(
                r.api_kind == ApiKind.CLI_SUBSCRIPTION for r in bound_rows
            )
            detail = (
                "only cli_subscription bindings; not a runtime credential"
                if only_cli
                else "no active runtime AI key for company"
            )
            raise AppError(code="NO_AI_KEY", title="No AI key", status=404, detail=detail)

        secret = self._secrets.get(chosen.secret_ref)
        return ResolvedCredential(
            key_id=chosen.id,
            provider=chosen.provider,
            api_kind=chosen.api_kind,
            secret=secret,
        )

    def _validate_provider_kind(self, provider: str, api_kind: str) -> None:
        if provider not in PROVIDERS:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad provider")
        if api_kind not in API_KINDS:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="bad api_kind")

    async def _get_row(self, key_id: str) -> AiProviderKeyRow:
        row = await self._session.get(AiProviderKeyRow, key_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="AI key not found")
        return row

    async def _company_ids(self, key_id: str) -> list[str]:
        q = await self._session.execute(
            select(CompanyAiKeyBindingRow.company_id).where(CompanyAiKeyBindingRow.key_id == key_id)
        )
        return list(q.scalars().all())

    async def list_audit_events(self, *, key_id: str | None = None, limit: int = 50) -> list[dict]:
        return await self._audit.list_events(key_id=key_id, limit=limit)

    async def _replace_bindings(self, key_id: str, company_ids: list[str]) -> None:
        unique = list(dict.fromkeys(company_ids))
        for cid in unique:
            co = await self._session.get(CompanyRow, cid)
            if co is None:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail=f"unknown company_id: {cid}",
                )
        existing = await self._session.execute(
            select(CompanyAiKeyBindingRow).where(CompanyAiKeyBindingRow.key_id == key_id)
        )
        for b in existing.scalars().all():
            await self._session.delete(b)
        await self._session.flush()
        for cid in unique:
            self._session.add(CompanyAiKeyBindingRow(company_id=cid, key_id=key_id))
