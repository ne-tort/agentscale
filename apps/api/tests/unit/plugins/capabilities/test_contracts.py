"""Unit-тесты typed контрактов capability-адаптеров плагинов (F2).

acceptance №11 (redaction), ADR-002 (запрет generic dispatch), §4.3 ( typed
поля, ``db.direct`` не через gateway), §4.4 (реестр имён).

Тесты НАПИСАНЫ, НО НЕ ЗАПУСКАЮТСЯ: верификация заморожена до явного решения
родителя (docs/migration/plugin-migration-plan.md §0.2, §0.6).
"""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime

import pytest

from prodavan.application.plugins.capabilities.contracts import (
    DB_DIRECT,
    PLUGIN_CAPABILITIES,
    CacheGetRequest,
    CacheSetRequest,
    CapabilityEnvelope,
    CapabilityName,
    DocsPredicate,
    DocsQueryOperator,
    DocsQueryRequest,
    McpToolDefinition,
    NetworkEgressRequest,
    NetworkHttpMethod,
    ObjectPermission,
    ObjectsScopedCredentialsRequest,
    PodActionName,
    PodsInvokeRequest,
    TypedContractError,
    empty_digest,
)
from prodavan.application.plugins.capabilities.ports import (
    CAPABILITY_OPERATIONS,
    AdapterMissingError,
    AdapterRegistry,
    UnknownCapabilityError,
)

DIGEST = "sha256:" + "a" * 64
OTHER_DIGEST = "sha256:" + "b" * 64
NOW = datetime(2026, 1, 1, tzinfo=UTC)


def envelope() -> CapabilityEnvelope:
    return CapabilityEnvelope(
        request_id="req-1",
        correlation_id="corr-1",
        company_id="co_123",
        cabinet_id="cab_456",
        subject_id="plugin:mod_equipment:co_123:inst_abc",
        plugin_instance_id="inst_abc",
        artifact_digest=DIGEST,
        grant_id="grant_1",
    )


# ---------------------------------------------------------------------------
# 1. Typed контракты НЕ принимают произвольный sql / command / args.
# ---------------------------------------------------------------------------


def test_typed_contracts_have_no_generic_dispatch_fields() -> None:
    """ADR-002: ни один Request/Response не имеет sql/command/args/params/payload/body."""
    import prodavan.application.plugins.capabilities.contracts as contracts

    forbidden = {
        "sql", "query_text", "command", "args", "argv", "params",
        "parameters", "payload", "body", "raw", "bytes", "blob", "base64",
        "script", "expression", "filter",
    }
    request_classes = [
        value for value in vars(contracts).values()
        if isinstance(value, type)
        and dataclasses.is_dataclass(value)
        and getattr(value, "__name__", "").endswith("Request")
    ]
    assert request_classes, "должны существовать typed Request-контракты"
    for cls in request_classes:
        field_names = {item.name for item in dataclasses.fields(cls)}
        assert not (field_names & forbidden), f"{cls.__name__} содержит generic dispatch: {field_names & forbidden}"
        # Ни одного свободного Mapping[str, Any] / dict в полях.
        for item in dataclasses.fields(cls):
            annotation = item.type
            if isinstance(annotation, str):
                assert "Any" not in annotation and "Mapping" not in annotation, (
                    f"{cls.__name__}.{item.name} не типизирован"
                )


def test_cache_get_request_rejects_arbitrary_sql_keyword_argument() -> None:
    """Прямая попытка передать ``sql`` в typed контракт — TypeError, а не тихий success."""
    with pytest.raises(TypeError):
        CacheGetRequest(key="k", envelope=envelope(), sql="SELECT * FROM users")  # type: ignore[call-arg]


def test_cache_set_request_rejects_command_and_args() -> None:
    """``{"command": ..., "args": ...}`` не является контрактом (§4.3)."""
    with pytest.raises(TypeError):
        CacheSetRequest(  # type: ignore[call-arg]
            key="k", value="v", envelope=envelope(), command="rm", args=["-rf", "/"],
        )


def test_docs_query_is_typed_not_raw_mongo_json() -> None:
    """Docs query — список типизированных предикатов, а не произвольный dict-фильтр."""
    predicate = DocsPredicate(field="status", operator=DocsQueryOperator.EQ, values=("active",))
    request = DocsQueryRequest(
        namespace="co_123", collection="equipment", predicates=(predicate,),
        limit=10, envelope=envelope(),
    )
    assert request.predicates[0].operator is DocsQueryOperator.EQ
    with pytest.raises(TypedContractError):
        DocsPredicate(field="x", operator=DocsQueryOperator.RANGE, values=("only-one",))
    with pytest.raises(TypeError):
        DocsPredicate(  # type: ignore[call-arg]
            field="x", operator=DocsQueryOperator.EQ, values=("a",), sql="SELECT 1",
        )


def test_pods_invoke_rejects_free_form_action_and_command() -> None:
    """Плагин не имеет k8s API: действие — закрытый enum, ``command`` запрещён."""
    with pytest.raises(TypedContractError):
        PodsInvokeRequest(  # type: ignore[arg-type]
            pod_id="pod-1", action="kubectl exec rm -rf /", target_ref="x", envelope=envelope(),
        )
    with pytest.raises(TypeError):
        PodsInvokeRequest(  # type: ignore[call-arg]
            pod_id="pod-1", action=PodActionName.READ_STATUS, target_ref="x",
            envelope=envelope(), command="sh", args=["-c", "curl evil"],
        )


def test_docs_query_request_rejects_untyped_predicates() -> None:
    """Предикат — DocsPredicate, а не произвольный dict/Mongo-фильтр."""
    with pytest.raises(TypedContractError):
        DocsQueryRequest(
            namespace="co_123", collection="equipment",
            predicates=({"sql": "SELECT 1"},),  # type: ignore[arg-type]
            limit=10, envelope=envelope(),
        )


def test_network_egress_has_no_timeout_or_allowed_hosts_from_request() -> None:
    """Таймаут и allowed_hosts задаются сервером из grant'а, не телом/JWT (§4.4, кр.22)."""
    request = NetworkEgressRequest(
        host="api.example.com", port=443, path="/v1/items",
        method=NetworkHttpMethod.GET, body_digest=empty_digest(), envelope=envelope(),
    )
    field_names = {item.name for item in dataclasses.fields(request)}
    assert "timeout_ms" not in field_names
    assert "allowed_hosts" not in field_names
    assert "headers" not in field_names  # только digest тела, никаких PII/токенов


def test_mcp_tool_must_map_to_its_own_capability() -> None:
    """MCP = адаптер к gateway: tool_name не превращается в авторизацию (§4.3)."""
    with pytest.raises(TypedContractError):
        McpToolDefinition(
            tool_name="drop_all", tool_version="1.0", capability=CapabilityName.CACHE,
            operation="docs.query", input_schema_digest=DIGEST,
        )
    tool = McpToolDefinition(
        tool_name="cache_get", tool_version="1.0", capability=CapabilityName.CACHE,
        operation="cache.get", input_schema_digest=DIGEST,
    )
    assert tool.capability is CapabilityName.CACHE


def test_objects_contract_never_carries_base64_bytes() -> None:
    """Объекты: только scoped credentials / ключи / digest'ы, не base64-байты (§4.3)."""
    import prodavan.application.plugins.capabilities.contracts as contracts

    for name in ("ObjectsScopedCredentialsRequest", "ObjectsListRequest", "ObjectsDeleteRequest"):
        cls = getattr(contracts, name)
        field_names = {item.name for item in dataclasses.fields(cls)}
        assert not ({"data", "content", "bytes", "base64", "blob", "file"} & field_names), name
    credentials = ObjectsScopedCredentialsRequest(
        bucket="tenant-co_123", key_prefix="mod_equipment/",
        permissions=frozenset({ObjectPermission.READ}), ttl_seconds=900, envelope=envelope(),
    )
    assert credentials.ttl_seconds == 900


def test_db_direct_is_not_a_gateway_capability() -> None:
    """``db.direct`` отсутствует среди адаптеров: БД = прямой DSN (§2, §4.3, ADR-007)."""
    assert DB_DIRECT == "db.direct"
    assert DB_DIRECT not in PLUGIN_CAPABILITIES
    assert DB_DIRECT not in CAPABILITY_OPERATIONS
    assert "db" not in {item.value for item in CapabilityName}


def test_secrets_read_contract_has_no_plaintext_field() -> None:
    """Чтение секрета по ``secret_ref``; plaintext/token не покидает контур."""
    import prodavan.application.plugins.capabilities.contracts as contracts

    cls = contracts.SecretsReadResponse
    field_names = {item.name for item in dataclasses.fields(cls)}
    assert not ({"value", "secret", "token", "plaintext", "password"} & field_names)
    assert "value_digest" in field_names


def test_jobs_contracts_carry_job_id_run_id_and_lease() -> None:
    """submit/get/cancel с ``job_id``, ``run_id``, lease (§4.3, §7.3)."""
    import prodavan.application.plugins.capabilities.contracts as contracts

    assert "job_id" in {f.name for f in dataclasses.fields(contracts.JobsSubmitResponse)}
    assert "run_id" in {f.name for f in dataclasses.fields(contracts.JobsGetRequest)}
    assert "lease" in {f.name for f in dataclasses.fields(contracts.JobsSubmitResponse)}
    assert "lease_id" in {f.name for f in dataclasses.fields(contracts.JobsCancelRequest)}


# ---------------------------------------------------------------------------
# 2. Неизвестный адаптер → AdapterMissingError (fail-closed).
# ---------------------------------------------------------------------------


class _DummyCache:
    def get(self, request: object) -> object:
        return object()

    def set(self, request: object) -> object:
        return object()

    def delete(self, request: object) -> object:
        return object()

    def incr(self, request: object) -> object:
        return object()

    def publish(self, request: object) -> object:
        return object()


def test_unknown_adapter_resolve_raises_adapter_missing_error() -> None:
    registry = AdapterRegistry()
    with pytest.raises(AdapterMissingError):
        registry.resolve("cache")


def test_unknown_capability_name_is_rejected_on_register() -> None:
    registry = AdapterRegistry()
    with pytest.raises(UnknownCapabilityError):
        registry.register("nonexistent", _DummyCache())


def test_db_direct_cannot_be_registered_or_resolved() -> None:
    registry = AdapterRegistry()
    with pytest.raises(UnknownCapabilityError) as exc:
        registry.register(DB_DIRECT, _DummyCache())
    assert "DSN" in str(exc.value)
    with pytest.raises(UnknownCapabilityError):
        registry.resolve(DB_DIRECT)


def test_register_requires_all_protocol_methods() -> None:
    """Fail-closed: частичная реализация адаптера не регистрируется."""

    class Partial:
        def get(self, request: object) -> object:
            return object()

    with pytest.raises(TypeError):
        AdapterRegistry().register("cache", Partial())


def test_register_is_idempotency_guarded() -> None:
    registry = AdapterRegistry()
    registry.register("cache", _DummyCache())
    with pytest.raises(ValueError):
        registry.register("cache", _DummyCache())


def test_dispatch_rejects_arbitrary_operation_string() -> None:
    """Typed dispatch не является generic: произвольная операция отклоняется."""
    with pytest.raises(UnknownCapabilityError):
        from prodavan.application.plugins.capabilities.ports import dispatch

        dispatch(_DummyCache(), "cache", "execute_sql", CacheGetRequest(key="k", envelope=envelope()))
