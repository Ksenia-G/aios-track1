"""Canonical SweepDiagnostic: behavior-preserving migration from SweepAgent.

Same guarantees as PressureDiagnostic: deterministic evidence service, no
Intent/Proposal/decision authority, no formula/threshold changes.
"""

from __future__ import annotations

import datetime

import pytest

from aios.agents.sweep_agent import SweepAgent, SweepAgentError
from aios.diagnostics import SweepDiagnostic, SweepDiagnosticError
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
        official={"producer_bhp_min": 50.0, "injector_bhp_max": 300.0, "wlpr_max": 500.0},
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


# D2
def test_canonical_import_works() -> None:
    assert SweepDiagnostic is not None


# D8
def test_sweep_diagnostic_is_deterministic() -> None:
    card = _card(wells=(_producer(wlpr=300.0, wopr=60.0, wwpr=240.0, wct=0.8), _injector()))
    first = SweepDiagnostic().propose(card)
    second = SweepDiagnostic().propose(card)
    assert first.to_json() == second.to_json()
    assert first.digest() == second.digest()


def test_sweep_diagnostic_output_is_not_an_intent_or_proposal() -> None:
    from aios.mas.intents import InjectionIntent, ProductionIntent
    from aios.mas.proposals import InjectionProposal, ProductionProposal

    proposal = SweepDiagnostic().propose(_card())
    assert not isinstance(proposal, ProductionIntent | InjectionIntent)
    assert not isinstance(proposal, ProductionProposal | InjectionProposal)


def test_sweep_diagnostic_error_is_the_legacy_error_alias() -> None:
    assert SweepDiagnosticError is SweepAgentError


# D6 / D20 -- legacy vs canonical equivalence across several fixtures.
@pytest.mark.parametrize(
    "wells",
    [
        (_producer(), _injector()),
        (_producer(wlpr=300.0, wopr=60.0, wwpr=240.0, wct=0.8), _injector()),
        (_producer(wlpr=498.0, wopr=150.0, wwpr=348.0, wct=0.7), _injector()),
    ],
)
def test_legacy_and_canonical_are_byte_identical(
    wells: tuple[WellStateSnapshot, ...],
) -> None:
    card = _card(wells=wells)
    legacy = SweepAgent().propose(card)
    canonical = SweepDiagnostic().propose(card)
    assert legacy.to_json() == canonical.to_json()
    assert legacy.digest() == canonical.digest()


def test_missing_state_card_data_fails_closed_identically() -> None:
    with pytest.raises(SweepDiagnosticError, match="P1: missing WWCT"):
        SweepDiagnostic().propose(_card(wells=(_producer(wct=None), _injector())))
