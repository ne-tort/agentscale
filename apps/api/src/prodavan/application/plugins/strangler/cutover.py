"""План cutover'а strangler'а (F10, §8 «Путь миграции»).

Последовательность шагов — ровно по §8:

1. ``infrastructure`` — tenant PostgreSQL, PluginClaims, Gateway, OCI/cosign и т.д.;
   declarative-app работает как прежде.
2. ``pilot`` — первый плагин за фича-флагом ``plugin_enabled_modules[]``.
3. ``frontend`` — страница плагина в iframe, backend-логика пока в API.
4. ``context:<id>`` — по одному bounded context'у, в порядке §8 этап 4:
   offers → prices → budget → documents → tenders, затем ``mod_management``
   последним (§8 этап 6).
5. ``cutover`` — переключение флага.
6. ``remove_declarative_app`` — удаление declarative-app, дропание ``module_*``
   таблиц ревизией Alembic без переноса содержимого.

``advance()`` **не пропускает шаги**: перейти к следующему можно только после
явной отметки текущего шага завершённым (``complete_current_step()``). Попытка
продвинуться мимо незавершённого шага — типизированная ``CutoverOrderError``.
Строгая последовательность исключает «перескочить пилот и сразу удалить
declarative-app».

ЧЕГО ЗДЕСЬ НЕТ (§8, §13.1, план §4): метрик парности, сверки контрольных сумм,
dual-read/dual-write, «N недель наблюдения», snapshot/бэкапов, переноса данных.
Данные dev эфемерны; откат = выключить флаг (§10 «Сломанный cutover»).
``final_state()`` фиксирует КОНЕЧНОЕ СОСТОЯНИЕ («всё под нож»): что именно
должно исчезнуть после шага 6.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .contexts import CONTEXT_ORDER


class CutoverStepKind(StrEnum):
    """Класс шага плана (для отчётов и проверок последовательности)."""

    INFRASTRUCTURE = "infrastructure"
    PILOT = "pilot"
    FRONTEND = "frontend"
    CONTEXT = "context"
    CUTOVER = "cutover"
    REMOVAL = "removal"


@dataclass(frozen=True, slots=True)
class CutoverStep:
    """Один шаг плана. ``completed`` выставляется только ``complete_current_step``."""

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
    """План уже завершён: ``advance()``/``complete_current_step()`` нечего делать."""


# Пять артефактов, которые должны исчезнуть в конечном состоянии (§8, §13.1).
# Имена зафиксированы задачей и являются частью публичного контракта.
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
    """Собрать последовательность §8. Context-шаги берутся из ``contexts.py``."""
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
                "Удаление declarative-app, mod_management на plugin, дроп module_* "
                "таблиц ревизией Alembic без переноса содержимого (§8 этап 6)"
            ),
        )
    )
    return tuple(steps)


CUTOVER_STEPS: tuple[CutoverStep, ...] = _build_steps()


@dataclass(slots=True)
class FinalState:
    """Конечное состояние «всё под нож» (результат ``final_state()``).

    ``removed_artifacts`` — ровно пять артефактов из задания. Метрик парности и
    «недель наблюдения» здесь НЕТ не «со значением 0/False», а **вовсе**: в
    классе отсутствуют соответствующие поля (§8, §13.1). Равно как нет snapshot,
    бэкапов, dual-read/dual-write и переноса данных.
    """

    removed_artifacts: tuple[str, ...] = FINAL_STATE_ARTIFACTS
    declarative_app_removed: bool = True

    def __contains__(self, artifact: object) -> bool:
        """Позволяет проверять ``artifact in plan.final_state()``."""
        return artifact in self.removed_artifacts

    def __iter__(self):
        """Итерация по удаляемым артефактам (список как «список того, что исчезнет»)."""
        return iter(self.removed_artifacts)

    def __len__(self) -> int:
        return len(self.removed_artifacts)


class CutoverPlan:
    """Строго последовательный план перехода (§8).

    Экземпляр не разделяется между компаниями: это описание **порядка работ**,
    а не рантайм-состояние фича-флага (флаг хранится в ``GrantStorePort`` и
    читается ``EnabledModulesPolicy``).
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
        """Отметить текущий шаг завершённым. Идемпотентно в рамках шага."""
        if self._index >= len(self._steps):
            raise CutoverPlanComplete("план уже завершён: отмечать нечего")
        current = self._steps[self._index]
        current.completed = True
        return current.step

    def advance(self) -> CutoverStep | None:
        """Перейти к следующему шагу, проверив завершение предыдущего.

        * текущий шаг не отмечен завершённым → ``CutoverOrderError`` (шаги не
          пропускаются);
        * план уже завершён → ``CutoverPlanComplete``;
        * иначе курсор сдвигается ровно на одну позицию.

        Возвращает новый ``current_step`` (``None`` после последнего шага).
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
        """Конечное состояние: список артефактов, которые должны исчезнуть.

        Список не зависит от текущего шага — это **цель**, а не прогресс. Пять
        артефактов: ``module_meta_documents``, ``ModuleRuntime.tsx``,
        ``module_instance_data_rows``, ``generic runtime``,
        ``routes/pages.tsx::EquipmentProcurementPage``.
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
