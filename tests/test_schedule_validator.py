"""Ворота допуска расписания. Режим отказа -- закрытый."""

from datetime import date

import pytest

from aios.cases import Case
from aios.pipeline import build_schedule_for_case
from aios.schedule import (
    GateStatus,
    InjectorControl,
    ProducerControl,
    Schedule,
    ScheduleStep,
    WellStatus,
    reconstruct_state,
    validate_schedule,
)
from aios.schedule.deck import parse_schedule_file, parse_start_date
from aios.schedule.policy import DEFAULT_POLICY_ID


@pytest.fixture
def mini_state(mini_case: Case):
    start = parse_start_date(mini_case.history.data_path.read_text(encoding="utf-8"))
    deck = parse_schedule_file(mini_case.history.schedule_path, start_date=start)
    return reconstruct_state(deck, mini_case.management_start_date)


def _producer(well: str, rate: float | None) -> ProducerControl:
    return ProducerControl(well, WellStatus.OPEN, "LRAT", rate, 50.0)


def _injector(well: str, rate: float) -> InjectorControl:
    return InjectorControl(well, "WATER", WellStatus.OPEN, "RATE", rate, 300.0)


def _schedule(*steps: ScheduleStep) -> Schedule:
    return Schedule(steps)


def _closing(case: Case) -> ScheduleStep:
    return ScheduleStep(case.management_end_date)


def _gate(report, gate_id: str):
    return next(gate for gate in report.gates if gate.gate_id == gate_id)


def test_baseline_schedule_passes_every_gate(mini_case: Case, mini_state) -> None:
    schedule = build_schedule_for_case(mini_case, policy_id=DEFAULT_POLICY_ID)[0]

    report = validate_schedule(schedule, mini_state, mini_case)

    assert report.is_valid
    assert report.failed == ()


def test_wlpr_at_the_limit_is_accepted(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(mini_case.management_start_date, producers=(_producer("P1", 500.0),)),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert _gate(report, "rate_limits").status is GateStatus.PASS
    assert report.is_valid


def test_wlpr_above_the_limit_is_rejected(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(mini_case.management_start_date, producers=(_producer("P1", 500.0001),)),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert not report.is_valid
    gate = _gate(report, "rate_limits")
    assert gate.status is GateStatus.FAIL
    assert "500.0001" in gate.violations[0]


def test_unknown_well_is_rejected(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(mini_case.management_start_date, producers=(_producer("GHOST", 100.0),)),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert not report.is_valid
    assert "GHOST" in _gate(report, "well_exists").violations[0]


def test_producer_to_injector_conversion_is_rejected(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(mini_case.management_start_date, injectors=(_injector("P1", 150.0),)),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert not report.is_valid
    gate = _gate(report, "no_forbidden_conversion")
    assert "producer -> injector" in gate.violations[0]


def test_injector_to_producer_conversion_is_rejected(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(mini_case.management_start_date, producers=(_producer("I1", 100.0),)),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert "injector -> producer" in _gate(report, "no_forbidden_conversion").violations[0]


def test_out_of_order_dates_are_rejected(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(mini_case.management_start_date),
        ScheduleStep(mini_case.management_end_date),
        ScheduleStep(date(2020, 3, 1)),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert not report.is_valid
    assert _gate(report, "chronological_dates").status is GateStatus.FAIL


def test_step_before_the_window_is_rejected(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(date(2020, 1, 1), producers=(_producer("P1", 100.0),)),
        ScheduleStep(mini_case.management_start_date),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert _gate(report, "date_valid").status is GateStatus.FAIL
    assert _gate(report, "history_untouched").status is GateStatus.FAIL


def test_conflicting_control_on_the_same_well_and_date_is_rejected(
    mini_case: Case, mini_state
) -> None:
    schedule = _schedule(
        ScheduleStep(
            mini_case.management_start_date,
            producers=(_producer("P1", 100.0), _producer("P1", 120.0)),
        ),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert not report.is_valid
    assert "conflicting control" in _gate(report, "no_duplicate_control").violations[0]


def test_disallowed_control_mode_is_rejected(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(
            mini_case.management_start_date,
            producers=(ProducerControl("P1", WellStatus.OPEN, "ORAT", 50.0, 50.0),),
        ),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert "'ORAT' not allowed" in _gate(report, "control_type_allowed").violations[0]


def test_bhp_floor_and_ceiling_are_enforced(mini_case: Case, mini_state) -> None:
    schedule = _schedule(
        ScheduleStep(
            mini_case.management_start_date,
            producers=(ProducerControl("P1", WellStatus.OPEN, "LRAT", 100.0, 40.0),),
            injectors=(InjectorControl("I1", "WATER", WellStatus.OPEN, "RATE", 200.0, 400.0),),
        ),
        _closing(mini_case),
    )

    report = validate_schedule(schedule, mini_state, mini_case)

    assert len(_gate(report, "bhp_limits").violations) == 2


def test_non_finite_target_is_rejected(mini_case: Case, mini_state) -> None:
    step = ScheduleStep(mini_case.management_start_date)
    with pytest.raises(ValueError, match="finite"):
        ProducerControl("P1", WellStatus.OPEN, "LRAT", float("inf"), 50.0)
    assert step.is_empty


def test_field_injection_ceiling_is_enforced(mini_case: Case, mini_state, case_payload) -> None:
    from aios.cases import parse_case

    case_payload["constraints"]["field_injection_rate_max"] = 100.0
    case = parse_case(case_payload, base_dir=mini_case.history.deck_root.parent)
    schedule = _schedule(
        ScheduleStep(case.management_start_date, injectors=(_injector("I1", 250.0),)),
        ScheduleStep(case.management_end_date),
    )

    report = validate_schedule(schedule, mini_state, case)

    assert _gate(report, "field_injection_limit").status is GateStatus.FAIL
