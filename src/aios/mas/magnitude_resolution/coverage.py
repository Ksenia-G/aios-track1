"""Численное представление и раздача слотов для coverage-кандидатов.

Coverage-кандидат физически проверяет НАПРАВЛЕНИЕ, которое специалист
рассмотрел, но не рекомендует. Ему не нужна вся лестница величин: полная
лестница -- инструмент поиска внутри предпочтённой гипотезы, а здесь
проверяется сам факт, что направление не опровергнуто.

Два правила задают всё.

1. ПОТОЛОК. Представители берутся только из тех вложенных ступеней
   качественной шкалы, что не выше силы, ЗАЯВЛЕННОЙ специалистом. Взять более
   сильную ступень значило бы усилить чужую структурную гипотезу за автора.

2. ГРАНИЦЫ. Внутри потолка берутся сильнейшие ступени каждого качественного
   уровня, от слабого к сильному, и в конце -- слабейшая ступень сетки. Это
   прямое следствие того, что шкала упорядочена, а сетки вложены; никакого
   конкретного множителя правило не знает и знать не может.

Слоты раздаются ПОЛНЫМИ раундами: раунд, который нельзя выдать всем ячейкам
своего класса, не начинается вовсе. Поэтому глубина ячейки не зависит от
позиции внутри раунда, и перестановка INCREASE <-> REDUCE не меняет ни одного
числа -- симметрия falsification обеспечена конструкцией, а не сортировкой.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from aios.mas.common import Intensity
from aios.mas.coverage import ScopeClass
from aios.mas.intents import ProductionAction
from aios.mas.magnitude_resolution.neighbourhood import producer_factor_grid

#: Качественная шкала от слабого к сильному. Порядок, а не величины.
INTENSITY_LADDER: tuple[Intensity, ...] = (
    Intensity.SMALL,
    Intensity.MODERATE,
    Intensity.AGGRESSIVE,
)

#: Слоты физического бюджета, зарезервированные под предпочтённые гипотезы.
#: Ровно столько же agent-слотов было и до покрытия: предпочтение ничего не
#: теряет из-за того, что покрытие появилось.
PREFERRED_RESERVE = 6

#: Слоты под coverage-кандидатов. Заимствованию не подлежат ни в одну сторону;
#: неиспользованные сгорают. Размер выбран как минимальный, при котором обе
#: BROAD-ячейки получают одинаковую гарантированную глубину даже когда
#: требуются все четыре ячейки сразу.
COVERAGE_RESERVE = 6


class CoverageAllocationError(ValueError):
    """Раздача coverage-слотов нарушила собственный контракт."""


def _strongest(grid: Sequence[float], action: ProductionAction) -> float:
    """Самая сильная ступень сетки: вверх -- наибольшая, вниз -- наименьшая."""
    return max(grid) if action is ProductionAction.INCREASE_OFFTAKE else min(grid)


def _weakest(grid: Sequence[float], action: ProductionAction) -> float:
    return min(grid) if action is ProductionAction.INCREASE_OFFTAKE else max(grid)


def ceiling_representatives(action: ProductionAction, authored: Intensity) -> tuple[float, ...]:
    """Представители величины под ЗАЯВЛЕННЫМ потолком качественной силы.

    Возвращает пустой кортеж, если у действия нет сетки: отсутствие
    представителей -- это отсутствие coverage-кандидата, а не повод подставить
    чужую величину.
    """
    action = ProductionAction(action)
    authored = Intensity(authored)
    if authored not in INTENSITY_LADDER:
        return ()
    ceiling_index = INTENSITY_LADDER.index(authored)
    representatives: list[float] = []
    for tier in INTENSITY_LADDER[: ceiling_index + 1]:
        grid = producer_factor_grid(action, tier)
        if not grid:
            continue
        rung = _strongest(grid, action)
        if rung not in representatives:
            representatives.append(rung)
    ceiling_grid = producer_factor_grid(action, authored)
    if ceiling_grid:
        weakest = _weakest(ceiling_grid, action)
        if weakest not in representatives:
            representatives.append(weakest)
    return tuple(representatives)


@dataclass(frozen=True, slots=True)
class CoverageOrigin:
    """Одна LLM-авторизованная рассмотренная альтернатива как источник кандидатов."""

    bundle_digest: str
    action: ProductionAction
    scope: ScopeClass
    authored_intensity: Intensity

    def representatives(self) -> tuple[float, ...]:
        return ceiling_representatives(self.action, self.authored_intensity)


def allocate_coverage(
    origins: Sequence[CoverageOrigin], slots: int = COVERAGE_RESERVE
) -> dict[str, int]:
    """Сколько представителей получает каждый источник. Полные раунды.

    Раунд 1 -- по одному каждому источнику. Раунд 2 -- по второму КАЖДОМУ
    BROAD-источнику, симметрично для обоих направлений. Дальше -- честная
    ротация по всем источникам. Неполный раунд не начинается, поэтому остаток
    слотов сгорает, а не достаётся тому, кто оказался первым в сортировке.
    """
    counts = {origin.bundle_digest: 0 for origin in origins}
    if not origins or slots <= 0:
        return counts
    capacity = {origin.bundle_digest: len(origin.representatives()) for origin in origins}

    def _round(members: Sequence[CoverageOrigin], remaining: int) -> int:
        eligible = [o for o in members if counts[o.bundle_digest] < capacity[o.bundle_digest]]
        if not eligible or len(eligible) > remaining:
            return 0
        for origin in eligible:
            counts[origin.bundle_digest] += 1
        return len(eligible)

    remaining = slots
    granted = _round(origins, remaining)
    remaining -= granted
    if granted:
        broad = [o for o in origins if o.scope is ScopeClass.BROAD]
        remaining -= _round(broad, remaining)
        while remaining > 0:
            step = _round(origins, remaining)
            if step == 0:
                break
            remaining -= step
    return counts


def coverage_funnel(origins: Iterable[CoverageOrigin], counts: dict[str, int]) -> dict[str, int]:
    """Наблюдаемость: сколько источников требовало слоты и сколько их получило."""
    origins = tuple(origins)
    return {
        "coverage_origins": len(origins),
        "coverage_origins_served": sum(1 for o in origins if counts.get(o.bundle_digest, 0)),
        "coverage_slots_used": sum(counts.values()),
        "coverage_slots_forfeited": max(COVERAGE_RESERVE - sum(counts.values()), 0),
    }


__all__ = [
    "COVERAGE_RESERVE",
    "INTENSITY_LADDER",
    "PREFERRED_RESERVE",
    "CoverageAllocationError",
    "CoverageOrigin",
    "allocate_coverage",
    "ceiling_representatives",
    "coverage_funnel",
]
