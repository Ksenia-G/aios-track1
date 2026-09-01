"""Sweep/Water-Cut Agent proposes from StateCard only."""

from __future__ import annotations

import datetime

import pytest

from aios.agents.sweep_agent import (
    SWEEP_AGENT_VERSION,
    ProposalType,
    SweepAgent,
    SweepAgentError,
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


def _metadata() -> StateMetadata:
    return StateMetadata(
        date=datetime.date(2020, 4, 1),
        case_id="mini",
        run_id="run-001",
        simulator_digest="example/flow@sha256:" + "0" * 64,
        schedule_hash="a" * 64,
        source_summary_identity="summary-sha256:" + "b" * 64,
        extractor_version="aios.state.extractor/1.0.0",
    )


def _field() -> FieldStateSnapshot:
    return FieldStateSnapshot(
        average_pressure=211.5,
        pressure_trend=0.5,
        oil_rate=120.0,
        water_rate=35.0,
        liquid_rate=155.0,
        water_injection_rate=150.0,
        voidage_production_rate=160.0,
        voidage_production_total=1400.0,
        oil_total=1000.0,
        water_total=400.0,
        water_injection_total=700.0,
        produced_water_available=35.0,
        compensation_value=None,
        compensation_status="NOT_APPLICABLE",
    )


def _producer(
    *,
    well_id: str = "P1",
    wlpr: float | None = 300.0,
    wopr: float | None = 240.0,
    wwpr: float | None = 60.0,
    wct: float | None = 0.2,
) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id=well_id,
        role=WellRole.PRODUCER,
        status="OPEN",
        current_target=300.0,
        control_mode="LRAT",
        bhp=55.0,
        wlpr=wlpr,
        wopr=wopr,
        wwpr=wwpr,
        wwir=0.0,
        wct=wct,
        wopt=1000.0,
        wwpt=400.0,
        wwit=0.0,
        producer_bhp_margin=5.0,
        injector_bhp_margin=None,
        wlpr_margin=None if wlpr is None else 500.0 - wlpr,
    )


def _injector() -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id="I1",
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
    )


def _constraints() -> ConstraintState:
    return ConstraintState(
        official={
            "producer_bhp_min": 50.0,
            "injector_bhp_max": 300.0,
            "wlpr_max": 500.0,
        },
        search={"producer_lrat_max": 500.0, "candidate_limit": 1.0},
        violations=(),
        binding_constraints=(),
        margins={},
    )


def _card(*, wells: tuple[WellStateSnapshot, ...] | None = None) -> StateCard:
    return StateCard(
        metadata=_metadata(),
        field=_field(),
        wells=wells or (_producer(), _injector()),
        constraints=_constraints(),
        geology=GeologyContext(),
        uncertainty=UncertaintyState(),
    )


def test_healthy_low_water_producer_returns_hold() -> None:
    proposal = SweepAgent().propose(_card())

    assert proposal.metadata["state_card_digest"] == _card().digest()
    assert proposal.metadata["agent_version"] == SWEEP_AGENT_VERSION
    assert proposal.proposal_type == ProposalType.HOLD
    assert proposal.well_ids == ()
    assert proposal.reason.water_condition == "WATER_CUT_STABLE"
    assert proposal.reason.oil_efficiency_condition == "OIL_EFFICIENCY_ACCEPTABLE"
    assert proposal.confidence == pytest.approx(0.7)
    assert proposal.risk_flags == ()


def test_high_water_cut_producer_returns_reduce_water_risk() -> None:
    proposal = SweepAgent().propose(
        _card(wells=(_producer(wlpr=300.0, wopr=60.0, wwpr=240.0, wct=0.8), _injector()))
    )

    assert proposal.proposal_type == ProposalType.REDUCE_WATER_RISK
    assert proposal.well_ids == ("P1",)
    assert proposal.reason.water_condition == "HIGH_WATER_CUT"
    assert proposal.reason.oil_efficiency_condition == "LOW_OIL_EFFICIENCY"
    assert (
        proposal.reason.expected_effect == "Reduce produced-water risk from inefficient producers."
    )
    assert "HIGH_WATER_CUT" in proposal.risk_flags


def test_high_liquid_near_limit_and_high_water_cut_returns_sweep_warning() -> None:
    proposal = SweepAgent().propose(
        _card(wells=(_producer(wlpr=498.0, wopr=150.0, wwpr=348.0, wct=0.7), _injector()))
    )

    assert proposal.proposal_type == ProposalType.WATER_SWEEP_WARNING
    assert proposal.well_ids == ("P1",)
    assert proposal.reason.water_condition == "NEAR_WLPR_LIMIT_WITH_HIGH_WATER_CUT"
    assert "NEAR_WLPR_LIMIT" in proposal.risk_flags
    assert all("VIOLATION" not in flag for flag in proposal.risk_flags)


def test_missing_water_cut_fails_closed() -> None:
    with pytest.raises(SweepAgentError, match="P1: missing WWCT"):
        SweepAgent().propose(_card(wells=(_producer(wct=None), _injector())))


def test_same_state_card_produces_same_proposal_digest() -> None:
    state = _card(wells=(_producer(wlpr=300.0, wopr=60.0, wwpr=240.0, wct=0.8), _injector()))

    first = SweepAgent().propose(state)
    second = SweepAgent().propose(state)

    assert first.to_json() == second.to_json()
    assert first.digest() == second.digest()


def test_agent_does_not_emit_official_constraint_violations() -> None:
    proposal = SweepAgent().propose(
        _card(wells=(_producer(wlpr=498.0, wopr=150.0, wwpr=348.0, wct=0.7), _injector()))
    )
    payload = proposal.to_json()

    assert "violations" not in payload
    assert "official_checks" not in payload
    assert all("constraint" not in flag.lower() for flag in proposal.risk_flags)
