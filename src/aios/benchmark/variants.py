"""Реестр вариантов бенчмарка и их происхождение в репозитории.

Числа сюда не вписываются руками: каждый вариант либо пересобирается из своего
сценария текущим кодом, либо честно помечается как историческая ссылка. Ровно
поэтому расхождение с записанным дайджестом расписания обнаруживается, а не
маскируется.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from aios.benchmark.adapters import (
    BenchmarkContext,
    ingest_historical_reference,
    run_fixed_schedule_variant,
)
from aios.benchmark.contracts import BenchmarkOutcome, VariantFamily
from aios.schedule.generator import schedule_sha256

REPO_ROOT = Path(__file__).resolve().parents[3]
EXP001_BASELINE = REPO_ROOT / "experiments" / "exp001" / "baseline.json"
EXP003_SUMMARY = REPO_ROOT / "experiments" / "exp003" / "summary.json"
EXP004_BEST = REPO_ROOT / "experiments" / "exp004" / "best_candidate.json"


@dataclass(frozen=True, slots=True)
class RecordedReference:
    """Записанный исторический результат варианта."""

    scenario_id: str
    schedule_sha256: str
    npv_mln_rub: float
    source: str


def recorded_baseline() -> RecordedReference:
    payload = json.loads(EXP001_BASELINE.read_text(encoding="utf-8"))
    return RecordedReference(
        scenario_id=payload["scenario"]["scenario_id"],
        schedule_sha256=payload["schedule_sha256"],
        npv_mln_rub=float(payload["economics"]["npv_mln_rub"]),
        source="experiments/exp001/baseline.json",
    )


def recorded_exp003() -> RecordedReference:
    payload = json.loads(EXP003_SUMMARY.read_text(encoding="utf-8"))
    best = payload["key_candidates"]["MAX_NPV"]
    return RecordedReference(
        scenario_id=best["scenario_id"],
        schedule_sha256=best["schedule_sha256"],
        npv_mln_rub=float(best["npv_mln_rub"]),
        source="experiments/exp003/summary.json#key_candidates.MAX_NPV",
    )


def recorded_exp004() -> RecordedReference:
    payload = json.loads(EXP004_BEST.read_text(encoding="utf-8"))
    return RecordedReference(
        scenario_id=payload["scenario_id"],
        schedule_sha256=payload["schedule_sha256"],
        npv_mln_rub=float(payload["npv_mln_rub"]),
        source="experiments/exp004/best_candidate.json",
    )


def _built(context: BenchmarkContext, builder: Callable, scenario):
    state, baseline, _history, _start = context.baseline_inputs()
    return builder(scenario, context.case, state=state, baseline=baseline)


def build_baseline(context: BenchmarkContext) -> tuple[object, str]:
    from aios.experiments.exp004 import baseline_scenario, build_exp004_schedule

    return _built(context, build_exp004_schedule, baseline_scenario())


def build_exp003_best(context: BenchmarkContext) -> tuple[object, str]:
    from aios.experiments.exp003 import (
        build_exp003_schedule,
        compensation_scenarios,
        exp002_winner_transfers,
    )

    recorded = recorded_exp003()
    for scenario in compensation_scenarios(exp002_winner_transfers()):
        if scenario.scenario_id == recorded.scenario_id:
            return _built(context, build_exp003_schedule, scenario)
    raise LookupError(f"EXP003 scenario {recorded.scenario_id!r} is not constructible")


def build_exp004_best(context: BenchmarkContext) -> tuple[object, str]:
    from aios.experiments.exp004 import build_exp004_schedule, joint_scenarios

    recorded = recorded_exp004()
    controls = json.loads(EXP004_BEST.read_text(encoding="utf-8"))["controls"]
    factor_p = float(controls["producer_uplift"]["factor"])
    factor_i = float(controls["injection_scale"]["factor"])
    for scenario in joint_scenarios((factor_p,), (factor_i,)):
        if scenario.scenario_id == recorded.scenario_id:
            return _built(context, build_exp004_schedule, scenario)
    raise LookupError(f"EXP004 scenario {recorded.scenario_id!r} is not constructible")


def run_reference_variant(
    context: BenchmarkContext,
    *,
    variant_id: str,
    family: VariantFamily,
    recorded: RecordedReference,
    builder: Callable[[BenchmarkContext], tuple[object, str]],
) -> BenchmarkOutcome:
    """Пересобрать вариант текущим кодом и сверить с записанным дайджестом.

    Совпал дайджест -- это тот же самый кандидат, и его можно оценивать заново.
    Не совпал -- текущий код строит другое расписание, и историческое число
    остаётся ссылкой: подменять одно другим значило бы сравнивать разные вещи
    под одним именем.
    """
    schedule, schedule_text = builder(context)
    rebuilt_sha = schedule_sha256(schedule_text)
    reproduced = rebuilt_sha == recorded.schedule_sha256

    notes = [f"recorded_schedule_sha256={recorded.schedule_sha256}"]
    if reproduced:
        notes.append("schedule reproduced bit-exactly from the recorded scenario")
    else:
        notes.append(
            f"schedule does NOT reproduce (rebuilt {rebuilt_sha}); evaluated as a "
            "re-derived candidate under current code, not as the historical artifact"
        )
    notes.append(f"recorded_npv_mln_rub={recorded.npv_mln_rub}")

    return run_fixed_schedule_variant(
        context,
        variant_id=variant_id,
        family=family,
        schedule=schedule,
        schedule_text=schedule_text,
        source_reference=recorded.source,
        notes=tuple(notes),
    )


def historical_variant(
    context: BenchmarkContext,
    *,
    variant_id: str,
    family: VariantFamily,
    recorded: RecordedReference,
    reason: str,
) -> BenchmarkOutcome:
    return ingest_historical_reference(
        context,
        variant_id=variant_id,
        family=family,
        npv_mln_rub=recorded.npv_mln_rub,
        schedule_digest=recorded.schedule_sha256,
        source_reference=recorded.source,
        notes=(reason,),
    )


__all__ = [
    "RecordedReference",
    "build_baseline",
    "build_exp003_best",
    "build_exp004_best",
    "historical_variant",
    "recorded_baseline",
    "recorded_exp003",
    "recorded_exp004",
    "run_reference_variant",
]
