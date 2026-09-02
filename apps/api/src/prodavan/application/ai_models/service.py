"""AI model catalog CRUD + key bindings."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.domain.errors import AppError
from prodavan.infrastructure.persistence.models.ai_keys import AiProviderKeyRow
from prodavan.infrastructure.persistence.models.ai_models import (
    AiKeyModelBindingRow,
    AiModelRow,
    AiModelSdkBindingRow,
)


class AiModelsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def list_models_for_company(self, company_id: str) -> list[dict[str, Any]]:
        q = await self._session.execute(
            select(AiModelRow)
            .where(
                (AiModelRow.owner_scope == "platform")
                | ((AiModelRow.owner_scope == "company") & (AiModelRow.owner_company_id == company_id))
            )
            .order_by(AiModelRow.name)
        )
        rows = list(q.scalars().all())
        out: list[dict[str, Any]] = []
        for row in rows:
            out.append(await self._model_public(row))
        return out

    async def create_model(
        self,
        *,
        company_id: str,
        name: str,
        api_kinds: list[str] | None = None,
        input_price_usd_per_mtok: Decimal | None = None,
        output_price_usd_per_mtok: Decimal | None = None,
        max_context_tokens: int | None = None,
        publisher: str | None = None,
        released_at: date | None = None,
    ) -> dict[str, Any]:
        model_name = name.strip()
        if not model_name:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="model name required")
        row = AiModelRow(
            name=model_name,
            owner_scope="company",
            owner_company_id=company_id,
            input_price_usd_per_mtok=input_price_usd_per_mtok,
            output_price_usd_per_mtok=output_price_usd_per_mtok,
            max_context_tokens=max_context_tokens,
            publisher=publisher.strip() if publisher else None,
            released_at=released_at,
        )
        self._session.add(row)
        await self._session.flush()
        for api_kind in api_kinds or []:
            kind = str(api_kind).strip()
            if kind:
                await self._bind_sdk(model_id=row.id, api_kind=kind)
        return await self._model_public(row)

    async def update_model(
        self,
        *,
        company_id: str,
        model_id: str,
        name: str | None = None,
        api_kinds: list[str] | None = None,
        input_price_usd_per_mtok: Decimal | None = None,
        output_price_usd_per_mtok: Decimal | None = None,
        max_context_tokens: int | None = None,
        publisher: str | None = None,
        released_at: date | None = None,
    ) -> dict[str, Any]:
        row = await self._require_company_model(model_id, company_id)
        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                raise AppError(
                    code="VALIDATION_ERROR",
                    title="Validation Error",
                    status=422,
                    detail="model name required",
                )
            row.name = cleaned
        if input_price_usd_per_mtok is not None:
            row.input_price_usd_per_mtok = input_price_usd_per_mtok
        if output_price_usd_per_mtok is not None:
            row.output_price_usd_per_mtok = output_price_usd_per_mtok
        if max_context_tokens is not None:
            row.max_context_tokens = max_context_tokens
        if publisher is not None:
            row.publisher = publisher.strip() or None
        if released_at is not None:
            row.released_at = released_at
        if api_kinds is not None:
            await self._session.execute(delete(AiModelSdkBindingRow).where(AiModelSdkBindingRow.model_id == model_id))
            for api_kind in api_kinds:
                kind = str(api_kind).strip()
                if kind:
                    await self._bind_sdk(model_id=model_id, api_kind=kind)
        await self._session.flush()
        return await self._model_public(row)

    async def list_key_models(self, *, company_id: str, key_id: str) -> list[dict[str, Any]]:
        key = await self._require_company_key(key_id, company_id)
        models = await self._models_for_api_kind(company_id=company_id, api_kind=key.api_kind)
        bindings_q = await self._session.execute(
            select(AiKeyModelBindingRow).where(AiKeyModelBindingRow.key_id == key_id)
        )
        bindings = {b.model_id: b for b in bindings_q.scalars().all()}
        out: list[dict[str, Any]] = []
        for model in models:
            binding = bindings.get(model.id)
            item = await self._model_public(model)
            item["enabled"] = bool(binding.enabled) if binding else False
            item["is_default"] = bool(binding.is_default) if binding else False
            out.append(item)
        return out

    async def update_key_models(
        self,
        *,
        company_id: str,
        key_id: str,
        selections: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        key = await self._require_company_key(key_id, company_id)
        allowed_ids = {
            m.id for m in await self._models_for_api_kind(company_id=company_id, api_kind=key.api_kind)
        }
        default_id: str | None = None
        for item in selections:
            model_id = str(item.get("model_id") or "").strip()
            if not model_id or model_id not in allowed_ids:
                continue
            enabled = bool(item.get("enabled"))
            is_default = bool(item.get("is_default"))
            if is_default:
                default_id = model_id
            existing_q = await self._session.execute(
                select(AiKeyModelBindingRow).where(
                    AiKeyModelBindingRow.key_id == key_id,
                    AiKeyModelBindingRow.model_id == model_id,
                )
            )
            existing = existing_q.scalar_one_or_none()
            if existing is None:
                if enabled or is_default:
                    self._session.add(
                        AiKeyModelBindingRow(
                            key_id=key_id,
                            model_id=model_id,
                            enabled=enabled,
                            is_default=is_default,
                        )
                    )
            else:
                existing.enabled = enabled
                existing.is_default = is_default
        if default_id:
            await self._session.execute(
                update(AiKeyModelBindingRow)
                .where(AiKeyModelBindingRow.key_id == key_id)
                .values(is_default=False)
            )
            await self._session.execute(
                update(AiKeyModelBindingRow)
                .where(
                    AiKeyModelBindingRow.key_id == key_id,
                    AiKeyModelBindingRow.model_id == default_id,
                )
                .values(is_default=True, enabled=True)
            )
        await self._session.flush()
        return await self.list_key_models(company_id=company_id, key_id=key_id)

    async def _bind_sdk(self, *, model_id: str, api_kind: str) -> None:
        self._session.add(AiModelSdkBindingRow(model_id=model_id, api_kind=api_kind))

    async def _models_for_api_kind(self, *, company_id: str, api_kind: str) -> list[AiModelRow]:
        q = await self._session.execute(
            select(AiModelRow)
            .join(AiModelSdkBindingRow, AiModelSdkBindingRow.model_id == AiModelRow.id)
            .where(
                AiModelSdkBindingRow.api_kind == api_kind,
                (AiModelRow.owner_scope == "platform")
                | ((AiModelRow.owner_scope == "company") & (AiModelRow.owner_company_id == company_id)),
            )
            .order_by(AiModelRow.name)
        )
        return list(q.scalars().unique().all())

    async def _model_public(self, row: AiModelRow) -> dict[str, Any]:
        sdk_q = await self._session.execute(
            select(AiModelSdkBindingRow.api_kind).where(AiModelSdkBindingRow.model_id == row.id)
        )
        return {
            "id": row.id,
            "name": row.name,
            "owner_scope": row.owner_scope,
            "owner_company_id": row.owner_company_id,
            "api_kinds": list(sdk_q.scalars().all()),
            "input_price_usd_per_mtok": float(row.input_price_usd_per_mtok)
            if row.input_price_usd_per_mtok is not None
            else None,
            "output_price_usd_per_mtok": float(row.output_price_usd_per_mtok)
            if row.output_price_usd_per_mtok is not None
            else None,
            "max_context_tokens": row.max_context_tokens,
            "publisher": row.publisher,
            "released_at": row.released_at.isoformat() if row.released_at else None,
        }

    async def _require_company_model(self, model_id: str, company_id: str) -> AiModelRow:
        row = await self._session.get(AiModelRow, model_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="model not found")
        if row.owner_scope == "company" and row.owner_company_id != company_id:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="model not in company scope")
        return row

    async def require_company_key(self, key_id: str, company_id: str) -> AiProviderKeyRow:
        return await self._require_company_key(key_id, company_id)

    async def _require_company_key(self, key_id: str, company_id: str) -> AiProviderKeyRow:
        row = await self._session.get(AiProviderKeyRow, key_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="AI key not found")
        if row.owner_scope == "company" and row.owner_company_id != company_id:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="AI key not in company scope")
        return row
