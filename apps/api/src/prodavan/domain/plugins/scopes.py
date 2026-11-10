"""Neutral validation of optional manifest scopes for one plugin instance.

This module depends only on domain contracts. The application supplies the
existing canonical secret-reference predicate, keeping sources and artifact
publication out of each other's import graph and avoiding a second ref parser.
"""

from __future__ import annotations

from typing import Protocol

from .helpers import plugin_instance_id_for
from .types import POD_SCOPE_MAX_LENGTH, POD_SCOPE_PATTERN, PluginManifest


class SecretDeliveryRefValidator(Protocol):
    """Structural signature of the existing canonical runtime predicate."""

    def __call__(
        self,
        *,
        company_id: str,
        plugin_id: str,
        plugin_instance_id: str,
        secret_ref: str,
    ) -> None: ...


def validate_plugin_instance_scopes(
    manifest: PluginManifest,
    *,
    company_id: str,
    plugin_id: str,
    secret_ref_validator: SecretDeliveryRefValidator,
    expected_instance_id: str | None = None,
) -> None:
    """Require every declared scope to target one trusted installation.

    Absent pods/secret_delivery declarations grant no access and are valid.
    Errors are sanitized ValueErrors so application boundaries can map them to
    their existing validation/quarantine contracts without leaking ref text.
    """

    if not isinstance(manifest, PluginManifest):
        raise TypeError("manifest must be a validated PluginManifest")
    if not callable(secret_ref_validator):
        raise TypeError("secret_ref_validator must be callable")
    if manifest.plugin_id != plugin_id:
        raise ValueError("manifest plugin_id does not match the requested scope")

    if expected_instance_id is None:
        expected_instance = plugin_instance_id_for(company_id, plugin_id)
    elif (
        not isinstance(expected_instance_id, str)
        or len(expected_instance_id) > POD_SCOPE_MAX_LENGTH
        or POD_SCOPE_PATTERN.fullmatch(expected_instance_id) is None
    ):
        raise ValueError("trusted expected_instance_id is invalid")
    else:
        # Explicit ids are accepted only from a server-side trusted resolver;
        # callers must never forward a manifest/request value into this slot.
        expected_instance = expected_instance_id

    if manifest.pods is not None:
        for entry in manifest.pods.allowlist:
            if entry.plugin_instance_id != expected_instance:
                raise ValueError(
                    "pods.allowlist plugin_instance_id does not match "
                    "the trusted plugin instance"
                )

    if manifest.secret_delivery is not None:
        for secret_ref in manifest.secret_delivery.allowlist:
            try:
                secret_ref_validator(
                    company_id=company_id,
                    plugin_id=plugin_id,
                    plugin_instance_id=expected_instance,
                    secret_ref=secret_ref,
                )
            except Exception:
                # The existing predicate is the only ref algorithm. Its typed
                # access error is deliberately normalized for neutral callers.
                raise ValueError(
                    "secret_delivery ref does not match the trusted "
                    "plugin instance namespace"
                ) from None


__all__ = ["SecretDeliveryRefValidator", "validate_plugin_instance_scopes"]
