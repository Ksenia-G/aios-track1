"""Инфраструктурные пределы поля и проектор в допустимое множество.

До 2015-01-01 поле физически не может поднимать больше 4000 м3/сут жидкости и
закачивать больше 4000 м3/сут воды. С 2015-01-01 эти два предела сняты;
поскважинные ограничения остаются и здесь не трогаются.

Проектор не масштабирует все скважины пропорционально -- организатор прямо
называет это плохим инженерным решением. Дефицитная мощность отдаётся туда, где
она даёт нефть: скважины упорядочиваются по заслуге (merit), и урезание идёт
снизу вверх. Заслуга приходит из уже существующей диагностики, а не выдумывается
здесь; при её отсутствии порядок вырождается в равномерный, и это честно
записано как деградация, а не как политика.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date

#: Дата, с которой инфраструктурные пределы поля сняты.
CAPS_RELEASED_FROM = date(2015, 1, 1)

FIELD_LIQUID_MAX = 4000.0
FIELD_INJECTION_MAX = 4000.0


@dataclass(frozen=True, slots=True)
class FieldCapacity:
    """Пределы поля, действующие на дату."""

    liquid_max: float | None
    injection_max: float | None

    @classmethod
    def at(cls, day: date) -> FieldCapacity:
        if day >= CAPS_RELEASED_FROM:
            return cls(liquid_max=None, injection_max=None)
        return cls(liquid_max=FIELD_LIQUID_MAX, injection_max=FIELD_INJECTION_MAX)

    @property
    def capped(self) -> bool:
        return self.liquid_max is not None or self.injection_max is not None


def project_to_capacity(
    requested: Mapping[str, float | None],
    *,
    cap: float | None,
    merit: Mapping[str, float],
) -> dict[str, float]:
    """Уложить запрошенные расходы в предел поля, сохранив лучшие по нефти.

    Скважины сортируются по убыванию заслуги; каждая получает столько, сколько
    просила, пока остаётся бюджет, и ровно остаток -- на границе. Слабые по
    нефти теряют первыми, сильные не трогаются вовсе.
    """
    live = {well: float(rate) for well, rate in requested.items() if rate is not None}
    total = sum(live.values())
    if cap is None or total <= cap:
        return live

    order = sorted(live, key=lambda well: (-merit.get(well, 0.0), well))
    budget = float(cap)
    projected: dict[str, float] = {}
    for well in order:
        share = min(live[well], max(budget, 0.0))
        projected[well] = share
        budget -= share
    return projected


__all__ = [
    "CAPS_RELEASED_FROM",
    "FIELD_INJECTION_MAX",
    "FIELD_LIQUID_MAX",
    "FieldCapacity",
    "project_to_capacity",
]
