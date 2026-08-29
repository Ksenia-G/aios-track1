"""Компиляция MagnitudeCandidate в детерминированное complete schedule.

Это первая ступень зоны runtime/execution: наверху решено, *какой* численный
кандидат проверить; здесь решается, как он без искажений превращается в файл
управления для симулятора. Компилятор ничего не интерпретирует и не улучшает:
SMALL/MODERATE/AGGRESSIVE до него не доходят, приходят готовые
ProducerControl/InjectorControl из frozen-контракта ``aios.mas.magnitude``.

Семантика complete schedule (требование организаторов: изменённые и
неизменённые режимы обязаны быть представлены явно, так, как их читает
симулятор):

* сетка дат берётся из опорного расписания кейса -- горизонт задаёт кейс,
  а не компилятор;
* на дату действия выписывается **полный** фонд: управления кандидата,
  наложенные на срез состояния, включая нетронутые скважины;
* если действие начинается позже открытия окна, первый шаг окна явно несёт
  carry-forward состояния фонда;
* остальные шаги пустые: по семантике расписания симулятора управление
  действует до следующего явного изменения. Пустой шаг продвигает время,
  а не обнуляет режимы.

Детерминизм: вход канонизируется, шаги и скважины упорядочивает существующий
``Schedule.canonical()``, текст печатает единственный генератор чисел
(``aios.schedule.generator``), поэтому один и тот же кандидат даёт один и тот
же SHA-256 независимо от порядка входных кортежей.

HOLD -- точный carry-forward предыдущих canonical controls. Никакого
пересчёта и никакого float tolerance: проверка делегируется готовому
инварианту ``require_hold_carry_forward``, а не reimplementится здесь.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from aios.cases.contract import Case
from aios.evaluation.canonical import JsonValue
from aios.execution.history import CommittedHistory, CommittedHistoryError
from aios.mas.common import CandidateKind
from aios.mas.invariants import (
    canonical_control_digest,
    canonical_control_snapshot,
    require_hold_carry_forward,
)
from aios.mas.magnitude import MagnitudeCandidate
from aios.schedule.events import (
    InjectorControl,
    ProducerControl,
    Schedule,
    ScheduleStep,
    WellRole,
    natural_key,
)
from aios.schedule.generator import render_schedule, schedule_sha256
from aios.schedule.state import FieldState
from aios.schedule.validator import require_representable_controls

COMPILER_ID = "aios.execution.compiler"
COMPILER_VERSION = "1.0.0"


class ScheduleCompilationError(RuntimeError):
    """Кандидат не компилируется в complete schedule без искажений."""


@dataclass(frozen=True, slots=True)
class CompiledCandidateSchedule:
    """Результат компиляции: расписание, его текст и провенанс компиляции."""

    schedule: Schedule
    schedule_text: str
    schedule_sha256: str
    action_date: date
    candidate_digest: str
    baseline_control_digest: str
    action_control_digest: str
    policy_id: str
    compiler_id: str = COMPILER_ID
    compiler_version: str = COMPILER_VERSION

    def compilation_to_json(self) -> dict[str, JsonValue]:
        """Провенанс компиляции без тяжёлого тела расписания."""
        return {
            "compiler_id": self.compiler_id,
            "compiler_version": self.compiler_version,
            "action_date": self.action_date.isoformat(),
            "candidate_digest": self.candidate_digest,
            "baseline_control_digest": self.baseline_control_digest,
            "action_control_digest": self.action_control_digest,
            "schedule_sha256": self.schedule_sha256,
            "policy_id": self.policy_id,
        }


def _validated_baseline_controls(
    state: FieldState,
) -> tuple[tuple[ProducerControl, ...], tuple[InjectorControl, ...]]:
    """Срез состояния в виде authoritative controls (порядок -- natural_key)."""
    return (
        tuple(producer.as_producer() for producer in state.producers),
        tuple(injector.as_injector() for injector in state.injectors),
    )


def _check_candidate_wells(candidate: MagnitudeCandidate, state: FieldState) -> None:
    """Скважины кандидата существуют и не меняют роль (конверсия запрещена)."""
    for control in candidate.producers:
        role = state.role_of(control.well)
        if role is None:
            raise ScheduleCompilationError(
                f"candidate controls unknown well {control.well!r}: "
                "well is absent from the reconstructed fund state"
            )
        if role is not WellRole.PRODUCER:
            raise ScheduleCompilationError(
                f"candidate treats {control.well!r} as a producer, "
                f"but the fund state holds it as {role!s}"
            )
    for control in candidate.injectors:
        role = state.role_of(control.well)
        if role is None:
            raise ScheduleCompilationError(
                f"candidate controls unknown well {control.well!r}: "
                "well is absent from the reconstructed fund state"
            )
        if role is not WellRole.INJECTOR:
            raise ScheduleCompilationError(
                f"candidate treats {control.well!r} as an injector, "
                f"but the fund state holds it as {role!s}"
            )


def _merge_controls(
    baseline: tuple[ProducerControl, ...],
    override: tuple[ProducerControl, ...],
) -> tuple[ProducerControl, ...]:
    """Наложить управления кандидата на состояние фонда."""
    merged = {control.well: control for control in baseline}
    for control in override:
        merged[control.well] = control
    return tuple(sorted(merged.values(), key=lambda control: natural_key(control.well)))


def compile_candidate_schedule(
    candidate: MagnitudeCandidate,
    *,
    case: Case,
    state: FieldState,
    baseline: Schedule,
    policy_id: str | None = None,
    committed_history: CommittedHistory | None = None,
    require_history_before: date | None = None,
) -> CompiledCandidateSchedule:
    """Скомпилировать кандидата в complete schedule управляемого окна.

    ``policy_id`` попадает в шапку сгенерированного файла; по умолчанию
    выводится из вида кандидата, поэтому одинаковый вход даёт одинаковый текст.

    ``committed_history`` -- неизменяемая история управлений предыдущих эпох.
    Кандидат владеет только ``[effective_date, H]``: всё, что раньше, выписывается
    из истории и кандидатом не переписывается. Пустая история means первая
    эпоха -- тогда префиксом остаётся baseline окна.

    ``require_history_before`` -- дата, до которой история обязана существовать.
    Шаг ``N > 0`` без истории -- это молчаливый откат к исходному baseline, и он
    закрывается отказом, а не тихо принимается.
    """
    canonical = candidate.canonical()
    grid = baseline.canonical().dates
    if not grid:
        raise ScheduleCompilationError("baseline schedule has no steps to inherit the grid from")
    if grid[0] != case.management_start_date or grid[-1] != case.management_end_date:
        raise ScheduleCompilationError(
            "baseline grid does not match the case management window: "
            f"grid {grid[0].isoformat()} .. {grid[-1].isoformat()}, "
            f"case {case.management_start_date.isoformat()} .. "
            f"{case.management_end_date.isoformat()}"
        )
    if canonical.effective_date not in grid:
        raise ScheduleCompilationError(
            f"candidate effective_date {canonical.effective_date.isoformat()} "
            "is not a report step of the case grid"
        )
    _check_candidate_wells(canonical, state)

    # Ранняя проверка двусмысленностей: два разных управления одной скважиной
    # внутри кандидата запрещены тем же правилом, что и в расписании.
    canonical_control_snapshot(canonical.producers, canonical.injectors)

    baseline_producers, baseline_injectors = _validated_baseline_controls(state)
    history = committed_history if committed_history is not None else CommittedHistory()

    if require_history_before is not None and not history.before(require_history_before):
        raise CommittedHistoryError(
            "compile_candidate_schedule: an epoch after the first requires committed history "
            f"before {require_history_before.isoformat()}; falling back to the window baseline "
            "would silently discard the policy already in force"
        )

    # Управления, реально стоящие в момент действия: до первого коммита это
    # baseline окна, после -- последний закоммиченный интервал. Именно против
    # них проверяется HOLD, и именно на них накладывается не-HOLD кандидат.
    in_force_producers, in_force_injectors = history.controls_in_force_at(
        canonical.effective_date,
        baseline_producers=baseline_producers,
        baseline_injectors=baseline_injectors,
    )

    if canonical.candidate_kind is CandidateKind.HOLD:
        held_producers = canonical.producers or in_force_producers
        held_injectors = canonical.injectors or in_force_injectors
        require_hold_carry_forward(
            previous_producers=in_force_producers,
            previous_injectors=in_force_injectors,
            held_producers=held_producers,
            held_injectors=held_injectors,
        )
        action_producers, action_injectors = held_producers, held_injectors
    elif canonical.candidate_kind is CandidateKind.DETERMINISTIC_BASELINE:
        # Опорная политика применяется ОТ текущей эпохи: её численный источник
        # -- baseline окна, но история до эпохи остаётся неизменной.
        action_producers = _merge_controls(baseline_producers, canonical.producers)
        action_injectors = _merge_controls(baseline_injectors, canonical.injectors)
    else:
        action_producers = _merge_controls(in_force_producers, canonical.producers)
        action_injectors = _merge_controls(in_force_injectors, canonical.injectors)

    # Управление, которое нельзя записать легальной строкой WCONPROD/WCONINJE,
    # обязано быть отвергнуто ЗДЕСЬ, а не потратить полный прогон симулятора,
    # чтобы упасть при разборе колоды.
    require_representable_controls(action_producers, action_injectors)

    # Композитное расписание: закоммиченная история на [T0, effective_date),
    # затем управления кандидата. История выписывается явно на своих датах --
    # неизменённые режимы обязаны быть представлены в файле, а не угаданы.
    committed_by_date = {
        item.effective_date: (item.producers, item.injectors)
        for item in history.before(canonical.effective_date)
    }
    steps: list[ScheduleStep] = []
    for day in grid:
        if day == canonical.effective_date:
            steps.append(ScheduleStep(day, action_producers, action_injectors))
        elif day in committed_by_date:
            committed_producers, committed_injectors = committed_by_date[day]
            steps.append(ScheduleStep(day, committed_producers, committed_injectors))
        elif day == grid[0]:
            # Окно открылось раньше действия: явный carry-forward фонда.
            steps.append(ScheduleStep(day, baseline_producers, baseline_injectors))
        else:
            steps.append(ScheduleStep(day))

    schedule = Schedule(tuple(steps)).canonical()
    resolved_policy_id = policy_id or f"{COMPILER_ID}/{canonical.candidate_kind!s}"
    schedule_text = render_schedule(schedule, case, policy_id=resolved_policy_id)
    return CompiledCandidateSchedule(
        schedule=schedule,
        schedule_text=schedule_text,
        schedule_sha256=schedule_sha256(schedule_text),
        action_date=canonical.effective_date,
        candidate_digest=canonical.digest(),
        baseline_control_digest=canonical_control_digest(baseline_producers, baseline_injectors),
        action_control_digest=canonical_control_digest(action_producers, action_injectors),
        policy_id=resolved_policy_id,
    )
