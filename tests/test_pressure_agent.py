"""Pressure/Voidage Agent proposes from StateCard only."""

from __future__ import annotations

import datetime

import pytest

from aios.agents.pressure_agent import (
    PRESSURE_AGENT_VERSION,
    PressureAgent,
    PressureAgentError,
    ProposalType,
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


def _producer(*, bhp: float | None = 55.0) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id="P1",
        role=WellRole.PRODUCER,
        status="OPEN",
        current_target=300.0,
        control_mode="LRAT",
        bhp=bhp,
        wlpr=300.0,
        wopr=220.0,
        wwpr=80.0,
        wwir=0.0,
        wct=0.2667,
        wopt=1000.0,
        wwpt=400.0,
        wwit=0.0,
        producer_bhp_margin=None if bhp is None else bhp - 50.0,
        injector_bhp_margin=None,
        wlpr_margin=200.0,
    )


def _injector(*, well_id: str = "I1", bhp: float | None = 250.0) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id=well_id,
        role=WellRole.INJECTOR,
        status="OPEN",
        current_target=150.0,
        control_mode="RATE",
        bhp=bhp,
        wlpr=0.0,
        wopr=0.0,
        wwpr=0.0,
        wwir=150.0,
        wct=0.0,
        wopt=0.0,
        wwpt=0.0,
        wwit=700.0,
        producer_bhp_margin=None,
        injector_bhp_margin=None if bhp is None else 300.0 - bhp,
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


def test_healthy_field_returns_hold() -> None:
    proposal = PressureAgent().propose(_card())

    assert proposal.metadata["state_card_digest"] == _card().digest()
    assert proposal.metadata["agent_version"] == PRESSURE_AGENT_VERSION
    assert proposal.proposal_type == ProposalType.HOLD
    assert proposal.well_ids == ()
    assert proposal.reason.pressure_condition == "PRESSURE_SUPPORT_STABLE"
    assert proposal.confidence == pytest.approx(0.7)
    assert proposal.risk_flags == ()


def test_producer_near_bhp_limit_returns_pressure_support_proposal() -> None:
    proposal = PressureAgent().propose(_card(wells=(_producer(bhp=51.0), _injector())))

    assert proposal.proposal_type == ProposalType.INJECTION_INCREASE
    assert proposal.well_ids == ("I1",)
    assert proposal.reason.pressure_condition == "PRESSURE_SUPPORT_NEEDED"
    assert proposal.reason.expected_effect == "Increase pressure support around weak producers."
    assert "PRESSURE_SUPPORT_NEEDED" in proposal.risk_flags


def test_injector_near_cap_returns_capacity_warning_without_increase() -> None:
    proposal = PressureAgent().propose(_card(wells=(_producer(), _injector(bhp=299.8))))

    assert proposal.proposal_type == ProposalType.HOLD
    assert proposal.well_ids == ("I1",)
    assert proposal.reason.pressure_condition == "INJECTOR_CAPACITY_LIMITED"
    assert "INJECTOR_CAPACITY_LIMITED" in proposal.risk_flags


def test_missing_state_card_pressure_data_fails_closed() -> None:
    with pytest.raises(PressureAgentError, match="P1: missing WBHP"):
        PressureAgent().propose(_card(wells=(_producer(bhp=None), _injector())))


def test_same_state_card_produces_same_proposal_digest() -> None:
    first = PressureAgent().propose(_card(wells=(_producer(bhp=51.0), _injector())))
    second = PressureAgent().propose(_card(wells=(_producer(bhp=51.0), _injector())))

    assert first.to_json() == second.to_json()
    assert first.digest() == second.digest()


def test_agent_reports_pressure_risk_not_official_constraint_violation() -> None:
    proposal = PressureAgent().propose(_card(wells=(_producer(bhp=51.0), _injector())))
    payload = proposal.to_json()

    assert "violations" not in payload
    assert "official_checks" not in payload
    assert all("VIOLATION" not in flag for flag in proposal.risk_flags)
