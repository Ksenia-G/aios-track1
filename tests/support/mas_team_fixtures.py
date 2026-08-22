"""Deterministic team-foundation fixtures over already-frozen MAS contracts.

TEST SUPPORT ONLY -- not production API (see tests/test_team_foundation.py,
TF14/TF15: no aios.* module may import this). Every synthetic identifier is
explicitly prefixed SYN- so nobody mistakes it for a real well/case name.
These factories build minimal valid instances of existing contracts; they
never define a new SimulationRequest/SimulationResult/ProducerControl/
InjectorControl/StateCard type (TF16) -- only reuse the existing ones.
"""

from __future__ import annotations

import datetime

from aios.evaluation.simulation import SimulationRequest
from aios.mas.arbiter import ArbiterDecision, DecisionBundle
from aios.mas.common import (
    CandidateKind,
    DecisionRound,
    Direction,
    Intensity,
    ProposalSource,
    UncertaintyLevel,
)
from aios.mas.envelopes import ArbiterEnvelope, InjectionEnvelope, ProductionEnvelope
from aios.mas.evidence import EvidenceArtifact, HorizonContext, MemorySlice, SimulationBudget
from aios.mas.intents import (
    InjectionAction,
    InjectionIntent,
    ProductionAction,
    ProductionIntent,
)
from aios.mas.magnitude import MagnitudeCandidate
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

SCHEMA_VERSION = "aios.mas.contracts/1.0.0"
SYNTHETIC_VARIANT_ID = "SYN-variant-team-foundation"
SYNTHETIC_CONFIG_DIGEST = "c" * 64


def synthetic_evidence_artifact(
    evidence_id: str = "SYN-evidence-1", kind: str = "SYN-diagnostic"
) -> EvidenceArtifact:
    return EvidenceArtifact(
        evidence_id=evidence_id,
        kind=kind,
        payload={"synthetic": True, "note": "team-foundation test fixture"},
        source_version="tests.support.mas_team_fixtures/1.0.0",
    )


def synthetic_horizon_context() -> HorizonContext:
    return HorizonContext(
        decision_date=datetime.date(2020, 4, 1),
        horizon_end=datetime.date(2020, 12, 1),
        remaining_steps=8,
    )


def synthetic_memory_slice() -> MemorySlice:
    return MemorySlice(records=(synthetic_evidence_artifact("SYN-memory-1"),))


def synthetic_state_card() -> StateCard:
    return StateCard(
        metadata=StateMetadata(
            date=datetime.date(2020, 4, 1),
            case_id="SYN-case",
            run_id="SYN-run-001",
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
                well_id="SYN-P1",
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
            WellStateSnapshot(
                well_id="SYN-I1",
                role=WellRole.INJECTOR,
                status="OPEN",
                current_target=150.0,
                control_mode="RATE",
                bhp=250.0,
                wlpr=0.0,
                wopr=0.0,
                wwpr=0.0,
                wwir=150.0,
                wct=0.0,
                wopt=0.0,
                wwpt=0.0,
                wwit=700.0,
                producer_bhp_margin=None,
                injector_bhp_margin=50.0,
                wlpr_margin=None,
            ),
        ),
        constraints=ConstraintState(
            official={
                "producer_bhp_min": 50.0,
                "injector_bhp_max": 300.0,
                "wlpr_max": 500.0,
            },
            search={"producer_lrat_max": 500.0},
        ),
        geology=GeologyContext(block_membership={"SYN-P1": "SYN-B1", "SYN-I1": "SYN-B1"}),
        uncertainty=UncertaintyState(items=("compensation definition unknown",)),
    )


def synthetic_production_envelope(**overrides: object) -> ProductionEnvelope:
    fields: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "round": DecisionRound.ROUND_1,
        "variant_id": SYNTHETIC_VARIANT_ID,
        "config_digest": SYNTHETIC_CONFIG_DIGEST,
        "state_card": synthetic_state_card(),
        "horizon_context": synthetic_horizon_context(),
        "memory_slice": synthetic_memory_slice(),
        "allowed_actions": (ProductionAction.HOLD, ProductionAction.INCREASE_OFFTAKE),
        "pressure_diagnostic": synthetic_evidence_artifact("SYN-pressure-1", "pressure_diagnostic"),
        "sweep_diagnostic": synthetic_evidence_artifact("SYN-sweep-1", "sweep_diagnostic"),
    }
    fields.update(overrides)
    return ProductionEnvelope(**fields)  # type: ignore[arg-type]


def synthetic_injection_envelope(**overrides: object) -> InjectionEnvelope:
    fields: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "round": DecisionRound.ROUND_1,
        "variant_id": SYNTHETIC_VARIANT_ID,
        "config_digest": SYNTHETIC_CONFIG_DIGEST,
        "state_card": synthetic_state_card(),
        "horizon_context": synthetic_horizon_context(),
        "memory_slice": synthetic_memory_slice(),
        "allowed_actions": (InjectionAction.HOLD, InjectionAction.REALLOCATE_INJECTION),
        "pressure_diagnostic": synthetic_evidence_artifact("SYN-pressure-1", "pressure_diagnostic"),
        "sweep_diagnostic": synthetic_evidence_artifact("SYN-sweep-1", "sweep_diagnostic"),
    }
    fields.update(overrides)
    return InjectionEnvelope(**fields)  # type: ignore[arg-type]


def synthetic_production_hold_intent() -> ProductionIntent:
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


def synthetic_injection_hold_intent() -> InjectionIntent:
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


def synthetic_production_proposal(
    *,
    envelope_digest: str | None = None,
    source: ProposalSource = ProposalSource.DETERMINISTIC_BASELINE,
) -> ProductionProposal:
    return ProductionProposal(
        schema_version=SCHEMA_VERSION,
        envelope_digest=envelope_digest or synthetic_production_envelope().digest(),
        source=source,
        intents=(synthetic_production_hold_intent(),),
        grounding_report=GroundingReport(),
        provenance=ProposalProvenance(),
        audit=ProposalAudit(),
    )


def synthetic_injection_proposal(
    *,
    envelope_digest: str | None = None,
    source: ProposalSource = ProposalSource.DETERMINISTIC_BASELINE,
) -> InjectionProposal:
    return InjectionProposal(
        schema_version=SCHEMA_VERSION,
        envelope_digest=envelope_digest or synthetic_injection_envelope().digest(),
        source=source,
        intents=(synthetic_injection_hold_intent(),),
        grounding_report=GroundingReport(),
        provenance=ProposalProvenance(),
        audit=ProposalAudit(),
    )


def synthetic_production_agent_output() -> ProductionAgentOutput:
    envelope_digest = synthetic_production_envelope().digest()
    return ProductionAgentOutput(
        primary_proposal=synthetic_production_proposal(
            envelope_digest=envelope_digest, source=ProposalSource.DETERMINISTIC_FALLBACK
        ),
        baseline_proposal=synthetic_production_proposal(
            envelope_digest=envelope_digest, source=ProposalSource.DETERMINISTIC_BASELINE
        ),
    )


def synthetic_injection_agent_output() -> InjectionAgentOutput:
    envelope_digest = synthetic_injection_envelope().digest()
    return InjectionAgentOutput(
        primary_proposal=synthetic_injection_proposal(
            envelope_digest=envelope_digest, source=ProposalSource.DETERMINISTIC_FALLBACK
        ),
        baseline_proposal=synthetic_injection_proposal(
            envelope_digest=envelope_digest, source=ProposalSource.DETERMINISTIC_BASELINE
        ),
    )


def synthetic_arbiter_envelope() -> ArbiterEnvelope:
    return ArbiterEnvelope(
        schema_version=SCHEMA_VERSION,
        variant_id=SYNTHETIC_VARIANT_ID,
        config_digest=SYNTHETIC_CONFIG_DIGEST,
        state_card_digest=synthetic_state_card().digest(),
        production_output=synthetic_production_agent_output(),
        injection_output=synthetic_injection_agent_output(),
        horizon_context=synthetic_horizon_context(),
        memory_slice=synthetic_memory_slice(),
        simulation_budget=SimulationBudget(max_candidates=8),
    )


def synthetic_arbiter_decision() -> ArbiterDecision:
    return ArbiterDecision(
        schema_version=SCHEMA_VERSION,
        arbiter_envelope_digest=synthetic_arbiter_envelope().digest(),
        bundles=(
            DecisionBundle(kind=CandidateKind.HOLD),
            DecisionBundle(
                kind=CandidateKind.DETERMINISTIC_BASELINE,
                production_proposal_digest=synthetic_production_proposal().digest(),
                injection_proposal_digest=synthetic_injection_proposal().digest(),
            ),
        ),
    )


def _synthetic_producer(well: str = "SYN-P1", liquid_rate: float = 150.0) -> ProducerControl:
    return ProducerControl(well=well, status=WellStatus.OPEN, mode="LRAT", liquid_rate=liquid_rate)


def _synthetic_injector(well: str = "SYN-I1", surface_rate: float = 200.0) -> InjectorControl:
    return InjectorControl(
        well=well, phase="WATER", status=WellStatus.OPEN, mode="RATE", surface_rate=surface_rate
    )


def synthetic_magnitude_candidate(
    *, kind: CandidateKind = CandidateKind.AGENT_BUNDLE
) -> MagnitudeCandidate:
    return MagnitudeCandidate(
        candidate_kind=kind,
        bundle_digest="b" * 64,
        effective_date=datetime.date(2020, 4, 1),
        producers=(_synthetic_producer("SYN-P1"), _synthetic_producer("SYN-P2", 200.0)),
        injectors=(_synthetic_injector("SYN-I1"),),
        source_intent_digests=("d" * 64,),
        resolver_version="tests.support.mas_team_fixtures/1.0.0",
    )


def synthetic_candidate_set_with_floor() -> tuple[MagnitudeCandidate, ...]:
    """Prepared candidate set that satisfies require_simulation_floor (T3)."""
    return (
        synthetic_magnitude_candidate(kind=CandidateKind.HOLD),
        synthetic_magnitude_candidate(kind=CandidateKind.DETERMINISTIC_BASELINE),
        synthetic_magnitude_candidate(kind=CandidateKind.AGENT_BUNDLE),
    )


def synthetic_hold_carry_forward_pair() -> tuple[
    tuple[tuple[ProducerControl, ...], tuple[InjectorControl, ...]],
    tuple[tuple[ProducerControl, ...], tuple[InjectorControl, ...]],
]:
    """Previous/held control pair that satisfies require_hold_carry_forward (T6).

    Deliberately reordered between previous and held to prove the guard is
    order-independent, while every control value stays identical.
    """
    previous = (
        (_synthetic_producer("SYN-P1"), _synthetic_producer("SYN-P2", 200.0)),
        (_synthetic_injector("SYN-I1"),),
    )
    held = (
        (_synthetic_producer("SYN-P2", 200.0), _synthetic_producer("SYN-P1")),
        (_synthetic_injector("SYN-I1"),),
    )
    return previous, held


def synthetic_ablation_config_pair() -> tuple[dict[str, object], dict[str, object]]:
    """Config pair differing only by llm_enabled -- satisfies T5's guard."""
    base = {
        "variant_id": SYNTHETIC_VARIANT_ID,
        "simulation_budget": 8,
        "resolver_version": "tests.support.mas_team_fixtures/1.0.0",
        "guardian_config_digest": "g" * 64,
        "chdd_config_digest": "c" * 64,
    }
    config_a = {**base, "llm_enabled": True}
    config_b = {**base, "llm_enabled": False}
    return config_a, config_b


def synthetic_simulation_request() -> SimulationRequest:
    """Uses the existing aios.evaluation.SimulationRequest -- not duplicated."""
    return SimulationRequest(
        payload={"synthetic": True, "note": "team-foundation test fixture"},
        seed=17,
        model_digest="SYN-model-digest",
        scenario_digest="SYN-scenario-digest",
        schedule_digest="SYN-schedule-digest",
    )


__all__ = [
    "SCHEMA_VERSION",
    "SYNTHETIC_CONFIG_DIGEST",
    "SYNTHETIC_VARIANT_ID",
    "synthetic_ablation_config_pair",
    "synthetic_arbiter_decision",
    "synthetic_arbiter_envelope",
    "synthetic_candidate_set_with_floor",
    "synthetic_evidence_artifact",
    "synthetic_hold_carry_forward_pair",
    "synthetic_horizon_context",
    "synthetic_injection_agent_output",
    "synthetic_injection_envelope",
    "synthetic_injection_hold_intent",
    "synthetic_injection_proposal",
    "synthetic_magnitude_candidate",
    "synthetic_memory_slice",
    "synthetic_production_agent_output",
    "synthetic_production_envelope",
    "synthetic_production_hold_intent",
    "synthetic_production_proposal",
    "synthetic_simulation_request",
    "synthetic_state_card",
]
