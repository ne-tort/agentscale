"""AI key probe application service (PROBE-P1).

Verifies an AI key by performing a short HTTP request to the provider's API
(GET /models, fallback: 1-token chat completion). Stores the last probe
result in `ai_key_check_results` and publishes a metrics event to Kafka.

Reliability rules:
- never raises to the caller — returns ProbeResult (ok/error/unavailable)
- does not mutate the key record itself (status is managed by AiKeysService)
- measures latency (ms) for the HTTP roundtrip
- handles unknown provider / cli_subscription / missing secret as explicit
  error codes (not exceptions)
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from prodavan.application.ai_keys.probe.http_probe import HttpProbeClient
from prodavan.application.ai_keys.probe.provider_resolver import ProviderResolver
from prodavan.domain.ai_keys import ApiKind, ProbeKind, ProbeResult, ProbeStatus, is_http_probe_kind
from prodavan.domain.errors import AppError
from prodavan.domain.identity import Principal
from prodavan.infrastructure.persistence.models.ai_keys import (
    AiKeyCheckResultRow,
    AiProviderKeyRow,
)
from prodavan.infrastructure.secrets.store import SecretStore, get_secret_store

logger = logging.getLogger(__name__)


class AiKeyProbeService:
    """Probe an AI key: verify the secret works against the provider API."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        secrets: SecretStore | None = None,
        probe_client: HttpProbeClient | None = None,
    ) -> None:
        self._session = session
        self._secrets = secrets or get_secret_store()
        self._probe = probe_client or HttpProbeClient()
        self._resolver = ProviderResolver(session)

    async def probe_key(
        self,
        key_id: str,
        *,
        principal: Principal | None = None,
    ) -> dict[str, Any]:
        """Probe a key and persist the result. Returns the result dict."""
        row = await self._get_row(key_id)
        result = await self._probe_row(row)
        await self._persist_result(row.id, result, principal=principal)
        await self._emit_metrics(row, result)
        return result.to_dict()

    async def get_last_result(self, key_id: str) -> dict[str, Any] | None:
        """Return the last stored probe result for a key (or None)."""
        q = await self._session.execute(select(AiKeyCheckResultRow).where(AiKeyCheckResultRow.key_id == key_id))
        row = q.scalar_one_or_none()
        if row is None:
            return None
        return self._row_to_public(row)

    async def _probe_row(self, row: AiProviderKeyRow) -> ProbeResult:
        # cli_subscription — no HTTP endpoint to verify (CLI login, not a token).
        if row.api_kind == ApiKind.CLI_SUBSCRIPTION:
            return ProbeResult(
                status=ProbeStatus.UNAVAILABLE,
                error_code="CLI_SUBSCRIPTION_NOT_PROBEABLE",
                error_message="CLI subscription keys cannot be probed via HTTP",
                provider=row.provider,
                api_kind=row.api_kind,
            )
        if not (row.secret_ref or "").strip():
            return ProbeResult(
                status=ProbeStatus.ERROR,
                error_code="NO_SECRET",
                error_message="key has no secret stored",
                provider=row.provider,
                api_kind=row.api_kind,
            )
        if not is_http_probe_kind(row.api_kind):
            return ProbeResult(
                status=ProbeStatus.UNAVAILABLE,
                error_code="PROBE_UNSUPPORTED_KIND",
                error_message=f"api_kind '{row.api_kind}' is not HTTP-probeable",
                provider=row.provider,
                api_kind=row.api_kind,
            )

        try:
            secret = self._secrets.get(row.secret_ref)
        except Exception as exc:
            logger.warning("probe: secret fetch failed key=%s: %s", row.id, exc)
            return ProbeResult(
                status=ProbeStatus.UNAVAILABLE,
                error_code="SECRET_STORE_ERROR",
                error_message=_trim(str(exc)),
                provider=row.provider,
                api_kind=row.api_kind,
            )
        if not (secret or "").strip():
            return ProbeResult(
                status=ProbeStatus.ERROR,
                error_code="NO_SECRET",
                error_message="stored secret is empty",
                provider=row.provider,
                api_kind=row.api_kind,
            )

        endpoint = await self._resolver.resolve(api_kind=row.api_kind, provider=row.provider)
        if endpoint is None:
            return ProbeResult(
                status=ProbeStatus.UNAVAILABLE,
                error_code="NO_PROVIDER_ENDPOINT",
                error_message=(
                    f"no HTTP provider endpoint registered for api_kind={row.api_kind} provider={row.provider}"
                ),
                provider=row.provider,
                api_kind=row.api_kind,
            )

        try:
            return await self._probe.probe(endpoint, secret)
        except Exception as exc:
            # Should never happen — HttpProbeClient catches all — but guard.
            logger.exception("probe: unexpected failure key=%s", row.id)
            return ProbeResult(
                status=ProbeStatus.UNAVAILABLE,
                error_code="PROBE_INTERNAL",
                error_message=_trim(str(exc)),
                provider=row.provider,
                api_kind=row.api_kind,
            )

    async def _persist_result(
        self,
        key_id: str,
        result: ProbeResult,
        *,
        principal: Principal | None,
    ) -> None:
        """Upsert the last probe result (1:1 with key)."""
        import json

        values = {
            "status": result.status.value if isinstance(result.status, ProbeStatus) else str(result.status),
            "kind": result.kind.value if isinstance(result.kind, ProbeKind) else result.kind,
            "latency_ms": result.latency_ms,
            "models": result.models,
            "default_model": result.default_model,
            "http_status": result.http_status,
            "error_code": result.error_code,
            "error_message": result.error_message,
            "provider": result.provider,
            "api_kind": result.api_kind,
            "checked_at": datetime.now(UTC),
            "checked_by": principal.sub if principal else None,
        }
        stmt = pg_insert(AiKeyCheckResultRow)
        stmt = stmt.on_conflict_do_update(
            index_elements=["key_id"],
            set_={
                "status": stmt.excluded.status,
                "kind": stmt.excluded.kind,
                "latency_ms": stmt.excluded.latency_ms,
                "models": stmt.excluded.models,
                "default_model": stmt.excluded.default_model,
                "http_status": stmt.excluded.http_status,
                "error_code": stmt.excluded.error_code,
                "error_message": stmt.excluded.error_message,
                "provider": stmt.excluded.provider,
                "api_kind": stmt.excluded.api_kind,
                "checked_at": stmt.excluded.checked_at,
                "checked_by": stmt.excluded.checked_by,
            },
        )
        # models JSONB needs explicit cast for psycopg3 when empty list.
        await self._session.execute(
            stmt,
            {**values, "models": json.dumps(values["models"], ensure_ascii=False)},
        )
        await self._session.commit()

    async def _emit_metrics(self, row: AiProviderKeyRow, result: ProbeResult) -> None:
        """Publish a metrics event for the probe (Kafka-first, no-op without Kafka)."""
        try:
            from prodavan.application.ai_keys.metrics import schedule_key_probe_event

            schedule_key_probe_event(
                self._session,
                key_id=row.id,
                provider=row.provider,
                api_kind=row.api_kind,
                result=result,
            )
        except Exception:
            logger.exception("probe: metrics publish failed key=%s", row.id)

    async def _get_row(self, key_id: str) -> AiProviderKeyRow:
        row = await self._session.get(AiProviderKeyRow, key_id)
        if row is None:
            raise AppError(code="NOT_FOUND", title="Not Found", status=404, detail="AI key not found")
        return row

    def _row_to_public(self, row: AiKeyCheckResultRow) -> dict[str, Any]:
        models = row.models
        if isinstance(models, str):
            try:
                import json

                models = json.loads(models)
            except Exception:
                models = []
        models_list = models if isinstance(models, list) else []
        return {
            "status": row.status,
            "kind": row.kind,
            "latency_ms": int(row.latency_ms) if row.latency_ms is not None else None,
            "models": models_list,
            "default_model": row.default_model,
            "http_status": int(row.http_status) if row.http_status is not None else None,
            "error_code": row.error_code,
            "error_message": row.error_message,
            "provider": row.provider,
            "api_kind": row.api_kind,
            "checked_at": row.checked_at.isoformat() if row.checked_at else None,
            "checked_by": row.checked_by,
        }


def _trim(msg: str) -> str:
    msg = (msg or "").strip()
    if len(msg) > 500:
        msg = msg[:500] + "…"
    return msg
