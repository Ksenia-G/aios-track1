"""Штатный контур должен доходить до конца настроенного окна управления.

Опорная сетка берётся из расписания колоды, а оно кончается там, где кончается
выданная история. Для старого кейса это совпадало с концом окна, для нового --
нет, и контур молча заканчивался на 2015-12. Продление представления управления
до `management_end` -- задача общая и настраиваемая: ни года, ни идентификатора
кейса в ней быть не должно.
"""

from __future__ import annotations

import pathlib
from datetime import date

from aios.schedule.events import ProducerControl, Schedule, ScheduleStep, WellStatus
from aios.schedule.horizon import extend_to_horizon, monthly_grid


def _schedule(*days: date) -> Schedule:
    return Schedule(
        tuple(
            ScheduleStep(
                day,
                producers=(
                    ProducerControl(
                        well="1",
                        status=WellStatus.OPEN,
                        mode="LRAT",
                        liquid_rate=100.0,
                        bhp_limit=50.0,
                    ),
                ),
            )
            for day in days
        )
    )


def test_a_grid_that_already_reaches_the_end_is_returned_unchanged() -> None:
    """Старый кейс: продлевать нечего, поведение обязано совпасть с прежним."""
    original = _schedule(date(2014, 1, 1), date(2015, 12, 1))

    extended = extend_to_horizon(original, end=date(2015, 12, 1))

    assert extended == original


def test_a_short_grid_is_carried_forward_to_the_configured_end() -> None:
    extended = extend_to_horizon(_schedule(date(2012, 1, 1)), end=date(2018, 12, 1))

    assert extended.steps[-1].date == date(2018, 12, 1)


def test_the_extended_grid_is_monthly_and_complete() -> None:
    extended = extend_to_horizon(_schedule(date(2012, 1, 1)), end=date(2018, 12, 1))

    assert len(extended.steps) == 84
    assert [s.date for s in extended.steps] == list(
        monthly_grid(date(2012, 1, 1), date(2018, 12, 1))
    )


def test_extension_invents_no_policy_change() -> None:
    """Продление повторяет последнее управление, а не придумывает новое."""
    extended = extend_to_horizon(_schedule(date(2012, 1, 1)), end=date(2018, 12, 1))
    rates = {c.liquid_rate for step in extended.steps for c in step.producers}

    assert rates == {100.0}


def test_history_before_the_window_is_never_touched() -> None:
    original = _schedule(date(2012, 1, 1), date(2012, 2, 1))

    extended = extend_to_horizon(original, end=date(2018, 12, 1))

    assert extended.steps[0] == original.steps[0]
    assert extended.steps[1] == original.steps[1]


def test_the_horizon_comes_from_the_argument_not_from_a_literal_year() -> None:
    """Никакого зашитого 2018: конец окна задаётся вызывающим."""
    extended = extend_to_horizon(_schedule(date(2012, 1, 1)), end=date(2016, 3, 1))

    assert extended.steps[-1].date == date(2016, 3, 1)
    assert len(extended.steps) == 51


def test_monthly_grid_is_inclusive_of_both_ends() -> None:
    grid = list(monthly_grid(date(2014, 11, 1), date(2015, 2, 1)))

    assert grid == [date(2014, 11, 1), date(2014, 12, 1), date(2015, 1, 1), date(2015, 2, 1)]


# --- штатный контур: сетка доходит до конца окна кейса ----------------------


def test_the_old_case_baseline_grid_is_byte_for_byte_what_it_was(model_y_case) -> None:
    """Старый кейс: окно кончается там же, где колода -- продлевать нечего."""
    from aios.pipeline.validation import read_baseline_inputs

    _state, baseline, _hist, _start = read_baseline_inputs(model_y_case)
    grid = baseline.canonical().dates

    assert len(grid) == 24
    assert grid[0] == date(2014, 1, 1)
    assert grid[-1] == date(2015, 12, 1)


def test_a_case_whose_window_outlives_the_deck_still_reaches_its_end(
    long_window_case,
) -> None:
    """Сетка обязана дойти до management_end, а не до конца расписания колоды."""
    from aios.pipeline.validation import read_baseline_inputs

    _state, baseline, _hist, _start = read_baseline_inputs(long_window_case)
    grid = baseline.canonical().dates

    assert grid[-1] == long_window_case.management_end_date


def test_the_extension_carries_the_last_control_without_changing_it(
    long_window_case,
) -> None:
    from aios.pipeline.validation import read_baseline_inputs

    _state, baseline, _hist, _start = read_baseline_inputs(long_window_case)
    steps = {s.date: s for s in baseline.canonical().steps}
    deck_last = steps[date(2020, 4, 1)]
    extended = steps[long_window_case.management_end_date]

    assert extended.producers == deck_last.producers
    assert extended.injectors == deck_last.injectors


def test_the_horizon_is_taken_from_the_case_not_from_a_literal() -> None:
    """В продлении нет ни года, ни идентификатора кейса."""
    source = (
        pathlib.Path(__file__).resolve().parents[1] / "src" / "aios" / "schedule" / "horizon.py"
    ).read_text(encoding="utf-8")

    assert "2018" not in source
    assert "2015" not in source
    assert "field_y" not in source
