"""Признак работающей скважины по тому, что сообщил симулятор.

Ограничение по забойному давлению осмысленно только для скважины, которая в
этом интервале действительно работала. У закрытой скважины режима нет, и
сообщённый ноль давления -- это отсутствие режима, а не нарушение предела.

Признак берётся из реализованного дебита, а не из метаданных ремонта: состояние
симулятора авторитетнее плана и закрывает любую фактически закрытую скважину, а
не только заранее известную. На реальном прогоне закрытая скважина отдаёт
``WLPR = -0.0`` при ``WBHP = 0.0``, тогда как работающая на пределе -- ``WLPR =
32.98`` при ``WBHP = 50.0``.

Эпсилон не нужен: ``-0.0 > 0`` уже ложно.
"""

from __future__ import annotations

from collections.abc import Mapping


def is_flowing_producer(*, liquid_rate: float | None) -> bool:
    """Работала ли добывающая скважина в этом интервале."""
    return liquid_rate is not None and liquid_rate > 0


def judgeable_producers(liquid_rates: Mapping[str, float | None]) -> tuple[str, ...]:
    """Скважины, к которым применимо ограничение по забойному давлению."""
    return tuple(
        well for well, rate in liquid_rates.items() if is_flowing_producer(liquid_rate=rate)
    )


__all__ = ["is_flowing_producer", "judgeable_producers"]
