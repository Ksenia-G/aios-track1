"""Скрининговые группы EXP-001.

Группы нагнетательных скважин заданы условием эксперимента. Состав
добывающих скважин выведен из массива `FIPFAULT` колоды: для каждой скважины
взяты ячейки её перфораций (`WELSPECS` даёт устье, `COMPDAT` -- интервал по
k) и определён номер региона. Вывод проверяем: он воспроизводит все пять
заданных групп нагнетательных скважин без единого расхождения, и ни одна
скважина не пересекает границу регионов.

Это именно скрининговая группировка. Она не утверждает, что блоки
гидродинамически изолированы, и используется только для того, чтобы
перебирать пары внутри группы и знать, за какими добывающими скважинами
смотреть в первую очередь.
"""

from dataclasses import dataclass

from aios.schedule.events import natural_key


@dataclass(frozen=True, slots=True)
class ScreeningBlock:
    """Одна скрининговая группа."""

    block_id: int
    injectors: tuple[str, ...]
    producers: tuple[str, ...]

    @property
    def supports_redistribution(self) -> bool:
        """Перераспределение требует минимум двух нагнетательных скважин."""

        return len(self.injectors) >= 2

    def ordered_pairs(self) -> tuple[tuple[str, str], ...]:
        """Все упорядоченные пары донор -> получатель внутри группы."""

        if not self.supports_redistribution:
            return ()
        return tuple(
            (donor, receiver)
            for donor in self.injectors
            for receiver in self.injectors
            if donor != receiver
        )


SCREENING_BLOCKS: tuple[ScreeningBlock, ...] = (
    ScreeningBlock(1, ("37",), ("53", "54", "55")),
    ScreeningBlock(2, ("19", "41"), ("25", "30", "51", "52")),
    ScreeningBlock(
        3,
        ("4", "13", "21", "32"),
        ("1", "6", "12", "16", "17", "20", "29", "43", "47", "49"),
    ),
    ScreeningBlock(4, ("7", "28", "42"), ("9", "14", "23", "44", "45", "56")),
    ScreeningBlock(
        5,
        ("2", "15", "24", "26", "34", "36"),
        ("3", "22", "27", "31", "33", "35", "38", "40", "48", "50"),
    ),
)

BLOCKS_BY_ID: dict[int, ScreeningBlock] = {block.block_id: block for block in SCREENING_BLOCKS}


def block_of_injector(well: str) -> ScreeningBlock | None:
    for block in SCREENING_BLOCKS:
        if well in block.injectors:
            return block
    return None


def screening_blocks_with_redistribution() -> tuple[ScreeningBlock, ...]:
    return tuple(block for block in SCREENING_BLOCKS if block.supports_redistribution)


def all_injectors() -> tuple[str, ...]:
    wells = [well for block in SCREENING_BLOCKS for well in block.injectors]
    return tuple(sorted(wells, key=natural_key))


def all_producers() -> tuple[str, ...]:
    wells = [well for block in SCREENING_BLOCKS for well in block.producers]
    return tuple(sorted(wells, key=natural_key))
