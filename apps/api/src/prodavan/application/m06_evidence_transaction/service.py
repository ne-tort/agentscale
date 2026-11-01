"""Application service for one exclusive M06 evidence transaction."""

from __future__ import annotations

from collections.abc import Mapping

from .contracts import AuditOutcomeEvidenceV2, QuarantineAllowResult, TransactionContext
from .ports import EvidenceTransactionPort


class M06EvidenceTransactionService:
    """Validate the complete envelope and delegate exactly one atomic commit."""

    def __init__(self, *, transaction: EvidenceTransactionPort) -> None:
        self._transaction = transaction

    async def record(
        self, *, evidence: AuditOutcomeEvidenceV2, context: TransactionContext
    ) -> str:
        if _contains_forbidden_metadata(evidence.metadata):
            raise ValueError("raw arguments and secrets are forbidden in M06 evidence")
        if evidence.decision is QuarantineAllowResult.QUARANTINE and evidence.quarantine_or_allow is not QuarantineAllowResult.QUARANTINE:
            raise ValueError("quarantine evidence must remain quarantine")
        if context.tenant_id != evidence.tenant_id:
            raise ValueError("evidence tenant does not match transaction context")
        if context.writer_generation != evidence.identity.writer_generation:
            raise ValueError("writer generation does not match shared identity")
        return await self._transaction.commit_evidence_transaction(
            evidence=evidence,
            context=context,
        )


def _contains_forbidden_metadata(value: object) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized = key.casefold().replace("-", "_") if isinstance(key, str) else ""
            if normalized in {"arg", "args", "arguments", "raw_input", "payload", "secret", "secrets", "token", "credential", "credentials", "raw_arguments"}:
                return True
            if _contains_forbidden_metadata(item):
                return True
    elif isinstance(value, (list, tuple)):
        return any(_contains_forbidden_metadata(item) for item in value)
    return False


__all__ = ["M06EvidenceTransactionService"]
