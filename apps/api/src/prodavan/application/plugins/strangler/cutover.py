"""Compatibility metadata sequence для historical strangler plan, НЕ runbook.

CutoverStepKind, step ids, CUTOVER_STEPS order и FinalState fields сохранены как
публичный compatibility API. Последовательность infrastructure → pilot → frontend
→ context:<id> → cutover → remove_declarative_app описывает только metadata
progression и не разрешает включение plugin platform или destructive cleanup.

REMOVAL step — inert historical/target inventory, а не approved executable action.
``2026110802`` dormant/not active. Никакого runtime/startup/deploy cutover или
удаления declarative-app/module_* этот модуль не выполняет.

``advance()`` и ``complete_current_step()`` изменяют только metadata compatibility
cursor/completion flags. Они не выполняют DB, DML, drop, remove, Alembic, deploy или
feature-flag operations. Строгий order остаётся behavior contract: нельзя перейти
к следующему шагу без завершения текущего.

``FinalState`` и ``declarative_app_removed=True`` — inert compatibility semantics
для historical/target inventory, а не evidence или side effect фактического
удаления. FINAL_STATE_ARTIFACTS сохранён без изменения.

Любая будущая destructive revision требует завершённого code-first S4 и
отдельного S5 ADR/review/acceptance, создаётся заново от тогдашней current head,
имеет schema-only downgrade и не использует env guard. Никакой cutover не
происходит автоматически из exports или metadata transitions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contexts import CONTEXT_ORDER


class CutoverStepKind(StrEnum):
    """Compatibility step kind; REMOVAL — inert inventory, не executable action."""

    INFRASTRUCTURE = "infrastructure"
    PILOT = "pilot"
    FRONTEND = "frontend"
    CONTEXT = "context"
    CUTOVER = "cutover"
    REMOVAL = "removal"


@dataclass(frozen=True, slots=True)
class CutoverStep:
    """Metadata step; ``completed`` не означает фактический deploy/removal."""

    step_id: str
    kind: CutoverStepKind
    description: str


@dataclass(slots=True)
class _MutableStep:
    """Внутреннее изменяемое состояние шага (в публичный API не выходит)."""

    step: CutoverStep
    completed: bool = False


class CutoverOrderError(RuntimeError):
    """Попытка ``advance()`` мимо незавершённого шага / вне последовательности."""


class CutoverPlanComplete(RuntimeError):
    """Metadata cursor исчерпан; это не статус фактического cutover."""


# Historical/target compatibility inventory, НЕ approved deletion list.
# Пять имён и их порядок остаются частью публичного контракта.
ARTIFACT_MODULE_META_DOCUMENTS = "module_meta_documents"
ARTIFACT_MODULE_RUNTIME_TSX = "ModuleRuntime.tsx"
ARTIFACT_MODULE_INSTANCE_DATA_ROWS = "module_instance_data_rows"
ARTIFACT_GENERIC_RUNTIME = "generic runtime"
ARTIFACT_EQUIPMENT_PAGE = "routes/pages.tsx::EquipmentProcurementPage"

FINAL_STATE_ARTIFACTS: tuple[str, ...] = (
    ARTIFACT_MODULE_META_DOCUMENTS,
    ARTIFACT_MODULE_RUNTIME_TSX,
    ARTIFACT_MODULE_INSTANCE_DATA_ROWS,
    ARTIFACT_GENERIC_RUNTIME,
    ARTIFACT_EQUIPMENT_PAGE,
)


def _build_steps() -> tuple[CutoverStep, ...]:
    """Собрать compatibility sequence из historical contexts; не runbook."""
    steps: list[CutoverStep] = [
        CutoverStep(
            step_id="infrastructure",
            kind=CutoverStepKind.INFRASTRUCTURE,
            description="Инфраструктура плагинов; declarative-app работает как прежде (§8 этап 1)",
        ),
        CutoverStep(
            step_id="pilot",
            kind=CutoverStepKind.PILOT,
            description="Пилотный плагин за plugin_enabled_modules[] (§8 этап 2)",
        ),
        CutoverStep(
            step_id="frontend",
            kind=CutoverStepKind.FRONTEND,
            description="Страница плагина в iframe; backend пока в API (§8 этап 3)",
        ),
    ]
    for context_id in CONTEXT_ORDER:
        steps.append(
            CutoverStep(
                step_id=f"context:{context_id}",
                kind=CutoverStepKind.CONTEXT,
                description=f"Перенос backend-контекста {context_id} за флагом (§8 этап 4/6)",
            )
        )
    steps.append(
        CutoverStep(
            step_id="cutover",
            kind=CutoverStepKind.CUTOVER,
            description="Переключение фича-флага (§8 этап 5)",
        )
    )
    steps.append(
        CutoverStep(
            step_id="remove_declarative_app",
            kind=CutoverStepKind.REMOVAL,
            description=(
                "Inert historical/target inventory only: no runtime/startup/deploy "
                "cutover or declarative-app/module_* removal; code-first S4 + "
                "separate S5 ADR/review/acceptance; new revision from then-current "
                "head with schema-only downgrade and no env guard"
            ),
        )
    )
    return tuple(steps)


CUTOVER_STEPS: tuple[CutoverStep, ...] = _build_steps()


@dataclass(slots=True)
class FinalState:
    """Inert historical/target inventory, возвращаемый ``final_state()``.

    ``removed_artifacts`` и ``declarative_app_removed=True`` сохраняют compatibility
    semantics, но не доказывают и не выполняют удаление. Нет runtime/startup/deploy
    side effects. Schema cleanup требует code-first S4 и отдельного S5 review.
    """

    removed_artifacts: tuple[str, ...] = FINAL_STATE_ARTIFACTS
    declarative_app_removed: bool = True

    def __contains__(self, artifact: object) -> bool:
        """Позволяет проверять ``artifact in plan.final_state()``."""
        return artifact in self.removed_artifacts

    def __iter__(self):
        """Итерация по inert historical/target artifact inventory."""
        return iter(self.removed_artifacts)

    def __len__(self) -> int:
        return len(self.removed_artifacts)


class CutoverPlan:
    """Metadata-only compatibility state machine, а не executable cutover.

    Экземпляр не разделяется между компаниями и не является runtime-состоянием
    фича-флага. Cursor/completion transitions не выполняют DB/DML/drop/remove и
    не запускают startup/deploy actions.
    """

    def __init__(self, steps: tuple[CutoverStep, ...] = CUTOVER_STEPS) -> None:
        if not steps:
            raise ValueError("CutoverPlan требует непустую последовательность шагов")
        self._steps: tuple[_MutableStep, ...] = tuple(_MutableStep(step=s) for s in steps)
        self._index = 0

    @property
    def steps(self) -> tuple[CutoverStep, ...]:
        """Неизменяемая последовательность шагов (без флагов завершения)."""
        return tuple(item.step for item in self._steps)

    @property
    def current_step(self) -> CutoverStep | None:
        """Текущий шаг или ``None``, когда план завершён."""
        if self._index >= len(self._steps):
            return None
        return self._steps[self._index].step

    @property
    def current_step_completed(self) -> bool:
        if self._index >= len(self._steps):
            return True
        return self._steps[self._index].completed

    @property
    def is_complete(self) -> bool:
        """Все шаги пройдены (последний завершён и курсор за ним)."""
        return self._index >= len(self._steps) and all(
            item.completed for item in self._steps
        )

    @property
    def completed_steps(self) -> tuple[str, ...]:
        """id уже завершённых шагов (в порядке выполнения)."""
        return tuple(item.step.step_id for item in self._steps if item.completed)

    def complete_current_step(self) -> CutoverStep:
        """Только metadata compatibility completion; идемпотентно в рамках шага.

        Метод не выполняет DB, DML, drop, remove, Alembic, deploy или flag writes.
        """
        if self._index >= len(self._steps):
            raise CutoverPlanComplete("план уже завершён: отмечать нечего")
        current = self._steps[self._index]
        current.completed = True
        return current.step

    def advance(self) -> CutoverStep | None:
        """Сдвинуть только metadata compatibility cursor, проверив completion.

        * текущий шаг не отмечен завершённым → ``CutoverOrderError`` (шаги не
          пропускаются);
        * план уже завершён → ``CutoverPlanComplete``;
        * иначе курсор сдвигается ровно на одну позицию.

        Возвращает новый ``current_step`` (``None`` после последнего шага).
        Метод не выполняет DB, DML, drop, remove, Alembic, deploy или flag writes.
        """
        if self._index >= len(self._steps):
            raise CutoverPlanComplete("план уже завершён: advance невозможен")
        if not self._steps[self._index].completed:
            current = self._steps[self._index].step
            raise CutoverOrderError(
                f"шаг {current.step_id!r} не завершён: advance пропустил бы шаг (§8)"
            )
        self._index += 1
        return self.current_step

    def final_state(self) -> FinalState:
        """Вернуть inert historical/target inventory без side effects.

        Результат не зависит от текущего шага — compatibility target, а не прогресс
        или доказательство удаления. Пять артефактов и declarative_app_removed=True
        сохранены как публичная семантика; никакого DDL/DML/remove не выполняется.
        """
        return FinalState()


__all__ = [
    "ARTIFACT_EQUIPMENT_PAGE",
    "ARTIFACT_GENERIC_RUNTIME",
    "ARTIFACT_MODULE_INSTANCE_DATA_ROWS",
    "ARTIFACT_MODULE_META_DOCUMENTS",
    "ARTIFACT_MODULE_RUNTIME_TSX",
    "CUTOVER_STEPS",
    "FINAL_STATE_ARTIFACTS",
    "CutoverOrderError",
    "CutoverPlan",
    "CutoverPlanComplete",
    "CutoverStep",
    "CutoverStepKind",
    "FinalState",
]
