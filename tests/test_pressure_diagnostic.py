"""Canonical PressureDiagnostic: behavior-preserving migration from PressureAgent.

PressureDiagnostic is a deterministic evidence/diagnostic service, not a
cognitive MAS agent -- it creates no Intent/Proposal (in the aios.mas sense)
and has no decision authority. No formula, threshold, or physics assumption
is changed by this migration: PressureDiagnostic is a thin compatibility
subclass over the legacy aios.agents.pressure_agent.PressureAgent.
"""

from __future__ import annotations

import datetime

import pytest

from aios.agents.pressure_agent import PressureAgent, PressureAgentError
from aios.diagnostics import PressureDiagnostic, PressureDiagnosticError
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


# D1
def test_canonical_import_works() -> None:
    assert PressureDiagnostic is not None


# D7
def test_pressure_diagnostic_is_deterministic() -> None:
    card = _card(wells=(_producer(bhp=51.0), _injector()))
    first = PressureDiagnostic().propose(card)
    second = PressureDiagnostic().propose(card)
    assert first.to_json() == second.to_json()
    assert first.digest() == second.digest()


# D9-D14 belong to test_diagnostic_authority.py; D15 here for locality.
def test_pressure_diagnostic_output_is_not_an_intent_or_proposal() -> None:
    from aios.mas.intents import InjectionIntent, ProductionIntent
    from aios.mas.proposals import InjectionProposal, ProductionProposal

    proposal = PressureDiagnostic().propose(_card())
    assert not isinstance(proposal, ProductionIntent | InjectionIntent)
    assert not isinstance(proposal, ProductionProposal | InjectionProposal)


def test_pressure_diagnostic_error_is_the_legacy_error_alias() -> None:
    assert PressureDiagnosticError is PressureAgentError


# D5 / D19 -- legacy vs canonical equivalence across several fixtures.
@pytest.mark.parametrize(
    "wells",
    [
        (_producer(), _injector()),
        (_producer(bhp=51.0), _injector()),
        (_producer(), _injector(bhp=299.8)),
    ],
)
def test_legacy_and_canonical_are_byte_identical(
    wells: tuple[WellStateSnapshot, ...],
) -> None:
    card = _card(wells=wells)
    legacy = PressureAgent().propose(card)
    canonical = PressureDiagnostic().propose(card)
    assert legacy.to_json() == canonical.to_json()
    assert legacy.digest() == canonical.digest()


def test_missing_state_card_data_fails_closed_identically() -> None:
    with pytest.raises(PressureDiagnosticError, match="P1: missing WBHP"):
        PressureDiagnostic().propose(_card(wells=(_producer(bhp=None), _injector())))
