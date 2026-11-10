"""Public API and source-port composition contracts."""

from __future__ import annotations

import dataclasses

import pytest

from prodavan.infrastructure.plugins.sources import (
    ApplyPatchRequest,
    CommitMetadata,
    CreatePrivateRepositoryRequest,
    CreatePullRequestRequest,
    GitHubAppClient,
    GitHubConfigurationError,
    GitHubPatchPort,
    GitHubPullRequestPort,
    GitHubRepositorySourcePort,
    PatchFile,
    PullRequestMetadata,
)

from .conftest import APP_ID, ORGANIZATION


def test_client_exposes_real_patch_and_pull_request_operations() -> None:
    for method_name in ("apply_patch", "create_pull_request"):
        assert callable(getattr(GitHubAppClient, method_name))


def test_source_ports_are_separate_contracts() -> None:
    assert GitHubRepositorySourcePort is not GitHubPatchPort
    assert GitHubRepositorySourcePort is not GitHubPullRequestPort
    assert GitHubPatchPort is not GitHubPullRequestPort


@pytest.mark.parametrize("operation", ["upsert", "delete"])
def test_patch_request_contract_is_typed(operation: str) -> None:
    request = ApplyPatchRequest(
        branch="agents/plugin-one/initial-import",
        expected_head_sha="a" * 40,
        commit_message="Apply source agent patch",
        files=(
            PatchFile(
                path="AGENTS.md",
                operation=operation,
                content="ok" if operation == "upsert" else None,
            ),
        ),
    )
    assert isinstance(request, ApplyPatchRequest)
    assert request.files[0].path == "AGENTS.md"


def test_commit_and_pull_request_metadata_contracts_are_typed() -> None:
    commit = CommitMetadata(sha="b" * 40)
    pull_request = PullRequestMetadata(
        number=1,
        url=f"https://github.com/{ORGANIZATION}/plugin-one/pull/1",
        head_branch="agents/plugin-one/task",
        base_branch="main",
        head_sha="c" * 40,
        base_sha="d" * 40,
    )
    assert commit.sha == "b" * 40
    assert pull_request.number == 1
    assert pull_request.url.endswith("/pull/1")
    assert pull_request.head_sha == "c" * 40
    assert pull_request.base_sha == "d" * 40
    with pytest.raises(ValueError):
        PullRequestMetadata(
            number=1,
            url=f"https://github.com/{ORGANIZATION}/plugin-one/pull/1",
            head_branch="agents/plugin-one/task",
            base_branch="agents/plugin-one/task",
            head_sha="c" * 40,
            base_sha="d" * 40,
        )
    with pytest.raises(ValueError):
        CreatePullRequestRequest(
            title="Update",
            head_branch="agents/plugin-one/task",
            base_branch="agents/plugin-one/task",
        )
    with pytest.raises(ValueError):
        PatchFile(path="../escape", operation="upsert", content="x")


def test_create_request_has_no_user_controlled_owner_field() -> None:
    field_names = {field.name for field in dataclasses.fields(CreatePrivateRepositoryRequest)}
    assert field_names == {"organization", "name", "description"}
    assert not any("owner" in field_name for field_name in field_names)
    with pytest.raises(ValueError):
        CreatePrivateRepositoryRequest(organization=ORGANIZATION, name="x" * 101)


def test_inline_private_key_is_not_accepted_as_configuration(
    private_key_pem: bytes,
    managed_organization,
) -> None:
    class UnusedSecretProvider:
        async def read_secret(self, reference: str) -> bytes:
            raise AssertionError("secret provider must not be called")

    with pytest.raises(GitHubConfigurationError):
        GitHubAppClient(
            app_id=APP_ID,
            private_key_secret_ref=private_key_pem.decode(),
            secret_provider=UnusedSecretProvider(),
            organizations=(managed_organization,),
        )
