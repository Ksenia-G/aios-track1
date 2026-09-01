"""DeterministicArbiter: production-ready deterministic arbitration core.

Covers frozen-contract test cases A1-A24. A25-A36 (import authority, no
duplicated types) live in tests/test_arbiter_authority.py.
"""

from __future__ import annotations

import dataclasses

import pytest

from aios.mas.arbiter import ArbiterDecision, ArbiterError, Round2Request
from aios.mas.arbitration.engine import ArbitrationError, DeterministicArbiter
from aios.mas.arbitration.policy import ConflictPolicy, NoRound2ConflictPolicy
from aios.mas.common import (
    AgentRole,
    CandidateKind,
    Direction,
    Intensity,
    ProposalSource,
    UncertaintyLevel,
)
from aios.mas.envelopes import ArbiterEnvelope
from aios.mas.evidence import SimulationBudget
from aios.mas.intents import (
    InjectionAction,
    InjectionIntent,
    ProductionAction,
    ProductionIntent,
)
from aios.mas.proposals import (
    GroundingReport,
    InjectionAgentOutput,
    InjectionProposal,
    ProductionAgentOutput,
    ProductionProposal,
    ProposalAudit,
    ProposalProvenance,
)
from tests.support.mas_team_fixtures import synthetic_arbiter_envelope

SCHEMA_VERSION = "aios.mas.contracts/1.0.0"


def _production_hold() -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        target_wells=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )


def _production_increase(
    *, target_wells: tuple[str, ...] = ("P1",), rationale_short: str = "Healthy margin."
) -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.INCREASE_OFFTAKE,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        target_wells=target_wells,
        target_regions=(),
        evidence_ids=("ev-1",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short=rationale_short,
    )


def _injection_hold() -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )


def _injection_support(
    *, recipient_wells: tuple[str, ...] = ("I1",), rationale_short: str = "Support weak offset."
) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.SUPPORT_REGION,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        donor_wells=(),
        recipient_wells=recipient_wells,
        source_regions=(),
        target_regions=(),
        evidence_ids=("ev-2",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short=rationale_short,
    )


def _production_proposal(
    *,
    envelope_digest: str,
    intents: tuple[ProductionIntent, ...],
    source: ProposalSource,
    audit: ProposalAudit | None = None,
    provenance: ProposalProvenance | None = None,
) -> ProductionProposal:
    return ProductionProposal(
        schema_version=SCHEMA_VERSION,
        envelope_digest=envelope_digest,
        source=source,
        intents=intents,
        grounding_report=GroundingReport(
            grounded_evidence_ids=tuple(eid for intent in intents for eid in intent.evidence_ids)
        ),
        provenance=provenance or ProposalProvenance(),
        audit=audit or ProposalAudit(),
    )


def _injection_proposal(
    *,
    envelope_digest: str,
    intents: tuple[InjectionIntent, ...],
    source: ProposalSource,
    audit: ProposalAudit | None = None,
    provenance: ProposalProvenance | None = None,
) -> InjectionProposal:
    return InjectionProposal(
        schema_version=SCHEMA_VERSION,
        envelope_digest=envelope_digest,
        source=source,
        intents=intents,
        grounding_report=GroundingReport(
            grounded_evidence_ids=tuple(eid for intent in intents for eid in intent.evidence_ids)
        ),
        provenance=provenance or ProposalProvenance(),
        audit=audit or ProposalAudit(),
    )


def _build_envelope(
    *,
    production_primary_intents: tuple[ProductionIntent, ...] = (),
    injection_primary_intents: tuple[InjectionIntent, ...] = (),
    production_primary_source: ProposalSource = ProposalSource.LLM_LIVE,
    injection_primary_source: ProposalSource = ProposalSource.LLM_LIVE,
    production_primary_audit: ProposalAudit | None = None,
    injection_primary_audit: ProposalAudit | None = None,
    production_primary_provenance: ProposalProvenance | None = None,
    max_candidates: int = 8,
) -> ArbiterEnvelope:
    base = synthetic_arbiter_envelope()

    production_envelope_digest = base.production_output.baseline_proposal.envelope_digest
    injection_envelope_digest = base.injection_output.baseline_proposal.envelope_digest

    production_baseline = _production_proposal(
        envelope_digest=production_envelope_digest,
        intents=(_production_hold(),),
        source=ProposalSource.DETERMINISTIC_BASELINE,
    )
    injection_baseline = _injection_proposal(
        envelope_digest=injection_envelope_digest,
        intents=(_injection_hold(),),
        source=ProposalSource.DETERMINISTIC_BASELINE,
    )
    production_primary = _production_proposal(
        envelope_digest=production_envelope_digest,
        intents=production_primary_intents or (_production_hold(),),
        source=production_primary_source,
        audit=production_primary_audit,
        provenance=production_primary_provenance,
    )
    injection_primary = _injection_proposal(
        envelope_digest=injection_envelope_digest,
        intents=injection_primary_intents or (_injection_hold(),),
        source=injection_primary_source,
        audit=injection_primary_audit,
    )

    return dataclasses.replace(
        base,
        production_output=ProductionAgentOutput(
            primary_proposal=production_primary, baseline_proposal=production_baseline
        ),
        injection_output=InjectionAgentOutput(
            primary_proposal=injection_primary, baseline_proposal=injection_baseline
        ),
        simulation_budget=SimulationBudget(max_candidates=max_candidates),
    )


# ---------------------------------------------------------------------------
# A1 / A2 -- basic acceptance and budget floor.
# ---------------------------------------------------------------------------


def test_a1_accepts_valid_envelope() -> None:
    envelope = _build_envelope()
    decision = DeterministicArbiter().decide(envelope)
    assert isinstance(decision, ArbiterDecision)


def test_a2_rejects_budget_below_two() -> None:
    envelope = _build_envelope(max_candidates=1)
    with pytest.raises(ArbitrationError):
        DeterministicArbiter().decide(envelope)


# ---------------------------------------------------------------------------
# A3 / A4 / A5 / A6 -- floor bundle shape.
# ---------------------------------------------------------------------------


def test_a3_a4_exactly_one_hold_and_one_baseline() -> None:
    decision = DeterministicArbiter().decide(_build_envelope())
    hold_bundles = [b for b in decision.bundles if b.kind is CandidateKind.HOLD]
    baseline_bundles = [
        b for b in decision.bundles if b.kind is CandidateKind.DETERMINISTIC_BASELINE
    ]
    assert len(hold_bundles) == 1
    assert len(baseline_bundles) == 1


def test_a5_hold_carries_no_references() -> None:
    decision = DeterministicArbiter().decide(_build_envelope())
    hold = next(b for b in decision.bundles if b.kind is CandidateKind.HOLD)
    assert hold.production_proposal_digest is None
    assert hold.injection_proposal_digest is None
    assert hold.production_intent_digests == ()
    assert hold.injection_intent_digests == ()


def test_a6_baseline_bundle_references_baseline_not_primary() -> None:
    envelope = _build_envelope(production_primary_intents=(_production_increase(),))
    decision = DeterministicArbiter().decide(envelope)
    baseline_bundle = next(
        b for b in decision.bundles if b.kind is CandidateKind.DETERMINISTIC_BASELINE
    )

    baseline_proposal = envelope.production_output.baseline_proposal
    primary_proposal = envelope.production_output.primary_proposal
    assert baseline_bundle.production_proposal_digest == baseline_proposal.digest()
    assert baseline_bundle.production_proposal_digest != primary_proposal.digest()
    assert (
        baseline_bundle.injection_proposal_digest
        == envelope.injection_output.baseline_proposal.digest()
    )


# ---------------------------------------------------------------------------
# A7 -- tight budget returns only floor candidates.
# ---------------------------------------------------------------------------


def test_a7_budget_two_returns_only_floor() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(),),
        injection_primary_intents=(_injection_support(),),
        max_candidates=2,
    )
    decision = DeterministicArbiter().decide(envelope)
    assert len(decision.bundles) == 2
    assert {b.kind for b in decision.bundles} == {
        CandidateKind.HOLD,
        CandidateKind.DETERMINISTIC_BASELINE,
    }


# ---------------------------------------------------------------------------
# A8 / A9 / A10 -- novelty detection.
# ---------------------------------------------------------------------------


def test_a8_no_agent_bundle_when_both_primaries_match_baseline() -> None:
    envelope = _build_envelope()  # both primaries default to HOLD, same as baseline
    decision = DeterministicArbiter().decide(envelope)
    assert not any(b.kind is CandidateKind.AGENT_BUNDLE for b in decision.bundles)
    assert len(decision.bundles) == 2


def test_a9_provenance_only_difference_is_not_novelty() -> None:
    # Primary intents are structurally identical HOLD, but source/provenance differ.
    envelope = _build_envelope(
        production_primary_source=ProposalSource.DETERMINISTIC_FALLBACK,
        production_primary_provenance=ProposalProvenance(model_id="some-model"),
    )
    decision = DeterministicArbiter().decide(envelope)
    assert not any(b.kind is CandidateKind.AGENT_BUNDLE for b in decision.bundles)


def test_a10_intent_ordering_difference_is_not_novelty() -> None:
    intent_a = _production_increase(target_wells=("P1",))
    intent_b = _production_hold()
    # Baseline carries only HOLD; give primary the SAME set as an imaginary
    # baseline-equivalent-but-reordered case by using two HOLD intents in
    # different declared order -- structurally identical regardless of order.
    envelope_1 = _build_envelope(production_primary_intents=(intent_a, intent_b))
    envelope_2 = _build_envelope(production_primary_intents=(intent_b, intent_a))
    decision_1 = DeterministicArbiter().decide(envelope_1)
    decision_2 = DeterministicArbiter().decide(envelope_2)
    kinds_1 = sorted(str(b.kind) for b in decision_1.bundles)
    kinds_2 = sorted(str(b.kind) for b in decision_2.bundles)
    assert kinds_1 == kinds_2


# ---------------------------------------------------------------------------
# A11 / A12 / A13 / A14 -- candidate generation combinations.
# ---------------------------------------------------------------------------


def test_a11_production_novel_injection_unchanged_yields_one_alternative() -> None:
    envelope = _build_envelope(production_primary_intents=(_production_increase(),))
    decision = DeterministicArbiter().decide(envelope)
    agent_bundles = [b for b in decision.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    assert len(agent_bundles) == 1
    assert agent_bundles[0].exploration is False


def test_a12_injection_novel_production_unchanged_yields_one_alternative() -> None:
    envelope = _build_envelope(injection_primary_intents=(_injection_support(),))
    decision = DeterministicArbiter().decide(envelope)
    agent_bundles = [b for b in decision.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    assert len(agent_bundles) == 1
    assert agent_bundles[0].exploration is False


def test_a13_both_novel_produce_combined_primary_bundle() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(),),
        injection_primary_intents=(_injection_support(),),
        max_candidates=3,
    )
    decision = DeterministicArbiter().decide(envelope)
    agent_bundles = [b for b in decision.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    assert len(agent_bundles) == 1
    combined = agent_bundles[0]
    assert combined.exploration is False
    assert (
        combined.production_proposal_digest == envelope.production_output.primary_proposal.digest()
    )
    assert combined.injection_proposal_digest == envelope.injection_output.primary_proposal.digest()


def test_a14_both_novel_with_budget_also_yields_mixed_exploration_bundles() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(),),
        injection_primary_intents=(_injection_support(),),
        max_candidates=8,
    )
    decision = DeterministicArbiter().decide(envelope)
    agent_bundles = [b for b in decision.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    # combined primary + 2 mixed alternatives = 3
    assert len(agent_bundles) == 3
    exploration_flags = sorted(b.exploration for b in agent_bundles)
    assert exploration_flags == [False, True, True]


# ---------------------------------------------------------------------------
# A15 / A16 -- budget respected, floor never dropped.
# ---------------------------------------------------------------------------


def test_a15_candidate_count_never_exceeds_budget() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(),),
        injection_primary_intents=(_injection_support(),),
        max_candidates=3,
    )
    decision = DeterministicArbiter().decide(envelope)
    assert len(decision.bundles) <= 3


def test_a16_floor_never_removed_by_tight_budget() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(),),
        injection_primary_intents=(_injection_support(),),
        max_candidates=2,
    )
    decision = DeterministicArbiter().decide(envelope)
    kinds = {b.kind for b in decision.bundles}
    assert CandidateKind.HOLD in kinds
    assert CandidateKind.DETERMINISTIC_BASELINE in kinds


# ---------------------------------------------------------------------------
# A17 -- neutral deterministic ordering for competing mixed alternatives.
# ---------------------------------------------------------------------------


def _surviving_mixed_role(envelope: ArbiterEnvelope) -> str:
    decision = DeterministicArbiter().decide(envelope)
    agent_bundles = [b for b in decision.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    assert len(agent_bundles) == 2  # combined primary + exactly one mixed alternative
    mixed = next(b for b in agent_bundles if b.exploration)
    if mixed.production_proposal_digest == envelope.production_output.primary_proposal.digest():
        return "production-mixed"
    return "injection-mixed"


def test_a17_competing_mixed_alternatives_use_neutral_ordering() -> None:
    # Budget 4 = floor(2) + combined primary(1) + exactly one of the two
    # mixed alternatives -- forcing a real competition for the last slot.
    # These two well-name pairs are pre-verified (empirically, against this
    # implementation) to make the *opposite* role's mixed alternative win,
    # proving the tie-break is driven by a canonical combined structural
    # digest rather than a hardcoded "Production always first" preference.
    winner_1 = _surviving_mixed_role(
        _build_envelope(
            production_primary_intents=(_production_increase(target_wells=("P1",)),),
            injection_primary_intents=(_injection_support(recipient_wells=("I1",)),),
            max_candidates=4,
        )
    )
    winner_2 = _surviving_mixed_role(
        _build_envelope(
            production_primary_intents=(_production_increase(target_wells=("PX",)),),
            injection_primary_intents=(_injection_support(recipient_wells=("IX",)),),
            max_candidates=4,
        )
    )
    assert winner_1 == "injection-mixed"
    assert winner_2 == "production-mixed"
    assert winner_1 != winner_2, (
        "the surviving mixed alternative must depend on structural content, "
        "not a fixed role preference"
    )


# ---------------------------------------------------------------------------
# A18 / A19 / A20 -- determinism, audit fields excluded.
# ---------------------------------------------------------------------------


def test_a18_same_envelope_yields_same_decision_digest() -> None:
    envelope = _build_envelope(production_primary_intents=(_production_increase(),))
    decision_1 = DeterministicArbiter().decide(envelope)
    decision_2 = DeterministicArbiter().decide(envelope)
    assert decision_1.digest() == decision_2.digest()


def test_a19_audit_latency_does_not_change_decision() -> None:
    envelope_a = _build_envelope(
        production_primary_intents=(_production_increase(),),
        production_primary_audit=ProposalAudit(latency_ms=10),
    )
    envelope_b = _build_envelope(
        production_primary_intents=(_production_increase(),),
        production_primary_audit=ProposalAudit(latency_ms=99999),
    )
    decision_a = DeterministicArbiter().decide(envelope_a)
    decision_b = DeterministicArbiter().decide(envelope_b)
    assert len(decision_a.bundles) == len(decision_b.bundles)
    assert {str(b.kind) for b in decision_a.bundles} == {str(b.kind) for b in decision_b.bundles}


def test_a20_declined_alternatives_does_not_change_decision() -> None:
    envelope_a = _build_envelope(
        production_primary_intents=(_production_increase(),),
        production_primary_audit=ProposalAudit(declined_alternatives=("x",)),
    )
    envelope_b = _build_envelope(
        production_primary_intents=(_production_increase(),),
        production_primary_audit=ProposalAudit(declined_alternatives=("x", "y", "z")),
    )
    decision_a = DeterministicArbiter().decide(envelope_a)
    decision_b = DeterministicArbiter().decide(envelope_b)
    assert len(decision_a.bundles) == len(decision_b.bundles)


# ---------------------------------------------------------------------------
# A21 -- rationale-only difference does not create a duplicate structural bundle.
# ---------------------------------------------------------------------------


def test_a21_rationale_only_difference_does_not_duplicate_bundle() -> None:
    envelope_a = _build_envelope(
        production_primary_intents=(_production_increase(rationale_short="Reason A."),)
    )
    envelope_b = _build_envelope(
        production_primary_intents=(_production_increase(rationale_short="Reason B."),)
    )
    decision_a = DeterministicArbiter().decide(envelope_a)
    decision_b = DeterministicArbiter().decide(envelope_b)
    agent_bundles_a = [b for b in decision_a.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    agent_bundles_b = [b for b in decision_b.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    assert len(agent_bundles_a) == 1
    assert len(agent_bundles_b) == 1


# ---------------------------------------------------------------------------
# A22 / A23 / A24 -- Round2 conflict policy plumbing.
# ---------------------------------------------------------------------------


def test_a22_default_policy_returns_no_round2_request() -> None:
    decision = DeterministicArbiter().decide(_build_envelope())
    assert decision.round2_request is None


def test_a23_injected_fake_policy_can_produce_round2_request() -> None:
    class _FakeConflictPolicy:
        def evaluate(self, envelope: ArbiterEnvelope) -> Round2Request | None:
            return Round2Request(
                requested_roles=(AgentRole.PRODUCTION, AgentRole.INJECTION),
                conflict_code="TEST_CONFLICT",
                proposal_digests=(envelope.production_output.primary_proposal.digest(),),
            )

    arbiter = DeterministicArbiter(conflict_policy=_FakeConflictPolicy())
    decision = arbiter.decide(_build_envelope())
    assert decision.round2_request is not None
    assert decision.round2_request.conflict_code == "TEST_CONFLICT"
    # Floor must still be present even when Round2 is requested.
    kinds = {b.kind for b in decision.bundles}
    assert CandidateKind.HOLD in kinds
    assert CandidateKind.DETERMINISTIC_BASELINE in kinds


def test_a24_round2_request_cannot_carry_arbiter_role() -> None:
    with pytest.raises(ArbiterError):
        Round2Request(
            requested_roles=(AgentRole.ARBITER,),
            conflict_code="X",
            proposal_digests=(),
        )


def test_no_round2_conflict_policy_conforms_to_protocol() -> None:
    assert isinstance(NoRound2ConflictPolicy(), ConflictPolicy)
    assert NoRound2ConflictPolicy().evaluate(_build_envelope()) is None
