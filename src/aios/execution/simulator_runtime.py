"""SimulatorRuntime -- фасад runtime/execution зоны.

Цепочка зоны: MagnitudeCandidate -> schedule compilation -> SimulationRequest ->
существующий Simulator -> физический SimulationResult + провенанс. Фасад не
решает, какой кандидат инженерно лучше, не интерпретирует SMALL/MODERATE/
AGGRESSIVE и не считает NPV/ЧДД: выбор победителя и экономика живут ниже по
конвейеру (Guardian -> CHDD -> deterministic selector), а magnitude-правила --
выше (MagnitudeResolver).

Режимы исполнения явные:

* ``ExecutionMode.FULL`` -- чистый прогон; единственный авторитетный путь для
  заявляемого результата;
* ``ExecutionMode.RESTART`` -- продолжение с сохранённого состояния через
  доказанные примитивы ``aios.opm.restart``; ускорение поиска, каждый запуск
  сопровождается reconciliation с источником состояния.

Провал симулятора никогда не превращается в успешный результат: он возвращается
типизированным SimulationResult со статусом CRASHED/TIMEOUT/INVALID_INPUT/
NON_CONVERGED и заполненным ``SimulationFailure``. Расхождение restart-цепочки
с чистым прогоном (reconciliation verdict FAIL) -- тоже физический отказ:
статус NON_CONVERGED, артефакт траектории не пишется.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aios.cases.contract import Case
from aios.evaluation.simulation import (
    SimulationFailure,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    compute_request_digest,
    compute_simulation_key,
)
from aios.evaluation.simulator import Simulator
from aios.execution.compiler import (
    CompiledCandidateSchedule,
    compile_candidate_schedule,
)
from aios.execution.execution import (
    DEFAULT_RECONCILIATION_TOLERANCE,
    ReconciliationVerdict,
    RestartExecutionError,
    reconcile_summaries,
    run_restart_chain,
    write_trajectory_artifact,
)
from aios.execution.history import CommittedHistory
from aios.execution.provenance import ExecutionMode, RuntimeProvenance
from aios.mas.invariants import require_simulation_floor
from aios.mas.magnitude import MagnitudeCandidate
from aios.schedule.events import Schedule
from aios.schedule.generator import render_full_schedule
from aios.schedule.state import FieldState

OPM_SIMULATOR_ID = "aios.execution.opm-flow"


def _bind_to_request(
    result: SimulationResult, request: SimulationRequest, simulation_key: str
) -> SimulationResult:
    """Проставить результату происхождение запроса, который его породил.

    Делается здесь, на авторитетной границе runtime-зоны, а не оставляется на
    усмотрение конкретного адаптера: результат, вернувшийся без происхождения,
    физически честен, но безымянен, и его невозможно отличить от результата
    другого прогона. Уже связанный результат перепривязке не подлежит --
    расхождение означает ошибку сборки, а не физику.
    """
    request_digest = compute_request_digest(request)
    if result.is_request_bound:
        if result.request_digest != request_digest or result.simulation_key != simulation_key:
            raise RuntimeExecutionBindingError(
                "simulator returned a result bound to a different request "
                f"(expected request {request_digest}, got {result.request_digest})"
            )
        return result
    return result.bound_to(request_digest=request_digest, simulation_key=simulation_key)


class RuntimeExecutionBindingError(RuntimeError):
    """Результат прогона не принадлежит исполнявшемуся запросу."""


@dataclass(frozen=True, slots=True)
class RuntimeExecution:
    """Итог исполнения одного кандидата: запрос, физический результат, след."""

    request: SimulationRequest
    result: SimulationResult
    provenance: RuntimeProvenance


class SimulatorRuntime:
    """Исполнитель кандидат -> симулятор. Без decision logic внутри."""

    def __init__(
        self,
        *,
        case: Case,
        simulator: Simulator,
        history_text: str,
        history_start_date: date,
        state: FieldState,
        baseline: Schedule,
        model_digest: str | None = None,
    ) -> None:
        self._case = case
        self._simulator = simulator
        self._history_text = history_text
        self._history_start_date = history_start_date
        self._state = state
        self._baseline = baseline
        self._model_digest = model_digest
        # Многошаговый прогон: неизменяемая история закоммиченных управлений и
        # дата, до которой она обязана существовать. На первой эпохе и в
        # одношаговом прогоне оба поля пусты, и поведение не меняется.
        self._committed_history = CommittedHistory()
        self._require_history_before: date | None = None

    @property
    def case(self) -> Case:
        return self._case

    @property
    def committed_history(self) -> CommittedHistory:
        return self._committed_history

    def advance_to_epoch(
        self, history: CommittedHistory, *, require_history_before: date | None
    ) -> None:
        """Перевести runtime в следующую эпоху решения.

        История управлений неизменяема: сюда приходит уже дописанный объект,
        собранный из ВЫБРАННОГО кандидата предыдущей эпохи.
        """
        self._committed_history = history
        self._require_history_before = require_history_before

    @staticmethod
    def ensure_simulation_floor(candidates: tuple[MagnitudeCandidate, ...]) -> None:
        """Батч кандидатов обязан содержать HOLD и DETERMINISTIC_BASELINE."""
        require_simulation_floor(candidates)

    def compile(
        self, candidate: MagnitudeCandidate, *, policy_id: str | None = None
    ) -> CompiledCandidateSchedule:
        return compile_candidate_schedule(
            candidate,
            case=self._case,
            state=self._state,
            baseline=self._baseline,
            policy_id=policy_id,
            committed_history=self._committed_history,
            require_history_before=self._require_history_before,
        )

    def build_request(
        self, candidate: MagnitudeCandidate, compilation: CompiledCandidateSchedule
    ) -> SimulationRequest:
        canonical = candidate.canonical()
        payload = {
            "schedule_text": compilation.schedule_text,
            "compiler_id": compilation.compiler_id,
            "compiler_version": compilation.compiler_version,
            "candidate_kind": str(canonical.candidate_kind),
            "candidate_digest": canonical.digest(),
            "effective_date": canonical.effective_date.isoformat(),
            "policy_id": compilation.policy_id,
        }
        # scenario_digest -- сценарий решения, который проверяется этим
        # прогоном; им является structural bundle кандидата.
        return SimulationRequest(
            payload=payload,
            seed=None,
            model_digest=self._model_digest,
            scenario_digest=canonical.bundle_digest,
            schedule_digest=compilation.schedule_sha256,
        )

    def execute(
        self,
        candidate: MagnitudeCandidate,
        *,
        workspace_root: Path,
        run_label: str,
        mode: ExecutionMode = ExecutionMode.FULL,
        restart_date: date | None = None,
        reconciliation_tolerance: float = DEFAULT_RECONCILIATION_TOLERANCE,
        policy_id: str | None = None,
    ) -> RuntimeExecution:
        """Исполнить кандидата в заданном режиме и вернуть результат+провенанс."""
        compilation = self.compile(candidate, policy_id=policy_id)
        request = self.build_request(candidate, compilation)
        identity = self._simulator.identity
        simulation_key = compute_simulation_key(request, identity)
        provenance = RuntimeProvenance.from_compilation(
            mode=mode,
            simulation_key=simulation_key,
            compilation=compilation,
            candidate=candidate,
            simulator_identity=identity,
        )
        if mode is ExecutionMode.FULL:
            result = self._run_via_protocol(request, Path(workspace_root) / run_label)
            return RuntimeExecution(
                request=request,
                result=_bind_to_request(result, request, simulation_key),
                provenance=provenance,
            )
        execution = self._execute_restart(
            request=request,
            base_provenance=provenance,
            compilation=compilation,
            workspace_root=Path(workspace_root) / run_label,
            restart_date=restart_date,
            reconciliation_tolerance=reconciliation_tolerance,
        )
        return RuntimeExecution(
            request=execution.request,
            result=_bind_to_request(execution.result, request, simulation_key),
            provenance=execution.provenance,
        )

    def _run_via_protocol(self, request: SimulationRequest, workspace: Path) -> SimulationResult:
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            return self._simulator.run(request, workspace)
        except Exception as error:  # noqa: BLE001
            # Отказ адаптера внешнего симулятора обязан попасть в историю
            # типизированным результатом, а не раствориться в исключении.
            return SimulationResult.create(
                status=SimulationStatus.CRASHED,
                failure=SimulationFailure(
                    message=f"{type(error).__name__}: {error}",
                    details={"stage": "simulator.run"},
                ),
            )

    def _execute_restart(
        self,
        *,
        request: SimulationRequest,
        base_provenance: RuntimeProvenance,
        compilation: CompiledCandidateSchedule,
        workspace_root: Path,
        restart_date: date | None,
        reconciliation_tolerance: float,
    ) -> RuntimeExecution:
        if restart_date is None:
            raise ValueError("restart execution requires an explicit restart_date")
        full_schedule_text = render_full_schedule(
            compilation.schedule,
            self._case,
            history_text=self._history_text,
            policy_id=compilation.policy_id,
            history_start_date=self._history_start_date,
        )
        try:
            chain = run_restart_chain(
                self._case,
                root=workspace_root / "restart",
                history_text=self._history_text,
                full_schedule_text=full_schedule_text,
                restart_date=restart_date,
            )
        except RestartExecutionError as error:
            return RuntimeExecution(
                request=request,
                result=SimulationResult.create(
                    status=SimulationStatus.CRASHED,
                    failure=SimulationFailure(
                        message=str(error),
                        details={"stage": "restart.chain"},
                    ),
                ),
                provenance=base_provenance,
            )

        continuation_run = chain.continuation_run
        if not continuation_run.is_pass or continuation_run.summary is None:
            status = (
                SimulationStatus.TIMEOUT
                if continuation_run.run_result.exit_code == 124
                else SimulationStatus.CRASHED
            )
            return RuntimeExecution(
                request=request,
                result=SimulationResult.create(
                    status=status,
                    failure=SimulationFailure(
                        message="restart continuation run did not complete successfully",
                        details={
                            "stage": "restart.continuation",
                            "exit_code": continuation_run.run_result.exit_code,
                            "errors": list(continuation_run.run_result.errors[:5]),
                        },
                    ),
                    diagnostics={"opm_run": continuation_run.run_result.to_json()},
                ),
                provenance=base_provenance.with_restart_details(chain.spec.to_json(), None),
            )

        opm_diagnostics = {"opm_run": continuation_run.run_result.to_json()}
        reconciliation_report = None
        if chain.source_summary is not None:
            reconciliation_report = reconcile_summaries(
                chain.source_summary,
                continuation_run.summary,
                tolerance=reconciliation_tolerance,
            )
        report_json = reconciliation_report.to_json() if reconciliation_report else None
        if (
            reconciliation_report is not None
            and reconciliation_report.verdict is ReconciliationVerdict.FAIL
        ):
            # Расхождение с чистым прогоном -- физический отказ ускоренного
            # пути. Такой результат не имеет права выглядеть как SUCCESS:
            # статус типизированный, артефакт траектории не пишется.
            return RuntimeExecution(
                request=request,
                result=SimulationResult.create(
                    status=SimulationStatus.NON_CONVERGED,
                    failure=SimulationFailure(
                        message="restart continuation diverged from the clean full run",
                        details={
                            "stage": "restart.reconciliation",
                            "verdict": str(reconciliation_report.verdict),
                            "tolerance": reconciliation_report.tolerance,
                            "max_abs_deviation": reconciliation_report.max_abs_deviation,
                            "worst_date": reconciliation_report.worst_date,
                            "worst_vector": reconciliation_report.worst_vector,
                            "missing_overlap": list(reconciliation_report.missing_overlap),
                        },
                    ),
                    diagnostics={
                        **opm_diagnostics,
                        "reconciliation": reconciliation_report.to_json(),
                    },
                ),
                provenance=base_provenance.with_restart_details(chain.spec.to_json(), report_json),
            )

        trajectory_ref = write_trajectory_artifact(
            continuation_run.summary,
            workspace_root=workspace_root,
            producer_id=(f"{OPM_SIMULATOR_ID}@{self._simulator.identity.simulator_version}"),
        )
        dates_count = len(continuation_run.summary.dates)
        result = SimulationResult.create(
            status=SimulationStatus.SUCCESS,
            trajectory_ref=trajectory_ref,
            metadata={
                "trajectory_format": "aios.execution.opm_summary",
                "report_steps": dates_count,
                "final_date": (
                    continuation_run.summary.dates[-1].date().isoformat() if dates_count else ""
                ),
                "execution_mode": str(ExecutionMode.RESTART),
            },
            diagnostics=opm_diagnostics,
        )
        return RuntimeExecution(
            request=request,
            result=result,
            provenance=base_provenance.with_restart_details(
                chain.spec.to_json(),
                reconciliation_report.to_json() if reconciliation_report else None,
            ),
        )
