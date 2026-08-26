"""Closed-loop orchestration зона Hybrid MAS v1.1.

Оркестратор владеет последовательностью вызовов и границами полномочий.
Физика, легальность, экономика и магнитуды принадлежат соседним зонам и
обязаны быть переданы явно -- значений по умолчанию у них нет.
"""

from aios.mas.orchestration.boundaries import (
    CHDD,
    ArbiterLike,
    BoundaryError,
    CandidateExecutor,
    EconomicResult,
    GuardianVerdict,
    IdentityPredictiveSafetyScreen,
    PredictiveSafetyScreen,
    RealizedConstraintGuardian,
    RealizedStateExtractor,
    SimulatorRuntimeExecutor,
    StateAdvancer,
    identity_pre_action_validator,
    never_conflict,
)
from aios.mas.orchestration.orchestrator import (
    CandidateRecord,
    ClosedLoopOrchestrator,
    ConflictPredicate,
    DecisionStepInput,
    DecisionStepResult,
    OrchestrationError,
    OrchestratorConfig,
    PreActionValidator,
    SpecialistFactory,
    default_next_input,
)
from aios.mas.orchestration.trace import DecisionTrace, StepFailure

__all__ = [
    "CHDD",
    "ArbiterLike",
    "BoundaryError",
    "CandidateExecutor",
    "CandidateRecord",
    "ClosedLoopOrchestrator",
    "ConflictPredicate",
    "DecisionStepInput",
    "DecisionStepResult",
    "DecisionTrace",
    "EconomicResult",
    "GuardianVerdict",
    "IdentityPredictiveSafetyScreen",
    "OrchestrationError",
    "OrchestratorConfig",
    "PreActionValidator",
    "PredictiveSafetyScreen",
    "RealizedConstraintGuardian",
    "RealizedStateExtractor",
    "SimulatorRuntimeExecutor",
    "SpecialistFactory",
    "StateAdvancer",
    "StepFailure",
    "default_next_input",
    "identity_pre_action_validator",
    "never_conflict",
]
