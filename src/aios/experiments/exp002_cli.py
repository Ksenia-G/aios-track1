"""Command line entry point for EXP-002."""

from __future__ import annotations

import argparse
import json
import threading
import time
from pathlib import Path
from typing import Any

from aios.cases import load_case
from aios.experiments.exp002 import (
    BASELINE_NPV_MLN_RUB,
    BASELINE_SCHEDULE_SHA256,
    BASELINE_TOLERANCE,
    DEFAULT_EXP001_RESULTS,
    DEFAULT_OUTPUT,
    DEFAULT_RUNTIME,
    EXP001_WINNER_ID,
    EXP001_WINNER_NPV_MLN_RUB,
    EXP001_WINNER_SCHEDULE_SHA256,
    MAX_PARALLELISM,
    RESULT_COLUMNS,
    STAGE_A_COLUMNS,
    CandidateType,
    Exp002Result,
    apply_behavior_classes,
    best_transfer_specs,
    build_combinations,
    build_same_b3_combinations,
    decomposition_scenarios,
    exp001_rows_as_candidates,
    load_exp001_rows,
    rank_admissible,
    row_for_result,
    run_exp002_batch,
    stage_a_row,
    stage_a_scenarios,
    summary_payload,
    write_csv,
)
from aios.experiments.runner import ScenarioResult, run_scenario
from aios.experiments.scenarios import make_scenario
from aios.experiments.sweep import BaselineMismatchError, run_baseline
from aios.opm.summary import SummaryData, read_summary
from aios.pipeline.validation import read_baseline_inputs

DEFAULT_CASE = Path("cases/model_y_validation.json")
OUTPUT_FILES = (
    "results.csv",
    "summary.json",
    "stage_a.csv",
    "decomposition.csv",
    "combinations.csv",
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="exp002",
        description="EXP-002 boundary search, decomposition, and transfer combinations.",
    )
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--runtime", type=Path, default=DEFAULT_RUNTIME)
    parser.add_argument("--exp001-results", type=Path, default=DEFAULT_EXP001_RESULTS)
    parser.add_argument("--parallelism", type=int, default=MAX_PARALLELISM)
    parser.add_argument("--limit-stage-a", type=int, default=0, help="debug only")
    return parser


def _invalidate_outputs(output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    for name in OUTPUT_FILES:
        (output_dir / name).unlink(missing_ok=True)


def _baseline_summary(result: ScenarioResult) -> SummaryData:
    if result.opm_result is None:
        raise RuntimeError("baseline has no OPM result")
    output_dir = result.opm_result.log_path.parent / "OUT"
    smspec = sorted(output_dir.glob("*.SMSPEC"))
    unsmry = sorted(output_dir.glob("*.UNSMRY"))
    if len(smspec) != 1 or len(unsmry) != 1:
        raise RuntimeError(f"cannot identify baseline summary files in {output_dir}")
    return read_summary(smspec[0], unsmry[0])


def _progress(total: int) -> Any:
    lock = threading.Lock()
    done = [0]

    def report(result: Exp002Result) -> None:
        with lock:
            done[0] += 1
            npv = result.npv_mln_rub
            value = f"{npv:.6f}" if npv is not None else "FAILED"
            print(
                f"  [{done[0]:>2}/{total}] {result.scenario.scenario_id:<72} {value:>14} {result.wall_seconds:6.1f}s",
                flush=True,
            )

    return report


def _reproduce_exp001_winner(case: Any, runtime_root: Path) -> ScenarioResult:
    state, baseline, history_text = read_baseline_inputs(case)
    result = run_scenario(
        make_scenario(3, "32", "4", 0.30),
        case,
        runtime_root=runtime_root,
        state=state,
        baseline=baseline,
        history_text=history_text,
    )
    if not result.is_pass:
        raise RuntimeError(f"EXP-001 winner rerun failed: {'; '.join(result.notes)}")
    if result.schedule_sha256 != EXP001_WINNER_SCHEDULE_SHA256:
        raise RuntimeError(
            f"EXP-001 winner SHA {result.schedule_sha256} != {EXP001_WINNER_SCHEDULE_SHA256}"
        )
    if (
        result.npv_mln_rub is None
        or abs(result.npv_mln_rub - EXP001_WINNER_NPV_MLN_RUB) > BASELINE_TOLERANCE
    ):
        raise RuntimeError(
            f"EXP-001 winner NPV {result.npv_mln_rub} != {EXP001_WINNER_NPV_MLN_RUB}"
        )
    return result


def _public_simulation(opm_result: Any) -> dict[str, Any]:
    payload = opm_result.to_json()
    payload.pop("command", None)
    return payload


def _exp001_winner_public(result: ScenarioResult) -> dict[str, Any]:
    return {
        "scenario_id": result.scenario.scenario_id,
        "stage": "RECONCILIATION",
        "candidate_type": CandidateType.ADMISSIBLE.value,
        "transfers": json.dumps(
            [{"block": 3, "donor": "32", "receiver": "4", "fraction": 0.30, "mode": "TRANSFER"}],
            sort_keys=True,
        ),
        "simulation_status": result.simulation_status,
        "constraints_status": result.constraints_status,
        "chdd_status": result.chdd_status,
        "schedule_sha256": result.schedule_sha256,
        "npv_mln_rub": result.npv_mln_rub,
        "delta_npv_mln_rub": None
        if result.npv_mln_rub is None
        else result.npv_mln_rub - BASELINE_NPV_MLN_RUB,
        "delta_npv_pct": None
        if result.npv_mln_rub is None
        else (result.npv_mln_rub - BASELINE_NPV_MLN_RUB) / BASELINE_NPV_MLN_RUB * 100.0,
        "wall_seconds": round(result.wall_seconds, 3),
        "notes": "; ".join(result.notes)[:500],
    }


def _result_rows(results: list[Exp002Result], baseline_chdd: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        row = row_for_result(result, baseline_chdd)
        row["transfers_obj"] = result.scenario.transfers
        rows.append(row)
    return rows


def _stage_a_rows(results: list[Exp002Result], baseline_chdd: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        row = stage_a_row(result, baseline_chdd)
        row["transfers_obj"] = result.scenario.transfers
        rows.append(row)
    apply_behavior_classes(rows)
    return rows


def _scenario_ids_unique(scenarios: list[Any]) -> None:
    ids = [scenario.scenario_id for scenario in scenarios]
    if len(ids) != len(set(ids)):
        raise RuntimeError("duplicate EXP-002 scenario id")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    output_dir = args.output.expanduser().resolve()
    runtime_root = args.runtime.expanduser().resolve()
    exp001_path = args.exp001_results.expanduser().resolve()
    _invalidate_outputs(output_dir)
    runtime_root.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()

    case = load_case(args.case)
    exp001_raw = load_exp001_rows(exp001_path)
    exp001_candidates = exp001_rows_as_candidates(exp001_raw)

    print("EXP-002 reconciliation: baseline ...", flush=True)
    try:
        baseline = run_baseline(
            case,
            runtime_root=runtime_root,
            expected_npv=BASELINE_NPV_MLN_RUB,
            expected_schedule_sha256=BASELINE_SCHEDULE_SHA256,
            tolerance=BASELINE_TOLERANCE,
        )
        baseline_summary = _baseline_summary(baseline)
        print("EXP-002 reconciliation: EXP-001 winner rerun ...", flush=True)
        exp001_winner = _reproduce_exp001_winner(case, runtime_root)
    except (BaselineMismatchError, RuntimeError, ValueError) as exc:
        blocked = {
            "status": "BLOCKED",
            "reason": str(exc),
            "baseline_expected_sha": BASELINE_SCHEDULE_SHA256,
        }
        (output_dir / "summary.json").write_text(
            json.dumps(blocked, indent=2) + "\n", encoding="utf-8"
        )
        print(f"EXP-002 BLOCKED: {exc}")
        print("VERDICT:\nEXP_002_BLOCKED")
        return 2

    baseline_row = {
        "npv_mln_rub": baseline.npv_mln_rub,
        "schedule_sha256": baseline.schedule_sha256,
        "simulation": None
        if baseline.opm_result is None
        else _public_simulation(baseline.opm_result),
    }
    print(f"  baseline {baseline.npv_mln_rub:.12f} sha {baseline.schedule_sha256}", flush=True)
    print(
        f"  {EXP001_WINNER_ID} {exp001_winner.npv_mln_rub:.12f} sha {exp001_winner.schedule_sha256}",
        flush=True,
    )

    stage_a = list(stage_a_scenarios())
    if args.limit_stage_a:
        stage_a = stage_a[: args.limit_stage_a]
    _scenario_ids_unique(stage_a)
    print(f"Stage A: {len(stage_a)} upper-fraction candidates", flush=True)
    stage_a_results = run_exp002_batch(
        stage_a,
        case,
        runtime_root=runtime_root,
        baseline_metrics=baseline.metrics,
        baseline_summary=baseline_summary,
        max_parallelism=args.parallelism,
        on_result=_progress(len(stage_a)),
    )
    stage_a_rows = _stage_a_rows(stage_a_results, baseline.chdd_result)
    write_csv(output_dir / "stage_a.csv", stage_a_rows, STAGE_A_COLUMNS)

    single_candidates = [*exp001_candidates, *stage_a_rows]
    best_by_block, best_by_pair, individual_delta = best_transfer_specs(single_candidates)

    decomposition = list(decomposition_scenarios())
    print(f"Stage B: {len(decomposition)} decomposition candidates", flush=True)
    decomposition_results = run_exp002_batch(
        decomposition,
        case,
        runtime_root=runtime_root,
        baseline_metrics=baseline.metrics,
        baseline_summary=baseline_summary,
        max_parallelism=args.parallelism,
        on_result=_progress(len(decomposition)),
    )
    decomposition_rows = _result_rows(decomposition_results, baseline.chdd_result)
    write_csv(output_dir / "decomposition.csv", decomposition_rows, RESULT_COLUMNS)

    combinations = list(build_combinations(best_by_block)) + list(
        build_same_b3_combinations(best_by_pair)
    )
    combinations = list({scenario.scenario_id: scenario for scenario in combinations}.values())
    _scenario_ids_unique(combinations)
    print(f"Stage D: {len(combinations)} combination candidates", flush=True)
    combination_results = run_exp002_batch(
        combinations,
        case,
        runtime_root=runtime_root,
        baseline_metrics=baseline.metrics,
        baseline_summary=baseline_summary,
        max_parallelism=args.parallelism,
        on_result=_progress(len(combinations)),
    )
    combination_rows: list[dict[str, Any]] = []
    for result in combination_results:
        row = row_for_result(
            result, baseline.chdd_result, individual_delta_by_component=individual_delta
        )
        row["transfers_obj"] = result.scenario.transfers
        combination_rows.append(row)
    write_csv(output_dir / "combinations.csv", combination_rows, RESULT_COLUMNS)

    all_rows = [*exp001_candidates, *stage_a_rows, *decomposition_rows, *combination_rows]
    ranked = rank_admissible(all_rows)
    write_csv(
        output_dir / "results.csv",
        ranked + [row for row in all_rows if row not in ranked],
        RESULT_COLUMNS,
    )
    summary = summary_payload(
        baseline=baseline_row,
        exp001_winner=_exp001_winner_public(exp001_winner),
        stage_a_rows=stage_a_rows,
        decomposition_rows=decomposition_rows,
        combination_rows=combination_rows,
        all_rows=all_rows,
        parallelism=args.parallelism,
        wall_seconds=time.monotonic() - started,
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    best = summary["overall_best_verified_candidate"]
    verdict = "EXP_002_EXP001_WINNER_REMAINS_BEST"
    if (
        best
        and best["scenario_id"] != EXP001_WINNER_ID
        and float(best["delta_npv_mln_rub"]) > EXP001_WINNER_NPV_MLN_RUB - BASELINE_NPV_MLN_RUB
    ):
        verdict = "EXP_002_STRONGER_VERIFIED_CANDIDATE_FOUND"

    print("\nAIOS TRACK 1 EXP-002")
    print(f"baseline NPV {baseline.npv_mln_rub:.12f}")
    print(f"EXP-001 winner NPV {exp001_winner.npv_mln_rub:.12f}")
    if best:
        print(
            "overall best "
            f"{best['scenario_id']} NPV {float(best['npv_mln_rub']):.12f} "
            f"delta {float(best['delta_npv_mln_rub']):+.12f}"
        )
        print(f"schedule SHA {best['schedule_sha256']}")
    print(
        f"stage A {len(stage_a_rows)}  decomposition {len(decomposition_rows)}  combinations {len(combination_rows)}"
    )
    print(f"wall {time.monotonic() - started:.1f}s")
    print("VERDICT:")
    print(verdict)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
