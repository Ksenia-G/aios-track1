"""Ворота эксперимента поверх ворот VALIDATION-001.

Проверяется не намерение, а результат: расписание кандидата разбирается
обратно из сгенерированного текста и сравнивается с опорным. Именно эти
байты увидит симулятор, и именно на них должно держаться утверждение, что
изменилась ровно одна пара нагнетательных скважин и что плановая сумма пары
сохранена на каждом шаге.
"""

from dataclasses import dataclass
from math import isclose, isfinite
from typing import Any

from aios.cases.contract import Case
from aios.experiments.blocks import block_of_injector
from aios.experiments.scenarios import Scenario
from aios.experiments.transfer import baseline_pair_targets
from aios.schedule.events import Schedule
from aios.schedule.state import FieldState
from aios.schedule.validator import GateResult, GateStatus, ScheduleValidationReport

# Допуск сравнения плановых уставок. Числа приходят из напечатанного и
# заново разобранного файла, поэтому запас нужен только на печать.
TARGET_TOLERANCE = 1e-9


def _gate(gate_id: str, violations: list[str], ok_detail: str) -> GateResult:
    if violations:
        return GateResult(
            gate_id, GateStatus.FAIL, f"{len(violations)} violation(s)", tuple(violations)
        )
    return GateResult(gate_id, GateStatus.PASS, ok_detail)


def _check_pair(scenario: Scenario, state: FieldState) -> GateResult:
    problems: list[str] = []
    if scenario.donor == scenario.receiver:
        problems.append("donor and receiver are the same well")
    by_well = state.by_well
    for role_label, well in (("donor", scenario.donor), ("receiver", scenario.receiver)):
        well_state = by_well.get(well)
        if well_state is None:
            problems.append(f"{role_label} {well!r} is unknown at the window start")
        elif well_state.role.name != "INJECTOR":
            problems.append(f"{role_label} {well!r} is not an injector ({well_state.role})")
    donor_block = block_of_injector(scenario.donor)
    receiver_block = block_of_injector(scenario.receiver)
    if donor_block is None or receiver_block is None:
        problems.append("donor or receiver does not belong to a screening block")
    elif donor_block.block_id != receiver_block.block_id:
        problems.append(
            f"donor is in block {donor_block.block_id}, receiver in block {receiver_block.block_id}"
        )
    elif donor_block.block_id != scenario.block_id:
        problems.append(
            f"scenario claims block {scenario.block_id}, pair is in {donor_block.block_id}"
        )
    if not 0.0 < scenario.fraction < 1.0:
        problems.append(f"fraction {scenario.fraction} is outside (0, 1)")
    return _gate("transfer_pair_valid", problems, "same-block injector pair, fraction in (0, 1)")


def _check_producers_untouched(baseline: Schedule, candidate: Schedule) -> GateResult:
    problems: list[str] = []
    base_steps = {step.date: step for step in baseline.canonical().steps}
    for step in candidate.canonical().steps:
        base = base_steps.get(step.date)
        if base is None:
            problems.append(f"{step.date.isoformat()}: step absent from the baseline")
            continue
        if step.producers != base.producers:
            problems.append(f"{step.date.isoformat()}: producer controls differ from the baseline")
    if len(base_steps) != len(candidate.steps):
        problems.append(
            f"step count changed: baseline {len(base_steps)}, candidate {len(candidate.steps)}"
        )
    return _gate("producers_untouched", problems, "producer controls identical to the baseline")


def _check_other_injectors_untouched(
    scenario: Scenario, baseline: Schedule, candidate: Schedule
) -> GateResult:
    pair = {scenario.donor, scenario.receiver}
    problems: list[str] = []
    base_steps = {step.date: step for step in baseline.canonical().steps}
    for step in candidate.canonical().steps:
        base = base_steps.get(step.date)
        if base is None:
            continue
        candidate_others = tuple(c for c in step.injectors if c.well not in pair)
        baseline_others = tuple(c for c in base.injectors if c.well not in pair)
        if candidate_others != baseline_others:
            problems.append(f"{step.date.isoformat()}: injector controls outside the pair differ")
    return _gate("other_injectors_untouched", problems, "only the donor/receiver pair differs")


def _check_conservation(
    scenario: Scenario, state: FieldState, baseline: Schedule, candidate: Schedule
) -> tuple[GateResult, GateResult]:
    donor, receiver = scenario.donor, scenario.receiver
    base_targets = baseline_pair_targets(state, baseline, donor, receiver)
    candidate_targets = baseline_pair_targets(state, candidate, donor, receiver)

    conservation: list[str] = []
    magnitude: list[str] = []
    for day, (base_donor, base_receiver) in sorted(base_targets.items()):
        cand_donor, cand_receiver = candidate_targets[day]
        if not all(isfinite(v) for v in (cand_donor, cand_receiver)):
            magnitude.append(f"{day.isoformat()}: non-finite candidate target")
            continue
        if cand_donor < 0.0 or cand_receiver < 0.0:
            magnitude.append(
                f"{day.isoformat()}: negative target donor={cand_donor} receiver={cand_receiver}"
            )
        if not isclose(
            cand_donor + cand_receiver,
            base_donor + base_receiver,
            rel_tol=0.0,
            abs_tol=TARGET_TOLERANCE,
        ):
            conservation.append(
                f"{day.isoformat()}: pair total {cand_donor + cand_receiver} != "
                f"baseline {base_donor + base_receiver}"
            )
        expected_delta = scenario.fraction * base_donor if base_donor > 0.0 else 0.0
        if not isclose(
            base_donor - cand_donor, expected_delta, rel_tol=0.0, abs_tol=TARGET_TOLERANCE
        ):
            magnitude.append(
                f"{day.isoformat()}: donor reduction {base_donor - cand_donor} != "
                f"expected {expected_delta}"
            )
    return (
        _gate(
            "pair_injection_conserved",
            conservation,
            f"pair total conserved at all {len(base_targets)} control steps",
        ),
        _gate(
            "transfer_magnitude_correct",
            magnitude,
            f"donor reduced by fraction x baseline at all {len(base_targets)} steps",
        ),
    )


@dataclass(frozen=True, slots=True)
class ExperimentGateReport:
    """Ворота эксперимента вместе с воротами VALIDATION-001."""

    base: ScheduleValidationReport
    experiment: tuple[GateResult, ...]

    @property
    def is_valid(self) -> bool:
        return self.base.is_valid and all(
            gate.status is GateStatus.PASS for gate in self.experiment
        )

    @property
    def failed(self) -> tuple[GateResult, ...]:
        return (
            *self.base.failed,
            *(gate for gate in self.experiment if gate.status is GateStatus.FAIL),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "validation_001": self.base.to_json(),
            "experiment": [gate.to_json() for gate in self.experiment],
        }


def validate_scenario(
    scenario: Scenario,
    *,
    case: Case,
    state: FieldState,
    baseline: Schedule,
    candidate: Schedule,
    base_report: ScheduleValidationReport,
) -> ExperimentGateReport:
    """Прогнать ворота эксперимента. Порядок фиксирован."""

    if scenario.is_baseline:
        identical = _gate(
            "baseline_is_unmodified",
            []
            if candidate.canonical() == baseline.canonical()
            else ["control run differs from the baseline schedule"],
            "control run reproduces the baseline schedule",
        )
        return ExperimentGateReport(base=base_report, experiment=(identical,))

    conserved, magnitude = _check_conservation(scenario, state, baseline, candidate)
    return ExperimentGateReport(
        base=base_report,
        experiment=(
            _check_pair(scenario, state),
            _check_producers_untouched(baseline, candidate),
            _check_other_injectors_untouched(scenario, baseline, candidate),
            conserved,
            magnitude,
        ),
    )
