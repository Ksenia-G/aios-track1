"""Прогон одного сценария экрана.

Используется уже проверенный стек VALIDATION-001: тот же генератор
расписания, те же ворота, тот же образ симулятора, тот же официальный
калькулятор и тот же строгий разбор его результата.

Отличается только адресация результата. Сценарий не публикует пакет
сабмишена: у эксперимента свой корень, свои артефакты и свои имена файлов.
Смешивать их с `output/` нельзя -- измерение экрана не является заявкой на
результат.

Каждый результат жёстко связан со своим прогоном: `scenario_id`, SHA
расписания, статус OPM и результат ЧДД лежат в одном файле и пишутся
только целиком. Провал стирает предыдущий результат сценария, поэтому
устаревшее число не может пережить неудачный повтор.
"""

import json
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.chdd.runner import ChddError, ChddResult, run_chdd
from aios.chdd.trajectory import TrajectoryError, build_chdd_input, write_chdd_csv
from aios.experiments.blocks import BLOCKS_BY_ID
from aios.experiments.gates import ExperimentGateReport, validate_scenario
from aios.experiments.metrics import RunMetrics, collect_metrics
from aios.experiments.scenarios import Scenario
from aios.opm.runner import OpmRunResult, SimulationError, run_simulation
from aios.opm.summary import SummaryReadError
from aios.opm.workspace import WorkspaceError, prepare_workspace
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.events import Schedule
from aios.schedule.generator import render_schedule, schedule_sha256
from aios.schedule.policy import BaselineContinuationPolicy
from aios.schedule.state import FieldState
from aios.schedule.validator import validate_schedule

RESULT_FILENAME = "result.json"
METRICS_FILENAME = "metrics.json"
SCHEDULE_FILENAME = "wells_schedule.inc"
LOG_TAIL_FILENAME = "flow_tail.log"

STATUS_PASS = "PASS"
STATUS_FAIL = "FAIL"

# Официальный калькулятор подключается через `sys.path` и кеш модулей, что
# делает его непригодным для одновременного вызова из нескольких потоков.
# Симуляция идёт параллельно, экономика -- по одному.
_CHDD_LOCK = threading.Lock()

_LOG_TAIL_LINES = 80


@dataclass(slots=True)
class ScenarioResult:
    """Итог одного сценария."""

    scenario: Scenario
    simulation_status: str = STATUS_FAIL
    constraints_status: str = STATUS_FAIL
    chdd_status: str = STATUS_FAIL
    schedule_sha256: str | None = None
    opm_result: OpmRunResult | None = None
    chdd_result: ChddResult | None = None
    metrics: RunMetrics | None = None
    gate_report: ExperimentGateReport | None = None
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
            "gates": None if self.gate_report is None else self.gate_report.to_json(),
            "notes": list(self.notes),
        }


def _observed_wells(scenario: Scenario) -> tuple[str, ...]:
    """Скважины, за которыми смотрим: добывающие блока плюс сама пара."""

    if scenario.is_baseline:
        wells = [w for block in BLOCKS_BY_ID.values() for w in (*block.producers, *block.injectors)]
    else:
        block = scenario.block
        wells = [*block.producers, *block.injectors]
    return tuple(dict.fromkeys(wells))


def _write_log_tail(source: Path, target: Path) -> None:
    if not source.is_file():
        return
    lines = source.read_text(encoding="utf-8", errors="replace").splitlines()
    interesting = [line for line in lines if "Error" in line or "error encountered" in line]
    tail = lines[-_LOG_TAIL_LINES:]
    target.write_text("\n".join([*interesting[:20], "...", *tail]) + "\n", encoding="utf-8")


def _prune_workspace(workspace_root: Path) -> None:
    """Убрать тяжёлые бинарники прогона, оставив лёгкие свидетельства."""

    if workspace_root.is_dir():
        shutil.rmtree(workspace_root, ignore_errors=True)


def build_candidate_schedule(
    scenario: Scenario, case: Case, *, state: FieldState, baseline: Schedule
) -> tuple[Schedule, str]:
    """Расписание кандидата и его текст."""

    policy = BaselineContinuationPolicy() if scenario.is_baseline else scenario.policy(case)
    schedule = policy.plan(state, baseline)
    text = render_schedule(schedule, case, policy_id=policy.policy_id)
    return schedule, text


def run_scenario(
    scenario: Scenario,
    case: Case,
    *,
    runtime_root: Path,
    state: FieldState,
    baseline: Schedule,
    history_text: str,
    keep_workspace: bool = False,
) -> ScenarioResult:
    """Провести сценарий целиком: расписание -> ворота -> OPM -> ЧДД -> метрики."""

    started = time.monotonic()
    result = ScenarioResult(scenario=scenario)
    scenario_dir = runtime_root / scenario.scenario_id
    if scenario_dir.exists():
        shutil.rmtree(scenario_dir)
    scenario_dir.mkdir(parents=True)

    try:
        schedule, schedule_text = build_candidate_schedule(
            scenario, case, state=state, baseline=baseline
        )
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ
        result.notes.append(f"schedule: {type(exc).__name__}: {exc}")
        result.wall_seconds = time.monotonic() - started
        _write_result(scenario_dir, result)
        return result

    result.schedule_sha256 = schedule_sha256(schedule_text)
    (scenario_dir / SCHEDULE_FILENAME).write_text(schedule_text, encoding="utf-8")

    base_report = validate_schedule(schedule, state, case)
    gate_report = validate_scenario(
        scenario,
        case=case,
        state=state,
        baseline=baseline,
        candidate=schedule,
        base_report=base_report,
    )
    result.gate_report = gate_report
    if not gate_report.is_valid:
        result.constraints_status = STATUS_FAIL
        result.notes.extend(f"gate {gate.gate_id}: {gate.detail}" for gate in gate_report.failed)
        result.wall_seconds = time.monotonic() - started
        _write_result(scenario_dir, result)
        return result
    result.constraints_status = STATUS_PASS

    workspace_root = scenario_dir / "workspace"
    try:
        workspace = prepare_workspace(
            case, root=workspace_root, history_text=history_text, schedule_text=schedule_text
        )
        opm_result, summary = run_simulation(case, workspace)
    except (WorkspaceError, SimulationError, SummaryReadError, OSError) as exc:
        result.notes.append(f"simulation: {exc}")
        result.wall_seconds = time.monotonic() - started
        _prune_workspace(workspace_root)
        _write_result(scenario_dir, result)
        return result
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ
        result.notes.append(f"unhandled {type(exc).__name__}: {exc}")
        result.wall_seconds = time.monotonic() - started
        _prune_workspace(workspace_root)
        _write_result(scenario_dir, result)
        return result

    result.opm_result = opm_result
    _write_log_tail(opm_result.log_path, scenario_dir / LOG_TAIL_FILENAME)
    if not opm_result.is_pass or summary is None:
        result.notes.extend(opm_result.errors[:5] or ["simulation produced no summary"])
        result.wall_seconds = time.monotonic() - started
        _prune_workspace(workspace_root)
        _write_result(scenario_dir, result)
        return result
    result.simulation_status = STATUS_PASS

    try:
        chdd_input = build_chdd_input(summary, case)
        csv_path = write_chdd_csv(chdd_input, scenario_dir / "chdd_input.csv")
        with _CHDD_LOCK:
            chdd_result = run_chdd(case, chdd_input, source_file=csv_path)
        result.metrics = collect_metrics(
            summary,
            wells=_observed_wells(scenario),
            window_start=case.management_start_date.isoformat(),
        )
    except (TrajectoryError, ChddError, SummaryReadError, OSError) as exc:
        result.notes.append(f"chdd: {exc}")
        result.wall_seconds = time.monotonic() - started
        _prune_workspace(workspace_root)
        _write_result(scenario_dir, result)
        return result
    except Exception as exc:  # noqa: BLE001 -- закрытый отказ
        result.notes.append(f"unhandled {type(exc).__name__}: {exc}")
        result.wall_seconds = time.monotonic() - started
        _prune_workspace(workspace_root)
        _write_result(scenario_dir, result)
        return result

    result.chdd_result = chdd_result
    result.chdd_status = STATUS_PASS
    result.wall_seconds = time.monotonic() - started

    if result.metrics is not None:
        (scenario_dir / METRICS_FILENAME).write_text(
            json.dumps(result.metrics.to_json(), ensure_ascii=False), encoding="utf-8"
        )
    if not keep_workspace:
        _prune_workspace(workspace_root)
        (scenario_dir / "chdd_input.csv").unlink(missing_ok=True)
    _write_result(scenario_dir, result)
    return result


def _write_result(scenario_dir: Path, result: ScenarioResult) -> None:
    """Записать итог сценария целиком.

    Файл переписывается всегда: провал повтора обязан затереть предыдущее
    число, а не оставить его рядом как «последний известный результат».
    """

    (scenario_dir / RESULT_FILENAME).write_text(
        json.dumps(result.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def load_case_inputs(case: Case) -> tuple[FieldState, Schedule, str]:
    return read_baseline_inputs(case)
