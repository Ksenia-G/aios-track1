"""Принадлежность скважин геологическим регионам, выведенная из колоды.

Region-действия специалиста (перераспределение закачки) требуют знать, какие
скважины лежат в одном регионе. Раньше этого знания не существовало ни в одном
рабочем пути: `geology.block_membership` приходил пустым, и действие корректно
снималось с предложения как незаземляемое.

Источник -- сама колода, а не таблица:

* размерность сетки (`DIMENS`);
* массив регионов по ячейкам (`FIPFAULT` и совместимые);
* устье скважины (`WELSPECS`) -- координаты I/J;
* интервал перфораций (`COMPDAT`) -- диапазон K.

Скважина относится к региону БОЛЬШИНСТВА своих перфорированных ячеек. При
равенстве побеждает меньший номер региона: результат не должен зависеть от
порядка обхода. Регион 0 означает отсутствие принадлежности (неактивная
ячейка), а не шестой блок -- выдумывать группу там, где свидетельства нет,
нельзя.

Ничего специфичного для конкретного кейса здесь нет: правило работает на любой
колоде, где есть перечисленные ключевые слова.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from aios.schedule.deck import DeckParseError, tokenize

if TYPE_CHECKING:  # pragma: no cover
    from aios.state import GeologyContext
from aios.schedule.events import natural_key

#: Имена массивов регионов, которые считаются источником принадлежности.
#: Порядок -- приоритет: первый найденный и есть источник.
REGION_ARRAY_KEYWORDS: tuple[str, ...] = ("FIPFAULT", "FIPNUM")

_DIMENS_RE = re.compile(r"^\s*DIMENS\s*$", re.MULTILINE)
_INT_RE = re.compile(r"-?\d+")


class RegionDerivationError(ValueError):
    """Принадлежность нельзя вывести из предоставленных материалов."""


@dataclass(frozen=True, slots=True)
class WellRegionMap:
    """Карта скважина -> регион с явным происхождением."""

    block_membership: dict[str, str] = field(default_factory=dict)
    provenance: str = ""

    @property
    def regions(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.block_membership.values()), key=natural_key))

    def wells_in(self, region: str) -> tuple[str, ...]:
        return tuple(
            sorted(
                (well for well, item in self.block_membership.items() if item == region),
                key=natural_key,
            )
        )


def parse_grid_dimensions(data_text: str) -> tuple[int, int, int]:
    """`DIMENS NX NY NZ /` из корневого файла колоды."""
    match = _DIMENS_RE.search(data_text)
    if match is None:
        raise RegionDerivationError("deck: DIMENS keyword is absent; grid shape is unknown")
    tail = data_text[match.end() :]
    end = tail.find("/")
    numbers = _INT_RE.findall(tail if end < 0 else tail[:end])
    if len(numbers) < 3:
        raise RegionDerivationError("deck: DIMENS does not carry three grid dimensions")
    return int(numbers[0]), int(numbers[1]), int(numbers[2])


def parse_cell_array(text: str, keyword: str) -> list[int] | None:
    """Массив целых по ячейкам с развёрткой повторов вида `N*V`.

    Сеточные массивы -- это одна запись на всё ключевое слово, поэтому
    построчный разбор расписания здесь неприменим.
    """
    match = re.search(rf"^\s*{re.escape(keyword)}\s*$", text, re.MULTILINE)
    if match is None:
        return None
    tail = text[match.end() :]
    end = tail.find("/")
    if end < 0:
        raise RegionDerivationError(f"deck: array {keyword} is not terminated")
    values: list[int] = []
    for token in tail[:end].split():
        if "*" in token:
            count, _, value = token.partition("*")
            try:
                values.extend([int(value)] * int(count))
            except ValueError as error:
                raise RegionDerivationError(
                    f"deck: array {keyword} carries a non-integer repeat {token!r}"
                ) from error
        else:
            try:
                values.append(int(token))
            except ValueError as error:
                raise RegionDerivationError(
                    f"deck: array {keyword} carries a non-integer value {token!r}"
                ) from error
    return values


def _well_heads_and_completions(
    schedule_text: str,
) -> tuple[dict[str, tuple[int, int]], dict[str, list[tuple[int, int]]]]:
    heads: dict[str, tuple[int, int]] = {}
    completions: dict[str, list[tuple[int, int]]] = defaultdict(list)
    try:
        blocks = list(tokenize(schedule_text))
    except DeckParseError as error:
        raise RegionDerivationError(f"schedule: {error}") from error
    for block in blocks:
        if block.keyword == "WELSPECS":
            for record in block.records:
                well = record.text(0)
                i, j = record.number(2), record.number(3)
                if well is not None and i is not None and j is not None:
                    heads.setdefault(well, (int(i), int(j)))
        elif block.keyword == "COMPDAT":
            for record in block.records:
                well = record.text(0)
                k1, k2 = record.number(3), record.number(4)
                if well is not None and k1 is not None and k2 is not None:
                    completions[well].append((int(k1), int(k2)))
    return heads, dict(completions)


def derive_well_regions(*, data_text: str, region_text: str, schedule_text: str) -> WellRegionMap:
    """Вывести принадлежность скважин регионам из материалов колоды.

    Отсутствие массива регионов -- это пустая карта, а не ошибка: система
    обязана продолжать работать там, где геологии нет, просто без
    region-действий.
    """
    keyword, cells = _first_region_array(region_text)
    if cells is None:
        return WellRegionMap()

    nx, ny, nz = parse_grid_dimensions(data_text)
    if len(cells) != nx * ny * nz:
        raise RegionDerivationError(
            f"deck: array {keyword} has {len(cells)} cells but the grid holds {nx * ny * nz}"
        )

    heads, completions = _well_heads_and_completions(schedule_text)
    membership: dict[str, str] = {}
    for well in sorted(heads, key=natural_key):
        i, j = heads[well]
        if not (1 <= i <= nx and 1 <= j <= ny):
            continue
        counts: Counter[int] = Counter()
        for k1, k2 in completions.get(well, []):
            for k in range(max(k1, 1), min(k2, nz) + 1):
                value = cells[(k - 1) * nx * ny + (j - 1) * nx + (i - 1)]
                if value > 0:
                    counts[value] += 1
        if not counts:
            continue
        # Большинство ячеек; при равенстве -- меньший номер региона, чтобы
        # результат не зависел от порядка обхода.
        best = min(counts.items(), key=lambda item: (-item[1], item[0]))[0]
        membership[well] = str(best)

    return WellRegionMap(
        block_membership=membership,
        provenance=(
            f"aios.geology.regions/1.0.0: dominant completion cell region from {keyword} "
            f"over grid {nx}x{ny}x{nz} (WELSPECS head, COMPDAT interval)"
        ),
    )


def _first_region_array(region_text: str) -> tuple[str, list[int] | None]:
    for keyword in REGION_ARRAY_KEYWORDS:
        cells = parse_cell_array(region_text, keyword)
        if cells is not None:
            return keyword, cells
    return "", None


def geology_for_deck(*, deck_root, data_file: str, schedule_include: str) -> GeologyContext:
    """`GeologyContext` для колоды, лежащей в ``deck_root``.

    Файл с массивом регионов не называется снаружи: он ищется среди материалов
    колоды. Так правило остаётся применимым к произвольной совместимой колоде,
    а не к одной конкретной раскладке файлов.
    """
    from pathlib import Path

    from aios.state import GeologyContext as _GeologyContext

    root = Path(deck_root)
    data_path = root / data_file
    schedule_path = root / schedule_include
    if not data_path.is_file() or not schedule_path.is_file():
        return _GeologyContext()

    data_text = data_path.read_text(encoding="utf-8", errors="ignore")
    schedule_text = schedule_path.read_text(encoding="utf-8", errors="ignore")

    region_text = ""
    for candidate in _region_sources(root, data_path):
        text = candidate.read_text(encoding="utf-8", errors="ignore")
        if any(
            re.search(rf"^\s*{re.escape(keyword)}\s*$", text, re.MULTILINE)
            for keyword in REGION_ARRAY_KEYWORDS
        ):
            region_text = text
            break
    if not region_text:
        return _GeologyContext()

    derived = derive_well_regions(
        data_text=data_text, region_text=region_text, schedule_text=schedule_text
    )
    return _GeologyContext(block_membership=dict(derived.block_membership))


def _region_sources(root, data_path):
    """Материалы колоды в устойчивом порядке: сам DATA, затем включения."""
    yield data_path
    yield from sorted(root.rglob("*.inc"))


__all__ = [
    "REGION_ARRAY_KEYWORDS",
    "RegionDerivationError",
    "WellRegionMap",
    "derive_well_regions",
    "geology_for_deck",
    "parse_cell_array",
    "parse_grid_dimensions",
]
