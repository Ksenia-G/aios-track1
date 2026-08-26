"""Typed records for ECONOMIC-SELECTION-001.

None of the three authoritative result types this selector consumes --
`aios.evaluation.simulation.SimulationResult`,
`aios.agents.constraint_guardian.ConstraintReport`,
`aios.chdd.ChddResult` -- carry a reference to a `MagnitudeCandidate` or to
each other. Each was designed independently and none of them changes here
(section 32/33 of the task: zero implementation diff expected).

The `*Outcome` wrappers below exist only to make identity explicit: the
caller (future Orchestrator) asserts which candidate and which simulation
result an outcome belongs to, and `EconomicSelector` verifies that claim.
This is deliberate -- identity is asserted and checked, never derived from
list position (section 21) and never invented where the underlying
contracts do not carry it (section 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aios.agents.constraint_guardian import ConstraintReport
from aios.chdd import ChddResult
from aios.evaluation.canonical import JsonValue, digest
from aios.evaluation.simulation import SimulationResult
from aios.mas.common import CandidateKind
from aios.mas.magnitude import MagnitudeCandidate


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class SimulationOutcome:
    """A claim: `result` is the physical simulation of candidate `candidate_identity`."""

    candidate_identity: str
    result: SimulationResult

    def __post_init__(self) -> None:
        _require_text(self.candidate_identity, "simulation_outcome.candidate_identity")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "result": self.result.to_json(),
        }


@dataclass(frozen=True, slots=True)
class GuardianOutcome:
    """A claim: `report` is the realized Guardian verdict for the given candidate/simulation."""

    candidate_identity: str
    simulation_result_digest: str
    report: ConstraintReport

    def __post_init__(self) -> None:
        _require_text(self.candidate_identity, "guardian_outcome.candidate_identity")
        _require_text(self.simulation_result_digest, "guardian_outcome.simulation_result_digest")

    @property
    def passed(self) -> bool:
        return self.report.summary.failed == 0

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "simulation_result_digest": self.simulation_result_digest,
            "report": self.report.to_json(),
        }


@dataclass(frozen=True, slots=True)
class ChddOutcome:
    """A claim: `result` is the authoritative CHDD evaluation for the given candidate/simulation."""

    candidate_identity: str
    simulation_result_digest: str
    result: ChddResult

    def __post_init__(self) -> None:
        _require_text(self.candidate_identity, "chdd_outcome.candidate_identity")
        _require_text(self.simulation_result_digest, "chdd_outcome.simulation_result_digest")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "simulation_result_digest": self.simulation_result_digest,
            "result": self.result.to_json(),
        }


@dataclass(frozen=True, slots=True)
class CandidateEvaluation:
    """Everything known about one candidate: structural identity plus (optional) evidence.

    `simulation`/`guardian`/`chdd` are independently optional: a candidate may
    exist without ever having been simulated, or simulated without a matching
    Guardian verdict yet, etc. `EconomicSelector` is what turns this into an
    eligibility decision -- this record only carries what was actually given.
    """

    candidate: MagnitudeCandidate
    simulation: SimulationOutcome | None = None
    guardian: GuardianOutcome | None = None
    chdd: ChddOutcome | None = None

    @property
    def candidate_identity(self) -> str:
        return self.candidate.digest()

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate": self.candidate.to_json(),
            "simulation": None if self.simulation is None else self.simulation.to_json(),
            "guardian": None if self.guardian is None else self.guardian.to_json(),
            "chdd": None if self.chdd is None else self.chdd.to_json(),
        }


class IneligibilityReason(StrEnum):
    """Why a candidate is not commit-eligible. Never used to rank -- only to explain."""

    NOT_SIMULATED = "NOT_SIMULATED"
    SIMULATION_IDENTITY_MISMATCH = "SIMULATION_IDENTITY_MISMATCH"
    SIMULATION_FAILED = "SIMULATION_FAILED"
    GUARDIAN_MISSING = "GUARDIAN_MISSING"
    GUARDIAN_IDENTITY_MISMATCH = "GUARDIAN_IDENTITY_MISMATCH"
    GUARDIAN_FAILED = "GUARDIAN_FAILED"
    CHDD_MISSING = "CHDD_MISSING"
    CHDD_IDENTITY_MISMATCH = "CHDD_IDENTITY_MISMATCH"
    NON_FINITE_CHDD_VALUE = "NON_FINITE_CHDD_VALUE"


@dataclass(frozen=True, slots=True)
class EligibilityRejection:
    """Audit record for one rejected candidate.

    Deliberately lightweight (identity + kind + reason + detail) instead of
    re-embedding the full evaluation: a rejected candidate's CHDD value may be
    non-finite, and non-finite floats cannot round-trip through the
    repository's canonical JSON (`aios.evaluation.canonical.normalize_json`
    rejects them by design). The reason/detail pair is enough to answer
    "which candidates existed, which failed, and why" without that hazard.
    """

    candidate_identity: str
    candidate_kind: CandidateKind
    reason: IneligibilityReason
    detail: str

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "candidate_kind": str(self.candidate_kind),
            "reason": str(self.reason),
            "detail": self.detail,
        }


SELECTION_RULE_VERSION = "economic_argmax_chdd_v1"


@dataclass(frozen=True, slots=True)
class EconomicSelectionResult:
    """Winner plus full audit trail, per section 25.

    `eligible`/`rejected` are stored in ascending `candidate_identity` order
    so that the result (and its digest) do not depend on input order.
    """

    selection_rule_version: str
    selected: CandidateEvaluation
    eligible: tuple[CandidateEvaluation, ...]
    rejected: tuple[EligibilityRejection, ...]

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "selection_rule_version": self.selection_rule_version,
            "selected": self.selected.to_json(),
            "eligible": [evaluation.to_json() for evaluation in self.eligible],
            "rejected": [rejection.to_json() for rejection in self.rejected],
        }

    def digest(self) -> str:
        return digest(self.to_json())
