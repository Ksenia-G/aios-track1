"""Ось времени: дата управления, операционный интервал, отчётная строка.

Три разные величины, которые нельзя сравнивать напрямую. Строка сводки
датируется КОНЦОМ интервала (см. `tests/test_chdd_interval_stamp.py` и
`chdd/trajectory.py`), поэтому строка на 2015-01-01 описывает операцию декабря
2014 года -- ещё под инфраструктурным лимитом, -- а строка на 2012-01-01
описывает декабрь 2011 года, то есть историю, которую новый лимит не судит.

Guardian обязан спрашивать «был ли интервал этой строки под лимитом», а не
«лежит ли метка строки между двумя датами управления».
"""

from __future__ import annotations

from datetime import date

import pytest

from aios.newcase.timeaxis import ReportInterval, cap_applies_to_report, report_intervals

CAPS_FROM = date(2012, 1, 1)
CAPS_UNTIL = date(2015, 1, 1)


def _applies(previous: date, stamp: date) -> bool:
    return cap_applies_to_report(
        ReportInterval(start=previous, end=stamp), caps_from=CAPS_FROM, caps_until=CAPS_UNTIL
    )


# --- 1..4 границы окна ------------------------------------------------------


def test_a_historical_interval_before_2012_is_not_judged() -> None:
    """Строка на 2012-01-01 описывает декабрь 2011: это история."""
    assert not _applies(date(2011, 12, 1), date(2012, 1, 1))


def test_the_first_managed_interval_of_2012_is_judged() -> None:
    assert _applies(date(2012, 1, 1), date(2012, 2, 1))


@pytest.mark.parametrize("month", range(1, 13))
def test_every_2014_operational_interval_is_judged(month: int) -> None:
    start = date(2014, month, 1)
    end = date(2015, 1, 1) if month == 12 else date(2014, month + 1, 1)

    assert _applies(start, end)


def test_the_interval_beginning_2015_01_01_is_not_judged() -> None:
    """Операция января 2015 свободна; её строка датируется февралём."""
    assert not _applies(date(2015, 1, 1), date(2015, 2, 1))


def test_the_row_stamped_2015_01_01_still_belongs_to_capped_operation() -> None:
    """Это и есть off-by-one: декабрь 2014 работал под лимитом."""
    assert _applies(date(2014, 12, 1), date(2015, 1, 1))


# --- 5..6 симметрия и отсутствие сдвига -------------------------------------


def test_liquid_and_injection_share_one_calendar() -> None:
    """Один предикат на оба предела: разойтись они не могут по построению."""
    interval = ReportInterval(start=date(2014, 12, 1), end=date(2015, 1, 1))
    applies = cap_applies_to_report(interval, caps_from=CAPS_FROM, caps_until=CAPS_UNTIL)

    from aios.newcase.capacity import FieldCapacity

    capacity = FieldCapacity.for_report(interval, caps_from=CAPS_FROM, caps_until=CAPS_UNTIL)
    assert applies is (capacity.liquid_max is not None)
    assert applies is (capacity.injection_max is not None)


def test_no_off_by_one_across_the_whole_boundary_neighbourhood() -> None:
    expected = {
        (date(2011, 12, 1), date(2012, 1, 1)): False,
        (date(2012, 1, 1), date(2012, 2, 1)): True,
        (date(2014, 11, 1), date(2014, 12, 1)): True,
        (date(2014, 12, 1), date(2015, 1, 1)): True,
        (date(2015, 1, 1), date(2015, 2, 1)): False,
        (date(2015, 2, 1), date(2015, 3, 1)): False,
    }

    assert {k: _applies(*k) for k in expected} == expected


# --- 7 синтетика и настоящие штампы OPM согласованы -------------------------


def test_intervals_are_derived_from_the_ordered_report_stamps() -> None:
    stamps = [date(2011, 12, 1), date(2012, 1, 1), date(2012, 2, 1)]

    intervals = report_intervals(stamps)

    assert intervals == (
        ReportInterval(start=date(2011, 12, 1), end=date(2012, 1, 1)),
        ReportInterval(start=date(2012, 1, 1), end=date(2012, 2, 1)),
    )


def test_the_first_stamp_opens_no_interval_and_is_never_judged() -> None:
    """У первой строки нет предыдущей: интервала она не описывает."""
    assert report_intervals([date(2012, 1, 1)]) == ()
