"""StateCard v1.0 contract and extraction foundation."""

from __future__ import annotations

import datetime

import pytest

from aios.cases import Case
from aios.opm.summary import SummaryData
from aios.state import (
    ConstraintState,
    FieldStateSnapshot,
    GeologyContext,
    StateCard,
    StateCardError,
    StateExtractor,
    StateMetadata,
    UncertaintyState,
    WellControlSnapshot,
    WellRole,
    WellStateSnapshot,
)


def _metadata() -> StateMetadata:
    return StateMetadata(
        date=datetime.date(2020, 4, 1),
        case_id="mini",
        run_id="run-001",
        simulator_digest="sim-sha256:abc",
        schedule_hash="a" * 64,
    )


def _field() -> FieldStateSnapshot:
    return FieldStateSnapshot(
        average_pressure=211.5,
        pressure_trend=None,
        oil_rate=120.0,
        water_rate=35.0,
        liquid_rate=155.0,
        water_injection_rate=150.0,
        produced_water_available=35.0,
        compensation_value=None,
        compensation_status="UNRESOLVED",
    )


def _well() -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id="P1",
        role=WellRole.PRODUCER,
        status="OPEN",
        current_target=120.0,
        control_mode="LRAT",
        bhp=55.0,
        wlpr=155.0,
        wopr=120.0,
        wwpr=35.0,
        wct=0.2258,
        wopt=1000.0,
        wwpt=400.0,
        wwit=0.0,
    )


def _constraints() -> ConstraintState:
    return ConstraintState(
        official={
            "producer_bhp_min": 50.0,
            "injector_bhp_max": 300.0,
            "wlpr_max": 500.0,
        },
        search={"producer_lrat_max": 500.0},
        violations=(),
        binding_constraints=("producer_bhp_min:P1",),
        margins={"producer_bhp_min:P1": 5.0},
    )


def _card() -> StateCard:
    return StateCard(
        metadata=_metadata(),
        field=_field(),
        wells=(_well(),),
        constraints=_constraints(),
        geology=GeologyContext(block_membership={"P1": "B1"}),
        uncertainty=UncertaintyState(items=("compensation definition unknown",)),
    )


def test_state_card_serializes_to_stable_json_shape() -> None:
    payload = _card().to_json()

    assert payload["schema_version"] == "1.0"
    assert payload["metadata"]["date"] == "2020-04-01"
    assert payload["metadata"]["simulator_digest"] == "sim-sha256:abc"
    assert payload["field"]["compensation"]["status"] == "UNRESOLVED"
    assert payload["wells"][0]["role"] == "producer"
    assert payload["wells"][0]["realized"]["BHP"] == 55.0
    assert payload["constraints"]["official"]["producer_bhp_min"] == 50.0
    assert payload["constraints"]["search"]["producer_lrat_max"] == 500.0
    assert payload["geology"]["block_membership"] == {"P1": "B1"}
    assert payload["uncertainty"]["items"] == ["compensation definition unknown"]


def test_state_card_round_trips_from_json() -> None:
    card = _card()

    assert StateCard.from_json(card.to_json()) == card


def test_state_card_digest_is_deterministic_and_tracks_provenance() -> None:
    first = _card().digest()
    second = StateCard.from_json(_card().to_json()).digest()
    changed = StateCard(
        metadata=StateMetadata(
            date=datetime.date(2020, 4, 1),
            case_id="mini",
            run_id="run-001",
            simulator_digest="sim-sha256:other",
            schedule_hash="a" * 64,
        ),
        field=_field(),
        wells=(_well(),),
        constraints=_constraints(),
        geology=GeologyContext(block_membership={"P1": "B1"}),
        uncertainty=UncertaintyState(items=("compensation definition unknown",)),
    ).digest()

    assert first == second
    assert first != changed


def test_missing_required_metadata_is_rejected() -> None:
    payload = _card().to_json()
    del payload["metadata"]["schedule_hash"]

    with pytest.raises(StateCardError, match="metadata.schedule_hash"):
        StateCard.from_json(payload)


def test_missing_required_well_field_is_rejected() -> None:
    payload = _card().to_json()
    del payload["wells"][0]["realized"]["WLPR"]

    with pytest.raises(StateCardError, match=r"wells\[0\]\.realized\.WLPR"):
        StateCard.from_json(payload)


def test_official_constraints_are_separate_from_search_constraints() -> None:
    constraints = _card().constraints

    assert "producer_bhp_min" in constraints.official
    assert "producer_lrat_max" not in constraints.official
    assert "producer_lrat_max" in constraints.search


def _model_time(year: int, month: int, day: int) -> datetime.datetime:
    return datetime.datetime(year, month, day)  # noqa: DTZ001


def _summary() -> SummaryData:
    fields = {
        "FPR": 0,
        "FOPR": 1,
        "FWPR": 2,
        "FLPR": 3,
        "FWIR": 4,
    }
    wells = {
        ("WBHP", "P1"): 5,
        ("WLPR", "P1"): 6,
        ("WOPR", "P1"): 7,
        ("WWPR", "P1"): 8,
        ("WWCT", "P1"): 9,
        ("WOPT", "P1"): 10,
        ("WWPT", "P1"): 11,
        ("WWIT", "P1"): 12,
    }
    return SummaryData(
        start=_model_time(2020, 1, 1),
        dates=(_model_time(2020, 3, 1), _model_time(2020, 4, 1)),
        wells=("P1",),
        units={},
        _field_index=fields,
        _well_index=wells,
        _params=(
            (200.0, 90.0, 20.0, 110.0, 100.0, 60.0, 110.0, 90.0, 20.0, 0.18, 900.0, 300.0, 0.0),
            (
                211.5,
                120.0,
                35.0,
                155.0,
                150.0,
                55.0,
                155.0,
                120.0,
                35.0,
                0.2258,
                1000.0,
                400.0,
                0.0,
            ),
        ),
    )


def test_extractor_builds_state_card_from_summary_and_explicit_controls(mini_case: Case) -> None:
    card = StateExtractor().extract(
        summary=_summary(),
        case=mini_case,
        run_id="run-001",
        simulator_digest="sim-sha256:abc",
        schedule_hash="a" * 64,
        well_controls={
            "P1": WellControlSnapshot(
                role=WellRole.PRODUCER,
                status="OPEN",
                current_target=120.0,
                control_mode="LRAT",
            )
        },
        step=-1,
    )

    assert card.metadata.date == datetime.date(2020, 4, 1)
    assert card.field.average_pressure == pytest.approx(211.5)
    assert card.field.pressure_trend == pytest.approx(11.5)
    assert card.field.produced_water_available == pytest.approx(35.0)
    assert card.wells[0].well_id == "P1"
    assert card.wells[0].bhp == pytest.approx(55.0)
    assert "compensation definition unknown" in card.uncertainty.items


def test_extractor_fails_closed_without_required_control_context(mini_case: Case) -> None:
    with pytest.raises(StateCardError, match="missing control context for well 'P1'"):
        StateExtractor().extract(
            summary=_summary(),
            case=mini_case,
            run_id="run-001",
            simulator_digest="sim-sha256:abc",
            schedule_hash="a" * 64,
            well_controls={},
        )
