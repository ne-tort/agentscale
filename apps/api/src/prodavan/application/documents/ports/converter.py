"""DocumentConverterPort — office conversion behind the Documents module."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(slots=True)
class ConvertedDocument:
    """Single conversion result: bytes + resolved target mime/filename."""

    data: bytes
    mime: str
    filename: str


class DocumentConverterPort(Protocol):
    """Conversion backend contract (Gotenberg / local in-proc adapter)."""

    async def convert(self, data: bytes, filename: str, target_format: str) -> ConvertedDocument: ...

    async def ping(self) -> bool: ...
