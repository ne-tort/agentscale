"""Canonical plugin instance identity tests; not run during freeze."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys

import pytest

from prodavan.domain.plugins.helpers import (
    PLUGIN_INSTANCE_ID_PATTERN,
    plugin_instance_id_for,
)

COMPANY = "co-tenant-secret"
PLUGIN = "mod_private"


def _expected(company_id: str, plugin_id: str) -> str:
    identity = json.dumps(
        [company_id, plugin_id],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    digest = hashlib.sha256(
        b"agentscale.plugin-instance.v1\x00" + identity
    ).hexdigest()
    return f"inst_{digest[:32]}"


def _id_from_process(hash_seed: str) -> str:
    script = (
        "from prodavan.domain.plugins.helpers import plugin_instance_id_for\n"
        f"print(plugin_instance_id_for({COMPANY!r}, {PLUGIN!r}))\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=True,
        capture_output=True,
        text=True,
        env=dict(os.environ, PYTHONHASHSEED=hash_seed),
    )
    return completed.stdout.strip()


def test_id_is_stable_across_calls_and_processes() -> None:
    expected = _expected(COMPANY, PLUGIN)

    assert plugin_instance_id_for(COMPANY, PLUGIN) == expected
    assert plugin_instance_id_for(COMPANY, PLUGIN) == expected
    assert _id_from_process("1") == expected
    assert _id_from_process("987654") == expected


def test_id_changes_with_either_scope_and_does_not_leak_tenant_values() -> None:
    instance_id = plugin_instance_id_for(COMPANY, PLUGIN)

    assert PLUGIN_INSTANCE_ID_PATTERN.fullmatch(instance_id)
    assert len(instance_id) == 37
    assert COMPANY not in instance_id
    assert PLUGIN not in instance_id
    assert instance_id != plugin_instance_id_for("co-other", PLUGIN)
    assert instance_id != plugin_instance_id_for(COMPANY, "mod_other")
    assert instance_id != plugin_instance_id_for(PLUGIN, COMPANY)


@pytest.mark.parametrize(
    ("company_id", "plugin_id"),
    [("", PLUGIN), (COMPANY, ""), (None, PLUGIN), (COMPANY, None)],
)
def test_id_rejects_missing_scope_without_a_fallback(
    company_id: object,
    plugin_id: object,
) -> None:
    with pytest.raises(ValueError):
        plugin_instance_id_for(company_id, plugin_id)  # type: ignore[arg-type]
