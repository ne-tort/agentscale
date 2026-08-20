"""Shared Pydantic helpers for request DTOs."""

from typing import Annotated, Any

from pydantic import BeforeValidator, EmailStr


def _empty_str_to_none(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


OptionalEmail = Annotated[EmailStr | None, BeforeValidator(_empty_str_to_none)]
