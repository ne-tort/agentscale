"""Canonical server-owned origins for the plugin iframe trust boundary."""

from __future__ import annotations

from urllib.parse import urlsplit


class PluginOriginError(ValueError):
    """A configured plugin origin is missing, ambiguous, or non-canonical."""


def exact_https_origin(value: object, *, setting: str) -> str:
    """Return a canonical exact HTTPS origin or fail closed.

    Paths, query/fragment, credentials, wildcards, non-ASCII/case-ambiguous
    hosts and non-default explicit ports are handled deterministically. A single
    trailing slash and an explicit default HTTPS port are accepted only as input
    notation; the returned DTO/CSP value is always the canonical URL origin.
    """

    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > 2048
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or "*" in value
    ):
        raise PluginOriginError(f"{setting} must be an exact HTTPS origin")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as exc:
        raise PluginOriginError(f"{setting} must be an exact HTTPS origin") from exc
    hostname = parsed.hostname
    if (
        parsed.scheme != "https"
        or not hostname
        or not hostname.isascii()
        or hostname != hostname.lower()
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise PluginOriginError(f"{setting} must be an exact HTTPS origin")
    host = f"[{hostname}]" if ":" in hostname else hostname
    expected_netloc = host + (f":{port}" if port is not None else "")
    without_trailing_slash = value[:-1] if value.endswith("/") else value
    if without_trailing_slash != f"https://{expected_netloc}":
        raise PluginOriginError(f"{setting} must be a canonical HTTPS origin")
    canonical_port = "" if port is None or port == 443 else f":{port}"
    return f"https://{host}{canonical_port}"


def exact_plugin_origins(*, static_origin: object, shell_origin: object) -> tuple[str, str]:
    """Validate the two server-owned origins and their required isolation."""

    static = exact_https_origin(static_origin, setting="PLUGIN_STATIC_ORIGIN")
    shell = exact_https_origin(shell_origin, setting="PLUGIN_SHELL_ORIGIN")
    if static == shell:
        raise PluginOriginError(
            "PLUGIN_SHELL_ORIGIN must be distinct from PLUGIN_STATIC_ORIGIN"
        )
    return static, shell


__all__ = ["PluginOriginError", "exact_https_origin", "exact_plugin_origins"]
