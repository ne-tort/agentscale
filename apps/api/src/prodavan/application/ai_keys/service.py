"""AI Provider Keys application service (L03)."""

from __future__ import annotations

from calendar import monthrange
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.ai_keys import (
    ApiKind,
    KeyStatus,
    ResolvedCredential,
    is_runtime_api_kind,
)
from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow, CompanyAiKeyBindingRow
from prodavan.infrastructure.persistence.models.identity import CompanyRow
from prodavan.infrastructure.secrets.file_store import FileSecretStore, new_key_id

PROVIDERS = frozenset({"cursor", "codex", "claude_code"})
API_KINDS = frozenset(k.value for k in ApiKind)
KEY_EXPIRING_SOON_DAYS = 14


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
    def __init__(self, session: AsyncSession, secrets: FileSecretStore | None = None) -> None:
        self._session = session
        self._secrets = secrets or FileSecretStore()

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
        out: list[dict] = []
        for row in q.scalars().all():
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
        return self._to_public(row, company_ids=await self._company_ids(key_id))

    async def create_key(
        self,
        *,
        name: str,
        provider: str,
        api_kind: str,
        secret: str,
        next_renewal_at: datetime | None = None,
        renewal_price: str | Decimal | None = None,
        currency: str | None = None,
        notes: str | None = None,
        company_ids: list[str] | None = None,
    ) -> dict:
        self._validate_provider_kind(provider, api_kind)
        key_id = new_key_id()
        secret_ref = self._secrets.put(key_id, secret)
        row = AiProviderKeyRow(
            id=key_id,
            name=name.strip(),
            provider=provider,
            api_kind=api_kind,
            secret_ref=secret_ref,
            status=KeyStatus.ACTIVE,
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
        return self._to_public(row, company_ids=list(company_ids or []))

    async def patch_key(self, key_id: str, updates: dict[str, Any]) -> dict:
        row = await self._get_row(key_id)
        if "name" in updates and updates["name"] is not None:
            row.name = str(updates["name"]).strip()
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
        return self._to_public(row, company_ids=await self._company_ids(key_id))

    async def renew(self, key_id: str, months: int) -> dict:
        if months < 1 or months > 12:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="months 1..12")
        row = await self._get_row(key_id)
        now = datetime.now(UTC)
        base = row.next_renewal_at if row.next_renewal_at and row.next_renewal_at > now else now
        row.next_renewal_at = _add_months(base, months)
        if row.status == KeyStatus.EXPIRED:
            row.status = KeyStatus.ACTIVE
        await self._session.commit()
        await self._session.refresh(row)
        return self._to_public(row, company_ids=await self._company_ids(key_id))

    async def rotate_secret(self, key_id: str, secret: str) -> dict:
        row = await self._get_row(key_id)
        old_ref = row.secret_ref
        row.secret_ref = self._secrets.put(key_id, secret)
        if old_ref != row.secret_ref:
            self._secrets.delete(old_ref)
        await self._session.commit()
        await self._session.refresh(row)
        return self._to_public(row, company_ids=await self._company_ids(key_id))

    async def set_companies(self, key_id: str, company_ids: list[str]) -> dict:
        await self._get_row(key_id)
        await self._replace_bindings(key_id, company_ids)
        await self._session.commit()
        return await self.get_key(key_id)

    async def delete_key(self, key_id: str) -> None:
        row = await self._get_row(key_id)
        ref = row.secret_ref
        await self._session.delete(row)
        await self._session.commit()
        self._secrets.delete(ref)

    def _mark_expired_if_past(self, row: AiProviderKeyRow) -> bool:
        """Lazy expiry by next_renewal_at. Returns True when key remains active."""
        now = datetime.now(UTC)
        if row.next_renewal_at is not None and row.next_renewal_at <= now:
            if row.status == KeyStatus.ACTIVE:
                row.status = KeyStatus.EXPIRED
            return False
        return row.status == KeyStatus.ACTIVE

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
            .order_by(AiProviderKeyRow.created_at)
        )
        rows = list(q.scalars().all())
        expired_any = False
        runtime: list[AiProviderKeyRow] = []
        for row in rows:
            if not self._mark_expired_if_past(row):
                expired_any = True
                continue
            if is_runtime_api_kind(row.api_kind):
                runtime.append(row)
        if expired_any:
            await self._session.commit()
        if preferred_provider:
            matched = [r for r in runtime if r.provider == preferred_provider]
            if matched:
                runtime = matched
        chosen = runtime[0] if runtime else None

        if chosen is None and platform_fallback:
            # Gap: platform-owned key pool not implemented yet (as-built L03).
            pass

        if chosen is None:
            only_cli = bool(rows) and all(r.api_kind == ApiKind.CLI_SUBSCRIPTION for r in rows)
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
