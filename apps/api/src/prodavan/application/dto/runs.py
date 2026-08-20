"""Spec / KP run DTOs."""

from pydantic import BaseModel, Field


class CreateRunRequest(BaseModel):
    input_filename: str = Field(min_length=1)


class AdvanceRunRequest(BaseModel):
    target_phase: str | None = None
    force: bool = False


class FinalizeRunRequest(BaseModel):
    confirmed: bool = False
    operator_note: str | None = None
