"""The quota port is a *named blocker*, and the implementation never says "yes" blindly.

Two halves, and both matter:

* **absent, not allow-everything.**  ``build_shadow_gateway`` requires a
  ``CapabilityQuotaPort``.  There is no table that can decide a per-invocation
  quota, so the builder reports :data:`QUOTA_PORT_BLOCKER` and describes the table
  that would be needed instead of returning a port that always allows.  An
  always-allow quota port would quietly turn "quota enforced" into "quota
  ignored" in every shadow observation — the one degradation this migration may
  not make.
* **no implicit "allow" anywhere in the adapters.**  A static scan proves the
  legacy port module contains no unconditional ``return True``: every positive
  answer must come from a comparison that the tests above exercise.  A gate that
  only looked for the substring ``return True`` would also match a commented-out
  line, so the scan parses the module and inspects the AST, and it asserts the
  positive answers it *does* allow are exactly the ones the design names.
"""

from __future__ import annotations

import ast
import inspect

import pytest

from prodavan.application.dynamic_app_plugin_gateway.ports import (
    CapabilityQuotaPort,
    CapabilityScopePort,
    DynamicPluginRepository,
)
from prodavan.application.migration.m06_grants_gateway.composition import (
    ShadowGatewayPorts,
    build_shadow_gateway,
)
from prodavan.infrastructure.persistence.dynamic_app_gateway import (
    QUOTA_PORT_BLOCKER,
    QUOTA_PORT_REASON,
    QUOTA_REQUIRED_COLUMNS,
    QUOTA_REQUIRED_TABLE,
    LegacyGatewayPorts,
    SqlCapabilityScopePort,
    SqlDynamicPluginRepository,
    build_legacy_gateway_ports,
)
from prodavan.infrastructure.persistence.dynamic_app_gateway import (
    legacy_ports as legacy_ports_module,
)

from .sqlite_mirror import make_engine


# ---------------------------------------------------------------------------
# The quota blocker
# ---------------------------------------------------------------------------


def test_the_builder_reports_the_quota_blocker_and_never_a_stub() -> None:
    ports = build_legacy_gateway_ports(session_factory=make_engine())

    assert isinstance(ports, LegacyGatewayPorts)
    assert ports.blockers == (QUOTA_PORT_BLOCKER,)
    assert ports.quota is None
    assert ports.complete is False
    # The two ports that *are* implemented come back as real adapters.
    assert isinstance(ports.repository, SqlDynamicPluginRepository)
    assert isinstance(ports.scope, SqlCapabilityScopePort)


def test_the_quota_blocker_names_the_table_and_columns_that_would_be_needed() -> None:
    """A blocker that does not say what is missing cannot be acted on."""

    assert QUOTA_REQUIRED_TABLE == "dynamic_app_capability_quota"
    names = {name for name, _ in QUOTA_REQUIRED_COLUMNS}
    assert {
        "tenant_id",
        "plugin_instance_id",
        "capability",
        "window_sec",
        "max_requests",
        "max_bytes",
        "used_requests",
        "used_bytes",
        "window_started_at",
    } == names
    assert QUOTA_PORT_REASON.strip()
    assert "quota" in QUOTA_PORT_REASON

    # The named table really is absent from the registered schema, so the code and
    # the tree agree: a table that existed would make this blocker stale.
    from prodavan.infrastructure.persistence.models import Base

    assert QUOTA_REQUIRED_TABLE not in Base.metadata.tables


def test_the_shadow_gateway_refuses_a_port_set_without_a_quota_port() -> None:
    """The composition root's own refusal, asserted here so the blocker stays real."""

    ports = build_legacy_gateway_ports(session_factory=make_engine())
    assert ports.quota is None

    class _Inert:
        def __getattr__(self, attribute: str):  # noqa: ANN401 - test double
            def refuse(*_a: object, **_k: object) -> object:
                raise AssertionError("an assembled port was executed")

            return refuse

    inert = _Inert()
    with pytest.raises(ValueError, match="legacy scope and quota ports are required"):
        build_shadow_gateway(
            ShadowGatewayPorts(
                company=inert,
                m04_scope=inert,
                m04e_definition_versions=inert,
                m05_instances=inert,
                m05_upstream=inert,
                handoff_partitions=inert,
                legacy_repository=ports.repository,
                legacy_scope=ports.scope,
                legacy_quota=ports.quota,  # type: ignore[arg-type]
                canonical_grants=inert,
                method_mappings=inert,
                method_servers={},
                method_versions={},
                evidence_read=inert,
                evidence_transaction=inert,
            )
        )


def test_the_builder_refuses_a_missing_session_factory() -> None:
    with pytest.raises(ValueError):
        build_legacy_gateway_ports(session_factory=None)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# The adapters are the protocols' structural shape
# ---------------------------------------------------------------------------


def test_the_adapters_state_every_method_the_protocols_require() -> None:
    for protocol, implementation in (
        (DynamicPluginRepository, SqlDynamicPluginRepository),
        (CapabilityScopePort, SqlCapabilityScopePort),
    ):
        required = {
            name
            for name, value in inspect.getmembers(protocol, inspect.isfunction)
            if not name.startswith("_")
        }
        assert required, protocol
        for name in required:
            assert callable(getattr(implementation, name)), (implementation, name)


def test_the_quota_protocol_has_no_implementation_in_the_module() -> None:
    """A quota adapter added here without the table would be the failure mode.

    ``CapabilityQuotaPort`` is not ``@runtime_checkable``, so ``issubclass`` is
    unavailable by design; the structural check is therefore done the way the
    protocol itself works — by method name.
    """

    required = {
        name
        for name, value in inspect.getmembers(CapabilityQuotaPort, inspect.isfunction)
        if not name.startswith("_")
    }
    assert required == {"allows"}
    for value in vars(legacy_ports_module).values():
        if inspect.isclass(value) and value.__module__ == legacy_ports_module.__name__:
            assert not all(
                callable(getattr(value, name, None)) for name in required
            ) or value is LegacyGatewayPorts, value
    assert "SqlCapabilityQuotaPort" not in dir(legacy_ports_module)


# ---------------------------------------------------------------------------
# No unconditional "allow"
# ---------------------------------------------------------------------------


def _returns(tree: ast.AST) -> list[ast.Return]:
    return [node for node in ast.walk(tree) if isinstance(node, ast.Return)]


def test_no_function_returns_a_bare_true() -> None:
    """``return True`` with no condition is how a fail-closed port becomes a lie.

    The scan is an AST walk, not a text search: a commented-out ``return True``
    or a docstring mentioning it must not satisfy or trip the check, and a
    ``return True`` hidden behind a one-line ``if`` is still visible here.
    """

    source = inspect.getsource(legacy_ports_module)
    tree = ast.parse(source)

    offenders: list[tuple[str, int]] = []
    for function in [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)]:
        for node in _returns(function):
            value = node.value
            if isinstance(value, ast.Constant) and value.value is True:
                offenders.append((function.name, node.lineno))
    assert offenders == [], (
        "an unconditional 'return True' makes the port allow by default: "
        f"{offenders}"
    )


def test_the_only_true_literals_are_the_documented_positive_decisions() -> None:
    """Every positive answer is a comparison, and the inventory is pinned.

    Three functions may answer "yes", each for a stated reason:

    ``allows``                  the unanimous decision set is exactly ``{True}``
                                (one installation, granting, live);
    ``_scope_belongs_to_tenant`` the tenant scope id equals the requested tenant —
                                the only scope kind with no row to look up;
    ``_installation_allows``    the manifest's bound capability is in the set of
                                ``granted``, unexpired entries of the grant row.

    Adding a fourth path to a positive answer without a test that reaches it is
    exactly the silent widening this task exists to prevent, so an unknown entry
    fails here rather than passing unnoticed.
    """

    source = inspect.getsource(legacy_ports_module)
    tree = ast.parse(source)

    positives: list[str] = []
    for function in [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
    ]:
        for node in _returns(function):
            value = node.value
            if isinstance(value, ast.Constant) and value.value is True:
                positives.append(f"{function.name}:bare")
            elif isinstance(value, ast.Compare):
                positives.append(f"{function.name}:compare")

    assert positives == [
        "allows:compare",
        "_scope_belongs_to_tenant:compare",
        "_installation_allows:compare",
    ], positives

    # Each of the three is asserted positively by a behavioural test, so no
    # positive answer exists only in the source.
    for function_name in ("allows", "_scope_belongs_to_tenant", "_installation_allows"):
        assert function_name in _POSITIVE_ANSWERS_ASSERTED, function_name


def test_the_scope_port_returns_false_on_every_negative_path_it_guards() -> None:
    """Each guard in ``allows``/``_scope_belongs_to_tenant`` answers ``False``.

    Counting the refusals is a weak proxy for behaviour, so it is only a *shape*
    assertion: the behavioural refusals live in ``test_scope_port``.  What this
    pins is that the guards exist at all — a refactor that deleted one would
    change the count.
    """

    for name in ("allows", "_scope_belongs_to_tenant", "_cabinet_is_granted"):
        function = getattr(SqlCapabilityScopePort, name)
        source = inspect.getsource(function)
        assert source.count("return False") >= 3, name
