"""AI key probe infrastructure (PROBE-P1).

Probe = short HTTP request to the provider's API to verify the stored secret
works. Preferred method: GET /models (free, no tokens spent). Fallback: a
minimal chat completion when /models is not exposed by the provider.

This module is isolated from the rest of ai_keys: it only reads the key +
secret + catalog, performs an HTTP call, and returns a ProbeResult. It never
mutates the key record itself (probe results are stored separately).
"""

from prodavan.application.ai_keys.probe.http_probe import HttpProbeClient, probe_http
from prodavan.application.ai_keys.probe.provider_resolver import (
    ProviderEndpoint,
    ProviderResolver,
)
from prodavan.application.ai_keys.probe.service import AiKeyProbeService

__all__ = [
    "AiKeyProbeService",
    "HttpProbeClient",
    "ProviderEndpoint",
    "ProviderResolver",
    "probe_http",
]
