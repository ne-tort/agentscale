"""MinIO/STS objects capability with exact tenant and plugin IAM scope."""
from __future__ import annotations

import asyncio
import json
import re
import time
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from prodavan.application.plugins.capabilities.contracts import (
    ObjectPermission, ObjectPresignMethod, ObjectRef, ObjectsDeleteRequest,
    ObjectsDeleteResponse, ObjectsListRequest, ObjectsListResponse,
    ObjectsPresignRequest, ObjectsPresignResponse, ObjectsScopedCredentialsRequest,
    ObjectsScopedCredentialsResponse, ScopedCredentials,
)
from prodavan.application.plugins.objects import policy as iam
from prodavan.application.plugins.objects import ports as object_ports
from ._shared import (
    CloseOnce, CapabilityAccessDenied, CapabilityBackendError,
    CapabilityConfigurationError, await_io, canonical_digest, canonical_json,
    digest_text, plugin_id, request_envelope, thread_io, usage,
)


class MinioObjectsAdapter(CloseOnce):
    def __init__(
        self,
        *,
        buckets: Any,
        sts: Any,
        secret_store: Any,
        presigner: Any = None,
        clock: Any = time,
        object_bytes_reservation: int = 50 * 1024 * 1024,
        timeout_seconds: float = 8.0,
        owns_resources: bool = False,
        owns_secret_store: bool = False,
    ) -> None:
        owned = (buckets, sts, presigner) if owns_resources else ()
        if owns_secret_store:
            owned = (*owned, secret_store)
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=owned,
        )
        for method in ("list_prefix_page", "delete_object", "content_sha256"):
            if not callable(getattr(buckets, method, None)):
                raise CapabilityConfigurationError("minio_bucket_capability_contract_required")
        if sts is None or not callable(getattr(sts, "assume_role", None)):
            raise CapabilityConfigurationError("minio_sts_required_no_presigned_fallback")
        if secret_store is None or not all(
            callable(getattr(secret_store, method, None)) for method in ("get", "put")
        ):
            raise CapabilityConfigurationError("objects_credentials_secret_store_required")
        if not 1 <= object_bytes_reservation <= 1024 * 1024 * 1024:
            raise CapabilityConfigurationError("objects_reservation_cap_invalid")
        self._buckets = buckets
        self._sts = sts
        self._presigner = presigner
        self._secrets = secret_store
        self._clock = clock
        self._object_bytes_reservation = object_bytes_reservation

    @staticmethod
    def _scope(request: Any) -> tuple[str, str, str, str]:
        envelope = request.envelope
        company = envelope.company_id
        plugin = plugin_id(envelope)
        bucket = iam.bucket_name(company)
        prefix = iam.module_prefix(plugin)
        if request.bucket != bucket:
            raise CapabilityAccessDenied("objects_bucket_tenant_mismatch")
        return company, plugin, bucket, prefix

    @staticmethod
    def _policy(company: str, plugin: str, permissions: frozenset[ObjectPermission]) -> dict[str, Any]:
        actions: set[str] = set()
        if ObjectPermission.READ in permissions:
            actions.update({"s3:GetObject", "s3:ListBucket"})
        if ObjectPermission.WRITE in permissions:
            actions.add("s3:PutObject")
        if ObjectPermission.DELETE in permissions:
            actions.add("s3:DeleteObject")
        statements: list[dict[str, Any]] = []
        for raw in iam.session_policy(company, plugin)["Statement"]:
            statement = dict(raw)
            allowed = [item for item in raw.get("Action", []) if item in actions]
            if allowed:
                statement["Action"] = allowed
                statements.append(statement)
        if not statements:
            raise CapabilityAccessDenied("objects_empty_session_policy")
        return {"Version": iam.POLICY_VERSION, "Statement": statements}

    @staticmethod
    def _credential_key(envelope: Any) -> str:
        return (
            "plugins/capabilities/objects/"
            f"{quote(envelope.company_id, safe='')}/"
            f"{quote(plugin_id(envelope), safe='')}/"
            f"{quote(envelope.plugin_instance_id, safe='')}/"
            f"{quote(envelope.request_id, safe='')}"
        )

    async def _read_secret(self, secret_ref: str) -> str | None:
        try:
            async with asyncio.timeout(self._timeout_seconds):
                value = await asyncio.to_thread(self._secrets.get, secret_ref)
        except Exception as exc:
            if getattr(exc, "code", "") in {"SECRET_NOT_FOUND", "SECRET_BACKEND_UNSUPPORTED"}:
                return None
            raise CapabilityBackendError("objects_credentials_read_failed") from None
        if not isinstance(value, str):
            raise CapabilityBackendError("objects_credentials_invalid_secret")
        return value

    def _credential_response(
        self, *, envelope: Any, ref: str, record: dict[str, Any], policy: dict[str, Any]
    ) -> ObjectsScopedCredentialsResponse:
        credentials = ScopedCredentials(
            credentials_ref=ref,
            bucket=record["bucket"],
            key_prefix=record["key_prefix"],
            expires_at_epoch=int(record["expires_at_epoch"]),
            policy_digest=canonical_digest(policy),
        )
        return ObjectsScopedCredentialsResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "credentials_ref": ref,
                "bucket": credentials.bucket,
                "key_prefix": credentials.key_prefix,
                "expires_at_epoch": credentials.expires_at_epoch,
                "policy_digest": credentials.policy_digest,
            }),
            credentials=credentials,
        )

    def _require_scoped_credentials_wired(self) -> None:
        # A prefix-wide credential request cannot be emulated by a per-key/method
        # presigned URL. Every StsPort, including an override, must explicitly
        # expose a verified gate; absence and non-boolean values stay unwired.
        if getattr(self._sts, "scoped_credentials_wired", False) is not True:
            raise CapabilityBackendError("objects_scoped_credentials_sts_unwired")

    async def scoped_credentials(
        self, request: ObjectsScopedCredentialsRequest
    ) -> ObjectsScopedCredentialsResponse:
        envelope = request_envelope(request, ObjectsScopedCredentialsRequest)
        self._ensure_open()
        company, plugin, bucket, prefix = self._scope(request)
        if request.key_prefix != prefix:
            raise CapabilityAccessDenied("objects_prefix_plugin_mismatch")
        if request.ttl_seconds < object_ports.MIN_STS_TTL_SECONDS:
            raise CapabilityAccessDenied("objects_credentials_ttl_too_short")
        policy = self._policy(company, plugin, request.permissions)
        self._require_scoped_credentials_wired()
        key = self._credential_key(envelope)
        refs = (f"vault://ai_keys/{key}", f"file://ai_keys/{key}.secret")
        for ref in refs:
            raw = await self._read_secret(ref)
            if raw is None:
                continue
            try:
                record = json.loads(raw)
            except (TypeError, ValueError):
                raise CapabilityBackendError("objects_credentials_invalid_record") from None
            expected = {
                "company_id": company,
                "plugin_id": plugin,
                "plugin_instance_id": envelope.plugin_instance_id,
                "request_id": envelope.request_id,
                "permissions": sorted(item.value for item in request.permissions),
                "policy": policy,
                "bucket": bucket,
                "key_prefix": prefix,
            }
            if any(record.get(name) != value for name, value in expected.items()):
                raise CapabilityAccessDenied("objects_credentials_binding_mismatch")
            if int(record.get("expires_at_epoch", 0)) <= int(self._clock.time()):
                raise CapabilityBackendError("objects_credentials_expired")
            stored = record.get("credentials") or {}
            if not all(isinstance(stored.get(name), str) and stored[name] for name in (
                "access_key", "secret_key", "session_token"
            )):
                raise CapabilityBackendError("objects_credentials_invalid_record")
            return self._credential_response(
                envelope=envelope, ref=ref, record=record, policy=policy
            )
        issued = await await_io(
            self._sts.assume_role(
                company_id=company,
                plugin_id=plugin,
                ttl_seconds=request.ttl_seconds,
                session_policy=policy,
            ),
            timeout=self._timeout_seconds,
            code="minio_sts_assume_role_failed",
        )
        if not isinstance(issued, object_ports.ScopedCredentials):
            raise CapabilityBackendError("minio_sts_invalid_credentials")
        expires = int(issued.expires_at)
        now = int(self._clock.time())
        if (
            issued.bucket != bucket or issued.prefix != prefix or issued.policy != policy
            or expires <= now or expires > now + request.ttl_seconds + 1
            or not all((issued.access_key, issued.secret_key, issued.session_token))
        ):
            raise CapabilityBackendError("minio_sts_scope_or_ttl_mismatch")
        record = {
            "company_id": company,
            "plugin_id": plugin,
            "plugin_instance_id": envelope.plugin_instance_id,
            "request_id": envelope.request_id,
            "permissions": sorted(item.value for item in request.permissions),
            "policy": policy,
            "bucket": bucket,
            "key_prefix": prefix,
            "expires_at_epoch": expires,
            "credentials": {
                "access_key": issued.access_key,
                "secret_key": issued.secret_key,
                "session_token": issued.session_token,
            },
        }
        secret_ref = await thread_io(
            self._secrets.put,
            key,
            canonical_json(record).decode("utf-8"),
            timeout=self._timeout_seconds,
            code="objects_credentials_store_failed",
        )
        if secret_ref not in refs:
            raise CapabilityBackendError("objects_credentials_ref_mismatch")
        return self._credential_response(
            envelope=envelope, ref=secret_ref, record=record, policy=policy
        )

    @staticmethod
    def _exact_presign_key(key: str, prefix: str) -> str:
        """Return an unchanged exact key only after raw/encoded scope checks."""
        candidates = [key]
        decoded = key
        for _ in range(3):
            decoded = unquote(decoded)
            if decoded == candidates[-1]:
                break
            candidates.append(decoded)
        for candidate in candidates:
            tail = candidate[len(prefix):] if candidate.startswith(prefix) else ""
            if (
                not candidate.startswith(prefix) or not tail or candidate.endswith("/")
                or candidate != candidate.strip() or candidate.startswith("/")
                or "\\" in candidate or "//" in candidate or "://" in candidate
                or ".." in candidate or any(char in candidate for char in ("*", "?"))
                or any(segment in ("", ".", "..") for segment in candidate.split("/"))
                or any(ord(char) < 32 or ord(char) == 127 for char in candidate)
                or tail.startswith("module-") or tail.lower().startswith("tenant-")
            ):
                raise CapabilityAccessDenied("objects_key_outside_plugin_scope")
        return key

    @staticmethod
    def _validate_presigned_url(url: Any, ttl_seconds: int, bucket: str, key: str) -> str:
        """Accept only an HTTP(S) signed URL with the exact requested expiry."""
        if (
            not isinstance(url, str) or not url or len(url) > 16384
            or any(ord(char) < 32 or ord(char) == 127 for char in url)
        ):
            raise CapabilityBackendError("minio_presign_invalid_url")
        try:
            parsed = urlsplit(url)
            query = parse_qs(parsed.query, keep_blank_values=True, strict_parsing=True)
        except ValueError:
            raise CapabilityBackendError("minio_presign_invalid_url") from None
        if (
            parsed.scheme not in ("http", "https") or not parsed.netloc
            or parsed.username is not None or parsed.password is not None or parsed.fragment
        ):
            raise CapabilityBackendError("minio_presign_invalid_url")
        decoded_path = unquote(parsed.path)
        path_style = decoded_path == f"/{bucket}/{key}"
        try:
            hostname = parsed.hostname
            virtual_hosted = bool(
                hostname and hostname.startswith(f"{bucket}.") and decoded_path == f"/{key}"
            )
        except ValueError:
            virtual_hosted = False
        if not path_style and not virtual_hosted:
            raise CapabilityBackendError("minio_presign_scope_mismatch")
        # Keep the leak-specific failure as defense in depth, but do not use a
        # denylist to decide whether a query is a valid signature.
        forbidden = {
            "password", "secret", "secret_key", "secretaccesskey",
            "aws_secret_access_key", "access_key", "accesskeyid", "sessiontoken",
            "x_amz_secret_key", "awsaccesskeyid", "x_amz_security_token",
        }
        if any(name.lower().replace("-", "_") in forbidden for name in query):
            raise CapabilityBackendError("minio_presign_credential_leak")

        # Params={"Bucket": ..., "Key": ...} produces static-credential SigV4
        # query authentication with exactly these six fields. Unknown names,
        # case variants, repeated fields and session-token queries fail closed.
        required = (
            "X-Amz-Algorithm", "X-Amz-Credential", "X-Amz-Date",
            "X-Amz-Expires", "X-Amz-SignedHeaders", "X-Amz-Signature",
        )
        if set(query) != set(required) or any(len(query[name]) != 1 for name in required):
            raise CapabilityBackendError("minio_presign_invalid_url")

        expiry = query["X-Amz-Expires"][0]
        if expiry != str(ttl_seconds):
            raise CapabilityBackendError("minio_presign_ttl_mismatch")

        amz_date = query["X-Amz-Date"][0]
        if (
            query["X-Amz-Algorithm"][0] != "AWS4-HMAC-SHA256"
            or query["X-Amz-SignedHeaders"][0] != "host"
            or re.fullmatch(r"[0-9]{8}T[0-9]{6}Z", amz_date) is None
            or re.fullmatch(r"[0-9a-f]{64}", query["X-Amz-Signature"][0]) is None
        ):
            raise CapabilityBackendError("minio_presign_invalid_url")
        try:
            datetime.strptime(amz_date, "%Y%m%dT%H%M%SZ")
        except ValueError:
            raise CapabilityBackendError("minio_presign_invalid_url") from None

        credential = query["X-Amz-Credential"][0].split("/")
        if (
            len(credential) != 5
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,255}", credential[0]) is None
            or credential[1] != amz_date[:8]
            or re.fullmatch(r"[a-z0-9-]{2,32}", credential[2]) is None
            or credential[3:] != ["s3", "aws4_request"]
        ):
            raise CapabilityBackendError("minio_presign_invalid_url")
        return url

    async def presign(self, request: ObjectsPresignRequest) -> ObjectsPresignResponse:
        envelope = request_envelope(request, ObjectsPresignRequest)
        self._ensure_open()
        company, plugin, bucket, prefix = self._scope(request)
        key = self._exact_presign_key(request.object_key, prefix)
        signer_name = {
            ObjectPresignMethod.GET: "presign_get",
            ObjectPresignMethod.PUT: "presign_put",
            ObjectPresignMethod.HEAD: "presign_head",
        }.get(request.method)
        if signer_name is None:
            raise CapabilityAccessDenied("objects_presign_method_not_allowed")
        signer = getattr(self._presigner, signer_name, None)
        if not callable(signer):
            raise CapabilityBackendError("objects_presigner_unavailable")
        url = await await_io(
            signer(bucket=bucket, key=key, ttl_seconds=request.ttl_seconds),
            timeout=self._timeout_seconds,
            code="minio_presign_failed",
        )
        url = self._validate_presigned_url(url, request.ttl_seconds, bucket, key)
        expires = int(self._clock.time()) + request.ttl_seconds
        metadata = {
            "bucket": bucket,
            "key_digest": digest_text(key),
            "method": request.method,
            "ttl_seconds": request.ttl_seconds,
            "expires_at_epoch": expires,
            "company_id": company,
            "plugin_id": plugin,
            "project_id": envelope.project_id,
            "plugin_instance_id": envelope.plugin_instance_id,
        }
        return ObjectsPresignResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest(metadata),
            url=url,
            **metadata,
        )

    async def list(self, request: ObjectsListRequest) -> ObjectsListResponse:
        envelope = request_envelope(request, ObjectsListRequest)
        self._ensure_open()
        _company, _plugin, bucket, prefix = self._scope(request)
        if request.key_prefix != prefix:
            raise CapabilityAccessDenied("objects_prefix_plugin_mismatch")
        page = await await_io(
            self._buckets.list_prefix_page(
                bucket=bucket, prefix=prefix, limit=request.limit, cursor=request.cursor
            ),
            timeout=self._timeout_seconds,
            code="minio_list_failed",
        )
        if not isinstance(page, dict) or not isinstance(page.get("objects"), (list, tuple)):
            raise CapabilityBackendError("minio_list_invalid_page")
        next_cursor = page.get("next_cursor")
        if next_cursor is not None and not isinstance(next_cursor, str):
            raise CapabilityBackendError("minio_list_invalid_page")
        objects: list[ObjectRef] = []
        for info in page["objects"]:
            key = getattr(info, "key", None)
            size = getattr(info, "size", None)
            if (
                not isinstance(key, str) or not key.startswith(prefix) or ".." in key
                or not isinstance(size, int) or size < 0
            ):
                raise CapabilityAccessDenied("minio_list_tenant_leak")
            content_digest = await await_io(
                self._buckets.content_sha256(
                    bucket=bucket,
                    key=key,
                    expected_size=size,
                ),
                timeout=self._timeout_seconds,
                code="minio_object_checksum_failed",
            )
            try:
                objects.append(ObjectRef(
                    object_key=key, size_bytes=size, content_digest=content_digest
                ))
            except ValueError:
                raise CapabilityBackendError("minio_object_sha256_missing") from None
        if len(objects) > request.limit:
            raise CapabilityBackendError("minio_list_cap_exceeded")
        return ObjectsListResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "objects": [
                    {"key": item.object_key, "size": item.size_bytes, "digest": item.content_digest}
                    for item in objects
                ],
                "next_cursor": next_cursor,
            }),
            objects=tuple(objects),
            next_cursor=next_cursor,
            total_bytes=sum(item.size_bytes for item in objects),
        )

    async def delete(self, request: ObjectsDeleteRequest) -> ObjectsDeleteResponse:
        envelope = request_envelope(request, ObjectsDeleteRequest)
        self._ensure_open()
        _company, _plugin, bucket, prefix = self._scope(request)
        key = request.object_key
        if (
            not key.startswith(prefix) or len(key) <= len(prefix) or "\\" in key
            or "//" in key or ".." in key.split("/")
            or any(char in key for char in ("*", "?"))
            or any(ord(char) < 32 or ord(char) == 127 for char in key)
        ):
            raise CapabilityAccessDenied("objects_key_outside_plugin_scope")
        deleted = await await_io(
            self._buckets.delete_object(bucket=bucket, key=key),
            timeout=self._timeout_seconds,
            code="minio_delete_failed",
        )
        if not isinstance(deleted, bool):
            raise CapabilityBackendError("minio_delete_invalid_result")
        return ObjectsDeleteResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({"bucket": bucket, "key": key, "deleted": deleted}),
            deleted=deleted,
        )

    async def quota_usage(self, request: Any) -> Any:
        if isinstance(request, ObjectsListRequest):
            response = await self.list(request)
            return usage(
                rows=len(response.objects),
                keys=len(response.objects),
                object_bytes=response.total_bytes,
            )
        if isinstance(request, ObjectsDeleteRequest):
            return usage(rows=1, keys=1, object_bytes=self._object_bytes_reservation)
        if isinstance(request, (ObjectsScopedCredentialsRequest, ObjectsPresignRequest)):
            return usage(rows=1, keys=1)
        raise CapabilityAccessDenied("typed_request_required")


__all__ = ["MinioObjectsAdapter"]
