"""Трактовые проверки нового кейса по РЕАЛИЗОВАННЫМ величинам поля.

Официальный `ConstraintGuardian` заморожен и не трогается: он проверяет
поскважинные реализованные пределы. Инфраструктурные пределы поля -- отдельное
ограничение нового кейса, поэтому они проверяются рядом, на тех же отчётных
шагах, и по фактическим `FLPR`/`FWIR`, а не по заказанным уставкам: предел
нарушает то, что поле реально подняло и закачало.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from aios.newcase.capacity import FieldCapacity

FIELD_LIQUID_MAX = "field_liquid_max"
FIELD_INJECTION_MAX = "field_injection_max"


@dataclass(frozen=True, slots=True)
class FieldViolation:
    """Один нарушенный инфраструктурный предел на одном отчётном шаге."""

    date: str
    constraint: str
    measured_value: float
    limit: float

    def to_json(self) -> dict[str, object]:
        return {
            "date": self.date,
            "constraint": self.constraint,
            "measured_value": self.measured_value,
            "limit": self.limit,
        }


@dataclass(frozen=True, slots=True)
class FieldTrajectoryReport:
    """Итог по всей траектории."""

    checks: int
    failed: int
    violations: tuple[FieldViolation, ...]

    @property
    def passed(self) -> bool:
        return self.failed == 0

    def to_json(self) -> dict[str, object]:
        return {
            "checks": self.checks,
            "failed": self.failed,
            "violations": [item.to_json() for item in self.violations],
        }


@dataclass(frozen=True, slots=True)
class FieldTrajectoryGuardian:
    """Проверить каждый отчётный шаг против пределов, действующих на его дату."""

    def evaluate(
        self, trajectory: Iterable[tuple[date, float | None, float | None]]
    ) -> FieldTrajectoryReport:
        checks = 0
        violations: list[FieldViolation] = []
        for day, liquid, injection in trajectory:
            capacity = FieldCapacity.at(day)
            for value, limit, name in (
                (liquid, capacity.liquid_max, FIELD_LIQUID_MAX),
                (injection, capacity.injection_max, FIELD_INJECTION_MAX),
            ):
                if limit is None or value is None:
                    continue
                checks += 1
                if value > limit:
                    violations.append(
                        FieldViolation(
                            date=day.isoformat(),
                            constraint=name,
                            measured_value=float(value),
                            limit=float(limit),
                        )
                    )
        return FieldTrajectoryReport(
            checks=checks, failed=len(violations), violations=tuple(violations)
        )


def drop_unavailable_violations(violations, calendar):
    """Убрать замечания по скважинам, закрытым обязательным ремонтом.

    Замороженный `ConstraintGuardian` судит каждую скважину на каждом отчётном
    шаге и об обязательных ремонтах не знает: у закрытой скважины забойное
    давление читается как 0 и выглядит нарушением предела 50 бар. Режима у неё
    нет, ограничивать нечего -- замечание снимается здесь, а не правкой
    замороженного файла. Снимается ровно оно: любое замечание вне окна ремонта
    и по любой другой скважине остаётся на месте.
    """
    kept = []
    for violation in violations:
        day = date.fromisoformat(str(violation.date))
        well = str(violation.well_name)
        # Отчётная строка датируется концом интервала, поэтому строка на дату
        # возврата описывает ещё ремонтный месяц. Команда возвращается с даты
        # возврата -- интервал доступности остаётся [shut, back).
        judged = _previous_month(day)
        if calendar.is_available(well, day) and calendar.is_available(well, judged):
            kept.append(violation)
    return tuple(kept)


def _previous_month(day: date) -> date:
    year, month = (day.year - 1, 12) if day.month == 1 else (day.year, day.month - 1)
    return date(year, month, 1)


def trajectory_from_summary(
    dates: Sequence[object], liquid: Sequence[float | None], injection: Sequence[float | None]
) -> list[tuple[date, float | None, float | None]]:
    """Разложить сводку прогона в последовательность (дата, FLPR, FWIR)."""
    out: list[tuple[date, float | None, float | None]] = []
    for index, stamp in enumerate(dates):
        day = stamp.date() if hasattr(stamp, "date") else stamp
        out.append(
            (
                day,
                liquid[index] if index < len(liquid) else None,
                injection[index] if index < len(injection) else None,
            )
        )
    return out


__all__ = [
    "FIELD_INJECTION_MAX",
    "FIELD_LIQUID_MAX",
    "FieldTrajectoryGuardian",
    "FieldTrajectoryReport",
    "FieldViolation",
    "drop_unavailable_violations",
    "trajectory_from_summary",
]
