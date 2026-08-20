"""S4B HTTP gateway port. Implementations must not invent offers."""

from __future__ import annotations

from typing import Any, Protocol


class S4BGateway(Protocol):
    def ping(self, username: str, password: str) -> dict[str, Any]:
        """Auth/upstream check. ok=True only when S4B accepted the credentials."""

    def search_by_part_numbers(
        self,
        username: str,
        password: str,
        part_numbers: list[str],
    ) -> dict[str, Any]:
        """Live search via sr=. Returns {ok, items?} without fabricating prices."""
