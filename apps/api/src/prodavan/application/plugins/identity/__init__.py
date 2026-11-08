"""F1: отдельный trust-контур плагинов (identity).

Публичный API:

* ``types``      — ``PluginClaims``, константы контура, ``PluginTokenError``;
* ``keys``       — ``KeyRing`` / ``KeyEntry``, ``peek_plugin_token``, ``kid``/
  JWKS/ротация, HS256+EdDSA;
* ``mint``       — ``mint_plugin_claims`` / ``verify_plugin_token`` /
  ``build_payload`` / ``claims_from_payload``;
* ``revocation`` — ``GrantRevocation`` / ``RevocationPort`` (``is_active``/
  ``is_revoked``) / ``InMemoryRevocation``;
* ``allowlist``  — ``PluginSurfaceAllowlist`` / ``assert_plugin_surface``.

Интеграционная цепочка (родитель подключает в middleware Plugin Gateway)::

    token = bearer(request)
    if is_plugin_capability_token(token):
        claims = verify_plugin_token(token, keyring=keyring, now=...)
        GrantRevocation(port).check(claims)
        assert_plugin_surface(claims, request.url.path)   # только /capabilities/v1/*

``PodBridgeClaims`` / ``core/middleware.py`` / ``main_pod.py`` не используются
и не изменяются: у агентов свой trust (§4.1, §13.3, критерий №17).
"""

from __future__ import annotations

from .allowlist import (
    PluginSurfaceAllowlist,
    assert_plugin_surface,
    is_plugin_capability_token,
    is_plugin_surface_path,
    plugin_surface_allowlist,
)
from .keys import (
    SUPPORTED_ALGORITHMS,
    KeyEntry,
    KeyRing,
    eddsa_entry,
    generate_kid,
    hs256_entry,
    keyring_from_secret,
    kid_from_secret,
    peek_plugin_token,
)
from .mint import (
    EXPIRATION_LEEWAY_SECONDS,
    MintGrant,
    MintInstance,
    build_payload,
    claims_from_payload,
    mint_plugin_claims,
    verify_plugin_token,
)
from .revocation import GrantRevocation, InMemoryRevocation, RevocationPort
from .types import (
    ARTIFACT_DIGEST_PATTERN,
    CAPABILITIES_PATH_PATTERN,
    DEFAULT_PLUGIN_TOKEN_TTL_SECONDS,
    FORBIDDEN_CLAIMS,
    JTI_PATTERN,
    PLUGIN_AUDIENCE,
    PLUGIN_ISSUER,
    PLUGIN_TOKEN_ERROR_CODES,
    PLUGIN_TOKEN_TYP,
    REQUIRED_CLAIMS,
    PluginClaims,
    PluginTokenError,
    PluginTokenErrorCode,
)

__all__ = [
    # types
    "PluginClaims",
    "PluginTokenError",
    "PluginTokenErrorCode",
    "PLUGIN_ISSUER",
    "PLUGIN_AUDIENCE",
    "PLUGIN_TOKEN_TYP",
    "PLUGIN_TOKEN_ERROR_CODES",
    "DEFAULT_PLUGIN_TOKEN_TTL_SECONDS",
    "ARTIFACT_DIGEST_PATTERN",
    "JTI_PATTERN",
    "CAPABILITIES_PATH_PATTERN",
    "REQUIRED_CLAIMS",
    "FORBIDDEN_CLAIMS",
    # keys
    "KeyEntry",
    "KeyRing",
    "SUPPORTED_ALGORITHMS",
    "generate_kid",
    "kid_from_secret",
    "hs256_entry",
    "eddsa_entry",
    "keyring_from_secret",
    "peek_plugin_token",
    # mint
    "MintGrant",
    "MintInstance",
    "EXPIRATION_LEEWAY_SECONDS",
    "build_payload",
    "claims_from_payload",
    "mint_plugin_claims",
    "verify_plugin_token",
    # revocation
    "RevocationPort",
    "InMemoryRevocation",
    "GrantRevocation",
    # allowlist
    "PluginSurfaceAllowlist",
    "plugin_surface_allowlist",
    "is_plugin_surface_path",
    "is_plugin_capability_token",
    "assert_plugin_surface",
]
