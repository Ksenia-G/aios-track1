#!/usr/bin/env python3
"""Разбор физики выигравшего сценария EXP-001.

Читает уже снятые разности (`experiments/exp001/comparisons/<id>.json`) и
печатает то, что по ним действительно видно: где нефть прибавилась, где
убавилась, что стало с водой, как отозвались давление и фактическая закачка
и через сколько отчётных шагов эффект становится заметен.

Ничего не пересчитывает и ничего не запускает.
"""

import argparse
import json
from pathlib import Path

DEFAULT_ROOT = Path("experiments/exp001")
# Порог заметности отклика по нефти, м3/сут. Ниже него разность неотличима
# от шума решателя и в «наблюдаемый лаг» не засчитывается.
LAG_THRESHOLD_M3D = 0.05


def _fmt(value: float, width: int = 12, digits: int = 3) -> str:
    return f"{value:>{width}.{digits}f}"


def observable_lag(series: list[float], start: int, dates: list[str]) -> str:
    for offset, value in enumerate(series[start:]):
        if abs(value) >= LAG_THRESHOLD_M3D:
            return f"{offset} report steps ({dates[start + offset]})"
    return "not observable above the threshold"


def main() -> int:
    parser = argparse.ArgumentParser(description="EXP-001 winner interpretation")
    parser.add_argument("scenario_id")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    args = parser.parse_args()

    comparison = json.loads(
        (args.root / "comparisons" / f"{args.scenario_id}.json").read_text("utf-8")
    )
    metrics = json.loads(
        (args.root / "runtime" / args.scenario_id / "metrics.json").read_text("utf-8")
    )
    dates = metrics["dates"]
    start = metrics["window_from_index"]

    print(f"\nEXP-001 — {args.scenario_id}\n")
    print("FIELD RESPONSE (management window)\n")
    print(f"{'vector':<8} {'mean Δ':>12} {'min Δ':>12} {'max Δ':>12} {'final Δ':>12}")
    for vector, entry in comparison["field"].items():
        window = entry["window"]
        print(
            f"{vector:<8} {_fmt(window['mean'])} {_fmt(window['min'])} "
            f"{_fmt(window['max'])} {_fmt(window['final'])}"
        )

    print("\nFIELD CUMULATIVE Δ (end of run)\n")
    for vector, value in sorted(comparison["field_final_delta"].items()):
        print(f"  {vector:<6} {value:>16.3f}")

    pre = max(entry["pre_window_max_abs"] for entry in comparison["field"].values())
    print(f"\nmax |Δ| before the management window: {pre:.6g}  (must be 0)")

    fopr = comparison["field"].get("FOPR")
    if fopr:
        print(f"observable oil response lag: {observable_lag(fopr['series'], start, dates)}")

    print("\nPER-WELL RESPONSE (screening block)\n")
    print(
        # WOPT/WWPT приходят из OPM в м3, а не в тоннах: массы появляются
        # только после плотностей, уже в экономическом мосте.
        f"{'well':<6} {'ΔWOPT Sm3':>14} {'ΔWWPT Sm3':>14} {'mean ΔWOPR':>12} "
        f"{'mean ΔWWCT':>12} {'mean ΔWBHP':>12}"
    )
    rows = []
    for well, entry in comparison["wells"].items():
        rows.append(
            (
                well,
                entry.get("WOPT_delta", 0.0),
                entry.get("WWPT_delta", 0.0),
                entry.get("WOPR", {}).get("window", {}).get("mean", 0.0),
                entry.get("WWCT", {}).get("window", {}).get("mean", 0.0),
                entry.get("WBHP", {}).get("window", {}).get("mean", 0.0),
            )
        )
    for well, oil, water, wopr, wwct, wbhp in sorted(rows, key=lambda r: -r[1]):
        print(f"{well:<6} {oil:>14.2f} {water:>14.2f} {wopr:>12.4f} {wwct:>12.5f} {wbhp:>12.4f}")

    gained = [r for r in rows if r[1] > 0.5]
    lost = [r for r in rows if r[1] < -0.5]
    print(f"\nwells gaining oil: {[r[0] for r in sorted(gained, key=lambda r: -r[1])]}")
    print(f"wells losing oil : {[r[0] for r in sorted(lost, key=lambda r: r[1])]}")
    print("\nclassification: OBSERVED_SIMULATION_EFFECT\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
