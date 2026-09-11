"""Каноническое соответствие между датой управления и отчётной строкой.

Три величины, которые легко перепутать:

* **дата управления** -- когда команда вступает в силу в колоде;
* **операционный интервал** -- отрезок времени, в течение которого поле
  работало под этим управлением;
* **отчётная строка** -- то, что вернул симулятор; она датируется КОНЦОМ
  своего интервала (`chdd/trajectory.py`, `tests/test_chdd_interval_stamp.py`).

Отсюда единственное правило: строка описывает интервал ``[предыдущая метка,
своя метка]``, и судить её нужно по НАЧАЛУ этого интервала. Строка на
2015-01-01 описывает декабрь 2014 -- ещё под инфраструктурным лимитом; строка
на 2012-01-01 описывает декабрь 2011 -- это история.

Весь сдвиг живёт здесь. В Guardian не должно остаться ни одного сравнения
отчётной метки с датой управления.
"""

from __future__ import annotations

import itertools
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class ReportInterval:
    """Операционный интервал, который описывает одна отчётная строка."""

    start: date
    end: date

    @property
    def stamp(self) -> date:
        """Метка строки -- конец интервала."""
        return self.end


def report_intervals(stamps: Sequence[date]) -> tuple[ReportInterval, ...]:
    """Разложить упорядоченные метки отчёта в операционные интервалы.

    У самой первой метки предыдущей нет, поэтому интервала она не описывает и
    судить её не по чему.
    """
    ordered = list(stamps)
    return tuple(
        ReportInterval(start=previous, end=current)
        for previous, current in itertools.pairwise(ordered)
    )


def cap_applies_to_report(interval: ReportInterval, *, caps_from: date, caps_until: date) -> bool:
    """Работал ли отчётный интервал под инфраструктурным пределом.

    ``caps_until`` исключающая: операция, начавшаяся в эту дату, уже свободна.
    """
    return caps_from <= interval.start < caps_until


__all__ = ["ReportInterval", "cap_applies_to_report", "report_intervals"]
