"""Settings surface and lifespan registration (MIG-AR-HOST-01).

Two things are asserted here that no runtime test can:

1. the six knobs are *additive* — they carry defaults, they accept both the
   conventional ``AGENT_RUN_*`` env spelling and the ``agent_run_*`` field name,
   and none of them is a network location or a credential;
2. the resource is registered in the delivery lifespan, after the database
   resource, under a name that does not collide with an existing one.

Registration order is a contract, not an aesthetic: startup runs in registration
order, so a loop registered before the engine could take a session on a disposed
engine, and shutdown runs in reverse, so a loop must be started after — and
therefore stopped before — the storage it uses.
"""

from __future__ import annotations

import inspect

import pytest

from prodavan.config.settings import Settings, settings
from prodavan.core.infra.agent_run_worker_resource import (
    CONSUMER_DEFAULT_BATCH_MAX,
    CONSUMER_DEFAULT_INTERVAL_SEC,
    MAX_CONSUMER_BATCH,
    MAX_INTERVAL_SEC,
    MAX_WORKER_BATCH,
    MIN_INTERVAL_SEC,
    WORKER_DEFAULT_BATCH_MAX,
    WORKER_DEFAULT_INTERVAL_SEC,
    AgentRunHostSettings,
    agent_run_host_settings_from_settings,
)

FIELD_NAMES = (
    "agent_run_worker_enabled",
    "agent_run_worker_interval_sec",
    "agent_run_worker_batch_max",
    "agent_run_consumer_enabled",
    "agent_run_consumer_interval_sec",
    "agent_run_consumer_batch_max",
)

FORBIDDEN_SUBSTRINGS = ("url", "token", "secret", "password", "key", "dsn", "endpoint")


# ---------------------------------------------------------------------------
# Application settings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("name", FIELD_NAMES)
def test_field_exists_with_an_explicit_default(name: str) -> None:
    assert name in Settings.model_fields
    assert Settings.model_fields[name].default is not None


@pytest.mark.parametrize(
    ("name", "expected"),
    (
        ("agent_run_worker_enabled", False),
        ("agent_run_consumer_enabled", False),
        ("agent_run_worker_interval_sec", WORKER_DEFAULT_INTERVAL_SEC),
        ("agent_run_worker_batch_max", WORKER_DEFAULT_BATCH_MAX),
        ("agent_run_consumer_interval_sec", CONSUMER_DEFAULT_INTERVAL_SEC),
        ("agent_run_consumer_batch_max", CONSUMER_DEFAULT_BATCH_MAX),
    ),
)
def test_defaults_are_off_and_consistent_with_the_resource(name: str, expected) -> None:
    """A single source of truth: the resource defaults equal the settings defaults."""
    assert Settings.model_fields[name].default == expected


@pytest.mark.parametrize("name", FIELD_NAMES)
def test_alias_choices_accept_both_spellings(name: str) -> None:
    """``AGENT_RUN_*`` (env) and ``agent_run_*`` (field/kwarg) both resolve."""
    alias = Settings.model_fields[name].validation_alias
    choices = tuple(alias.choices)
    assert name in choices
    assert name.upper() in choices
    # The plural spelling is accepted as a convenience alias, never required.
    assert name.replace("agent_run_", "agent_runs_").upper() in choices


@pytest.mark.parametrize("name", FIELD_NAMES)
def test_no_field_is_a_location_or_a_credential(name: str) -> None:
    for forbidden in FORBIDDEN_SUBSTRINGS:
        assert forbidden not in name


def test_settings_object_exposes_the_six_knobs() -> None:
    """The process-wide settings instance must carry all six attributes."""
    for name in FIELD_NAMES:
        assert hasattr(settings, name)
    assert isinstance(settings.agent_run_worker_enabled, bool)
    assert isinstance(settings.agent_run_consumer_enabled, bool)


# ---------------------------------------------------------------------------
# Host settings projection
# ---------------------------------------------------------------------------


class _Stub:
    def __init__(self, **values: object) -> None:
        for name in FIELD_NAMES:
            setattr(self, name, values.get(name))


def test_projection_reads_flags_and_intervals() -> None:
    stub = _Stub(
        agent_run_worker_enabled=True,
        agent_run_worker_interval_sec=30,
        agent_run_worker_batch_max=3,
        agent_run_consumer_enabled=True,
        agent_run_consumer_interval_sec=2.5,
        agent_run_consumer_batch_max=40,
    )
    config = agent_run_host_settings_from_settings(stub)
    assert config == AgentRunHostSettings(
        worker_enabled=True,
        worker_interval_sec=30.0,
        worker_batch_max=3,
        consumer_enabled=True,
        consumer_interval_sec=2.5,
        consumer_batch_max=40,
    )


def test_projection_on_a_settings_object_predating_the_fields_is_disabled() -> None:
    """A missing knob means "off", never an ``AttributeError`` at startup."""

    class Legacy:
        pass

    config = agent_run_host_settings_from_settings(Legacy())
    assert config.any_enabled is False
    assert config == AgentRunHostSettings()


@pytest.mark.parametrize("value", ("1", "true", "TRUE", "yes", "on", " On "))
def test_flag_strings_are_accepted(value: str) -> None:
    config = agent_run_host_settings_from_settings(
        _Stub(agent_run_worker_enabled=value, agent_run_consumer_enabled="0")
    )
    assert config.worker_enabled is True
    assert config.consumer_enabled is False


@pytest.mark.parametrize("value", ("", "0", "false", "no", "off", "nonsense"))
def test_flag_strings_that_are_not_true_stay_off(value: str) -> None:
    config = agent_run_host_settings_from_settings(_Stub(agent_run_worker_enabled=value))
    assert config.worker_enabled is False


@pytest.mark.parametrize("value", (0, -5, 0.0001))
def test_interval_below_the_floor_is_clamped_not_fatal(value: float) -> None:
    """A zero interval must not crash the process or spin the CPU."""
    config = AgentRunHostSettings(consumer_interval_sec=value)
    assert config.consumer_interval_sec == MIN_INTERVAL_SEC


@pytest.mark.parametrize("value", (10**9, 99999.0))
def test_interval_above_the_ceiling_is_clamped(value: float) -> None:
    config = AgentRunHostSettings(worker_interval_sec=value)
    assert config.worker_interval_sec == MAX_INTERVAL_SEC


@pytest.mark.parametrize("value", (0, -1, 10_000))
def test_batch_sizes_are_clamped_to_the_dispatcher_and_worker_limits(value: int) -> None:
    config = AgentRunHostSettings(consumer_batch_max=value, worker_batch_max=value)
    assert 1 <= config.consumer_batch_max <= MAX_CONSUMER_BATCH
    assert 1 <= config.worker_batch_max <= MAX_WORKER_BATCH


def test_garbage_intervals_fall_back_to_defaults() -> None:
    config = agent_run_host_settings_from_settings(
        _Stub(
            agent_run_worker_interval_sec="not-a-number",
            agent_run_consumer_batch_max=None,
        )
    )
    assert config.worker_interval_sec == WORKER_DEFAULT_INTERVAL_SEC
    assert config.consumer_batch_max == CONSUMER_DEFAULT_BATCH_MAX


def test_any_enabled_requires_at_least_one_flag() -> None:
    assert AgentRunHostSettings().any_enabled is False
    assert AgentRunHostSettings(worker_enabled=True).any_enabled is True
    assert AgentRunHostSettings(consumer_enabled=True).any_enabled is True


# ---------------------------------------------------------------------------
# Lifespan registration
# ---------------------------------------------------------------------------


def test_resource_is_registered_after_the_database_resource() -> None:
    """Static check: the wiring module registers it immediately after the DB."""
    from prodavan.core import wiring

    source = inspect.getsource(wiring.build_lifespan_manager)
    database_at = source.index("manager.register(DatabaseEngineResource())")
    host_at = source.index("manager.register(AgentRunWorkerResource())")
    assert database_at < host_at, "the loops must start after the engine"
    # And before the loop-heavy tail (samplers/workers), so shutdown order makes
    # the loops stop before the storage they depend on.
    assert host_at < source.index("manager.register(TriggerWorkerResource())")


def test_import_is_present_once() -> None:
    from prodavan.core import wiring

    source = inspect.getsource(wiring)
    assert source.count("from prodavan.core.infra.agent_run_worker_resource import") == 1
    assert source.count("manager.register(AgentRunWorkerResource())") == 1


def test_registered_name_does_not_collide() -> None:
    """``LifespanManager.register`` raises on a duplicate name."""
    from prodavan.core.infra.agent_run_worker_resource import AgentRunWorkerResource as Resource

    assert Resource(config=AgentRunHostSettings()).name == "agent_run_host"


def test_default_construction_is_inert() -> None:
    """Constructing the resource (which wiring does on import) starts nothing."""
    from prodavan.core.infra.agent_run_worker_resource import AgentRunWorkerResource as Resource

    resource = Resource()
    assert resource.enabled is False
    assert resource.report()["started"] is False
    assert all(not loop["running"] for loop in resource.report()["loops"].values())
