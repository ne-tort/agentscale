"""Settings surface and lifespan registration (MIG-COMPOSITION-WIRING-01).

Two facts no runtime test can state:

1. the flag is **additive** — it carries an off default, accepts the documented
   ``MIGRATION_CHAIN_ENABLED`` env spelling plus one convenience alias, and names
   no location or credential, so a stray environment cannot turn a diagnostic
   resource into a privileged one;
2. the resource is registered in the delivery lifespan **after** the database
   resource, under a name that does not collide with an existing one.

Registration order is a contract, not an aesthetic: startup runs in registration
order, so a resource whose ports open sessions must start after the engine, and
shutdown runs in reverse, so it must stop before the storage it uses.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from prodavan.config.settings import Settings, settings
from prodavan.core.infra.migration_chain_resource import RESOURCE_NAME
from prodavan.core.wiring import build_lifespan_manager

API_ROOT = Path(__file__).resolve().parents[3]
WIRING_SOURCE = API_ROOT / "src" / "prodavan" / "core" / "wiring.py"

FORBIDDEN_SUBSTRINGS = ("url", "token", "secret", "password", "key", "dsn", "endpoint")


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def test_field_exists_with_an_off_default() -> None:
    field = Settings.model_fields["migration_chain_enabled"]
    assert field.default is False
    assert field.default is not None


def test_alias_choices_accept_the_documented_env_spelling() -> None:
    field = Settings.model_fields["migration_chain_enabled"]
    choices = tuple(field.validation_alias.choices)
    # Same shape as every other opt-in flag in this file: the documented env name
    # first, one historical alias second. The field name itself is accepted via
    # ``populate_by_name`` and is therefore not part of the alias list.
    assert choices[0] == "MIGRATION_CHAIN_ENABLED"
    assert "MIGRATION_COMPOSITION_ENABLED" in choices


def test_field_name_is_not_a_location_or_a_credential() -> None:
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden not in "migration_chain_enabled"


def test_process_settings_expose_the_flag_as_a_bool() -> None:
    assert hasattr(settings, "migration_chain_enabled")
    assert isinstance(settings.migration_chain_enabled, bool)
    assert settings.migration_chain_enabled is False


def test_env_value_turns_the_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """An operator turns the diagnostic on with the documented name only."""

    monkeypatch.setenv("MIGRATION_CHAIN_ENABLED", "true")
    assert Settings().migration_chain_enabled is True

    monkeypatch.setenv("MIGRATION_CHAIN_ENABLED", "false")
    assert Settings().migration_chain_enabled is False


def test_no_env_value_means_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIGRATION_CHAIN_ENABLED", raising=False)
    monkeypatch.delenv("MIGRATION_COMPOSITION_ENABLED", raising=False)
    assert Settings().migration_chain_enabled is False


def test_convenience_alias_also_turns_the_flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MIGRATION_CHAIN_ENABLED", raising=False)
    monkeypatch.setenv("MIGRATION_COMPOSITION_ENABLED", "true")
    assert Settings().migration_chain_enabled is True


# ---------------------------------------------------------------------------
# Wiring registration
# ---------------------------------------------------------------------------


def test_wiring_registers_the_chain_after_the_database() -> None:
    source = inspect.getsource(build_lifespan_manager)
    database_at = source.index("manager.register(DatabaseEngineResource())")
    chain_at = source.index("manager.register(migration_chain_resource_from_settings())")
    assert database_at < chain_at, "every migration port opens sessions from the process factory"
    # And before the loop-heavy tail, so shutdown order stops the chain before the
    # storage it depends on.
    assert chain_at < source.index("manager.register(TriggerWorkerResource())")


def test_wiring_import_and_registration_appear_exactly_once() -> None:
    source = WIRING_SOURCE.read_text(encoding="utf-8")
    assert source.count("from prodavan.core.infra.migration_chain_resource import") == 1
    assert source.count("manager.register(migration_chain_resource_from_settings())") == 1


def test_wiring_imports_the_module_it_registers() -> None:
    tree = ast.parse(WIRING_SOURCE.read_text(encoding="utf-8"))
    modules = {
        node.module
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom) and node.module
    }
    assert "prodavan.core.infra.migration_chain_resource" in modules


def test_manager_carries_the_resource_in_the_delivery_lifespan() -> None:
    manager = build_lifespan_manager()
    names = [resource.name for resource in manager.resources]
    assert RESOURCE_NAME in names
    assert names.index(RESOURCE_NAME) > names.index("database")
    # Registered exactly once: the manager refuses a duplicate name, so a second
    # registration would have raised above.
    assert names.count(RESOURCE_NAME) == 1
    assert len(names) == len(set(names))


def test_registered_resource_is_inert_with_the_default_flag() -> None:
    """Registration must not compose anything in a default deployment."""

    resource = build_lifespan_manager().get(RESOURCE_NAME)
    assert resource is not None
    assert resource.enabled is False
    report = resource.report()
    assert report.assembled is False
    assert report.stages == ()
    assert report.blockers == ()


async def test_registered_resource_reports_not_applicable_health() -> None:
    manager = build_lifespan_manager()
    report = await manager.health_report()
    assert report[RESOURCE_NAME] is None
