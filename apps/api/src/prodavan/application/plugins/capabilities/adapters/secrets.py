"""Digest-only reads and optional, policy-bound Kubernetes secret delivery.

Plaintext is resolved only by the control-plane SecretStore. The optional
deliver/revoke methods pass it directly to SecretProjection; typed responses
contain references and SHA-256 metadata only. A missing store, policy, or
projection is a typed unwired operation, never a file fallback.
"""
from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable
from urllib.parse import quote

from prodavan.application.plugins.capabilities.contracts import (
    SecretsReadRequest,
    SecretsReadResponse,
    TypedContractError,
)
if TYPE_CHECKING:
    from prodavan.application.plugins.admin.contracts import SecretPreDeliveryRequest

from ._shared import (
    CloseOnce,
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    await_io,
    usage,
    canonical_digest,
    digest_text,
    plugin_id,
    request_envelope,
    thread_io,
)

K8S_SECRET_DELIVERY_MODE = "k8s_secret"
_EMPTY_VALUE_DIGEST = digest_text("")


def _require_typed_control_request(request: Any) -> None:
    """Resolve the control type lazily without an admin/secrets import cycle."""
    from prodavan.application.plugins.admin.contracts import SecretPreDeliveryRequest

    if not isinstance(request, SecretPreDeliveryRequest):
        raise CapabilityAccessDenied("typed_control_request_required")


def secret_delivery_allowlist(manifest: Any) -> tuple[str, ...]:
    """Shared fail-closed hook for the signed manifest delivery declaration.

    Returns an empty tuple only when ``secret_delivery`` is absent. Values are
    exact opaque source references and are never included in error text; the
    signed delivery policy maps failures to its own typed access errors, and
    the runtime lifecycle adapter maps them to typed deployment failures.
    """

    if not isinstance(manifest, Mapping):
        raise ValueError("secret_delivery_manifest_invalid")
    if "secret_delivery" not in manifest:
        return ()
    declaration = manifest["secret_delivery"]
    if not isinstance(declaration, Mapping) or set(declaration) != {"mode", "allowlist"}:
        raise ValueError("secret_delivery_not_allowlisted")
    if declaration.get("mode") != K8S_SECRET_DELIVERY_MODE:
        raise ValueError("secret_delivery_mode_not_approved")
    refs = declaration.get("allowlist")
    if (
        not isinstance(refs, (list, tuple))
        or not refs
        or len(refs) > 256
        or any(not isinstance(ref, str) or not ref for ref in refs)
        or len(set(refs)) != len(refs)
    ):
        raise ValueError("secret_delivery_allowlist_invalid")
    return tuple(refs)


def validate_secret_delivery_ref(
    *,
    company_id: str,
    plugin_id: str,
    plugin_instance_id: str,
    secret_ref: str,
) -> None:
    """Require an exact source ref in the canonical instance namespace."""
    if not isinstance(secret_ref, str) or not secret_ref or len(secret_ref) > 2048:
        raise CapabilityAccessDenied("secret_ref_not_canonical")
    suffix = (
        "plugins/capabilities/secrets/"
        f"{quote(company_id, safe='')}/"
        f"{quote(plugin_id, safe='')}/"
        f"{quote(plugin_instance_id, safe='')}/"
    )
    candidates = {
        scheme: f"{scheme}://ai_keys/{suffix}"
        for scheme in ("vault", "file")
    }
    scheme = next(
        (name for name, prefix in candidates.items() if secret_ref.startswith(prefix)),
        None,
    )
    if scheme is None:
        raise CapabilityAccessDenied("secret_ref_outside_plugin_scope")
    tail = secret_ref[len(candidates[scheme]):]
    if scheme == "file":
        if not tail.endswith(".secret"):
            raise CapabilityAccessDenied("secret_ref_not_canonical")
        tail = tail[:-len(".secret")]
    if (
        not tail
        or "/" in tail
        or tail in {".", ".."}
        or quote(tail, safe="") != tail
    ):
        raise CapabilityAccessDenied("secret_ref_not_canonical")


def _delivery_data_key(secret_ref: str) -> str:
    return "SECRET_" + hashlib.sha256(secret_ref.encode("utf-8")).hexdigest().upper()


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretsDeliveryRequest(SecretsReadRequest):
    """Deliver one exact, allowlisted source reference to its instance Secret."""

    mode: str = K8S_SECRET_DELIVERY_MODE

    def __post_init__(self) -> None:
        SecretsReadRequest.__post_init__(self)
        if self.mode != K8S_SECRET_DELIVERY_MODE:
            raise TypedContractError("mode must be k8s_secret")


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretsRevokeRequest(SecretsDeliveryRequest):
    """Remove a projected value without deleting its source secret."""


@dataclass(frozen=True, slots=True, kw_only=True)
class SecretsDeliveryResponse(SecretsReadResponse):
    """Plaintext-free delivery acknowledgement (including idempotent revoke)."""

    mode: str = K8S_SECRET_DELIVERY_MODE
    delivered: bool = True

    def __post_init__(self) -> None:
        SecretsReadResponse.__post_init__(self)
        if self.mode != K8S_SECRET_DELIVERY_MODE:
            raise TypedContractError("mode must be k8s_secret")
        if not isinstance(self.delivered, bool):
            raise TypedContractError("delivered must be a boolean")


# Verb-shaped aliases match the existing SecretsReadRequest naming style.
SecretsDeliverRequest = SecretsDeliveryRequest
SecretsDeliverResponse = SecretsDeliveryResponse
SecretsDeleteRequest = SecretsRevokeRequest


def _delivery_response(
    *,
    request_id: str,
    secret_ref: str,
    version: str,
    delivered: bool,
) -> SecretsDeliveryResponse:
    return SecretsDeliveryResponse(
        request_id=request_id,
        ok=True,
        digest=canonical_digest({
            "secret_ref": secret_ref,
            "version": version,
            "value_digest": version,
            "mode": K8S_SECRET_DELIVERY_MODE,
            "delivered": delivered,
        }),
        secret_ref=secret_ref,
        version=version,
        value_digest=version,
        mode=K8S_SECRET_DELIVERY_MODE,
        delivered=delivered,
    )


@runtime_checkable
class SecretDeliveryPolicyPort(Protocol):
    """Trusted signed-manifest or approved-policy authorization boundary."""

    async def authorize(
        self,
        *,
        envelope: Any,
        plugin_id: str,
        secret_ref: str,
    ) -> None: ...


class SignedManifestSecretDeliveryPolicy:
    """Authorize exact refs from the artifact pinned by the active grant.

    The signed manifest uses secret_delivery with mode=k8s_secret and an exact
    allowlist. Wildcards and caller-supplied logical names are not accepted.
    Grant, company/plugin/instance, artifact digest and verified registry record
    must all match the server envelope.
    """

    def __init__(self, *, artifact_registry: Any, grant_store: Any) -> None:
        if not callable(getattr(artifact_registry, "get_async", None)) or not callable(
            getattr(grant_store, "get_by_grant_id", None)
        ):
            raise CapabilityConfigurationError("secret_delivery_policy_unwired")
        self._artifacts = artifact_registry
        self._grants = grant_store

    @staticmethod
    def _allowlist(manifest: Any) -> frozenset[str]:
        if isinstance(manifest, Mapping) and "secret_delivery" not in manifest:
            raise CapabilityAccessDenied("secret_delivery_not_allowlisted")
        try:
            refs = secret_delivery_allowlist(manifest)
        except ValueError as exc:
            raise CapabilityAccessDenied(str(exc)) from None
        return frozenset(refs)

    async def authorize(
        self,
        *,
        envelope: Any,
        plugin_id: str,
        secret_ref: str,
    ) -> None:
        try:
            grant = await await_io(
                self._grants.get_by_grant_id(grant_id=envelope.grant_id),
                timeout=5.0,
                code="secret_delivery_grant_failed",
            )
            expected = {
                "grant_id": envelope.grant_id,
                "company_id": envelope.company_id,
                "plugin_id": plugin_id,
                "plugin_instance_id": envelope.plugin_instance_id,
                "artifact_digest": envelope.artifact_digest,
            }
            active = getattr(grant, "is_active", False)
            expires_at = getattr(grant, "expires_at", None)
            if (
                grant is None
                or any(getattr(grant, name, None) != value for name, value in expected.items())
                or callable(active)
                or not bool(active)
                or getattr(grant, "revoked_at", None) is not None
                or "secrets" not in frozenset(getattr(grant, "capabilities", ()))
                or (
                    expires_at is not None
                    and (
                        not isinstance(expires_at, datetime)
                        or expires_at.tzinfo is None
                        or expires_at.utcoffset() is None
                        or expires_at <= datetime.now(UTC)
                    )
                )
            ):
                raise CapabilityAccessDenied("secret_delivery_grant_not_approved")

            version = getattr(grant, "manifest_version", "")
            if not isinstance(version, str) or not version:
                raise CapabilityAccessDenied("secret_delivery_manifest_invalid")
            artifact = await await_io(
                self._artifacts.get_async(plugin_id, version),
                timeout=5.0,
                code="secret_delivery_artifact_failed",
            )
            verification = getattr(artifact, "verification", None)
            if (
                artifact is None
                or getattr(artifact, "plugin_id", None) != plugin_id
                or getattr(artifact, "version", None) != version
                or getattr(artifact, "digest", None) != envelope.artifact_digest
                or getattr(artifact, "published", False) is not True
                or getattr(artifact, "quarantine", None) is not None
                or verification is None
                or getattr(verification, "artifact_digest", None) != envelope.artifact_digest
            ):
                raise CapabilityAccessDenied("secret_delivery_artifact_not_verified")
            if secret_ref not in self._allowlist(getattr(artifact, "manifest", None)):
                raise CapabilityAccessDenied("secret_ref_not_allowlisted")
        except CapabilityAccessDenied:
            raise
        except CapabilityBackendError:
            raise
        except Exception:
            # Registry/driver messages can contain signed content or backend DSNs.
            raise CapabilityAccessDenied("secret_delivery_policy_failed") from None


class SecretStoreSecretsAdapter(CloseOnce):
    def __init__(
        self,
        *,
        store: Any,
        max_secret_bytes: int = 65536,
        timeout_seconds: float = 5.0,
        owns_store: bool = False,
        projection: Any = None,
        delivery_policy: Any = None,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=(store,) if owns_store else (),
        )
        if store is None or not callable(getattr(store, "get", None)):
            raise CapabilityConfigurationError("secret_store_unwired")
        if (
            not isinstance(max_secret_bytes, int)
            or isinstance(max_secret_bytes, bool)
            or not 1 <= max_secret_bytes <= 1024 * 1024
        ):
            raise CapabilityConfigurationError("secret_size_cap_invalid")
        self._store = store
        self._max_secret_bytes = max_secret_bytes
        self._projection = projection
        self._delivery_policy = delivery_policy

    def _validate_ref(self, envelope: Any, secret_ref: str) -> None:
        validate_secret_delivery_ref(
            company_id=envelope.company_id,
            plugin_id=plugin_id(envelope),
            plugin_instance_id=envelope.plugin_instance_id,
            secret_ref=secret_ref,
        )

    async def _value_and_digest(self, envelope: Any, secret_ref: str) -> tuple[str, str]:
        del envelope
        value = await thread_io(
            self._store.get,
            secret_ref,
            timeout=self._timeout_seconds,
            code="secret_store_read_failed",
        )
        if not isinstance(value, str) or not value:
            raise CapabilityBackendError("secret_value_missing_or_invalid")
        size = len(value.encode("utf-8"))
        if size > self._max_secret_bytes:
            raise CapabilityBackendError("secret_value_exceeds_cap")
        return value, digest_text(value)

    def _delivery_dependencies(self, *, revoke: bool = False) -> None:
        method = "revoke_secret" if revoke else "deliver_secret"
        if (
            not callable(getattr(self._projection, method, None))
            or not callable(getattr(self._delivery_policy, "authorize", None))
        ):
            raise CapabilityConfigurationError("secret_delivery_unwired")

    async def _authorize(self, envelope: Any, secret_ref: str) -> None:
        try:
            pending = self._delivery_policy.authorize(
                envelope=envelope,
                plugin_id=plugin_id(envelope),
                secret_ref=secret_ref,
            )
            await await_io(
                pending,
                timeout=self._timeout_seconds,
                code="secret_delivery_policy_failed",
            )
        except (CapabilityAccessDenied, CapabilityBackendError):
            raise
        except Exception:
            raise CapabilityAccessDenied("secret_delivery_policy_failed") from None

    @staticmethod
    def _response(
        *,
        envelope: Any,
        secret_ref: str,
        version: str,
        delivered: bool,
    ) -> SecretsDeliveryResponse:
        return _delivery_response(
            request_id=envelope.request_id,
            secret_ref=secret_ref,
            version=version,
            delivered=delivered,
        )

    def quota_usage(self, request: SecretsReadRequest) -> Any:
        return usage(rows=1, bytes=self._max_secret_bytes, keys=1)

    async def read(self, request: SecretsReadRequest) -> SecretsReadResponse:
        """Return only source metadata; this method never projects plaintext."""
        envelope = request_envelope(request, SecretsReadRequest)
        self._ensure_open()
        self._validate_ref(envelope, request.secret_ref)
        _value, value_digest = await self._value_and_digest(envelope, request.secret_ref)
        # Content addressing is truthful for file:// and vault:// stores and does
        # not pretend to be Vault's numeric KV metadata version.
        version = value_digest
        return SecretsReadResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "secret_ref": request.secret_ref,
                "version": version,
                "value_digest": value_digest,
            }),
            secret_ref=request.secret_ref,
            version=version,
            value_digest=value_digest,
        )

    async def deliver(self, request: SecretsDeliveryRequest) -> SecretsDeliveryResponse:
        envelope = request_envelope(request, SecretsDeliveryRequest)
        self._ensure_open()
        self._delivery_dependencies()
        self._validate_ref(envelope, request.secret_ref)
        await self._authorize(envelope, request.secret_ref)
        value, value_digest = await self._value_and_digest(envelope, request.secret_ref)
        try:
            pending = self._projection.deliver_secret(
                company_id=envelope.company_id,
                plugin_id=plugin_id(envelope),
                plugin_instance_id=envelope.plugin_instance_id,
                secret_ref=request.secret_ref,
                value=value,
            )
            reference = await await_io(
                pending,
                timeout=self._timeout_seconds,
                code="secret_delivery_projection_failed",
            )
        except CapabilityBackendError:
            raise
        except Exception:
            raise CapabilityBackendError("secret_delivery_projection_failed") from None
        if (
            getattr(reference, "value_digest", None) != value_digest
            or getattr(reference, "version", None) != value_digest
            or getattr(reference, "delivered", None) is not True
        ):
            raise CapabilityBackendError("secret_delivery_projection_invalid")
        return self._response(
            envelope=envelope,
            secret_ref=request.secret_ref,
            version=value_digest,
            delivered=True,
        )

    async def revoke(self, request: SecretsRevokeRequest) -> SecretsDeliveryResponse:
        envelope = request_envelope(request, SecretsRevokeRequest)
        self._ensure_open()
        self._delivery_dependencies(revoke=True)
        self._validate_ref(envelope, request.secret_ref)
        await self._authorize(envelope, request.secret_ref)
        try:
            pending = self._projection.revoke_secret(
                company_id=envelope.company_id,
                plugin_id=plugin_id(envelope),
                plugin_instance_id=envelope.plugin_instance_id,
                secret_ref=request.secret_ref,
            )
            reference = await await_io(
                pending,
                timeout=self._timeout_seconds,
                code="secret_revoke_projection_failed",
            )
        except CapabilityBackendError:
            raise
        except Exception:
            raise CapabilityBackendError("secret_revoke_projection_failed") from None
        version = getattr(reference, "value_digest", _EMPTY_VALUE_DIGEST)
        if (
            getattr(reference, "delivered", None) is not False
            or not isinstance(version, str)
            or not version.startswith("sha256:")
        ):
            raise CapabilityBackendError("secret_revoke_projection_invalid")
        return self._response(
            envelope=envelope,
            secret_ref=request.secret_ref,
            version=version,
            delivered=False,
        )

    async def delete(self, request: SecretsRevokeRequest) -> SecretsDeliveryResponse:
        """Lifecycle delete alias; source secret material is not deleted."""
        return await self.revoke(request)


class ControlPlaneSecretDelivery:
    """Two-phase delivery using an approved artifact and persisted installation.

    This service deliberately has no grant-store, CapabilityEnvelope, subject, or
    plugin-token dependency. It is callable only by the trusted admin lifecycle.
    """

    def __init__(
        self,
        *,
        store: Any,
        projection: Any,
        artifact_registry: Any,
        max_secret_bytes: int = 65536,
        timeout_seconds: float = 5.0,
    ) -> None:
        if store is None or not callable(getattr(store, "get", None)):
            raise CapabilityConfigurationError("secret_store_unwired")
        if not callable(getattr(artifact_registry, "get_async", None)) or not all(
            callable(getattr(projection, method, None))
            for method in ("reconcile_secret_values", "revoke_secret")
        ):
            raise CapabilityConfigurationError("secret_control_delivery_unwired")
        if (
            not isinstance(max_secret_bytes, int)
            or isinstance(max_secret_bytes, bool)
            or not 1 <= max_secret_bytes <= 1024 * 1024
        ):
            raise CapabilityConfigurationError("secret_size_cap_invalid")
        if timeout_seconds <= 0:
            raise CapabilityConfigurationError("invalid_io_timeout")
        self._store = store
        self._projection = projection
        self._artifacts = artifact_registry
        self._max_secret_bytes = max_secret_bytes
        self._timeout_seconds = float(timeout_seconds)

    async def _approved_refs(
        self,
        request: SecretPreDeliveryRequest,
        *,
        require_current_pin: bool,
    ) -> tuple[str, ...]:
        installation = request.installation
        if require_current_pin and (
            installation.manifest_version != request.manifest_version
            or installation.artifact_digest != request.artifact_digest
        ):
            raise CapabilityAccessDenied("secret_control_installation_pin_mismatch")
        try:
            artifact = await await_io(
                self._artifacts.get_async(installation.plugin_id, request.manifest_version),
                timeout=self._timeout_seconds,
                code="secret_control_artifact_failed",
            )
        except CapabilityBackendError:
            raise
        except Exception:
            raise CapabilityAccessDenied("secret_control_artifact_failed") from None
        verification = getattr(artifact, "verification", None)
        if (
            artifact is None
            or getattr(artifact, "plugin_id", None) != installation.plugin_id
            or getattr(artifact, "version", None) != request.manifest_version
            or getattr(artifact, "digest", None) != request.artifact_digest
            or (
                not require_current_pin
                and (
                    getattr(artifact, "published", False) is not True
                    or getattr(artifact, "quarantine", None) is not None
                )
            )
            or verification is None
            or getattr(verification, "artifact_digest", None) != request.artifact_digest
        ):
            raise CapabilityAccessDenied("secret_control_artifact_not_approved")
        try:
            refs = secret_delivery_allowlist(getattr(artifact, "manifest", None))
        except ValueError as exc:
            raise CapabilityAccessDenied(str(exc)) from None
        for secret_ref in refs:
            validate_secret_delivery_ref(
                company_id=installation.company_id,
                plugin_id=installation.plugin_id,
                plugin_instance_id=installation.plugin_instance_id,
                secret_ref=secret_ref,
            )
        return refs

    async def _read_values(self, refs: tuple[str, ...]) -> dict[str, str]:
        values: dict[str, str] = {}
        for secret_ref in refs:
            value = await thread_io(
                self._store.get,
                secret_ref,
                timeout=self._timeout_seconds,
                code="secret_store_read_failed",
            )
            if not isinstance(value, str) or not value:
                raise CapabilityBackendError("secret_value_missing_or_invalid")
            if len(value.encode("utf-8")) > self._max_secret_bytes:
                raise CapabilityBackendError("secret_value_exceeds_cap")
            values[secret_ref] = value
        return values

    async def pre_deliver(
        self, request: SecretPreDeliveryRequest
    ) -> tuple[SecretsDeliveryResponse, ...]:
        _require_typed_control_request(request)
        refs = await self._approved_refs(request, require_current_pin=False)
        if not refs:
            return ()
        values = await self._read_values(refs)
        installation = request.installation
        try:
            pending = self._projection.reconcile_secret_values(
                company_id=installation.company_id,
                plugin_id=installation.plugin_id,
                plugin_instance_id=installation.plugin_instance_id,
                values=values,
            )
            references = await await_io(
                pending,
                timeout=self._timeout_seconds,
                code="secret_control_projection_failed",
            )
        except CapabilityBackendError:
            raise
        except Exception:
            raise CapabilityBackendError("secret_control_projection_failed") from None
        if not isinstance(references, tuple) or len(references) != len(refs):
            raise CapabilityBackendError("secret_control_projection_invalid")
        responses: list[SecretsDeliveryResponse] = []
        for secret_ref, reference in zip(refs, references, strict=True):
            expected_digest = digest_text(values[secret_ref])
            if (
                getattr(reference, "key", None) != _delivery_data_key(secret_ref)
                or getattr(reference, "value_digest", None) != expected_digest
                or getattr(reference, "version", None) != expected_digest
                or getattr(reference, "delivered", None) is not True
            ):
                raise CapabilityBackendError("secret_control_projection_invalid")
            responses.append(_delivery_response(
                request_id=request.request_id,
                secret_ref=secret_ref,
                version=expected_digest,
                delivered=True,
            ))
        return tuple(responses)

    async def pre_revoke(
        self, request: SecretPreDeliveryRequest
    ) -> tuple[SecretsDeliveryResponse, ...]:
        _require_typed_control_request(request)
        refs = await self._approved_refs(request, require_current_pin=True)
        installation = request.installation
        responses: list[SecretsDeliveryResponse] = []
        for secret_ref in refs:
            try:
                pending = self._projection.revoke_secret(
                    company_id=installation.company_id,
                    plugin_id=installation.plugin_id,
                    plugin_instance_id=installation.plugin_instance_id,
                    secret_ref=secret_ref,
                )
                reference = await await_io(
                    pending,
                    timeout=self._timeout_seconds,
                    code="secret_control_revoke_failed",
                )
            except CapabilityBackendError:
                raise
            except Exception:
                raise CapabilityBackendError("secret_control_revoke_failed") from None
            version = getattr(reference, "value_digest", _EMPTY_VALUE_DIGEST)
            if (
                getattr(reference, "key", None) != _delivery_data_key(secret_ref)
                or getattr(reference, "delivered", None) is not False
                or not isinstance(version, str)
                or len(version) != 71
                or not version.startswith("sha256:")
            ):
                raise CapabilityBackendError("secret_control_revoke_invalid")
            responses.append(_delivery_response(
                request_id=request.request_id,
                secret_ref=secret_ref,
                version=version,
                delivered=False,
            ))
        return tuple(responses)


__all__ = [
    "K8S_SECRET_DELIVERY_MODE",
    "ControlPlaneSecretDelivery",
    "SecretDeliveryPolicyPort",
    "SecretStoreSecretsAdapter",
    "SecretsDeleteRequest",
    "SecretsDeliverRequest",
    "SecretsDeliverResponse",
    "SecretsDeliveryRequest",
    "SecretsDeliveryResponse",
    "SecretsRevokeRequest",
    "secret_delivery_allowlist",
    "SignedManifestSecretDeliveryPolicy",
    "validate_secret_delivery_ref",
]
