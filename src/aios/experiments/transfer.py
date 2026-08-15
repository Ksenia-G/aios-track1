"""Наложение «перенос закачки» поверх опорной политики.

Единственное воздействие эксперимента: у одной нагнетательной скважины
плановая закачка уменьшается, у другой -- увеличивается ровно на ту же
величину. Всё остальное расписание остаётся опорным.

Величина переноса не фиксированная, а пропорциональная: на каждом шаге
управления `delta = fraction × donor_baseline_target`. Поэтому плановая
сумма пары сохраняется на каждом шаге, а не только в среднем по горизонту,
и профиль закачки донора не ломается там, где опорная колода его меняет.

Пара выписывается на шаге, если опорная колода трогает донора или
получателя, и обязательно на первом шаге окна -- иначе воздействие начнётся
не с начала окна, а с первого попавшегося изменения.
"""

from dataclasses import dataclass, replace
from datetime import date

from aios.schedule.events import InjectorControl, Schedule, ScheduleStep
from aios.schedule.state import FieldState


class TransferError(ValueError):
    """Перенос нельзя применить к этому состоянию или расписанию."""


@dataclass(frozen=True, slots=True)
class InjectionTransfer:
    """Контракт воздействия."""

    donor_well: str
    receiver_well: str
    fraction: float
    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.donor_well == self.receiver_well:
            raise TransferError("donor and receiver must be different wells")
        if not 0.0 < self.fraction < 1.0:
            raise TransferError(f"fraction must lie in (0, 1), got {self.fraction}")
        if self.end_date <= self.start_date:
            raise TransferError("transfer end date must be after its start date")

    @property
    def wells(self) -> tuple[str, str]:
        return self.donor_well, self.receiver_well


@dataclass(frozen=True, slots=True)
class InjectionTransferPolicy:
    """Опорная политика плюс один перенос закачки."""

    transfer: InjectionTransfer
    policy_id: str

    def plan(self, state: FieldState, baseline: Schedule) -> Schedule:
        donor, receiver = self.transfer.wells
        by_well = state.by_well
        for well in (donor, receiver):
            well_state = by_well.get(well)
            if well_state is None:
                raise TransferError(f"well {well} has no reconstructed state at the window start")
            if well_state.role.name != "INJECTOR":
                raise TransferError(f"well {well} is not an injector at the window start")
            if well_state.target is None:
                raise TransferError(f"well {well} has no injection target at the window start")

        donor_template = by_well[donor].as_injector()
        receiver_template = by_well[receiver].as_injector()
        donor_target = float(donor_template.surface_rate or 0.0)
        receiver_target = float(receiver_template.surface_rate or 0.0)

        canonical = baseline.canonical()
        steps: list[ScheduleStep] = []
        for index, step in enumerate(canonical.steps):
            touched = False
            for control in step.injectors:
                if control.well == donor:
                    donor_template = control
                    donor_target = float(control.surface_rate or 0.0)
                    touched = True
                elif control.well == receiver:
                    receiver_template = control
                    receiver_target = float(control.surface_rate or 0.0)
                    touched = True

            in_window = self.transfer.start_date <= step.date <= self.transfer.end_date
            if not in_window or not (touched or index == 0):
                steps.append(step)
                continue

            delta = self.transfer.fraction * donor_target if donor_target > 0.0 else 0.0
            others = tuple(c for c in step.injectors if c.well not in (donor, receiver))
            steps.append(
                ScheduleStep(
                    date=step.date,
                    producers=step.producers,
                    injectors=(
                        *others,
                        replace(donor_template, surface_rate=donor_target - delta),
                        replace(receiver_template, surface_rate=receiver_target + delta),
                    ),
                )
            )
        return Schedule(tuple(steps)).canonical()


def baseline_pair_targets(
    state: FieldState, baseline: Schedule, donor: str, receiver: str
) -> dict[date, tuple[float, float]]:
    """Опорные действующие уставки пары на каждом шаге окна.

    Нужны воротам: сохранение суммы проверяется против этих чисел, а не
    против того, что политика сама себе насчитала.
    """

    by_well = state.by_well
    donor_state, receiver_state = by_well.get(donor), by_well.get(receiver)
    if donor_state is None or receiver_state is None:
        raise TransferError("pair is not present in the reconstructed state")
    donor_target = float(donor_state.target or 0.0)
    receiver_target = float(receiver_state.target or 0.0)

    effective: dict[date, tuple[float, float]] = {}
    for step in baseline.canonical().steps:
        for control in step.injectors:
            if control.well == donor:
                donor_target = float(control.surface_rate or 0.0)
            elif control.well == receiver:
                receiver_target = float(control.surface_rate or 0.0)
        effective[step.date] = (donor_target, receiver_target)
    return effective


def candidate_pair_targets(
    state: FieldState, candidate: Schedule, donor: str, receiver: str
) -> dict[date, tuple[float, float]]:
    """То же самое, но по расписанию кандидата."""

    return baseline_pair_targets(state, candidate, donor, receiver)


def injector_control_of(step: ScheduleStep, well: str) -> InjectorControl | None:
    for control in step.injectors:
        if control.well == well:
            return control
    return None
