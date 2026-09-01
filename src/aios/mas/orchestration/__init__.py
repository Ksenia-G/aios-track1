"""Closed-loop orchestration зона Hybrid MAS v1.1.

Оркестратор владеет последовательностью вызовов и границами полномочий.
Физика, легальность, экономика и магнитуды принадлежат соседним зонам и
обязаны быть переданы явно -- значений по умолчанию у них нет.
"""

from aios.mas.orchestration.audit import (
    DecisionEpochAudit,
    EvidenceAudit,
    SpecialistAudit,
)
from aios.mas.orchestration.boundaries import (
    CHDD,
    ArbiterLike,
    BoundaryError,
    CandidateExecutor,
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
from aios.mas.orchestration.composition import (
    CompositionError,
    OpmChdd,
    OpmRealizedStateExtractor,
    OpmRunLocator,
    RealClosedLoopComposition,
    RealizedStateAdvancer,
    build_real_closed_loop,
    report_step_for,
)
from aios.mas.orchestration.context import (
    CONTEXT_SLOTS,
    build_decision_context,
    epoch_evidence_id,
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
    "CONTEXT_SLOTS",
    "ArbiterLike",
    "BoundaryError",
    "CandidateExecutor",
    "CandidateRecord",
    "ClosedLoopOrchestrator",
    "CompositionError",
    "ConflictPredicate",
    "DecisionEpochAudit",
    "DecisionStepInput",
    "DecisionStepResult",
    "DecisionTrace",
    "EvidenceAudit",
    "GuardianVerdict",
    "IdentityPredictiveSafetyScreen",
    "OpmChdd",
    "OpmRealizedStateExtractor",
    "OpmRunLocator",
    "OrchestrationError",
    "OrchestratorConfig",
    "PreActionValidator",
    "PredictiveSafetyScreen",
    "RealClosedLoopComposition",
    "RealizedConstraintGuardian",
    "RealizedStateAdvancer",
    "RealizedStateExtractor",
    "SimulatorRuntimeExecutor",
    "SpecialistAudit",
    "SpecialistFactory",
    "StateAdvancer",
    "StepFailure",
    "build_decision_context",
    "build_real_closed_loop",
    "default_next_input",
    "epoch_evidence_id",
    "identity_pre_action_validator",
    "never_conflict",
    "report_step_for",
]
