"""Admin AI model catalog CRUD API (MODELS-L1).

Platform-admin surface for the model catalog: list/get/create/patch/delete
any model (platform or company). The model entity is the catalog entry; most
fields are optional, `name` is required.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from fastapi import APIRouter
from pydantic import BaseModel, Field

from prodavan.api.deps import PlatformAdminDep, SessionDep
from prodavan.application.ai_models.service import AiModelsService

router = APIRouter(prefix="/admin/ai-models", tags=["ai-models"])


class CreateModelBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str = Field(min_length=1, max_length=128)
    model_ids: list[str] = Field(default_factory=list)
    provider: str | None = Field(default=None, max_length=64)
    reasoning_level: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=512)
    input_price_usd_per_mtok: Decimal | None = None
    output_price_usd_per_mtok: Decimal | None = None
    max_context_tokens: int | None = Field(default=None, ge=1)
    publisher: str | None = Field(default=None, max_length=128)
    released_at: date | None = None
    owner_scope: str = Field(default="platform", pattern="^(platform|company)$")
    owner_company_id: str | None = None


class PatchModelBody(BaseModel):
    model_config = {"extra": "forbid"}

    name: str | None = Field(default=None, min_length=1, max_length=128)
    model_ids: list[str] | None = None
    provider: str | None = Field(default=None, max_length=64)
    reasoning_level: str | None = Field(default=None, max_length=32)
    description: str | None = Field(default=None, max_length=512)
    input_price_usd_per_mtok: Decimal | None = None
    output_price_usd_per_mtok: Decimal | None = None
    max_context_tokens: int | None = Field(default=None, ge=1)
    publisher: str | None = Field(default=None, max_length=128)
    released_at: date | None = None


@router.get("")
async def list_models(_admin: PlatformAdminDep, session: SessionDep) -> list[dict]:
    return await AiModelsService(session).list_models()


@router.post("", status_code=201)
async def create_model(admin: PlatformAdminDep, session: SessionDep, body: CreateModelBody) -> dict:
    return await AiModelsService(session).create_model(
        name=body.name,
        key_aliases=body.model_ids,
        provider=body.provider,
        reasoning_level=body.reasoning_level,
        description=body.description,
        input_price_usd_per_mtok=body.input_price_usd_per_mtok,
        output_price_usd_per_mtok=body.output_price_usd_per_mtok,
        max_context_tokens=body.max_context_tokens,
        publisher=body.publisher,
        released_at=body.released_at,
        owner_scope=body.owner_scope,
        owner_company_id=body.owner_company_id,
    )


@router.get("/{model_id}")
async def get_model(_admin: PlatformAdminDep, session: SessionDep, model_id: str) -> dict:
    return await AiModelsService(session).get_model(model_id)


@router.patch("/{model_id}")
async def patch_model(admin: PlatformAdminDep, session: SessionDep, model_id: str, body: PatchModelBody) -> dict:
    return await AiModelsService(session).update_model(
        model_id=model_id,
        name=body.name,
        key_aliases=body.model_ids,
        provider=body.provider,
        reasoning_level=body.reasoning_level,
        description=body.description,
        input_price_usd_per_mtok=body.input_price_usd_per_mtok,
        output_price_usd_per_mtok=body.output_price_usd_per_mtok,
        max_context_tokens=body.max_context_tokens,
        publisher=body.publisher,
        released_at=body.released_at,
    )


@router.delete("/{model_id}", status_code=204)
async def delete_model(admin: PlatformAdminDep, session: SessionDep, model_id: str) -> None:
    await AiModelsService(session).delete_model(model_id=model_id)
