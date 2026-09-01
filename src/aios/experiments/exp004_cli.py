"""Пошаговый поиск EXP-004.

Стадии запускаются по одной и намеренно: следующая сетка строится по
результатам предыдущей, а не заранее. Слепо выпущенные сотни прогонов
дают объём, а не знание, и стоят часов счёта.

Каждая стадия дописывает строки в общую таблицу и пишет свой JSON на
сценарий. Повторный запуск стадии перезаписывает её сценарии целиком:
устаревшее число не должно пережить неудачный повтор.
"""

from __future__ import annotations

import argparse
import csv
import json
import threading
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from aios.cases import load_case
from aios.experiments.exp003 import SupplementalInjection
from aios.experiments.exp004 import (
    Exp004Scenario,
    Family,
    assert_unique,
    baseline_scenario,
    exp003_winner_scenario,
    exp003_winner_transfers,
    injection_scale_scenarios,
    joint_scenarios,
    producer_uplift_scenarios,
)
from aios.experiments.exp004_runner import Exp004Result, run_exp004_batch
from aios.experiments.vrr import load_water_pvt

DEFAULT_CASE = Path("cases/model_y_validation.json")
DEFAULT_OUTPUT = Path("experiments/exp004")
DEFAULT_RUNTIME = Path("experiments/exp004/runtime")
PVT_INCLUDE = Path("INCLUDE/DemoSpe_002_2_pvt.inc")

SCENARIO_COLUMNS = (
    "scenario_id",
    "family",
    "label",
    "simulation_status",
    "constraints_status",
    "chdd_status",
    "schedule_sha256",
    "npv_mln_rub",
    "delta_npv_mln_rub",
    "delta_npv_pct",
    "oil_t",
    "delta_oil_t",
    "water_t",
    "delta_water_t",
    "liquid_t",
    "injection_m3",
    "delta_injection_m3",
    "vrr_mean",
    "vrr_min",
    "vrr_max",
    "vrr_cumulative",
    "fpr_mean_bar",
    "fpr_min_bar",
    "fpr_final_bar",
    "delta_fpr_mean_bar",
    "delta_fpr_final_bar",
    "producers_bhp_limited",
    "producers_target_limited",
    "max_realized_wlpr",
    "wlpr_violations",
    "injector_max_wbhp_bar",
    "injector_cap_bound_states",
    "pump_capex_m",
    "pump_events",
    "wall_seconds",
    "notes",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="exp004", description="EXP-004 staged search.")
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--parallelism", type=int, default=4)
    parser.add_argument("--stage", required=True)
    return parser


def _progress(stage: str, total: int) -> Any:
    lock = threading.Lock()
    done = [0]

    def report(result: Exp004Result) -> None:
        with lock:
            done[0] += 1
            npv = result.npv_mln_rub
            value = f"{npv:11.4f}" if npv is not None else "     FAILED"
            vrr = result.vrr or {}
            extra = ""
            if result.is_pass:
                extra = (
                    f" VRR {vrr.get('mean', 0.0):.3f}"
                    f" FPR {(result.pressure or {}).get('final_bar', 0.0):7.2f}"
                )
            print(
                f"  [{stage} {done[0]:>2}/{total}] {result.scenario.scenario_id:<34} "
                f"{value}{extra}  {result.wall_seconds:5.0f}s",
                flush=True,
            )

    return report


def _totals(result: Exp004Result) -> tuple[float | None, ...]:
    chdd = result.chdd_result
    if chdd is None:
        return None, None, None, None
    oil = chdd.oil_kt_total * 1000.0
    liquid = chdd.liquid_kt_total * 1000.0
    return oil, liquid, liquid - oil, chdd.injection_k_total * 1000.0


def build_row(result: Exp004Result, baseline: Exp004Result | None) -> dict[str, Any]:
    oil, liquid, water, injection = _totals(result)
    base_oil, _base_liquid, base_water, base_injection = (
        _totals(baseline) if baseline is not None else (None, None, None, None)
    )
    npv = result.npv_mln_rub
    base_npv = None if baseline is None else baseline.npv_mln_rub
    delta = None if npv is None or base_npv is None else npv - base_npv
    vrr = result.vrr or {}
    pressure = result.pressure or {}
    base_pressure = (baseline.pressure or {}) if baseline is not None else {}
    producers = result.producers or {}
    injectors = result.injectors or {}
    decomposition = result.decomposition or {}

    def diff(a: float | None, b: float | None) -> float | None:
        return None if a is None or b is None else a - b

    return {
        "scenario_id": result.scenario.scenario_id,
        "family": result.scenario.family.value,
        "label": result.scenario.label,
        "simulation_status": result.simulation_status,
        "constraints_status": result.constraints_status,
        "chdd_status": result.chdd_status,
        "schedule_sha256": result.schedule_sha256,
        "npv_mln_rub": npv,
        "delta_npv_mln_rub": delta,
        "delta_npv_pct": None if delta is None or not base_npv else delta / base_npv * 100.0,
        "oil_t": oil,
        "delta_oil_t": diff(oil, base_oil),
        "water_t": water,
        "delta_water_t": diff(water, base_water),
        "liquid_t": liquid,
        "injection_m3": injection,
        "delta_injection_m3": diff(injection, base_injection),
        "vrr_mean": vrr.get("mean"),
        "vrr_min": vrr.get("min"),
        "vrr_max": vrr.get("max"),
        "vrr_cumulative": (vrr.get("cumulative") or {}).get("cumulative_vrr"),
        "fpr_mean_bar": pressure.get("mean_bar"),
        "fpr_min_bar": pressure.get("min_bar"),
        "fpr_final_bar": pressure.get("final_bar"),
        "delta_fpr_mean_bar": diff(pressure.get("mean_bar"), base_pressure.get("mean_bar")),
        "delta_fpr_final_bar": diff(pressure.get("final_bar"), base_pressure.get("final_bar")),
        "producers_bhp_limited": producers.get("bhp_limited_count"),
        "producers_target_limited": producers.get("target_limited_count"),
        "max_realized_wlpr": producers.get("max_realized_wlpr"),
        "wlpr_violations": len(producers.get("wlpr_violations") or []),
        "injector_max_wbhp_bar": injectors.get("max_wbhp_bar"),
        "injector_cap_bound_states": injectors.get("cap_bound_states"),
        "pump_capex_m": decomposition.get("pump_capex_m"),
        "pump_events": None if result.chdd_result is None else result.chdd_result.pump_events,
        "wall_seconds": round(result.wall_seconds, 1),
        "notes": "; ".join(result.notes)[:300],
    }


def load_rows(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    with open(path, newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def save_rows(rows: Sequence[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with open(temp, "w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=list(SCENARIO_COLUMNS), extrasaction="ignore", lineterminator="\n"
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k) for k in SCENARIO_COLUMNS})
    temp.replace(path)


def merge_rows(existing: Sequence[dict[str, Any]], fresh: Sequence[dict[str, Any]]) -> list[dict]:
    """Свежие строки вытесняют одноимённые старые."""

    by_id = {row["scenario_id"]: dict(row) for row in existing}
    for row in fresh:
        by_id[row["scenario_id"]] = row
    return sorted(
        by_id.values(),
        key=lambda r: (
            -float(r["npv_mln_rub"]) if r.get("npv_mln_rub") not in (None, "") else 1e18,
            r["scenario_id"],
        ),
    )


def load_baseline_result(runtime: Path) -> Exp004Result | None:
    """Опорный прогон читается с диска: он нужен всем стадиям."""

    path = runtime / "EXP004_R_BASELINE" / "result.json"
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("status") != "PASS":
        return None
    result = Exp004Result(scenario=baseline_scenario())
    result.simulation_status = "PASS"
    result.constraints_status = "PASS"
    result.chdd_status = "PASS"
    result.schedule_sha256 = payload["schedule_sha256"]
    result.vrr = payload["vrr"]
    result.pressure = payload["pressure"]
    result.producers = payload["producers"]
    result.injectors = payload["injectors"]
    result.decomposition = payload["economic_decomposition"]

    from aios.chdd.runner import ChddResult

    economics = payload["economics"]
    result.chdd_result = ChddResult(
        calculator_version=economics["calculator_version"],
        provenance=economics["provenance"],
        npv_mln_rub=economics["npv_mln_rub"],
        profitability_index=economics["profitability_index"],
        economic_month_count=economics["economic_month_count"],
        months_by_year=economics["months_by_year"],
        start_date=economics["economic_start_date"],
        last_economic_month=economics["last_economic_month"],
        source_max_date=economics["source_max_date"],
        oil_kt_total=economics["oil_kt_total"],
        liquid_kt_total=economics["liquid_kt_total"],
        injection_k_total=economics["injection_k_total"],
        active_well_months=economics["active_well_months"],
        pump_events=economics["events"]["pump"],
        stop_events=economics["events"]["stop"],
        start_events=economics["events"]["start"],
        conversion_events=economics["events"]["conversion"],
        excluded_negative_rows=economics["excluded_negative_rows"],
        duplicate_well_months=economics["duplicate_well_months"],
        invalid_dates=economics["invalid_dates"],
        ignored_blank_wells=economics["ignored_blank_wells"],
        warnings=tuple(economics["warnings"]),
        raw={},
    )
    return result


def stage_scenarios(stage: str) -> tuple[Exp004Scenario, ...]:
    """Сетка стадии. Поздние стадии опираются на выводы ранних."""

    transfers = exp003_winner_transfers()
    supplement = (SupplementalInjection("42", 100.0),)

    if stage == "0":
        return (baseline_scenario(), exp003_winner_scenario())
    if stage == "1a":
        return injection_scale_scenarios((0.60, 0.80, 0.90, 1.10, 1.25, 1.50))
    if stage == "1b":
        return producer_uplift_scenarios((1.10, 1.25, 1.50, 2.00, 3.00, 5.00))
    if stage == "2":
        # Стадия 1a показала: закачка сама по себе выше 1.0 вредит, оптимум
        # около 0.90. Стадия 1b: подъём отбора работает и упирается около
        # x2. Совместная сетка проверяет, меняет ли рост отбора знак
        # полезности закачки -- воидажа теперь больше.
        return joint_scenarios((1.50, 2.00, 2.50, 3.00), (1.20, 1.50, 1.80))
    if stage == "3":
        # Стадия 2 перевернула знак полезности закачки: при удвоенном отборе
        # она уже не вредит, а решает, и на краю сетки (x1.8) рост не
        # закончился. Сетка расширяется вверх по закачке.
        return joint_scenarios((2.50, 3.00, 4.00), (2.20, 3.00, 4.00))
    if stage == "4":
        # Отбор упёрся в потолок 500 м3/сут (множитель x5 от опорных 100),
        # закачка -- в 300 бар. Уточняется промежуток по закачке и
        # проверяется, добавляют ли что-нибудь адресные направления
        # EXP-003 поверх насыщенного равномерного режима.
        grid = joint_scenarios((5.00,), (3.00, 4.00, 6.00))
        overlay = joint_scenarios(
            (5.00,),
            (5.00, 7.00),
            transfers=transfers,
            supplements=supplement,
            prefix="EXP004_C_OVL",
        )
        return (*grid, *overlay)
    if stage == "5":
        # Явно помеченная верхняя инженерная граница: предел ищется
        # ограничениями проекта, а не разрешением сетки.
        return joint_scenarios(
            (3.00, 5.00), (5.00, 7.00), family=Family.AGGRESSIVE, prefix="EXP004_D_AGGR"
        )
    raise ValueError(f"unknown stage {stage!r}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output = args.output.expanduser().resolve()
    runtime = args.runtime.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    runtime.mkdir(parents=True, exist_ok=True)

    case = load_case(args.case)
    pvt = load_water_pvt(case.history.deck_root / PVT_INCLUDE)
    scenarios = stage_scenarios(args.stage)
    assert_unique(scenarios)

    started = time.monotonic()
    print(f"EXP-004 stage {args.stage}: {len(scenarios)} scenarios", flush=True)
    results = run_exp004_batch(
        scenarios,
        case,
        runtime_root=runtime,
        pvt=pvt,
        max_parallelism=args.parallelism,
        on_result=_progress(args.stage, len(scenarios)),
    )

    baseline = load_baseline_result(runtime)
    rows = [build_row(r, baseline) for r in results]
    merged = merge_rows(load_rows(output / "scenarios.csv"), rows)
    save_rows(merged, output / "scenarios.csv")

    passed = sum(1 for r in results if r.is_pass)
    print(
        f"\nstage {args.stage}: {passed}/{len(results)} passed, "
        f"wall {time.monotonic() - started:.0f}s, table now {len(merged)} rows",
        flush=True,
    )
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
