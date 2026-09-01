"""Constraint Guardian evaluates only official StateCard constraints."""

from __future__ import annotations

import datetime

import pytest

from aios.agents.constraint_guardian import (
    CONSTRAINT_GUARDIAN_VERSION,
    ConstraintEvaluationError,
    ConstraintGuardian,
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
        pressure_trend=1.0,
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


def _producer(*, bhp: float | None = 55.0, wlpr: float | None = 300.0) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id="P1",
        role=WellRole.PRODUCER,
        status="OPEN",
        current_target=300.0,
        control_mode="LRAT",
        bhp=bhp,
        wlpr=wlpr,
        wopr=220.0,
        wwpr=80.0,
        wwir=0.0,
        wct=0.2667,
        wopt=1000.0,
        wwpt=400.0,
        wwit=0.0,
        producer_bhp_margin=None if bhp is None else bhp - 50.0,
        injector_bhp_margin=None,
        wlpr_margin=None if wlpr is None else 500.0 - wlpr,
    )


def _injector(*, bhp: float | None = 250.0) -> WellStateSnapshot:
    return WellStateSnapshot(
        well_id="I1",
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
        search={
            "producer_lrat_max": 100.0,
            "candidate_limit": 1.0,
        },
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


def _checks_by_key(report):
    return {(check.well_name, check.constraint): check for check in report.official_checks}


def test_valid_state_card_passes_all_official_constraints() -> None:
    report = ConstraintGuardian().evaluate(_card())

    assert report.metadata["state_card_digest"] == _card().digest()
    assert report.metadata["evaluation_version"] == CONSTRAINT_GUARDIAN_VERSION
    assert report.summary.total_checks == 3
    assert report.summary.passed == 3
    assert report.summary.failed == 0
    assert report.summary.near_binding == 0
    assert report.violations == ()


def test_producer_bhp_below_official_limit_fails() -> None:
    report = ConstraintGuardian().evaluate(_card(wells=(_producer(bhp=49.8), _injector())))
    check = _checks_by_key(report)[("P1", "producer_bhp_min")]

    assert check.measured_value == pytest.approx(49.8)
    assert check.limit == pytest.approx(50.0)
    assert check.margin == pytest.approx(-0.2)
    assert check.status == "FAIL"
    assert report.summary.failed == 1
    assert tuple(item.constraint for item in report.violations) == ("producer_bhp_min",)


def test_injector_pressure_above_official_limit_fails() -> None:
    report = ConstraintGuardian().evaluate(_card(wells=(_producer(), _injector(bhp=301.0))))
    check = _checks_by_key(report)[("I1", "injector_bhp_max")]

    assert check.margin == pytest.approx(-1.0)
    assert check.status == "FAIL"
    assert report.summary.failed == 1
    assert tuple(item.well_name for item in report.violations) == ("I1",)


def test_producer_wlpr_above_official_limit_fails() -> None:
    report = ConstraintGuardian().evaluate(_card(wells=(_producer(wlpr=501.0), _injector())))
    check = _checks_by_key(report)[("P1", "wlpr_max")]

    assert check.measured_value == pytest.approx(501.0)
    assert check.margin == pytest.approx(-1.0)
    assert check.status == "FAIL"
    assert report.summary.failed == 1


def test_near_binding_uses_configurable_margin_without_failing() -> None:
    report = ConstraintGuardian().evaluate(_card(wells=(_producer(wlpr=498.0), _injector())))
    check = _checks_by_key(report)[("P1", "wlpr_max")]

    assert check.status == "PASS"
    assert check.near_binding is True
    assert report.summary.failed == 0
    assert report.summary.near_binding == 1
    assert tuple(item.constraint for item in report.near_binding) == ("wlpr_max",)


def test_missing_required_bhp_fails_closed() -> None:
    with pytest.raises(ConstraintEvaluationError, match="P1: missing WBHP"):
        ConstraintGuardian().evaluate(_card(wells=(_producer(bhp=None), _injector())))


def test_search_constraints_are_not_reported_as_official_violations() -> None:
    report = ConstraintGuardian().evaluate(_card(wells=(_producer(wlpr=300.0), _injector())))

    assert all("lrat" not in check.constraint for check in report.official_checks)
    assert all("candidate" not in check.constraint for check in report.official_checks)
    assert report.summary.failed == 0
