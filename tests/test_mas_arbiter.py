"""Arbiter structural contracts: Round2Request, DecisionBundle, ArbiterDecision."""

from __future__ import annotations

import dataclasses

import pytest

from aios.mas.arbiter import ArbiterDecision, ArbiterError, DecisionBundle, Round2Request
from aios.mas.common import AgentRole, CandidateKind


def _hold_bundle() -> DecisionBundle:
    return DecisionBundle(kind=CandidateKind.HOLD)


def _baseline_bundle() -> DecisionBundle:
    return DecisionBundle(
        kind=CandidateKind.DETERMINISTIC_BASELINE,
        production_proposal_digest="a" * 64,
        injection_proposal_digest="b" * 64,
    )


def _agent_bundle() -> DecisionBundle:
    return DecisionBundle(
        kind=CandidateKind.AGENT_BUNDLE,
        production_proposal_digest="c" * 64,
        production_intent_digests=("d" * 64,),
        priority_rank=1,
    )


# ---------------------------------------------------------------------------
# C27: Round2Request rejects AgentRole.ARBITER.
# ---------------------------------------------------------------------------


def test_round2_request_accepts_production_and_injection_roles() -> None:
    request = Round2Request(
        requested_roles=(AgentRole.PRODUCTION, AgentRole.INJECTION),
        conflict_code="OVERLAPPING_WELL_TARGET",
        proposal_digests=("a" * 64,),
    )
    assert request.requested_roles == (AgentRole.PRODUCTION, AgentRole.INJECTION)


def test_round2_request_rejects_arbiter_role() -> None:
    with pytest.raises(ArbiterError):
        Round2Request(
            requested_roles=(AgentRole.ARBITER,),
            conflict_code="X",
            proposal_digests=(),
        )


def test_round2_request_rejects_duplicate_roles() -> None:
    with pytest.raises(ArbiterError):
        Round2Request(
            requested_roles=(AgentRole.PRODUCTION, AgentRole.PRODUCTION),
            conflict_code="X",
            proposal_digests=(),
        )


def test_round2_request_rejects_empty_roles() -> None:
    with pytest.raises(ArbiterError):
        Round2Request(requested_roles=(), conflict_code="X", proposal_digests=())


# ---------------------------------------------------------------------------
# DecisionBundle semantics.
# ---------------------------------------------------------------------------


def test_hold_bundle_carries_no_references() -> None:
    bundle = _hold_bundle()
    assert bundle.production_proposal_digest is None
    assert bundle.injection_proposal_digest is None
    assert bundle.production_intent_digests == ()


def test_hold_bundle_rejects_proposal_reference() -> None:
    with pytest.raises(ArbiterError):
        DecisionBundle(kind=CandidateKind.HOLD, production_proposal_digest="a" * 64)


def test_agent_bundle_requires_at_least_one_reference() -> None:
    with pytest.raises(ArbiterError):
        DecisionBundle(kind=CandidateKind.AGENT_BUNDLE)
    _agent_bundle()


def test_decision_bundle_rejects_negative_priority_rank() -> None:
    with pytest.raises(ArbiterError):
        DecisionBundle(kind=CandidateKind.HOLD, priority_rank=-1)


# ---------------------------------------------------------------------------
# C24 / C25: ArbiterDecision requires HOLD and DETERMINISTIC_BASELINE.
# ---------------------------------------------------------------------------


def test_arbiter_decision_requires_hold_candidate() -> None:
    with pytest.raises(ArbiterError):
        ArbiterDecision(
            schema_version="aios.mas.contracts/1.0.0",
            arbiter_envelope_digest="e" * 64,
            bundles=(_baseline_bundle(), _agent_bundle()),
        )


def test_arbiter_decision_requires_deterministic_baseline_candidate() -> None:
    with pytest.raises(ArbiterError):
        ArbiterDecision(
            schema_version="aios.mas.contracts/1.0.0",
            arbiter_envelope_digest="e" * 64,
            bundles=(_hold_bundle(), _agent_bundle()),
        )


def test_arbiter_decision_accepts_minimal_valid_candidate_set() -> None:
    decision = ArbiterDecision(
        schema_version="aios.mas.contracts/1.0.0",
        arbiter_envelope_digest="e" * 64,
        bundles=(_hold_bundle(), _baseline_bundle()),
    )
    assert len(decision.bundles) == 2


def test_arbiter_decision_accepts_optional_round2_request() -> None:
    decision = ArbiterDecision(
        schema_version="aios.mas.contracts/1.0.0",
        arbiter_envelope_digest="e" * 64,
        bundles=(_hold_bundle(), _baseline_bundle()),
        round2_request=Round2Request(
            requested_roles=(AgentRole.PRODUCTION,),
            conflict_code="OVERLAPPING_WELL_TARGET",
            proposal_digests=("a" * 64,),
        ),
    )
    assert decision.round2_request is not None


# ---------------------------------------------------------------------------
# C26: ArbiterDecision contains no ProducerControl/InjectorControl fields.
# ---------------------------------------------------------------------------


def test_arbiter_decision_has_no_numerical_control_fields() -> None:
    for cls in (ArbiterDecision, DecisionBundle):
        field_names = {f.name.lower() for f in dataclasses.fields(cls)}
        assert "producers" not in field_names
        assert "injectors" not in field_names
        assert "producer_controls" not in field_names
        assert "injector_controls" not in field_names


def test_arbiter_decision_digest_is_deterministic() -> None:
    decision = ArbiterDecision(
        schema_version="aios.mas.contracts/1.0.0",
        arbiter_envelope_digest="e" * 64,
        bundles=(_hold_bundle(), _baseline_bundle()),
    )
    other = ArbiterDecision(
        schema_version="aios.mas.contracts/1.0.0",
        arbiter_envelope_digest="e" * 64,
        bundles=(_hold_bundle(), _baseline_bundle()),
    )
    assert decision.digest() == other.digest()
