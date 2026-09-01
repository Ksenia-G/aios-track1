"""Адаптеры вариантов бенчмарка к общей физической и экономической оценке.

Все варианты проходят через одни и те же Guardian и CHDD и через один и тот же
симулятор: иначе сравнение измеряло бы разницу инструментов, а не разницу
решений. Различается ровно одно -- откуда берутся управления:

* фиксированное расписание (baseline, EXP003, EXP004) рендерится один раз и
  оценивается на всём горизонте;
* замкнутый контур MAS принимает решения по шагам и порождает расписание сам.

Историческая запись, которую нельзя воспроизвести текущим кодом, остаётся в
бенчмарке как ссылка и помечается соответствующим родом прогона, а не выдаётся
за свежий результат.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from aios.agents.constraint_guardian import CONSTRAINT_GUARDIAN_VERSION, ConstraintGuardian
from aios.benchmark.contracts import (
    BENCHMARK_SCHEMA_VERSION,
    BenchmarkError,
    BenchmarkOutcome,
    BenchmarkRunManifest,
    CognitiveConfig,
    CompensationPolicy,
    EvaluationLineage,
    PhysicalFrame,
    RunKind,
    VariantFamily,
)
from aios.cases.contract import Case
from aios.chdd.runner import run_chdd
from aios.chdd.trajectory import build_chdd_input, write_chdd_csv
from aios.evaluation.canonical import digest
from aios.execution import run_full_opm
from aios.opm.summary import SummaryData
from aios.pipeline.validation import read_baseline_inputs
from aios.schedule.generator import schedule_sha256
from aios.state.extractor import StateExtractor, well_controls_from_schedule

#: Симулятор, зафиксированный кейсом. Не константа исходника: берётся из кейса.
_FULL_RUN_MODE = "FULL"


@dataclass(frozen=True, slots=True)
class BenchmarkContext:
    """Общие входы, одинаковые для всех вариантов одного прогона бенчмарка."""

    case: Case
    case_path: Path
    code_sha: str
    workspace_root: Path
    deck_reference: str
    compensation_policy: CompensationPolicy = CompensationPolicy.OFFICIAL_UNKNOWN_PORT

    def baseline_inputs(self):
        return read_baseline_inputs(self.case)

    def geology(self):
        """Геология, выведенная из самой колоды кейса.

        Пустой контекст -- это отсутствие массива регионов в колоде, а не
        ошибка: система обязана работать и без region-действий.
        """
        from aios.geology import geology_for_deck

        history = self.case.history
        return geology_for_deck(
            deck_root=history.deck_root,
            data_file=str(history.data_file),
            schedule_include=str(history.schedule_include),
        )

    def frame(self, *, initial_state_digest: str, chdd_month_count: int | None) -> PhysicalFrame:
        case = self.case
        return PhysicalFrame(
            case_id=case.case_id,
            case_digest=digest(self.case_path.read_text(encoding="utf-8")),
            deck_reference=self.deck_reference,
            deck_digest=None,
            simulator_identity=case.simulator.docker_image,
            runtime_mode=_FULL_RUN_MODE,
            initial_state_digest=initial_state_digest,
            effective_start=case.management_start_date.isoformat(),
            effective_end=case.management_end_date.isoformat(),
            chdd_calculator_version=case.economics.provenance,
            chdd_month_count=chdd_month_count,
            guardian_version=CONSTRAINT_GUARDIAN_VERSION,
            compensation_policy=self.compensation_policy,
        )


@dataclass(frozen=True, slots=True)
class ScheduleEvaluation:
    """Результат одной физической оценки фиксированного расписания."""

    schedule_sha: str
    summary: SummaryData
    realized_state_digest: str
    guardian_pass: bool
    guardian_checks: int
    guardian_failed: int
    npv_mln_rub: float
    chdd_input_digest: str
    chdd_result_digest: str
    yearly_profile_digest: str
    report_steps: int
    well_count: int


def evaluate_schedule_text(
    context: BenchmarkContext,
    *,
    schedule_text: str,
    schedule,  # aios.schedule.events.Schedule
    run_label: str,
) -> ScheduleEvaluation:
    """Прогнать расписание через настоящий OPM, Guardian и CHDD.

    Это единственный физический путь для всех вариантов с фиксированным
    расписанием. Типизированный отказ симулятора остаётся отказом и никогда не
    превращается в результат.
    """
    state, _baseline, history_text, _start = context.baseline_inputs()
    root = context.workspace_root / run_label
    run = run_full_opm(
        context.case,
        root=root,
        history_text=history_text,
        schedule_text=schedule_text,
    )
    if not run.is_pass or run.summary is None:
        raise BenchmarkError(
            f"benchmark: OPM run {run_label!r} did not succeed "
            f"(exit_code={run.run_result.exit_code}); a failed run is never a result"
        )
    summary = run.summary

    controls = well_controls_from_schedule(
        state, schedule, at_date=context.case.management_start_date
    )
    realized = StateExtractor().extract(
        summary=summary,
        case=context.case,
        run_id=run_label,
        simulator_digest=context.case.simulator.docker_image,
        schedule_hash=schedule_sha256(schedule_text),
        well_controls=controls,
    )
    report = ConstraintGuardian().evaluate(realized)

    chdd_input = build_chdd_input(summary, context.case)
    source = write_chdd_csv(chdd_input, root / "chdd_input.csv")
    chdd = run_chdd(context.case, chdd_input, source_file=source)

    return ScheduleEvaluation(
        schedule_sha=schedule_sha256(schedule_text),
        summary=summary,
        realized_state_digest=realized.digest(),
        guardian_pass=report.summary.failed == 0,
        guardian_checks=report.summary.total_checks,
        guardian_failed=report.summary.failed,
        npv_mln_rub=chdd.npv_mln_rub,
        chdd_input_digest=chdd_input.digest(),
        chdd_result_digest=digest(chdd.to_json()),
        yearly_profile_digest=digest([row.to_json() for row in chdd.yearly_profile]),
        report_steps=summary.report_steps,
        well_count=len(summary.wells),
    )


def _control_change_count(schedule) -> int:
    """Сколько управляющих записей несёт расписание окна."""
    return sum(len(step.producers) + len(step.injectors) for step in schedule.steps)


def run_fixed_schedule_variant(
    context: BenchmarkContext,
    *,
    variant_id: str,
    family: VariantFamily,
    schedule,  # aios.schedule.events.Schedule
    schedule_text: str,
    source_reference: str,
    notes: tuple[str, ...] = (),
) -> BenchmarkOutcome:
    """Оценить вариант с фиксированным расписанием и собрать его манифест."""
    started = time.monotonic()
    state, _baseline, _history, _start = context.baseline_inputs()
    evaluation = evaluate_schedule_text(
        context, schedule_text=schedule_text, schedule=schedule, run_label=variant_id
    )
    manifest = BenchmarkRunManifest(
        benchmark_schema_version=BENCHMARK_SCHEMA_VERSION,
        variant_id=variant_id,
        variant_family=family,
        run_kind=RunKind.FIXED_SCHEDULE_REPLAY,
        code_sha=context.code_sha,
        frame=context.frame(
            initial_state_digest=digest(
                [well.well for well in state.wells] + [state.as_of.isoformat()]
            ),
            chdd_month_count=None,
        ),
        cognitive=CognitiveConfig(llm_enabled=False),
        lineage=(
            EvaluationLineage(
                schedule_digest=evaluation.schedule_sha,
                realized_state_digest=evaluation.realized_state_digest,
                chdd_input_digest=evaluation.chdd_input_digest,
            ),
        ),
        source_reference=source_reference,
        notes=notes,
    )
    return BenchmarkOutcome(
        manifest=manifest,
        npv_mln_rub=evaluation.npv_mln_rub,
        guardian_pass=evaluation.guardian_pass,
        guardian_check_count=evaluation.guardian_checks,
        guardian_failed_count=evaluation.guardian_failed,
        simulation_count=1,
        control_change_count=_control_change_count(schedule),
        decision_steps=1,
        chdd_result_digest=evaluation.chdd_result_digest,
        yearly_profile_digest=evaluation.yearly_profile_digest,
        wall_duration_seconds=round(time.monotonic() - started, 3),
        extra={
            "report_steps": evaluation.report_steps,
            "well_count": evaluation.well_count,
        },
    )


def ingest_historical_reference(
    context: BenchmarkContext,
    *,
    variant_id: str,
    family: VariantFamily,
    npv_mln_rub: float,
    schedule_digest: str,
    source_reference: str,
    notes: tuple[str, ...],
    guardian_pass: bool = True,
) -> BenchmarkOutcome:
    """Внести историческую запись, которую текущий код не воспроизводит.

    Такая запись честно помечается как историческая: она попадает в таблицу как
    ссылка и не может быть объявлена победителем над свежими прогонами.
    """
    manifest = BenchmarkRunManifest(
        benchmark_schema_version=BENCHMARK_SCHEMA_VERSION,
        variant_id=variant_id,
        variant_family=family,
        run_kind=RunKind.HISTORICAL_REFERENCE,
        code_sha=context.code_sha,
        frame=context.frame(initial_state_digest="historical", chdd_month_count=None),
        cognitive=CognitiveConfig(llm_enabled=False),
        lineage=(EvaluationLineage(schedule_digest=schedule_digest),),
        source_reference=source_reference,
        notes=notes,
    )
    return BenchmarkOutcome(
        manifest=manifest,
        npv_mln_rub=npv_mln_rub,
        guardian_pass=guardian_pass,
        simulation_count=0,
        decision_steps=0,
    )


__all__ = [
    "BenchmarkContext",
    "ScheduleEvaluation",
    "evaluate_schedule_text",
    "ingest_historical_reference",
    "run_fixed_schedule_variant",
]
