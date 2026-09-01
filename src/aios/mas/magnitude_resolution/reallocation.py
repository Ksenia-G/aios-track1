"""Многопарное перераспределение закачки -- численная часть.

Резолвер поддерживал ровно одну пару донор/получатель. Экономический механизм,
отделяющий текущий MAS от опорного результата, устроен иначе: несколько
одновременных переносов при почти неизменной суммарной закачке.

Контракт сохранения: сколько закачки снято с доноров, столько же роздано
получателям. Закачка здесь не создаётся и не исчезает -- иначе «перенос»
незаметно превратился бы в масштабирование, и сравнивать варианты было бы
не с чем. Донор не уходит ниже нуля; если после клиппинга отдавать нечего,
кандидат объявляется непредставимым, а не молча вырождается в HOLD.

Ни списка скважин, ни привязки к конкретному кейсу здесь нет: доноры и
получатели приходят из структурной оболочки, авторизованной специалистом.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from aios.mas.common import Intensity
from aios.schedule.events import InjectorControl, natural_key

#: Потолок числа виртуальных вариантов перераспределения на одну гипотезу.
MAX_REALLOCATION_VIRTUAL_CANDIDATES = 12

#: Доли переноса, вложенные по интенсивности. Каждая сетка содержит прежний
#: anchor своей интенсивности, поэтому ранее достижимая политика остаётся
#: достижимой, а не заменяется соседями.
_TRANSFER_GRIDS: dict[Intensity, tuple[float, ...]] = {
    Intensity.SMALL: (0.20, 0.35),
    Intensity.MODERATE: (0.20, 0.35, 0.60, 0.825),
    Intensity.AGGRESSIVE: (0.20, 0.35, 0.60, 0.825, 0.975),
}


class ReallocationError(ValueError):
    """Перераспределение нельзя материализовать в этих управлениях."""


def transfer_fraction_grid(intensity: Intensity) -> tuple[float, ...]:
    """Доли переноса, которые стоит проверить для этой интенсивности."""
    return _TRANSFER_GRIDS.get(Intensity(intensity), ())


def donor_recipient_splits(
    donors: Sequence[str], recipients: Sequence[str]
) -> tuple[tuple[tuple[str, ...], tuple[str, ...]], ...]:
    """Разбиения донор/получатель в порядке разнообразия, с потолком.

    Первым идёт полный набор: самая многопарная гипотеза проверяется прежде
    своих сужений. Дальше -- одиночные доноры на всех получателях и все доноры
    на одиночных получателях. Комбинаторный взрыв закрыт потолком, а не
    надеждой на небольшие входы.
    """
    ordered_donors = tuple(sorted(dict.fromkeys(donors), key=natural_key))
    ordered_recipients = tuple(sorted(dict.fromkeys(recipients), key=natural_key))
    # Скважина не может быть одновременно донором и получателем.
    overlap = set(ordered_donors) & set(ordered_recipients)
    ordered_donors = tuple(w for w in ordered_donors if w not in overlap)
    ordered_recipients = tuple(w for w in ordered_recipients if w not in overlap)
    if not ordered_donors or not ordered_recipients:
        return ()

    splits: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    seen: set[tuple[tuple[str, ...], tuple[str, ...]]] = set()

    def _add(donor_set: tuple[str, ...], recipient_set: tuple[str, ...]) -> None:
        if not donor_set or not recipient_set:
            return
        key = (donor_set, recipient_set)
        if key in seen:
            return
        seen.add(key)
        splits.append(key)

    _add(ordered_donors, ordered_recipients)
    for donor in ordered_donors:
        _add((donor,), ordered_recipients)
    for recipient in ordered_recipients:
        _add(ordered_donors, (recipient,))
    for donor in ordered_donors:
        for recipient in ordered_recipients:
            _add((donor,), (recipient,))
    return tuple(splits[:MAX_REALLOCATION_VIRTUAL_CANDIDATES])


def materialize_reallocation(
    *,
    donors: Sequence[str],
    recipients: Sequence[str],
    fraction: float,
    controls: Mapping[str, InjectorControl],
) -> dict[str, InjectorControl]:
    """Собрать управления нагнетателей для одного перераспределения.

    Каждый донор отдаёт ``fraction`` своей текущей закачки; суммарно снятое
    делится между получателями поровну. Скважины вне авторизованного набора
    не трогаются вообще.
    """
    if not 0.0 < fraction <= 1.0:
        raise ReallocationError(f"reallocation: transfer fraction {fraction!r} is out of range")
    donor_set = tuple(sorted(dict.fromkeys(donors), key=natural_key))
    recipient_set = tuple(sorted(dict.fromkeys(recipients), key=natural_key))
    if not donor_set or not recipient_set:
        raise ReallocationError("reallocation: both a donor and a recipient are required")
    if set(donor_set) & set(recipient_set):
        raise ReallocationError("reallocation: a well cannot both donate and receive")

    updated = dict(controls)
    removed = 0.0
    for well in donor_set:
        control = updated.get(well)
        if control is None:
            raise ReallocationError(f"reallocation: donor {well!r} is not an injector on the card")
        rate = control.surface_rate or 0.0
        delta = min(rate * fraction, rate)
        removed += delta
        updated[well] = replace(control, surface_rate=rate - delta)

    if removed <= 0.0:
        raise ReallocationError(
            "reallocation: donors carry no injection to move; the candidate is not representable"
        )

    share = removed / len(recipient_set)
    for well in recipient_set:
        control = updated.get(well)
        if control is None:
            raise ReallocationError(
                f"reallocation: recipient {well!r} is not an injector on the card"
            )
        updated[well] = replace(control, surface_rate=(control.surface_rate or 0.0) + share)
    return updated


__all__ = [
    "MAX_REALLOCATION_VIRTUAL_CANDIDATES",
    "ReallocationError",
    "donor_recipient_splits",
    "materialize_reallocation",
    "transfer_fraction_grid",
]
