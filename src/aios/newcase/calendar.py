"""Календарь обязательных ремонтов.

Ремонт -- детерминированное ограничение доступности, а не предложение. Он
сильнее любого предложения специалиста, предыдущей команды, аллокатора и HOLD:
скважину под ремонтом не может открыть никто.

Интервал читается как ``[shut, back)``: скважина недоступна начиная ровно с
даты остановки и доступна снова начиная ровно с даты возврата. Колода не задаёт
другого соглашения, поэтому оно записано здесь явно.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class WellAvailability(StrEnum):
    """Состояние доступности скважины на дату."""

    AVAILABLE = "AVAILABLE"
    FORCED_SHUT = "FORCED_SHUT"


@dataclass(frozen=True, slots=True)
class Repair:
    """Один обязательный ремонт: ``[shut, back)``."""

    well: str
    shut: date
    back: date

    def covers(self, day: date) -> bool:
        return self.shut <= day < self.back


REPAIRS: tuple[Repair, ...] = (
    Repair("40", date(2013, 4, 1), date(2013, 8, 1)),
    Repair("26", date(2014, 10, 1), date(2015, 2, 1)),
    Repair("30", date(2015, 5, 1), date(2015, 9, 1)),
    Repair("37", date(2015, 12, 1), date(2016, 3, 1)),
    Repair("25", date(2016, 6, 1), date(2016, 10, 1)),
    Repair("24", date(2017, 2, 1), date(2017, 5, 1)),
    Repair("45", date(2017, 9, 1), date(2018, 1, 1)),
    Repair("42", date(2018, 4, 1), date(2018, 9, 1)),
)


@dataclass(frozen=True, slots=True)
class WellAvailabilityCalendar:
    """Доступность скважин по датам."""

    repairs: tuple[Repair, ...] = REPAIRS

    def forced_state(self, well: str, day: date) -> WellAvailability:
        for repair in self.repairs:
            if repair.well == well and repair.covers(day):
                return WellAvailability.FORCED_SHUT
        return WellAvailability.AVAILABLE

    def is_available(self, well: str, day: date) -> bool:
        return self.forced_state(well, day) is WellAvailability.AVAILABLE

    def enforce(self, requested: Mapping[str, float | None], day: date) -> dict[str, float | None]:
        """Снять команду со всех скважин, стоящих в ремонте на эту дату.

        ``None`` означает «величины нет»: скважина закрывается, и никакой
        уставки для неё не печатается.
        """
        return {
            well: (rate if self.is_available(well, day) else None)
            for well, rate in requested.items()
        }


__all__ = ["REPAIRS", "Repair", "WellAvailability", "WellAvailabilityCalendar"]
