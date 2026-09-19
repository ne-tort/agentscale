"""AI model catalog CRUD + key bindings.

The model entity is the catalog entry for an AI model. Most fields are
optional; `name` is the only required field. `key_aliases` is a JSONB array
of stable provider model ids (e.g. ["ca-opus-4.6", "claude-opus-4-6"]) used
to auto-match models returned by a key probe (GET /models) to catalog entries.

owner_scope: "platform" (admin-owned, shared) | "company" (company-owned).
"""

from __future__ import annotations

import json
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


def _normalize_aliases(aliases: list[str] | None) -> list[str]:
    """Dedup + strip + drop empties; preserve order."""
    out: list[str] = []
    seen: set[str] = set()
    for a in aliases or []:
        s = str(a).strip()
        if not s or s.lower() in seen:
            continue
        seen.add(s.lower())
        out.append(s)
    return out


def _normalize_api_kinds(kinds: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for k in kinds or []:
        s = str(k).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


class AiModelsService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ---- read ----

    async def list_models(self) -> list[dict[str, Any]]:
        """Admin listing: all models (platform + every company)."""
        q = await self._session.execute(select(AiModelRow).order_by(AiModelRow.name))
        return [await self._model_public(r) for r in q.scalars().all()]

    async def get_model(self, model_id: str) -> dict[str, Any]:
        row = await self._get_row(model_id)
        return await self._model_public(row)

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

    # ---- create ----

    async def create_model(
        self,
        *,
        name: str,
        key_aliases: list[str] | None = None,
        provider: str | None = None,
        reasoning_level: str | None = None,
        description: str | None = None,
        api_kinds: list[str] | None = None,
        input_price_usd_per_mtok: Decimal | None = None,
        output_price_usd_per_mtok: Decimal | None = None,
        max_context_tokens: int | None = None,
        publisher: str | None = None,
        released_at: date | None = None,
        owner_scope: str = "platform",
        owner_company_id: str | None = None,
    ) -> dict[str, Any]:
        model_name = name.strip()
        if not model_name:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="model name required")
        scope = (owner_scope or "platform").strip().lower()
        if scope not in {"platform", "company"}:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="owner_scope must be platform|company",
            )
        if scope == "company" and not owner_company_id:
            raise AppError(
                code="VALIDATION_ERROR",
                title="Validation Error",
                status=422,
                detail="owner_company_id required for company scope",
            )
        row = AiModelRow(
            name=model_name,
            key_aliases=_normalize_aliases(key_aliases),
            provider=(provider or "").strip() or None if provider is not None else None,
            reasoning_level=(reasoning_level or "").strip() or None if reasoning_level is not None else None,
            description=(description or "").strip() or None if description is not None else None,
            owner_scope=scope,
            owner_company_id=owner_company_id if scope == "company" else None,
            input_price_usd_per_mtok=input_price_usd_per_mtok,
            output_price_usd_per_mtok=output_price_usd_per_mtok,
            max_context_tokens=max_context_tokens,
            publisher=(publisher or "").strip() or None if publisher is not None else None,
            released_at=released_at,
        )
        self._session.add(row)
        await self._session.flush()
        for api_kind in _normalize_api_kinds(api_kinds):
            await self._bind_sdk(model_id=row.id, api_kind=api_kind)
        await self._session.commit()
        await self._session.refresh(row)
        self._emit_model_created_event(row)
        return await self._model_public(row)

    # ---- update ----

    async def update_model(
        self,
        *,
        model_id: str,
        name: str | None = None,
        key_aliases: list[str] | None = None,
        provider: str | None = None,
        reasoning_level: str | None = None,
        description: str | None = None,
        api_kinds: list[str] | None = None,
        input_price_usd_per_mtok: Decimal | None = None,
        output_price_usd_per_mtok: Decimal | None = None,
        max_context_tokens: int | None = None,
        publisher: str | None = None,
        released_at: date | None = None,
        company_id: str | None = None,
    ) -> dict[str, Any]:
        row = await self._require_model(model_id, company_id)
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
        if key_aliases is not None:
            row.key_aliases = _normalize_aliases(key_aliases)
        if provider is not None:
            row.provider = (provider or "").strip() or None
        if reasoning_level is not None:
            row.reasoning_level = (reasoning_level or "").strip() or None
        if description is not None:
            row.description = (description or "").strip() or None
        if input_price_usd_per_mtok is not None:
            row.input_price_usd_per_mtok = input_price_usd_per_mtok
        if output_price_usd_per_mtok is not None:
            row.output_price_usd_per_mtok = output_price_usd_per_mtok
        if max_context_tokens is not None:
            row.max_context_tokens = max_context_tokens
        if publisher is not None:
            row.publisher = (publisher or "").strip() or None
        if released_at is not None:
            row.released_at = released_at
        if api_kinds is not None:
            await self._session.execute(delete(AiModelSdkBindingRow).where(AiModelSdkBindingRow.model_id == model_id))
            for api_kind in _normalize_api_kinds(api_kinds):
                await self._bind_sdk(model_id=model_id, api_kind=api_kind)
        await self._session.commit()
        await self._session.refresh(row)
        self._emit_model_updated_event(row)
        return await self._model_public(row)

    # ---- delete ----

    async def delete_model(self, *, model_id: str, company_id: str | None = None) -> None:
        row = await self._require_model(model_id, company_id)
        name = row.name
        await self._session.delete(row)
        await self._session.commit()
        self._emit_model_deleted_event(model_id, name)

    # ---- auto-match on probe (business logic) ----

    async def upsert_by_alias(
        self,
        model_key: str,
        *,
        provider: str | None = None,
        api_kind: str | None = None,
    ) -> tuple[dict[str, Any], bool]:
        """Match a provider model id (from a key probe GET /models) to a catalog entry.

        Exact match strategy:
        1. Look for a catalog entry whose `key_aliases` (case-insensitive)
           contains the model_key, OR whose `name` (case-insensitive) equals it.
        2. If found — return it (no mutation).
        3. If not found — create a new platform model with name=model_key,
           key_aliases=[model_key], provider, and an SDK binding to api_kind
           (so it shows up in list_key_models for that key).

        Returns (model_dict, created).
        """
        key = (model_key or "").strip()
        if not key:
            raise AppError(code="VALIDATION_ERROR", title="Validation Error", status=422, detail="model_key required")
        key_lower = key.lower()

        # 1. exact match by alias or name (case-insensitive)
        rows_q = await self._session.execute(select(AiModelRow))
        for row in rows_q.scalars().all():
            aliases = row.key_aliases if isinstance(row.key_aliases, list) else []
            aliases_lower = {str(a).strip().lower() for a in aliases if a}
            if key_lower in aliases_lower or row.name.strip().lower() == key_lower:
                return await self._model_public(row), False

        # 2. create new platform entry from the model_key
        row = AiModelRow(
            name=key,
            key_aliases=[key],
            provider=(provider or "").strip() or None if provider is not None else None,
            owner_scope="platform",
            owner_company_id=None,
        )
        self._session.add(row)
        await self._session.flush()
        if api_kind:
            kind = str(api_kind).strip()
            if kind:
                await self._bind_sdk(model_id=row.id, api_kind=kind)
        await self._session.commit()
        await self._session.refresh(row)
        self._emit_model_created_event(row)
        return await self._model_public(row), True

    async def reconcile_probe_models(
        self,
        *,
        model_keys: list[str],
        provider: str | None = None,
        api_kind: str | None = None,
    ) -> dict[str, Any]:
        """Auto-match a probe's model list against the catalog; create missing ones.

        Returns {matched: int, created: int, total: int}.
        """
        matched = 0
        created = 0
        for mk in model_keys:
            s = (mk or "").strip()
            if not s:
                continue
            _, was_created = await self.upsert_by_alias(s, provider=provider, api_kind=api_kind)
            if was_created:
                created += 1
            else:
                matched += 1
        return {"matched": matched, "created": created, "total": matched + created}

    async def list_key_models(self, *, company_id: str, key_id: str) -> list[dict[str, Any]]:
        """Models visible to a key (platform + company scope) with the
        key↔model binding state (enabled / is_default).

        Per MODELS-L2: filtering is by the key↔model binding (AiKeyModelBindingRow),
        NOT by api_kind/SDK. A model enabled on a key shows up here; models with
        no binding show enabled=false so the user can toggle them on.
        """
        key = await self._require_company_key(key_id, company_id)
        models = await self._models_visible_to_company(company_id=key.owner_company_id or company_id)
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
        """Set the key↔model binding state (enabled / is_default toggle).

        Per MODELS-L2: this IS the key↔model link with an on/off property.
        Toggling enabled=true emits a relation.granted event (model linked to
        key); enabled=false emits relation.revoked. The AiKeyModelBindingRow
        itself stays (so the user can re-toggle without re-creating); the
        relation event reflects the logical on/off state.
        """
        from prodavan.application.relations.commands import RelationsCommand

        await self._require_company_key(key_id, company_id)
        allowed_ids = {m.id for m in await self._models_visible_to_company(company_id=company_id)}
        default_id: str | None = None
        rel = RelationsCommand(self._session)
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
                    if enabled:
                        await rel.link_model_to_key(model_id=model_id, key_id=key_id, company_id=company_id)
            else:
                was_enabled = bool(existing.enabled)
                existing.enabled = enabled
                existing.is_default = is_default
                if enabled and not was_enabled:
                    await rel.link_model_to_key(model_id=model_id, key_id=key_id, company_id=company_id)
                elif not enabled and was_enabled:
                    await rel.unlink_model_from_key(model_id=model_id, key_id=key_id, company_id=company_id)
        if default_id:
            await self._session.execute(
                update(AiKeyModelBindingRow).where(AiKeyModelBindingRow.key_id == key_id).values(is_default=False)
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
        await self._session.commit()
        return await self.list_key_models(company_id=company_id, key_id=key_id)

    async def _bind_sdk(self, *, model_id: str, api_kind: str) -> None:
        self._session.add(AiModelSdkBindingRow(model_id=model_id, api_kind=api_kind))

    async def _models_visible_to_company(self, *, company_id: str) -> list[AiModelRow]:
        """All platform models + company-owned models (no api_kind filter)."""
        q = await self._session.execute(
            select(AiModelRow)
            .where(
                (AiModelRow.owner_scope == "platform")
                | ((AiModelRow.owner_scope == "company") & (AiModelRow.owner_company_id == company_id))
            )
            .order_by(AiModelRow.name)
        )
        return list(q.scalars().all())

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

    async def _last_probe_model_ids(self, key_id: str) -> list[str]:
        """Model ids returned by the last successful probe of this key (or [])."""
        from prodavan.infrastructure.persistence.models.ai_keys import AiKeyCheckResultRow

        q = await self._session.execute(
            select(AiKeyCheckResultRow).where(AiKeyCheckResultRow.key_id == key_id)
        )
        row = q.scalar_one_or_none()
        if row is None:
            return []
        models = row.models
        if isinstance(models, str):
            try:
                import json

                models = json.loads(models)
            except Exception:
                return []
        if not isinstance(models, list):
            return []
        return [str(m).strip() for m in models if m]

    @staticmethod
    def _aliases_list(aliases: object) -> list[str]:
        if isinstance(aliases, str):
            try:
                import json

                aliases = json.loads(aliases)
            except Exception:
                return []
        if not isinstance(aliases, list):
            return []
        return [str(a) for a in aliases if a]

    async def _model_public(self, row: AiModelRow) -> dict[str, Any]:
        aliases = row.key_aliases
        if isinstance(aliases, str):
            try:
                aliases = json.loads(aliases)
            except Exception:
                aliases = []
        aliases_list = aliases if isinstance(aliases, list) else []
        return {
            "id": row.id,
            "name": row.name,
            # Provider model ids (stable keys like "claude-opus-5"); multiple
            # equivalent variants allowed. Renamed from "key_aliases" in the
            # API/UI; the DB column stays "key_aliases" (no rename migration).
            "model_ids": [str(a) for a in aliases_list if a],
            "provider": row.provider,
            "reasoning_level": row.reasoning_level,
            "description": row.description,
            "owner_scope": row.owner_scope,
            "owner_company_id": row.owner_company_id,
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

    async def _require_model(self, model_id: str, company_id: str | None) -> AiModelRow:
        row = await self._get_row(model_id)
        # company_id=None means platform admin — may touch any model.
        if company_id is not None and row.owner_scope == "company" and row.owner_company_id != company_id:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="model not in company scope")
        return row

    async def _get_row(self, model_id: str) -> AiModelRow:
        row = await self._session.get(AiModelRow, model_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="model not found")
        return row

    async def _require_company_model(self, model_id: str, company_id: str) -> AiModelRow:
        return await self._require_model(model_id, company_id)

    # ---- metrics events (Kafka-first, dual-write post-commit) ----

    def _emit_model_created_event(self, row: AiModelRow) -> None:
        from prodavan.application.ai_models.metrics import schedule_model_created_event

        schedule_model_created_event(
            self._session,
            model_id=row.id,
            name=row.name,
            provider=row.provider,
            key_aliases=row.key_aliases if isinstance(row.key_aliases, list) else [],
            owner_scope=row.owner_scope,
            owner_company_id=row.owner_company_id,
        )

    def _emit_model_updated_event(self, row: AiModelRow) -> None:
        from prodavan.application.ai_models.metrics import schedule_model_updated_event

        schedule_model_updated_event(
            self._session,
            model_id=row.id,
            name=row.name,
            provider=row.provider,
            owner_scope=row.owner_scope,
            owner_company_id=row.owner_company_id,
        )

    def _emit_model_deleted_event(self, model_id: str, name: str) -> None:
        from prodavan.application.ai_models.metrics import schedule_model_deleted_event

        schedule_model_deleted_event(self._session, model_id=model_id, name=name)

    async def require_company_key(self, key_id: str, company_id: str) -> AiProviderKeyRow:
        return await self._require_company_key(key_id, company_id)

    async def _require_company_key(self, key_id: str, company_id: str) -> AiProviderKeyRow:
        row = await self._session.get(AiProviderKeyRow, key_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="AI key not found")
        if row.owner_scope == "company" and row.owner_company_id != company_id:
            raise AppError(code="FORBIDDEN", title="Forbidden", status=403, detail="AI key not in company scope")
        return row
