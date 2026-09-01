"""Прогон сценария EXP-004 и снятие всех требуемых показателей.

Стек тот же, что прошёл независимую проверку в VALIDATION-001 и в трёх
экспериментах: тот же генератор расписания, те же ворота Track 1, тот же
образ симулятора, тот же официальный калькулятор ЧДД.

Новое здесь -- измерения. Для каждого сценария снимается не только
экономика, но и то, чем за неё заплачено: замещение отбора, пластовое
давление, насыщение по забойному давлению у нагнетательных, и главное --
разница между заказанной и фактической производительностью добывающих.
Именно она отличает работающее воздействие от бумажного.
"""

from __future__ import annotations

import json
import shutil
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.chdd.runner import ChddError, ChddResult, run_chdd
from aios.chdd.trajectory import TrajectoryError, build_chdd_input, write_chdd_csv
from aios.experiments.exp002 import STATUS_FAIL, STATUS_PASS
from aios.experiments.exp003 import economic_decomposition
from aios.experiments.exp004 import (
    WLPR_HARD_CEILING,
    Exp004Scenario,
    build_exp004_schedule,
)
from aios.experiments.runner import LOG_TAIL_FILENAME, _write_log_tail
from aios.experiments.vrr import (
    VrrError,
    WaterPvt,
    cumulative_vrr,
    vrr_envelope,
    vrr_series,
    write_vrr_csv,
)
from aios.opm.runner import OpmRunResult, SimulationError, run_simulation
from aios.opm.summary import SummaryData, SummaryReadError
from aios.opm.workspace import WorkspaceError, prepare_workspace
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.events import Schedule
from aios.schedule.generator import schedule_sha256
from aios.schedule.state import FieldState
from aios.schedule.validator import GateStatus, validate_schedule

MAX_PARALLELISM = 4
RESULT_FILENAME = "result.json"
SCHEDULE_FILENAME = "wells_schedule.inc"

# Скважина считается упирающейся в предел, если её забойное давление
# отстоит от предела не более чем на этот допуск.
BHP_TOLERANCE_BAR = 0.1
# Уставка считается достигнутой, если фактический дебит отстоит от неё
# менее чем на эту долю.
TARGET_REACHED_TOLERANCE = 0.02

_CHDD_LOCK = threading.Lock()


RUNTIME_PLACEHOLDER = "<runtime>"


def _portable(payload: Any, runtime_root: Path | None) -> Any:
    """Заменить абсолютный путь запуска на заполнитель.

    Команда запуска симулятора содержит абсолютный путь рабочего каталога на
    машине, где считали. В опубликованных артефактах такого пути быть не должно:
    он привязывает результат к чужой файловой системе и раскрывает её устройство.
    """

    if runtime_root is None:
        return payload
    prefix = str(runtime_root.resolve())
    if isinstance(payload, str):
        return payload.replace(prefix, RUNTIME_PLACEHOLDER)
    if isinstance(payload, dict):
        return {key: _portable(value, runtime_root) for key, value in payload.items()}
    if isinstance(payload, list):
        return [_portable(item, runtime_root) for item in payload]
    return payload


@dataclass(slots=True)
class Exp004Result:
    """Итог одного сценария EXP-004."""

    scenario: Exp004Scenario
    simulation_status: str = STATUS_FAIL
    constraints_status: str = STATUS_FAIL
    chdd_status: str = STATUS_FAIL
    schedule_sha256: str | None = None
    opm_result: OpmRunResult | None = None
    chdd_result: ChddResult | None = None
    vrr: dict[str, Any] | None = None
    pressure: dict[str, float] | None = None
    producers: dict[str, Any] | None = None
    injectors: dict[str, Any] | None = None
    decomposition: dict[str, float] | None = None
    wall_seconds: float = 0.0
    notes: list[str] = field(default_factory=list)
    runtime_root: Path | None = None

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
            "simulation": None if opm is None else _portable(opm.to_json(), self.runtime_root),
            "economics": None if self.chdd_result is None else self.chdd_result.to_json(),
            "economic_decomposition": self.decomposition,
            "vrr": self.vrr,
            "pressure": self.pressure,
            "producers": self.producers,
            "injectors": self.injectors,
            "notes": list(self.notes),
        }


def _pressure_profile(summary: SummaryData, window_start: str) -> dict[str, float]:
    dates = [m.date().isoformat() for m in summary.dates]
    start = next((i for i, d in enumerate(dates) if d >= window_start), 0)
    series = summary.field_series("FPR")[start:]
    return {
        "start_bar": series[0],
        "mean_bar": sum(series) / len(series),
        "final_bar": series[-1],
        "min_bar": min(series),
    }


def _producer_diagnostics(
    summary: SummaryData, case: Case, state: FieldState, schedule: Schedule, window_start: str
) -> dict[str, Any]:
    """Заказано против получено, по каждой добывающей скважине.

    Забойный предел берётся из ограничений кейса; уставка -- действующая на
    конец окна. Скважина, у которой давление на пределе, считается
    ограниченной физикой, а не планом: поднимать ей уставку бессмысленно.
    """

    floor = case.constraints.producer_bhp_min
    dates = [m.date().isoformat() for m in summary.dates]
    start = next((i for i, d in enumerate(dates) if d >= window_start), 0)

    targets: dict[str, float] = {s.well: float(s.target or 0.0) for s in state.producers}
    for step in schedule.canonical().steps:
        for control in step.producers:
            targets[control.well] = float(control.liquid_rate or 0.0)

    bhp_limited: list[str] = []
    target_limited: list[str] = []
    max_wlpr = 0.0
    violations: list[str] = []
    per_well: dict[str, dict[str, float]] = {}
    for well, target in sorted(targets.items()):
        if not summary.has_well_vector("WLPR", well):
            continue
        wlpr = summary.well_series("WLPR", well)[start:]
        wbhp = (
            summary.well_series("WBHP", well)[start:]
            if summary.has_well_vector("WBHP", well)
            else ()
        )
        realized_max = max(wlpr) if wlpr else 0.0
        max_wlpr = max(max_wlpr, realized_max)
        if realized_max > WLPR_HARD_CEILING:
            violations.append(f"{well}: realized WLPR {realized_max:.4f} > {WLPR_HARD_CEILING}")
        on_floor = (
            floor is not None
            and wbhp
            and sum(1 for v in wbhp if abs(v - floor) <= BHP_TOLERANCE_BAR) > 0
        )
        reached = target > 0.0 and realized_max >= target * (1.0 - TARGET_REACHED_TOLERANCE)
        if on_floor:
            bhp_limited.append(well)
        if reached:
            target_limited.append(well)
        per_well[well] = {
            "target": target,
            "realized_max_wlpr": realized_max,
            "realized_mean_wlpr": sum(wlpr) / len(wlpr) if wlpr else 0.0,
            "min_wbhp": min(wbhp) if wbhp else 0.0,
        }
    return {
        "count": len(per_well),
        "bhp_limited": sorted(bhp_limited, key=lambda w: (len(w), w)),
        "bhp_limited_count": len(bhp_limited),
        "target_limited": sorted(target_limited, key=lambda w: (len(w), w)),
        "target_limited_count": len(target_limited),
        "max_realized_wlpr": max_wlpr,
        "wlpr_violations": violations,
        "per_well": per_well,
    }


def _injector_diagnostics(
    summary: SummaryData, case: Case, state: FieldState, schedule: Schedule, window_start: str
) -> dict[str, Any]:
    ceiling = case.constraints.injector_bhp_max
    dates = [m.date().isoformat() for m in summary.dates]
    start = next((i for i, d in enumerate(dates) if d >= window_start), 0)
    last = summary.report_steps - 1

    targets: dict[str, float] = {s.well: float(s.target or 0.0) for s in state.injectors}
    for step in schedule.canonical().steps:
        for control in step.injectors:
            targets[control.well] = float(control.surface_rate or 0.0)

    max_wbhp = 0.0
    capped_states = 0
    capped_wells: list[str] = []
    scheduled_final = 0.0
    realized_total = 0.0
    for well, target in sorted(targets.items()):
        scheduled_final += target
        if summary.has_well_vector("WWIT", well):
            realized_total += summary.well_value("WWIT", well, last) - summary.well_value(
                "WWIT", well, start
            )
        if not summary.has_well_vector("WBHP", well):
            continue
        series = summary.well_series("WBHP", well)[start:]
        if series:
            max_wbhp = max(max_wbhp, max(series))
        if ceiling is not None:
            hits = sum(1 for v in series if abs(v - ceiling) <= BHP_TOLERANCE_BAR)
            capped_states += hits
            if hits:
                capped_wells.append(well)
    return {
        "bhp_ceiling_bar": ceiling,
        "max_wbhp_bar": max_wbhp,
        "cap_bound_states": capped_states,
        "cap_bound_wells": sorted(capped_wells, key=lambda w: (len(w), w)),
        "scheduled_final_total_rate_m3_per_day": scheduled_final,
        "realized_injection_window_m3": realized_total,
    }


def _prune(root: Path) -> None:
    if root.is_dir():
        shutil.rmtree(root, ignore_errors=True)


def _finish(scenario_dir: Path, result: Exp004Result, started: float) -> Exp004Result:
    result.wall_seconds = time.monotonic() - started
    temp = scenario_dir / (RESULT_FILENAME + ".tmp")
    temp.write_text(
        json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(scenario_dir / RESULT_FILENAME)
    return result


def run_exp004_scenario(
    scenario: Exp004Scenario,
    case: Case,
    *,
    runtime_root: Path,
    state: FieldState,
    baseline: Schedule,
    history_text: str,
    pvt: WaterPvt,
    keep_workspace: bool = False,
    write_vrr: bool = False,
) -> Exp004Result:
    started = time.monotonic()
    result = Exp004Result(scenario=scenario, runtime_root=runtime_root)
    scenario_dir = runtime_root / scenario.scenario_id
    if scenario_dir.exists():
        shutil.rmtree(scenario_dir)
    scenario_dir.mkdir(parents=True)

    try:
        schedule, schedule_text = build_exp004_schedule(
            scenario, case, state=state, baseline=baseline
        )
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ
        result.notes.append(f"schedule: {type(exc).__name__}: {exc}")
        return _finish(scenario_dir, result, started)

    result.schedule_sha256 = schedule_sha256(schedule_text)
    (scenario_dir / SCHEDULE_FILENAME).write_text(schedule_text, encoding="utf-8")

    report = validate_schedule(schedule, state, case)
    if not report.is_valid:
        result.notes.extend(f"gate {g.gate_id}: {g.detail}" for g in report.failed)
        return _finish(scenario_dir, result, started)
    result.constraints_status = STATUS_PASS

    workspace_root = scenario_dir / "workspace"
    try:
        workspace = prepare_workspace(
            case, root=workspace_root, history_text=history_text, schedule_text=schedule_text
        )
        opm_result, summary = run_simulation(case, workspace)
    except (WorkspaceError, SimulationError, SummaryReadError, OSError) as exc:
        result.notes.append(f"simulation: {exc}")
        _prune(workspace_root)
        return _finish(scenario_dir, result, started)
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ
        result.notes.append(f"unhandled {type(exc).__name__}: {exc}")
        _prune(workspace_root)
        return _finish(scenario_dir, result, started)

    result.opm_result = opm_result
    _write_log_tail(opm_result.log_path, scenario_dir / LOG_TAIL_FILENAME)
    if not opm_result.is_pass or summary is None:
        result.notes.extend(opm_result.errors[:5] or ["simulation produced no summary"])
        _prune(workspace_root)
        return _finish(scenario_dir, result, started)
    result.simulation_status = STATUS_PASS

    window_start = case.management_start_date.isoformat()
    try:
        chdd_input = build_chdd_input(summary, case)
        csv_path = write_chdd_csv(chdd_input, scenario_dir / "chdd_input.csv")
        with _CHDD_LOCK:
            result.chdd_result = run_chdd(case, chdd_input, source_file=csv_path)
        result.decomposition = economic_decomposition(result.chdd_result)
        result.pressure = _pressure_profile(summary, window_start)
        result.producers = _producer_diagnostics(summary, case, state, schedule, window_start)
        result.injectors = _injector_diagnostics(summary, case, state, schedule, window_start)
        points = vrr_series(summary, pvt, window_start=window_start)
        envelope = vrr_envelope(points)
        envelope["cumulative"] = cumulative_vrr(summary, pvt, window_start=window_start)
        result.vrr = envelope
        if write_vrr:
            write_vrr_csv(points, scenario_dir / "vrr.csv")
    except (TrajectoryError, ChddError, SummaryReadError, VrrError, OSError) as exc:
        result.notes.append(f"post-processing: {exc}")
        _prune(workspace_root)
        return _finish(scenario_dir, result, started)
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ
        result.notes.append(f"unhandled {type(exc).__name__}: {exc}")
        _prune(workspace_root)
        return _finish(scenario_dir, result, started)

    result.chdd_status = STATUS_PASS
    if not keep_workspace:
        _prune(workspace_root)
        (scenario_dir / "chdd_input.csv").unlink(missing_ok=True)
    return _finish(scenario_dir, result, started)


def run_exp004_batch(
    scenarios: Sequence[Exp004Scenario],
    case: Case,
    *,
    runtime_root: Path,
    pvt: WaterPvt,
    max_parallelism: int = MAX_PARALLELISM,
    on_result: Callable[[Exp004Result], None] | None = None,
) -> list[Exp004Result]:
    """Каждый прогон получает свой каталог; общих изменяемых файлов нет."""

    state, baseline, history_text = read_baseline_inputs(case)
    workers = max(1, min(max_parallelism, len(scenarios))) if scenarios else 1

    def task(scenario: Exp004Scenario) -> Exp004Result:
        result = run_exp004_scenario(
            scenario,
            case,
            runtime_root=runtime_root,
            state=state,
            baseline=baseline,
            history_text=history_text,
            pvt=pvt,
        )
        if on_result is not None:
            on_result(result)
        return result

    if workers == 1:
        return [task(s) for s in scenarios]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(task, scenarios))


def gate_summary(case: Case) -> dict[str, Any]:
    return {
        "producer_wlpr_max": case.constraints.producer_wlpr_max,
        "producer_bhp_min": case.constraints.producer_bhp_min,
        "injector_bhp_max": case.constraints.injector_bhp_max,
        "allow_producer_to_injector": case.constraints.allow_producer_to_injector,
        "gate_status_enum": [s.value for s in GateStatus],
    }
