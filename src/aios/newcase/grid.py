"""Месячная сетка управляемого горизонта 2012-01 .. 2018-12."""

from __future__ import annotations

from datetime import date

MANAGEMENT_START = date(2012, 1, 1)
MANAGEMENT_END = date(2018, 12, 1)
MONTHS = 84


def management_months() -> tuple[date, ...]:
    """Все 84 управляющие даты, по первому числу каждого месяца."""
    return tuple(
        date(
            MANAGEMENT_START.year + (MANAGEMENT_START.month - 1 + offset) // 12,
            (MANAGEMENT_START.month - 1 + offset) % 12 + 1,
            1,
        )
        for offset in range(MONTHS)
    )


__all__ = ["MANAGEMENT_END", "MANAGEMENT_START", "MONTHS", "management_months"]
