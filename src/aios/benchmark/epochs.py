"""Эпохи решения многошагового прогона.

Правило одно и детерминированное: ``N`` эпох раскладываются по окну
управления и садятся на существующую сетку отчётных шагов колоды. Второй
регулятор частоты намеренно не вводится -- у бенчмарка остаётся ровно одна
ручка ``--decision-steps``, и рамка сравнения не меняет форму.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date


class DecisionEpochError(ValueError):
    """Эпохи решения нельзя разложить по этой сетке."""


def decision_epochs(grid: Sequence[date], steps: int) -> tuple[date, ...]:
    """``T_i = grid[round(i * (n - 1) / N)]`` для ``i = 0 .. N-1``.

    ``grid`` -- канонические даты опорного расписания, они же единственные
    легальные ``effective_date`` (см. ``compile_candidate_schedule``), поэтому
    каждая эпоха по построению является валидной отчётной границей.

    Конец горизонта ``grid[-1]`` эпохой решения не является: последняя эпоха
    обязана оставить хотя бы один интервал до конца окна.
    """
    if steps <= 0:
        raise DecisionEpochError("decision_epochs.steps: must be > 0")
    size = len(grid)
    if size < 2:
        raise DecisionEpochError("decision_epochs.grid: needs at least two report steps")
    if steps > size - 1:
        raise DecisionEpochError(
            f"decision_epochs: {steps} epochs do not fit a grid of {size} report steps"
        )
    epochs = tuple(grid[round(index * (size - 1) / steps)] for index in range(steps))
    if len(set(epochs)) != len(epochs):
        raise DecisionEpochError(
            f"decision_epochs: {steps} epochs collapse onto the same report step"
        )
    return epochs


__all__ = ["DecisionEpochError", "decision_epochs"]
