"""Продление управляющей сетки до настроенного конца окна.

Опорная сетка берётся из расписания колоды и кончается там, где кончается
выданная история. Когда окно управления кейса длиннее, штатному контуру не на
чем идти дальше, и горизонт молча обрывается.

Продление не принимает решений: оно повторяет последнее известное управление на
недостающих месяцах. Ни одного нового значения здесь не рождается, поэтому
уставка мимо MagnitudeResolver не проходит. Конец горизонта -- аргумент, не
литерал: модуль ничего не знает ни про конкретный кейс, ни про конкретный год.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date

from aios.schedule.events import Schedule, ScheduleStep


def monthly_grid(start: date, end: date) -> Iterator[date]:
    """Помесячные даты от ``start`` до ``end`` включительно."""
    year, month = start.year, start.month
    while date(year, month, 1) <= end:
        yield date(year, month, 1)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)


def extend_to_horizon(schedule: Schedule, *, end: date) -> Schedule:
    """Дотянуть расписание до ``end``, повторяя последнее управление.

    Уже дошедшее до конца расписание возвращается как есть -- у старого кейса
    поведение обязано совпасть с прежним побайтово.
    """
    steps = list(schedule.steps)
    if not steps or steps[-1].date >= end:
        return schedule

    last = steps[-1]
    for day in monthly_grid(last.date, end):
        if day <= last.date:
            continue
        steps.append(
            ScheduleStep(
                date=day,
                producers=last.producers,
                injectors=last.injectors,
            )
        )
    return Schedule(tuple(steps))


__all__ = ["extend_to_horizon", "monthly_grid"]
