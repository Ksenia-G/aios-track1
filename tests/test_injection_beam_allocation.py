"""Пучок доходит до арбитража, и слоты делятся между гипотезами.

Без этого первая структурная гипотеза съедала бы весь физический бюджет, и
пучок остался бы декоративным: смысл в том, чтобы физика высказалась о каждой
гипотезе, а не только о первой.
"""

from __future__ import annotations

import dataclasses

from aios.mas.arbitration.engine import DeterministicArbiter
from aios.mas.common import CandidateKind, Direction, Intensity, ProposalSource, UncertaintyLevel
from aios.mas.intents import InjectionAction, InjectionIntent
from aios.mas.proposals import GroundingReport, InjectionAgentOutput
from tests.support.mas_team_fixtures import (
    synthetic_arbiter_envelope,
    synthetic_injection_proposal,
)


def _intent(action, wells):
    return InjectionIntent(
        action=action,
        direction=Direction.UP,
        intensity=Intensity.MODERATE,
        donor_wells=(),
        recipient_wells=tuple(wells),
        source_regions=(),
        target_regions=(),
        evidence_ids=("SYN-pressure-1",),
        uncertainty=UncertaintyLevel.MEDIUM,
        rationale_short="beam allocation fixture",
    )


def _hypothesis(wells) -> object:
    return dataclasses.replace(
        synthetic_injection_proposal(source=ProposalSource.LLM_LIVE),
        intents=(_intent(InjectionAction.SUPPORT_REGION, wells),),
        grounding_report=GroundingReport(grounded_evidence_ids=("SYN-pressure-1",)),
    )


def _envelope_with_beam(count: int):
    base = synthetic_arbiter_envelope()
    hypotheses = [_hypothesis((f"SYN-I{i}",)) for i in range(count)]
    output = InjectionAgentOutput(
        primary_proposal=hypotheses[0],
        baseline_proposal=base.injection_output.baseline_proposal,
        alternatives=tuple(hypotheses[1:]),
    )
    return dataclasses.replace(base, injection_output=output)


# ===========================================================================
# B10 -- каждая гипотеза доходит до арбитража
# ===========================================================================


def test_every_beam_hypothesis_reaches_arbitration() -> None:
    envelope = _envelope_with_beam(3)
    decision = DeterministicArbiter().decide(envelope)
    agent_bundles = [b for b in decision.bundles if b.kind is CandidateKind.AGENT_BUNDLE]
    referenced = {b.injection_proposal_digest for b in agent_bundles}
    expected = {h.digest() for h in envelope.injection_output.hypotheses()}
    assert expected <= referenced, "each structural hypothesis must reach the candidate pool"


def test_single_hypothesis_behaviour_is_unchanged() -> None:
    """C3: с пустыми alternatives арбитраж прежний."""
    base = synthetic_arbiter_envelope()
    before = DeterministicArbiter().decide(base)
    same = DeterministicArbiter().decide(base)
    key = lambda bundle: (
        bundle.kind,
        bundle.production_proposal_digest,
        bundle.injection_proposal_digest,
        bundle.production_intent_digests,
        bundle.injection_intent_digests,
    )
    assert [key(b) for b in before.bundles] == [key(b) for b in same.bundles]
    assert base.injection_output.alternatives == ()


def test_floor_bundles_are_still_present_with_a_beam() -> None:
    decision = DeterministicArbiter().decide(_envelope_with_beam(3))
    kinds = {b.kind for b in decision.bundles}
    assert CandidateKind.HOLD in kinds
    assert CandidateKind.DETERMINISTIC_BASELINE in kinds


# ===========================================================================
# B11/B12/B13 -- распределение слотов между гипотезами
# ===========================================================================


def _round_robin(groups, slots):
    from aios.mas.magnitude_resolution.engine import allocate_by_origin

    return allocate_by_origin(groups, slots)


def test_each_origin_gets_one_slot_before_any_gets_a_second() -> None:
    candidates = [("H1", 1), ("H1", 2), ("H1", 3), ("H2", 1), ("H2", 2), ("H3", 1)]
    chosen = _round_robin(candidates, 3)
    assert [origin for origin, _ in chosen] == ["H1", "H2", "H3"]


def test_second_round_only_after_every_origin_was_served() -> None:
    candidates = [("H1", 1), ("H1", 2), ("H2", 1), ("H3", 1)]
    chosen = _round_robin(candidates, 5)
    assert [origin for origin, _ in chosen][:3] == ["H1", "H2", "H3"]
    assert ("H1", 2) in chosen


def test_allocation_never_exceeds_the_slot_count() -> None:
    candidates = [(f"H{i}", j) for i in range(5) for j in range(4)]
    assert len(_round_robin(candidates, 6)) == 6


def test_allocation_preserves_first_seen_origin_order() -> None:
    candidates = [("H2", 1), ("H1", 1), ("H3", 1)]
    chosen = _round_robin(candidates, 3)
    assert [origin for origin, _ in chosen] == ["H2", "H1", "H3"]


def test_a_lone_origin_may_use_every_slot() -> None:
    candidates = [("H1", i) for i in range(6)]
    assert len(_round_robin(candidates, 4)) == 4


def test_allocation_is_deterministic() -> None:
    candidates = [("H1", 1), ("H2", 1), ("H1", 2), ("H3", 1)]
    assert _round_robin(candidates, 3) == _round_robin(candidates, 3)
