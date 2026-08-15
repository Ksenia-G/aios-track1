"""EXP-002 boundary search, decomposition, and transfer combinations."""

from __future__ import annotations

import csv
import json
import math
import shutil
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from itertools import pairwise
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.chdd.runner import ChddError, ChddResult, run_chdd
from aios.chdd.trajectory import TrajectoryError, build_chdd_input, write_chdd_csv
from aios.experiments.blocks import BLOCKS_BY_ID, block_of_injector
from aios.experiments.metrics import RunMetrics, collect_metrics, compare_metrics
from aios.experiments.runner import LOG_TAIL_FILENAME, _write_log_tail
from aios.experiments.scenarios import fraction_tag
from aios.experiments.sweep import run_baseline
from aios.opm.runner import OpmRunResult, SimulationError, run_simulation
from aios.opm.summary import SummaryData, SummaryReadError
from aios.opm.workspace import WorkspaceError, prepare_workspace
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.events import InjectorControl, Schedule, ScheduleStep, natural_key
from aios.schedule.generator import render_schedule, schedule_sha256
from aios.schedule.state import FieldState
from aios.schedule.validator import GateResult, GateStatus, validate_schedule

BASELINE_NPV_MLN_RUB = 1030.8776958837277
BASELINE_SCHEDULE_SHA256 = "7662d10495796ace6b0d20df404ca68932b2894f367de2062c232cbe3e01c399"
EXP001_WINNER_ID = "EXP001_B3_32_TO_4_F30"
EXP001_WINNER_NPV_MLN_RUB = 1051.6400268038235
EXP001_WINNER_SCHEDULE_SHA256 = "ed7e76a5a097c2f66eeef17d38d65b3053bb3c0132e9db4aff528972179cd6c6"
BASELINE_TOLERANCE = 1e-6

DEFAULT_OUTPUT = Path("experiments/exp002")
DEFAULT_RUNTIME = Path("experiments/exp002/runtime")
DEFAULT_EXP001_RESULTS = Path("experiments/exp001/results.csv")
MAX_PARALLELISM = 4

STAGE_A_FRACTIONS: tuple[float, ...] = (0.40, 0.50, 0.60, 0.70, 0.80, 0.90)
DECOMPOSITION_FRACTIONS: tuple[float, ...] = (0.30, 0.50, 0.70)
STAGE_A_DIRECTIONS: tuple[tuple[int, str, str], ...] = (
    (3, "32", "4"),
    (3, "32", "21"),
    (3, "13", "21"),
    (3, "13", "4"),
    (5, "26", "2"),
    (2, "41", "19"),
    (4, "7", "42"),
)

NEAR_ADDITIVE_TOLERANCE_MLN_RUB = 0.25
BHP_HIT_TOLERANCE_BAR = 0.1
STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"

RESULT_COLUMNS = (
    "scenario_id",
    "stage",
    "candidate_type",
    "transfers",
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
    "field_final_pressure_delta_bar",
    "field_mean_pressure_delta_bar",
    "behavior_class",
    "interaction_class",
    "sum_individual_delta_npv",
    "interaction_delta_npv",
    "wall_seconds",
    "notes",
)

STAGE_A_EXTRA_COLUMNS = (
    "donor",
    "receiver",
    "fraction",
    "donor_target_baseline_first",
    "receiver_target_baseline_first",
    "donor_target_candidate_first",
    "receiver_target_candidate_first",
    "scheduled_transfer_volume",
    "donor_actual_injection_m3",
    "receiver_actual_injection_m3",
    "delta_actual_donor_injection_m3",
    "delta_actual_receiver_injection_m3",
    "pair_total_actual_injection_delta_m3",
    "receiver_realized_added_injection",
    "realization_ratio",
    "donor_max_wbhp_bar",
    "receiver_max_wbhp_bar",
    "donor_bhp_limit_bar",
    "receiver_bhp_limit_bar",
    "donor_bhp_hit_count",
    "receiver_bhp_hit_count",
    "donor_first_bhp_hit_date",
    "receiver_first_bhp_hit_date",
    "donor_last_bhp_hit_date",
    "receiver_last_bhp_hit_date",
)
STAGE_A_COLUMNS = (*RESULT_COLUMNS, *STAGE_A_EXTRA_COLUMNS)


class CandidateType(StrEnum):
    ADMISSIBLE = "ADMISSIBLE"
    DIAGNOSTIC_ONLY = "DIAGNOSTIC_ONLY"


class TransferMode(StrEnum):
    TRANSFER = "TRANSFER"
    DONOR_ONLY = "DONOR_ONLY"
    RECEIVER_ONLY = "RECEIVER_ONLY"


class BehaviorClass(StrEnum):
    MONOTONE_INCREASING = "MONOTONE_INCREASING"
    BRACKETED_PEAK = "BRACKETED_PEAK"
    PLATEAU = "PLATEAU"
    DECREASING = "DECREASING"
    CONSTRAINT_SATURATED = "CONSTRAINT_SATURATED"
    BOUNDARY_BEST = "BOUNDARY_BEST"
    INSUFFICIENT_DATA = "INSUFFICIENT_DATA"


class InteractionClass(StrEnum):
    POSITIVE_INTERACTION = "POSITIVE_INTERACTION"
    NEAR_ADDITIVE = "NEAR_ADDITIVE"
    NEGATIVE_INTERACTION = "NEGATIVE_INTERACTION"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass(frozen=True, slots=True)
class TransferSpec:
    block_id: int
    donor: str
    receiver: str
    fraction: float
    mode: TransferMode = TransferMode.TRANSFER

    @property
    def component_id(self) -> str:
        return f"B{self.block_id}_{self.donor}_TO_{self.receiver}_{fraction_tag(self.fraction)}"

    @property
    def wells(self) -> tuple[str, str]:
        return self.donor, self.receiver

    def to_json(self) -> dict[str, Any]:
        return {
            "block": self.block_id,
            "donor": self.donor,
            "receiver": self.receiver,
            "fraction": self.fraction,
            "mode": self.mode.value,
        }


@dataclass(frozen=True, slots=True)
class Exp002Scenario:
    scenario_id: str
    stage: str
    candidate_type: CandidateType
    transfers: tuple[TransferSpec, ...]

    def to_json(self) -> dict[str, Any]:
        return {
            "scenario_id": self.scenario_id,
            "stage": self.stage,
            "candidate_type": self.candidate_type.value,
            "transfers": [transfer.to_json() for transfer in self.transfers],
        }


@dataclass(frozen=True, slots=True)
class WellSaturation:
    well: str
    max_wbhp_bar: float | None
    bhp_limit_bar: float | None
    hit_count: int
    first_hit_date: str | None
    last_hit_date: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "well": self.well,
            "max_wbhp_bar": self.max_wbhp_bar,
            "bhp_limit_bar": self.bhp_limit_bar,
            "hit_count": self.hit_count,
            "first_hit_date": self.first_hit_date,
            "last_hit_date": self.last_hit_date,
        }


@dataclass(frozen=True, slots=True)
class TransferDiagnostics:
    transfer: TransferSpec
    donor_target_baseline_first: float | None
    receiver_target_baseline_first: float | None
    donor_target_candidate_first: float | None
    receiver_target_candidate_first: float | None
    scheduled_transfer_volume: float | None
    donor_actual_injection_m3: float | None
    receiver_actual_injection_m3: float | None
    delta_actual_donor_injection_m3: float | None
    delta_actual_receiver_injection_m3: float | None
    pair_total_actual_injection_delta_m3: float | None
    receiver_realized_added_injection: float | None
    realization_ratio: float | None
    donor_saturation: WellSaturation
    receiver_saturation: WellSaturation

    def to_json(self) -> dict[str, Any]:
        return {
            "transfer": self.transfer.to_json(),
            "donor_target_baseline_first": self.donor_target_baseline_first,
            "receiver_target_baseline_first": self.receiver_target_baseline_first,
            "donor_target_candidate_first": self.donor_target_candidate_first,
            "receiver_target_candidate_first": self.receiver_target_candidate_first,
            "scheduled_transfer_volume": self.scheduled_transfer_volume,
            "donor_actual_injection_m3": self.donor_actual_injection_m3,
            "receiver_actual_injection_m3": self.receiver_actual_injection_m3,
            "delta_actual_donor_injection_m3": self.delta_actual_donor_injection_m3,
            "delta_actual_receiver_injection_m3": self.delta_actual_receiver_injection_m3,
            "pair_total_actual_injection_delta_m3": self.pair_total_actual_injection_delta_m3,
            "receiver_realized_added_injection": self.receiver_realized_added_injection,
            "realization_ratio": self.realization_ratio,
            "donor_saturation": self.donor_saturation.to_json(),
            "receiver_saturation": self.receiver_saturation.to_json(),
        }


@dataclass(slots=True)
class Exp002Result:
    scenario: Exp002Scenario
    simulation_status: str = STATUS_FAIL
    constraints_status: str = STATUS_FAIL
    chdd_status: str = STATUS_FAIL
    schedule_sha256: str | None = None
    opm_result: OpmRunResult | None = None
    chdd_result: ChddResult | None = None
    metrics: RunMetrics | None = None
    metric_delta: dict[str, Any] | None = None
    gate_report: dict[str, Any] | None = None
    diagnostics: list[TransferDiagnostics] = field(default_factory=list)
    wall_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)

    @property
    def is_pass(self) -> bool:
        return (
            self.simulation_status == STATUS_PASS
            and self.constraints_status == STATUS_PASS
            and self.chdd_status == STATUS_PASS
        )

    @property
    def npv_mln_rub(self) -> float | None:
        return None if self.chdd_result is None else self.chdd_result.npv_mln_rub

    def to_json(self) -> dict[str, Any]:
        opm = self.opm_result
        return {
            "scenario": self.scenario.to_json(),
            "status": STATUS_PASS if self.is_pass else STATUS_FAIL,
            "simulation_status": self.simulation_status,
            "constraints_status": self.constraints_status,
            "chdd_status": self.chdd_status,
            "schedule_sha256": self.schedule_sha256,
            "wall_seconds": round(self.wall_seconds, 3),
            "simulation": None if opm is None else opm.to_json(),
            "economics": None if self.chdd_result is None else self.chdd_result.to_json(),
            "gates": self.gate_report,
            "metrics": None if self.metrics is None else self.metrics.to_json(),
            "metric_delta": self.metric_delta,
            "transfer_diagnostics": [diag.to_json() for diag in self.diagnostics],
            "notes": list(self.notes),
        }


def transfer_spec(block_id: int, donor: str, receiver: str, fraction: float) -> TransferSpec:
    return TransferSpec(block_id=block_id, donor=donor, receiver=receiver, fraction=fraction)


def stage_a_scenarios() -> tuple[Exp002Scenario, ...]:
    return tuple(
        Exp002Scenario(
            scenario_id=f"EXP002_A_B{block}_{donor}_TO_{receiver}_{fraction_tag(fraction)}",
            stage="A",
            candidate_type=CandidateType.ADMISSIBLE,
            transfers=(transfer_spec(block, donor, receiver, fraction),),
        )
        for block, donor, receiver in STAGE_A_DIRECTIONS
        for fraction in STAGE_A_FRACTIONS
    )


def decomposition_scenarios() -> tuple[Exp002Scenario, ...]:
    scenarios: list[Exp002Scenario] = []
    for fraction in DECOMPOSITION_FRACTIONS:
        for mode in TransferMode:
            transfer = TransferSpec(3, "32", "4", fraction, mode)
            scenarios.append(
                Exp002Scenario(
                    scenario_id=f"EXP002_B_32_TO_4_{fraction_tag(fraction)}_{mode.value}",
                    stage="B",
                    candidate_type=CandidateType.DIAGNOSTIC_ONLY,
                    transfers=(transfer,),
                )
            )
    return tuple(scenarios)


def assert_non_overlapping(transfers: Sequence[TransferSpec]) -> None:
    wells: list[str] = []
    for transfer in transfers:
        wells.extend(transfer.wells)
    duplicates = sorted({well for well in wells if wells.count(well) > 1}, key=natural_key)
    if duplicates:
        raise ValueError(f"overlapping transfer wells: {', '.join(duplicates)}")


def combined_scenario_id(prefix: str, transfers: Sequence[TransferSpec]) -> str:
    assert_non_overlapping(transfers)
    parts = sorted(transfer.component_id for transfer in transfers)
    return f"EXP002_{prefix}_" + "__".join(parts)


def _component_from_row(row: Mapping[str, str]) -> TransferSpec:
    return TransferSpec(
        int(row["block"]),
        str(row["donor"]),
        str(row["receiver"]),
        float(row["fraction"]),
    )


def load_exp001_rows(path: Path) -> list[dict[str, str]]:
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def best_exp001_by_block(rows: Sequence[Mapping[str, str]]) -> dict[int, Mapping[str, str]]:
    best: dict[int, Mapping[str, str]] = {}
    for row in rows:
        delta = _float_or_none(row.get("delta_npv_mln_rub"))
        if delta is None:
            continue
        block = int(row["block"])
        if block not in best or delta > float(best[block]["delta_npv_mln_rub"]):
            best[block] = row
    return best


def exp001_winner_row(rows: Sequence[Mapping[str, str]]) -> Mapping[str, str]:
    for row in rows:
        if row.get("scenario_id") == EXP001_WINNER_ID:
            return row
    raise ValueError(f"{EXP001_WINNER_ID} absent from EXP-001 results")


def build_combinations(best_by_block: Mapping[int, TransferSpec]) -> tuple[Exp002Scenario, ...]:
    block_sets = (
        (3, 5),
        (3, 4),
        (3, 2),
        (5, 4),
        (5, 2),
        (4, 2),
        (3, 5, 4),
        (3, 5, 2),
        (3, 5, 4, 2),
    )
    scenarios: list[Exp002Scenario] = []
    for blocks in block_sets:
        transfers = tuple(best_by_block[block] for block in blocks if block in best_by_block)
        if len(transfers) != len(blocks):
            continue
        scenarios.append(
            Exp002Scenario(
                scenario_id=combined_scenario_id("D", transfers),
                stage="D",
                candidate_type=CandidateType.ADMISSIBLE,
                transfers=transfers,
            )
        )
    return tuple(dict.fromkeys(scenarios))


def build_same_b3_combinations(
    best_by_pair: Mapping[tuple[str, str], TransferSpec],
) -> tuple[Exp002Scenario, ...]:
    pair_sets = ((("32", "4"), ("13", "21")), (("32", "21"), ("13", "4")))
    scenarios: list[Exp002Scenario] = []
    for pairs in pair_sets:
        if any(pair not in best_by_pair for pair in pairs):
            continue
        transfers = tuple(best_by_pair[pair] for pair in pairs)
        assert_non_overlapping(transfers)
        scenarios.append(
            Exp002Scenario(
                scenario_id=combined_scenario_id("D_B3", transfers),
                stage="D",
                candidate_type=CandidateType.ADMISSIBLE,
                transfers=transfers,
            )
        )
    return tuple(dict.fromkeys(scenarios))


class MultiTransferError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class MultiTransferPolicy:
    transfers: tuple[TransferSpec, ...]
    policy_id: str

    def plan(self, state: FieldState, baseline: Schedule, case: Case) -> Schedule:
        _validate_transfers(self.transfers, state)
        templates, targets = _initial_injector_state(self.transfers, state)
        affected = {well for transfer in self.transfers for well in transfer.wells}
        canonical = baseline.canonical()
        steps: list[ScheduleStep] = []
        for index, step in enumerate(canonical.steps):
            touched = False
            for control in step.injectors:
                if control.well in affected:
                    templates[control.well] = control
                    targets[control.well] = float(control.surface_rate or 0.0)
                    touched = True
            if not (case.management_start_date <= step.date <= case.management_end_date):
                steps.append(step)
                continue
            if not (touched or index == 0):
                steps.append(step)
                continue

            candidate_targets = dict(targets)
            for transfer in self.transfers:
                donor_target = targets[transfer.donor]
                delta = transfer.fraction * donor_target if donor_target > 0.0 else 0.0
                if transfer.mode in (TransferMode.TRANSFER, TransferMode.DONOR_ONLY):
                    candidate_targets[transfer.donor] -= delta
                if transfer.mode in (TransferMode.TRANSFER, TransferMode.RECEIVER_ONLY):
                    candidate_targets[transfer.receiver] += delta
            controls = [control for control in step.injectors if control.well not in affected]
            for well in sorted(affected, key=natural_key):
                controls.append(replace(templates[well], surface_rate=candidate_targets[well]))
            steps.append(ScheduleStep(step.date, step.producers, tuple(controls)))
        return Schedule(tuple(steps)).canonical()


def _validate_transfers(transfers: Sequence[TransferSpec], state: FieldState) -> None:
    if not transfers:
        raise MultiTransferError("at least one transfer is required")
    assert_non_overlapping(transfers)
    for transfer in transfers:
        if transfer.donor == transfer.receiver:
            raise MultiTransferError("donor and receiver are identical")
        if not 0.0 < transfer.fraction < 1.0:
            raise MultiTransferError(f"fraction outside (0, 1): {transfer.fraction}")
        block = block_of_injector(transfer.donor)
        receiver_block = block_of_injector(transfer.receiver)
        if block is None or receiver_block is None or block.block_id != receiver_block.block_id:
            raise MultiTransferError("transfer must stay inside one screening block")
        if block.block_id != transfer.block_id:
            raise MultiTransferError(
                f"transfer claims block {transfer.block_id}, got {block.block_id}"
            )
        for well in transfer.wells:
            well_state = state.by_well.get(well)
            if well_state is None:
                raise MultiTransferError(f"unknown well {well}")
            if well_state.role.name != "INJECTOR":
                raise MultiTransferError(f"well {well} is not an injector")
            if well_state.target is None:
                raise MultiTransferError(f"well {well} has no target")


def _initial_injector_state(
    transfers: Sequence[TransferSpec], state: FieldState
) -> tuple[dict[str, InjectorControl], dict[str, float]]:
    templates: dict[str, InjectorControl] = {}
    targets: dict[str, float] = {}
    for well in {well for transfer in transfers for well in transfer.wells}:
        template = state.by_well[well].as_injector()
        templates[well] = template
        targets[well] = float(template.surface_rate or 0.0)
    return templates, targets


def build_exp002_schedule(
    scenario: Exp002Scenario, case: Case, *, state: FieldState, baseline: Schedule
) -> tuple[Schedule, str]:
    policy = MultiTransferPolicy(scenario.transfers, f"EXP002::{scenario.scenario_id}")
    schedule = policy.plan(state, baseline, case)
    text = render_schedule(schedule, case, policy_id=policy.policy_id)
    return schedule, text


def validate_exp002_candidate(
    scenario: Exp002Scenario,
    case: Case,
    *,
    state: FieldState,
    baseline: Schedule,
    candidate: Schedule,
) -> dict[str, Any]:
    base_report = validate_schedule(candidate, state, case)
    gates: list[GateResult] = []
    gates.append(
        _gate(
            "producer_controls_unchanged",
            _producer_violations(baseline, candidate),
            "producer controls identical",
        )
    )
    gates.append(
        _gate(
            "other_injectors_unchanged",
            _other_injector_violations(scenario, baseline, candidate),
            "only transfer wells changed",
        )
    )
    gates.append(
        _gate(
            "transfer_controls_valid",
            _target_violations(candidate),
            "all candidate injector targets non-negative and finite",
        )
    )
    gates.append(
        _gate(
            "pre_window_history_immutable",
            _pre_window_violations(case, baseline, candidate),
            "candidate starts at management window",
        )
    )
    if scenario.candidate_type is CandidateType.ADMISSIBLE:
        gates.append(
            _gate(
                "per_pair_scheduled_conservation",
                _conservation_violations(scenario, state, baseline, candidate),
                "per-pair scheduled totals conserved",
            )
        )
    valid = base_report.is_valid and all(gate.status is GateStatus.PASS for gate in gates)
    return {
        "validation_001": base_report.to_json(),
        "experiment": [gate.to_json() for gate in gates],
        "is_valid": valid,
        "failed": [gate.to_json() for gate in gates if gate.status is GateStatus.FAIL],
    }


def _gate(gate_id: str, violations: list[str], ok_detail: str) -> GateResult:
    if violations:
        return GateResult(
            gate_id, GateStatus.FAIL, f"{len(violations)} violation(s)", tuple(violations)
        )
    return GateResult(gate_id, GateStatus.PASS, ok_detail)


def _producer_violations(baseline: Schedule, candidate: Schedule) -> list[str]:
    problems: list[str] = []
    base_steps = {step.date: step for step in baseline.canonical().steps}
    for step in candidate.canonical().steps:
        base = base_steps.get(step.date)
        if base is None or step.producers != base.producers:
            problems.append(f"{step.date.isoformat()}: producer controls differ")
    if len(base_steps) != len(candidate.steps):
        problems.append("candidate step count differs from baseline")
    return problems


def _other_injector_violations(
    scenario: Exp002Scenario, baseline: Schedule, candidate: Schedule
) -> list[str]:
    affected = {well for transfer in scenario.transfers for well in transfer.wells}
    problems: list[str] = []
    base_steps = {step.date: step for step in baseline.canonical().steps}
    for step in candidate.canonical().steps:
        base = base_steps.get(step.date)
        if base is None:
            continue
        base_others = tuple(c for c in base.injectors if c.well not in affected)
        cand_others = tuple(c for c in step.injectors if c.well not in affected)
        if base_others != cand_others:
            problems.append(f"{step.date.isoformat()}: injector outside transfer set differs")
    return problems


def _target_violations(candidate: Schedule) -> list[str]:
    problems: list[str] = []
    for step in candidate.canonical().steps:
        for control in step.injectors:
            target = control.surface_rate
            if target is None or not math.isfinite(target):
                problems.append(f"{step.date.isoformat()} {control.well}: non-finite target")
            elif target < 0.0:
                problems.append(f"{step.date.isoformat()} {control.well}: negative target {target}")
    return problems


def _pre_window_violations(case: Case, baseline: Schedule, candidate: Schedule) -> list[str]:
    problems: list[str] = []
    for base, cand in zip(baseline.canonical().steps, candidate.canonical().steps, strict=True):
        if base.date < case.management_start_date and base != cand:
            problems.append(f"{base.date.isoformat()}: pre-window step changed")
    return problems


def _effective_targets(
    state: FieldState, schedule: Schedule, wells: Sequence[str]
) -> dict[str, dict[str, float]]:
    targets = {well: float(state.by_well[well].target or 0.0) for well in wells}
    effective: dict[str, dict[str, float]] = {}
    for step in schedule.canonical().steps:
        for control in step.injectors:
            if control.well in targets:
                targets[control.well] = float(control.surface_rate or 0.0)
        effective[step.date.isoformat()] = dict(targets)
    return effective


def _conservation_violations(
    scenario: Exp002Scenario, state: FieldState, baseline: Schedule, candidate: Schedule
) -> list[str]:
    wells = tuple({well for transfer in scenario.transfers for well in transfer.wells})
    base = _effective_targets(state, baseline, wells)
    cand = _effective_targets(state, candidate, wells)
    problems: list[str] = []
    for day, base_targets in base.items():
        cand_targets = cand[day]
        for transfer in scenario.transfers:
            base_donor = base_targets[transfer.donor]
            base_receiver = base_targets[transfer.receiver]
            cand_donor = cand_targets[transfer.donor]
            cand_receiver = cand_targets[transfer.receiver]
            delta = transfer.fraction * base_donor if base_donor > 0.0 else 0.0
            if not math.isclose(cand_donor, base_donor - delta, abs_tol=1e-9):
                problems.append(f"{day} {transfer.component_id}: donor target mismatch")
            if not math.isclose(cand_receiver, base_receiver + delta, abs_tol=1e-9):
                problems.append(f"{day} {transfer.component_id}: receiver target mismatch")
            if not math.isclose(
                cand_donor + cand_receiver, base_donor + base_receiver, abs_tol=1e-9
            ):
                problems.append(f"{day} {transfer.component_id}: pair total mismatch")
    return problems


def _observed_wells(scenario: Exp002Scenario) -> tuple[str, ...]:
    wells: list[str] = []
    for transfer in scenario.transfers:
        block = BLOCKS_BY_ID[transfer.block_id]
        wells.extend(block.producers)
        wells.extend(block.injectors)
        wells.extend(transfer.wells)
    return tuple(dict.fromkeys(sorted(wells, key=natural_key)))


_CHDD_LOCK = threading.Lock()


def run_exp002_scenario(
    scenario: Exp002Scenario,
    case: Case,
    *,
    runtime_root: Path,
    state: FieldState,
    baseline: Schedule,
    history_text: str,
    baseline_metrics: RunMetrics | None = None,
    baseline_summary: SummaryData | None = None,
    keep_workspace: bool = False,
) -> Exp002Result:
    started = time.monotonic()
    result = Exp002Result(scenario=scenario)
    scenario_dir = runtime_root / scenario.scenario_id
    if scenario_dir.exists():
        shutil.rmtree(scenario_dir)
    scenario_dir.mkdir(parents=True)

    try:
        schedule, schedule_text = build_exp002_schedule(
            scenario, case, state=state, baseline=baseline
        )
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"schedule: {type(exc).__name__}: {exc}")
        return _finish_result(scenario_dir, result, started)

    result.schedule_sha256 = schedule_sha256(schedule_text)
    (scenario_dir / "wells_schedule.inc").write_text(schedule_text, encoding="utf-8")
    gate_report = validate_exp002_candidate(
        scenario, case, state=state, baseline=baseline, candidate=schedule
    )
    result.gate_report = gate_report
    if not gate_report["is_valid"]:
        result.notes.extend(
            f"gate {gate['gate_id']}: {gate['detail']}" for gate in gate_report["failed"]
        )
        return _finish_result(scenario_dir, result, started)
    result.constraints_status = STATUS_PASS

    workspace_root = scenario_dir / "workspace"
    try:
        workspace = prepare_workspace(
            case, root=workspace_root, history_text=history_text, schedule_text=schedule_text
        )
        opm_result, summary = run_simulation(case, workspace)
    except (WorkspaceError, SimulationError, SummaryReadError, OSError) as exc:
        result.notes.append(f"simulation: {exc}")
        _prune_workspace(workspace_root)
        return _finish_result(scenario_dir, result, started)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"unhandled {type(exc).__name__}: {exc}")
        _prune_workspace(workspace_root)
        return _finish_result(scenario_dir, result, started)

    result.opm_result = opm_result
    _write_log_tail(opm_result.log_path, scenario_dir / LOG_TAIL_FILENAME)
    if not opm_result.is_pass or summary is None:
        result.notes.extend(opm_result.errors[:5] or ["simulation produced no summary"])
        _prune_workspace(workspace_root)
        return _finish_result(scenario_dir, result, started)
    result.simulation_status = STATUS_PASS

    try:
        chdd_input = build_chdd_input(summary, case)
        csv_path = write_chdd_csv(chdd_input, scenario_dir / "chdd_input.csv")
        with _CHDD_LOCK:
            result.chdd_result = run_chdd(case, chdd_input, source_file=csv_path)
        result.metrics = collect_metrics(
            summary,
            wells=_observed_wells(scenario),
            window_start=case.management_start_date.isoformat(),
        )
        if baseline_metrics is not None:
            result.metric_delta = compare_metrics(result.metrics, baseline_metrics)
        result.diagnostics = transfer_diagnostics(
            scenario, case, state, baseline, schedule, summary, baseline_summary
        )
    except (TrajectoryError, ChddError, SummaryReadError, OSError) as exc:
        result.notes.append(f"chdd: {exc}")
        _prune_workspace(workspace_root)
        return _finish_result(scenario_dir, result, started)
    except Exception as exc:  # noqa: BLE001
        result.notes.append(f"unhandled {type(exc).__name__}: {exc}")
        _prune_workspace(workspace_root)
        return _finish_result(scenario_dir, result, started)

    result.chdd_status = STATUS_PASS
    if not keep_workspace:
        _prune_workspace(workspace_root)
        (scenario_dir / "chdd_input.csv").unlink(missing_ok=True)
    return _finish_result(scenario_dir, result, started)


def _finish_result(scenario_dir: Path, result: Exp002Result, started: float) -> Exp002Result:
    result.wall_seconds = time.monotonic() - started
    temp = scenario_dir / "result.json.tmp"
    final = scenario_dir / "result.json"
    temp.write_text(
        json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(final)
    return result


def _prune_workspace(workspace_root: Path) -> None:
    if workspace_root.is_dir():
        shutil.rmtree(workspace_root, ignore_errors=True)


def run_exp002_batch(
    scenarios: Sequence[Exp002Scenario],
    case: Case,
    *,
    runtime_root: Path,
    baseline_metrics: RunMetrics | None,
    baseline_summary: SummaryData | None,
    max_parallelism: int = MAX_PARALLELISM,
    on_result: Callable[[Exp002Result], None] | None = None,
) -> list[Exp002Result]:
    state, baseline, history_text = read_baseline_inputs(case)
    workers = max(1, min(max_parallelism, len(scenarios))) if scenarios else 1

    def task(scenario: Exp002Scenario) -> Exp002Result:
        result = run_exp002_scenario(
            scenario,
            case,
            runtime_root=runtime_root,
            state=state,
            baseline=baseline,
            history_text=history_text,
            baseline_metrics=baseline_metrics,
            baseline_summary=baseline_summary,
        )
        if on_result is not None:
            on_result(result)
        return result

    if workers == 1:
        return [task(scenario) for scenario in scenarios]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(task, scenarios))


def _float_or_none(value: Any) -> float | None:
    if value in (None, ""):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def totals_from_chdd(
    chdd: ChddResult | None,
) -> tuple[float | None, float | None, float | None, float | None]:
    if chdd is None:
        return None, None, None, None
    oil = chdd.oil_kt_total * 1000.0
    liquid = chdd.liquid_kt_total * 1000.0
    injection = chdd.injection_k_total * 1000.0
    water = liquid - oil
    return oil, liquid, water, injection


def row_for_result(
    result: Exp002Result,
    baseline_chdd: ChddResult,
    *,
    behavior: str | None = None,
    individual_delta_by_component: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    oil, liquid, water, injection = totals_from_chdd(result.chdd_result)
    base_oil, base_liquid, base_water, base_injection = totals_from_chdd(baseline_chdd)
    npv = result.npv_mln_rub
    base_npv = baseline_chdd.npv_mln_rub
    delta_npv = None if npv is None else npv - base_npv
    sum_individual = None
    interaction_delta = None
    interaction_class = InteractionClass.NOT_APPLICABLE.value
    if (
        individual_delta_by_component
        and len(result.scenario.transfers) > 1
        and delta_npv is not None
    ):
        pieces = [
            individual_delta_by_component.get(t.component_id) for t in result.scenario.transfers
        ]
        if all(piece is not None for piece in pieces):
            sum_individual = float(sum(piece for piece in pieces if piece is not None))
            interaction_delta = delta_npv - sum_individual
            interaction_class = classify_interaction(interaction_delta).value
    return {
        "scenario_id": result.scenario.scenario_id,
        "stage": result.scenario.stage,
        "candidate_type": result.scenario.candidate_type.value,
        "transfers": json.dumps([t.to_json() for t in result.scenario.transfers], sort_keys=True),
        "simulation_status": result.simulation_status,
        "constraints_status": result.constraints_status,
        "chdd_status": result.chdd_status,
        "schedule_sha256": result.schedule_sha256,
        "npv_mln_rub": npv,
        "delta_npv_mln_rub": delta_npv,
        "delta_npv_pct": None if delta_npv is None else delta_npv / base_npv * 100.0,
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
        "field_final_pressure_delta_bar": _field_delta(result, "FPR", "final"),
        "field_mean_pressure_delta_bar": _field_delta(result, "FPR", "mean"),
        "behavior_class": behavior,
        "interaction_class": interaction_class,
        "sum_individual_delta_npv": sum_individual,
        "interaction_delta_npv": interaction_delta,
        "wall_seconds": round(result.wall_seconds, 3),
        "notes": "; ".join(result.notes)[:500],
    }


def _field_delta(result: Exp002Result, vector: str, stat: str) -> float | None:
    try:
        field = result.metric_delta or {}
        return float(field["field"][vector]["window"][stat])
    except (KeyError, TypeError, ValueError):
        return None


def stage_a_row(
    result: Exp002Result, baseline_chdd: ChddResult, behavior: str | None = None
) -> dict[str, Any]:
    row = row_for_result(result, baseline_chdd, behavior=behavior)
    if not result.diagnostics:
        return row
    diag = result.diagnostics[0]
    row.update(
        {
            "donor": diag.transfer.donor,
            "receiver": diag.transfer.receiver,
            "fraction": diag.transfer.fraction,
            "donor_target_baseline_first": diag.donor_target_baseline_first,
            "receiver_target_baseline_first": diag.receiver_target_baseline_first,
            "donor_target_candidate_first": diag.donor_target_candidate_first,
            "receiver_target_candidate_first": diag.receiver_target_candidate_first,
            "scheduled_transfer_volume": diag.scheduled_transfer_volume,
            "donor_actual_injection_m3": diag.donor_actual_injection_m3,
            "receiver_actual_injection_m3": diag.receiver_actual_injection_m3,
            "delta_actual_donor_injection_m3": diag.delta_actual_donor_injection_m3,
            "delta_actual_receiver_injection_m3": diag.delta_actual_receiver_injection_m3,
            "pair_total_actual_injection_delta_m3": diag.pair_total_actual_injection_delta_m3,
            "receiver_realized_added_injection": diag.receiver_realized_added_injection,
            "realization_ratio": diag.realization_ratio,
            "donor_max_wbhp_bar": diag.donor_saturation.max_wbhp_bar,
            "receiver_max_wbhp_bar": diag.receiver_saturation.max_wbhp_bar,
            "donor_bhp_limit_bar": diag.donor_saturation.bhp_limit_bar,
            "receiver_bhp_limit_bar": diag.receiver_saturation.bhp_limit_bar,
            "donor_bhp_hit_count": diag.donor_saturation.hit_count,
            "receiver_bhp_hit_count": diag.receiver_saturation.hit_count,
            "donor_first_bhp_hit_date": diag.donor_saturation.first_hit_date,
            "receiver_first_bhp_hit_date": diag.receiver_saturation.first_hit_date,
            "donor_last_bhp_hit_date": diag.donor_saturation.last_hit_date,
            "receiver_last_bhp_hit_date": diag.receiver_saturation.last_hit_date,
        }
    )
    return row


def transfer_diagnostics(
    scenario: Exp002Scenario,
    case: Case,
    state: FieldState,
    baseline: Schedule,
    candidate: Schedule,
    summary: SummaryData,
    baseline_summary: SummaryData | None,
) -> list[TransferDiagnostics]:
    wells = tuple({well for transfer in scenario.transfers for well in transfer.wells})
    base_targets = _effective_targets(state, baseline, wells)
    cand_targets = _effective_targets(state, candidate, wells)
    first_day = case.management_start_date.isoformat()
    return [
        _one_transfer_diagnostics(
            transfer, case, summary, baseline_summary, base_targets, cand_targets, first_day
        )
        for transfer in scenario.transfers
    ]


def _one_transfer_diagnostics(
    transfer: TransferSpec,
    case: Case,
    summary: SummaryData,
    baseline_summary: SummaryData | None,
    base_targets: Mapping[str, Mapping[str, float]],
    cand_targets: Mapping[str, Mapping[str, float]],
    first_day: str,
) -> TransferDiagnostics:
    donor_cum = cumulative_window(summary, transfer.donor, case.management_start_date.isoformat())
    receiver_cum = cumulative_window(
        summary, transfer.receiver, case.management_start_date.isoformat()
    )
    donor_delta = _well_cum_delta(
        baseline_summary, transfer.donor, donor_cum, case.management_start_date.isoformat()
    )
    receiver_delta = _well_cum_delta(
        baseline_summary, transfer.receiver, receiver_cum, case.management_start_date.isoformat()
    )
    scheduled = scheduled_transfer_volume(transfer, case, base_targets)
    ratio = (
        None if scheduled in (None, 0.0) or receiver_delta is None else receiver_delta / scheduled
    )
    return TransferDiagnostics(
        transfer=transfer,
        donor_target_baseline_first=base_targets[first_day].get(transfer.donor),
        receiver_target_baseline_first=base_targets[first_day].get(transfer.receiver),
        donor_target_candidate_first=cand_targets[first_day].get(transfer.donor),
        receiver_target_candidate_first=cand_targets[first_day].get(transfer.receiver),
        scheduled_transfer_volume=scheduled,
        donor_actual_injection_m3=donor_cum,
        receiver_actual_injection_m3=receiver_cum,
        delta_actual_donor_injection_m3=donor_delta,
        delta_actual_receiver_injection_m3=receiver_delta,
        pair_total_actual_injection_delta_m3=None
        if donor_delta is None or receiver_delta is None
        else donor_delta + receiver_delta,
        receiver_realized_added_injection=receiver_delta,
        realization_ratio=ratio,
        donor_saturation=saturation_for_well(
            summary,
            transfer.donor,
            _bhp_limit(case, base_targets, transfer.donor),
            case.management_start_date.isoformat(),
        ),
        receiver_saturation=saturation_for_well(
            summary,
            transfer.receiver,
            _bhp_limit(case, base_targets, transfer.receiver),
            case.management_start_date.isoformat(),
        ),
    )


def scheduled_transfer_volume(
    transfer: TransferSpec, case: Case, base_targets: Mapping[str, Mapping[str, float]]
) -> float | None:
    days = sorted(base_targets)
    if not days:
        return None
    total = 0.0
    for index, day in enumerate(days):
        start = date.fromisoformat(day)
        if start < case.management_start_date or start > case.management_end_date:
            continue
        end = (
            date.fromisoformat(days[index + 1])
            if index + 1 < len(days)
            else case.management_end_date
        )
        if end <= start:
            continue
        donor = base_targets[day][transfer.donor]
        total += max(0.0, donor) * transfer.fraction * (end - start).days
    return total


def cumulative_window(summary: SummaryData, well: str, window_start: str) -> float | None:
    if not summary.has_well_vector("WWIT", well):
        return None
    dates = tuple(moment.date().isoformat() for moment in summary.dates)
    start_index = next((idx for idx, day in enumerate(dates) if day >= window_start), 0)
    last = summary.report_steps - 1
    return summary.well_value("WWIT", well, last) - summary.well_value("WWIT", well, start_index)


def _well_cum_delta(
    baseline_summary: SummaryData | None,
    well: str,
    candidate_value: float | None,
    window_start: str,
) -> float | None:
    if baseline_summary is None or candidate_value is None:
        return None
    base_value = cumulative_window(baseline_summary, well, window_start)
    if base_value is None:
        return None
    return candidate_value - base_value


def _bhp_limit(
    case: Case, base_targets: Mapping[str, Mapping[str, float]], well: str
) -> float | None:
    limit = case.constraints.injector_bhp_max
    if limit is not None:
        return limit
    return None


def saturation_for_well(
    summary: SummaryData, well: str, bhp_limit: float | None, window_start: str
) -> WellSaturation:
    if not summary.has_well_vector("WBHP", well):
        return WellSaturation(well, None, bhp_limit, 0, None, None)
    dates = tuple(moment.date().isoformat() for moment in summary.dates)
    start_index = next((idx for idx, day in enumerate(dates) if day >= window_start), 0)
    series = summary.well_series("WBHP", well)[start_index:]
    max_wbhp = max(series) if series else None
    hit_dates: list[str] = []
    if bhp_limit is not None:
        hit_dates = [
            dates[start_index + idx]
            for idx, value in enumerate(series)
            if abs(value - bhp_limit) <= BHP_HIT_TOLERANCE_BAR
        ]
    return WellSaturation(
        well=well,
        max_wbhp_bar=max_wbhp,
        bhp_limit_bar=bhp_limit,
        hit_count=len(hit_dates),
        first_hit_date=hit_dates[0] if hit_dates else None,
        last_hit_date=hit_dates[-1] if hit_dates else None,
    )


def classify_interaction(delta: float) -> InteractionClass:
    if delta > NEAR_ADDITIVE_TOLERANCE_MLN_RUB:
        return InteractionClass.POSITIVE_INTERACTION
    if delta < -NEAR_ADDITIVE_TOLERANCE_MLN_RUB:
        return InteractionClass.NEGATIVE_INTERACTION
    return InteractionClass.NEAR_ADDITIVE


def classify_sequence(rows: Sequence[Mapping[str, Any]]) -> BehaviorClass:
    values = [
        (
            float(row["fraction"]),
            _float_or_none(row.get("delta_npv_mln_rub")),
            int(row.get("receiver_bhp_hit_count") or 0),
        )
        for row in rows
    ]
    values = [(fraction, delta, hits) for fraction, delta, hits in values if delta is not None]
    if len(values) < 2:
        return BehaviorClass.INSUFFICIENT_DATA
    deltas = [delta for _fraction, delta, _hits in values]
    hits = [hits for _fraction, _delta, hits in values]
    nondecreasing = all(b >= a - 1e-9 for a, b in pairwise(deltas))
    nonincreasing = all(b <= a + 1e-9 for a, b in pairwise(deltas))
    best_index = max(range(len(deltas)), key=deltas.__getitem__)
    if hits[best_index] > 0 and best_index == len(deltas) - 1:
        return BehaviorClass.CONSTRAINT_SATURATED
    if best_index == len(deltas) - 1 and nondecreasing:
        return BehaviorClass.BOUNDARY_BEST
    if nondecreasing:
        return BehaviorClass.MONOTONE_INCREASING
    if nonincreasing:
        return BehaviorClass.DECREASING
    if 0 < best_index < len(deltas) - 1:
        return BehaviorClass.BRACKETED_PEAK
    return BehaviorClass.PLATEAU


def rows_by_pair(
    rows: Sequence[Mapping[str, Any]],
) -> dict[tuple[int, str, str], list[Mapping[str, Any]]]:
    grouped: dict[tuple[int, str, str], list[Mapping[str, Any]]] = {}
    for row in rows:
        key = (
            int(row["transfers_obj"][0].block_id),
            row["transfers_obj"][0].donor,
            row["transfers_obj"][0].receiver,
        )
        grouped.setdefault(key, []).append(row)
    return grouped


def apply_behavior_classes(rows: list[dict[str, Any]]) -> None:
    grouped: dict[tuple[int, str, str], list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(
            (int(row["transfers_obj"][0].block_id), row["donor"], row["receiver"]), []
        ).append(row)
    for group in grouped.values():
        group.sort(key=lambda row: float(row["fraction"]))
        behavior = classify_sequence(group).value
        for row in group:
            row["behavior_class"] = behavior


def best_transfer_specs(
    rows: Sequence[Mapping[str, Any]],
) -> tuple[dict[int, TransferSpec], dict[tuple[str, str], TransferSpec], dict[str, float]]:
    best_by_block: dict[int, Mapping[str, Any]] = {}
    best_by_pair: dict[tuple[str, str], Mapping[str, Any]] = {}
    delta_by_component: dict[str, float] = {}
    for row in rows:
        delta = _float_or_none(row.get("delta_npv_mln_rub"))
        if delta is None:
            continue
        transfer = row["transfers_obj"][0]
        delta_by_component[transfer.component_id] = delta
        if transfer.block_id not in best_by_block or delta > float(
            best_by_block[transfer.block_id]["delta_npv_mln_rub"]
        ):
            best_by_block[transfer.block_id] = row
        pair = (transfer.donor, transfer.receiver)
        if pair not in best_by_pair or delta > float(best_by_pair[pair]["delta_npv_mln_rub"]):
            best_by_pair[pair] = row
    return (
        {block: row["transfers_obj"][0] for block, row in best_by_block.items()},
        {pair: row["transfers_obj"][0] for pair, row in best_by_pair.items()},
        delta_by_component,
    )


def exp001_rows_as_candidates(rows: Sequence[Mapping[str, str]]) -> list[dict[str, Any]]:
    converted: list[dict[str, Any]] = []
    for row in rows:
        if not row.get("scenario_id", "").startswith("EXP001_B"):
            continue
        transfer = _component_from_row(row)
        converted.append(
            {
                "scenario_id": row["scenario_id"],
                "stage": "EXP001",
                "candidate_type": CandidateType.ADMISSIBLE.value,
                "transfers": json.dumps([transfer.to_json()], sort_keys=True),
                "transfers_obj": (transfer,),
                "simulation_status": row.get("simulation_status"),
                "constraints_status": row.get("constraints_status"),
                "chdd_status": STATUS_PASS if row.get("npv_mln_rub") else STATUS_FAIL,
                "schedule_sha256": row.get("schedule_sha256"),
                "npv_mln_rub": _float_or_none(row.get("npv_mln_rub")),
                "delta_npv_mln_rub": _float_or_none(row.get("delta_npv_mln_rub")),
                "delta_npv_pct": _float_or_none(row.get("delta_npv_pct")),
                "oil_t": _float_or_none(row.get("oil_t")),
                "delta_oil_t": _float_or_none(row.get("delta_oil_t")),
                "liquid_t": _float_or_none(row.get("liquid_t")),
                "delta_liquid_t": _float_or_none(row.get("delta_liquid_t")),
                "water_t": None,
                "delta_water_t": None,
                "injection_m3": _float_or_none(row.get("injection_m3")),
                "delta_injection_m3": _float_or_none(row.get("delta_injection_m3")),
                "field_final_pressure_delta_bar": None,
                "field_mean_pressure_delta_bar": None,
                "behavior_class": None,
                "interaction_class": InteractionClass.NOT_APPLICABLE.value,
                "sum_individual_delta_npv": None,
                "interaction_delta_npv": None,
                "wall_seconds": _float_or_none(row.get("wall_seconds")),
                "notes": row.get("notes", ""),
            }
        )
    return converted


def public_row(row: Mapping[str, Any], columns: Sequence[str] = RESULT_COLUMNS) -> dict[str, Any]:
    return {key: row.get(key) for key in columns}


def write_csv(path: Path, rows: Sequence[Mapping[str, Any]], columns: Sequence[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=columns, extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow(public_row(row, columns))
    temp.replace(path)


def rank_admissible(rows: Sequence[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    ranked = [
        row
        for row in rows
        if row.get("candidate_type") == CandidateType.ADMISSIBLE.value
        and _float_or_none(row.get("delta_npv_mln_rub")) is not None
    ]
    return sorted(ranked, key=lambda row: (-float(row["delta_npv_mln_rub"]), row["scenario_id"]))


def summary_payload(
    *,
    baseline: Mapping[str, Any],
    exp001_winner: Mapping[str, Any],
    stage_a_rows: Sequence[Mapping[str, Any]],
    decomposition_rows: Sequence[Mapping[str, Any]],
    combination_rows: Sequence[Mapping[str, Any]],
    all_rows: Sequence[Mapping[str, Any]],
    parallelism: int,
    wall_seconds: float,
) -> dict[str, Any]:
    ranked = rank_admissible(all_rows)
    best = ranked[0] if ranked else None
    best_stage_a = rank_admissible(stage_a_rows)[0] if rank_admissible(stage_a_rows) else None
    best_combo = rank_admissible(combination_rows)[0] if rank_admissible(combination_rows) else None
    return {
        "baseline": baseline,
        "exp001_winner_reconciled": exp001_winner,
        "counts": {
            "stage_a": len(stage_a_rows),
            "decomposition": len(decomposition_rows),
            "combinations": len(combination_rows),
            "all_ranked_rows": len(all_rows),
            "stage_a_pass": sum(1 for row in stage_a_rows if row.get("npv_mln_rub") is not None),
            "combination_pass": sum(
                1 for row in combination_rows if row.get("npv_mln_rub") is not None
            ),
        },
        "best_exp001": public_row(exp001_winner),
        "best_single_exp002": None if best_stage_a is None else public_row(best_stage_a),
        "best_combination": None if best_combo is None else public_row(best_combo),
        "overall_best_verified_candidate": None if best is None else public_row(best),
        "parallelism": parallelism,
        "wall_seconds": round(wall_seconds, 1),
    }


def run_reconciliation(
    case: Case, runtime_root: Path, exp001_rows: Sequence[Mapping[str, str]]
) -> tuple[Any, Any]:
    baseline = run_baseline(
        case,
        runtime_root=runtime_root,
        expected_npv=BASELINE_NPV_MLN_RUB,
        expected_schedule_sha256=BASELINE_SCHEDULE_SHA256,
        tolerance=BASELINE_TOLERANCE,
    )
    winner = exp001_winner_row(exp001_rows)
    if (
        _float_or_none(winner.get("npv_mln_rub")) is None
        or abs(float(winner["npv_mln_rub"]) - EXP001_WINNER_NPV_MLN_RUB) > BASELINE_TOLERANCE
    ):
        raise ValueError(f"EXP-001 winner NPV mismatch: {winner.get('npv_mln_rub')}")
    if winner.get("schedule_sha256") != EXP001_WINNER_SCHEDULE_SHA256:
        raise ValueError(f"EXP-001 winner SHA mismatch: {winner.get('schedule_sha256')}")
    return baseline, winner
