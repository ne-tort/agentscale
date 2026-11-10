"""Private template generation, allowlist and immutable metadata tests."""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from prodavan.infrastructure.plugins.sources import (
    CreatePrivateRepositoryRequest,
    GitHubOrganizationNotAllowed,
    GitHubResponseError,
    GitHubValidationError,
)

from .conftest import DEFAULT_REPO_PAYLOAD, ORGANIZATION, TEMPLATE, FakeGitHub, MutableClock


@pytest.mark.asyncio
async def test_create_private_repository_from_verified_template(
    make_client,
    clock: MutableClock,
) -> None:
    fake = FakeGitHub(clock)
    client = make_client(fake.handler, clock=clock)

    created = await client.create_private_repository(
        CreatePrivateRepositoryRequest(
            organization=ORGANIZATION,
            name="plugin-one",
            description="Managed private plugin repository",
        )
    )

    assert fake.template_checked is True
    assert fake.generate_payload == {
        "owner": ORGANIZATION,
        "name": "plugin-one",
        "description": "Managed private plugin repository",
        "private": True,
        "include_all_branches": False,
    }
    assert created.repo_id == DEFAULT_REPO_PAYLOAD["id"]
    assert created.full_name == f"{ORGANIZATION}/plugin-one"
    assert created.default_branch == "main"
    assert created.private is True
    assert created.template == TEMPLATE
    assert created.template.agents_revision == "a" * 40
    assert created.template.security_revision == "b" * 40
    with pytest.raises(FrozenInstanceError):
        created.repo_id = 1


@pytest.mark.asyncio
async def test_forbidden_organization_fails_before_any_http(
    make_client,
    clock: MutableClock,
) -> None:
    fake = FakeGitHub(clock)
    client = make_client(fake.handler, clock=clock)
    request = CreatePrivateRepositoryRequest(organization="somebody-else", name="plugin-one")

    with pytest.raises(GitHubOrganizationNotAllowed):
        await client.create_private_repository(request)
    assert fake.requests == []


@pytest.mark.parametrize(
    "name",
    ["", "repo/name", "../repo", ".hidden", "repo.git", "repo..name", "bad name", "a" * 101],
)
def test_repository_name_is_strictly_validated(name: str) -> None:
    with pytest.raises(GitHubValidationError):
        CreatePrivateRepositoryRequest(organization=ORGANIZATION, name=name)


@pytest.mark.parametrize("owner", ["", "../owner", "owner/name", "owner name", "-owner", "owner-", "a--b"])
def test_organization_name_is_strictly_validated(owner: str) -> None:
    with pytest.raises(GitHubValidationError):
        CreatePrivateRepositoryRequest(organization=owner, name="plugin-one")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("private", False),
        ("is_template", False),
        ("full_name", "attacker/not-the-template"),
    ],
)
async def test_template_must_be_the_expected_private_template(
    make_client,
    clock: MutableClock,
    field: str,
    value: object,
) -> None:
    template_payload = {
        "id": 123,
        "full_name": f"{TEMPLATE.owner}/{TEMPLATE.name}",
        "private": True,
        "is_template": True,
    }
    template_payload[field] = value
    fake = FakeGitHub(clock, template_payload=template_payload)
    client = make_client(fake.handler, clock=clock)

    with pytest.raises(GitHubResponseError):
        await client.create_private_repository(
            CreatePrivateRepositoryRequest(organization=ORGANIZATION, name="plugin-one")
        )
    assert fake.generate_payload is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        {**DEFAULT_REPO_PAYLOAD, "full_name": "attacker/plugin-one"},
        {**DEFAULT_REPO_PAYLOAD, "private": False},
        {**DEFAULT_REPO_PAYLOAD, "default_branch": ""},
        {**DEFAULT_REPO_PAYLOAD, "id": True},
    ],
)
async def test_created_repository_response_invariants_are_fail_closed(
    make_client,
    clock: MutableClock,
    payload: dict[str, object],
) -> None:
    fake = FakeGitHub(clock, repository_payload=payload)
    client = make_client(fake.handler, clock=clock)

    with pytest.raises(GitHubResponseError):
        await client.create_private_repository(
            CreatePrivateRepositoryRequest(organization=ORGANIZATION, name="plugin-one")
        )
