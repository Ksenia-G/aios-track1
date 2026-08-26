"""Сборка реального замкнутого контура на OPM.

Здесь нет ни одной новой физики, экономики или легальности: модуль только
соединяет уже существующие компоненты репозитория в один рабочий контур и
требует, чтобы каждый внешний ресурс был передан явно.

Реальная цепочка одного шага:

    MagnitudeCandidate
        -> aios.execution.SimulatorRuntime.execute   (компиляция расписания,
           SimulationRequest, запуск OPM через docker, провенанс)
        -> RuntimeExecution (request + физический SimulationResult + провенанс)
        -> aios.opm.summary.read_summary             (сводка прогона)
        -> aios.state.StateExtractor.extract         (реализованный StateCard)
        -> aios.agents.ConstraintGuardian.evaluate   (реализованная легальность)
        -> aios.chdd.build_chdd_input / run_chdd     (авторитетный ЧДД)
        -> aios.mas.selection.EconomicSelector       (экономический отбор)
        -> реализованный StateCard следующего шага

Guardian судит реализованное состояние прогона, а не запрошенные управления,
и следующее состояние берётся из выходов реального прогона -- дата сама по
себе шагом контура не является.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aios.cases.contract import Case
from aios.chdd.runner import ChddResult, run_chdd
from aios.chdd.trajectory import build_chdd_input, write_chdd_csv
from aios.execution import ExecutionMode, RuntimeExecution, SimulatorRuntime
from aios.mas.magnitude import MagnitudeCandidate
from aios.opm.summary import SummaryData, read_summary
from aios.opm.workspace import OUTPUT_DIRNAME
from aios.schedule.events import Schedule
from aios.schedule.state import FieldState
from aios.state import GeologyContext, StateCard
from aios.state.extractor import StateExtractor, state_run_id, well_controls_from_schedule


class CompositionError(ValueError):
    """Реальный контур собрать нельзя: не хватает авторитетного ресурса."""


def _require(value: object, name: str) -> object:
    if value is None:
        raise CompositionError(
            f"real_closed_loop.{name}: a real resource is required; the real loop has no "
            "default for it, because a default here would substitute physics, legality "
            "or economics with something that merely looks like them"
        )
    return value


@dataclass(frozen=True, slots=True)
class OpmRunLocator:
    """Где на диске лежат выходы конкретного прогона.

    Раскладка задаётся runtime-зоной и адаптером OPM (``<workspace_root>/
    <run_label>/opm/OUT``); повторяем её здесь, а не изобретаем свою.
    """

    workspace_root: Path

    def output_dir(self, run_label: str) -> Path:
        return Path(self.workspace_root) / run_label / "opm" / OUTPUT_DIRNAME

    def summary_paths(self, run_label: str, case_name: str) -> tuple[Path, Path]:
        out = self.output_dir(run_label)
        return out / f"{case_name}.SMSPEC", out / f"{case_name}.UNSMRY"

    def read_summary(self, run_label: str, case_name: str) -> SummaryData:
        smspec, unsmry = self.summary_paths(run_label, case_name)
        if not smspec.is_file() or not unsmry.is_file():
            raise CompositionError(
                f"real_closed_loop: OPM summary for run {run_label!r} is not available at "
                f"{smspec} / {unsmry}"
            )
        return read_summary(smspec, unsmry)


@dataclass(frozen=True, slots=True)
class OpmRealizedStateExtractor:
    """Реализованный StateCard из выходов настоящего прогона OPM.

    Никакой физики здесь не считается: сводка читается существующим
    ``read_summary``, а StateCard строится существующим ``StateExtractor``.
    Роли скважин и текущие режимы приходят со стороны расписания -- extractor
    сознательно отказывается выводить их из сырых артефактов симулятора.
    """

    case: Case
    locator: OpmRunLocator
    initial_state: FieldState
    baseline: Schedule
    geology: GeologyContext | None = None
    extractor: StateExtractor = None  # type: ignore[assignment]

    def extract(self, candidate: MagnitudeCandidate, execution: RuntimeExecution) -> StateCard:
        run_label = execution.provenance.candidate_digest
        summary = self.locator.read_summary(run_label, self.case.history.data_path.stem)
        controls = well_controls_from_schedule(
            self.initial_state,
            self.baseline,
            at_date=candidate.canonical().effective_date,
        )
        extractor = self.extractor if self.extractor is not None else StateExtractor()
        return extractor.extract(
            summary=summary,
            case=self.case,
            run_id=state_run_id(
                case_id=self.case.case_id,
                schedule_hash=execution.provenance.schedule_sha256,
                simulator_digest=execution.provenance.simulation_key,
            ),
            simulator_digest=execution.provenance.simulation_key,
            schedule_hash=execution.provenance.schedule_sha256,
            well_controls=controls,
            geology=self.geology,
        )


@dataclass(frozen=True, slots=True)
class OpmChdd:
    """Авторитетный ЧДД по траектории настоящего прогона.

    Формулы не трогаются: используется существующая связка
    ``build_chdd_input`` -> ``write_chdd_csv`` -> ``run_chdd``.
    """

    case: Case
    locator: OpmRunLocator

    def evaluate(self, candidate: MagnitudeCandidate, execution: RuntimeExecution) -> ChddResult:
        run_label = execution.provenance.candidate_digest
        summary = self.locator.read_summary(run_label, self.case.history.data_path.stem)
        chdd_input = build_chdd_input(summary, self.case)
        source = write_chdd_csv(chdd_input, self.locator.output_dir(run_label) / "chdd_input.csv")
        return run_chdd(self.case, chdd_input, source_file=source)


@dataclass(frozen=True, slots=True)
class RealizedStateAdvancer:
    """Следующее состояние -- это реализованное состояние закоммиченного прогона.

    Не «то же состояние с другой датой»: контур обязан продолжаться из того,
    что физически получилось, иначе второй шаг решает ту же задачу заново.
    """

    realized_state_extractor: OpmRealizedStateExtractor

    def advance(
        self,
        state: StateCard,
        candidate: MagnitudeCandidate,
        execution: RuntimeExecution,
    ) -> StateCard:
        return self.realized_state_extractor.extract(candidate, execution)


@dataclass(frozen=True, slots=True)
class RealClosedLoopComposition:
    """Готовые к передаче в оркестратор реальные границы одного контура."""

    runtime: SimulatorRuntime
    executor: object
    realized_state_extractor: OpmRealizedStateExtractor
    guardian: object
    chdd: OpmChdd
    state_advancer: RealizedStateAdvancer
    locator: OpmRunLocator

    def boundaries(self) -> Mapping[str, object]:
        """Именованные аргументы для ``ClosedLoopOrchestrator``."""
        return {
            "candidate_executor": self.executor,
            "realized_state_extractor": self.realized_state_extractor,
            "guardian": self.guardian,
            "chdd": self.chdd,
            "state_advancer": self.state_advancer,
        }


def build_real_closed_loop(
    *,
    case: Case,
    history_text: str,
    history_start_date: date,
    initial_state: FieldState,
    baseline: Schedule,
    workspace_root: Path,
    simulator: object | None = None,
    guardian: object | None = None,
    geology: GeologyContext | None = None,
    mode: ExecutionMode = ExecutionMode.FULL,
    restart_date: date | None = None,
    policy_id: str | None = None,
    model_digest: str | None = None,
) -> RealClosedLoopComposition:
    """Собрать реальный контур из существующих компонентов репозитория.

    Каждый внешний ресурс обязателен и передаётся явно. Молчаливых значений по
    умолчанию нет: подставленная заглушка на этих границах даёт «успешный»
    прогон, «легальный» кандидат и правдоподобную экономику, за которыми
    ничего не стоит.
    """
    from aios.agents.constraint_guardian import ConstraintGuardian
    from aios.execution import OpmFlowSimulator
    from aios.mas.orchestration.boundaries import SimulatorRuntimeExecutor

    _require(case, "case")
    _require(history_text, "history_text")
    _require(initial_state, "initial_state")
    _require(baseline, "baseline")
    _require(workspace_root, "workspace_root")
    if mode is ExecutionMode.RESTART and restart_date is None:
        raise CompositionError(
            "real_closed_loop.restart_date: RESTART execution requires an explicit restart date"
        )

    resolved_simulator = (
        simulator if simulator is not None else OpmFlowSimulator(case, history_text)
    )
    runtime = SimulatorRuntime(
        case=case,
        simulator=resolved_simulator,
        history_text=history_text,
        history_start_date=history_start_date,
        state=initial_state,
        baseline=baseline,
        model_digest=model_digest,
    )
    executor = SimulatorRuntimeExecutor(
        runtime=runtime,
        workspace_root=Path(workspace_root),
        mode=mode,
        policy_id=policy_id,
        restart_date=restart_date,
    )
    locator = OpmRunLocator(workspace_root=Path(workspace_root))
    realized = OpmRealizedStateExtractor(
        case=case,
        locator=locator,
        initial_state=initial_state,
        baseline=baseline,
        geology=geology,
    )
    return RealClosedLoopComposition(
        runtime=runtime,
        executor=executor,
        realized_state_extractor=realized,
        guardian=guardian if guardian is not None else ConstraintGuardian(),
        chdd=OpmChdd(case=case, locator=locator),
        state_advancer=RealizedStateAdvancer(realized_state_extractor=realized),
        locator=locator,
    )


__all__ = [
    "CompositionError",
    "OpmChdd",
    "OpmRealizedStateExtractor",
    "OpmRunLocator",
    "RealClosedLoopComposition",
    "RealizedStateAdvancer",
    "build_real_closed_loop",
]
