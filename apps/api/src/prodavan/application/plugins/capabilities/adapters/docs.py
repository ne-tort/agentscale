"""Company/plugin-scoped typed Mongo DocumentStore queries."""
from __future__ import annotations

import re
from typing import Any

from prodavan.application.plugins.capabilities.contracts import (
    DocsPredicate,
    DocsQueryOperator,
    DocsQueryRequest,
    DocsQueryResponse,
    DocsSortOrder,
    DocumentRef,
)
from prodavan.domain.document_store.types import validate_collection
from ._shared import (
    CloseOnce,
    CapabilityAccessDenied,
    CapabilityBackendError,
    CapabilityConfigurationError,
    usage,
    await_io,
    canonical_digest,
    canonical_json,
    request_envelope,
)

_FIELD = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_RESERVED = frozenset({"company_id", "plugin_instance_id", "_id"})


class DocumentStoreDocsAdapter(CloseOnce):
    """Use the existing DocumentStorePort, never a generic Mongo filter body."""

    def __init__(
        self,
        *,
        service: Any,
        allowed_namespaces: frozenset[str] = frozenset({"tenant_infra", "plugins"}),
        max_scan: int = 1000,
        timeout_seconds: float = 5.0,
        owns_service: bool = False,
    ) -> None:
        super().__init__(
            timeout_seconds=timeout_seconds,
            owned_resources=(service,) if owns_service else (),
        )
        if service is None or not callable(getattr(service, "find", None)):
            raise CapabilityConfigurationError("document_store_service_required")
        if not allowed_namespaces or any(
            not isinstance(item, str) or not item.strip() for item in allowed_namespaces
        ):
            raise CapabilityConfigurationError("docs_namespace_allowlist_required")
        if not 1 <= max_scan <= 1000:
            raise CapabilityConfigurationError("docs_scan_cap_invalid")
        self._service = service
        self._allowed_namespaces = frozenset(allowed_namespaces)
        self._max_scan = max_scan

    @staticmethod
    def _field(value: str) -> str:
        if not _FIELD.fullmatch(value) or value in _RESERVED:
            raise CapabilityAccessDenied("docs_field_not_allowed")
        return value

    def _predicate(self, predicate: DocsPredicate) -> dict[str, Any]:
        field = self._field(predicate.field)
        if len(predicate.values) > 100:
            raise CapabilityAccessDenied("docs_predicate_values_cap_exceeded")
        if predicate.operator is DocsQueryOperator.EQ:
            if len(predicate.values) != 1:
                raise CapabilityAccessDenied("docs_eq_requires_one_value")
            return {field: predicate.values[0]}
        if predicate.operator is DocsQueryOperator.IN:
            return {field: {"$in": list(predicate.values)}}
        if predicate.operator is DocsQueryOperator.RANGE:
            lower, upper = predicate.values
            return {field: {"$gte": lower, "$lte": upper}}
        if predicate.operator is DocsQueryOperator.EXISTS:
            if len(predicate.values) != 1 or predicate.values[0] not in ("true", "false"):
                raise CapabilityAccessDenied("docs_exists_requires_boolean")
            return {field: {"$exists": predicate.values[0] == "true"}}
        raise CapabilityAccessDenied("docs_operator_not_allowed")

    def _filter(self, request: DocsQueryRequest) -> dict[str, Any]:
        clauses: list[dict[str, Any]] = [
            {"company_id": request.envelope.company_id},
            {"plugin_instance_id": request.envelope.plugin_instance_id},
        ]
        clauses.extend(self._predicate(item) for item in request.predicates)
        return {"$and": clauses} if clauses else {}

    def _namespace_collection(self, request: DocsQueryRequest) -> tuple[str, str]:
        if request.namespace not in self._allowed_namespaces:
            raise CapabilityAccessDenied("docs_namespace_not_allowed")
        try:
            collection = validate_collection(request.collection)
        except ValueError:
            raise CapabilityAccessDenied("docs_collection_invalid") from None
        return request.namespace, collection

    def quota_usage(self, request: DocsQueryRequest) -> Any:
        return usage(rows=request.limit, bytes=71 * request.limit)

    async def query(self, request: DocsQueryRequest) -> DocsQueryResponse:
        envelope = request_envelope(request, DocsQueryRequest)
        self._ensure_open()
        namespace, collection = self._namespace_collection(request)
        for field in request.projection:
            self._field(field)
        if request.sort_field is not None:
            self._field(request.sort_field)
        scan_limit = self._max_scan if request.sort_field is not None else min(
            request.limit, self._max_scan
        )
        result = await await_io(
            self._service.find(
                namespace=namespace,
                collection=collection,
                filter=self._filter(request),
                limit=scan_limit,
                skip=0,
                company_id=envelope.company_id,
                cabinet_id=envelope.cabinet_id,
                project_id=envelope.project_id,
            ),
            timeout=self._timeout_seconds,
            code="document_store_query_failed",
        )
        items = list(getattr(result, "items", ()))
        matched = int(getattr(result, "count", -1))
        if matched < 0 or len(items) > scan_limit:
            raise CapabilityBackendError("document_store_invalid_page")
        if request.sort_field is not None and matched > len(items):
            # Never pretend that a page sorted only inside a truncated scan is
            # globally sorted. A dedicated Mongo sorted port is required beyond cap.
            raise CapabilityBackendError("docs_sorted_scan_cap_exceeded")
        for item in items:
            if (
                not isinstance(item, dict)
                or item.get("company_id") != envelope.company_id
                or item.get("plugin_instance_id") != envelope.plugin_instance_id
            ):
                raise CapabilityAccessDenied("docs_backend_tenant_leak")
        if request.sort_field is not None:
            field = request.sort_field
            items.sort(
                key=lambda item: canonical_json(item.get(field)),
                reverse=request.sort_order is DocsSortOrder.DESC,
            )
        items = items[: request.limit]
        documents: list[DocumentRef] = []
        for item in items:
            document_id = item.get("id", item.get("_id"))
            if not isinstance(document_id, str) or not document_id:
                document_id = str(document_id or "")
            if not document_id:
                raise CapabilityBackendError("docs_document_id_missing")
            projected = (
                {field: item[field] for field in request.projection if field in item}
                if request.projection
                else item
            )
            documents.append(DocumentRef(
                document_id=document_id,
                content_digest=canonical_digest(projected),
            ))
        return DocsQueryResponse(
            request_id=envelope.request_id,
            ok=True,
            digest=canonical_digest({
                "matched": matched,
                "documents": [
                    {"id": item.document_id, "digest": item.content_digest}
                    for item in documents
                ],
            }),
            documents=tuple(documents),
            matched=matched,
        )


__all__ = ["DocumentStoreDocsAdapter"]
