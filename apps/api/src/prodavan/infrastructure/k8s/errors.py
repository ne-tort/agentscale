"""Kubernetes API error taxonomy (anti-corruption layer)."""

from __future__ import annotations


class K8sError(Exception):
    """Base k8s infrastructure error."""


class TransientK8sError(K8sError):
    """Retryable failure (timeout, 429, 5xx)."""


class PermanentK8sError(K8sError):
    """Non-retryable failure (403, invalid spec)."""


class K8sNotFoundError(K8sError):
    """Resource not found (404)."""


def classify_http_status(status: int, detail: str = "") -> K8sError:
    if status == 404:
        return K8sNotFoundError(detail or "not found")
    if status in (408, 429, 500, 502, 503, 504):
        return TransientK8sError(detail or f"HTTP {status}")
    if status >= 400:
        return PermanentK8sError(detail or f"HTTP {status}")
    return TransientK8sError(detail or f"unexpected HTTP {status}")
