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
    def _lineage_failure(
        evaluation: CandidateEvaluation,
    ) -> tuple[IneligibilityReason, str] | None:
        """Every outcome must come from the same verified run.

        Matching labels are not enough. A Guardian verdict computed for one
        candidate can be wrapped with another candidate's name and result
        digest, and every field of that wrapper agrees with itself. Only the
        four-part execution root ties the verdict back to the run it was
        actually computed from, so all three outcomes must carry it and it
        must be the same root in each.
        """
        outcomes = (
            ("simulation", evaluation.simulation),
            ("guardian", evaluation.guardian),
            ("chdd", evaluation.chdd),
        )
        present = [(name, outcome) for name, outcome in outcomes if outcome is not None]
        unverified = [name for name, outcome in present if outcome.execution is None]
        if unverified:
            return (
                IneligibilityReason.UNVERIFIED_EXECUTION,
                (
                    f"outcome(s) {sorted(unverified)} carry no verified execution identity; "
                    "an asserted label cannot establish economic eligibility"
                ),
            )

        roots = {name: outcome.execution for name, outcome in present}
        distinct = {root.digest() for root in roots.values()}
        if len(distinct) > 1:
            detail = ", ".join(
                f"{name}={root.candidate_digest[:12]}/{root.result_digest[:12]}"
                for name, root in sorted(roots.items())
            )
            return (
                IneligibilityReason.EXECUTION_LINEAGE_MISMATCH,
                f"outcomes come from different verified executions ({detail})",
            )

        root = next(iter(roots.values()), None)
        if root is not None and root.candidate_digest != evaluation.candidate_identity:
            return (
                IneligibilityReason.EXECUTION_LINEAGE_MISMATCH,
                (
                    f"verified execution belongs to candidate {root.candidate_digest!r}, "
                    f"expected {evaluation.candidate_identity!r}"
                ),
            )
        if root is not None and evaluation.simulation is not None:
            result = evaluation.simulation.result
            if not result.is_request_bound:
                return (
                    IneligibilityReason.UNVERIFIED_EXECUTION,
                    "simulation result carries no request binding",
                )
            if (
                result.request_digest != root.request_digest
                or result.simulation_key != root.simulation_key
                or result.result_digest != root.result_digest
            ):
                return (
                    IneligibilityReason.EXECUTION_LINEAGE_MISMATCH,
                    "simulation result does not match its verified execution root",
                )
        return None

    @staticmethod
    def _ineligibility(
        evaluation: CandidateEvaluation,
    ) -> tuple[IneligibilityReason, str] | None:
        candidate_identity = evaluation.candidate_identity

        lineage = EconomicSelector._lineage_failure(evaluation)
        if lineage is not None:
            return lineage

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
