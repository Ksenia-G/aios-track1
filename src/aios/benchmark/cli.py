"""Единая точка запуска бенчмарка MAS v1.1.

Один вход на все варианты: физическая и экономическая машинерия у них общая, и
разводить их по отдельным скриптам значило бы разрешить им незаметно разойтись.
Редактировать исходники для запуска не требуется.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from aios.benchmark.adapters import BenchmarkContext
from aios.benchmark.contracts import BenchmarkOutcome, CompensationPolicy, VariantFamily
from aios.benchmark.provider import detect_provider
from aios.benchmark.scoreboard import Scoreboard
from aios.benchmark.variants import (
    build_baseline,
    build_exp003_best,
    build_exp004_best,
    historical_variant,
    recorded_baseline,
    recorded_exp003,
    recorded_exp004,
    run_reference_variant,
)

ALL_VARIANTS = ("baseline", "exp003", "exp004", "mas-off", "mas-on")
DEFAULT_REFERENCE = "baseline"


def _code_sha(repo_root: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _context(args: argparse.Namespace, repo_root: Path) -> BenchmarkContext:
    from aios.cases import load_case

    case_path = Path(args.case)
    return BenchmarkContext(
        case=load_case(case_path),
        case_path=case_path,
        code_sha=_code_sha(repo_root),
        workspace_root=Path(args.workspace_root),
        deck_reference=args.deck_reference,
        compensation_policy=CompensationPolicy(args.compensation_policy),
    )


def _initial_state_card(context: BenchmarkContext):
    """Стартовое состояние для замкнутого контура.

    Берётся из опорного расписания того же кейса, чтобы MAS начинал ровно с
    той же точки, что и варианты с фиксированным расписанием.

    `evaluate_schedule_text` прогоняет ПОЛНУЮ историю плюс опорное расписание
    окна, поэтому её сводка кончается в H, а не в T0. Без явного `step=` here
    `StateExtractor` брал бы последний отчётный шаг -- состояние на конец
    горизонта -- и выдавал бы его специалистам за состояние решения на T0:
    решение "на T0" читало бы будущее. Отчётный шаг ищется той же самой
    date->step функцией, которой уже находит свою эпоху середина контура
    (`aios.mas.orchestration.report_step_for`), а не отдельным magic index.
    """
    from aios.benchmark.adapters import evaluate_schedule_text
    from aios.mas.orchestration import report_step_for
    from aios.state.extractor import StateExtractor, well_controls_from_schedule

    schedule, schedule_text = build_baseline(context)
    evaluation = evaluate_schedule_text(
        context, schedule_text=schedule_text, schedule=schedule, run_label="initial-state"
    )
    state, _baseline, _history, _start = context.baseline_inputs()
    controls = well_controls_from_schedule(
        state, schedule, at_date=context.case.management_start_date
    )
    return StateExtractor().extract(
        summary=evaluation.summary,
        case=context.case,
        run_id="benchmark-initial-state",
        simulator_digest=context.case.simulator.docker_image,
        schedule_hash=evaluation.schedule_sha,
        well_controls=controls,
        geology=context.geology(),
        step=report_step_for(evaluation.summary, context.case.management_start_date),
    )


def run_variants(args: argparse.Namespace, repo_root: Path) -> list[BenchmarkOutcome]:
    context = _context(args, repo_root)
    requested = [name.strip() for name in args.variants.split(",") if name.strip()]
    outcomes: list[BenchmarkOutcome] = []

    if "baseline" in requested:
        outcomes.append(
            run_reference_variant(
                context,
                variant_id="B0_BASELINE",
                family=VariantFamily.BASELINE,
                recorded=recorded_baseline(),
                builder=build_baseline,
            )
        )

    if "exp003" in requested:
        outcomes.append(
            run_reference_variant(
                context,
                variant_id="B1_EXP003",
                family=VariantFamily.EXP003,
                recorded=recorded_exp003(),
                builder=build_exp003_best,
            )
        )
        outcomes.append(
            historical_variant(
                context,
                variant_id="B1_EXP003_HISTORICAL",
                family=VariantFamily.EXP003,
                recorded=recorded_exp003(),
                reason=(
                    "recorded before render_full_schedule changed supplemental-injection "
                    "rendering; kept as an ingested reference, never as a fresh run"
                ),
            )
        )

    if "exp004" in requested:
        outcomes.append(
            run_reference_variant(
                context,
                variant_id="B2_EXP004",
                family=VariantFamily.EXP004,
                recorded=recorded_exp004(),
                builder=build_exp004_best,
            )
        )

    needs_mas = "mas-off" in requested or "mas-on" in requested
    initial_state = _initial_state_card(context) if needs_mas else None

    if "mas-off" in requested:
        from aios.benchmark.mas_variant import run_mas_variant

        outcomes.append(
            run_mas_variant(
                context,
                variant_id="B3_MAS_OFF",
                llm_enabled=False,
                initial_state_card=initial_state,
                simulation_budget=args.simulation_budget,
                decision_steps=args.decision_steps,
            )
        )

    if "mas-on" in requested:
        from aios.benchmark.mas_variant import run_mas_variant

        availability = detect_provider()
        if not availability.available:
            print(
                f"MAS_ON_BLOCKED_BY_APPROVED_PROVIDER_CREDENTIAL: {availability.reason}",
                file=sys.stderr,
            )
        else:
            from aios.benchmark.provider import build_live_providers

            production_provider, injection_provider, provider_config = build_live_providers()
            for index in range(args.repeats):
                outcomes.append(
                    run_mas_variant(
                        context,
                        variant_id="B4_MAS_ON",
                        llm_enabled=True,
                        initial_state_card=initial_state,
                        provider_id=availability.provider_id,
                        model_id=availability.model_id,
                        run_id=f"run-{index + 1}",
                        production_provider=production_provider,
                        injection_provider=injection_provider,
                        notes=(f"provider_config_digest={provider_config.digest()}",),
                        simulation_budget=args.simulation_budget,
                        decision_steps=args.decision_steps,
                    )
                )
    return outcomes


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m aios.benchmark",
        description="Reproducible MAS v1.1 economic benchmark.",
    )
    parser.add_argument("--suite", default="mas-v1.1")
    parser.add_argument(
        "--variants",
        default=",".join(ALL_VARIANTS),
        help="Comma separated: " + ",".join(ALL_VARIANTS),
    )
    parser.add_argument("--case", default="cases/model_y_validation.json")
    parser.add_argument("--deck-reference", default="${MODEL_Y_ROOT}")
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--output", default="benchmark-results")
    parser.add_argument("--reference-variant", default="B0_BASELINE")
    parser.add_argument("--simulation-budget", type=int, default=14)
    parser.add_argument("--decision-steps", type=int, default=1)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--compensation-policy",
        default=str(CompensationPolicy.OFFICIAL_UNKNOWN_PORT),
        choices=[str(item) for item in CompensationPolicy],
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Resolve variants and provider state without running any simulation.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(__file__).resolve().parents[3]

    if args.validate_only:
        availability = detect_provider()
        print(json.dumps({"provider": availability.to_json()}, ensure_ascii=False, indent=2))
        return 0

    outcomes = run_variants(args, repo_root)
    if not outcomes:
        print("no variants produced an outcome", file=sys.stderr)
        return 1

    reference = args.reference_variant
    if all(outcome.variant_id != reference for outcome in outcomes):
        reference = outcomes[0].variant_id
    scoreboard = Scoreboard(outcomes=tuple(outcomes), reference_variant_id=reference)

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "suite": args.suite,
        "provider": detect_provider().to_json(),
        "outcomes": [outcome.to_json() for outcome in outcomes],
        "scoreboard": scoreboard.to_json(),
    }
    target = output_dir / "benchmark_summary.json"
    target.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"benchmark summary written to {target}")
    return 0


__all__ = ["ALL_VARIANTS", "build_parser", "main", "run_variants"]
