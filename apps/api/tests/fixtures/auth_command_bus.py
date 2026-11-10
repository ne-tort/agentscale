"""Explicit test doubles for the no-broker auth command security lifecycle."""

from __future__ import annotations

TEST_AUTH_COMMAND_SECRET = "test-only-auth-command-signing-key-0123456789abcdef"
TEST_AUTH_EVENT_SECRET = "test-only-auth-event-signing-key-9876543210fedcba"


class FakeCredentialRedis:
    """Async SET NX EX / GETDEL semantics with controllable expiry."""

    def __init__(self) -> None:
        self.now = 0.0
        self.values: dict[str, str] = {}
        self.expires_at: dict[str, float] = {}
        self.getdel_calls = 0

    def _purge(self, key: str) -> None:
        deadline = self.expires_at.get(key)
        if deadline is not None and deadline <= self.now:
            self.values.pop(key, None)
            self.expires_at.pop(key, None)

    def advance(self, seconds: float) -> None:
        self.now += seconds

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        self._purge(key)
        if nx and key in self.values:
            return False
        self.values[key] = value
        if ex is not None:
            self.expires_at[key] = self.now + ex
        else:
            self.expires_at.pop(key, None)
        return True

    async def getdel(self, key: str) -> str | None:
        self.getdel_calls += 1
        self._purge(key)
        self.expires_at.pop(key, None)
        return self.values.pop(key, None)

    async def delete(self, key: str) -> int:
        self._purge(key)
        self.expires_at.pop(key, None)
        return int(self.values.pop(key, None) is not None)


class FakeCredentialRedisManager:
    enabled = True

    def __init__(self, client: FakeCredentialRedis) -> None:
        self.client = client
