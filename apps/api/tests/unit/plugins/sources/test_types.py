"""ModuleSource validation tests; no database or GitHub credentials involved."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.sources.types import ModuleSource, ModuleSourceStatus
from prodavan.domain.plugins.helpers import (
    PLUGIN_INSTANCE_ID_PATTERN,
    plugin_instance_id_for,
)

WORKFLOW = "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40
DIRECT_WORKFLOW = (
    "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@refs/heads/main"
)


def _source() -> ModuleSource:
    now = datetime(2026, 11, 9, tzinfo=UTC)
    return ModuleSource(
        id="psrc_0123456789abcdef",
        company_id="company_1",
        plugin_id="hello_plugin",
        repo_id=123,
        full_name="ne-tort/example",
        private=True,
        default_branch="main",
        installation_credential_ref="vault://github_app/company_1/hello_plugin/installation",
        trusted_workflow_ref=WORKFLOW,
        created_at=now,
        updated_at=now,
        status=ModuleSourceStatus.READY,
    )


def test_source_projects_the_prepublication_plugin_instance_id() -> None:
    source = _source()
    instance_id = source.plugin_instance_id
    assert instance_id == plugin_instance_id_for(source.company_id, source.plugin_id)
    assert PLUGIN_INSTANCE_ID_PATTERN.fullmatch(instance_id)
    assert source.company_id not in instance_id


def test_source_has_exact_oidc_and_cosign_identities() -> None:
    source = _source()
    assert source.expected_ref == "refs/heads/main"
    assert source.expected_signer_identity == "https://github.com/" + source.trusted_workflow_ref


def test_direct_source_binds_exact_canonical_protected_branch_identity() -> None:
    for default_branch in ("main", "refs/heads/main"):
        source = replace(
            _source(),
            full_name="ne-tort/agentscale",
            default_branch=default_branch,
            trusted_workflow_ref=DIRECT_WORKFLOW,
        )
        assert source.expected_ref == "refs/heads/main"
        assert source.expected_signer_identity == "https://github.com/" + DIRECT_WORKFLOW


@pytest.mark.parametrize(
    "changes",
    [
        {"provider": "github"},
        {"installation_credential_ref": "ghp_raw-token-value"},
        {"installation_credential_ref": "file:///tmp/github-token"},
        {"trusted_workflow_ref": "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@main"},
        {"trusted_workflow_ref": "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 39},
        {"trusted_workflow_ref": "ne-tort/agentscale/.github/workflows/plugin-artifacts.yml@" + "A" * 40},
        {"trusted_workflow_ref": "other/agentscale/.github/workflows/plugin-artifacts.yml@" + "a" * 40},
        {"trusted_workflow_ref": "ne-tort/agentscale/.github/workflows/other.yml@" + "a" * 40},
        {"trusted_workflow_ref": DIRECT_WORKFLOW},
        {
            "full_name": "ne-tort/agentscale",
            "trusted_workflow_ref": DIRECT_WORKFLOW.replace("refs/heads/main", "refs/heads/release"),
        },
        {
            "full_name": "ne-tort/agentscale",
            "trusted_workflow_ref": DIRECT_WORKFLOW.replace("refs/heads/main", "refs/heads/*"),
        },
        {
            "full_name": "ne-tort/agentscale",
            "default_branch": "release",
            "trusted_workflow_ref": DIRECT_WORKFLOW.replace("refs/heads/main", "refs/heads/release"),
        },
        {"default_branch": "refs/tags/main"},
        {"status": "active"},
    ],
)
def test_unknown_provider_mutable_workflow_and_raw_secrets_fail_closed(changes: dict) -> None:
    with pytest.raises(ValueError):
        replace(_source(), **changes)


def test_scope_pair_must_be_complete_exact_and_trimmed() -> None:
    for changes in (
        {"cabinet_id": "cab_1"},
        {"project_id": "prj_1"},
        {"cabinet_id": " cab_1 ", "project_id": "prj_1"},
    ):
        with pytest.raises(ValueError):
            replace(_source(), **changes)
    bound = replace(_source(), cabinet_id="cab_1", project_id="prj_1")
    assert bound.cabinet_id == "cab_1"
    assert bound.project_id == "prj_1"
