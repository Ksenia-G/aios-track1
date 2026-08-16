"""Оркестратор экрана EXP-001.

Порядок жёсткий: сначала контрольный прогон, и только если он сходится с
принятым baseline -- сам экран. Смысл в том, что все сравнения в этой работе
разностные, и разность имеет смысл лишь тогда, когда контроль воспроизводит
уже проверенное число.

Параллелизм ограничен и честно записывается в отчёт. Каждый прогон получает
свой рабочий каталог, поэтому два процесса физически не могут писать в один
и тот же кейс. Экономический расчёт при этом сериализуется: официальный
пакет подключается через `sys.path` и не рассчитан на одновременный вызов.
"""

import csv
import json
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from aios.cases.contract import Case
from aios.experiments.metrics import compare_metrics
from aios.experiments.runner import ScenarioResult, run_scenario
from aios.experiments.scenarios import Scenario, baseline_scenario
from aios.pipeline.validation import read_baseline_inputs

RESULTS_CSV = "results.csv"
SUMMARY_JSON = "summary.json"
BASELINE_JSON = "baseline.json"
COMPARISON_DIRNAME = "comparisons"

DEFAULT_MAX_PARALLELISM = 4

RESULT_COLUMNS = (
    "scenario_id",
    "block",
    "donor",
    "receiver",
    "fraction",
    "simulation_status",
    "constraints_status",
    "schedule_sha256",
    "npv_mln_rub",
    "delta_npv_mln_rub",
    "delta_npv_pct",
    "oil_t",
    "delta_oil_t",
    "liquid_t",
    "delta_liquid_t",
    "injection_m3",
    "delta_injection_m3",
    "wall_seconds",
    "notes",
)


class BaselineMismatchError(RuntimeError):
    """Контроль не воспроизвёл принятый baseline. Экран не запускается."""


@dataclass(frozen=True, slots=True)
class ScenarioRow:
    """Строка таблицы результатов."""

    values: dict[str, Any]

    @property
    def delta_npv(self) -> float:
        value = self.values["delta_npv_mln_rub"]
        return float("-inf") if value is None else float(value)


def _totals(result: ScenarioResult) -> tuple[float | None, float | None, float | None]:
    chdd = result.chdd_result
    if chdd is None:
        return None, None, None
    return (
        chdd.oil_kt_total * 1000.0,
        chdd.liquid_kt_total * 1000.0,
        chdd.injection_k_total * 1000.0,
    )


def build_row(result: ScenarioResult, baseline: ScenarioResult | None) -> ScenarioRow:
    scenario = result.scenario
    oil, liquid, injection = _totals(result)
    base_oil, base_liquid, base_injection = (
        _totals(baseline) if baseline is not None else (None, None, None)
    )
    npv = result.npv_mln_rub
    base_npv = None if baseline is None else baseline.npv_mln_rub

    delta_npv = None if npv is None or base_npv is None else npv - base_npv
    delta_pct = None if delta_npv is None or not base_npv else delta_npv / base_npv * 100.0
    return ScenarioRow(
        {
            "scenario_id": scenario.scenario_id,
            "block": scenario.block_id,
            "donor": scenario.donor,
            "receiver": scenario.receiver,
            "fraction": scenario.fraction,
            "simulation_status": result.simulation_status,
            "constraints_status": result.constraints_status,
            "schedule_sha256": result.schedule_sha256,
            "npv_mln_rub": npv,
            "delta_npv_mln_rub": delta_npv,
            "delta_npv_pct": delta_pct,
            "oil_t": oil,
            "delta_oil_t": None if oil is None or base_oil is None else oil - base_oil,
            "liquid_t": liquid,
            "delta_liquid_t": (
                None if liquid is None or base_liquid is None else liquid - base_liquid
            ),
            "injection_m3": injection,
            "delta_injection_m3": (
                None if injection is None or base_injection is None else injection - base_injection
            ),
            "wall_seconds": round(result.wall_seconds, 3),
            "notes": "; ".join(result.notes)[:500],
        }
    )


def rank_rows(rows: Sequence[ScenarioRow]) -> list[ScenarioRow]:
    """Упорядочить по ΔЧДД. Порядок детерминирован при равных значениях."""

    passed = [row for row in rows if row.values["delta_npv_mln_rub"] is not None]
    failed = [row for row in rows if row.values["delta_npv_mln_rub"] is None]
    passed.sort(key=lambda row: (-row.delta_npv, row.values["scenario_id"]))
    failed.sort(key=lambda row: row.values["scenario_id"])
    return [*passed, *failed]


def write_results_csv(rows: Sequence[ScenarioRow], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=RESULT_COLUMNS, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.values.get(key) for key in RESULT_COLUMNS})
    return path


def run_baseline(
    case: Case,
    *,
    runtime_root: Path,
    expected_npv: float,
    expected_schedule_sha256: str,
    tolerance: float,
) -> ScenarioResult:
    """Контрольный прогон и его сверка с принятым baseline."""

    state, baseline_schedule, history_text = read_baseline_inputs(case)
    result = run_scenario(
        baseline_scenario(),
        case,
        runtime_root=runtime_root,
        state=state,
        baseline=baseline_schedule,
        history_text=history_text,
        keep_workspace=True,
    )
    if not result.is_pass:
        raise BaselineMismatchError(f"baseline run did not pass: {'; '.join(result.notes)}")
    if result.schedule_sha256 != expected_schedule_sha256:
        raise BaselineMismatchError(
            f"baseline schedule SHA {result.schedule_sha256} != {expected_schedule_sha256}"
        )
    npv = result.npv_mln_rub
    if npv is None or abs(npv - expected_npv) > tolerance:
        raise BaselineMismatchError(f"baseline NPV {npv} != {expected_npv}")
    return result


def run_scenarios(
    scenarios: Sequence[Scenario],
    case: Case,
    *,
    runtime_root: Path,
    max_parallelism: int = DEFAULT_MAX_PARALLELISM,
    on_result: Callable[[ScenarioResult], None] | None = None,
) -> list[ScenarioResult]:
    """Провести набор сценариев с ограниченным параллелизмом.

    Каждый сценарий получает свой каталог, поэтому изоляция обеспечена самой
    адресацией, а не дисциплиной вызывающего кода.
    """

    state, baseline_schedule, history_text = read_baseline_inputs(case)
    workers = max(1, min(max_parallelism, len(scenarios))) if scenarios else 1

    def task(scenario: Scenario) -> ScenarioResult:
        result = run_scenario(
            scenario,
            case,
            runtime_root=runtime_root,
            state=state,
            baseline=baseline_schedule,
            history_text=history_text,
        )
        if on_result is not None:
            on_result(result)
        return result

    if workers == 1:
        return [task(scenario) for scenario in scenarios]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(task, scenarios))


def write_comparison(
    result: ScenarioResult, baseline: ScenarioResult, *, output_dir: Path
) -> Path | None:
    """Разности физики кандидата и контроля."""

    if result.metrics is None or baseline.metrics is None:
        return None
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{result.scenario.scenario_id}.json"
    path.write_text(
        json.dumps(compare_metrics(result.metrics, baseline.metrics), ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def build_summary(
    rows: Sequence[ScenarioRow],
    *,
    baseline_npv: float,
    parallelism: int,
    wall_seconds: float,
) -> dict[str, Any]:
    ranked = rank_rows(rows)
    passed = [row for row in ranked if row.values["npv_mln_rub"] is not None]
    failed = [row for row in ranked if row.values["npv_mln_rub"] is None]
    positive = [row for row in passed if (row.values["delta_npv_mln_rub"] or 0.0) > 0.0]
    best = passed[0] if passed else None
    return {
        "baseline_npv": baseline_npv,
        "scenario_count": len(ranked),
        "passed_count": len(passed),
        "failed_count": len(failed),
        "positive_npv_count": len(positive),
        "best_scenario": None if best is None else best.values["scenario_id"],
        "best_npv": None if best is None else best.values["npv_mln_rub"],
        "best_delta_npv": None if best is None else best.values["delta_npv_mln_rub"],
        "best_delta_pct": None if best is None else best.values["delta_npv_pct"],
        "parallelism": parallelism,
        "wall_seconds": round(wall_seconds, 1),
    }


def best_per_block(rows: Sequence[ScenarioRow]) -> dict[int, ScenarioRow]:
    best: dict[int, ScenarioRow] = {}
    for row in rank_rows(rows):
        if row.values["delta_npv_mln_rub"] is None:
            continue
        block = int(row.values["block"])
        if block not in best:
            best[block] = row
    return best
