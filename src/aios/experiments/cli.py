"""Запуск экрана EXP-001 одной командой."""

import argparse
import json
import threading
import time
from collections.abc import Callable
from pathlib import Path

from aios.cases import load_case
from aios.experiments.runner import ScenarioResult
from aios.experiments.scenarios import (
    FIRST_PASS_FRACTION,
    REFINEMENT_FRACTIONS,
    first_pass_scenarios,
    refinement_scenarios,
)
from aios.experiments.sweep import (
    BASELINE_JSON,
    COMPARISON_DIRNAME,
    DEFAULT_MAX_PARALLELISM,
    RESULTS_CSV,
    SUMMARY_JSON,
    BaselineMismatchError,
    ScenarioRow,
    best_per_block,
    build_row,
    build_summary,
    rank_rows,
    run_baseline,
    run_scenarios,
    write_comparison,
    write_results_csv,
)

DEFAULT_CASE = Path("cases/model_y_validation.json")
DEFAULT_OUTPUT = Path("experiments/exp001")
DEFAULT_RUNTIME = Path("experiments/exp001/runtime")

BASELINE_NPV_MLN_RUB = 1030.8776958837277
BASELINE_SCHEDULE_SHA256 = "7662d10495796ace6b0d20df404ca68932b2894f367de2062c232cbe3e01c399"
BASELINE_TOLERANCE = 1e-6

REFINEMENT_PAIR_COUNT = 5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp001",
        description="EXP-001 -- экран перераспределения закачки внутри скрининговых групп.",
    )
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--parallelism", type=int, default=DEFAULT_MAX_PARALLELISM)
    parser.add_argument("--skip-refinement", action="store_true", help="только первый проход F15")
    parser.add_argument(
        "--limit", type=int, default=0, help="ограничить число сценариев первого прохода (отладка)"
    )
    return parser


def _report_rows(rows: list[ScenarioRow], title: str, limit: int) -> list[str]:
    lines = [title, ""]
    header = f"{'scenario':<34} {'ΔNPV':>12} {'Δ%':>9} {'Δoil t':>12} {'Δinj m3':>13}"
    lines.append(header)
    for row in rows[:limit]:
        values = row.values
        if values["delta_npv_mln_rub"] is None:
            lines.append(f"{values['scenario_id']:<34} {'FAILED':>12}")
            continue
        lines.append(
            f"{values['scenario_id']:<34} "
            f"{values['delta_npv_mln_rub']:>12.6f} "
            f"{values['delta_npv_pct']:>8.4f}% "
            f"{values['delta_oil_t']:>12.1f} "
            f"{values['delta_injection_m3']:>13.1f}"
        )
    lines.append("")
    return lines


def _progress_reporter(total: int) -> "Callable[[ScenarioResult], None]":
    """Печатать завершённые сценарии по мере готовности."""

    lock = threading.Lock()
    done = [0]

    def report(result: ScenarioResult) -> None:
        with lock:
            done[0] += 1
            npv = result.npv_mln_rub
            value = f"{npv:.6f}" if npv is not None else "FAILED"
            print(
                f"  [{done[0]:>2}/{total}] {result.scenario.scenario_id:<34} "
                f"{value:>14}  {result.wall_seconds:6.1f}s",
                flush=True,
            )

    return report


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output.expanduser().resolve()
    runtime_root = args.runtime.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    runtime_root.mkdir(parents=True, exist_ok=True)

    case = load_case(args.case)
    started = time.monotonic()

    print("EXP-001 — baseline control run ...", flush=True)
    try:
        baseline = run_baseline(
            case,
            runtime_root=runtime_root,
            expected_npv=BASELINE_NPV_MLN_RUB,
            expected_schedule_sha256=BASELINE_SCHEDULE_SHA256,
            tolerance=BASELINE_TOLERANCE,
        )
    except BaselineMismatchError as exc:
        print(f"\nBASELINE DID NOT RECONCILE: {exc}\n\nVERDICT:\nEXP_001_BLOCKED\n")
        return 2
    print(
        f"  baseline NPV {baseline.npv_mln_rub!r}  sha {baseline.schedule_sha256[:16]}  "
        f"{baseline.wall_seconds:.1f}s",
        flush=True,
    )
    (output_dir / BASELINE_JSON).write_text(
        json.dumps(baseline.to_json(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    scenarios = list(first_pass_scenarios(FIRST_PASS_FRACTION))
    if args.limit:
        scenarios = scenarios[: args.limit]
    print(f"first pass: {len(scenarios)} scenarios at f={FIRST_PASS_FRACTION}", flush=True)
    results: list[ScenarioResult] = run_scenarios(
        scenarios,
        case,
        runtime_root=runtime_root,
        max_parallelism=args.parallelism,
        on_result=_progress_reporter(len(scenarios)),
    )

    rows = [build_row(result, baseline) for result in results]
    for result in results:
        write_comparison(result, baseline, output_dir=output_dir / COMPARISON_DIRNAME)

    ranked = rank_rows(rows)
    positive = [r for r in ranked if (r.values["delta_npv_mln_rub"] or 0.0) > 0.0]

    if positive and not args.skip_refinement:
        top_pairs = [
            (int(r.values["block"]), r.values["donor"], r.values["receiver"])
            for r in ranked
            if r.values["delta_npv_mln_rub"] is not None
        ][:REFINEMENT_PAIR_COUNT]
        extra = list(refinement_scenarios(top_pairs, REFINEMENT_FRACTIONS))
        print(f"refinement: {len(extra)} scenarios over {len(top_pairs)} pairs", flush=True)
        extra_results = run_scenarios(
            extra,
            case,
            runtime_root=runtime_root,
            max_parallelism=args.parallelism,
            on_result=_progress_reporter(len(extra)),
        )
        results.extend(extra_results)
        for result in extra_results:
            write_comparison(result, baseline, output_dir=output_dir / COMPARISON_DIRNAME)
        rows = [build_row(result, baseline) for result in results]
        ranked = rank_rows(rows)

    write_results_csv(ranked, output_dir / RESULTS_CSV)
    summary = build_summary(
        rows,
        baseline_npv=float(baseline.npv_mln_rub or 0.0),
        parallelism=args.parallelism,
        wall_seconds=time.monotonic() - started,
    )
    (output_dir / SUMMARY_JSON).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    lines = ["", "AIOS TRACK 1 — EXP-001 INJECTION REDISTRIBUTION SCREEN", ""]
    lines += [f"Baseline NPV: {summary['baseline_npv']:.6f} mln RUB", ""]
    lines += _report_rows(ranked, "TOP 10 by ΔNPV", 10)
    lines += ["Best per block:", ""]
    for block, row in sorted(best_per_block(rows).items()):
        lines.append(
            f"  block {block}: {row.values['scenario_id']:<34} "
            f"ΔNPV {row.values['delta_npv_mln_rub']:+.6f}"
        )
    lines += [
        "",
        (
            f"scenarios {summary['scenario_count']}  passed {summary['passed_count']}  "
            f"failed {summary['failed_count']}  positive {summary['positive_npv_count']}"
        ),
        f"parallelism {summary['parallelism']}  wall {summary['wall_seconds']:.0f}s",
        "",
        "VERDICT:",
        "EXP_001_POSITIVE_DELTA_NPV_FOUND"
        if summary["positive_npv_count"]
        else "NO_POSITIVE_F15_REDISTRIBUTION_FOUND",
        "",
    ]
    print("\n".join(lines))
    return 0 if summary["passed_count"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
