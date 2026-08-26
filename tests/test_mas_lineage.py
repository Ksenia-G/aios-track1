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
    OutcomeLineageError,
    SimulationOutcome,
    VerifiedChddInput,
    VerifiedExecution,
    VerifiedRealizedState,
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
        guardian=GuardianOutcome.verified(_realized(verified), _report()),
        chdd=ChddOutcome.verified(_chdd_input(verified), chdd_result(9.0, input_digest="chdd-A")),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


# ---------------------------------------------------------------------------
# Guardian and CHDD lineage.
# ---------------------------------------------------------------------------


def _report(*, failed: int = 0, state_card_digest: str = "state-A") -> ConstraintReport:
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
        metadata={"state_card_digest": state_card_digest, "evaluation_version": "test/1.0.0"},
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


def _realized(execution: VerifiedExecution, *, state: str = "state-A") -> VerifiedRealizedState:
    return VerifiedRealizedState(execution=execution, state_card_digest=state)


def _chdd_input(execution: VerifiedExecution, *, chdd: str = "chdd-A") -> VerifiedChddInput:
    return VerifiedChddInput(execution=execution, input_digest=chdd)


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
        guardian=GuardianOutcome.verified(_realized(root), _report()),
        chdd=ChddOutcome.verified(_chdd_input(root), chdd_result(5.0, input_digest="chdd-A")),
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
        guardian=GuardianOutcome.verified(
            _realized(root_b, state="state-B") if swapped == "guardian" else _realized(root_a),
            _report(state_card_digest="state-B" if swapped == "guardian" else "state-A"),
        ),
        chdd=ChddOutcome.verified(
            _chdd_input(root_b, chdd="chdd-B") if swapped == "chdd" else _chdd_input(root_a),
            chdd_result(5.0, input_digest="chdd-B" if swapped == "chdd" else "chdd-A"),
        ),
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
        chdd=ChddOutcome.verified(_chdd_input(root_a), chdd_result(5.0, input_digest="chdd-A")),
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
        guardian=GuardianOutcome.verified(_realized(drifted), _report()),
        chdd=ChddOutcome.verified(_chdd_input(root), chdd_result(5.0, input_digest="chdd-A")),
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
        chdd=ChddOutcome.verified(_chdd_input(root), chdd_result(5.0, input_digest="chdd-A")),
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
        guardian=GuardianOutcome.verified(_realized(root), _report()),
        chdd=ChddOutcome.verified(_chdd_input(root), chdd_result(npv, input_digest="chdd-A")),
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
        guardian=GuardianOutcome.verified(
            _realized(root_b, state="state-B"), _report(state_card_digest="state-B")
        ),
        chdd=ChddOutcome.verified(_chdd_input(root_a), chdd_result(5.0, input_digest="chdd-A")),
    )
    result = EconomicSelector().select([mismatched, _eligible(B, 3.0)])
    assert _reason(result, A.canonical().digest()) == str(
        IneligibilityReason.EXECUTION_LINEAGE_MISMATCH
    )


# ---------------------------------------------------------------------------
# Payload lineage: a real verdict about one run cannot be worn by another.
#
# The execution root alone never proved this. A ConstraintReport genuinely
# produced by the Guardian for candidate B, or a ChddResult genuinely computed
# from B's trajectory, could be wrapped beside A's root and every field of the
# wrapper agreed with itself. What settles it is the input each payload names.
# ---------------------------------------------------------------------------


def _real_report(card) -> ConstraintReport:
    from aios.agents.constraint_guardian import ConstraintGuardian

    return ConstraintGuardian().evaluate(card)


def _cards() -> tuple[object, object]:
    """Two genuinely different realized StateCards."""
    from tests.support.mas_team_fixtures import synthetic_state_card

    card_a = synthetic_state_card()
    card_b = dataclasses.replace(
        card_a,
        wells=tuple(
            dataclasses.replace(well, wlpr=(well.wlpr or 0.0) + 7.0) for well in card_a.wells
        ),
    )
    return card_a, card_b


def test_guardian_g1_report_for_the_state_we_judged_is_accepted() -> None:
    card_a, _ = _cards()
    root = _verified(A)
    outcome = GuardianOutcome.verified(VerifiedRealizedState.of(root, card_a), _real_report(card_a))
    assert outcome.state_card_digest == card_a.digest()


def test_guardian_g2_real_report_from_another_run_is_refused() -> None:
    """The exploit: Guardian(B)'s genuine verdict presented for A."""
    card_a, card_b = _cards()
    root = _verified(A)
    with pytest.raises(OutcomeLineageError, match="computed on state"):
        GuardianOutcome.verified(VerifiedRealizedState.of(root, card_a), _real_report(card_b))


def test_guardian_g3_different_realized_state_digest_is_refused() -> None:
    card_a, card_b = _cards()
    root = _verified(A)
    with pytest.raises(OutcomeLineageError, match="computed on state"):
        GuardianOutcome.verified(VerifiedRealizedState.of(root, card_b), _real_report(card_a))


def test_guardian_g4_report_without_state_identity_cannot_be_authoritative() -> None:
    root = _verified(A)
    unbound = dataclasses.replace(_report(), metadata={"evaluation_version": "test/1.0.0"})
    assert unbound.state_card_digest is None
    with pytest.raises(OutcomeLineageError, match="no state_card_digest"):
        GuardianOutcome.verified(_realized(root), unbound)


def test_guardian_g5_same_card_but_another_execution_root_is_ineligible() -> None:
    card_a, _ = _cards()
    root_a, root_b = _verified(A), _verified(B)
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root_a, _bound_result(A)),
        guardian=GuardianOutcome.verified(
            VerifiedRealizedState.of(root_b, card_a), _real_report(card_a)
        ),
        chdd=ChddOutcome.verified(_chdd_input(root_a), chdd_result(5.0, input_digest="chdd-A")),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


def test_chdd_c1_result_for_the_input_we_evaluated_is_accepted() -> None:
    root = _verified(A)
    outcome = ChddOutcome.verified(
        VerifiedChddInput(execution=root, input_digest="chdd-A"),
        chdd_result(5.0, input_digest="chdd-A"),
    )
    assert outcome.input_digest == "chdd-A"


def test_chdd_c2_real_result_from_another_input_is_refused() -> None:
    """The exploit: a genuine ChddResult computed from B's trajectory."""
    root = _verified(A)
    with pytest.raises(OutcomeLineageError, match="computed on input"):
        ChddOutcome.verified(
            VerifiedChddInput(execution=root, input_digest="chdd-A"),
            chdd_result(999.0, input_digest="chdd-B"),
        )


def test_chdd_c4_wrong_input_digest_is_refused() -> None:
    root = _verified(A)
    with pytest.raises(OutcomeLineageError, match="computed on input"):
        ChddOutcome.verified(
            VerifiedChddInput(execution=root, input_digest="chdd-B"),
            chdd_result(5.0, input_digest="chdd-A"),
        )


def test_chdd_c5_result_without_input_identity_cannot_be_authoritative() -> None:
    root = _verified(A)
    assert chdd_result(5.0).input_digest is None
    with pytest.raises(OutcomeLineageError, match="no input_digest"):
        ChddOutcome.verified(
            VerifiedChddInput(execution=root, input_digest="chdd-A"), chdd_result(5.0)
        )


def test_chdd_c3_same_candidate_different_request_is_ineligible() -> None:
    root_a, root_b = _verified(A), _verified(B)
    drifted = dataclasses.replace(root_a, request_digest=root_b.request_digest)
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root_a, _bound_result(A)),
        guardian=GuardianOutcome.verified(_realized(root_a), _report()),
        chdd=ChddOutcome.verified(
            VerifiedChddInput(execution=drifted, input_digest="chdd-A"),
            chdd_result(5.0, input_digest="chdd-A"),
        ),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([evaluation])


def test_selector_refuses_a_guardian_outcome_whose_payload_drifted() -> None:
    """Even hand-built wrappers cannot desync payload from binding."""
    card_a, card_b = _cards()
    root = _verified(A)
    smuggled = GuardianOutcome(
        candidate_identity=root.candidate_digest,
        simulation_result_digest=root.result_digest,
        report=_real_report(card_b),
        execution=root,
        state_card_digest=card_a.digest(),
    )
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root, _bound_result(A)),
        guardian=smuggled,
        chdd=ChddOutcome.verified(_chdd_input(root), chdd_result(5.0, input_digest="chdd-A")),
    )
    result = EconomicSelector().select([evaluation, _eligible(B, 1.0)])
    assert _reason(result, A.canonical().digest()) == str(
        IneligibilityReason.EXECUTION_LINEAGE_MISMATCH
    )


def test_selector_refuses_a_chdd_outcome_whose_payload_drifted() -> None:
    root = _verified(A)
    smuggled = ChddOutcome(
        candidate_identity=root.candidate_digest,
        simulation_result_digest=root.result_digest,
        result=chdd_result(999.0, input_digest="chdd-B"),
        execution=root,
        input_digest="chdd-A",
    )
    evaluation = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome.verified(root, _bound_result(A)),
        guardian=GuardianOutcome.verified(_realized(root), _report()),
        chdd=smuggled,
    )
    result = EconomicSelector().select([evaluation, _eligible(B, 1.0)])
    assert _reason(result, A.canonical().digest()) == str(
        IneligibilityReason.EXECUTION_LINEAGE_MISMATCH
    )
