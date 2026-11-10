"""Deterministic, non-secret plugin installation identity helpers.

The identifier is computable from authoritative company/plugin scope before a
source, manifest, publication approval, or grant exists. It deliberately has
no installation nonce: replay, revoke/re-grant, and artifact updates therefore
keep addressing the same installation. Persisted legacy identifiers remain
authoritative whenever an existing grant/instance is replayed.
"""

from __future__ import annotations

import hashlib
import json
import re

PLUGIN_INSTANCE_ID_PATTERN: re.Pattern[str] = re.compile(r"^inst_[0-9a-f]{32}$")
_PLUGIN_INSTANCE_ID_NAMESPACE = b"agentscale.plugin-instance.v1\x00"


def plugin_instance_id_for(company_id: str, plugin_id: str) -> str:
    """Return the canonical inst_<32 hex> id for a company/plugin scope.

    A fixed namespace and canonical JSON array make the SHA-256 input stable
    across processes and unambiguous for arbitrary string boundaries. Raw
    tenant values are never included in the returned identifier.
    """

    if not isinstance(company_id, str) or not company_id:
        raise ValueError("company_id must be a non-empty string")
    if not isinstance(plugin_id, str) or not plugin_id:
        raise ValueError("plugin_id must be a non-empty string")
    identity = json.dumps(
        [company_id, plugin_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(_PLUGIN_INSTANCE_ID_NAMESPACE + identity).hexdigest()
    return f"inst_{digest[:32]}"


__all__ = ["PLUGIN_INSTANCE_ID_PATTERN", "plugin_instance_id_for"]
