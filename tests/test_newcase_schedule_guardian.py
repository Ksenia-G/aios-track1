"""Материализация 84 месяцев и трактовые проверки нового кейса."""

from __future__ import annotations

from datetime import date

from aios.newcase.calendar import WellAvailabilityCalendar
from aios.newcase.guardian import FieldTrajectoryGuardian
from aios.newcase.policy import build_monthly_schedule
from aios.schedule.events import WellStatus

PRODUCERS = {str(w): 100.0 for w in (1, 24, 25, 26, 30, 37, 40, 42, 45)}
INJECTORS = {str(w): 200.0 for w in (2, 4, 7)}


def _schedule():
    return build_monthly_schedule(
        producers=PRODUCERS, injectors=INJECTORS, merit={}, calendar=WellAvailabilityCalendar()
    )


# --- 13..14 SHUT материализуется, RETURN возвращает право управления --------


def test_schedule_covers_exactly_the_84_management_months() -> None:
    steps = _schedule().steps

    assert len(steps) == 84
    assert steps[0].date == date(2012, 1, 1)
    assert steps[-1].date == date(2018, 12, 1)


def test_a_well_in_repair_gets_no_control_line_at_all() -> None:
    """Недоступность выражается WELOPEN; управляющей строки просто нет."""
    step = next(s for s in _schedule().steps if s.date == date(2013, 5, 1))

    assert all(c.well != "40" for c in step.producers)


def test_a_returned_well_is_open_again_with_a_rate() -> None:
    step = next(s for s in _schedule().steps if s.date == date(2013, 8, 1))
    well40 = next(c for c in step.producers if c.well == "40")

    assert well40.status is WellStatus.OPEN
    assert well40.liquid_rate == 100.0


def test_no_month_ever_commands_a_well_that_is_under_repair() -> None:
    calendar = WellAvailabilityCalendar()

    for step in _schedule().steps:
        for control in (*step.producers, *step.injectors):
            assert calendar.is_available(control.well, step.date), (control.well, step.date)


def test_every_repair_window_is_closed_for_its_whole_length() -> None:
    steps = {s.date: s for s in _schedule().steps}
    for well, shut, back in (
        ("40", date(2013, 4, 1), date(2013, 8, 1)),
        ("26", date(2014, 10, 1), date(2015, 2, 1)),
    ):
        for step_date, step in steps.items():
            commanded = {c.well for c in (*step.producers, *step.injectors)}
            if shut <= step_date < back:
                assert well not in commanded, (well, step_date)
        assert well in steps[shut].shut_wells
        assert well in steps[back].opened_wells


# --- 15..16 пределы поля в материализованном расписании ---------------------


def test_commanded_liquid_never_exceeds_the_cap_before_2015() -> None:
    for step in _schedule().steps:
        if step.date >= date(2015, 1, 1):
            continue
        total = sum(c.liquid_rate or 0.0 for c in step.producers)
        assert total <= 4000.0 + 1e-9, step.date


def test_commanded_totals_are_not_clipped_to_4000_after_2015() -> None:
    big = {str(w): 500.0 for w in range(1, 20)}  # 9500 m3/day requested
    schedule = build_monthly_schedule(
        producers=big, injectors=INJECTORS, merit={}, calendar=WellAvailabilityCalendar()
    )

    before = next(s for s in schedule.steps if s.date == date(2014, 12, 1))
    after = next(s for s in schedule.steps if s.date == date(2015, 1, 1))

    assert sum(c.liquid_rate or 0.0 for c in before.producers) <= 4000.0 + 1e-9
    assert sum(c.liquid_rate or 0.0 for c in after.producers) > 4000.0


def test_injection_is_capped_before_2015_and_free_after() -> None:
    big = {str(w): 900.0 for w in (2, 4, 7, 13, 15, 19)}  # 5400 requested
    schedule = build_monthly_schedule(
        producers=PRODUCERS, injectors=big, merit={}, calendar=WellAvailabilityCalendar()
    )

    before = next(s for s in schedule.steps if s.date == date(2014, 12, 1))
    after = next(s for s in schedule.steps if s.date == date(2015, 1, 1))

    assert sum(c.surface_rate or 0.0 for c in before.injectors) <= 4000.0 + 1e-9
    assert sum(c.surface_rate or 0.0 for c in after.injectors) > 4000.0


# --- 17..18 поскважинные пределы сохранены ---------------------------------


def test_producer_bhp_floor_is_written_on_every_open_producer() -> None:
    for step in _schedule().steps:
        for control in step.producers:
            if control.status is WellStatus.OPEN:
                assert control.bhp_limit == 50.0


def test_injector_bhp_ceiling_is_written_on_every_open_injector() -> None:
    for step in _schedule().steps:
        for control in step.injectors:
            if control.status is WellStatus.OPEN:
                assert control.bhp_limit == 300.0


# --- Guardian: трактовые проверки по реализованным величинам ----------------


def test_guardian_passes_a_trajectory_inside_both_caps() -> None:
    """Строка судится по своему интервалу, поэтому ей нужна предыдущая метка."""
    report = FieldTrajectoryGuardian().evaluate(
        [
            (date(2013, 5, 1), 3700.0, 3800.0),
            (date(2013, 6, 1), 3800.0, 3900.0),
            (date(2013, 7, 1), 3900.0, 3950.0),
        ]
    )

    assert report.failed == 0
    # две строки описывают интервалы, каждый судится по двум пределам
    assert report.checks == 4


def test_guardian_rejects_realized_liquid_above_the_cap_before_2015() -> None:
    report = FieldTrajectoryGuardian().evaluate(
        [(date(2013, 5, 1), 100.0, 100.0), (date(2013, 6, 1), 4200.0, 100.0)]
    )

    assert report.failed == 1
    assert report.violations[0].constraint == "field_liquid_max"


def test_guardian_rejects_realized_injection_above_the_cap_before_2015() -> None:
    report = FieldTrajectoryGuardian().evaluate(
        [(date(2014, 11, 1), 100.0, 100.0), (date(2014, 12, 1), 100.0, 4500.0)]
    )

    assert report.failed == 1
    assert report.violations[0].constraint == "field_injection_max"


def test_guardian_does_not_apply_the_caps_after_they_are_released() -> None:
    """Операция января 2015 свободна; её строка датируется февралём."""
    report = FieldTrajectoryGuardian().evaluate(
        [(date(2015, 1, 1), 100.0, 100.0), (date(2015, 2, 1), 9000.0, 9000.0)]
    )

    assert report.failed == 0


def test_guardian_still_caps_december_2014_reported_on_the_2015_stamp() -> None:
    """Ровно тот off-by-one, ради которого ось времени вынесена отдельно."""
    report = FieldTrajectoryGuardian().evaluate(
        [(date(2014, 12, 1), 100.0, 100.0), (date(2015, 1, 1), 9000.0, 100.0)]
    )

    assert report.failed == 1
    assert report.violations[0].constraint == "field_liquid_max"


def test_guardian_never_caps_a_historical_interval() -> None:
    """Строка на 2012-01-01 описывает декабрь 2011: новый предел её не судит."""
    report = FieldTrajectoryGuardian().evaluate(
        [(date(2011, 12, 1), 9000.0, 9000.0), (date(2012, 1, 1), 9000.0, 9000.0)]
    )

    assert report.checks == 0
    assert report.failed == 0


# --- 13b. SHUT-инъектор: OPM требует режим управления даже у закрытой скважины


def test_no_shut_injector_control_line_is_ever_written() -> None:
    """WCONINJE у закрытой скважины невозможен без выдуманной величины.

    OPM отвергает и режим по умолчанию (`Unknown control mode string`), и режим
    без величины (`Tried to set invalid control: RATE`). Поэтому закрытая
    скважина не попадает в WCONINJE вовсе.
    """
    for step in _schedule().steps:
        for control in step.injectors:
            assert control.status is WellStatus.OPEN
            assert control.surface_rate is not None


# --- 13c. недоступность выражается WELOPEN, а не величиной ------------------


def test_a_well_under_repair_is_shut_with_welopen_and_no_control_line(newcase_window_case) -> None:
    """WELOPEN закрывает скважину, не называя ни одной величины.

    WCONINJE для этого не годится: закрытой скважине всё равно пришлось бы
    задать режим с величиной (`Tried to set invalid control: RATE`), то есть
    выдумать уставку. WELOPEN выражает ровно доступность.
    """
    from aios.schedule.generator import render_schedule

    case = newcase_window_case
    schedule = build_monthly_schedule(
        producers={"1": 100.0, "40": 100.0},
        injectors={"26": 200.0},
        merit={},
        calendar=WellAvailabilityCalendar(),
    )
    opening = next(s for s in schedule.steps if s.date == date(2014, 10, 1))
    middle = next(s for s in schedule.steps if s.date == date(2014, 11, 1))

    assert "26" in opening.shut_wells  # WELOPEN пишется на переходе
    assert middle.shut_wells == ()  # и дальше просто держится
    assert all(c.well != "26" for c in (*opening.injectors, *middle.injectors))

    text = render_schedule(schedule, case, policy_id="t")
    block = text.split("01 'OCT' 2014")[1].split("DATES")[0]
    assert "WELOPEN" in block
    assert "'26' 'SHUT'" in block


def test_a_returned_well_is_reopened_with_welopen_and_a_control_line(newcase_window_case) -> None:
    from aios.schedule.generator import render_schedule

    case = newcase_window_case
    schedule = build_monthly_schedule(
        producers={"1": 100.0},
        injectors={"26": 200.0},
        merit={},
        calendar=WellAvailabilityCalendar(),
    )
    step = next(s for s in schedule.steps if s.date == date(2015, 2, 1))

    assert "26" in step.opened_wells
    control = next(c for c in step.injectors if c.well == "26")
    assert control.surface_rate == 200.0

    text = render_schedule(schedule, case, policy_id="t")
    block = text.split("01 'FEB' 2015")[1].split("DATES")[0]
    assert "'26' 'OPEN'" in block


# --- закрытая на ремонт скважина не судится по забойному давлению ------------


def test_a_non_flowing_well_is_not_judged_by_its_bottomhole_pressure() -> None:
    """Признак -- реализованный дебит, а не план ремонта."""
    from aios.newcase.guardian import drop_non_flowing_violations

    class _V:
        def __init__(self, day, well, constraint):
            self.date, self.well_name, self.constraint = day, well, constraint

    violations = (
        _V("2013-05-01", "40", "producer_bhp_min"),
        _V("2013-09-01", "40", "producer_bhp_min"),
        _V("2013-05-01", "1", "producer_bhp_min"),
    )
    rates = {
        ("2013-05-01", "40"): -0.0,
        ("2013-09-01", "40"): 100.0,
        ("2013-05-01", "1"): 32.98,
    }

    kept = drop_non_flowing_violations(violations, rates)

    assert [(v.well_name, v.date) for v in kept] == [("40", "2013-09-01"), ("1", "2013-05-01")]
