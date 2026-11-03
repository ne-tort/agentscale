"""Fail-closed API integration contracts for runtime materialization."""

from .contracts import (
    OBSERVED_RUNTIME_API_ENDPOINTS,
    UNSUPPORTED_RUNTIME_API_ENDPOINTS,
    RuntimeApiAtomicExecutorPort,
    RuntimeApiBridgeLeaseReference,
    RuntimeApiDiagnostic,
    RuntimeApiEndpointContract,
    RuntimeApiEndpointKind,
    RuntimeApiEvidencePort,
    RuntimeApiExecutionCommand,
    RuntimeApiExecutionReceipt,
    RuntimeApiOutcome,
    RuntimeApiPreparedOperation,
    RuntimeApiPrepareRequest,
    RuntimeApiProjectBinding,
    RuntimeApiProjectScope,
    RuntimeApiResult,
    RuntimeApiScopeRepositoryPort,
    RuntimeApiStateMachinePort,
    endpoint_contract,
    is_atomic_materialization_endpoint,
)
from .coordinator import RuntimeApiCoordinator

__all__ = [
    "OBSERVED_RUNTIME_API_ENDPOINTS", "UNSUPPORTED_RUNTIME_API_ENDPOINTS", "RuntimeApiAtomicExecutorPort",
    "RuntimeApiBridgeLeaseReference", "RuntimeApiCoordinator", "RuntimeApiDiagnostic",
    "RuntimeApiEndpointContract", "RuntimeApiEndpointKind", "RuntimeApiEvidencePort",
    "RuntimeApiExecutionCommand", "RuntimeApiExecutionReceipt", "RuntimeApiOutcome",
    "RuntimeApiPreparedOperation", "RuntimeApiPrepareRequest", "RuntimeApiProjectBinding",
    "RuntimeApiProjectScope", "RuntimeApiResult", "RuntimeApiScopeRepositoryPort",
    "RuntimeApiStateMachinePort", "endpoint_contract", "is_atomic_materialization_endpoint",
]