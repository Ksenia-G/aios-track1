"""Одна команда, которая проходит весь контур и печатает итог."""

import argparse
import sys
from pathlib import Path

from aios.pipeline.validation import (
    VERDICT_READY,
    PipelineOutcome,
    run_validation_pipeline,
)
from aios.schedule.policy import DEFAULT_POLICY_ID, POLICIES

DEFAULT_CASE = Path("cases/model_y_validation.json")
DEFAULT_OUTPUT = Path("output")
DEFAULT_RUNTIME = Path("runtime")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="validation-demo",
        description="AIOS Track 1 -- сквозной прогон: кейс -> расписание -> OPM -> ЧДД.",
    )
    parser.add_argument("--case", type=Path, default=DEFAULT_CASE, help="файл кейса (JSON)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="каталог сабмишена")
    parser.add_argument(
        "--runtime", type=Path, default=DEFAULT_RUNTIME, help="каталог рабочих файлов прогона"
    )
    parser.add_argument(
        "--policy",
        choices=sorted(POLICIES),
        default=DEFAULT_POLICY_ID,
        help="политика управления",
    )
    return parser


def _status(flag: bool) -> str:
    return "PASS" if flag else "FAIL"


def print_summary(outcome: PipelineOutcome, *, stream=sys.stdout) -> None:
    report = outcome.report
    case_id = report.case.case_id if report.case else str(report.case_path)
    lines = [
        "",
        "AIOS TRACK 1 — VALIDATION DEMO",
        "",
        f"Case: {case_id}",
        f"Policy: {report.policy_id}",
        f"Schedule: {_status(report.schedule_valid)}",
        f"Constraints: {_status(report.all_hard_constraints_pass)}",
        f"OPM: {_status(report.simulation_pass)}",
        f"CHDD: {_status(report.chdd_pass)}",
        "",
    ]
    if report.npv is not None:
        provenance = report.chdd_result.provenance if report.chdd_result else "UNKNOWN"
        lines += [
            "Baseline NPV:",
            f"{report.npv:.6f} mln RUB",
            f"Provenance: {provenance}",
            "",
        ]
        if report.npv_difference is not None:
            lines += [f"Difference vs case reference: {report.npv_difference:+.9f} mln RUB", ""]
    else:
        lines += ["Baseline NPV:", "not produced", ""]

    if outcome.schedule_path.is_file():
        lines += ["Submission:", str(outcome.schedule_path.resolve()), ""]
    else:
        lines += ["Submission:", "not published — the run did not pass every gate", ""]
    if report.invalidated_artifacts:
        lines += [
            "Previous package invalidated at start: " + ", ".join(report.invalidated_artifacts),
            "",
        ]
    if report.blockers:
        lines += ["Blockers:"] + [f"  - {item}" for item in report.blockers] + [""]
    lines += ["VERDICT:", report.verdict, ""]
    print("\n".join(lines), file=stream)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    outcome = run_validation_pipeline(
        args.case,
        output_dir=args.output.expanduser().resolve(),
        runtime_dir=args.runtime.expanduser().resolve(),
        policy_id=args.policy,
    )
    print_summary(outcome)
    return 0 if outcome.verdict == VERDICT_READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
