"""Границы обязательных ремонтов: календарь организатора против END-stamp.

Две разные оси, которые легко перепутать:

* **дата управления** -- когда команда вступает в силу в колоде. Здесь
  действует календарь организатора, интервал ``[shut, back)``: закрыта с даты
  остановки, снова управляема с даты возврата.
* **дата отчётной строки** -- симулятор датирует строку КОНЦОМ интервала,
  поэтому строка на дату возврата описывает ещё ремонтный месяц.

Отсюда правило: судить по отчётной строке нужно с оглядкой на END-stamp, а
команду возврата задерживать на месяц НЕЛЬЗЯ. Эти тесты держат обе оси врозь.
"""

from __future__ import annotations

from datetime import date

import pytest

from aios.newcase.calendar import REPAIRS, WellAvailabilityCalendar
from aios.newcase.guardian import drop_non_flowing_violations
from aios.newcase.policy import build_monthly_schedule

ALL_WELLS = {r.well for r in REPAIRS}
CALENDAR = WellAvailabilityCalendar()


def _month_before(day: date) -> date:
    return date(day.year - 1, 12, 1) if day.month == 1 else date(day.year, day.month - 1, 1)


def _month_after(day: date) -> date:
    return date(day.year + 1, 1, 1) if day.month == 12 else date(day.year, day.month + 1, 1)


@pytest.fixture(scope="module")
def schedule():
    return build_monthly_schedule(
        producers={w: 100.0 for w in sorted(ALL_WELLS)},
        injectors={},
        merit={},
        calendar=CALENDAR,
    )


# --- ось управления: календарь организатора ---------------------------------


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.shut}")
def test_control_availability_follows_the_organizer_calendar(repair) -> None:
    assert CALENDAR.is_available(repair.well, _month_before(repair.shut))
    assert not CALENDAR.is_available(repair.well, repair.shut)
    assert not CALENDAR.is_available(repair.well, _month_before(repair.back))
    assert CALENDAR.is_available(repair.well, repair.back)
    assert CALENDAR.is_available(repair.well, _month_after(repair.back))


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.shut}")
def test_shut_is_effective_on_the_official_shut_date(repair, schedule) -> None:
    step = next(s for s in schedule.steps if s.date == repair.shut)

    assert repair.well in step.shut_wells
    assert all(c.well != repair.well for c in step.producers)


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.back}")
def test_return_is_effective_on_the_official_return_date_not_a_month_later(
    repair, schedule
) -> None:
    """Возврат не сдвигается на месяц: END-stamp касается отчёта, не команды."""
    step = next(s for s in schedule.steps if s.date == repair.back)
    control = next((c for c in step.producers if c.well == repair.well), None)

    assert repair.well in step.opened_wells
    assert control is not None, "скважина обязана снова получить управление"
    assert control.liquid_rate == 100.0


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.shut}")
def test_the_well_is_commanded_the_month_before_its_repair(repair, schedule) -> None:
    step = next(s for s in schedule.steps if s.date == _month_before(repair.shut))

    assert any(c.well == repair.well for c in step.producers)


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.shut}")
def test_no_command_survives_anywhere_inside_the_repair(repair, schedule) -> None:
    for step in schedule.steps:
        if repair.shut <= step.date < repair.back:
            assert all(c.well != repair.well for c in step.producers), (repair.well, step.date)


# --- ось отчёта: признак работы берётся у симулятора ------------------------


class _V:
    def __init__(self, day: date, well: str) -> None:
        self.date, self.well_name, self.constraint = day.isoformat(), well, "producer_bhp_min"


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.shut}")
def test_a_closed_well_is_not_failed_on_its_bottomhole_pressure(repair) -> None:
    """Внутри ремонта дебита нет, значит предел давления неприменим."""
    inside = _V(_month_after(repair.shut), repair.well)
    rates = {(inside.date, repair.well): -0.0}

    assert drop_non_flowing_violations((inside,), rates) == ()


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.back}")
def test_the_row_stamped_on_the_return_date_still_reports_no_flow(repair) -> None:
    """Строка на дату возврата описывает последний ремонтный месяц."""
    boundary = _V(repair.back, repair.well)
    rates = {(boundary.date, repair.well): -0.0}

    assert drop_non_flowing_violations((boundary,), rates) == ()


@pytest.mark.parametrize("repair", REPAIRS, ids=lambda r: f"{r.well}@{r.back}")
def test_once_the_well_flows_again_it_is_judged(repair) -> None:
    working = _V(_month_after(repair.back), repair.well)
    rates = {(working.date, repair.well): 100.0}

    assert drop_non_flowing_violations((working,), rates) == (working,)


def test_another_well_is_never_suppressed_by_someone_elses_repair() -> None:
    other = _V(date(2013, 5, 1), "1")
    rates = {(other.date, "1"): 32.98, ("2013-05-01", "40"): -0.0}

    assert drop_non_flowing_violations((other,), rates) == (other,)


def test_a_well_with_no_reported_rate_is_still_judged() -> None:
    """Молчание не повод снимать проверку."""
    unknown = _V(date(2013, 5, 1), "7")

    assert drop_non_flowing_violations((unknown,), {}) == (unknown,)


def test_the_two_axes_stay_separate() -> None:
    """Команда возвращается 2013-08-01; работы в том интервале ещё не было."""
    repair = next(r for r in REPAIRS if r.well == "40")
    row = _V(repair.back, "40")

    assert CALENDAR.is_available("40", repair.back)  # ось управления
    assert drop_non_flowing_violations((row,), {(row.date, "40"): -0.0}) == ()  # ось отчёта
