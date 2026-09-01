"""Role-specific envelopes: no god AgentEnvelope, no baseline leakage into prompts."""

from __future__ import annotations

import dataclasses
import datetime

import pytest

from aios.mas.common import AgentRole, DecisionRound, UncertaintyLevel
from aios.mas.envelopes import ArbiterEnvelope, EnvelopeError, InjectionEnvelope, ProductionEnvelope
from aios.mas.evidence import (
    EvidenceArtifact,
    HorizonContext,
    MemorySlice,
    ProposalReference,
    SimulationBudget,
)
from aios.mas.intents import (
    Direction,
    InjectionAction,
    InjectionIntent,
    Intensity,
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
from aios.state import (
    ConstraintState,
    FieldStateSnapshot,
    GeologyContext,
    StateCard,
    StateMetadata,
    UncertaintyState,
    WellRole,
    WellStateSnapshot,
)


def _state_card() -> StateCard:
    return StateCard(
        metadata=StateMetadata(
            date=datetime.date(2020, 4, 1),
            case_id="mini",
            run_id="run-001",
            simulator_digest="sim-sha256:" + "a" * 10,
            schedule_hash="a" * 64,
            source_summary_identity="summary-sha256:" + "b" * 10,
            extractor_version="aios.state.extractor/1.0.0",
        ),
        field=FieldStateSnapshot(
            average_pressure=211.5,
            pressure_trend=None,
            oil_rate=120.0,
            water_rate=35.0,
            liquid_rate=155.0,
            water_injection_rate=150.0,
            voidage_production_rate=160.0,
            voidage_production_total=1400.0,
            oil_total=1000.0,
            water_total=400.0,
            water_injection_total=0.0,
            produced_water_available=35.0,
            compensation_value=None,
            compensation_status="UNRESOLVED",
        ),
        wells=(
            WellStateSnapshot(
                well_id="P1",
                role=WellRole.PRODUCER,
                status="OPEN",
                current_target=120.0,
                control_mode="LRAT",
                bhp=55.0,
                wlpr=155.0,
                wopr=120.0,
                wwpr=35.0,
                wwir=0.0,
                wct=0.2258,
                wopt=1000.0,
                wwpt=400.0,
                wwit=0.0,
                producer_bhp_margin=5.0,
                injector_bhp_margin=None,
                wlpr_margin=345.0,
            ),
        ),
        constraints=ConstraintState(
            official={"producer_bhp_min": 50.0, "injector_bhp_max": 300.0, "wlpr_max": 500.0},
            search={"producer_lrat_max": 500.0},
        ),
        geology=GeologyContext(block_membership={"P1": "B1"}),
        uncertainty=UncertaintyState(items=("compensation definition unknown",)),
    )


def _horizon() -> HorizonContext:
    return HorizonContext(
        decision_date=datetime.date(2020, 4, 1),
        horizon_end=datetime.date(2020, 12, 1),
        remaining_steps=8,
    )


def _production_envelope(**overrides: object) -> ProductionEnvelope:
    fields = {
        "schema_version": "aios.mas.contracts/1.0.0",
        "round": DecisionRound.ROUND_1,
        "variant_id": "variant-A",
        "config_digest": "c" * 64,
        "state_card": _state_card(),
        "horizon_context": _horizon(),
        "memory_slice": MemorySlice(),
        "allowed_actions": (ProductionAction.HOLD, ProductionAction.INCREASE_OFFTAKE),
        "peer_proposals": (),
    }
    fields.update(overrides)
    return ProductionEnvelope(**fields)  # type: ignore[arg-type]


def _injection_envelope(**overrides: object) -> InjectionEnvelope:
    fields = {
        "schema_version": "aios.mas.contracts/1.0.0",
        "round": DecisionRound.ROUND_1,
        "variant_id": "variant-A",
        "config_digest": "c" * 64,
        "state_card": _state_card(),
        "horizon_context": _horizon(),
        "memory_slice": MemorySlice(),
        "allowed_actions": (InjectionAction.HOLD,),
        "peer_proposals": (),
    }
    fields.update(overrides)
    return InjectionEnvelope(**fields)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# C1 / C2: ROUND_1 rejects non-empty peer_proposals.
# ---------------------------------------------------------------------------


def test_round1_production_envelope_rejects_peer_proposals() -> None:
    with pytest.raises(EnvelopeError):
        _production_envelope(
            peer_proposals=(ProposalReference(role=AgentRole.INJECTION, proposal_digest="d" * 64),)
        )


def test_round1_injection_envelope_rejects_peer_proposals() -> None:
    with pytest.raises(EnvelopeError):
        _injection_envelope(
            peer_proposals=(ProposalReference(role=AgentRole.PRODUCTION, proposal_digest="d" * 64),)
        )


# ---------------------------------------------------------------------------
# C3: ROUND_2 accepts peer proposal reference (and requires one).
# ---------------------------------------------------------------------------


def test_round2_envelope_accepts_peer_proposal_reference() -> None:
    envelope = _production_envelope(
        round=DecisionRound.ROUND_2,
        peer_proposals=(ProposalReference(role=AgentRole.INJECTION, proposal_digest="d" * 64),),
    )
    assert envelope.round is DecisionRound.ROUND_2
    assert len(envelope.peer_proposals) == 1


def test_round2_envelope_requires_non_empty_peer_proposals() -> None:
    with pytest.raises(EnvelopeError):
        _production_envelope(round=DecisionRound.ROUND_2, peer_proposals=())


# ---------------------------------------------------------------------------
# C4 / C5: envelopes carry no baseline field.
# ---------------------------------------------------------------------------


def test_production_envelope_has_no_baseline_field() -> None:
    field_names = {f.name for f in dataclasses.fields(ProductionEnvelope)}
    assert "baseline" not in field_names
    assert "baseline_proposal" not in field_names


def test_injection_envelope_has_no_baseline_field() -> None:
    field_names = {f.name for f in dataclasses.fields(InjectionEnvelope)}
    assert "baseline" not in field_names
    assert "baseline_proposal" not in field_names


def test_injection_envelope_has_no_economic_context_field() -> None:
    field_names = {f.name for f in dataclasses.fields(InjectionEnvelope)}
    assert "economic_context" not in field_names


def test_production_envelope_has_economic_context_field() -> None:
    field_names = {f.name for f in dataclasses.fields(ProductionEnvelope)}
    assert "economic_context" in field_names


def test_envelope_digest_is_stable_for_equivalent_state_card_instances() -> None:
    # Two independently constructed but content-identical StateCard instances
    # must yield the same envelope digest: only state_card.digest() enters the
    # envelope's semantic payload, never raw filesystem/object identity.
    a = _production_envelope(state_card=_state_card())
    b = _production_envelope(state_card=_state_card())
    assert a.digest() == b.digest()


def test_envelope_digest_is_order_invariant_for_allowed_actions() -> None:
    a = _production_envelope(
        allowed_actions=(ProductionAction.HOLD, ProductionAction.INCREASE_OFFTAKE)
    )
    b = _production_envelope(
        allowed_actions=(ProductionAction.INCREASE_OFFTAKE, ProductionAction.HOLD)
    )
    assert a.digest() == b.digest()


def test_envelope_with_diagnostic_evidence_digests_deterministically() -> None:
    evidence = EvidenceArtifact(evidence_id="press-1", kind="pressure_diagnostic", payload={"a": 1})
    a = _production_envelope(pressure_diagnostic=evidence)
    b = _production_envelope(pressure_diagnostic=evidence)
    assert a.digest() == b.digest()
    c = _production_envelope()
    assert a.digest() != c.digest()


# ---------------------------------------------------------------------------
# C23: ArbiterEnvelope receives specialist outputs, no numerical controls.
# ---------------------------------------------------------------------------


def _production_proposal(source: str) -> ProductionProposal:
    from aios.mas.common import ProposalSource

    intent = ProductionIntent(
        action=ProductionAction.HOLD,
        direction=Direction.NONE,
        intensity=Intensity.NONE,
        target_wells=(),
        target_regions=(),
        evidence_ids=(),
        uncertainty=UncertaintyLevel.LOW,
        rationale_short="",
    )
    return ProductionProposal(
        schema_version="aios.mas.contracts/1.0.0",
        envelope_digest="e" * 64,
        source=ProposalSource(source),
        intents=(intent,),
        grounding_report=GroundingReport(),
        provenance=ProposalProvenance(),
        audit=ProposalAudit(),
    )


def _injection_proposal(source: str) -> InjectionProposal:
    from aios.mas.common import ProposalSource

    intent = InjectionIntent(
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
    return InjectionProposal(
        schema_version="aios.mas.contracts/1.0.0",
        envelope_digest="e" * 64,
        source=ProposalSource(source),
        intents=(intent,),
        grounding_report=GroundingReport(),
        provenance=ProposalProvenance(),
        audit=ProposalAudit(),
    )


def _arbiter_envelope() -> ArbiterEnvelope:
    production_output = ProductionAgentOutput(
        primary_proposal=_production_proposal("LLM_LIVE"),
        baseline_proposal=_production_proposal("DETERMINISTIC_BASELINE"),
    )
    injection_output = InjectionAgentOutput(
        primary_proposal=_injection_proposal("LLM_LIVE"),
        baseline_proposal=_injection_proposal("DETERMINISTIC_BASELINE"),
    )
    return ArbiterEnvelope(
        schema_version="aios.mas.contracts/1.0.0",
        variant_id="variant-A",
        config_digest="c" * 64,
        state_card_digest="s" * 64,
        production_output=production_output,
        injection_output=injection_output,
        horizon_context=_horizon(),
        memory_slice=MemorySlice(),
        simulation_budget=SimulationBudget(max_candidates=8),
    )


def test_arbiter_envelope_holds_specialist_outputs() -> None:
    envelope = _arbiter_envelope()
    assert envelope.production_output.primary_proposal.role is AgentRole.PRODUCTION
    assert envelope.injection_output.primary_proposal.role is AgentRole.INJECTION


def test_arbiter_envelope_has_no_numerical_control_fields() -> None:
    field_names = {f.name.lower() for f in dataclasses.fields(ArbiterEnvelope)}
    assert "producers" not in field_names
    assert "injectors" not in field_names
    assert "producer_controls" not in field_names
    assert "injector_controls" not in field_names


def test_arbiter_envelope_digest_is_deterministic() -> None:
    assert _arbiter_envelope().digest() == _arbiter_envelope().digest()
