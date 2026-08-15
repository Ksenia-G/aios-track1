"""Политики управления.

Первая версия намеренно тупая. Здесь проверяется инфраструктура: что кейс
читается, расписание собирается, симулятор считает, а экономика сходится с
уже полученным baseline. Никакой оптимизации в этом модуле нет и не должно
быть -- она появится отдельной задачей и подключится через тот же протокол.
"""

from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

from aios.schedule.events import Schedule, ScheduleStep
from aios.schedule.state import FieldState


@runtime_checkable
class Policy(Protocol):
    """Политика превращает состояние и опорное расписание в новое расписание."""

    @property
    def policy_id(self) -> str: ...

    def plan(self, state: FieldState, baseline: Schedule) -> Schedule: ...


@dataclass(frozen=True, slots=True)
class BaselineContinuationPolicy:
    """Продолжение baseline: воздействия окна берутся из истории кейса как есть.

    Это опорная политика приёмки. Она обязана воспроизводить исходный прогон
    с точностью до знака, иначе весь контур измеряет не то, что думает.
    """

    policy_id: str = "BASELINE_CONTINUATION"

    def plan(self, state: FieldState, baseline: Schedule) -> Schedule:
        return baseline.canonical()


@dataclass(frozen=True, slots=True)
class NoChangePolicy:
    """Ничего не менять: управление замораживается на срезе `state`.

    Сетка дат берётся из опорного расписания -- горизонт и отчётные шаги
    определяются кейсом, а не политикой. Меняются только воздействия.
    """

    policy_id: str = "NO_CHANGE"

    def plan(self, state: FieldState, baseline: Schedule) -> Schedule:
        dates: tuple[date, ...] = baseline.canonical().dates
        if not dates:
            return Schedule(())
        first = ScheduleStep(
            date=dates[0],
            producers=tuple(s.as_producer() for s in state.producers),
            injectors=tuple(s.as_injector() for s in state.injectors),
        )
        rest = tuple(ScheduleStep(day) for day in dates[1:])
        return Schedule((first, *rest)).canonical()


POLICIES: dict[str, Policy] = {
    policy.policy_id: policy for policy in (BaselineContinuationPolicy(), NoChangePolicy())
}
DEFAULT_POLICY_ID = BaselineContinuationPolicy().policy_id


def get_policy(policy_id: str) -> Policy:
    try:
        return POLICIES[policy_id]
    except KeyError:
        known = ", ".join(sorted(POLICIES))
        raise ValueError(f"unknown policy {policy_id!r}; known policies: {known}") from None
