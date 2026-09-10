"""Tenant Infra Gateway BC (in-proc).

Do not eagerly import services here — that creates a circular import:
``bridge`` → ``core.infra.cache`` → … → ``tenant_infra.service`` → ``bridge``.
Import concrete modules (``service``, ``quota``, …) directly at call sites.
"""

__all__: list[str] = []
