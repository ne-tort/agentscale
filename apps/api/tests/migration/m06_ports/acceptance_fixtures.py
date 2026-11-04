"""Fixtures for ``ShadowGatewayPorts`` assembly (``MIG-M06-PORTS-01``).

Two rules, the same ones the neighbouring migration suites keep.

**1. The session factory refuses to be called.** Assembly composes; it must not
open a session, read a page or take a decision. :class:`RefusingSessionFactory`
counts its calls and raises on the first one, so "assembled" can never quietly
mean "ran".

**2. Only real construction where real construction is possible.** The builder
and the facade under test are the production ones, and every port the builder
derives is the production adapter. The doubles below replace exactly the
collaborators a *process* cannot own (a tenant-context binder, one snapshot's
``M05UpstreamEvidence``, a caller-owned definition repository, the method
registry, the quota port) and are inert: calling one fails the test loudly.
"""

from __future__ import annotations

from typing import Any

from prodavan.application.capability_method_mapping.contracts import (
    CapabilityMethodMapping,
    MethodIdentity,
    ResourceDomainConstraint,
)
from prodavan.application.migration.m04e_definition_version.repository import (
    ServerDefinitionVersionRepository,
)
from prodavan.application.migration.m06_grants_gateway.method_mapping import (
    DeclaredMappingReadPort,
)

#: One partition, stated the way a partition key is stated everywhere else.
TENANT = "comp_aaaaaaaaaaaaaaaaaaaa"
DEFINITION = "app_aaaaaaaaaaaaaaaaaaaa"
INSTANCE = "ains_aaaaaaaaaaaaaaaaaaaa"
VERSION = "aver_aaaaaaaaaaaaaaaaaaaa"

#: The partition key's tuple form — ``build_shadow_ports`` accepts this or a key.
PARTITION = (TENANT, DEFINITION)
WRITER_GENERATION = 3

METHOD = "kv.put"


class RefusingSessionFactory:
    """Callable factory that records and refuses every attempt to open a session."""

    def __init__(self) -> None:
        self.opened = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.opened += 1
        raise AssertionError(
            "assembling ShadowGatewayPorts must not open a database session"
        )

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"RefusingSessionFactory(opened={self.opened})"


class Inert:
    """A stated collaborator on which any method call is a test failure."""

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return "Inert()"

    def __getattr__(self, attribute: str) -> Any:
        def refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise AssertionError(
                f"{attribute} was executed: composing a port set must not run a port"
            )

        return refuse


def definition_repository() -> ServerDefinitionVersionRepository:
    """The **real** M04E repository around an inert resolver.

    Its constructor is pure and the composition treats the resolver as
    caller-owned state, so the production class is more faithful than a fake here.
    """

    return ServerDefinitionVersionRepository(Inert())


def method_mappings() -> DeclaredMappingReadPort:
    """A registry declaration the M06 adapter accepts.

    Built from the real contracts (``CapabilityMethodMapping`` validates itself),
    so the assembled method port is configured exactly as production would be.
    """

    return DeclaredMappingReadPort(
        (
            CapabilityMethodMapping(
                identity=MethodIdentity(server="server-1", tool=METHOD, version="1.0.0"),
                capability_key="storage.kv",
                operation="write",
                constraints=ResourceDomainConstraint(
                    resources=("drafts",), domains=("storage",)
                ),
                supported_protocol_version="mcp-1",
                tool_version_compatibility=("1.0.0",),
            ),
        )
    )


def method_configuration() -> tuple[dict[str, str], dict[str, str]]:
    """The server-owned half of the method decision: one server and one version."""

    return {METHOD: "server-1"}, {METHOD: "1.0.0"}


def full_ports(**overrides: Any):
    """``build_shadow_ports`` with every collaborator a host must state supplied.

    The keyword arguments are exactly the *stated* half of the builder's
    signature; the derived half comes from ``session_factory`` and the partition.
    An override with the value ``None`` states absence on purpose, which is how the
    per-field refusal tests are written.
    """

    from prodavan.application.migration.m06_grants_gateway.ports_assembly import (
        build_shadow_ports,
    )

    servers, versions = method_configuration()
    values: dict[str, Any] = {
        "session_factory": RefusingSessionFactory(),
        "partition": PARTITION,
        "writer_generation": WRITER_GENERATION,
        "m04_tenant_context": Inert(),
        "m05_tenant_context": Inert(),
        "m04e_definition_versions": definition_repository(),
        "m05_upstream": Inert(),
        "legacy_quota": Inert(),
        "method_mappings": method_mappings(),
        "method_servers": servers,
        "method_versions": versions,
    }
    values.update(overrides)
    return build_shadow_ports(**values)


__all__ = [
    "DEFINITION",
    "INSTANCE",
    "Inert",
    "METHOD",
    "PARTITION",
    "RefusingSessionFactory",
    "TENANT",
    "VERSION",
    "WRITER_GENERATION",
    "definition_repository",
    "full_ports",
    "method_configuration",
    "method_mappings",
]
