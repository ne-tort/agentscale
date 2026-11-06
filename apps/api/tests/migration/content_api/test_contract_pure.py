"""Pure Content API contract tests — no database, no HTTP.

These assert the fail-closed decisions directly: tenant boundary, ACL entry
validation, idempotency projections, cursor pagination and download filename
sanitisation. They are the executable half of ``application/content/CONTRACT.md``.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from prodavan.application.content.contracts import (
    ARCHIVE_BLOCKER,
    ARCHIVE_BLOCKER_CODE,
    PUBLIC_LINK_KINDS,
    SUPPORTED_PRINCIPAL_KINDS,
    ContentErrorCode,
    ContentLinkKind,
    ContentPrincipalKind,
    acl_duplicate_keys,
    acl_grant_shallow_problem,
    alias_visible_to_tenants,
    asset_visible_to_tenants,
    canonical_alias_request,
    canonical_alias_row,
    canonical_asset_request,
    canonical_asset_row,
    digest_of,
    link_kind_is_caller_assignable,
    normalize_link_kind,
    projections_match,
)
from prodavan.application.content.download_service import safe_filename
from prodavan.application.content.pagination import decode_cursor, encode_cursor
from prodavan.application.content.schemas import (
    AclEntryBody,
    BeginVersionBody,
    CreateAliasBody,
    CreateAssetBody,
    CreateAssetLinkBody,
    PatchAssetBody,
    ReplaceAclBody,
)


# --- tenant boundary --------------------------------------------------------


def test_company_asset_is_invisible_outside_its_tenant() -> None:
    assert asset_visible_to_tenants(
        owner_scope="company",
        owner_company_id="co_a",
        tenant_ids={"co_a"},
        is_platform_admin=False,
    )
    assert not asset_visible_to_tenants(
        owner_scope="company",
        owner_company_id="co_a",
        tenant_ids={"co_b"},
        is_platform_admin=False,
    )


def test_platform_asset_crosses_tenant_boundary_and_leaves_it_to_the_acl() -> None:
    assert asset_visible_to_tenants(
        owner_scope="platform",
        owner_company_id=None,
        tenant_ids={"co_b"},
        is_platform_admin=False,
    )


def test_platform_admin_bypasses_the_tenant_boundary() -> None:
    assert asset_visible_to_tenants(
        owner_scope="company",
        owner_company_id="co_a",
        tenant_ids=set(),
        is_platform_admin=True,
    )


def test_company_asset_without_owner_id_is_never_visible() -> None:
    """A company-scoped row with a NULL owner must not be readable by anyone."""
    assert not asset_visible_to_tenants(
        owner_scope="company",
        owner_company_id=None,
        tenant_ids={"co_a", "co_b"},
        is_platform_admin=False,
    )


def test_alias_visibility_mirrors_asset_visibility() -> None:
    assert alias_visible_to_tenants(
        owner_company_id="co_a", tenant_ids={"co_a"}, is_platform_admin=False
    )
    assert not alias_visible_to_tenants(
        owner_company_id="co_a", tenant_ids={"co_b"}, is_platform_admin=False
    )
    assert alias_visible_to_tenants(
        owner_company_id=None, tenant_ids={"co_b"}, is_platform_admin=False
    )


# --- ACL entry validation ---------------------------------------------------


@pytest.mark.parametrize(
    "kind",
    ["employee", "company", "cabinet", "project", "role"],
)
def test_every_documented_principal_kind_is_accepted(kind: str) -> None:
    assert kind in SUPPORTED_PRINCIPAL_KINDS
    problem = acl_grant_shallow_problem(
        principal_kind=kind,
        principal_id="member" if kind == "role" else "co_a",
        permission="read",
        resource_tenant_id="co_a",
        actor_is_platform_admin=False,
    )
    assert problem is None


def test_platform_principal_kind_is_accepted_only_from_a_platform_admin() -> None:
    """``platform`` is a documented kind but never grantable by a tenant admin.

    It was wrongly folded into the parametrised "every kind is accepted" case, which asked
    for a successful grant with ``actor_is_platform_admin=False`` — the exact situation
    ``CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN`` exists to refuse (see
    ``test_platform_principal_grant_requires_a_platform_admin``).
    """
    assert "platform" in SUPPORTED_PRINCIPAL_KINDS
    assert (
        acl_grant_shallow_problem(
            principal_kind="platform",
            principal_id="platform",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=False,
        )
        == ContentErrorCode.CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN.value
    )
    assert (
        acl_grant_shallow_problem(
            principal_kind="platform",
            principal_id="platform",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=True,
        )
        is None
    )


def test_unknown_principal_kind_is_rejected() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="tenant_group",
            principal_id="g1",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=True,
        )
        == ContentErrorCode.CONTENT_ACL_PRINCIPAL_UNSUPPORTED.value
    )


def test_unknown_permission_is_rejected() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="company",
            principal_id="co_a",
            permission="execute",
            resource_tenant_id="co_a",
            actor_is_platform_admin=True,
        )
        == ContentErrorCode.CONTENT_ACL_PERMISSION_UNSUPPORTED.value
    )


def test_company_grant_outside_the_resource_tenant_is_rejected() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="company",
            principal_id="co_b",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=False,
        )
        == ContentErrorCode.CONTENT_ACL_CROSS_TENANT.value
    )
    # Only a platform admin may publish across tenants.
    assert (
        acl_grant_shallow_problem(
            principal_kind="company",
            principal_id="co_b",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=True,
        )
        is None
    )


def test_cabinet_grant_on_a_platform_asset_needs_a_platform_admin() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="cabinet",
            principal_id="cab_1",
            permission="read",
            resource_tenant_id=None,
            actor_is_platform_admin=False,
        )
        == ContentErrorCode.CONTENT_ACL_CROSS_TENANT.value
    )


def test_platform_principal_grant_requires_a_platform_admin() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="platform",
            principal_id="platform",
            permission="read",
            resource_tenant_id=None,
            actor_is_platform_admin=False,
        )
        == ContentErrorCode.CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN.value
    )
    assert (
        acl_grant_shallow_problem(
            principal_kind="platform",
            principal_id="platform",
            permission="read",
            resource_tenant_id=None,
            actor_is_platform_admin=True,
        )
        is None
    )


def test_platform_principal_id_must_be_literal() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="platform",
            principal_id="co_a",
            permission="read",
            resource_tenant_id=None,
            actor_is_platform_admin=True,
        )
        == ContentErrorCode.VALIDATION_ERROR.value
    )


def test_platform_role_grant_requires_a_platform_admin() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="role",
            principal_id="platform.admin",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=False,
        )
        == ContentErrorCode.CONTENT_ACL_PLATFORM_GRANT_FORBIDDEN.value
    )


def test_unknown_role_name_is_rejected() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="role",
            principal_id="superuser",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=True,
        )
        == ContentErrorCode.CONTENT_ACL_PRINCIPAL_UNSUPPORTED.value
    )


def test_empty_principal_id_is_rejected() -> None:
    assert (
        acl_grant_shallow_problem(
            principal_kind="employee",
            principal_id="   ",
            permission="read",
            resource_tenant_id="co_a",
            actor_is_platform_admin=True,
        )
        == ContentErrorCode.VALIDATION_ERROR.value
    )


def test_duplicate_acl_triples_are_detected() -> None:
    duplicates = acl_duplicate_keys(
        [
            {"principal_kind": "employee", "principal_id": "emp_1", "permission": "read"},
            {"principal_kind": "employee", "principal_id": "emp_1", "permission": "read"},
            {"principal_kind": "employee", "principal_id": "emp_1", "permission": "write"},
        ]
    )
    assert duplicates == {("employee", "emp_1", "read")}


# --- idempotency projections ------------------------------------------------


def test_asset_projection_is_order_insensitive_for_tags() -> None:
    left = canonical_asset_request(
        owner_company_id="co_a",
        visibility="company",
        mime="text/plain",
        title="readme",
        tags=["b", "a"],
    )
    right = canonical_asset_request(
        owner_company_id="co_a",
        visibility="company",
        mime="text/plain",
        title="readme",
        tags=["a", "b", "a"],
    )
    assert projections_match(left, right)


def test_asset_projection_detects_a_changed_field() -> None:
    base = canonical_asset_request(
        owner_company_id="co_a", visibility="company", mime="text/plain", title="a", tags=[]
    )
    changed = canonical_asset_request(
        owner_company_id="co_a", visibility="private", mime="text/plain", title="a", tags=[]
    )
    assert not projections_match(base, changed)


def test_asset_row_projection_matches_the_request_projection() -> None:
    class Row:
        owner_company_id = "co_a"
        visibility = "company"
        mime = None
        title = "spec"
        tags = ["x"]

    assert projections_match(
        canonical_asset_row(Row()),
        canonical_asset_request(
            owner_company_id="co_a",
            visibility="company",
            mime=None,
            title="spec",
            tags=["x"],
        ),
    )


def test_alias_projection_normalises_metadata_key_order() -> None:
    left = canonical_alias_request(
        slug="docs.readme",
        owner_company_id="co_a",
        visibility="company",
        label=None,
        description=None,
        metadata={"b": 1, "a": 2},
    )
    right = canonical_alias_request(
        slug="docs.readme",
        owner_company_id="co_a",
        visibility="company",
        label=None,
        description=None,
        metadata={"a": 2, "b": 1},
    )
    assert projections_match(left, right)


def test_alias_projection_detects_a_changed_slug() -> None:
    class Row:
        slug = "docs.readme"
        owner_company_id = "co_a"
        visibility = "company"
        label = None
        description = None
        metadata_: dict = {}

    assert not projections_match(
        canonical_alias_row(Row()),
        canonical_alias_request(
            slug="docs.other",
            owner_company_id="co_a",
            visibility="company",
            label=None,
            description=None,
            metadata={},
        ),
    )


def test_digest_helper_is_sha256_hex() -> None:
    assert digest_of(b"abc") == (
        "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    )


# --- link kinds -------------------------------------------------------------


def test_documents_link_kind_is_domain_owned_and_not_caller_assignable() -> None:
    assert not link_kind_is_caller_assignable(ContentLinkKind.DOCUMENTS.value)
    assert link_kind_is_caller_assignable(ContentLinkKind.PROJECT.value)
    assert link_kind_is_caller_assignable(ContentLinkKind.PROJECT_ATTACHMENT.value)
    assert PUBLIC_LINK_KINDS == {
        "project_attachment",
        "project",
        "module",
        "module_catalog",
    }


def test_link_kind_is_normalised_before_comparison() -> None:
    assert normalize_link_kind(" PROJECT ") == "project"
    assert link_kind_is_caller_assignable(normalize_link_kind("Module"))


# --- pagination -------------------------------------------------------------


def test_cursor_round_trips_time_and_id() -> None:
    moment = datetime(2026, 11, 1, 12, 30, tzinfo=UTC)
    cursor = encode_cursor(created_at=moment, row_id="cast_1")
    assert decode_cursor(cursor) == (moment, "cast_1")


def test_cursor_round_trips_a_version_number() -> None:
    cursor = encode_cursor(created_at=None, row_id="7")
    decoded = decode_cursor(cursor)
    assert decoded is not None
    assert decoded[0] is None
    assert int(decoded[1]) == 7


@pytest.mark.parametrize("bad", ["", None])
def test_absent_cursor_is_not_an_error(bad: str | None) -> None:
    assert decode_cursor(bad) is None


@pytest.mark.parametrize("bad", ["!!!not-base64!!!", "eyJ4IjoxfQ", "Zm9v"])
def test_malformed_cursor_is_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        decode_cursor(bad)


# --- download filenames -----------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("spec.xlsx", "spec.xlsx"),
        ("../../etc/passwd", "passwd"),
        ("..\\..\\windows\\system32\\cmd.exe", "cmd.exe"),
        ("bad\x00name\n.txt", "badname.txt"),
        ("", None),
        (None, None),
    ],
)
def test_download_filename_is_sanitised(raw: str | None, expected: str | None) -> None:
    assert safe_filename(raw) == expected


# --- schemas ----------------------------------------------------------------


def test_asset_body_rejects_unknown_fields_and_bad_visibility() -> None:
    with pytest.raises(ValidationError):
        CreateAssetBody.model_validate({"owner_company_id": "co_a", "owner_scope": "platform"})
    with pytest.raises(ValidationError):
        CreateAssetBody.model_validate({"owner_company_id": "co_a", "visibility": "public"})


def test_asset_body_limits_tags_and_tag_length() -> None:
    with pytest.raises(ValidationError):
        CreateAssetBody.model_validate({"tags": ["x" * 65]})
    with pytest.raises(ValidationError):
        PatchAssetBody.model_validate({"tags": ["t"] * 65})


def test_asset_body_accepts_a_platform_owned_asset_request() -> None:
    body = CreateAssetBody.model_validate({"title": "shared.pdf"})
    assert body.owner_company_id is None
    assert body.visibility == "company"


def test_version_body_requires_a_full_sha256_when_supplied() -> None:
    with pytest.raises(ValidationError):
        BeginVersionBody.model_validate({"sha256": "abc"})
    with pytest.raises(ValidationError):
        BeginVersionBody.model_validate({"ttl_seconds": 5})
    with pytest.raises(ValidationError):
        BeginVersionBody.model_validate({"ttl_seconds": 7200})
    assert BeginVersionBody.model_validate({"mime": "text/plain"}).ttl_seconds == 900


def test_alias_body_rejects_unknown_visibility_and_short_slug() -> None:
    with pytest.raises(ValidationError):
        CreateAliasBody.model_validate({"slug": "ab"})
    with pytest.raises(ValidationError):
        CreateAliasBody.model_validate({"slug": "docs.readme", "visibility": "global"})


def test_acl_body_restricts_kinds_permissions_and_extra_keys() -> None:
    with pytest.raises(ValidationError):
        AclEntryBody.model_validate({"principal_kind": "group", "principal_id": "g"})
    with pytest.raises(ValidationError):
        AclEntryBody.model_validate(
            {"principal_kind": "company", "principal_id": "co_a", "permission": "own"}
        )
    with pytest.raises(ValidationError):
        AclEntryBody.model_validate(
            {"principal_kind": "company", "principal_id": "co_a", "note": "hi"}
        )
    body = ReplaceAclBody.model_validate(
        {"entries": [{"principal_kind": "role", "principal_id": "member"}]}
    )
    assert body.entries[0].permission == "read"


def test_link_body_only_allows_caller_assignable_kinds() -> None:
    with pytest.raises(ValidationError):
        CreateAssetLinkBody.model_validate({"link_kind": "documents", "link_id": "d1"})
    with pytest.raises(ValidationError):
        CreateAssetLinkBody.model_validate({"link_kind": "project", "link_id": ""})


# --- stable codes and blockers ---------------------------------------------


def test_error_code_catalogue_is_stable() -> None:
    assert ContentErrorCode.IDEMPOTENCY_REPLAY.value == "IDEMPOTENCY_REPLAY"
    assert ContentErrorCode.CONTENT_VERSION_SEALED.value == "CONTENT_VERSION_SEALED"
    assert ContentErrorCode.CONTENT_DIGEST_MISMATCH.value == "CONTENT_DIGEST_MISMATCH"
    assert ContentErrorCode.CONTENT_ARCHIVE_UNAVAILABLE.value == ARCHIVE_BLOCKER_CODE
    assert ContentErrorCode.NOT_FOUND.value == "NOT_FOUND"
    assert ContentErrorCode.FORBIDDEN.value == "FORBIDDEN"


def test_archive_blocker_names_the_missing_columns() -> None:
    assert "content_assets" in ARCHIVE_BLOCKER
    assert "archived_at" in ARCHIVE_BLOCKER
    assert "No DDL" not in ARCHIVE_BLOCKER  # blocker is about columns, not process


def test_principal_kind_enum_matches_the_supported_set() -> None:
    assert {kind.value for kind in ContentPrincipalKind} == set(SUPPORTED_PRINCIPAL_KINDS)
