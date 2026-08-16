"""Прогон одного сценария EXP-003.

Стек тот же, что прошёл QG в VALIDATION-001 и EXP-002: тот же генератор,
те же ворота Track 1, тот же образ симулятора, тот же официальный
калькулятор и тот же строгий разбор его результата. Новое здесь только то,
что снимается сверх этого: водный учёт, профиль давления, декомпозиция
экономики и запас по забойному давлению нагнетательных скважин.
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
from aios.experiments.exp003 import (
    Exp003Scenario,
    WaterAccounting,
    build_exp003_schedule,
    economic_decomposition,
    injector_saturation_table,
    observed_wells,
    pressure_profile,
    validate_exp003_candidate,
    water_accounting,
)
from aios.experiments.metrics import RunMetrics, collect_metrics, compare_metrics
from aios.experiments.runner import LOG_TAIL_FILENAME, _write_log_tail
from aios.opm.runner import OpmRunResult, SimulationError, run_simulation
from aios.opm.summary import SummaryData, SummaryReadError
from aios.opm.workspace import WorkspaceError, prepare_workspace
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.events import Schedule
from aios.schedule.generator import schedule_sha256
from aios.schedule.state import FieldState

MAX_PARALLELISM = 4
RESULT_FILENAME = "result.json"
SCHEDULE_FILENAME = "wells_schedule.inc"

# Официальный калькулятор подключается через sys.path и кеш модулей, поэтому
# одновременный вызов из нескольких потоков недопустим. Симуляция идёт
# параллельно, экономика -- по одному.
_CHDD_LOCK = threading.Lock()


@dataclass(slots=True)
class Exp003Result:
    """Итог одного сценария EXP-003."""

    scenario: Exp003Scenario
    simulation_status: str = STATUS_FAIL
    constraints_status: str = STATUS_FAIL
    chdd_status: str = STATUS_FAIL
    schedule_sha256: str | None = None
    opm_result: OpmRunResult | None = None
    chdd_result: ChddResult | None = None
    metrics: RunMetrics | None = None
    metric_delta: dict[str, Any] | None = None
    gate_report: dict[str, Any] | None = None
    water: WaterAccounting | None = None
    pressure: dict[str, float] | None = None
    decomposition: dict[str, float] | None = None
    saturation: dict[str, dict[str, Any]] | None = None
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
            "economic_decomposition": self.decomposition,
            "water_accounting": None if self.water is None else self.water.to_json(),
            "pressure": self.pressure,
            "injector_saturation": self.saturation,
            "gates": self.gate_report,
            "metric_delta": self.metric_delta,
            "notes": list(self.notes),
        }


def _prune(workspace_root: Path) -> None:
    if workspace_root.is_dir():
        shutil.rmtree(workspace_root, ignore_errors=True)


def _finish(scenario_dir: Path, result: Exp003Result, started: float) -> Exp003Result:
    """Записать итог целиком.

    Файл переписывается всегда: провал повтора обязан затереть предыдущее
    число, а не оставить его рядом как «последний известный результат».
    """

    result.wall_seconds = time.monotonic() - started
    temp = scenario_dir / (RESULT_FILENAME + ".tmp")
    temp.write_text(
        json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temp.replace(scenario_dir / RESULT_FILENAME)
    return result


def run_exp003_scenario(
    scenario: Exp003Scenario,
    case: Case,
    *,
    runtime_root: Path,
    state: FieldState,
    baseline: Schedule,
    history_text: str,
    baseline_metrics: RunMetrics | None = None,
    injector_wells: Sequence[str] = (),
    keep_workspace: bool = False,
) -> Exp003Result:
    started = time.monotonic()
    result = Exp003Result(scenario=scenario)
    scenario_dir = runtime_root / scenario.scenario_id
    if scenario_dir.exists():
        shutil.rmtree(scenario_dir)
    scenario_dir.mkdir(parents=True)

    try:
        schedule, schedule_text = build_exp003_schedule(
            scenario, case, state=state, baseline=baseline
        )
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ
        result.notes.append(f"schedule: {type(exc).__name__}: {exc}")
        return _finish(scenario_dir, result, started)

    result.schedule_sha256 = schedule_sha256(schedule_text)
    (scenario_dir / SCHEDULE_FILENAME).write_text(schedule_text, encoding="utf-8")

    gate_report = validate_exp003_candidate(
        scenario, case, state=state, baseline=baseline, candidate=schedule
    )
    result.gate_report = gate_report
    if not gate_report["is_valid"]:
        result.notes.extend(
            f"gate {gate['gate_id']}: {gate['detail']}" for gate in gate_report["failed"]
        )
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
        result.metrics = collect_metrics(
            summary, wells=observed_wells(scenario), window_start=window_start
        )
        if baseline_metrics is not None:
            result.metric_delta = compare_metrics(result.metrics, baseline_metrics)
        result.water = water_accounting(summary, window_start)
        result.pressure = pressure_profile(summary, window_start)
        result.decomposition = economic_decomposition(result.chdd_result)
        if injector_wells:
            result.saturation = injector_saturation_table(
                summary, case, injector_wells, window_start
            )
    except (TrajectoryError, ChddError, SummaryReadError, OSError) as exc:
        result.notes.append(f"chdd: {exc}")
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


def run_exp003_batch(
    scenarios: Sequence[Exp003Scenario],
    case: Case,
    *,
    runtime_root: Path,
    baseline_metrics: RunMetrics | None,
    injector_wells: Sequence[str] = (),
    max_parallelism: int = MAX_PARALLELISM,
    on_result: Callable[[Exp003Result], None] | None = None,
) -> list[Exp003Result]:
    """Провести набор сценариев с ограниченным параллелизмом.

    Каждый прогон получает свой каталог, поэтому два процесса физически не
    могут писать в один и тот же кейс.
    """

    state, baseline, history_text = read_baseline_inputs(case)
    workers = max(1, min(max_parallelism, len(scenarios))) if scenarios else 1

    def task(scenario: Exp003Scenario) -> Exp003Result:
        result = run_exp003_scenario(
            scenario,
            case,
            runtime_root=runtime_root,
            state=state,
            baseline=baseline,
            history_text=history_text,
            baseline_metrics=baseline_metrics,
            injector_wells=injector_wells,
        )
        if on_result is not None:
            on_result(result)
        return result

    if workers == 1:
        return [task(scenario) for scenario in scenarios]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(task, scenarios))


def summary_from_result(result: Exp003Result) -> SummaryData:
    """Перечитать сводку удержанного прогона (для контроля)."""

    if result.opm_result is None:
        raise RuntimeError("result has no OPM run")
    from aios.opm.summary import read_summary

    output_dir = result.opm_result.log_path.parent / "OUT"
    smspec = sorted(output_dir.glob("*.SMSPEC"))
    unsmry = sorted(output_dir.glob("*.UNSMRY"))
    if len(smspec) != 1 or len(unsmry) != 1:
        raise RuntimeError(f"cannot identify summary files in {output_dir}")
    return read_summary(smspec[0], unsmry[0])
