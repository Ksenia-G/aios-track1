"""Runtime/execution зона Hybrid MAS v1.1.

Граница зоны: наверху решено, *какой* численный candidate проверить
(MagnitudeResolver -> MagnitudeCandidate); здесь он воспроизводимо, полностью
и корректно исполняется физическим симулятором, а физический результат
передаётся ниже (RealizedConstraintGuardian -> CHDD -> deterministic selector).
Runtime не выбирает победителя и не считает экономику.
"""

from aios.execution.compiler import (
    COMPILER_ID,
    COMPILER_VERSION,
    CompiledCandidateSchedule,
    ScheduleCompilationError,
    compile_candidate_schedule,
)
from aios.execution.execution import (
    DEFAULT_RECONCILIATION_TOLERANCE,
    TRAJECTORY_FORMAT,
    FullOpmRun,
    ReconciliationReport,
    ReconciliationVerdict,
    RestartChainOutcome,
    RestartExecutionError,
    normalized_summary_document,
    reconcile_documents,
    reconcile_summaries,
    run_full_opm,
    run_restart_chain,
    serialize_trajectory_document,
    write_trajectory_artifact,
)
from aios.execution.provenance import ExecutionMode, RuntimeProvenance
from aios.execution.simulator_adapter import OPM_SIMULATOR_ID, OpmFlowSimulator
from aios.execution.simulator_runtime import RuntimeExecution, SimulatorRuntime

__all__ = [
    "COMPILER_ID",
    "COMPILER_VERSION",
    "DEFAULT_RECONCILIATION_TOLERANCE",
    "OPM_SIMULATOR_ID",
    "TRAJECTORY_FORMAT",
    "CompiledCandidateSchedule",
    "ExecutionMode",
    "FullOpmRun",
    "OpmFlowSimulator",
    "ReconciliationReport",
    "ReconciliationVerdict",
    "RestartChainOutcome",
    "RestartExecutionError",
    "RuntimeExecution",
    "RuntimeProvenance",
    "ScheduleCompilationError",
    "SimulatorRuntime",
    "compile_candidate_schedule",
    "normalized_summary_document",
    "reconcile_documents",
    "reconcile_summaries",
    "run_full_opm",
    "run_restart_chain",
    "serialize_trajectory_document",
    "write_trajectory_artifact",
]
