"""Static tests compatibility metadata sequence и inert target inventory.

Тесты проходят в текущем прогоне (pytest tests/unit/plugins).
"""

from __future__ import annotations

import pytest

import prodavan.application.plugins.strangler.cutover as cutover_module
from prodavan.application.plugins.strangler import (
    CONTEXT_ORDER,
    FINAL_STATE_ARTIFACTS,
    CutoverOrderError,
    CutoverPlan,
    CutoverPlanComplete,
    CutoverStepKind,
    FinalState,
)
from prodavan.application.plugins.strangler.cutover import CUTOVER_STEPS

EXPECTED_ARTIFACTS = (
    "module_meta_documents",
    "ModuleRuntime.tsx",
    "module_instance_data_rows",
    "generic runtime",
    "routes/pages.tsx::EquipmentProcurementPage",
)


def _cutover_narrative() -> str:
    return "\n".join(
        (
            cutover_module.__doc__ or "",
            CutoverStepKind.__doc__ or "",
            FinalState.__doc__ or "",
            CutoverPlan.__doc__ or "",
            CutoverPlan.complete_current_step.__doc__ or "",
            CutoverPlan.advance.__doc__ or "",
            CutoverPlan.final_state.__doc__ or "",
            CUTOVER_STEPS[-1].description,
        )
    )


def test_step_sequence_matches_compatibility_contract() -> None:
    """Historical metadata order сохранён; это не executable runbook."""
    ids = tuple(step.step_id for step in CUTOVER_STEPS)
    assert ids[0] == "infrastructure"
    assert ids[1] == "pilot"
    assert ids[2] == "frontend"
    assert ids[3:8] == tuple(f"context:{c}" for c in CONTEXT_ORDER[:5])
    assert ids[8] == "context:mod_management"
    assert ids[9] == "cutover"
    assert ids[10] == "remove_declarative_app"
    assert len(ids) == 11


def test_context_steps_are_in_strict_order() -> None:
    kinds = tuple(step.kind for step in CUTOVER_STEPS)
    assert kinds[0] is CutoverStepKind.INFRASTRUCTURE
    assert kinds[1] is CutoverStepKind.PILOT
    assert kinds[2] is CutoverStepKind.FRONTEND
    assert kinds[9] is CutoverStepKind.CUTOVER
    assert kinds[10] is CutoverStepKind.REMOVAL


def test_initial_current_step_is_infrastructure() -> None:
    plan = CutoverPlan()
    assert plan.current_step is not None
    assert plan.current_step.step_id == "infrastructure"
    assert plan.current_step_completed is False
    assert plan.is_complete is False
    assert plan.completed_steps == ()


def test_advance_without_completing_current_step_is_rejected() -> None:
    """advance НЕ пропускает шаги: незавершённый текущий → CutoverOrderError."""
    plan = CutoverPlan()
    with pytest.raises(CutoverOrderError):
        plan.advance()
    assert plan.current_step is not None
    assert plan.current_step.step_id == "infrastructure"


def test_advance_can_not_skip_ahead() -> None:
    """Даже многократный advance не сдвигает курсор мимо незавершённого шага."""
    plan = CutoverPlan()
    for _ in range(5):
        with pytest.raises(CutoverOrderError):
            plan.advance()
    assert plan.current_step is not None
    assert plan.current_step.step_id == "infrastructure"


def test_advance_moves_exactly_one_step_after_completion() -> None:
    plan = CutoverPlan()
    plan.complete_current_step()
    nxt = plan.advance()
    assert nxt is not None
    assert nxt.step_id == "pilot"
    # Новый шаг снова не завершён — следующий advance без отметки упадёт.
    with pytest.raises(CutoverOrderError):
        plan.advance()


def test_full_sequence_must_complete_each_step() -> None:
    """Metadata cursor требует completion каждого шага, без side effects."""
    plan = CutoverPlan()
    seen: list[str] = []
    while plan.current_step is not None:
        seen.append(plan.current_step.step_id)
        plan.complete_current_step()
        plan.advance()  # после последнего вернёт None, но не бросит
    # seen собирается в list по ходу цикла; контракт CUTOVER_STEPS — tuple.
    # Сравниваем ПОСЛЕДОВАТЕЛЬНОСТЬ (приводим к tuple), а не тип контейнера:
    # ослаблять проверку порядка/полноты нельзя, менять API плана — незачем.
    assert tuple(seen) == tuple(step.step_id for step in CUTOVER_STEPS)
    assert plan.is_complete is True
    assert plan.completed_steps == tuple(step.step_id for step in CUTOVER_STEPS)
    assert plan.current_step is None
    assert plan.current_step_completed is True


def test_advance_on_completed_plan_raises() -> None:
    plan = CutoverPlan()
    while plan.current_step is not None:
        plan.complete_current_step()
        plan.advance()
    with pytest.raises(CutoverPlanComplete):
        plan.advance()
    with pytest.raises(CutoverPlanComplete):
        plan.complete_current_step()


def test_custom_steps_are_respected() -> None:
    """План не хардкодит число шагов внутри advance (удобно для проверок)."""
    from prodavan.application.plugins.strangler import CutoverStep

    custom = (
        CutoverStep("a", CutoverStepKind.INFRASTRUCTURE, "a"),
        CutoverStep("b", CutoverStepKind.REMOVAL, "b"),
    )
    plan = CutoverPlan(custom)
    assert plan.current_step is not None and plan.current_step.step_id == "a"
    plan.complete_current_step()
    plan.advance()
    assert plan.current_step is not None and plan.current_step.step_id == "b"


def test_empty_plan_is_rejected() -> None:
    with pytest.raises(ValueError):
        CutoverPlan(())


# ---------------------------------------------------------------- final_state


def test_final_state_contains_all_five_artifacts() -> None:
    state = CutoverPlan().final_state()
    assert isinstance(state, FinalState)
    assert FINAL_STATE_ARTIFACTS == EXPECTED_ARTIFACTS
    assert state.removed_artifacts == EXPECTED_ARTIFACTS
    for artifact in EXPECTED_ARTIFACTS:
        assert artifact in state
    assert len(state) == 5
    assert list(state) == list(EXPECTED_ARTIFACTS)


def test_final_state_is_the_target_not_progress() -> None:
    """final_state — inert target inventory, не прогресс или удаление."""
    plan = CutoverPlan()
    assert plan.final_state().removed_artifacts == EXPECTED_ARTIFACTS
    plan.complete_current_step()
    plan.advance()
    assert plan.final_state().removed_artifacts == EXPECTED_ARTIFACTS


def test_final_state_has_no_parity_metrics_or_observation_weeks() -> None:
    """Compatibility fields сохранены; никакого фактического cutover нет."""
    import prodavan.application.plugins.strangler.cutover as module

    public = set(vars(FinalState)) | set(getattr(FinalState, "__dataclass_fields__", {}))
    public |= set(vars(module)) | set(module.__all__)
    for forbidden in (
        "parity_metrics",
        "observation_weeks",
        "dual_read",
        "dual_write",
        "snapshot",
        "migrate_data",
        "data_migration_performed",
    ):
        assert forbidden not in public, f"запрещённое понятие присутствует: {forbidden}"
    # Compatibility boolean сохранён; это не evidence фактического удаления.
    assert CutoverPlan().final_state().declarative_app_removed is True


def test_no_observation_weeks_in_cutover_source() -> None:
    """В тексте модуля нет концепции «недель наблюдения»/метрик парности."""
    import inspect

    import prodavan.application.plugins.strangler.cutover as module

    source = inspect.getsource(module)
    # Guard запрещает возврат operational parity/observation APIs; metadata API сохранён.
    for forbidden in (
        "def parity_metrics",
        "def observation_weeks",
        "self.parity_metrics",
        "self.observation_weeks",
    ):
        assert forbidden not in source


def test_removal_and_final_state_narratives_are_inert_inventory() -> None:
    narrative = _cutover_narrative()
    for token in (
        "2026110802",
        "dormant/not active",
        "runtime/startup/deploy",
        "inert historical/target inventory",
        "code-first S4",
        "S5 ADR/review/acceptance",
        "тогдашней current head",
        "schema-only downgrade",
        "env guard",
    ):
        assert token in narrative
    for forbidden in (
        "дропание",
        "дроп module_*",
        "без переноса содержимого",
        "Данные dev эфемерны",
        "сразу удалить",
        "всё под нож",
        "drop-first",
    ):
        assert forbidden not in narrative


def test_metadata_transitions_explicitly_do_not_execute_cutover() -> None:
    for method in (CutoverPlan.complete_current_step, CutoverPlan.advance):
        doc = method.__doc__ or ""
        assert "metadata compatibility" in doc
        assert "не выполняет DB, DML, drop, remove" in doc
    state = CutoverPlan().final_state()
    assert state.declarative_app_removed is True
    assert state.removed_artifacts == FINAL_STATE_ARTIFACTS
