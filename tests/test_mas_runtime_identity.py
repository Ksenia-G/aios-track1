"""Runtime execution identity: a foreign run must never pass as ours.

`provenance.candidate_digest` is a label the executor writes. On its own it
proves nothing: an executor returning another candidate's request and physical
result can simply stamp the requested candidate's digest on it. Identity is
therefore established by recomputation from the actual objects.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from aios.evaluation.simulation import (
    ResultDigestMismatchError,
    SimulationRequest,
    SimulationResult,
    SimulationStatus,
)
from aios.execution import RuntimeExecution
from aios.mas.common import CandidateKind
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.orchestration.orchestrator import (
    OrchestrationError,
    _require_execution_identity,
)
from aios.schedule import InjectorControl, ProducerControl, WellStatus
from tests.support.orchestration_doubles import build_provenance, build_request


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
        resolver_version="tests.identity/1.0.0",
    )


A = _candidate("A-bundle", rate=200.0)
B = _candidate("B-bundle", rate=900.0)


def _result(candidate: MagnitudeCandidate) -> SimulationResult:
    return SimulationResult.create(
        status=SimulationStatus.SUCCESS,
        metadata={"candidate_digest": candidate.canonical().digest()},
    )


def test_case1_matching_request_provenance_and_result_is_accepted() -> None:
    request = build_request(A)
    _require_execution_identity(
        A, RuntimeExecution(request, _result(A), build_provenance(A, request))
    )


def test_case2_foreign_request_and_result_relabelled_as_ours_is_rejected() -> None:
    """The exact exploit: B's whole execution, wearing A's name."""
    foreign_request = build_request(B)
    execution = RuntimeExecution(
        foreign_request,
        _result(B),
        build_provenance(B, foreign_request, stamped_digest=A.canonical().digest()),
    )
    with pytest.raises(OrchestrationError, match="does not belong to the executed candidate"):
        _require_execution_identity(A, execution)


def test_case3_our_request_with_foreign_provenance_is_rejected() -> None:
    foreign_request = build_request(B)
    execution = RuntimeExecution(build_request(A), _result(A), build_provenance(B, foreign_request))
    with pytest.raises(OrchestrationError, match="does not belong to the executed candidate"):
        _require_execution_identity(A, execution)


def test_case4_wholly_foreign_execution_is_rejected() -> None:
    foreign_request = build_request(B)
    execution = RuntimeExecution(foreign_request, _result(B), build_provenance(B, foreign_request))
    with pytest.raises(OrchestrationError, match="does not belong to the executed candidate"):
        _require_execution_identity(A, execution)


def test_case5_provenance_naming_a_different_schedule_is_rejected() -> None:
    request = build_request(A)
    execution = RuntimeExecution(
        request,
        _result(A),
        build_provenance(A, request, stamped_schedule_digest="f" * 64),
    )
    with pytest.raises(OrchestrationError, match="schedule_sha256"):
        _require_execution_identity(A, execution)


def test_case6_simulation_key_must_recompute_from_the_actual_request() -> None:
    """A key that was not derived from this request cannot be forged."""
    request = build_request(A)
    execution = RuntimeExecution(
        request, _result(A), build_provenance(A, request, simulation_key="0" * 64)
    )
    with pytest.raises(OrchestrationError, match="simulation_key"):
        _require_execution_identity(A, execution)


def test_case6b_tampered_result_content_cannot_even_be_constructed() -> None:
    """The frozen SimulationResult contract refuses a stale result_digest."""
    with pytest.raises(ResultDigestMismatchError):
        dataclasses.replace(_result(A), metadata={"candidate_digest": B.canonical().digest()})


def test_case7_missing_request_identity_material_is_rejected() -> None:
    request = build_request(A)
    stripped = SimulationRequest(
        payload={},
        seed=None,
        model_digest=None,
        scenario_digest=A.canonical().bundle_digest,
        schedule_digest=request.schedule_digest,
    )
    execution = RuntimeExecution(stripped, _result(A), build_provenance(A, stripped))
    with pytest.raises(OrchestrationError, match="candidate_digest"):
        _require_execution_identity(A, execution)


def test_scenario_digest_must_match_the_candidate_bundle() -> None:
    request = build_request(A)
    swapped = dataclasses.replace(request, scenario_digest=B.canonical().bundle_digest)
    execution = RuntimeExecution(swapped, _result(A), build_provenance(A, swapped))
    with pytest.raises(OrchestrationError, match="scenario_digest"):
        _require_execution_identity(A, execution)


def test_non_runtime_execution_is_rejected() -> None:
    with pytest.raises(OrchestrationError, match="expected RuntimeExecution"):
        _require_execution_identity(A, object())  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# B7 -- downstream lineage: Guardian and CHDD outcomes cannot be swapped.
# ---------------------------------------------------------------------------


def test_guardian_outcome_from_another_candidate_is_not_eligible() -> None:
    """A verdict about B must not make A commit-eligible."""
    from aios.agents.constraint_guardian import ConstraintGuardian
    from aios.mas.selection import (
        CandidateEvaluation,
        ChddOutcome,
        EconomicSelector,
        GuardianOutcome,
        NoCommitEligibleCandidateError,
        SimulationOutcome,
    )
    from tests.support.mas_team_fixtures import synthetic_state_card
    from tests.support.orchestration_doubles import chdd_result

    report = ConstraintGuardian().evaluate(synthetic_state_card())
    result_a = _result(A)
    identity_a = A.canonical().digest()
    identity_b = B.canonical().digest()

    swapped = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome(candidate_identity=identity_a, result=result_a),
        # Guardian verdict recorded against candidate B.
        guardian=GuardianOutcome(
            candidate_identity=identity_b,
            simulation_result_digest=result_a.result_digest,
            report=report,
        ),
        chdd=ChddOutcome(
            candidate_identity=identity_a,
            simulation_result_digest=result_a.result_digest,
            result=chdd_result(9.0),
        ),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([swapped])


def test_chdd_outcome_from_another_candidate_is_not_eligible() -> None:
    from aios.agents.constraint_guardian import ConstraintGuardian
    from aios.mas.selection import (
        CandidateEvaluation,
        ChddOutcome,
        EconomicSelector,
        GuardianOutcome,
        NoCommitEligibleCandidateError,
        SimulationOutcome,
    )
    from tests.support.mas_team_fixtures import synthetic_state_card
    from tests.support.orchestration_doubles import chdd_result

    report = ConstraintGuardian().evaluate(synthetic_state_card())
    result_a = _result(A)
    identity_a = A.canonical().digest()

    swapped = CandidateEvaluation(
        candidate=A,
        simulation=SimulationOutcome(candidate_identity=identity_a, result=result_a),
        guardian=GuardianOutcome(
            candidate_identity=identity_a,
            simulation_result_digest=result_a.result_digest,
            report=report,
        ),
        # Economics computed over a different simulation result.
        chdd=ChddOutcome(
            candidate_identity=identity_a,
            simulation_result_digest=_result(B).result_digest,
            result=chdd_result(9.0),
        ),
    )
    with pytest.raises(NoCommitEligibleCandidateError):
        EconomicSelector().select([swapped])


def test_orchestrator_sources_every_outcome_identity_from_the_validated_run() -> None:
    """Identity is read off the execution, never written by the caller."""
    import inspect

    from aios.mas.orchestration import orchestrator as module

    source = inspect.getsource(module.ClosedLoopOrchestrator._simulate_guard_and_commit)
    assert "identity = execution.provenance.candidate_digest" in source
    for outcome in ("SimulationOutcome(", "GuardianOutcome(", "ChddOutcome("):
        assert outcome in source
    # No outcome is built from a literal or a caller-supplied label.
    assert "candidate_identity=identity" in source
