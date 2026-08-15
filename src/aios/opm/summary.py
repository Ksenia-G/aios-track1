"""Чтение сводки OPM (`.SMSPEC` + `.UNSMRY`).

Формат -- классический ECLIPSE binary: последовательность блоков
`[len][keyword(8)][count][type(4)][len]` и следом сами данные, разбитые на
куски, каждый в своей обёртке длины. Разбирается ровно столько, сколько нужно
для траектории: имена векторов, имена скважин, единицы, дата старта и все
записи PARAMS.
"""

import datetime
import struct
from dataclasses import dataclass
from pathlib import Path

from aios.schedule.events import natural_key

_TYPES: dict[str, tuple[str, int]] = {
    "INTE": (">i", 4),
    "REAL": (">f", 4),
    "DOUB": (">d", 8),
    "CHAR": ("8s", 8),
    "LOGI": (">i", 4),
}

_MISSING_WELL = ":+:+:+:+"


class SummaryReadError(RuntimeError):
    """Сводка не читается. Числа не досочиняются."""


def read_blocks(path: Path) -> list[tuple[str, str, list]]:
    """Разобрать файл на блоки `(keyword, type, values)`."""

    blocks: list[tuple[str, str, list]] = []
    try:
        with open(path, "rb") as handle:
            while True:
                header = handle.read(4)
                if len(header) < 4:
                    break
                struct.unpack(">i", header)[0]
                keyword = handle.read(8).decode("ascii").strip()
                count = struct.unpack(">i", handle.read(4))[0]
                data_type = handle.read(4).decode("ascii").strip()
                handle.read(4)
                fmt, size = _TYPES.get(data_type, ("", 0))
                values: list = []
                if count > 0 and fmt:
                    remaining = count
                    while remaining > 0:
                        chunk_len = struct.unpack(">i", handle.read(4))[0]
                        chunk = handle.read(chunk_len)
                        struct.unpack(">i", handle.read(4))[0]
                        items = chunk_len // size
                        for index in range(items):
                            value = struct.unpack_from(fmt, chunk, index * size)[0]
                            values.append(
                                value.decode("ascii").strip() if isinstance(value, bytes) else value
                            )
                        remaining -= items
                blocks.append((keyword, data_type, values))
    except (OSError, struct.error) as exc:
        raise SummaryReadError(f"cannot read summary file {path}: {exc}") from exc
    return blocks


def _round_to_minute(moment: datetime.datetime) -> datetime.datetime:
    return (moment + datetime.timedelta(seconds=30)).replace(second=0, microsecond=0)


@dataclass(frozen=True, slots=True)
class SummaryData:
    """Сводка прогона: даты отчётных шагов и значения векторов по скважинам."""

    start: datetime.datetime
    dates: tuple[datetime.datetime, ...]
    wells: tuple[str, ...]
    units: dict[str, str]
    _well_index: dict[tuple[str, str], int]
    _field_index: dict[str, int]
    _params: tuple[tuple[float, ...], ...]

    @property
    def report_steps(self) -> int:
        return len(self.dates)

    def has_well_vector(self, vector: str, well: str) -> bool:
        return (vector, well) in self._well_index

    def well_value(self, vector: str, well: str, step: int) -> float:
        try:
            column = self._well_index[(vector, well)]
        except KeyError:
            raise SummaryReadError(f"summary has no vector {vector} for well {well}") from None
        return float(self._params[step][column])

    def missing_well_vectors(self, vectors: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            f"{vector}:{well}"
            for vector in vectors
            for well in self.wells
            if not self.has_well_vector(vector, well)
        )


def read_summary(smspec_path: Path, unsmry_path: Path) -> SummaryData:
    """Прочитать пару `.SMSPEC` / `.UNSMRY`."""

    meta: dict[str, list] = {}
    for keyword, _type, values in read_blocks(smspec_path):
        if keyword in ("KEYWORDS", "WGNAMES", "UNITS", "STARTDAT") and keyword not in meta:
            meta[keyword] = values
    for required in ("KEYWORDS", "WGNAMES", "UNITS", "STARTDAT"):
        if required not in meta:
            raise SummaryReadError(f"{smspec_path}: missing {required}")

    keywords, names, units, start_raw = (
        meta["KEYWORDS"],
        meta["WGNAMES"],
        meta["UNITS"],
        meta["STARTDAT"],
    )
    # Модельное время колоды -- календарь без часового пояса. Приписывать ему
    # UTC значило бы утверждать то, чего в исходных данных нет.
    start = datetime.datetime(  # noqa: DTZ001
        int(start_raw[2]), int(start_raw[1]), int(start_raw[0])
    )
    try:
        time_column = keywords.index("TIME")
    except ValueError:
        raise SummaryReadError(f"{smspec_path}: no TIME vector") from None

    params = tuple(
        tuple(float(value) for value in values)
        for keyword, _type, values in read_blocks(unsmry_path)
        if keyword == "PARAMS"
    )
    if not params:
        raise SummaryReadError(f"{unsmry_path}: no PARAMS records")

    dates = tuple(
        _round_to_minute(start + datetime.timedelta(days=row[time_column])) for row in params
    )

    well_index: dict[tuple[str, str], int] = {}
    field_index: dict[str, int] = {}
    vector_units: dict[str, str] = {}
    for column, keyword in enumerate(keywords):
        name = names[column] if column < len(names) else ""
        if keyword.startswith("W") and name and name != _MISSING_WELL:
            well_index.setdefault((keyword, name), column)
        elif keyword.startswith("F"):
            field_index.setdefault(keyword, column)
        if column < len(units):
            vector_units.setdefault(keyword, units[column])

    wells = tuple(
        sorted({name for keyword, name in well_index if keyword == "WOPT"}, key=natural_key)
    )
    return SummaryData(
        start=start,
        dates=dates,
        wells=wells,
        units=vector_units,
        _well_index=well_index,
        _field_index=field_index,
        _params=params,
    )
