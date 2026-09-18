"""AI Provider Key probe (verification) domain types (L03).

Probe = a short request to the provider's API to verify that the stored
secret actually works. Never throws — failures are returned as a result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class ProbeStatus(StrEnum):
    """Outcome of a key probe."""

    OK = "ok"
    ERROR = "error"
    UNAVAILABLE = "unavailable"  # infra problem (timeout / network / unknown host)


class ProbeKind(StrEnum):
    """What kind of probe was attempted."""

    MODELS = "models"  # GET /models (preferred — no tokens spent)
    CHAT = "chat"  # POST /chat/completions with 1 token (fallback when /models unavailable)


@dataclass(frozen=True, slots=True)
class ProbeResult:
    """Immutable probe outcome — stored as last check + returned to UI."""

    status: ProbeStatus
    kind: ProbeKind | None = None
    latency_ms: int | None = None
    models: list[str] = field(default_factory=list)
    default_model: str | None = None
    http_status: int | None = None
    error_code: str | None = None
    error_message: str | None = None
    provider: str | None = None
    api_kind: str | None = None
    model: str | None = None  # set when probing a specific model (probe_model)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value if isinstance(self.status, ProbeStatus) else str(self.status),
            "kind": self.kind.value if isinstance(self.kind, ProbeKind) else (self.kind if self.kind else None),
            "latency_ms": self.latency_ms,
            "models": list(self.models),
            "default_model": self.default_model,
            "http_status": self.http_status,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "provider": self.provider,
            "api_kind": self.api_kind,
            "model": self.model,
        }


# api_kinds that can be probed directly via HTTP. Excludes cli_subscription
# (no HTTP endpoint to verify — it's a CLI login, not an API token).
HTTP_PROBE_API_KINDS: frozenset[str] = frozenset(
    {
        "openai_api",
        "anthropic_api",
        "openrouter",
        "custom",
        "cursor_sdk",
        "codex_sdk",
        "claude_agent_sdk",
    }
)


def is_http_probe_kind(api_kind: str | None) -> bool:
    return bool(api_kind) and api_kind in HTTP_PROBE_API_KINDS
