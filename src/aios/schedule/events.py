"""Модель управляющих воздействий.

Расписание -- это упорядоченный список шагов по датам, в каждом из которых
задаются управления добывающих и нагнетательных скважин. Ничего сверх того,
что реально поддерживается колодой Model Y: `WCONPROD` и `WCONINJE`.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from math import isfinite
from typing import Any

DEFAULTED = "1*"


class WellRole(StrEnum):
    """Роль скважины в текущем состоянии поля."""

    PRODUCER = "PRODUCER"
    INJECTOR = "INJECTOR"


class WellStatus(StrEnum):
    """Статус скважины в управляющем ключевом слове."""

    OPEN = "OPEN"
    SHUT = "SHUT"
    STOP = "STOP"


def natural_key(name: str) -> tuple[Any, ...]:
    """Ключ сортировки, при котором '9' идёт перед '10'.

    Совпадает по смыслу с `natural_key` официального калькулятора, поэтому
    порядок скважин в расписании и в экономическом входе одинаков.
    """

    parts = re.split(r"(\d+)", name)
    return tuple(int(part) if part.isdigit() else part for part in parts if part != "")


@dataclass(frozen=True, slots=True)
class ProducerControl:
    """Одна строка `WCONPROD`."""

    well: str
    status: WellStatus
    mode: str
    liquid_rate: float | None = None
    bhp_limit: float | None = None

    def __post_init__(self) -> None:
        for field_name, value in (("liquid_rate", self.liquid_rate), ("bhp_limit", self.bhp_limit)):
            if value is not None and not isfinite(value):
                raise ValueError(f"producer {self.well}: {field_name} must be finite")

    @property
    def role(self) -> WellRole:
        return WellRole.PRODUCER


@dataclass(frozen=True, slots=True)
class InjectorControl:
    """Одна строка `WCONINJE`."""

    well: str
    phase: str
    status: WellStatus
    mode: str
    surface_rate: float | None = None
    bhp_limit: float | None = None

    def __post_init__(self) -> None:
        for field_name, value in (
            ("surface_rate", self.surface_rate),
            ("bhp_limit", self.bhp_limit),
        ):
            if value is not None and not isfinite(value):
                raise ValueError(f"injector {self.well}: {field_name} must be finite")

    @property
    def role(self) -> WellRole:
        return WellRole.INJECTOR


@dataclass(frozen=True, slots=True)
class ScheduleStep:
    """Дата и все управления, вступающие в силу на эту дату.

    Пустой шаг -- законная и частая конструкция: он просто продвигает время
    симулятора и создаёт отчётный шаг, не меняя управления.
    """

    date: date
    producers: tuple[ProducerControl, ...] = ()
    injectors: tuple[InjectorControl, ...] = ()

    @property
    def is_empty(self) -> bool:
        return not self.producers and not self.injectors

    def canonical(self) -> "ScheduleStep":
        """Убрать дословные повторы и упорядочить скважины.

        Колода вправе повторить одно и то же воздействие дважды -- Model Y так
        и делает 01.01.2015. Такой повтор ничего не значит и в файл не
        переносится. А вот два *разных* управления одной скважиной на одну
        дату остаются на месте: это противоречие политики, и его должны
        увидеть ворота, а не тихо съесть генератор.
        """

        return ScheduleStep(
            date=self.date,
            producers=tuple(
                sorted(dict.fromkeys(self.producers), key=lambda c: natural_key(c.well))
            ),
            injectors=tuple(
                sorted(dict.fromkeys(self.injectors), key=lambda c: natural_key(c.well))
            ),
        )


@dataclass(frozen=True, slots=True)
class Schedule:
    """Расписание управления на окне кейса."""

    steps: tuple[ScheduleStep, ...]

    @property
    def dates(self) -> tuple[date, ...]:
        return tuple(step.date for step in self.steps)

    @property
    def controlled_wells(self) -> tuple[str, ...]:
        wells = {c.well for step in self.steps for c in step.producers}
        wells |= {c.well for step in self.steps for c in step.injectors}
        return tuple(sorted(wells, key=natural_key))

    def canonical(self) -> "Schedule":
        """Детерминированный вид: шаги по возрастанию даты, скважины -- по
        натуральному ключу. Один и тот же набор воздействий всегда даёт один
        и тот же файл и, значит, один и тот же SHA."""

        ordered = sorted(self.steps, key=lambda step: step.date)
        return Schedule(tuple(step.canonical() for step in ordered))


def build_schedule(steps: Iterable[ScheduleStep]) -> Schedule:
    """Собрать расписание, объединив управления, попавшие на одну дату."""

    merged: dict[date, tuple[list[ProducerControl], list[InjectorControl]]] = {}
    order: list[date] = []
    for step in steps:
        if step.date not in merged:
            merged[step.date] = ([], [])
            order.append(step.date)
        producers, injectors = merged[step.date]
        producers.extend(step.producers)
        injectors.extend(step.injectors)
    return Schedule(
        tuple(
            ScheduleStep(day, tuple(merged[day][0]), tuple(merged[day][1])) for day in sorted(order)
        )
    )
