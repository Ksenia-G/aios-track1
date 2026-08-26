"""Authoritative lineage: candidate -> request -> result -> Guardian -> CHDD.

A physically honest simulation result describes itself correctly no matter
which run produced it, so self-consistency proves nothing about ownership.
These tests pin the one property that does: every downstream verdict must
trace back to the same verified execution root.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from aios.agents.constraint_guardian import ConstraintCheck, ConstraintReport, ConstraintSummary
from aios.evaluation.simulation import (
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
    compute_request_digest,
    compute_result_digest,
)
from aios.execution import RuntimeExecution
from aios.mas.common import CandidateKind
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.orchestration.orchestrator import (
    OrchestrationError,
    _require_execution_identity,
)
from aios.mas.selection import (
    CandidateEvaluation,
    ChddOutcome,
    EconomicSelector,
    GuardianOutcome,
    IneligibilityReason,
    NoCommitEligibleCandidateError,
    SimulationOutcome,
    VerifiedExecution,
)
from aios.schedule import InjectorControl, ProducerControl, WellStatus
from tests.support.orchestration_doubles import (
    build_provenance,
    build_request,
    chdd_result,
    request_binding,
)


def _candidate(bundle: str, *, rate: float) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=CandidateKind.AGENT_BUNDLE,
        bundle_digest=bundle.ljust(64, "0"),
        effective_date=datetime.date(2020, 4, 1),
        producers=(
            ProducerControl(well="SYN-P1", status=WellStatus.OPEN, mode="LRAT", liquid_rate=rate),
        ),
        injectors=(
            InjectorControl(
                well="SYN-I1",
                phase="WATER",
                status=WellStatus.OPEN,
                mode="RATE",
                surface_rate=150.0,
            ),
        ),
        source_intent_digests=("d" * 64,),
        resolver_version="tests.lineage/1.0.0",
    )


A = _candidate("A-bundle", rate=200.0)
B = _candidate("B-bundle", rate=900.0)


def _bound_result(
    candidate: MagnitudeCandidate, *, status: SimulationStatus = SimulationStatus.SUCCESS
) -> SimulationResult:
    return SimulationResult.create(
        status=status,
        metadata={"candidate_digest": candidate.canonical().digest()},
        **request_binding(build_request(candidate)),
    )


def _execution(
    candidate: MagnitudeCandidate,
    *,
    request: SimulationRequest | None = None,
    result: SimulationResult | None = None,
    provenance_of: MagnitudeCandidate | None = None,
) -> RuntimeExecution:
    resolved_request = request if request is not None else build_request(candidate)
    provenance_source = provenance_of if provenance_of is not None else candidate
    return RuntimeExecution(
        request=resolved_request,
        result=result if result is not None else _bound_result(candidate),
        provenance=build_provenance(provenance_source, build_request(provenance_source)),
    )


# ---------------------------------------------------------------------------
# Runtime result binding.
# ---------------------------------------------------------------------------


def test_r1_fully_consistent_execution_is_accepted() -> None:
    verified = _require_execution_identity(A, _execution(A))
    assert verified.candidate_digest == A.canonical().digest()
    assert verified.request_digest == compute_request_digest(build_request(A))
    assert verified.result_digest == _bound_result(A).result_digest


def test_r2_legitimate_result_from_another_run_is_rejected() -> None:
    """The blocker: request A, provenance A, but B's own honest result.

    B's result is internally perfect -- it simply belongs to a different run.
    """
    execution = _execution(A, result=_bound_result(B))
    with pytest.raises(OrchestrationError, match="result.request_digest"):
        _require_execution_identity(A, execution)


def test_r3_foreign_request_with_our_provenance_is_rejected() -> None:
    execution = _execution(A, request=build_request(B), result=_bound_result(B))
    with pytest.raises(OrchestrationError, match="does not belong to the executed candidate"):
        _require_execution_identity(A, execution)


def test_r4_foreign_provenance_is_rejected() -> None:
    execution = _execution(A, provenance_of=B)
    with pytest.raises(OrchestrationError, match="does not belong to the executed candidate"):
        _require_execution_identity(A, execution)


def test_r5_forged_result_request_digest_is_rejected() -> None:
    forged = SimulationResult.create(
        status=SimulationStatus.SUCCESS,
        metadata={"candidate_digest": A.canonical().digest()},
        request_digest=compute_request_digest(build_request(B)),
        simulation_key=request_binding(build_request(A))["simulation_key"],
    )
    with pytest.raises(OrchestrationError, match="result.request_digest"):
        _require_execution_identity(A, _execution(A, result=forged))


def test_r6_forged_result_simulation_key_is_rejected() -> None:
    forged = SimulationResult.create(
        status=SimulationStatus.SUCCESS,
        metadata={"candidate_digest": A.canonical().digest()},
        request_digest=request_binding(build_request(A))["request_digest"],
        simulation_key="0" * 64,
    )
    with pytest.raises(OrchestrationError, match="result.simulation_key"):
        _require_execution_identity(A, _execution(A, result=forged))


def test_r7_result_digest_must_cover_the_binding() -> None:
    """Re-pointing a result at another request must change its digest."""
    request_a = build_request(A)
    request_b = build_request(B)
    common = {
        "status": SimulationStatus.SUCCESS,
        "trajectory_ref": None,
        "metadata": {"candidate_digest": A.canonical().digest()},
        "failure": None,
    }
    digest_a = compute_result_digest(**common, **request_binding(request_a))
    digest_b = compute_result_digest(**common, **request_binding(request_b))
    assert digest_a != digest_b

    # A stale digest cannot survive construction at all.
    bound = _bound_result(A)
    with pytest.raises(Exception, match="result_digest"):
        dataclasses.replace(bound, request_digest=compute_request_digest(request_b))


def test_r8_result_without_request_binding_is_rejected() -> None:
    unbound = SimulationResult.create(
        status=SimulationStatus.SUCCESS,
        metadata={"candidate_digest": A.canonical().digest()},
    )
    assert unbound.is_request_bound is False
    with pytest.raises(OrchestrationError, match="carries no request binding"):
        _require_execution_identity(A, _execution(A, result=unbound))


def test_r9_failed_run_with_valid_lineage_is_never_economically_eligible() -> None:
    crashed = _bound_result(A, status=SimulationStatus.CRASHED)
    verified = _require_execution_identity(A, _execution(A, result=crashed))
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(verified, crashed),
        guardian=GuardianOutcome.verified(verified, _report()),
        chdd=ChddOutcome.verified(verified, chdd_result(9.0)),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


# ---------------------------------------------------------------------------
# Guardian and CHDD lineage.
# ---------------------------------------------------------------------------


def _report(*, failed: int = 0) -> ConstraintReport:
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
        metadata={"state_card_digest": "sc", "evaluation_version": "test/1.0.0"},
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


def _verified(candidate: MagnitudeCandidate) -> VerifiedExecution:
    return _require_execution_identity(candidate, _execution(candidate))


def _reason(result_or_error, identity: str) -> str:
    return next(
        str(rejection.reason)
        for rejection in result_or_error.rejected
        if rejection.candidate_identity == identity
    )


def test_g1_matching_guardian_lineage_is_eligible() -> None:
    root = _verified(A)
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root, _bound_result(A)),
        guardian=GuardianOutcome.verified(root, _report()),
        chdd=ChddOutcome.verified(root, chdd_result(5.0)),
    )
    assert (
        EconomicSelector().select([evaluation]).selected.candidate_identity == root.candidate_digest
    )


@pytest.mark.parametrize("swapped", ["guardian", "chdd"])
def test_g2_c2_outcome_from_another_run_is_ineligible(swapped: str) -> None:
    """A verdict computed for B cannot be worn by A, however it is labelled."""
    root_a, root_b = _verified(A), _verified(B)
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root_a, _bound_result(A)),
        guardian=GuardianOutcome.verified(root_b if swapped == "guardian" else root_a, _report()),
        chdd=ChddOutcome.verified(root_b if swapped == "chdd" else root_a, chdd_result(5.0)),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


def test_g3_relabelled_outcome_cannot_pass_as_ours() -> None:
    """Labels say A everywhere; the execution roots disagree."""
    root_a, root_b = _verified(A), _verified(B)
    relabelled = GuardianOutcome(
        candidate_identity=root_a.candidate_digest,
        simulation_result_digest=root_a.result_digest,
        report=_report(),
        execution=root_b,
    )
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root_a, _bound_result(A)),
        guardian=relabelled,
        chdd=ChddOutcome.verified(root_a, chdd_result(5.0)),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


@pytest.mark.parametrize("field", ["request_digest", "simulation_key", "result_digest"])
def test_g4_g5_c3_c4_any_root_field_divergence_is_ineligible(field: str) -> None:
    root = _verified(A)
    drifted = dataclasses.replace(root, **{field: "f" * 64})
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root, _bound_result(A)),
        guardian=GuardianOutcome.verified(drifted, _report()),
        chdd=ChddOutcome.verified(root, chdd_result(5.0)),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


def test_outcome_without_a_verified_root_is_ineligible() -> None:
    root = _verified(A)
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root, _bound_result(A)),
        guardian=GuardianOutcome(
            candidate_identity=root.candidate_digest,
            simulation_result_digest=root.result_digest,
            report=_report(),
        ),
        chdd=ChddOutcome.verified(root, chdd_result(5.0)),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


# ---------------------------------------------------------------------------
# Economic eligibility.
# ---------------------------------------------------------------------------


def _eligible(candidate: MagnitudeCandidate, npv: float) -> CandidateEvaluation:
    root = _verified(candidate)
    return CandidateEvaluation(
        candidate=candidate,
        simulation=SimulationOutcome.verified(root, _bound_result(candidate)),
        guardian=GuardianOutcome.verified(root, _report()),
        chdd=ChddOutcome.verified(root, chdd_result(npv)),
    )


def test_e1_e5_two_valid_candidates_are_both_eligible_and_argmax_holds() -> None:
    result = EconomicSelector().select([_eligible(A, 1.0), _eligible(B, 7.0)])
    assert len(result.eligible) == 2
    assert result.selected.candidate_identity == B.canonical().digest()
    assert result.rejected == ()


def test_e4_uniform_labels_over_different_roots_are_ineligible() -> None:
    """Every wrapper says A; the underlying executions are A and B."""
    root_a, root_b = _verified(A), _verified(B)
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome(
            candidate_identity=root_a.candidate_digest,
            result=_bound_result(A),
            execution=root_a,
        ),
        guardian=GuardianOutcome(
            candidate_identity=root_a.candidate_digest,
            simulation_result_digest=root_a.result_digest,
            report=_report(),
            execution=root_b,
        ),
        chdd=ChddOutcome(
            candidate_identity=root_a.candidate_digest,
            simulation_result_digest=root_a.result_digest,
            result=chdd_result(9.0),
            execution=root_b,
        ),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


def test_e6_tie_break_and_finiteness_are_unchanged() -> None:
    result = EconomicSelector().select([_eligible(A, 5.0), _eligible(B, 5.0)])
    identities = sorted((A.canonical().digest(), B.canonical().digest()))
    assert result.selected.candidate_identity == identities[0]


def test_lineage_rejections_are_reported_with_a_typed_reason() -> None:
    root_a, root_b = _verified(A), _verified(B)
    mismatched = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root_a, _bound_result(A)),
        guardian=GuardianOutcome.verified(root_b, _report()),
        chdd=ChddOutcome.verified(root_a, chdd_result(5.0)),
    )
    result = EconomicSelector().select([mismatched, _eligible(B, 3.0)])
    assert _reason(result, A.canonical().digest()) == str(
        IneligibilityReason.EXECUTION_LINEAGE_MISMATCH
    )
