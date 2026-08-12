"""Оркестратор оценки.

EvaluationEngine -- единственное место, которое знает одновременно про
симулятор, ограничения, кеш, эвалюатор NPV и историю. Все остальные модули
остаются независимыми и заменяемыми.

Ключевой инвариант: осуществимость (constraints) и экономическая оценка (NPV)
независимы. Недопустимый по ограничениям вариант может иметь посчитанный NPV,
а отсутствующий NPV НИКОГДА не заменяется нулём, -inf или штрафом --
политика штрафов принадлежит будущему оптимизатору.

Второй инвариант: целевая функция считается только по полному успеху.
Частичный результат проверяется ограничениями, но NPV по нему не считается и
в кеш он не попадает -- иначе число по оборванной траектории оказалось бы
в одном ряду с полными прогонами.
"""

import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aios.evaluation.artifacts import ArtifactStore, ArtifactStoreError
from aios.evaluation.cache import SimulationCache
from aios.evaluation.canonical import JsonValue
from aios.evaluation.constraints import ConstraintRegistry, ConstraintReport, ConstraintStatus
from aios.evaluation.npv import EvaluatorProvenance, NpvEstimate, NpvEvaluator, NpvStatus
from aios.evaluation.records import RunRecord, RunStatus
from aios.evaluation.simulation import (
    SimulationFailure,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    compute_request_digest,
    compute_simulation_key,
)
from aios.evaluation.simulator import Simulator
from aios.evaluation.store import ExperimentDB


@dataclass(frozen=True, slots=True)
class EvaluationOutcome:
    """Полный итог одной попытки оценки."""

    run_record: RunRecord
    pre_report: ConstraintReport
    post_report: ConstraintReport
    simulation_result: SimulationResult | None = None
    npv_estimate: NpvEstimate | None = None

    @property
    def run_id(self) -> str:
        return self.run_record.run_id

    @property
    def simulation_key(self) -> str:
        return self.run_record.simulation_key


class EvaluationEngine:
    """Конвейер: ограничения -> ключ -> кеш/симулятор -> ограничения -> NPV -> история."""

    def __init__(
        self,
        *,
        simulator: Simulator,
        db: ExperimentDB,
        artifact_store: ArtifactStore,
        cache: SimulationCache,
        workspace_root: Path,
        constraints: ConstraintRegistry | None = None,
        npv_evaluator: NpvEvaluator | None = None,
    ) -> None:
        self._simulator = simulator
        self._db = db
        self._artifacts = artifact_store
        self._cache = cache
        # Корень рабочих каталогов задаёт вызывающая сторона: дефолтного пути
        # внутри репозитория нет, чтобы прогон не мусорил в исходники.
        self._workspace_root = Path(workspace_root)
        self._constraints = constraints or ConstraintRegistry()
        self._npv_evaluator = npv_evaluator

    def evaluate(self, request: SimulationRequest, *, use_cache: bool = True) -> EvaluationOutcome:
        """Оценить один вариант.

        `use_cache` -- политика вызова, а не свойство эксперимента, поэтому она
        не входит в simulation_key. При use_cache=False симулятор запускается
        даже при наличии записи в кеше (нужно для проверки воспроизводимости).
        """
        started_at = time.monotonic()
        run_id = uuid.uuid4().hex
        created_at = datetime.now(UTC)
        identity = self._simulator.identity
        simulation_key = compute_simulation_key(request, identity)
        request_digest = compute_request_digest(request)

        pre_report = self._constraints.check_pre(request)
        empty_report = ConstraintReport()

        if pre_report.has_blocking:
            # Блокирующее PRE-нарушение: симулятор не запускается вовсе.
            # NPV отсутствует не потому, что вариант плох, а потому что
            # физического результата не существует.
            record = self._build_record(
                run_id=run_id,
                created_at=created_at,
                simulation_key=simulation_key,
                request_digest=request_digest,
                request=request,
                identity_id=identity.simulator_id,
                identity_version=identity.simulator_version,
                status=RunStatus.BLOCKED,
                simulation_result=None,
                constraint_status=ConstraintStatus.BLOCKED,
                is_feasible=False,
                npv_status=NpvStatus.NOT_EVALUATED,
                npv_estimate=None,
                cache_hit=False,
                cached_from_run_id=None,
                runtime_seconds=time.monotonic() - started_at,
                metadata={"pre_violations": pre_report.to_json()},
            )
            self._db.record_run(record)
            return EvaluationOutcome(
                run_record=record, pre_report=pre_report, post_report=empty_report
            )

        cached = self._cache.lookup(simulation_key) if use_cache else None
        extra_metadata: dict[str, JsonValue] = {"pre_violations": pre_report.to_json()}

        if cached is not None:
            simulation_result = cached.result
            cache_hit = True
            cached_from_run_id: str | None = cached.source_run_id
        else:
            cache_hit = False
            cached_from_run_id = None
            simulation_result = self._run_simulator(request, run_id)

        post_report = empty_report
        npv_estimate: NpvEstimate | None = None
        npv_status = NpvStatus.NOT_EVALUATED
        constraints_checked = simulation_result.supports_constraint_checks

        if constraints_checked:
            # POST-ограничения читают артефакт через тот же доверенный
            # ArtifactReader, что и эвалюатор: конкретное хранилище в контракт
            # потребителя не попадает.
            post_report = self._constraints.check_post(simulation_result, self._artifacts)

        if simulation_result.supports_objective_evaluation:
            # POST-нарушение не отменяет оценку: осуществимость и экономика --
            # независимые характеристики варианта.
            npv_status, npv_estimate, npv_error = self._evaluate_npv(simulation_result)
            if npv_error is not None:
                extra_metadata["npv_error"] = npv_error

        run_status = self._run_status(simulation_result.status)
        constraint_status = self._constraint_status(
            pre_report, post_report, checked=constraints_checked
        )
        combined_feasible = self._combined_feasibility(
            pre_report, post_report, checked=constraints_checked
        )
        if simulation_result.failure is not None:
            extra_metadata["simulation_failure"] = simulation_result.failure.to_json()
        if post_report.violations:
            extra_metadata["post_violations"] = post_report.to_json()

        record = self._build_record(
            run_id=run_id,
            created_at=created_at,
            simulation_key=simulation_key,
            request_digest=request_digest,
            request=request,
            identity_id=identity.simulator_id,
            identity_version=identity.simulator_version,
            status=run_status,
            simulation_result=simulation_result,
            constraint_status=constraint_status,
            is_feasible=combined_feasible,
            npv_status=npv_status,
            npv_estimate=npv_estimate,
            cache_hit=cache_hit,
            cached_from_run_id=cached_from_run_id,
            runtime_seconds=time.monotonic() - started_at,
            metadata=extra_metadata,
        )
        self._db.record_run(record)

        if simulation_result.status is SimulationStatus.SUCCESS and not cache_hit:
            # Кешируем только успех и только после записи прогона в историю:
            # внешний ключ simulation_cache.source_run_id ссылается на runs.
            self._cache.store(simulation_key, simulation_result, run_id)

        return EvaluationOutcome(
            run_record=record,
            pre_report=pre_report,
            post_report=post_report,
            simulation_result=simulation_result,
            npv_estimate=npv_estimate,
        )

    def _run_simulator(self, request: SimulationRequest, run_id: str) -> SimulationResult:
        workspace = self._workspace_root / run_id
        workspace.mkdir(parents=True, exist_ok=True)
        try:
            result = self._simulator.run(request, workspace)
        except Exception as error:  # noqa: BLE001
            # Ловим широко намеренно: адаптер внешнего симулятора может упасть
            # чем угодно, а отказ обязан попасть в историю. Он не превращается
            # в успешный результат и не теряется.
            return SimulationResult.create(
                status=SimulationStatus.CRASHED,
                failure=SimulationFailure(
                    message=f"{type(error).__name__}: {error}",
                    details={"stage": "simulator.run"},
                ),
            )

        if result.trajectory_ref is None:
            return result

        try:
            stored_ref = self._artifacts.ingest(workspace, result.trajectory_ref)
        except ArtifactStoreError as error:
            # Артефакт не сошёлся по дайджесту или отсутствует: такой прогон
            # нельзя ни оценивать, ни кешировать.
            return SimulationResult.create(
                status=SimulationStatus.CRASHED,
                metadata=dict(result.metadata),
                diagnostics=dict(result.diagnostics),
                failure=SimulationFailure(
                    message=f"{type(error).__name__}: {error}",
                    details={"stage": "artifact.ingest"},
                ),
            )
        # Дайджест содержимого не меняется при переносе, меняется только путь,
        # поэтому result_digest остаётся валидным.
        return SimulationResult(
            status=result.status,
            result_digest=result.result_digest,
            trajectory_ref=stored_ref,
            metadata=result.metadata,
            diagnostics=result.diagnostics,
            failure=result.failure,
        )

    def _evaluate_npv(
        self, result: SimulationResult
    ) -> tuple[NpvStatus, NpvEstimate | None, str | None]:
        if self._npv_evaluator is None:
            return NpvStatus.NOT_EVALUATED, None, None
        try:
            estimate = self._npv_evaluator.evaluate(result, self._artifacts)
        except Exception as error:  # noqa: BLE001
            # Отказ эвалюатора учитывается отдельно от отказа симуляции.
            # Отсутствующий NPV остаётся отсутствующим: ни 0.0, ни -inf,
            # ни штраф здесь появиться не могут.
            return NpvStatus.FAILED, None, f"{type(error).__name__}: {error}"
        return NpvStatus.OK, estimate, None

    @staticmethod
    def _run_status(status: SimulationStatus) -> RunStatus:
        """Итог попытки. PARTIAL не сливается с COMPLETED."""
        if status is SimulationStatus.SUCCESS:
            return RunStatus.COMPLETED
        if status is SimulationStatus.PARTIAL:
            return RunStatus.PARTIAL
        return RunStatus.FAILED

    @staticmethod
    def _constraint_status(
        pre_report: ConstraintReport, post_report: ConstraintReport, *, checked: bool
    ) -> ConstraintStatus:
        if pre_report.violations or post_report.violations:
            return ConstraintStatus.VIOLATED
        if not checked:
            # POST-проверки не выполнялись, потому что результата нет.
            return ConstraintStatus.NOT_CHECKED
        return ConstraintStatus.SATISFIED

    @staticmethod
    def _combined_feasibility(
        pre_report: ConstraintReport, post_report: ConstraintReport, *, checked: bool
    ) -> bool | None:
        if pre_report.violations or post_report.violations:
            return False
        if not checked:
            # Неизвестно, а не «допустимо».
            return None
        return True

    @staticmethod
    def _build_record(
        *,
        run_id: str,
        created_at: datetime,
        simulation_key: str,
        request_digest: str,
        request: SimulationRequest,
        identity_id: str,
        identity_version: str,
        status: RunStatus,
        simulation_result: SimulationResult | None,
        constraint_status: ConstraintStatus,
        is_feasible: bool | None,
        npv_status: NpvStatus,
        npv_estimate: NpvEstimate | None,
        cache_hit: bool,
        cached_from_run_id: str | None,
        runtime_seconds: float,
        metadata: Mapping[str, JsonValue],
    ) -> RunRecord:
        trajectory_digest = None
        if simulation_result is not None and simulation_result.trajectory_ref is not None:
            trajectory_digest = simulation_result.trajectory_ref.content_digest
        npv_value = None if npv_estimate is None else float(npv_estimate.value)
        npv_provenance: EvaluatorProvenance | None = (
            None if npv_estimate is None else npv_estimate.provenance
        )
        record_metadata: dict[str, JsonValue] = dict(metadata)
        if simulation_result is not None:
            record_metadata["result_digest"] = simulation_result.result_digest
        if npv_estimate is not None:
            record_metadata["npv_estimate"] = npv_estimate.to_json()
        return RunRecord(
            run_id=run_id,
            created_at=created_at,
            simulation_key=simulation_key,
            simulation_request_digest=request_digest,
            simulator_id=identity_id,
            simulator_version=identity_version,
            status=status,
            model_digest=request.model_digest,
            scenario_digest=request.scenario_digest,
            schedule_digest=request.schedule_digest,
            simulation_status=None if simulation_result is None else simulation_result.status,
            trajectory_digest=trajectory_digest,
            npv_status=npv_status,
            npv_value=npv_value,
            npv_provenance=npv_provenance,
            constraint_status=constraint_status,
            is_feasible=is_feasible,
            cache_hit=cache_hit,
            cached_from_run_id=cached_from_run_id,
            runtime_seconds=runtime_seconds,
            metadata=record_metadata,
        )
