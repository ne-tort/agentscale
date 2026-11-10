"""Celery + Redis implementation of the typed JobQueue boundary."""
from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable, Mapping
from typing import Any

from prodavan.application.plugins.capabilities.adapters._shared import canonical_digest
from prodavan.application.plugins.capabilities.contracts import empty_digest


def _job_key(identity: Mapping[str, str]) -> str:
    parts = (identity["company_id"], identity["plugin_instance_id"], identity["job_id"])
    canonical = "".join(f"{len(part)}:{part}" for part in parts)
    return "plugin:jobs:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _dump(record: Mapping[str, Any]) -> str:
    return json.dumps(record, sort_keys=True, separators=(",", ":"), default=str)


class RedisCeleryJobQueue:
    def __init__(
        self,
        *,
        redis: Any,
        worker_provider: Callable[[], Any],
        celery_provider: Callable[[], Any],
        lease_seconds: int = 300,
        clock: Callable[[], float] = time.time,
    ) -> None:
        if redis is None or not all(
            callable(getattr(redis, method, None)) for method in ("get", "set")
        ):
            raise ValueError("job queue requires a real Redis client")
        if not callable(worker_provider) or not callable(celery_provider):
            raise ValueError("job queue requires worker and Celery providers")
        if not 30 <= lease_seconds <= 3600:
            raise ValueError("job lease TTL is invalid")
        self._redis = redis
        self._worker_provider = worker_provider
        self._celery_provider = celery_provider
        self._lease_seconds = lease_seconds
        self._clock = clock

    @staticmethod
    def _validate(identity: Mapping[str, str], *, submission: bool = False) -> None:
        required = [
            "company_id", "plugin_id", "plugin_instance_id", "grant_id", "run_id", "job_id",
        ]
        if submission:
            required.extend(("task_name", "arguments_digest"))
        for name in required:
            value = identity.get(name)
            if (
                not isinstance(value, str) or not value or len(value) > 2048
                or any(ord(char) < 32 for char in value)
            ):
                raise ValueError("job identity is invalid")

    async def _read(self, identity: Mapping[str, str]) -> dict[str, Any] | None:
        raw = await self._redis.get(_job_key(identity))
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        try:
            record = json.loads(raw)
        except (TypeError, ValueError):
            raise RuntimeError("job state is corrupt") from None
        if not isinstance(record, dict):
            raise RuntimeError("job state is invalid")
        for name in ("company_id", "plugin_id", "plugin_instance_id", "grant_id", "job_id", "run_id"):
            if record.get(name) != identity[name]:
                raise PermissionError("job tenant binding mismatch")
        return record

    async def _write(self, identity: Mapping[str, str], record: Mapping[str, Any], *, nx: bool = False) -> bool:
        result = await self._redis.set(_job_key(identity), _dump(record), nx=nx or None)
        if not isinstance(result, (bool, int)):
            raise RuntimeError("job state write was not confirmed")
        return bool(result)

    def _new_record(self, identity: Mapping[str, str]) -> dict[str, Any]:
        raw = f"{identity['job_id']}|{identity['run_id']}"
        lease_id = "lease_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()
        return {
            **dict(identity),
            "state": "pending",
            "result_digest": empty_digest(),
            "lease": {
                "lease_id": lease_id,
                "lease_owner": f"celery:{identity['plugin_instance_id']}",
                "expires_at_epoch": int(self._clock()) + self._lease_seconds,
            },
            "submission_uncertain": False,
        }

    async def submit(self, *, identity: Mapping[str, str]) -> Mapping[str, Any]:
        self._validate(identity, submission=True)
        existing = await self._read(identity)
        if existing is not None:
            for name in ("task_name", "run_id", "arguments_digest", "grant_id"):
                if existing.get(name) != identity[name]:
                    raise PermissionError("job idempotency binding mismatch")
            return existing
        record = self._new_record(identity)
        created = await self._write(identity, record, nx=True)
        if not created:
            raced = await self._read(identity)
            if raced is None:
                raise RuntimeError("job idempotency race could not be read")
            return raced
        worker = self._worker_provider()
        try:
            result = worker.send_task(
                identity["task_name"],
                kwargs={
                    "company_id": identity["company_id"],
                    "plugin_id": identity["plugin_id"],
                    "plugin_instance_id": identity["plugin_instance_id"],
                    "grant_id": identity["grant_id"],
                    "run_id": identity["run_id"],
                    "arguments_digest": identity["arguments_digest"],
                },
                task_id=identity["job_id"],
            )
        except Exception:
            record["submission_uncertain"] = True
            await self._write(identity, record)
            raise RuntimeError("job submission outcome is unknown") from None
        result_id = getattr(result, "id", getattr(result, "task_id", None))
        if result_id != identity["job_id"]:
            record["submission_uncertain"] = True
            await self._write(identity, record)
            raise RuntimeError("Celery returned a mismatched task id")
        record["broker_task_id"] = result_id
        await self._write(identity, record)
        return record

    def _result(self, job_id: str) -> Any:
        app = self._celery_provider()
        factory = getattr(app, "AsyncResult", None)
        if not callable(factory):
            raise RuntimeError("Celery result backend is unavailable")
        return factory(job_id)

    @staticmethod
    def _state(raw: Any) -> str:
        return {
            "PENDING": "pending", "RECEIVED": "pending", "STARTED": "running",
            "RETRY": "running", "SUCCESS": "completed", "FAILURE": "failed",
            "REVOKED": "cancelled",
        }.get(str(raw or "PENDING").upper(), "pending")

    async def get(self, *, identity: Mapping[str, str]) -> Mapping[str, Any]:
        self._validate(identity)
        record = await self._read(identity)
        if record is None:
            raise LookupError("job not found")
        result = self._result(identity["job_id"])
        record["state"] = self._state(getattr(result, "status", None))
        if record["state"] == "completed":
            value = getattr(result, "result", None)
            record["result_digest"] = (
                value if isinstance(value, str) and value.startswith("sha256:")
                else canonical_digest(value) if value is not None else empty_digest()
            )
        await self._write(identity, record)
        return record

    async def cancel(self, *, identity: Mapping[str, str]) -> Mapping[str, Any]:
        self._validate(identity)
        record = await self._read(identity)
        if record is None:
            raise LookupError("job not found")
        lease = record.get("lease") or {}
        if not identity.get("lease_id") or lease.get("lease_id") != identity["lease_id"]:
            raise PermissionError("job lease mismatch")
        if record.get("state") in ("completed", "failed", "cancelled"):
            return record
        app = self._celery_provider()
        revoke = getattr(getattr(app, "control", None), "revoke", None)
        if not callable(revoke):
            raise RuntimeError("Celery revoke is unavailable")
        revoke(identity["job_id"], terminate=False)
        record["state"] = "cancelled"
        record["result_digest"] = empty_digest()
        await self._write(identity, record)
        return record


__all__ = ["RedisCeleryJobQueue"]
