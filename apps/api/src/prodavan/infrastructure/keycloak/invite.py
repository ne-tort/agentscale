"""Back-compat re-exports — prefer ``provisioning`` module."""

from prodavan.infrastructure.keycloak.provisioning import (  # noqa: F401
    ROLE_COMPANY,
    ROLE_EMPLOYEE,
    CompanyPrincipalResult,
    FakeIdentityProvisioning,
    FakeKeycloakInviteClient,
    IdentityProvisioningPort,
    InviteResult,
    KeycloakInvitePort,
    get_invite_client,
    get_provisioning,
    reset_invite_client,
    reset_provisioning,
)

__all__ = [
    "InviteResult",
    "CompanyPrincipalResult",
    "IdentityProvisioningPort",
    "FakeIdentityProvisioning",
    "KeycloakInvitePort",
    "FakeKeycloakInviteClient",
    "get_provisioning",
    "reset_provisioning",
    "get_invite_client",
    "reset_invite_client",
    "ROLE_COMPANY",
    "ROLE_EMPLOYEE",
]
