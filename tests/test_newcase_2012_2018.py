"""Новый операционный контракт 2012-2018 вокруг существующего движка.

Тесты идут первыми: ни одно поведение ниже ещё не написано.
"""

from __future__ import annotations

import itertools
from datetime import date

import pytest

from aios.newcase.calendar import REPAIRS, WellAvailability, WellAvailabilityCalendar
from aios.newcase.capacity import FieldCapacity, project_to_capacity
from aios.newcase.grid import management_months

# --- 1..3 месячная сетка ----------------------------------------------------


def test_grid_has_exactly_84_months() -> None:
    assert len(management_months()) == 84


def test_grid_starts_2012_01() -> None:
    assert management_months()[0] == date(2012, 1, 1)


def test_grid_ends_2018_12() -> None:
    assert management_months()[-1] == date(2018, 12, 1)


def test_grid_is_monthly_and_strictly_increasing() -> None:
    months = management_months()
    assert all(a < b for a, b in itertools.pairwise(months))
    assert {(d.year, d.month) for d in months} == {
        (y, m) for y in range(2012, 2019) for m in range(1, 13)
    }


# --- 4..7 полевые ограничения и их граница ---------------------------------


def test_liquid_cap_is_active_at_the_start_of_2012() -> None:
    assert FieldCapacity.at(date(2012, 1, 1)).liquid_max == 4000.0


def test_liquid_cap_is_still_active_in_december_2014() -> None:
    assert FieldCapacity.at(date(2014, 12, 1)).liquid_max == 4000.0


def test_liquid_cap_is_gone_from_january_2015() -> None:
    assert FieldCapacity.at(date(2015, 1, 1)).liquid_max is None


def test_injection_cap_follows_the_same_boundary() -> None:
    assert FieldCapacity.at(date(2014, 12, 1)).injection_max == 4000.0
    assert FieldCapacity.at(date(2015, 1, 1)).injection_max is None


# --- 8..9 ремонты -----------------------------------------------------------


def test_there_are_exactly_eight_repairs() -> None:
    assert len(REPAIRS) == 8


@pytest.mark.parametrize(("well", "shut", "back"), [(r.well, r.shut, r.back) for r in REPAIRS])
def test_every_repair_shuts_at_its_start_and_returns_at_its_return(
    well: str, shut: date, back: date
) -> None:
    calendar = WellAvailabilityCalendar()
    before = date(shut.year - 1, shut.month, 1) if shut.month else shut

    assert calendar.is_available(well, before)
    assert not calendar.is_available(well, shut)
    assert calendar.is_available(well, back)


def test_interval_is_half_open_start_inclusive_return_exclusive() -> None:
    """[shut, back): закрыт с даты остановки, доступен с даты возврата."""
    calendar = WellAvailabilityCalendar()

    assert not calendar.is_available("40", date(2013, 4, 1))
    assert not calendar.is_available("40", date(2013, 7, 1))
    assert calendar.is_available("40", date(2013, 8, 1))


def test_forced_state_names_the_repair_that_closed_the_well() -> None:
    calendar = WellAvailabilityCalendar()

    assert calendar.forced_state("40", date(2013, 5, 1)) is WellAvailability.FORCED_SHUT
    assert calendar.forced_state("40", date(2013, 8, 1)) is WellAvailability.AVAILABLE


def test_a_well_without_repairs_is_always_available() -> None:
    calendar = WellAvailabilityCalendar()

    assert calendar.is_available("1", date(2013, 5, 1))
    assert calendar.is_available("1", date(2018, 12, 1))


# --- 10..12 ремонт сильнее любого предложения -------------------------------


def test_hold_cannot_keep_a_well_open_through_its_repair() -> None:
    calendar = WellAvailabilityCalendar()
    requested = {"40": 150.0, "1": 100.0}

    allowed = calendar.enforce(requested, date(2013, 5, 1))

    assert allowed["40"] is None
    assert allowed["1"] == 100.0


def test_an_agent_cannot_reopen_a_well_under_forced_repair() -> None:
    calendar = WellAvailabilityCalendar()

    allowed = calendar.enforce({"26": 500.0}, date(2014, 11, 1))

    assert allowed["26"] is None


def test_a_returned_well_may_be_commanded_again() -> None:
    calendar = WellAvailabilityCalendar()

    allowed = calendar.enforce({"26": 120.0}, date(2015, 2, 1))

    assert allowed["26"] == 120.0


# --- 15..16 проектор в допустимое множество ---------------------------------


def test_allocator_never_exceeds_the_liquid_cap_before_2015() -> None:
    requested = {f"p{i}": 200.0 for i in range(30)}  # 6000 > 4000

    projected = project_to_capacity(requested, cap=4000.0, merit={})

    assert sum(projected.values()) <= 4000.0 + 1e-9


def test_allocator_is_not_clipped_after_the_caps_are_released() -> None:
    requested = {f"p{i}": 200.0 for i in range(30)}

    projected = project_to_capacity(requested, cap=None, merit={})

    assert sum(projected.values()) == pytest.approx(6000.0)


def test_allocator_keeps_oil_rich_wells_and_cuts_watery_ones_first() -> None:
    """Не пропорциональное урезание: слабый по нефти теряет первым."""
    requested = {"oil": 2000.0, "water": 2000.0, "mid": 2000.0}
    merit = {"oil": 0.9, "mid": 0.5, "water": 0.05}

    projected = project_to_capacity(requested, cap=4000.0, merit=merit)

    assert sum(projected.values()) <= 4000.0 + 1e-9
    assert projected["oil"] == 2000.0
    assert projected["water"] < projected["oil"]
    assert projected["water"] < projected["mid"]


def test_allocator_is_not_proportional_scaling() -> None:
    requested = {"oil": 2000.0, "water": 2000.0}
    merit = {"oil": 0.9, "water": 0.05}

    projected = project_to_capacity(requested, cap=2000.0, merit=merit)

    assert projected != pytest.approx({"oil": 1000.0, "water": 1000.0})
    assert projected["oil"] > projected["water"]
