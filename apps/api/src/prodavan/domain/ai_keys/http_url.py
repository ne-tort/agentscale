"""Provider endpoint URL hygiene (scheme default + shared segment dedup).

User-entered ``base_url`` values may lack a scheme ("cheapai.lol/v1") and a
catalog ``models_path`` may repeat the base's trailing ``/v1`` segment — raw
concatenation then yields "cheapai.lol/v1/v1/models" (not a fetchable URL,
or a 404) and every model list comes back empty (503 MODELS_UNAVAILABLE).
Single source of truth so the HTTP probe, the pod probe and the live model
list build identical URLs from the same catalog entry.
"""

from __future__ import annotations


def ensure_scheme(base_url: str) -> str:
    """Bare host (no ``://``) defaults to https."""
    base = (base_url or "").strip()
    if base and "://" not in base:
        return f"https://{base}"
    return base


def join_endpoint_url(base_url: str, path: str) -> str:
    """Join base_url + path, defaulting the scheme and deduplicating a shared
    trailing path segment (``/v1`` base + ``/v1/models`` path → ``/v1/models``).
    """
    base = ensure_scheme((base_url or "").rstrip("/"))
    p = path or ""
    if not p.startswith("/"):
        p = "/" + p
    # Last path segment of base (e.g. "/v1" for "https://x/v1"); skip the
    # scheme "//" pseudo-segment so "https://api.openai.com" does not match.
    seg = ""
    after_scheme = base.split("://", 1)[-1]
    slash = after_scheme.rfind("/")
    if slash >= 1:
        seg = after_scheme[slash:]
    if seg and p.startswith(seg + "/"):
        return base + p[len(seg) :]
    if seg and p == seg:
        return base
    return base + p


def endpoint_path_for(base_url: str, path: str) -> str:
    """Path component (leading ``/``) that concatenates with ``base_url``
    without duplicating a shared segment — for callers that pass base_url and
    the path to the runtime as separate query params.
    """
    full = join_endpoint_url(base_url, path)
    base = ensure_scheme((base_url or "").rstrip("/"))
    suffix = full[len(base) :] if base and full.startswith(base) else (path or "/")
    if not suffix.startswith("/"):
        suffix = "/" + suffix
    return suffix or "/"
