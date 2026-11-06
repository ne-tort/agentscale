"""Composition root for the M06 shadow gateway and bounded parity runner.

Nothing here may be imported by a request handler that authorizes a call: the
composition returns a shadow facade whose only output is evidence, and it refuses to
construct when the partition handoff gates are absent. Client-supplied capabilities,
scope, tenant, instance or ownership never become authorization input at any step:
the request only ever *asserts* identity, which is compared against server resolution
and rejected on any difference.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from prodavan.application.canonical_grants.ports import (
    AuthenticatedCompanyPort,
    CanonicalGrantReadPort,
)
from prodavan.application.capability_method_mapping.ports import (
    CapabilityMethodMappingReadPort,
    ProtocolCompatibilityReadPort,
)
from prodavan.application.dynamic_app_plugin_gateway.ports import (
    CapabilityQuotaPort,
    CapabilityScopePort,
    DynamicPluginRepository,
)
from prodavan.application.migration.m04_scope.scope_migration import ResolveScopePort
from prodavan.application.migration.m04e_definition_version.repository import (
    ServerDefinitionVersionRepository,
)
from prodavan.application.migration.m05_instance_runtime.runtime_service import (
    FailClosedResolvePluginInstance,
    M05UpstreamEvidence,
)

from .adapter import CapabilityGatewayService, EvidenceReadPort, TrustedEvidenceTransactionPort
from .canonical_decision_adapter import CanonicalGrantShadowAdapter
from .handoff import PartitionHandoffReadPort, ServerResolvedHandoffResolver
from .identity_adapter import ApplicationIdentityResolutionAdapter
from .legacy_decision_adapter import LegacyPluginGrantDecisionAdapter
from .method_mapping import ApprovedMethodOperationAdapter
from .parity_runner import BoundedParityRunner, ParityEvidenceSummaryPort, ParityReportStorePort


@dataclass(frozen=True, slots=True)
class ShadowGatewayPorts:
    """Every trusted port the shadow facade needs; no port is optional."""

    company: AuthenticatedCompanyPort
    m04_scope: ResolveScopePort
    m04e_definition_versions: ServerDefinitionVersionRepository
    m05_instances: FailClosedResolvePluginInstance
    m05_upstream: M05UpstreamEvidence
    handoff_partitions: PartitionHandoffReadPort
    legacy_repository: DynamicPluginRepository
    legacy_scope: CapabilityScopePort | None
    legacy_quota: CapabilityQuotaPort | None
    canonical_grants: CanonicalGrantReadPort
    method_mappings: CapabilityMethodMappingReadPort
    method_servers: dict[str, str]
    method_versions: dict[str, str]
    evidence_read: EvidenceReadPort
    evidence_transaction: TrustedEvidenceTransactionPort
    protocol: ProtocolCompatibilityReadPort | None = None


def build_shadow_gateway(ports: ShadowGatewayPorts) -> CapabilityGatewayService:
    """Assemble the evidence-only facade; the caller must keep the legacy owner live."""
    if ports.legacy_scope is None or ports.legacy_quota is None:
        raise ValueError("legacy scope and quota ports are required for exact parity")
    return CapabilityGatewayService(
        identity=ApplicationIdentityResolutionAdapter(
            company=ports.company, m04=ports.m04_scope,
            m04e=ports.m04e_definition_versions, m05=ports.m05_instances,
            m05_upstream=ports.m05_upstream,
        ),
        legacy=LegacyPluginGrantDecisionAdapter(
            repository=ports.legacy_repository, scope=ports.legacy_scope, quota=ports.legacy_quota,
        ),
        canonical_shadow=CanonicalGrantShadowAdapter(grants=ports.canonical_grants),
        handoffs=ServerResolvedHandoffResolver(partitions=ports.handoff_partitions),
        evidence_read=ports.evidence_read,
        evidence_transaction=ports.evidence_transaction,
        method_mapping=ApprovedMethodOperationAdapter(
            mappings=ports.method_mappings, method_servers=ports.method_servers,
            method_versions=ports.method_versions, protocol=ports.protocol,
        ),
    )


def build_parity_runner(*, observations: Any, summaries: ParityEvidenceSummaryPort,
                        reports: ParityReportStorePort | None = None) -> BoundedParityRunner:
    """Assemble the bounded runner; report persistence is required for recorded runs."""
    return BoundedParityRunner(observations=observations, summaries=summaries, reports=reports)


def caller_assertion_report(*, gateway: CapabilityGatewayService) -> dict[str, str]:
    """Disclose the ownership invariants this composition guarantees."""
    if gateway.DECISION_OWNER != "legacy" or gateway.CANONICAL_READ_OWNER != "legacy":
        raise ValueError("shadow composition must not claim canonical ownership")
    if gateway.CANONICAL_POSITIVE_WRITES is not False:
        raise ValueError("shadow composition must not enable canonical positive writes")
    return {
        "caller_capabilities": "request assertion only; absence or presence never authorizes",
        "caller_ownership": "request assertion only; compared to server resolution, never trusted",
        "decision_owner": gateway.DECISION_OWNER,
        "canonical_read_owner": gateway.CANONICAL_READ_OWNER,
        "canonical_positive_writes": "false",
    }


__all__ = [
    "ShadowGatewayPorts",
    "build_parity_runner",
    "build_shadow_gateway",
    "caller_assertion_report",
]
