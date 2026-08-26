"""EconomicSelector -- argmax authoritative CHDD over the commit-eligible set.

Not an optimizer, not a simulator, not a CHDD implementation. It consumes
already-produced `SimulationResult`/`ConstraintReport`/`ChddResult` evidence
and deterministically picks a winner among candidates that were actually
simulated, Guardian-passed, and CHDD-evaluated (section 10's canonical
commit-eligibility rule). See `docs/architecture/mas-v1.1-economic-selection.md`.
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from aios.mas.selection.models import (
    SELECTION_RULE_VERSION,
    CandidateEvaluation,
    EconomicSelectionResult,
    EligibilityRejection,
    IneligibilityReason,
)


class EconomicSelectionError(ValueError):
    """Base error for economic selection failures."""


class DuplicateCandidateIdentityError(EconomicSelectionError):
    """Two evaluation records share a candidate identity but disagree.

    Never resolved by list order (section 19): byte-identical duplicates are
    deduplicated, inconsistent ones fail closed.
    """


class NoCommitEligibleCandidateError(EconomicSelectionError):
    """No candidate is simulated, Guardian-passed, and CHDD-evaluated.

    The selector never falls back to HOLD, baseline, or "least bad" (section
    23) -- even HOLD must be verified for this decision step. The caller
    (future Orchestrator) decides how to handle a zero-eligible step.
    """


class EconomicSelector:
    """Deterministic `economic_argmax_chdd_v1` selection policy."""

    rule_version: str = SELECTION_RULE_VERSION

    def select(self, evaluations: Sequence[CandidateEvaluation]) -> EconomicSelectionResult:
        deduped = self._deduplicate(evaluations)

        eligible: list[CandidateEvaluation] = []
        rejected: list[EligibilityRejection] = []
        for evaluation in deduped:
            outcome = self._ineligibility(evaluation)
            if outcome is None:
                eligible.append(evaluation)
                continue
            reason, detail = outcome
            rejected.append(
                EligibilityRejection(
                    candidate_identity=evaluation.candidate_identity,
                    candidate_kind=evaluation.candidate.candidate_kind,
                    reason=reason,
                    detail=detail,
                )
            )

        if not eligible:
            raise NoCommitEligibleCandidateError(
                "no candidate is simulated, Guardian-passed, and CHDD-evaluated"
            )

        # argmax CHDD; exact ties broken by ascending candidate identity
        # (neutral canonical digest order -- no HOLD/baseline/agent bias).
        selected = min(
            eligible,
            key=lambda evaluation: (
                -evaluation.chdd.result.npv_mln_rub,  # type: ignore[union-attr]
                evaluation.candidate_identity,
            ),
        )

        return EconomicSelectionResult(
            selection_rule_version=self.rule_version,
            selected=selected,
            eligible=tuple(sorted(eligible, key=lambda evaluation: evaluation.candidate_identity)),
            rejected=tuple(sorted(rejected, key=lambda rejection: rejection.candidate_identity)),
        )

    @staticmethod
    def _deduplicate(
        evaluations: Sequence[CandidateEvaluation],
    ) -> tuple[CandidateEvaluation, ...]:
        by_identity: dict[str, CandidateEvaluation] = {}
        for evaluation in evaluations:
            identity = evaluation.candidate_identity
            existing = by_identity.get(identity)
            if existing is None:
                by_identity[identity] = evaluation
            elif existing != evaluation:
                raise DuplicateCandidateIdentityError(
                    f"candidate {identity} appears more than once with inconsistent "
                    "evaluation records"
                )
        return tuple(by_identity.values())

    @staticmethod
    def _ineligibility(
        evaluation: CandidateEvaluation,
    ) -> tuple[IneligibilityReason, str] | None:
        candidate_identity = evaluation.candidate_identity

        simulation = evaluation.simulation
        if simulation is None:
            return IneligibilityReason.NOT_SIMULATED, "no SimulationResult was provided"
        if simulation.candidate_identity != candidate_identity:
            return (
                IneligibilityReason.SIMULATION_IDENTITY_MISMATCH,
                (
                    f"simulation claims candidate {simulation.candidate_identity!r}, "
                    f"expected {candidate_identity!r}"
                ),
            )
        if not simulation.result.supports_objective_evaluation:
            return (
                IneligibilityReason.SIMULATION_FAILED,
                f"simulation status is {simulation.result.status!s}, not SUCCESS",
            )

        guardian = evaluation.guardian
        if guardian is None:
            return IneligibilityReason.GUARDIAN_MISSING, "no Guardian result was provided"
        if guardian.candidate_identity != candidate_identity:
            return (
                IneligibilityReason.GUARDIAN_IDENTITY_MISMATCH,
                (
                    f"guardian result claims candidate {guardian.candidate_identity!r}, "
                    f"expected {candidate_identity!r}"
                ),
            )
        if guardian.simulation_result_digest != simulation.result.result_digest:
            return (
                IneligibilityReason.GUARDIAN_IDENTITY_MISMATCH,
                (
                    f"guardian result claims simulation {guardian.simulation_result_digest!r}, "
                    f"expected {simulation.result.result_digest!r}"
                ),
            )
        if not guardian.passed:
            return (
                IneligibilityReason.GUARDIAN_FAILED,
                f"{guardian.report.summary.failed} official constraint(s) failed",
            )

        chdd = evaluation.chdd
        if chdd is None:
            return IneligibilityReason.CHDD_MISSING, "no CHDD result was provided"
        if chdd.candidate_identity != candidate_identity:
            return (
                IneligibilityReason.CHDD_IDENTITY_MISMATCH,
                (
                    f"chdd result claims candidate {chdd.candidate_identity!r}, "
                    f"expected {candidate_identity!r}"
                ),
            )
        if chdd.simulation_result_digest != simulation.result.result_digest:
            return (
                IneligibilityReason.CHDD_IDENTITY_MISMATCH,
                (
                    f"chdd result claims simulation {chdd.simulation_result_digest!r}, "
                    f"expected {simulation.result.result_digest!r}"
                ),
            )
        if not math.isfinite(chdd.result.npv_mln_rub):
            return (
                IneligibilityReason.NON_FINITE_CHDD_VALUE,
                f"chdd npv_mln_rub is not finite: {chdd.result.npv_mln_rub!r}",
            )

        return None
