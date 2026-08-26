"""ECONOMIC-SELECTION-001: authoritative CHDD selection boundary.

Not an optimizer, not a simulator, not a CHDD implementation. Consumes
already-produced `SimulationResult`/`ConstraintReport`/`ChddResult` evidence
and deterministically selects the commit-eligible candidate with maximum
authoritative CHDD. See `docs/architecture/mas-v1.1-economic-selection.md`.

Implementation (central integration write zone), not a new frozen contract,
and is not re-exported from `aios.mas.__init__` -- import it explicitly as
`aios.mas.selection`.
"""

from aios.mas.selection.economic import (
    DuplicateCandidateIdentityError,
    EconomicSelectionError,
    EconomicSelector,
    NoCommitEligibleCandidateError,
)
from aios.mas.selection.models import (
    SELECTION_RULE_VERSION,
    CandidateEvaluation,
    ChddOutcome,
    EconomicSelectionResult,
    EligibilityRejection,
    GuardianOutcome,
    IneligibilityReason,
    SimulationOutcome,
    VerifiedExecution,
)

__all__ = [
    "SELECTION_RULE_VERSION",
    "CandidateEvaluation",
    "ChddOutcome",
    "DuplicateCandidateIdentityError",
    "EconomicSelectionError",
    "EconomicSelectionResult",
    "EconomicSelector",
    "EligibilityRejection",
    "GuardianOutcome",
    "IneligibilityReason",
    "NoCommitEligibleCandidateError",
    "SimulationOutcome",
    "VerifiedExecution",
]
