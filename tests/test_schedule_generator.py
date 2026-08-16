"""Генератор расписания: детерминизм, устойчивый SHA, формат записи."""

from datetime import date

import pytest

from aios.cases import Case
from aios.pipeline import build_schedule_for_case
from aios.schedule import (
    InjectorControl,
    ProducerControl,
    Schedule,
    ScheduleRenderError,
    ScheduleStep,
    WellStatus,
    build_schedule,
    format_number,
    natural_key,
    render_schedule,
    schedule_sha256,
)
from aios.schedule.policy import DEFAULT_POLICY_ID


def _producer(well: str, rate: float | None = 100.0) -> ProducerControl:
    return ProducerControl(well, WellStatus.OPEN, "LRAT", rate, 50.0)


def _injector(well: str, rate: float = 200.0) -> InjectorControl:
    return InjectorControl(well, "WATER", WellStatus.OPEN, "RATE", rate, 300.0)


def test_generated_schedule_is_byte_stable(mini_case: Case) -> None:
    first = build_schedule_for_case(mini_case, policy_id=DEFAULT_POLICY_ID)[2]
    second = build_schedule_for_case(mini_case, policy_id=DEFAULT_POLICY_ID)[2]

    assert first == second
    assert schedule_sha256(first) == schedule_sha256(second)


def test_schedule_hash_moves_when_a_target_moves(mini_case: Case) -> None:
    schedule, _state, text, _history = build_schedule_for_case(
        mini_case, policy_id=DEFAULT_POLICY_ID
    )
    bumped = Schedule(
        (
            ScheduleStep(
                schedule.steps[0].date,
                producers=(_producer("P1", 101.0),),
                injectors=schedule.steps[0].injectors,
            ),
            *schedule.steps[1:],
        )
    )

    other = render_schedule(bumped, mini_case, policy_id=DEFAULT_POLICY_ID)

    assert schedule_sha256(other) != schedule_sha256(text)


def test_well_order_is_natural_not_lexicographic(mini_case: Case) -> None:
    schedule = Schedule(
        (
            ScheduleStep(
                mini_case.management_start_date,
                producers=(_producer("P10"), _producer("P9"), _producer("P1")),
            ),
            ScheduleStep(mini_case.management_end_date),
        )
    )

    text = render_schedule(schedule, mini_case, policy_id=DEFAULT_POLICY_ID)

    positions = [text.index(f"'{well}'") for well in ("P1", "P9", "P10")]
    assert positions == sorted(positions)
    assert natural_key("P9") < natural_key("P10")


def test_steps_are_sorted_by_date_regardless_of_input_order(mini_case: Case) -> None:
    late = ScheduleStep(mini_case.management_end_date, producers=(_producer("P2"),))
    early = ScheduleStep(mini_case.management_start_date, producers=(_producer("P1"),))

    text = render_schedule(Schedule((late, early)), mini_case, policy_id=DEFAULT_POLICY_ID)

    assert text.index("01 'FEB' 2020") < text.index("01 'APR' 2020")


def test_identical_repeats_collapse_but_conflicts_survive() -> None:
    step = ScheduleStep(
        date(2020, 2, 1),
        producers=(_producer("P1"), _producer("P1"), _producer("P1", 120.0)),
    )

    canonical = build_schedule((step,)).canonical().steps[0]

    assert len(canonical.producers) == 2
    assert {c.liquid_rate for c in canonical.producers} == {100.0, 120.0}


def test_rendered_records_follow_the_keyword_layout(mini_case: Case) -> None:
    schedule = Schedule(
        (
            ScheduleStep(
                mini_case.management_start_date,
                producers=(_producer("P1", 100.0),),
                injectors=(_injector("I1", 250.0),),
            ),
            ScheduleStep(mini_case.management_end_date),
        )
    )

    text = render_schedule(schedule, mini_case, policy_id=DEFAULT_POLICY_ID)

    assert " 'P1' 'OPEN' 'LRAT' 1* 1* 1* 100 1* 50 1* 1* /" in text
    assert " 'I1' 'WATER' 'OPEN' 'RATE' 250 1* 300 1* 1* /" in text
    assert text.index("WCONPROD") < text.index("WCONINJE")
    assert text.endswith("/\n")


def test_render_refuses_a_schedule_outside_the_case_window(mini_case: Case) -> None:
    schedule = Schedule((ScheduleStep(date(2020, 3, 1)), ScheduleStep(date(2020, 4, 1))))

    with pytest.raises(ScheduleRenderError, match="management window starts"):
        render_schedule(schedule, mini_case, policy_id=DEFAULT_POLICY_ID)


def test_render_refuses_a_schedule_that_stops_early(mini_case: Case) -> None:
    schedule = Schedule((ScheduleStep(date(2020, 2, 1)), ScheduleStep(date(2020, 3, 1))))

    with pytest.raises(ScheduleRenderError, match="management window ends"):
        render_schedule(schedule, mini_case, policy_id=DEFAULT_POLICY_ID)


def test_no_change_policy_freezes_the_reconstructed_state(mini_case: Case) -> None:
    schedule, _state, _text, _history = build_schedule_for_case(mini_case, policy_id="NO_CHANGE")

    assert [step.is_empty for step in schedule.steps] == [False, True, True]
    assert {c.well: c.liquid_rate for c in schedule.steps[0].producers} == {
        "P1": 100.0,
        "P2": 100.0,
    }
    assert {c.well: c.surface_rate for c in schedule.steps[0].injectors} == {"I1": 200.0}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, "1*"), (100.0, "100"), (100, "100"), (0.0, "0"), (12.5, "12.5"), (-3.25, "-3.25")],
)
def test_number_formatting_is_single_valued(value: float | None, expected: str) -> None:
    assert format_number(value) == expected
