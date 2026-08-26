"""ECONOMIC-SELECTION-001 core behavior (section 30, E1-E24).

EconomicSelector consumes already-produced SimulationResult/ConstraintReport/
ChddResult evidence and deterministically argmax-selects the commit-eligible
candidate with maximum authoritative CHDD. It never simulates, never
recomputes Guardian physics, never recomputes the CHDD formula.
"""

from __future__ import annotations

import datetime
import math

import pytest

from aios.agents.constraint_guardian import ConstraintCheck, ConstraintReport, ConstraintSummary
from aios.chdd import AnnualChddRow, ChddResult
from aios.evaluation.simulation import SimulationResult, SimulationStatus
from aios.mas.common import CandidateKind
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.selection import (
    CandidateEvaluation,
    ChddOutcome,
    DuplicateCandidateIdentityError,
    EconomicSelector,
    GuardianOutcome,
    IneligibilityReason,
    NoCommitEligibleCandidateError,
    SimulationOutcome,
    VerifiedExecution,
)

_DATE = datetime.date(2020, 4, 1)


def _candidate(
    *, kind: CandidateKind = CandidateKind.AGENT_BUNDLE, bundle_digest: str = "bundle-1"
) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=kind,
        bundle_digest=bundle_digest,
        effective_date=_DATE,
        resolver_version="test-resolver/1.0.0",
    )


def _simulation_result(
    *, status: SimulationStatus = SimulationStatus.SUCCESS, suffix: str = "a"
) -> SimulationResult:
    """A result carrying the request binding a real runtime would stamp on it."""
    return SimulationResult.create(
        status=status,
        request_digest=f"request-digest-{suffix}",
        simulation_key=f"simulation-key-{suffix}",
    )


def _verified(candidate: MagnitudeCandidate, result: SimulationResult) -> VerifiedExecution:
    """The lineage root the orchestrator derives after validating a run."""
    return VerifiedExecution(
        candidate_digest=candidate.digest(),
        request_digest=result.request_digest or "",
        simulation_key=result.simulation_key or "",
        result_digest=result.result_digest,
    )


def _guardian_report(*, failed: int = 0) -> ConstraintReport:
    total = max(failed, 1)
    checks = tuple(
        ConstraintCheck(
            well_name=f"W{i}",
            constraint="producer_bhp_min",
            measured_value=60.0,
            limit=50.0,
            margin=10.0,
            status="FAIL" if i < failed else "PASS",
            near_binding=False,
            reason="x",
        )
        for i in range(total)
    )
    violations = tuple(check for check in checks if check.status == "FAIL")
    return ConstraintReport(
        metadata={"state_card_digest": "sc-digest", "evaluation_version": "test/1.0.0"},
        official_checks=checks,
        summary=ConstraintSummary(
            total_checks=len(checks),
            passed=len(checks) - len(violations),
            failed=len(violations),
            near_binding=0,
        ),
        violations=violations,
        near_binding=(),
    )


def _chdd_result(*, npv: float = 100.0) -> ChddResult:
    return ChddResult(
        calculator_version="test-chdd/1.0.0",
        provenance="OFFICIAL",
        npv_mln_rub=npv,
        profitability_index=1.5,
        economic_month_count=12,
        months_by_year={"2020": 12},
        yearly_profile=(
            AnnualChddRow(
                year=2020,
                npv_mln_rub=npv,
                cumulative_mln_rub=npv,
                months=12,
                active_well_months=10,
                stop_events=0,
                start_events=0,
            ),
        ),
        start_date="2020-01-01",
        last_economic_month="2020-12",
        source_max_date=None,
        oil_kt_total=10.0,
        liquid_kt_total=20.0,
        injection_k_total=5.0,
        active_well_months=10,
        pump_events=0,
        stop_events=0,
        start_events=0,
        conversion_events=0,
        excluded_negative_rows=0,
        duplicate_well_months=0,
        invalid_dates=0,
        ignored_blank_wells=0,
        warnings=(),
        raw={},
    )


def _evaluation(
    *,
    kind: CandidateKind = CandidateKind.AGENT_BUNDLE,
    bundle_digest: str = "bundle-1",
    npv: float = 100.0,
    guardian_failed: int = 0,
    simulation_status: SimulationStatus = SimulationStatus.SUCCESS,
    override_sim_identity: str | None = None,
    override_guardian_identity: str | None = None,
    override_guardian_sim_digest: str | None = None,
    override_chdd_identity: str | None = None,
    override_chdd_sim_digest: str | None = None,
    include_simulation: bool = True,
    include_guardian: bool = True,
    include_chdd: bool = True,
) -> CandidateEvaluation:
    candidate = _candidate(kind=kind, bundle_digest=bundle_digest)
    identity = candidate.digest()
    sim_result = _simulation_result(status=simulation_status, suffix=bundle_digest)
    root = _verified(candidate, sim_result)

    simulation = None
    if include_simulation:
        simulation = SimulationOutcome(
            candidate_identity=override_sim_identity or identity,
            result=sim_result,
            execution=root,
        )

    guardian = None
    if include_guardian:
        guardian = GuardianOutcome(
            candidate_identity=override_guardian_identity or identity,
            simulation_result_digest=override_guardian_sim_digest or sim_result.result_digest,
            report=_guardian_report(failed=guardian_failed),
            execution=root,
        )

    chdd = None
    if include_chdd:
        chdd = ChddOutcome(
            candidate_identity=override_chdd_identity or identity,
            simulation_result_digest=override_chdd_sim_digest or sim_result.result_digest,
            result=_chdd_result(npv=npv),
            execution=root,
        )

    return CandidateEvaluation(
        candidate=candidate, simulation=simulation, guardian=guardian, chdd=chdd
    )


def _rejection_for(result, candidate_identity: str):
    matches = [r for r in result.rejected if r.candidate_identity == candidate_identity]
    assert len(matches) == 1
    return matches[0]


# E1
def test_e1_valid_fully_evaluated_candidate_is_eligible() -> None:
    evaluation = _evaluation()
    result = EconomicSelector().select([evaluation])
    assert result.selected.candidate_identity == evaluation.candidate_identity
    assert result.eligible == (evaluation,)
    assert result.rejected == ()


# E2
def test_e2_unsimulated_candidate_cannot_be_eligible() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(bundle_digest="broken", npv=999.0, include_simulation=False)
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.NOT_SIMULATED


# E3
def test_e3_simulation_failure_cannot_be_eligible() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(
        bundle_digest="broken", npv=999.0, simulation_status=SimulationStatus.CRASHED
    )
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.SIMULATION_FAILED


# E4
def test_e4_missing_guardian_result_cannot_be_eligible() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(bundle_digest="broken", npv=999.0, include_guardian=False)
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.GUARDIAN_MISSING


# E5
def test_e5_guardian_fail_cannot_be_eligible() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(bundle_digest="broken", npv=999.0, guardian_failed=1)
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.GUARDIAN_FAILED


# E6
def test_e6_missing_chdd_cannot_be_eligible() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(bundle_digest="broken", include_chdd=False)
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.CHDD_MISSING


# E7
def test_e7_candidate_simulation_identity_mismatch_rejected() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(
        bundle_digest="broken", npv=999.0, override_sim_identity="not-this-candidate"
    )
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.SIMULATION_IDENTITY_MISMATCH


# E8
def test_e8_simulation_guardian_identity_mismatch_rejected() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(
        bundle_digest="broken", npv=999.0, override_guardian_sim_digest="not-this-simulation"
    )
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.GUARDIAN_IDENTITY_MISMATCH


# E9
def test_e9_guardian_chdd_identity_mismatch_rejected() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(
        bundle_digest="broken", npv=999.0, override_chdd_sim_digest="not-this-simulation"
    )
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.CHDD_IDENTITY_MISMATCH


# E10
def test_e10_highest_chdd_among_eligible_wins() -> None:
    low = _evaluation(bundle_digest="low", npv=10.0)
    mid = _evaluation(bundle_digest="mid", npv=50.0)
    high = _evaluation(bundle_digest="high", npv=90.0)
    result = EconomicSelector().select([mid, low, high])
    assert result.selected.candidate_identity == high.candidate_identity
    assert result.rejected == ()


# E11
def test_e11_guardian_failed_candidate_with_highest_chdd_still_loses() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    failed_but_richest = _evaluation(bundle_digest="richest", npv=1_000_000.0, guardian_failed=1)
    result = EconomicSelector().select([winner, failed_but_richest])
    assert result.selected.candidate_identity == winner.candidate_identity


# E12
def test_e12_hold_may_win() -> None:
    hold = _evaluation(kind=CandidateKind.HOLD, bundle_digest="hold", npv=90.0)
    other = _evaluation(kind=CandidateKind.AGENT_BUNDLE, bundle_digest="agent", npv=10.0)
    result = EconomicSelector().select([hold, other])
    assert result.selected.candidate_identity == hold.candidate_identity


# E13
def test_e13_deterministic_baseline_may_win() -> None:
    baseline = _evaluation(
        kind=CandidateKind.DETERMINISTIC_BASELINE, bundle_digest="baseline", npv=90.0
    )
    other = _evaluation(kind=CandidateKind.AGENT_BUNDLE, bundle_digest="agent", npv=10.0)
    result = EconomicSelector().select([baseline, other])
    assert result.selected.candidate_identity == baseline.candidate_identity


# E14
def test_e14_agent_bundle_may_win() -> None:
    agent = _evaluation(kind=CandidateKind.AGENT_BUNDLE, bundle_digest="agent", npv=90.0)
    other = _evaluation(kind=CandidateKind.HOLD, bundle_digest="hold", npv=10.0)
    result = EconomicSelector().select([agent, other])
    assert result.selected.candidate_identity == agent.candidate_identity


# E15
def test_e15_no_candidate_kind_preference_in_exact_tie() -> None:
    hold = _evaluation(kind=CandidateKind.HOLD, bundle_digest="hold", npv=100.0)
    agent = _evaluation(kind=CandidateKind.AGENT_BUNDLE, bundle_digest="agent", npv=100.0)
    result = EconomicSelector().select([hold, agent])
    expected = min(hold.candidate_identity, agent.candidate_identity)
    assert result.selected.candidate_identity == expected


# E16
def test_e16_input_reorder_does_not_change_winner_or_digest() -> None:
    a = _evaluation(bundle_digest="a", npv=10.0)
    b = _evaluation(bundle_digest="b", npv=50.0)
    c = _evaluation(bundle_digest="c", npv=30.0)
    forward = EconomicSelector().select([a, b, c])
    backward = EconomicSelector().select([c, b, a])
    assert forward.selected.candidate_identity == backward.selected.candidate_identity
    assert forward.digest() == backward.digest()


# E17
def test_e17_exact_tie_resolves_by_neutral_canonical_identity() -> None:
    x = _evaluation(bundle_digest="x", npv=42.0)
    y = _evaluation(bundle_digest="y", npv=42.0)
    result = EconomicSelector().select([x, y])
    assert result.selected.candidate_identity == min(x.candidate_identity, y.candidate_identity)


# E18
def test_e18_non_finite_economic_value_rejected() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(bundle_digest="broken", npv=math.nan)
    result = EconomicSelector().select([winner, broken])
    assert result.selected.candidate_identity == winner.candidate_identity
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.reason is IneligibilityReason.NON_FINITE_CHDD_VALUE
    # audit trail must stay serializable even though one candidate's CHDD was NaN.
    assert result.digest()


# E19
def test_e19_no_eligible_candidates_raises_typed_error() -> None:
    broken = _evaluation(include_simulation=False)
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([broken])


# E20
def test_e20_exactly_one_eligible_is_selected() -> None:
    only = _evaluation()
    result = EconomicSelector().select([only])
    assert result.selected.candidate_identity == only.candidate_identity


# E21
def test_e21_duplicate_inconsistent_candidate_identity_fails_closed() -> None:
    first = _evaluation(bundle_digest="dup", npv=10.0)
    second = _evaluation(bundle_digest="dup", npv=20.0)
    assert first.candidate_identity == second.candidate_identity
    with pytest.raises(DuplicateCandidateIdentityError):
        EconomicSelector().select([first, second])


# E22
def test_e22_duplicate_ordering_cannot_alter_result() -> None:
    winner = _evaluation(bundle_digest="winner", npv=90.0)
    duplicate_a = _evaluation(bundle_digest="dup", npv=10.0)
    duplicate_b = _evaluation(bundle_digest="dup", npv=10.0)
    assert duplicate_a == duplicate_b
    forward = EconomicSelector().select([winner, duplicate_a, duplicate_b])
    backward = EconomicSelector().select([duplicate_b, duplicate_a, winner])
    assert forward.digest() == backward.digest()
    assert forward.selected.candidate_identity == winner.candidate_identity


# E23
def test_e23_rejected_candidates_remain_auditable() -> None:
    winner = _evaluation(bundle_digest="winner", npv=10.0)
    broken = _evaluation(bundle_digest="broken", include_chdd=False)
    result = EconomicSelector().select([winner, broken])
    rejection = _rejection_for(result, broken.candidate_identity)
    assert rejection.candidate_kind is CandidateKind.AGENT_BUNDLE
    assert "CHDD" in rejection.detail or "chdd" in rejection.detail.lower()


# E24
def test_e24_same_input_gives_identical_deterministic_digest() -> None:
    evaluations = [
        _evaluation(bundle_digest="a", npv=10.0),
        _evaluation(bundle_digest="b", npv=20.0),
    ]
    first = EconomicSelector().select(evaluations)
    second = EconomicSelector().select(evaluations)
    assert first.digest() == second.digest()
