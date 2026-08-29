"""Детерминированная численная окрестность вокруг структурной гипотезы.

Специалист выдаёт только структуру: действие, направление, интенсивность и
целевые скважины. Численный перебор внутри этой структурной оболочки --
работа резолвера, и он остаётся полностью детерминированным. Ни одно значение
отсюда не приходит от модели.

До этого модуля одна структурная гипотеза превращалась ровно в один численный
кандидат: интенсивность отображалась в единственный anchor. На эпохе с одной
гипотезой это означало одну проверяемую политику при бюджете 8.

Сетки заданы как множители к ТЕКУЩЕМУ управлению, а не как абсолютные ставки:
поиск обязан оставаться осмысленным на любом совместимом StateCard, а не
только на одной колоде. Ни списка скважин, ни отпечатка состояния, ни
результатов исторических экспериментов здесь нет и быть не может.
"""

from __future__ import annotations

from collections.abc import Sequence

from aios.mas.common import Intensity
from aios.mas.intents import ProductionAction
from aios.schedule.events import natural_key

#: Верхняя граница числа шагов согласованной лестницы при нескольких
#: масштабирующих намерениях: варианты перебираются совместно, а не
#: декартовым произведением, иначе число кандидатов растёт как произведение
#: сеток и перестаёт быть ограниченным.
MAX_MULTI_INTENT_STEPS = 8

#: Верхняя граница числа подмножеств целей. Комбинаторику обязан ограничивать
#: сам поиск: авторизованный набор целей задаёт LLM, и он может быть большим.
MAX_TARGET_SUBSETS = 7

#: Множители подъёма отбора. Вложены по интенсивности: SMALL уже MODERATE,
#: MODERATE уже AGGRESSIVE. Каждая сетка содержит прежний единственный anchor
#: своей интенсивности (1.10 / 1.50 / 5.00), поэтому прежняя политика остаётся
#: достижимой, а не заменяется соседями.
_INCREASE_GRIDS: dict[Intensity, tuple[float, ...]] = {
    Intensity.SMALL: (1.10, 1.25),
    Intensity.MODERATE: (1.10, 1.25, 1.50, 2.00),
    Intensity.AGGRESSIVE: (1.10, 1.25, 1.50, 2.00, 3.00, 5.00),
}

#: Множители снижения отбора, той же вложенной формы; прежние anchors
#: 0.90 / 0.80 / 0.60 остаются членами своих сеток.
_REDUCE_GRIDS: dict[Intensity, tuple[float, ...]] = {
    Intensity.SMALL: (0.90, 0.85),
    Intensity.MODERATE: (0.90, 0.85, 0.80, 0.70),
    Intensity.AGGRESSIVE: (0.90, 0.85, 0.80, 0.70, 0.60),
}

_GRIDS_BY_ACTION = {
    ProductionAction.INCREASE_OFFTAKE: _INCREASE_GRIDS,
    ProductionAction.REDUCE_OFFTAKE: _REDUCE_GRIDS,
}


def producer_factor_grid(action: ProductionAction, intensity: Intensity) -> tuple[float, ...]:
    """Множители, которые стоит проверить для этого действия и интенсивности.

    Пустой кортеж означает «расширять нечего»: действие не масштабирующее,
    и резолвер обязан остаться на своём прежнем единственном пути.
    """
    grids = _GRIDS_BY_ACTION.get(ProductionAction(action))
    if grids is None:
        return ()
    return grids.get(Intensity(intensity), ())


def target_subsets(wells: Sequence[str]) -> tuple[tuple[str, ...], ...]:
    """Детерминированные подмножества авторизованных целей.

    Первым идёт ПОЛНЫЙ набор: прежнее поведение обязано остаться первым
    кандидатом, а не потеряться среди соседей. Дальше -- одиночные скважины и
    наборы без одной, то есть более узкие и почти полные варианты той же
    гипотезы. Новых скважин здесь не появляется никогда: подмножество по
    построению есть подмножество.
    """
    ordered = tuple(sorted(dict.fromkeys(wells), key=natural_key))
    if not ordered:
        return ()
    if len(ordered) == 1:
        return (ordered,)

    subsets: list[tuple[str, ...]] = [ordered]
    seen = {ordered}

    def _add(subset: tuple[str, ...]) -> None:
        if subset and subset not in seen:
            seen.add(subset)
            subsets.append(subset)

    for well in ordered:
        _add((well,))
    if len(ordered) >= 3:
        for well in ordered:
            _add(tuple(item for item in ordered if item != well))
    return tuple(subsets[:MAX_TARGET_SUBSETS])


def expansion_plan(
    action: ProductionAction,
    intensity: Intensity,
    wells: Sequence[str],
    anchor: float,
) -> tuple[tuple[tuple[str, ...], float], ...]:
    """Пары (подмножество целей, множитель) в порядке разнообразия.

    Первой идёт прежняя пара «полный набор + anchor интенсивности», чтобы
    ранее выигрывавшая политика оставалась в наборе и оставалась первой.
    Дальше пары обходятся по диагонали (сумма индексов), что раздвигает
    одновременно и размер подмножества, и величину шага: слоты не уходят на
    почти одинаковые управления.
    """
    grid = producer_factor_grid(action, intensity)
    subsets = target_subsets(wells)
    if not grid or not subsets:
        return ()

    # Прежний единственный множитель приходит из профиля -- единственного
    # источника правды по anchors. Выводить его из формы сетки нельзя:
    # сетка расширена соседями, и «самый дальний» член ей больше не равен.
    plan: list[tuple[tuple[str, ...], float]] = [(subsets[0], anchor)]
    seen = {(subsets[0], anchor)}
    for total in range(len(subsets) + len(grid)):
        for subset_index in range(len(subsets)):
            factor_index = total - subset_index
            if not 0 <= factor_index < len(grid):
                continue
            pair = (subsets[subset_index], grid[factor_index])
            if pair not in seen:
                seen.add(pair)
                plan.append(pair)
    return tuple(plan)


__all__ = [
    "MAX_MULTI_INTENT_STEPS",
    "MAX_TARGET_SUBSETS",
    "expansion_plan",
    "producer_factor_grid",
    "target_subsets",
]
