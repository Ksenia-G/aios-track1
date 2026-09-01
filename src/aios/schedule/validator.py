"""Ворота допуска расписания к симуляции.

Проверка идёт до OPM, потому что дешевле отказать за миллисекунду, чем за
полторы минуты счёта, и потому что часть нарушений симулятор не заметит --
их заметит официальный калькулятор, обрушив весь экономический расчёт.

Режим отказа -- закрытый: любая непройденная проверка означает FAIL всего
пайплайна, а не предупреждение в отчёте.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from enum import StrEnum
from itertools import pairwise
from math import isfinite
from typing import Any

from aios.cases.contract import Case
from aios.schedule.events import (
    InjectorControl,
    ProducerControl,
    Schedule,
    WellRole,
    WellStatus,
)
from aios.schedule.state import FieldState


class ControlRepresentationError(ValueError):
    """Управление нельзя записать легальной строкой WCONPROD/WCONINJE."""


def require_representable_controls(
    producers: Sequence[ProducerControl],
    injectors: Sequence[InjectorControl],
) -> None:
    """Отвергнуть управления, которые симулятор не сможет разобрать.

    Открытая скважина обязана иметь величину своего режима: OPM отказывается
    разбирать `CMODE` без значения, и до этой проверки такой кандидат тратил
    полный прогон симулятора, чтобы упасть при разборе колоды. Закрытая
    скважина под правило не подпадает -- у неё величины нет по существу,
    и поле режима остаётся дефолтным.
    """
    for producer in producers:
        if producer.status is WellStatus.OPEN and producer.liquid_rate is None:
            raise ControlRepresentationError(
                f"producer {producer.well!r}: status OPEN requires a value for control "
                f"mode {producer.mode!r}; a rate mode without a value is not parsable"
            )
    for injector in injectors:
        if injector.status is WellStatus.OPEN and injector.surface_rate is None:
            raise ControlRepresentationError(
                f"injector {injector.well!r}: status OPEN requires a value for control "
                f"mode {injector.mode!r}; a rate mode without a value is not parsable"
            )


class GateStatus(StrEnum):
    PASS = "PASS"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class GateResult:
    """Итог одной проверки."""

    gate_id: str
    status: GateStatus
    detail: str = ""
    violations: tuple[str, ...] = ()

    def to_json(self) -> dict[str, Any]:
        return {
            "gate_id": self.gate_id,
            "status": str(self.status),
            "detail": self.detail,
            "violations": list(self.violations),
        }


@dataclass(frozen=True, slots=True)
class ScheduleValidationReport:
    """Полный отчёт по воротам."""

    gates: tuple[GateResult, ...]

    @property
    def is_valid(self) -> bool:
        return all(gate.status is GateStatus.PASS for gate in self.gates)

    @property
    def failed(self) -> tuple[GateResult, ...]:
        return tuple(gate for gate in self.gates if gate.status is GateStatus.FAIL)

    def to_json(self) -> list[dict[str, Any]]:
        return [gate.to_json() for gate in self.gates]


def _gate(gate_id: str, violations: Iterable[str], ok_detail: str) -> GateResult:
    found = tuple(violations)
    if found:
        return GateResult(gate_id, GateStatus.FAIL, f"{len(found)} violation(s)", found)
    return GateResult(gate_id, GateStatus.PASS, ok_detail)


def _check_wells_exist(schedule: Schedule, state: FieldState) -> GateResult:
    unknown = [well for well in schedule.controlled_wells if well not in state.known_wells]
    return _gate(
        "well_exists",
        (f"unknown well {well!r}" for well in unknown),
        f"{len(schedule.controlled_wells)} controlled well(s) known to the deck",
    )


def _check_dates(schedule: Schedule, case: Case) -> tuple[GateResult, GateResult]:
    dates = schedule.dates
    out_of_window = [
        day.isoformat()
        for day in dates
        if not case.management_start_date <= day <= case.management_end_date
    ]
    chronological = [
        f"{earlier.isoformat()} is not before {later.isoformat()}"
        for earlier, later in pairwise(dates)
        if earlier >= later
    ]
    return (
        _gate(
            "date_valid",
            (f"date {day} outside the management window" for day in out_of_window),
            f"{len(dates)} step date(s) inside the window",
        ),
        _gate(
            "chronological_dates",
            chronological,
            "step dates strictly increasing",
        ),
    )


def _check_history_untouched(schedule: Schedule, case: Case) -> GateResult:
    dates = schedule.dates
    problems: list[str] = []
    if not dates:
        problems.append("schedule is empty")
    else:
        if dates[0] != case.management_start_date:
            problems.append(
                f"first step {dates[0].isoformat()} != management start "
                f"{case.management_start_date.isoformat()}"
            )
        early = [day for day in dates if day < case.management_start_date]
        problems.extend(f"step {day.isoformat()} would rewrite management history" for day in early)
    return _gate("history_untouched", problems, "no step precedes the management start")


def _check_control_types(schedule: Schedule, case: Case) -> GateResult:
    allowed_producer = set(case.constraints.allowed_producer_controls)
    allowed_injector = set(case.constraints.allowed_injector_controls)
    problems: list[str] = []
    for step in schedule.steps:
        for control in step.producers:
            if control.mode not in allowed_producer:
                problems.append(
                    f"{step.date.isoformat()} well {control.well}: producer control "
                    f"{control.mode!r} not allowed"
                )
        for control in step.injectors:
            if control.mode not in allowed_injector:
                problems.append(
                    f"{step.date.isoformat()} well {control.well}: injector control "
                    f"{control.mode!r} not allowed"
                )
    return _gate("control_type_allowed", problems, "all control modes allowed by the case")


def _check_no_conversion(schedule: Schedule, state: FieldState, case: Case) -> GateResult:
    if case.constraints.allow_producer_to_injector:
        return GateResult(
            "no_forbidden_conversion", GateStatus.PASS, "conversion allowed by the case"
        )
    roles = {well: st.role for well, st in state.by_well.items()}
    problems: list[str] = []
    for step in schedule.steps:
        for control in step.producers:
            if roles.get(control.well) is WellRole.INJECTOR:
                problems.append(
                    f"{step.date.isoformat()} well {control.well}: injector -> producer conversion"
                )
            roles[control.well] = WellRole.PRODUCER
        for control in step.injectors:
            if roles.get(control.well) is WellRole.PRODUCER:
                problems.append(
                    f"{step.date.isoformat()} well {control.well}: producer -> injector conversion"
                )
            roles[control.well] = WellRole.INJECTOR
    return _gate("no_forbidden_conversion", problems, "no well changes role inside the window")


def _check_rate_limits(schedule: Schedule, case: Case) -> tuple[GateResult, GateResult]:
    limit = case.constraints.producer_wlpr_max
    injector_limit = case.constraints.injector_rate_max
    over: list[str] = []
    nonfinite: list[str] = []
    for step in schedule.steps:
        for control in step.producers:
            rate = control.liquid_rate
            if rate is None:
                continue
            if not isfinite(rate):
                nonfinite.append(f"{step.date.isoformat()} well {control.well}: LRAT is not finite")
            elif rate > limit:
                over.append(f"{step.date.isoformat()} well {control.well}: LRAT {rate} > {limit}")
            elif rate < 0:
                over.append(f"{step.date.isoformat()} well {control.well}: LRAT {rate} < 0")
        for control in step.injectors:
            rate = control.surface_rate
            if rate is None:
                continue
            if not isfinite(rate):
                nonfinite.append(f"{step.date.isoformat()} well {control.well}: RATE is not finite")
            elif rate < 0:
                over.append(f"{step.date.isoformat()} well {control.well}: RATE {rate} < 0")
            elif injector_limit is not None and rate > injector_limit:
                over.append(
                    f"{step.date.isoformat()} well {control.well}: RATE {rate} > {injector_limit}"
                )
        for control in (*step.producers, *step.injectors):
            if control.bhp_limit is not None and not isfinite(control.bhp_limit):
                nonfinite.append(
                    f"{step.date.isoformat()} well {control.well}: BHP limit is not finite"
                )
    return (
        _gate("rate_limits", over, f"producer LRAT within {limit}, injector rates non-negative"),
        _gate("finite_targets", nonfinite, "all numeric targets finite"),
    )


def _check_bhp_limits(schedule: Schedule, case: Case) -> GateResult:
    producer_floor = case.constraints.producer_bhp_min
    injector_ceiling = case.constraints.injector_bhp_max
    if producer_floor is None and injector_ceiling is None:
        return GateResult("bhp_limits", GateStatus.PASS, "no BHP limits configured")
    problems: list[str] = []
    for step in schedule.steps:
        if producer_floor is not None:
            problems.extend(
                f"{step.date.isoformat()} well {c.well}: BHP {c.bhp_limit} < {producer_floor}"
                for c in step.producers
                if c.bhp_limit is not None and c.bhp_limit < producer_floor
            )
        if injector_ceiling is not None:
            problems.extend(
                f"{step.date.isoformat()} well {c.well}: BHP {c.bhp_limit} > {injector_ceiling}"
                for c in step.injectors
                if c.bhp_limit is not None and c.bhp_limit > injector_ceiling
            )
    return _gate("bhp_limits", problems, "BHP limits respected")


def _check_field_injection(schedule: Schedule, case: Case) -> GateResult:
    ceiling = case.constraints.field_injection_rate_max
    if ceiling is None:
        return GateResult("field_injection_limit", GateStatus.PASS, "no field limit configured")
    active: dict[str, float] = {}
    problems: list[str] = []
    for step in schedule.steps:
        for control in step.injectors:
            active[control.well] = control.surface_rate or 0.0
        total = sum(active.values())
        if total > ceiling:
            problems.append(f"{step.date.isoformat()}: field injection {total} > {ceiling}")
    return _gate("field_injection_limit", problems, f"field injection within {ceiling}")


def _check_duplicates(schedule: Schedule) -> GateResult:
    problems: list[str] = []
    for step in schedule.steps:
        seen: dict[str, str] = {}
        for keyword, controls in (("WCONPROD", step.producers), ("WCONINJE", step.injectors)):
            for control in controls:
                previous = seen.get(control.well)
                if previous is not None:
                    # Дословные повторы уже свёрнуты канонизацией, значит здесь
                    # два разных управления на одну скважину и одну дату.
                    problems.append(
                        f"{step.date.isoformat()} well {control.well}: conflicting control "
                        f"in {previous} and {keyword}"
                    )
                seen[control.well] = keyword
    return _gate("no_duplicate_control", problems, "one control per well per date")


def validate_schedule(
    schedule: Schedule, state: FieldState, case: Case
) -> ScheduleValidationReport:
    """Прогнать все ворота. Порядок фиксирован, чтобы отчёт был сравним между прогонами."""

    date_gate, chronology_gate = _check_dates(schedule, case)
    rate_gate, finite_gate = _check_rate_limits(schedule, case)
    return ScheduleValidationReport(
        (
            _check_wells_exist(schedule, state),
            date_gate,
            chronology_gate,
            _check_history_untouched(schedule, case),
            _check_control_types(schedule, case),
            _check_no_conversion(schedule, state, case),
            rate_gate,
            finite_gate,
            _check_bhp_limits(schedule, case),
            _check_field_injection(schedule, case),
            _check_duplicates(schedule),
        )
    )
