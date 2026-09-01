"""DeterministicMagnitudeResolver: MR-001, the first prescriptive numerical boundary.

MagnitudeResolver MAY create ProducerControl/InjectorControl/MagnitudeCandidate.
It MUST NOT simulate, call Guardian/CHDD, invent an official compensation/water
rule, or use arbitrary (non-evidence-backed) magnitude percentages. HOLD is an
exact carry-forward; DETERMINISTIC_BASELINE is resolved from an explicit
canonical baseline snapshot, never through the empirical profile.
"""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from aios.mas.arbiter import ArbiterDecision, DecisionBundle
from aios.mas.common import (
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
from aios.mas.magnitude import MagnitudeCandidate
from aios.mas.magnitude_resolution.engine import (
    DeterministicMagnitudeResolver,
    MagnitudeContext,
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
from aios.schedule import InjectorControl, ProducerControl, WellStatus
from tests.support.mas_team_fixtures import synthetic_arbiter_envelope

SCHEMA_VERSION = "aios.mas.contracts/1.0.0"
EFFECTIVE_DATE = datetime.date(2020, 4, 1)


def _producer(
    well: str, *, rate: float = 100.0, status: WellStatus = WellStatus.OPEN
) -> ProducerControl:
    return ProducerControl(well=well, status=status, mode="LRAT", liquid_rate=rate)


def _injector(
    well: str, *, rate: float = 200.0, status: WellStatus = WellStatus.OPEN
) -> InjectorControl:
    return InjectorControl(well=well, phase="WATER", status=status, mode="RATE", surface_rate=rate)


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


def _production_increase(
    *, target_wells: tuple[str, ...], intensity: Intensity = Intensity.SMALL
) -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.INCREASE_OFFTAKE,
        direction=Direction.UP,
        intensity=intensity,
        target_wells=target_wells,
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _production_reduce(
    *, target_wells: tuple[str, ...], intensity: Intensity = Intensity.SMALL
) -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.REDUCE_OFFTAKE,
        direction=Direction.DOWN,
        intensity=intensity,
        target_wells=target_wells,
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _production_shut_in(*, target_wells: tuple[str, ...]) -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.SHUT_IN,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        target_wells=target_wells,
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _production_reopen(*, target_wells: tuple[str, ...]) -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.REOPEN,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        target_wells=target_wells,
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _production_rebalance(
    *, target_wells: tuple[str, ...], target_regions: tuple[str, ...]
) -> ProductionIntent:
    return ProductionIntent(
        action=ProductionAction.REBALANCE_WITHIN_BLOCK,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        target_wells=target_wells,
        target_regions=target_regions,
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _injection_scale(
    *, direction: Direction, intensity: Intensity = Intensity.SMALL
) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.SCALE_TOTAL_INJECTION,
        direction=direction,
        intensity=intensity,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _injection_support(
    *, recipient_wells: tuple[str, ...], intensity: Intensity = Intensity.SMALL
) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.SUPPORT_REGION,
        direction=Direction.UP,
        intensity=intensity,
        donor_wells=(),
        recipient_wells=recipient_wells,
        source_regions=(),
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _injection_reduce_support(
    *, donor_wells: tuple[str, ...], intensity: Intensity = Intensity.SMALL
) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.REDUCE_SUPPORT_PATH,
        direction=Direction.DOWN,
        intensity=intensity,
        donor_wells=donor_wells,
        recipient_wells=(),
        source_regions=(),
        target_regions=(),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _injection_reallocate(
    *,
    donor_wells: tuple[str, ...],
    recipient_wells: tuple[str, ...],
    intensity: Intensity = Intensity.SMALL,
) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.REALLOCATE_INJECTION,
        direction=Direction.NONE,
        intensity=intensity,
        donor_wells=donor_wells,
        recipient_wells=recipient_wells,
        source_regions=("B1",),
        target_regions=("B1",),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _injection_rebalance_across(
    *, donor_wells: tuple[str, ...], recipient_wells: tuple[str, ...]
) -> InjectionIntent:
    return InjectionIntent(
        action=InjectionAction.REBALANCE_ACROSS_BLOCKS,
        direction=Direction.NONE,
        intensity=Intensity.SMALL,
        donor_wells=donor_wells,
        recipient_wells=recipient_wells,
        source_regions=("B1",),
        target_regions=("B2",),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )


def _proposal(cls, *, intents, envelope_digest: str, source=ProposalSource.LLM_LIVE):
    return cls(
        schema_version=SCHEMA_VERSION,
        envelope_digest=envelope_digest,
        source=source,
        intents=intents,
        grounding_report=GroundingReport(
            grounded_evidence_ids=tuple(eid for intent in intents for eid in intent.evidence_ids)
        ),
        provenance=ProposalProvenance(),
        audit=ProposalAudit(),
    )


def _build_envelope(
    *,
    production_primary_intents=None,
    injection_primary_intents=None,
) -> ArbiterEnvelope:
    base = synthetic_arbiter_envelope()
    ped = base.production_output.baseline_proposal.envelope_digest
    ied = base.injection_output.baseline_proposal.envelope_digest

    production_baseline = _proposal(
        ProductionProposal,
        intents=(_production_hold(),),
        envelope_digest=ped,
        source=ProposalSource.DETERMINISTIC_BASELINE,
    )
    injection_baseline = _proposal(
        InjectionProposal,
        intents=(_injection_hold(),),
        envelope_digest=ied,
        source=ProposalSource.DETERMINISTIC_BASELINE,
    )
    production_primary = _proposal(
        ProductionProposal,
        intents=production_primary_intents or (_production_hold(),),
        envelope_digest=ped,
    )
    injection_primary = _proposal(
        InjectionProposal,
        intents=injection_primary_intents or (_injection_hold(),),
        envelope_digest=ied,
    )
    return dataclasses.replace(
        base,
        production_output=ProductionAgentOutput(
            primary_proposal=production_primary, baseline_proposal=production_baseline
        ),
        injection_output=InjectionAgentOutput(
            primary_proposal=injection_primary, baseline_proposal=injection_baseline
        ),
        simulation_budget=SimulationBudget(max_candidates=8),
    )


def _decision_bundle_for(
    envelope: ArbiterEnvelope, kind: CandidateKind, *, use_primary: bool
) -> DecisionBundle:
    production_proposal = (
        envelope.production_output.primary_proposal
        if use_primary
        else envelope.production_output.baseline_proposal
    )
    injection_proposal = (
        envelope.injection_output.primary_proposal
        if use_primary
        else envelope.injection_output.baseline_proposal
    )
    if kind is CandidateKind.HOLD:
        return DecisionBundle(kind=CandidateKind.HOLD)
    return DecisionBundle(
        kind=kind,
        production_proposal_digest=production_proposal.digest(),
        injection_proposal_digest=injection_proposal.digest(),
        production_intent_digests=tuple(sorted(i.digest() for i in production_proposal.intents)),
        injection_intent_digests=tuple(sorted(i.digest() for i in injection_proposal.intents)),
        exploration=kind is CandidateKind.AGENT_BUNDLE,
    )


def _decision_for(
    envelope: ArbiterEnvelope, *, extra_bundles: tuple[DecisionBundle, ...] = ()
) -> ArbiterDecision:
    bundles = (
        _decision_bundle_for(envelope, CandidateKind.HOLD, use_primary=False),
        _decision_bundle_for(envelope, CandidateKind.DETERMINISTIC_BASELINE, use_primary=False),
        *extra_bundles,
    )
    return ArbiterDecision(
        schema_version=SCHEMA_VERSION,
        arbiter_envelope_digest=envelope.digest(),
        bundles=bundles,
    )


def _context(
    envelope: ArbiterEnvelope,
    *,
    previous_producers=None,
    previous_injectors=None,
    baseline_producers=None,
    baseline_injectors=None,
) -> MagnitudeContext:
    resolved_previous_producers = (
        previous_producers if previous_producers is not None else (_producer("P1"), _producer("P2"))
    )
    resolved_previous_injectors = (
        previous_injectors if previous_injectors is not None else (_injector("I1"),)
    )
    return MagnitudeContext(
        effective_date=EFFECTIVE_DATE,
        arbiter_envelope=envelope,
        previous_producers=resolved_previous_producers,
        previous_injectors=resolved_previous_injectors,
        baseline_producers=(
            baseline_producers if baseline_producers is not None else resolved_previous_producers
        ),
        baseline_injectors=(
            baseline_injectors if baseline_injectors is not None else resolved_previous_injectors
        ),
    )


# ---------------------------------------------------------------------------
# M1 / M2 / M3 -- basic acceptance, frozen output type.
# ---------------------------------------------------------------------------


def test_m1_valid_resolver_input_accepted() -> None:
    envelope = _build_envelope()
    decision = _decision_for(envelope)
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    candidates = resolver.resolve(decision)
    assert len(candidates) >= 2


def test_m2_output_uses_frozen_magnitude_candidate() -> None:
    envelope = _build_envelope()
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    candidates = resolver.resolve(_decision_for(envelope))
    assert all(isinstance(c, MagnitudeCandidate) for c in candidates)


def test_m3_no_duplicate_magnitude_candidate_type() -> None:
    import aios.mas.magnitude_resolution.engine as engine_module

    for obj in vars(engine_module).values():
        if isinstance(obj, type) and obj.__name__ == "MagnitudeCandidate":
            assert obj is MagnitudeCandidate


# ---------------------------------------------------------------------------
# M4 / M5 -- HOLD exact carry-forward.
# ---------------------------------------------------------------------------


def test_m4_hold_exact_carry_forward() -> None:
    envelope = _build_envelope()
    previous_producers = (_producer("P1", rate=123.456), _producer("P2", rate=77.0))
    previous_injectors = (_injector("I1", rate=210.5),)
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope, previous_producers=previous_producers, previous_injectors=previous_injectors
        )
    )
    candidates = resolver.resolve(_decision_for(envelope))
    hold = next(c for c in candidates if c.candidate_kind is CandidateKind.HOLD)
    assert set(hold.producers) == set(previous_producers)
    assert set(hold.injectors) == set(previous_injectors)


def test_m5_one_unit_control_change_makes_hold_invariant_fail() -> None:
    from aios.mas.invariants import require_hold_carry_forward

    envelope = _build_envelope()
    previous_producers = (_producer("P1", rate=100.0),)
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_producers=previous_producers, previous_injectors=())
    )
    candidates = resolver.resolve(_decision_for(envelope))
    hold = next(c for c in candidates if c.candidate_kind is CandidateKind.HOLD)

    mutated_previous = (_producer("P1", rate=100.0000001),)
    with pytest.raises(Exception):  # noqa: B017 -- MasInvariantError from frozen invariants module
        require_hold_carry_forward(
            previous_producers=mutated_previous,
            previous_injectors=(),
            held_producers=hold.producers,
            held_injectors=hold.injectors,
        )


# ---------------------------------------------------------------------------
# M6 / M7 / M8 -- baseline exact, floor always present.
# ---------------------------------------------------------------------------


def test_m6_baseline_resolved_through_exact_baseline_source() -> None:
    envelope = _build_envelope()
    baseline_producers = (_producer("P1", rate=999.0), _producer("P2", rate=888.0))
    baseline_injectors = (_injector("I1", rate=777.0),)
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope, baseline_producers=baseline_producers, baseline_injectors=baseline_injectors
        )
    )
    candidates = resolver.resolve(_decision_for(envelope))
    baseline = next(
        c for c in candidates if c.candidate_kind is CandidateKind.DETERMINISTIC_BASELINE
    )
    assert set(baseline.producers) == set(baseline_producers)
    assert set(baseline.injectors) == set(baseline_injectors)


def test_m7_m8_hold_and_baseline_always_present() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(target_wells=("P1",)),),
    )
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    extra = (_decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True),)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=extra))
    kinds = {c.candidate_kind for c in candidates}
    assert CandidateKind.HOLD in kinds
    assert CandidateKind.DETERMINISTIC_BASELINE in kinds


# ---------------------------------------------------------------------------
# M9 -- agent candidate cannot replace floor.
# ---------------------------------------------------------------------------


def test_m9_agent_candidate_cannot_replace_floor() -> None:
    envelope = _build_envelope(
        production_primary_intents=(
            _production_increase(target_wells=("P1",), intensity=Intensity.AGGRESSIVE),
        ),
    )
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    extra = (_decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True),)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=extra))
    assert sum(1 for c in candidates if c.candidate_kind is CandidateKind.HOLD) == 1
    assert (
        sum(1 for c in candidates if c.candidate_kind is CandidateKind.DETERMINISTIC_BASELINE) == 1
    )


# ---------------------------------------------------------------------------
# M10 / M11 -- determinism, intent reordering invariance.
# ---------------------------------------------------------------------------


def test_m10_same_input_same_candidate_digests() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(target_wells=("P1",)),)
    )
    decision = _decision_for(
        envelope,
        extra_bundles=(
            _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True),
        ),
    )
    resolver_1 = DeterministicMagnitudeResolver(context=_context(envelope))
    resolver_2 = DeterministicMagnitudeResolver(context=_context(envelope))
    digests_1 = [c.digest() for c in resolver_1.resolve(decision)]
    digests_2 = [c.digest() for c in resolver_2.resolve(decision)]
    assert digests_1 == digests_2


def test_m11_intent_reordering_same_numerical_result() -> None:
    intent_a = _production_increase(target_wells=("P1",))
    intent_b = _production_shut_in(target_wells=("P2",))
    envelope_forward = _build_envelope(production_primary_intents=(intent_a, intent_b))
    envelope_backward = _build_envelope(production_primary_intents=(intent_b, intent_a))

    resolver_forward = DeterministicMagnitudeResolver(context=_context(envelope_forward))
    resolver_backward = DeterministicMagnitudeResolver(context=_context(envelope_backward))

    bundle_forward = _decision_bundle_for(
        envelope_forward, CandidateKind.AGENT_BUNDLE, use_primary=True
    )
    bundle_backward = _decision_bundle_for(
        envelope_backward, CandidateKind.AGENT_BUNDLE, use_primary=True
    )

    candidates_forward = resolver_forward.resolve(
        _decision_for(envelope_forward, extra_bundles=(bundle_forward,))
    )
    candidates_backward = resolver_backward.resolve(
        _decision_for(envelope_backward, extra_bundles=(bundle_backward,))
    )

    agent_forward = next(
        c for c in candidates_forward if c.candidate_kind is CandidateKind.AGENT_BUNDLE
    )
    agent_backward = next(
        c for c in candidates_backward if c.candidate_kind is CandidateKind.AGENT_BUNDLE
    )
    assert set(agent_forward.producers) == set(agent_backward.producers)


# ---------------------------------------------------------------------------
# M12 -- unrelated wells unchanged.
# ---------------------------------------------------------------------------


def test_m12_unrelated_wells_unchanged() -> None:
    previous_producers = (_producer("P1", rate=100.0), _producer("P2", rate=200.0))
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(target_wells=("P1",)),)
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_producers=previous_producers)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    p2 = next(c for c in agent.producers if c.well == "P2")
    assert p2 == _producer("P2", rate=200.0)


# ---------------------------------------------------------------------------
# M13 / M14 / M15 / M16 -- unknown/unsupported fail closed, not silently mapped.
# ---------------------------------------------------------------------------


def test_m13_unknown_target_well_fails_closed() -> None:
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(target_wells=("GHOST",)),)
    )
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    # Unsupported/unresolvable AGENT_BUNDLE is dropped, not crashed or faked.
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)


def test_m14_m15_m16_unsupported_action_fails_closed_not_hold_or_baseline() -> None:
    # REDUCE_OFFTAKE used to be the example here; EXP-005 gave it an anchor, so
    # the invariant is now demonstrated with an action that is still genuinely
    # unresolvable. What is under test is unchanged: an unsupported action is
    # dropped, and the HOLD/baseline floor is neither crashed nor substituted.
    envelope = _build_envelope(
        production_primary_intents=(
            _production_rebalance(target_wells=("P1", "P2"), target_regions=("B1",)),
        )
    )
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent_candidates = [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    assert agent_candidates == []
    # Floor is untouched and not silently substituted for the failed agent bundle.
    assert sum(1 for c in candidates if c.candidate_kind is CandidateKind.HOLD) == 1
    assert (
        sum(1 for c in candidates if c.candidate_kind is CandidateKind.DETERMINISTIC_BASELINE) == 1
    )


def test_rebalance_within_block_is_unsupported() -> None:
    envelope = _build_envelope(
        production_primary_intents=(
            _production_rebalance(target_wells=("P1", "P2"), target_regions=("B1",)),
        )
    )
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)


def test_rebalance_across_blocks_is_unsupported() -> None:
    envelope = _build_envelope(
        injection_primary_intents=(
            _injection_rebalance_across(donor_wells=("I1",), recipient_wells=("I2",)),
        )
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope,
            previous_injectors=(_injector("I1"), _injector("I2")),
        )
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)


# ---------------------------------------------------------------------------
# M17 -- no last-write-wins for conflicting same-control intents.
# ---------------------------------------------------------------------------


def test_m17_conflicting_intents_on_same_well_fail_closed() -> None:
    intent_a = _production_increase(target_wells=("P1",), intensity=Intensity.SMALL)
    intent_b = _production_shut_in(target_wells=("P1",))
    envelope = _build_envelope(production_primary_intents=(intent_a, intent_b))
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)


# ---------------------------------------------------------------------------
# M18 / M19 -- numeric dedup preserves floor semantic identity.
# ---------------------------------------------------------------------------


def test_m18_numerically_duplicate_agent_candidate_does_not_consume_extra_slot() -> None:
    # Primary intent is structurally novel but numerically resolves to
    # exactly the same controls as HOLD (uplift factor effectively no-op is
    # not possible via SMALL, so use a HOLD-equivalent structural intent
    # applied to an unrelated well that doesn't change anything numerically:
    # simplest reliable duplicate is two AGENT_BUNDLEs from the same primary).
    envelope = _build_envelope(
        production_primary_intents=(_production_increase(target_wells=("P1",)),)
    )
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    bundle_1 = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    bundle_2 = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    single = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle_1,)))
    doubled = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle_1, bundle_2)))
    # Инвариант M18 -- численный дубликат не занимает лишнего слота. Одна
    # структурная гипотеза с PERF-002B раскрывается в численную окрестность,
    # поэтому проверяется не абсолютное число, а то, что второй такой же
    # bundle не добавляет ничего.
    agents_single = [c for c in single if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    agents_doubled = [c for c in doubled if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    assert [c.digest() for c in agents_single] == [c.digest() for c in agents_doubled]


def test_m19_hold_baseline_survive_even_if_numerically_equal_to_each_other() -> None:
    same_controls = (_producer("P1", rate=100.0),)
    envelope = _build_envelope()
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope,
            previous_producers=same_controls,
            previous_injectors=(),
            baseline_producers=same_controls,
            baseline_injectors=(),
        )
    )
    candidates = resolver.resolve(_decision_for(envelope))
    kinds = {c.candidate_kind for c in candidates}
    assert CandidateKind.HOLD in kinds
    assert CandidateKind.DETERMINISTIC_BASELINE in kinds
    assert len(candidates) == 2


# ---------------------------------------------------------------------------
# Real supported-action coverage: INCREASE_OFFTAKE, SHUT_IN, REOPEN,
# SCALE_TOTAL_INJECTION, SUPPORT_REGION, REDUCE_SUPPORT_PATH, REALLOCATE_INJECTION.
# ---------------------------------------------------------------------------


def test_increase_offtake_uses_evidence_backed_factor() -> None:
    from aios.mas.magnitude_resolution.profile import ModelYEmpiricalMagnitudeProfile

    previous_producers = (_producer("P1", rate=100.0),)
    envelope = _build_envelope(
        production_primary_intents=(
            _production_increase(target_wells=("P1",), intensity=Intensity.SMALL),
        )
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_producers=previous_producers)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    p1 = next(c for c in agent.producers if c.well == "P1")
    anchor = ModelYEmpiricalMagnitudeProfile().producer_uplift_factor(Intensity.SMALL)
    assert p1.liquid_rate == pytest.approx(100.0 * anchor.value)


def test_increase_offtake_respects_wlpr_ceiling() -> None:
    from aios.cases.contract import TRACK1_WLPR_HARD_MAX

    previous_producers = (_producer("P1", rate=1000.0),)
    envelope = _build_envelope(
        production_primary_intents=(
            _production_increase(target_wells=("P1",), intensity=Intensity.AGGRESSIVE),
        )
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_producers=previous_producers)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    p1 = next(c for c in agent.producers if c.well == "P1")
    assert p1.liquid_rate == pytest.approx(TRACK1_WLPR_HARD_MAX)


def test_shut_in_sets_status_shut_and_clears_rate() -> None:
    previous_producers = (_producer("P1", rate=150.0),)
    envelope = _build_envelope(
        production_primary_intents=(_production_shut_in(target_wells=("P1",)),)
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_producers=previous_producers)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    p1 = next(c for c in agent.producers if c.well == "P1")
    assert p1.status is WellStatus.SHUT
    assert p1.liquid_rate is None


def test_reopen_uses_baseline_canonical_source() -> None:
    # Previous injector state deliberately differs from baseline's so the
    # resolved AGENT_BUNDLE (previous injectors unchanged + reopened
    # producer) doesn't numerically collide with DETERMINISTIC_BASELINE
    # (baseline injectors) and get deduplicated away by the floor-preserving
    # dedup rule (see test_hold_intent_inside_agent_bundle_is_a_noop for that
    # behavior tested directly).
    previous_producers = (_producer("P1", rate=0.0, status=WellStatus.SHUT),)
    previous_injectors = (_injector("I1", rate=111.0),)
    baseline_producers = (_producer("P1", rate=140.0, status=WellStatus.OPEN),)
    baseline_injectors = (_injector("I1", rate=222.0),)
    envelope = _build_envelope(
        production_primary_intents=(_production_reopen(target_wells=("P1",)),)
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope,
            previous_producers=previous_producers,
            previous_injectors=previous_injectors,
            baseline_producers=baseline_producers,
            baseline_injectors=baseline_injectors,
        )
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    p1 = next(c for c in agent.producers if c.well == "P1")
    assert p1 == _producer("P1", rate=140.0, status=WellStatus.OPEN)


def test_reopen_fails_closed_without_open_baseline_source() -> None:
    previous_producers = (_producer("P1", rate=0.0, status=WellStatus.SHUT),)
    baseline_producers = (
        _producer("P1", rate=0.0, status=WellStatus.SHUT),
    )  # also shut in baseline
    envelope = _build_envelope(
        production_primary_intents=(_production_reopen(target_wells=("P1",)),)
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(
            envelope, previous_producers=previous_producers, baseline_producers=baseline_producers
        )
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)


def test_scale_total_injection_up_applies_to_all_injectors() -> None:
    from aios.mas.magnitude_resolution.profile import ModelYEmpiricalMagnitudeProfile

    previous_injectors = (_injector("I1", rate=100.0), _injector("I2", rate=200.0))
    envelope = _build_envelope(
        injection_primary_intents=(
            _injection_scale(direction=Direction.UP, intensity=Intensity.SMALL),
        )
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_injectors=previous_injectors)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    anchor = ModelYEmpiricalMagnitudeProfile().injection_scale_factor(
        Intensity.SMALL, direction_up=True
    )
    rates = {c.well: c.surface_rate for c in agent.injectors}
    assert rates["I1"] == pytest.approx(100.0 * anchor.value)
    assert rates["I2"] == pytest.approx(200.0 * anchor.value)


def test_support_region_scopes_to_recipient_wells_only() -> None:
    previous_injectors = (_injector("I1", rate=100.0), _injector("I2", rate=200.0))
    envelope = _build_envelope(
        injection_primary_intents=(_injection_support(recipient_wells=("I1",)),)
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_injectors=previous_injectors)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    rates = {c.well: c.surface_rate for c in agent.injectors}
    assert rates["I1"] > 100.0
    assert rates["I2"] == pytest.approx(200.0)


def test_support_region_without_recipient_wells_fails_closed() -> None:
    intent = InjectionIntent(
        action=InjectionAction.SUPPORT_REGION,
        direction=Direction.UP,
        intensity=Intensity.SMALL,
        donor_wells=(),
        recipient_wells=(),
        source_regions=(),
        target_regions=("B1",),
        evidence_ids=("e",),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="r.",
    )
    envelope = _build_envelope(injection_primary_intents=(intent,))
    resolver = DeterministicMagnitudeResolver(context=_context(envelope))
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)


def test_reduce_support_path_scopes_to_donor_wells_only() -> None:
    previous_injectors = (_injector("I1", rate=100.0), _injector("I2", rate=200.0))
    envelope = _build_envelope(
        injection_primary_intents=(_injection_reduce_support(donor_wells=("I1",)),)
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_injectors=previous_injectors)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    rates = {c.well: c.surface_rate for c in agent.injectors}
    assert rates["I1"] < 100.0
    assert rates["I2"] == pytest.approx(200.0)


def test_reallocate_injection_conserves_total_between_pair() -> None:
    previous_injectors = (_injector("I1", rate=100.0), _injector("I2", rate=50.0))
    envelope = _build_envelope(
        injection_primary_intents=(
            _injection_reallocate(
                donor_wells=("I1",), recipient_wells=("I2",), intensity=Intensity.SMALL
            ),
        )
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_injectors=previous_injectors)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agent = next(c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE)
    rates = {c.well: c.surface_rate for c in agent.injectors}
    assert rates["I1"] < 100.0
    assert rates["I2"] > 50.0
    assert rates["I1"] + rates["I2"] == pytest.approx(150.0)


def test_reallocate_injection_supports_multiple_donors() -> None:
    """PERF-003 снял ограничение «ровно одна пара».

    Раньше многопарный перенос отвергался как непредставимый. Теперь он
    материализуется, и суммарная закачка сохраняется: перенос не имеет права
    незаметно стать масштабированием поля.
    """
    previous_injectors = (
        _injector("I1", rate=100.0),
        _injector("I2", rate=50.0),
        _injector("I3", rate=40.0),
    )
    envelope = _build_envelope(
        injection_primary_intents=(
            _injection_reallocate(
                donor_wells=("I1", "I2"), recipient_wells=("I3",), intensity=Intensity.SMALL
            ),
        )
    )
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_injectors=previous_injectors)
    )
    bundle = _decision_bundle_for(envelope, CandidateKind.AGENT_BUNDLE, use_primary=True)
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    agents = [c for c in candidates if c.candidate_kind is CandidateKind.AGENT_BUNDLE]
    assert agents, "a multi-donor reallocation must now be representable"
    before = sum(c.surface_rate for c in previous_injectors)
    for candidate in agents:
        after = sum(c.surface_rate for c in candidate.injectors)
        assert after == pytest.approx(before, abs=1e-9)


def test_hold_intent_inside_agent_bundle_is_a_noop() -> None:
    previous_producers = (_producer("P1", rate=100.0), _producer("P2", rate=200.0))
    envelope = _build_envelope(production_primary_intents=(_production_hold(),))
    resolver = DeterministicMagnitudeResolver(
        context=_context(envelope, previous_producers=previous_producers)
    )
    bundle = DecisionBundle(
        kind=CandidateKind.AGENT_BUNDLE,
        production_proposal_digest=envelope.production_output.primary_proposal.digest(),
        production_intent_digests=(
            envelope.production_output.primary_proposal.intents[0].digest(),
        ),
    )
    candidates = resolver.resolve(_decision_for(envelope, extra_bundles=(bundle,)))
    # Numerically identical to HOLD -> deduplicated away, floor remains.
    assert not any(c.candidate_kind is CandidateKind.AGENT_BUNDLE for c in candidates)
    assert len(candidates) == 2
