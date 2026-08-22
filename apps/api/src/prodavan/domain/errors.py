"""Stable domain / application errors mapped to problem+json."""

from __future__ import annotations


class AppError(Exception):
    """Raise from application/domain; handlers emit RFC 7807 problem+json."""

    def __init__(
        self,
        *,
        code: str,
        title: str,
        status: int = 400,
        detail: str | None = None,
    ) -> None:
        self.code = code
        self.title = title
        self.status = status
        self.detail = detail
        super().__init__(detail or title)
