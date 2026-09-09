"""Typed records for ECONOMIC-SELECTION-001.

None of the three authoritative result types this selector consumes --
`aios.evaluation.simulation.SimulationResult`,
`aios.agents.constraint_guardian.ConstraintReport`,
`aios.chdd.ChddResult` -- carry a reference to a `MagnitudeCandidate` or to
each other. Each was designed independently and none of them changes here
(section 32/33 of the task: zero implementation diff expected).

The `*Outcome` wrappers below make identity explicit, and `EconomicSelector`
verifies it -- never derived from list position (section 21).

An asserted label alone is not identity, though. A Guardian report computed
for candidate B can be wrapped with candidate A's name and A's result digest,
and nothing in the wrapper contradicts it. `VerifiedExecution` closes that:
it is the four-part lineage root of one validated run, it can only be
produced by code that actually checked a `RuntimeExecution` against its
candidate, and an outcome carrying it cannot be re-pointed at another run
without the mismatch becoming visible. Outcomes without it stay
representable -- historical records and non-authoritative fixtures still
exist -- but they can never become economically eligible.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from aios.agents.constraint_guardian import ConstraintReport
from aios.agents.trajectory_guardian import TrajectoryConstraintReport
from aios.chdd import ChddResult
from aios.evaluation.canonical import JsonValue, digest
from aios.evaluation.simulation import SimulationResult
from aios.mas.common import CandidateKind
from aios.mas.magnitude import MagnitudeCandidate


class OutcomeLineageError(ValueError):
    """An authoritative outcome does not belong to the input it claims."""


@dataclass(frozen=True, slots=True)
class VerifiedExecution:
    """Lineage root of one run whose identity has already been verified.

    Every field is recomputed from the real objects by the code that
    validates the execution; none of them is a caller-chosen label. Holding
    an instance therefore means the check happened.
    """

    candidate_digest: str
    request_digest: str
    simulation_key: str
    result_digest: str

    def __post_init__(self) -> None:
        for name in ("candidate_digest", "request_digest", "simulation_key", "result_digest"):
            _require_text(getattr(self, name), f"verified_execution.{name}")

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_digest": self.candidate_digest,
            "request_digest": self.request_digest,
            "simulation_key": self.simulation_key,
            "result_digest": self.result_digest,
        }

    def digest(self) -> str:
        return digest(self.to_json())


def _require_text(value: str, where: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where}: expected non-empty string")
    return value


@dataclass(frozen=True, slots=True)
class VerifiedRealizedState:
    """The realized state of one verified run, and the run it came from.

    Guardian judges a StateCard. Knowing which run produced that card is what
    keeps a real verdict about one candidate from being presented as a verdict
    about another.
    """

    execution: VerifiedExecution
    state_card_digest: str

    def __post_init__(self) -> None:
        _require_text(self.state_card_digest, "verified_realized_state.state_card_digest")

    @classmethod
    def of(cls, execution: VerifiedExecution, state_card: object) -> VerifiedRealizedState:
        """Derive the binding from the actual card, never from a label."""
        digest_value = state_card.digest()  # type: ignore[attr-defined]
        return cls(execution=execution, state_card_digest=digest_value)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "execution": self.execution.to_json(),
            "state_card_digest": self.state_card_digest,
        }


@dataclass(frozen=True, slots=True)
class VerifiedChddInput:
    """The economic input of one verified run, and the run it came from.

    CHDD is computed over a trajectory, not over a StateCard, so the trajectory
    is what has to be identified: a StateCard digest would not prove which
    economic input the number came from.
    """

    execution: VerifiedExecution
    input_digest: str

    def __post_init__(self) -> None:
        _require_text(self.input_digest, "verified_chdd_input.input_digest")

    @classmethod
    def of(cls, execution: VerifiedExecution, chdd_input: object) -> VerifiedChddInput:
        """Derive the binding from the actual input, never from a label."""
        digest_value = chdd_input.digest()  # type: ignore[attr-defined]
        return cls(execution=execution, input_digest=digest_value)

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "execution": self.execution.to_json(),
            "input_digest": self.input_digest,
        }


@dataclass(frozen=True, slots=True)
class SimulationOutcome:
    """A claim: `result` is the physical simulation of candidate `candidate_identity`."""

    candidate_identity: str
    result: SimulationResult
    execution: VerifiedExecution | None = None

    def __post_init__(self) -> None:
        _require_text(self.candidate_identity, "simulation_outcome.candidate_identity")

    @classmethod
    def verified(cls, execution: VerifiedExecution, result: SimulationResult) -> SimulationOutcome:
        """Build from a validated run; the label is derived, not supplied."""
        return cls(
            candidate_identity=execution.candidate_digest,
            result=result,
            execution=execution,
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "result": self.result.to_json(),
            "execution": None if self.execution is None else self.execution.to_json(),
        }


@dataclass(frozen=True, slots=True)
class GuardianOutcome:
    """A claim: `report` is the realized Guardian verdict for the given candidate/simulation."""

    candidate_identity: str
    simulation_result_digest: str
    report: ConstraintReport | TrajectoryConstraintReport
    execution: VerifiedExecution | None = None
    state_card_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.candidate_identity, "guardian_outcome.candidate_identity")
        _require_text(self.simulation_result_digest, "guardian_outcome.simulation_result_digest")

    @classmethod
    def verified(
        cls, realized: VerifiedRealizedState, report: ConstraintReport | TrajectoryConstraintReport
    ) -> GuardianOutcome:
        """Build from the realized state this verdict was actually computed on.

        The report has to name that state itself. Supplying a run root beside
        an unrelated report used to be enough; it no longer is, because a real
        verdict about another candidate names another card.
        """
        if report.state_card_digest is None:
            raise OutcomeLineageError(
                "guardian report carries no state_card_digest and cannot be authoritative"
            )
        if report.state_card_digest != realized.state_card_digest:
            raise OutcomeLineageError(
                "guardian report was computed on state "
                f"{report.state_card_digest!r}, not on {realized.state_card_digest!r}"
            )
        return cls(
            candidate_identity=realized.execution.candidate_digest,
            simulation_result_digest=realized.execution.result_digest,
            report=report,
            execution=realized.execution,
            state_card_digest=realized.state_card_digest,
        )

    @property
    def passed(self) -> bool:
        return self.report.summary.failed == 0

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "simulation_result_digest": self.simulation_result_digest,
            "report": self.report.to_json(),
            "execution": None if self.execution is None else self.execution.to_json(),
            "state_card_digest": self.state_card_digest,
        }


@dataclass(frozen=True, slots=True)
class ChddOutcome:
    """A claim: `result` is the authoritative CHDD evaluation for the given candidate/simulation."""

    candidate_identity: str
    simulation_result_digest: str
    result: ChddResult
    execution: VerifiedExecution | None = None
    input_digest: str | None = None

    def __post_init__(self) -> None:
        _require_text(self.candidate_identity, "chdd_outcome.candidate_identity")
        _require_text(self.simulation_result_digest, "chdd_outcome.simulation_result_digest")

    @classmethod
    def verified(cls, chdd_input: VerifiedChddInput, result: ChddResult) -> ChddOutcome:
        """Build from the economic input this number was actually computed on."""
        if result.input_digest is None:
            raise OutcomeLineageError(
                "chdd result carries no input_digest and cannot be authoritative"
            )
        if result.input_digest != chdd_input.input_digest:
            raise OutcomeLineageError(
                "chdd result was computed on input "
                f"{result.input_digest!r}, not on {chdd_input.input_digest!r}"
            )
        return cls(
            candidate_identity=chdd_input.execution.candidate_digest,
            simulation_result_digest=chdd_input.execution.result_digest,
            result=result,
            execution=chdd_input.execution,
            input_digest=chdd_input.input_digest,
        )

    def to_json(self) -> dict[str, JsonValue]:
        return {
            "candidate_identity": self.candidate_identity,
            "simulation_result_digest": self.simulation_result_digest,
            "result": self.result.to_json(),
            "execution": None if self.execution is None else self.execution.to_json(),
            "input_digest": self.input_digest,
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
    UNVERIFIED_EXECUTION = "UNVERIFIED_EXECUTION"
    EXECUTION_LINEAGE_MISMATCH = "EXECUTION_LINEAGE_MISMATCH"


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
