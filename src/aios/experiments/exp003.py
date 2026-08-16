"""EXP-003: robustness envelope around the EXP-002 winner.

Здесь три новые вещи поверх машинерии EXP-002.

1. **Дополнительная закачка.** До сих пор всякое воздействие только
   перераспределяло плановую закачку и сохраняло сумму пары. Компенсация
   этого правила не подчиняется: она добавляет объём. Поэтому сохранение
   проверяется не как «сумма пары равна опорной», а как «сумма пары равна
   опорной плюс явно заявленная добавка». Ворота остаются точными.

2. **Водный учёт.** Считаются накопленная добыча воды и накопленная
   фактическая закачка на окне управления, помесячно и суммарно. Это
   поверхностный учёт по модели. Он **не** называется VRR и не выдаётся за
   замещение отбора в пластовых условиях: официальная трактовка водного
   баланса не определена.

3. **Фронт Парето.** Кандидаты сравниваются сразу по трём осям и не
   сворачиваются в один взвешенный балл: веса здесь пришлось бы придумать,
   а придуманный вес -- это спрятанное решение за организатора.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.chdd.runner import ChddResult
from aios.experiments.blocks import BLOCKS_BY_ID
from aios.experiments.exp002 import (
    CandidateType,
    Exp002Scenario,
    TransferMode,
    TransferSpec,
    _effective_targets,
    _float_or_none,
    _gate,
    _other_injector_violations,
    _pre_window_violations,
    _producer_violations,
    _target_violations,
    _validate_transfers,
    assert_non_overlapping,
    saturation_for_well,
    totals_from_chdd,
)
from aios.experiments.scenarios import fraction_tag
from aios.opm.summary import SummaryData
from aios.schedule.events import InjectorControl, Schedule, ScheduleStep, natural_key
from aios.schedule.generator import render_schedule
from aios.schedule.state import FieldState
from aios.schedule.validator import GateResult, GateStatus, validate_schedule

BASELINE_NPV_MLN_RUB = 1030.8776958837277
BASELINE_SCHEDULE_SHA256 = "7662d10495796ace6b0d20df404ca68932b2894f367de2062c232cbe3e01c399"
EXP002_WINNER_NPV_MLN_RUB = 1166.9843692054437
EXP002_WINNER_SCHEDULE_SHA256 = "aa494b9d25a42a3a35d7567e55958a5de73bc3d6f41061a39f7ec9b0ef13421d"
EXP001_BEST_NPV_MLN_RUB = 1051.6400268038235
RECONCILE_TOLERANCE = 1e-6

EXP002_WINNER_TRANSFERS: tuple[tuple[int, str, str, float], ...] = (
    (2, "41", "19", 0.40),
    (3, "32", "4", 0.80),
    (4, "7", "42", 0.90),
    (5, "26", "2", 0.90),
)

# Уточняющая сетка. Доли, уже измеренные в EXP-002, здесь не повторяются:
# они входят в общую кривую как проверенные точки.
STAGE_A_GRID: dict[tuple[int, str, str], tuple[float, ...]] = {
    (2, "41", "19"): (0.30, 0.35, 0.45, 0.50, 0.60),
    (3, "32", "4"): (0.75, 0.825, 0.85, 0.875),
    (4, "7", "42"): (0.925, 0.95, 0.975),
    (5, "26", "2"): (0.925, 0.95, 0.975),
}

SCALING_ALPHAS: tuple[float, ...] = (0.40, 0.55, 0.70, 0.85)
SUPPLEMENT_RATES: tuple[float, ...] = (20.0, 40.0, 60.0, 80.0, 100.0)
PRIMARY_SUPPLEMENT_WELL = "42"

# Доля округляется до сотой доли процента: идентификатор сценария должен
# однозначно задавать расписание.
FRACTION_QUANTUM = 1e-4
NEAR_BASELINE_INJECTION_PCT = 1.0
COMPENSATION_TRIGGER_SHORTFALL_M3 = 10000.0
BHP_SATURATION_MIN_HITS = 1

STAGE_RECONCILIATION = "R"
STAGE_LOCAL_REFINEMENT = "A"
STAGE_REFINED_QUAD = "B"
STAGE_SCALED_QUAD = "C"
STAGE_COMPENSATION = "D"
STAGE_SECONDARY_COMPENSATION = "E"


class KeyCandidate(StrEnum):
    MAX_NPV = "MAX_NPV"
    NEAR_BASELINE_INJECTION = "NEAR_BASELINE_INJECTION"
    LOW_PRESSURE_DEVIATION = "LOW_PRESSURE_DEVIATION"


class WaterBalanceStatus(StrEnum):
    ORGANIZER_RULE_UNRESOLVED = "ORGANIZER_RULE_UNRESOLVED"


class RefinementClass(StrEnum):
    LOCAL_BEST_TESTED = "LOCAL_BEST_TESTED"
    BOUNDARY_BEST = "BOUNDARY_BEST"
    PEAK_REGION_BRACKETED = "PEAK_REGION_BRACKETED"
    CONSTRAINT_SATURATED = "CONSTRAINT_SATURATED"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


def quantize_fraction(value: float) -> float:
    """Привести долю к сетке в сотую долю процента."""

    return round(value / FRACTION_QUANTUM) * FRACTION_QUANTUM


@dataclass(frozen=True, slots=True)
class SupplementalInjection:
    """Постоянная добавка к плановой закачке одной скважины на окне.

    Это единственное воздействие эксперимента, которое меняет суммарный
    плановый объём, а не только его распределение, поэтому оно объявляется
    отдельным типом и отдельно проверяется воротами.
    """

    well: str
    added_rate_m3_per_day: float

    def __post_init__(self) -> None:
        if self.added_rate_m3_per_day <= 0.0:
            raise ValueError("supplemental injection must be positive")
        if not math.isfinite(self.added_rate_m3_per_day):
            raise ValueError("supplemental injection must be finite")

    @property
    def component_id(self) -> str:
        rate = self.added_rate_m3_per_day
        text = f"{rate:g}".replace(".", "p")
        return f"ADD_{self.well}_{text}"

    def to_json(self) -> dict[str, Any]:
        return {"well": self.well, "added_rate_m3_per_day": self.added_rate_m3_per_day}


@dataclass(frozen=True, slots=True)
class Exp003Scenario:
    """Сценарий EXP-003: набор переносов плюс, возможно, добавки закачки."""

    scenario_id: str
    stage: str
    candidate_type: CandidateType
    transfers: tuple[TransferSpec, ...]
    supplements: tuple[SupplementalInjection, ...] = ()
    label: str = ""
    # Метка политики попадает в шапку `wells_schedule.inc`, а значит и в его
    # SHA. Повтор чужого сценария обязан печатать чужую метку -- иначе файл
    # получится другой, и сверка по SHA потеряет смысл.
    render_policy_id: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "stage": self.stage,
            "candidate_type": self.candidate_type.value,
            "transfers": [transfer.to_json() for transfer in self.transfers],
            "supplements": [item.to_json() for item in self.supplements],
            "label": self.label,
            "render_policy_id": self.render_policy_id,
        }

    @property
    def as_exp002(self) -> Exp002Scenario:
        """Вид, который понимают уже проверенные помощники EXP-002."""

        return Exp002Scenario(
            scenario_id=self.scenario_id,
            stage=self.stage,
            candidate_type=self.candidate_type,
            transfers=self.transfers,
        )

    @property
    def supplement_by_well(self) -> dict[str, float]:
        return {item.well: item.added_rate_m3_per_day for item in self.supplements}


def transfer(block_id: int, donor: str, receiver: str, fraction: float) -> TransferSpec:
    return TransferSpec(block_id, donor, receiver, quantize_fraction(fraction))


def exp002_winner_transfers() -> tuple[TransferSpec, ...]:
    return tuple(transfer(*spec) for spec in EXP002_WINNER_TRANSFERS)


def quad_id(prefix: str, transfers: Sequence[TransferSpec]) -> str:
    assert_non_overlapping(transfers)
    parts = sorted(item.component_id for item in transfers)
    return f"{prefix}_" + "__".join(parts)


EXP002_WINNER_SCENARIO_ID = (
    "EXP002_D_B2_41_TO_19_F40__B3_32_TO_4_F80__B4_7_TO_42_F90__B5_26_TO_2_F90"
)
EXP002_WINNER_POLICY_ID = f"EXP002::{EXP002_WINNER_SCENARIO_ID}"


def reconciliation_scenario() -> Exp003Scenario:
    """Повтор победителя EXP-002 в этом worktree.

    Метка политики берётся из EXP-002 намеренно: сверка идёт по SHA файла
    расписания, а метка печатается в его шапке. Переименовать её значило бы
    сверять один файл, а предъявлять другой.
    """

    return Exp003Scenario(
        scenario_id="EXP003_R_EXP002_WINNER",
        stage=STAGE_RECONCILIATION,
        candidate_type=CandidateType.ADMISSIBLE,
        transfers=exp002_winner_transfers(),
        label="EXP-002 winner re-run",
        render_policy_id=EXP002_WINNER_POLICY_ID,
    )


def stage_a_scenarios() -> tuple[Exp003Scenario, ...]:
    """Уточнение по каждому из четырёх выигравших направлений."""

    scenarios: list[Exp003Scenario] = []
    for (block_id, donor, receiver), fractions in STAGE_A_GRID.items():
        for fraction in fractions:
            spec = transfer(block_id, donor, receiver, fraction)
            scenarios.append(
                Exp003Scenario(
                    scenario_id=f"EXP003_A_{spec.component_id}",
                    stage=STAGE_LOCAL_REFINEMENT,
                    candidate_type=CandidateType.ADMISSIBLE,
                    transfers=(spec,),
                )
            )
    return tuple(scenarios)


def refined_quad_scenario(
    best_by_direction: Mapping[tuple[int, str, str], float],
) -> Exp003Scenario:
    """Один комбинированный прогон на лучших уточнённых долях."""

    transfers = tuple(
        transfer(block_id, donor, receiver, fraction)
        for (block_id, donor, receiver), fraction in sorted(best_by_direction.items())
    )
    return Exp003Scenario(
        scenario_id=quad_id("EXP003_B_REFINED_QUAD", transfers),
        stage=STAGE_REFINED_QUAD,
        candidate_type=CandidateType.ADMISSIBLE,
        transfers=transfers,
        label="EXP003_REFINED_QUAD",
    )


def scaled_quad_scenarios(
    base_transfers: Sequence[TransferSpec], alphas: Sequence[float] = SCALING_ALPHAS
) -> tuple[Exp003Scenario, ...]:
    """Одновременное ослабление всех четырёх переносов коэффициентом alpha."""

    scenarios: list[Exp003Scenario] = []
    for alpha in alphas:
        scaled = tuple(
            replace(item, fraction=quantize_fraction(alpha * item.fraction))
            for item in base_transfers
        )
        if any(not 0.0 < item.fraction < 1.0 for item in scaled):
            raise ValueError(f"alpha {alpha} produces a fraction outside (0, 1)")
        scenarios.append(
            Exp003Scenario(
                scenario_id=f"EXP003_C_SCALE_{fraction_tag(quantize_fraction(alpha))}",
                stage=STAGE_SCALED_QUAD,
                candidate_type=CandidateType.ADMISSIBLE,
                transfers=scaled,
                label=f"alpha={alpha:g}",
            )
        )
    return tuple(scenarios)


def compensation_scenarios(
    base_transfers: Sequence[TransferSpec],
    *,
    well: str = PRIMARY_SUPPLEMENT_WELL,
    rates: Sequence[float] = SUPPLEMENT_RATES,
    stage: str = STAGE_COMPENSATION,
) -> tuple[Exp003Scenario, ...]:
    """Добавка закачки на одну скважину поверх лучшего квада."""

    return tuple(
        Exp003Scenario(
            scenario_id=f"EXP003_{stage}_COMP_{SupplementalInjection(well, rate).component_id}",
            stage=stage,
            candidate_type=CandidateType.ADMISSIBLE,
            transfers=tuple(base_transfers),
            supplements=(SupplementalInjection(well, rate),),
            label=f"+{rate:g} m3/day at {well}",
        )
        for rate in rates
    )


def split_compensation_scenarios(
    base_transfers: Sequence[TransferSpec],
    *,
    allocations: Sequence[tuple[tuple[str, float], ...]],
    stage: str = STAGE_SECONDARY_COMPENSATION,
) -> tuple[Exp003Scenario, ...]:
    """Добавка, разделённая между несколькими скважинами с запасом."""

    scenarios: list[Exp003Scenario] = []
    for allocation in allocations:
        supplements = tuple(SupplementalInjection(well, rate) for well, rate in allocation)
        tag = "__".join(sorted(item.component_id for item in supplements))
        scenarios.append(
            Exp003Scenario(
                scenario_id=f"EXP003_{stage}_SPLIT_{tag}",
                stage=stage,
                candidate_type=CandidateType.ADMISSIBLE,
                transfers=tuple(base_transfers),
                supplements=supplements,
                label="; ".join(f"+{rate:g} at {well}" for well, rate in allocation),
            )
        )
    return tuple(scenarios)


class Exp003PolicyError(ValueError):
    """Воздействие нельзя наложить на это состояние."""


@dataclass(frozen=True, slots=True)
class Exp003Policy:
    """Переносы EXP-002 плюс добавки закачки."""

    transfers: tuple[TransferSpec, ...]
    supplements: tuple[SupplementalInjection, ...]
    policy_id: str

    def plan(self, state: FieldState, baseline: Schedule, case: Case) -> Schedule:
        if not self.transfers and not self.supplements:
            # Контрольный прогон: воздействия нет, расписание опорное.
            return baseline.canonical()
        if self.transfers:
            _validate_transfers(self.transfers, state)
        supplement_by_well = {item.well: item.added_rate_m3_per_day for item in self.supplements}
        if len(supplement_by_well) != len(self.supplements):
            raise Exp003PolicyError("duplicate supplemental injection well")
        for well in supplement_by_well:
            well_state = state.by_well.get(well)
            if well_state is None:
                raise Exp003PolicyError(f"unknown supplemental well {well}")
            if well_state.role.name != "INJECTOR":
                raise Exp003PolicyError(f"supplemental well {well} is not an injector")

        affected = {well for item in self.transfers for well in item.wells}
        affected |= set(supplement_by_well)
        templates: dict[str, InjectorControl] = {}
        targets: dict[str, float] = {}
        for well in affected:
            template = state.by_well[well].as_injector()
            templates[well] = template
            targets[well] = float(template.surface_rate or 0.0)

        steps: list[ScheduleStep] = []
        for index, step in enumerate(baseline.canonical().steps):
            touched = False
            for control in step.injectors:
                if control.well in affected:
                    templates[control.well] = control
                    targets[control.well] = float(control.surface_rate or 0.0)
                    touched = True
            in_window = case.management_start_date <= step.date <= case.management_end_date
            if not in_window or not (touched or index == 0):
                steps.append(step)
                continue

            candidate = dict(targets)
            for item in self.transfers:
                donor_target = targets[item.donor]
                delta = item.fraction * donor_target if donor_target > 0.0 else 0.0
                if item.mode in (TransferMode.TRANSFER, TransferMode.DONOR_ONLY):
                    candidate[item.donor] -= delta
                if item.mode in (TransferMode.TRANSFER, TransferMode.RECEIVER_ONLY):
                    candidate[item.receiver] += delta
            for well, added in supplement_by_well.items():
                candidate[well] += added

            controls = [c for c in step.injectors if c.well not in affected]
            for well in sorted(affected, key=natural_key):
                controls.append(replace(templates[well], surface_rate=candidate[well]))
            steps.append(ScheduleStep(step.date, step.producers, tuple(controls)))
        return Schedule(tuple(steps)).canonical()


def build_exp003_schedule(
    scenario: Exp003Scenario, case: Case, *, state: FieldState, baseline: Schedule
) -> tuple[Schedule, str]:
    policy = Exp003Policy(
        transfers=scenario.transfers,
        supplements=scenario.supplements,
        policy_id=scenario.render_policy_id or f"EXP003::{scenario.scenario_id}",
    )
    schedule = policy.plan(state, baseline, case)
    return schedule, render_schedule(schedule, case, policy_id=policy.policy_id)


def _conservation_violations(
    scenario: Exp003Scenario, state: FieldState, baseline: Schedule, candidate: Schedule
) -> list[str]:
    """Сумма пары равна опорной плюс объявленная добавка.

    Добавка входит в проверку явно, поэтому компенсация не размывает
    контроль: любой объём сверх заявленного остаётся нарушением.
    """

    wells = tuple({well for item in scenario.transfers for well in item.wells})
    if not wells:
        return []
    base = _effective_targets(state, baseline, wells)
    cand = _effective_targets(state, candidate, wells)
    supplements = scenario.supplement_by_well
    problems: list[str] = []
    for day, base_targets in base.items():
        cand_targets = cand[day]
        for item in scenario.transfers:
            base_donor = base_targets[item.donor]
            base_receiver = base_targets[item.receiver]
            delta = item.fraction * base_donor if base_donor > 0.0 else 0.0
            expected_donor = base_donor - delta + supplements.get(item.donor, 0.0)
            expected_receiver = base_receiver + delta + supplements.get(item.receiver, 0.0)
            if not math.isclose(cand_targets[item.donor], expected_donor, abs_tol=1e-9):
                problems.append(f"{day} {item.component_id}: donor target mismatch")
            if not math.isclose(cand_targets[item.receiver], expected_receiver, abs_tol=1e-9):
                problems.append(f"{day} {item.component_id}: receiver target mismatch")
            expected_total = (
                base_donor
                + base_receiver
                + supplements.get(item.donor, 0.0)
                + supplements.get(item.receiver, 0.0)
            )
            actual_total = cand_targets[item.donor] + cand_targets[item.receiver]
            if not math.isclose(actual_total, expected_total, abs_tol=1e-9):
                problems.append(f"{day} {item.component_id}: pair total mismatch")
    return problems


def _supplement_violations(
    scenario: Exp003Scenario, state: FieldState, baseline: Schedule, candidate: Schedule
) -> list[str]:
    """Добавка равна заявленной ровно и только на окне управления."""

    if not scenario.supplements:
        return []
    transfer_wells = {well for item in scenario.transfers for well in item.wells}
    wells = tuple(scenario.supplement_by_well)
    base = _effective_targets(state, baseline, wells)
    cand = _effective_targets(state, candidate, wells)
    problems: list[str] = []
    for well, added in scenario.supplement_by_well.items():
        if well in transfer_wells:
            # Такая скважина уже покрыта проверкой сохранения пары.
            continue
        for day, base_targets in base.items():
            expected = base_targets[well] + added
            if not math.isclose(cand[day][well], expected, abs_tol=1e-9):
                problems.append(f"{day} {well}: supplemental target mismatch")
    return problems


def validate_exp003_candidate(
    scenario: Exp003Scenario,
    case: Case,
    *,
    state: FieldState,
    baseline: Schedule,
    candidate: Schedule,
) -> dict[str, Any]:
    base_report = validate_schedule(candidate, state, case)
    gates: list[GateResult] = [
        _gate(
            "producer_controls_unchanged",
            _producer_violations(baseline, candidate),
            "producer controls identical",
        ),
        _gate(
            "other_injectors_unchanged",
            _other_injector_violations(scenario.as_exp002, baseline, candidate)
            if not scenario.supplements
            else _other_injector_violations_with_supplements(scenario, baseline, candidate),
            "only transfer and supplemental wells changed",
        ),
        _gate(
            "transfer_controls_valid",
            _target_violations(candidate),
            "all candidate injector targets non-negative and finite",
        ),
        _gate(
            "pre_window_history_immutable",
            _pre_window_violations(case, baseline, candidate),
            "candidate starts at management window",
        ),
        _gate(
            "pair_totals_match_declared_supplement",
            _conservation_violations(scenario, state, baseline, candidate),
            "per-pair totals equal baseline plus declared supplement",
        ),
        _gate(
            "supplemental_targets_correct",
            _supplement_violations(scenario, state, baseline, candidate),
            "supplemental additions match the declaration",
        ),
    ]
    valid = base_report.is_valid and all(gate.status is GateStatus.PASS for gate in gates)
    return {
        "validation_001": base_report.to_json(),
        "experiment": [gate.to_json() for gate in gates],
        "is_valid": valid,
        "failed": [gate.to_json() for gate in gates if gate.status is GateStatus.FAIL],
    }


def _other_injector_violations_with_supplements(
    scenario: Exp003Scenario, baseline: Schedule, candidate: Schedule
) -> list[str]:
    affected = {well for item in scenario.transfers for well in item.wells}
    affected |= set(scenario.supplement_by_well)
    problems: list[str] = []
    base_steps = {step.date: step for step in baseline.canonical().steps}
    for step in candidate.canonical().steps:
        base = base_steps.get(step.date)
        if base is None:
            continue
        base_others = tuple(c for c in base.injectors if c.well not in affected)
        cand_others = tuple(c for c in step.injectors if c.well not in affected)
        if base_others != cand_others:
            problems.append(f"{step.date.isoformat()}: injector outside the change set differs")
    return problems


def observed_wells(scenario: Exp003Scenario) -> tuple[str, ...]:
    wells: list[str] = []
    for item in scenario.transfers:
        block = BLOCKS_BY_ID[item.block_id]
        wells.extend(block.producers)
        wells.extend(block.injectors)
        wells.extend(item.wells)
    wells.extend(scenario.supplement_by_well)
    return tuple(dict.fromkeys(sorted(wells, key=natural_key)))


# --- водный учёт -----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class WaterAccounting:
    """Поверхностный водный учёт по модели.

    Это НЕ VRR и не замещение отбора в пластовых условиях: величины сняты
    на поверхностных условиях модели, а официальная трактовка водного
    баланса пока не определена.
    """

    window_start: str
    produced_water_m3: float
    injected_water_m3: float
    monthly_produced_water_m3: tuple[float, ...]
    monthly_injected_water_m3: tuple[float, ...]
    monthly_dates: tuple[str, ...]

    @property
    def injection_minus_production_m3(self) -> float:
        return self.injected_water_m3 - self.produced_water_m3

    def to_json(self) -> dict[str, Any]:
        return {
            "basis": "surface/model water accounting; NOT voidage replacement",
            "window_start": self.window_start,
            "produced_water_m3": self.produced_water_m3,
            "injected_water_m3": self.injected_water_m3,
            "injection_minus_production_m3": self.injection_minus_production_m3,
            "monthly_dates": list(self.monthly_dates),
            "monthly_produced_water_m3": list(self.monthly_produced_water_m3),
            "monthly_injected_water_m3": list(self.monthly_injected_water_m3),
        }


def water_accounting(summary: SummaryData, window_start: str) -> WaterAccounting:
    """Снять водный учёт окна прямо из накопленных векторов сводки."""

    dates = tuple(moment.date().isoformat() for moment in summary.dates)
    start = next((idx for idx, day in enumerate(dates) if day >= window_start), 0)
    produced = summary.field_series("FWPT")
    injected = summary.field_series("FWIT")
    last = summary.report_steps - 1
    monthly_produced = tuple(b - a for a, b in pairwise(produced[start:]))
    monthly_injected = tuple(b - a for a, b in pairwise(injected[start:]))
    return WaterAccounting(
        window_start=dates[start],
        produced_water_m3=produced[last] - produced[start],
        injected_water_m3=injected[last] - injected[start],
        monthly_produced_water_m3=monthly_produced,
        monthly_injected_water_m3=monthly_injected,
        monthly_dates=dates[start + 1 :],
    )


def water_comparison(candidate: WaterAccounting, baseline: WaterAccounting) -> dict[str, Any]:
    return {
        "basis": "surface/model water accounting; NOT voidage replacement",
        "candidate_produced_water_m3": candidate.produced_water_m3,
        "candidate_injected_water_m3": candidate.injected_water_m3,
        "candidate_injection_minus_production_m3": candidate.injection_minus_production_m3,
        "baseline_produced_water_m3": baseline.produced_water_m3,
        "baseline_injected_water_m3": baseline.injected_water_m3,
        "baseline_injection_minus_production_m3": baseline.injection_minus_production_m3,
        "delta_produced_water_m3": candidate.produced_water_m3 - baseline.produced_water_m3,
        "delta_injected_water_m3": candidate.injected_water_m3 - baseline.injected_water_m3,
        "delta_injection_minus_production_m3": (
            candidate.injection_minus_production_m3 - baseline.injection_minus_production_m3
        ),
    }


def injection_deviation_pct(candidate_m3: float, baseline_m3: float) -> float | None:
    if not baseline_m3:
        return None
    return abs(candidate_m3 - baseline_m3) / abs(baseline_m3) * 100.0


# --- давление --------------------------------------------------------------


def pressure_profile(summary: SummaryData, window_start: str) -> dict[str, float]:
    dates = tuple(moment.date().isoformat() for moment in summary.dates)
    start = next((idx for idx, day in enumerate(dates) if day >= window_start), 0)
    series = summary.field_series("FPR")[start:]
    return {
        "start_bar": series[0],
        "mean_bar": sum(series) / len(series),
        "final_bar": series[-1],
        "min_bar": min(series),
    }


def pressure_comparison(
    candidate: Mapping[str, float], baseline: Mapping[str, float]
) -> dict[str, float]:
    return {
        "delta_mean_bar": candidate["mean_bar"] - baseline["mean_bar"],
        "delta_final_bar": candidate["final_bar"] - baseline["final_bar"],
        "delta_min_bar": candidate["min_bar"] - baseline["min_bar"],
    }


# --- экономическая декомпозиция -------------------------------------------

DECOMPOSITION_FIELDS: tuple[tuple[str, str], ...] = (
    ("revenue_m", "mainRevenueM"),
    ("deductions_m", "deductionsM"),
    ("opex_m", "opexM"),
    ("oil_opex_m", "oilOpexM"),
    ("liquid_opex_m", "liquidOpexM"),
    ("injection_opex_m", "injectionOpexM"),
    ("fund_opex_m", "fundOpexM"),
    ("pump_capex_m", "pumpCapexM"),
    ("start_stop_cost_m", "startStopCostM"),
    ("conversion_opex_m", "conversionOpexM"),
    ("gtm_m", "gtmM"),
    ("property_tax_m", "propertyTaxM"),
    ("profit_tax_m", "profitTaxM"),
    ("depreciation_m", "depreciationM"),
    ("ebitda_m", "ebitdaM"),
    ("fcf_m", "fcfM"),
    ("chdd_m", "chddM"),
)


def economic_decomposition(chdd: ChddResult | None) -> dict[str, float]:
    """Слагаемые ЧДД, просуммированные по годам расчёта."""

    if chdd is None:
        return {}
    annual = chdd.raw.get("annual", []) if isinstance(chdd.raw, Mapping) else []
    totals: dict[str, float] = {}
    for label, key in DECOMPOSITION_FIELDS:
        total = 0.0
        present = False
        for year in annual:
            if isinstance(year, Mapping) and key in year:
                value = year[key]
                if isinstance(value, int | float) and not isinstance(value, bool):
                    total += float(value)
                    present = True
        if present:
            totals[label] = total
    totals["npv_m"] = chdd.npv_mln_rub
    return totals


def decomposition_delta(
    candidate: Mapping[str, float], baseline: Mapping[str, float]
) -> dict[str, float]:
    keys = sorted(set(candidate) | set(baseline))
    return {key: candidate.get(key, 0.0) - baseline.get(key, 0.0) for key in keys}


# --- насыщение по забойному давлению ---------------------------------------


def injector_saturation_table(
    summary: SummaryData, case: Case, wells: Sequence[str], window_start: str
) -> dict[str, dict[str, Any]]:
    limit = case.constraints.injector_bhp_max
    table: dict[str, dict[str, Any]] = {}
    for well in wells:
        saturation = saturation_for_well(summary, well, limit, window_start)
        headroom = (
            None
            if saturation.max_wbhp_bar is None or limit is None
            else limit - saturation.max_wbhp_bar
        )
        table[well] = {**saturation.to_json(), "headroom_bar": headroom}
    return table


def headroom_ranking(
    table: Mapping[str, Mapping[str, Any]], *, exclude: Sequence[str] = ()
) -> list[tuple[str, float]]:
    """Скважины с наибольшим запасом до предела, по данным прогона."""

    ranked: list[tuple[str, float]] = []
    for well, entry in table.items():
        if well in exclude:
            continue
        if int(entry.get("hit_count") or 0) >= BHP_SATURATION_MIN_HITS:
            continue
        headroom = entry.get("headroom_bar")
        if headroom is None:
            continue
        ranked.append((well, float(headroom)))
    ranked.sort(key=lambda item: (-item[1], natural_key(item[0])))
    return ranked


# --- классификация уточнения ----------------------------------------------


def classify_refinement(points: Sequence[tuple[float, float, int]]) -> RefinementClass:
    """`points` = (доля, ΔЧДД, число состояний получателя на пределе BHP).

    Считается насыщение **получателя**, а не любой скважины поля. Донор в
    этих направлениях уходит вниз по расходу, а часть доноров упирается в
    300 бар уже на опорном режиме -- их насыщение к переносу отношения не
    имеет и, если его учитывать, все направления получают один и тот же
    ярлык и классификация перестаёт что-либо различать.

    Порядок проверок тоже содержательный. Если максимум найден внутри
    сетки, главное утверждение -- что он найден; насыщение при этом
    остаётся контекстом и печатается отдельной колонкой. Ярлык
    «упёрлись в ограничение» остаётся для случая, когда лучшая точка
    оказалась последней и получатель на ней уже на пределе: тогда
    неизвестно, кончился ли рост или кончился запас.
    """

    usable = [(f, d, h) for f, d, h in points if d is not None]
    if len(usable) < 2:
        return RefinementClass.INSUFFICIENT_DATA
    usable.sort(key=lambda item: item[0])
    deltas = [d for _f, d, _h in usable]
    hits = [h for _f, _d, h in usable]
    best = max(range(len(deltas)), key=deltas.__getitem__)
    if 0 < best < len(deltas) - 1:
        return RefinementClass.PEAK_REGION_BRACKETED
    if best == len(deltas) - 1:
        if hits[best] >= BHP_SATURATION_MIN_HITS:
            return RefinementClass.CONSTRAINT_SATURATED
        return RefinementClass.BOUNDARY_BEST
    return RefinementClass.LOCAL_BEST_TESTED


# --- фронт Парето ----------------------------------------------------------

PARETO_OBJECTIVES: tuple[tuple[str, str], ...] = (
    ("npv_mln_rub", "max"),
    ("abs_delta_injection_m3", "min"),
    ("abs_mean_pressure_delta_bar", "min"),
)


def pareto_key(row: Mapping[str, Any]) -> tuple[float, float, float] | None:
    npv = _float_or_none(row.get("npv_mln_rub"))
    injection = _float_or_none(row.get("delta_injection_m3"))
    pressure = _float_or_none(row.get("field_mean_pressure_delta_bar"))
    if npv is None or injection is None or pressure is None:
        return None
    return npv, abs(injection), abs(pressure)


def dominates(a: tuple[float, float, float], b: tuple[float, float, float]) -> bool:
    """`a` доминирует `b`: не хуже по всем осям и строго лучше хотя бы по одной."""

    not_worse = a[0] >= b[0] and a[1] <= b[1] and a[2] <= b[2]
    strictly_better = a[0] > b[0] or a[1] < b[1] or a[2] < b[2]
    return not_worse and strictly_better


def pareto_frontier(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    scored = [(row, pareto_key(row)) for row in rows]
    usable = [(row, key) for row, key in scored if key is not None]
    frontier = [
        row
        for row, key in usable
        if not any(dominates(other, key) for _row, other in usable if other != key)
    ]
    return sorted(frontier, key=lambda row: (-float(row["npv_mln_rub"]), row["scenario_id"]))


def select_key_candidates(
    rows: Sequence[Mapping[str, Any]], *, baseline_injection_m3: float
) -> dict[str, Mapping[str, Any] | None]:
    usable = [row for row in rows if pareto_key(row) is not None]
    if not usable:
        return {member.value: None for member in KeyCandidate}

    max_npv = max(usable, key=lambda row: (float(row["npv_mln_rub"]), row["scenario_id"]))
    near = [
        row
        for row in usable
        if (deviation := injection_deviation_pct(float(row["injection_m3"]), baseline_injection_m3))
        is not None
        and deviation <= NEAR_BASELINE_INJECTION_PCT
    ]
    near_best = (
        max(near, key=lambda row: (float(row["npv_mln_rub"]), row["scenario_id"])) if near else None
    )
    low_pressure = min(
        usable,
        key=lambda row: (abs(float(row["field_mean_pressure_delta_bar"])), row["scenario_id"]),
    )
    return {
        KeyCandidate.MAX_NPV.value: max_npv,
        KeyCandidate.NEAR_BASELINE_INJECTION.value: near_best,
        KeyCandidate.LOW_PRESSURE_DEVIATION.value: low_pressure,
    }


def nearest_by_injection(
    rows: Sequence[Mapping[str, Any]], *, baseline_injection_m3: float
) -> Mapping[str, Any] | None:
    usable = [row for row in rows if _float_or_none(row.get("injection_m3")) is not None]
    if not usable:
        return None
    return min(
        usable,
        key=lambda row: (
            abs(float(row["injection_m3"]) - baseline_injection_m3),
            row["scenario_id"],
        ),
    )


# --- снимок для КТ1 --------------------------------------------------------


def kt1_snapshot(
    *,
    baseline_npv: float,
    exp001_best_npv: float,
    exp002_best_npv: float,
    max_npv_row: Mapping[str, Any] | None,
    near_baseline_row: Mapping[str, Any] | None,
    simulation_count_total: int,
    all_full_opm: bool,
    all_chdd_verified: bool,
) -> dict[str, Any]:
    def entry(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        npv = float(row["npv_mln_rub"])
        delta = npv - baseline_npv
        return {
            "scenario_id": row["scenario_id"],
            "schedule_sha256": row.get("schedule_sha256"),
            "npv": npv,
            "delta": delta,
            "delta_pct": delta / baseline_npv * 100.0,
            "injection_delta": _float_or_none(row.get("delta_injection_m3")),
        }

    return {
        "baseline": {"npv": baseline_npv},
        "exp001_best": {"npv": exp001_best_npv, "delta": exp001_best_npv - baseline_npv},
        "exp002_best": {"npv": exp002_best_npv, "delta": exp002_best_npv - baseline_npv},
        "exp003_max_npv": entry(max_npv_row),
        "near_baseline_injection": entry(near_baseline_row),
        "simulation_count_total": simulation_count_total,
        "all_full_opm": all_full_opm,
        "all_chdd_verified": all_chdd_verified,
        "water_balance_status": WaterBalanceStatus.ORGANIZER_RULE_UNRESOLVED.value,
        "provenance": "NOT_OFFICIAL_PENDING_TNAV_REFERENCE",
    }


RESULT_COLUMNS: tuple[str, ...] = (
    "scenario_id",
    "stage",
    "label",
    "candidate_type",
    "transfers",
    "supplements",
    "simulation_status",
    "constraints_status",
    "chdd_status",
    "schedule_sha256",
    "npv_mln_rub",
    "delta_npv_mln_rub",
    "delta_npv_pct",
    "oil_t",
    "delta_oil_t",
    "liquid_t",
    "delta_liquid_t",
    "water_t",
    "delta_water_t",
    "injection_m3",
    "delta_injection_m3",
    "injection_deviation_pct",
    "produced_water_m3",
    "delta_produced_water_m3",
    "injected_water_m3",
    "delta_injected_water_m3",
    "injection_minus_production_m3",
    "delta_injection_minus_production_m3",
    "mean_fpr_bar",
    "final_fpr_bar",
    "min_fpr_bar",
    "field_mean_pressure_delta_bar",
    "field_final_pressure_delta_bar",
    "field_min_pressure_delta_bar",
    "wall_seconds",
    "notes",
)

STAGE_A_EXTRA: tuple[str, ...] = (
    "block",
    "donor",
    "receiver",
    "fraction",
    "donor_max_wbhp_bar",
    "donor_bhp_hit_count",
    "receiver_max_wbhp_bar",
    "receiver_bhp_hit_count",
    "refinement_class",
)
STAGE_A_COLUMNS: tuple[str, ...] = (*RESULT_COLUMNS, *STAGE_A_EXTRA)
SCALED_EXTRA: tuple[str, ...] = ("alpha",)
SCALED_COLUMNS: tuple[str, ...] = (*RESULT_COLUMNS, *SCALED_EXTRA)
COMPENSATION_EXTRA: tuple[str, ...] = (
    "supplement_wells",
    "supplement_total_m3_per_day",
    "scheduled_added_injection_m3",
    # Реализованная добавка считается относительно того же квада без
    # компенсации, а не относительно baseline: иначе в неё попадает весь
    # эффект переносов и «реализация добавки» перестаёт быть про добавку.
    "actual_added_injection_m3",
    "supplement_realization_ratio",
    "field_delta_injection_vs_baseline_m3",
    "well42_max_wbhp_bar",
    "well42_bhp_hit_count",
    "well42_headroom_bar",
    "saturation_status",
)
COMPENSATION_COLUMNS: tuple[str, ...] = (*RESULT_COLUMNS, *COMPENSATION_EXTRA)
PARETO_COLUMNS: tuple[str, ...] = (
    "scenario_id",
    "source",
    "npv_mln_rub",
    "delta_npv_mln_rub",
    "delta_npv_pct",
    "delta_injection_m3",
    "injection_deviation_pct",
    "field_mean_pressure_delta_bar",
    "field_final_pressure_delta_bar",
    "delta_oil_t",
    "delta_water_t",
    "schedule_sha256",
    "key_labels",
)


def _field_delta(result: Any, vector: str, stat: str) -> float | None:
    try:
        return float((result.metric_delta or {})["field"][vector]["window"][stat])
    except (KeyError, TypeError, ValueError):
        return None


def exp003_row(result: Any, baseline: Any) -> dict[str, Any]:
    """Строка таблицы результатов для одного сценария."""

    oil, liquid, water, injection = totals_from_chdd(result.chdd_result)
    base_oil, base_liquid, base_water, base_injection = totals_from_chdd(baseline.chdd_result)
    npv = result.npv_mln_rub
    base_npv = baseline.npv_mln_rub
    delta_npv = None if npv is None or base_npv is None else npv - base_npv
    scenario = result.scenario
    pressure = result.pressure or {}
    base_pressure = baseline.pressure or {}
    water_acc = result.water
    base_water_acc = baseline.water
    return {
        "scenario_id": scenario.scenario_id,
        "stage": scenario.stage,
        "label": scenario.label,
        "candidate_type": scenario.candidate_type.value,
        "transfers": json.dumps([t.to_json() for t in scenario.transfers], sort_keys=True),
        "supplements": json.dumps([s.to_json() for s in scenario.supplements], sort_keys=True),
        "simulation_status": result.simulation_status,
        "constraints_status": result.constraints_status,
        "chdd_status": result.chdd_status,
        "schedule_sha256": result.schedule_sha256,
        "npv_mln_rub": npv,
        "delta_npv_mln_rub": delta_npv,
        "delta_npv_pct": None
        if delta_npv is None or not base_npv
        else delta_npv / base_npv * 100.0,
        "oil_t": oil,
        "delta_oil_t": None if oil is None or base_oil is None else oil - base_oil,
        "liquid_t": liquid,
        "delta_liquid_t": None if liquid is None or base_liquid is None else liquid - base_liquid,
        "water_t": water,
        "delta_water_t": None if water is None or base_water is None else water - base_water,
        "injection_m3": injection,
        "delta_injection_m3": None
        if injection is None or base_injection is None
        else injection - base_injection,
        "injection_deviation_pct": None
        if injection is None or base_injection is None
        else injection_deviation_pct(injection, base_injection),
        "produced_water_m3": None if water_acc is None else water_acc.produced_water_m3,
        "delta_produced_water_m3": None
        if water_acc is None or base_water_acc is None
        else water_acc.produced_water_m3 - base_water_acc.produced_water_m3,
        "injected_water_m3": None if water_acc is None else water_acc.injected_water_m3,
        "delta_injected_water_m3": None
        if water_acc is None or base_water_acc is None
        else water_acc.injected_water_m3 - base_water_acc.injected_water_m3,
        "injection_minus_production_m3": None
        if water_acc is None
        else water_acc.injection_minus_production_m3,
        "delta_injection_minus_production_m3": None
        if water_acc is None or base_water_acc is None
        else water_acc.injection_minus_production_m3 - base_water_acc.injection_minus_production_m3,
        "mean_fpr_bar": pressure.get("mean_bar"),
        "final_fpr_bar": pressure.get("final_bar"),
        "min_fpr_bar": pressure.get("min_bar"),
        "field_mean_pressure_delta_bar": _field_delta(result, "FPR", "mean")
        if _field_delta(result, "FPR", "mean") is not None
        else (
            None
            if not pressure or not base_pressure
            else pressure["mean_bar"] - base_pressure["mean_bar"]
        ),
        "field_final_pressure_delta_bar": None
        if not pressure or not base_pressure
        else pressure["final_bar"] - base_pressure["final_bar"],
        "field_min_pressure_delta_bar": None
        if not pressure or not base_pressure
        else pressure["min_bar"] - base_pressure["min_bar"],
        "wall_seconds": round(result.wall_seconds, 3),
        "notes": "; ".join(result.notes)[:500],
    }


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    import csv

    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(columns), extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})
    temp.replace(path)


def load_csv_rows(path: Path) -> list[dict[str, Any]]:
    import csv

    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def coerce_numeric(
    rows: Sequence[Mapping[str, Any]], columns: Sequence[str]
) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        for column in columns:
            item[column] = _float_or_none(row.get(column))
        converted.append(item)
    return converted


def totals_row(chdd: ChddResult | None) -> tuple[float | None, ...]:
    return totals_from_chdd(chdd)


def scenario_window(case: Case) -> tuple[date, date]:
    return case.management_start_date, case.management_end_date


def dumps(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
