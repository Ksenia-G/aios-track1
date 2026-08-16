"""Разбор SCHEDULE-секции колоды.

Нужен минимальный, но честный парсер свободного формата ECLIPSE: без него
нельзя ни восстановить состояние на начало окна управления, ни отрезать
историю от управляемой части по реальной границе, а не по номеру строки,
подсмотренному глазами.

Поддерживается ровно то подмножество, которое встречается в Model Y.
Неизвестное ключевое слово не игнорируется молча -- оно сохраняется как блок
и, если попало в окно управления, приводит к отказу генератора.
"""

import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from aios.schedule.events import (
    DEFAULTED,
    InjectorControl,
    ProducerControl,
    Schedule,
    ScheduleStep,
    WellStatus,
    build_schedule,
    natural_key,
)

_TOKEN_RE = re.compile(r"'[^']*'|/|[^\s/]+")
_KEYWORD_RE = re.compile(r"^[A-Z][A-Z0-9_]*$")
_REPEAT_RE = re.compile(r"^(\d+)\*(.*)$")

# Ключевые слова с ровно одной записью и без завершающего пустого '/'.
_SINGLE_RECORD_KEYWORDS = frozenset({"RPTRST", "RPTSCHED", "SKIPREST", "END"})

_MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JLY": 7,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}
MONTH_NAMES = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JLY", "AUG", "SEP", "OCT", "NOV", "DEC")


class DeckParseError(ValueError):
    """Колода не разбирается. Догадки не строятся."""


@dataclass(frozen=True, slots=True)
class DeckRecord:
    """Одна запись ключевого слова, завершённая символом '/'."""

    tokens: tuple[str, ...]
    line: int

    def token(self, index: int) -> str | None:
        """Значение по позиции; `1*` и выход за границы дают `None`."""

        if index >= len(self.tokens):
            return None
        value = self.tokens[index]
        return None if value == DEFAULTED else value

    def text(self, index: int) -> str | None:
        value = self.token(index)
        return None if value is None else value.strip("'")

    def number(self, index: int) -> float | None:
        value = self.token(index)
        if value is None:
            return None
        try:
            return float(value)
        except ValueError as exc:
            raise DeckParseError(f"line {self.line + 1}: {value!r} is not a number") from exc


@dataclass(frozen=True, slots=True)
class DeckBlock:
    """Ключевое слово со всеми его записями и точным местом в файле."""

    keyword: str
    records: tuple[DeckRecord, ...]
    start_line: int
    end_line: int


def tokenize(text: str) -> Iterator[DeckBlock]:
    """Разобрать текст колоды на блоки ключевых слов."""

    lines = text.splitlines()
    index = 0
    total = len(lines)
    while index < total:
        stripped = _strip_comment(lines[index]).strip()
        if not stripped:
            index += 1
            continue
        if not _KEYWORD_RE.match(stripped):
            raise DeckParseError(f"line {index + 1}: expected a keyword, got {stripped!r}")
        keyword = stripped
        start_line = index
        index += 1
        records: list[DeckRecord] = []
        pending: list[str] = []
        pending_line = index
        end_line = start_line
        while index < total:
            end_line = index
            for token in _expand_repeats(_TOKEN_RE.findall(_strip_comment(lines[index]))):
                if token == "/":
                    if not pending and (records or keyword not in _SINGLE_RECORD_KEYWORDS):
                        pending_line = index
                        break
                    records.append(DeckRecord(tuple(pending), pending_line))
                    pending = []
                    pending_line = index + 1
                else:
                    if not pending:
                        pending_line = index
                    pending.append(token)
            else:
                index += 1
                if keyword in _SINGLE_RECORD_KEYWORDS and records:
                    break
                continue
            index += 1
            break
        if pending:
            raise DeckParseError(f"keyword {keyword} at line {start_line + 1} is not terminated")
        yield DeckBlock(keyword, tuple(records), start_line, end_line)


def _strip_comment(line: str) -> str:
    position = line.find("--")
    return line if position < 0 else line[:position]


def _expand_repeats(tokens: Sequence[str]) -> list[str]:
    """Развернуть счётчики повторов: `3*` -> три умолчания, `3*100` -> три сотни.

    Без этого позиционные индексы записи разъезжаются молча, а не падают.
    """

    expanded: list[str] = []
    for token in tokens:
        match = _REPEAT_RE.match(token)
        if match is None:
            expanded.append(token)
            continue
        count = int(match.group(1))
        expanded.extend([match.group(2) or DEFAULTED] * count)
    return expanded


def parse_deck_date(record: DeckRecord) -> date:
    """`01 'JAN' 2014` -> date(2014, 1, 1)."""

    if len(record.tokens) < 3:
        raise DeckParseError(f"line {record.line + 1}: DATES record needs day, month, year")
    month_name = record.tokens[1].strip("'").upper()
    if month_name not in _MONTHS:
        raise DeckParseError(f"line {record.line + 1}: unknown month {month_name!r}")
    try:
        return date(int(record.tokens[2]), _MONTHS[month_name], int(record.tokens[0]))
    except ValueError as exc:
        raise DeckParseError(f"line {record.line + 1}: bad DATES record") from exc


def format_deck_date(day: date) -> str:
    """Обратная операция: date -> `01 'JAN' 2014`."""

    return f"{day.day:02d} '{MONTH_NAMES[day.month - 1]}' {day.year}"


def _status(value: str | None, where: str) -> WellStatus:
    if value is None:
        raise DeckParseError(f"{where}: well status is mandatory")
    try:
        return WellStatus(value.upper())
    except ValueError as exc:
        raise DeckParseError(f"{where}: unsupported well status {value!r}") from exc


def parse_producer_record(record: DeckRecord) -> ProducerControl:
    """`WCONPROD`: well status control ORAT WRAT GRAT LRAT RESV BHP THP VFP."""

    well = record.text(0)
    if not well:
        raise DeckParseError(f"line {record.line + 1}: WCONPROD record without a well name")
    mode = record.text(2)
    if not mode:
        raise DeckParseError(f"line {record.line + 1}: WCONPROD record without a control mode")
    return ProducerControl(
        well=well,
        status=_status(record.text(1), f"line {record.line + 1}"),
        mode=mode.upper(),
        liquid_rate=record.number(6),
        bhp_limit=record.number(8),
    )


def parse_injector_record(record: DeckRecord) -> InjectorControl:
    """`WCONINJE`: well phase status control RATE RESV BHP THP VFP."""

    well = record.text(0)
    if not well:
        raise DeckParseError(f"line {record.line + 1}: WCONINJE record without a well name")
    phase = record.text(1)
    mode = record.text(3)
    if not phase or not mode:
        raise DeckParseError(
            f"line {record.line + 1}: WCONINJE record needs phase and control mode"
        )
    return InjectorControl(
        well=well,
        phase=phase.upper(),
        status=_status(record.text(2), f"line {record.line + 1}"),
        mode=mode.upper(),
        surface_rate=record.number(4),
        bhp_limit=record.number(6),
    )


@dataclass(frozen=True, slots=True)
class DeckSchedule:
    """Разобранная SCHEDULE-секция вместе с исходным текстом.

    Исходные строки хранятся целиком: история переносится в рабочую копию
    байт в байт, а не пересобирается генератором.
    """

    path: Path
    text: str
    start_date: date
    blocks: tuple[DeckBlock, ...]
    block_dates: tuple[date, ...]
    wells: tuple[str, ...]

    def split_line(self, day: date) -> int:
        """Номер строки, с которой начинается блок `DATES` заданной даты."""

        for block, block_date in zip(self.blocks, self.block_dates, strict=True):
            if block.keyword == "DATES" and block_date == day and len(block.records) == 1:
                return block.start_line
        raise DeckParseError(f"{self.path}: no single-date DATES block for {day.isoformat()}")

    def history_text(self, day: date) -> str:
        """Текст истории до даты `day`, без изменений."""

        lines = self.text.splitlines(keepends=True)
        return "".join(lines[: self.split_line(day)])

    def control_schedule(self, start: date, end: date) -> Schedule:
        """Управляющие воздействия окна как объект расписания."""

        steps: list[ScheduleStep] = []
        for block, block_date in zip(self.blocks, self.block_dates, strict=True):
            if not start <= block_date <= end:
                continue
            if block.keyword == "DATES":
                for record in block.records:
                    steps.append(ScheduleStep(parse_deck_date(record)))
            elif block.keyword == "WCONPROD":
                steps.append(
                    ScheduleStep(
                        block_date,
                        producers=tuple(parse_producer_record(r) for r in block.records),
                    )
                )
            elif block.keyword == "WCONINJE":
                steps.append(
                    ScheduleStep(
                        block_date,
                        injectors=tuple(parse_injector_record(r) for r in block.records),
                    )
                )
            else:
                raise DeckParseError(
                    f"{self.path}: keyword {block.keyword} at line {block.start_line + 1} "
                    f"falls inside the management window and is not a control keyword"
                )
        return build_schedule(steps).canonical()


def parse_start_date(data_text: str) -> date:
    """Дата START из корневого .DATA файла.

    Целиком .DATA не токенизируется намеренно: в RUNSPEC живут ключевые слова
    вроде `TITLE` со свободным текстом вместо записи, и общий парсер на них
    не рассчитан. Здесь нужна ровно одна величина.
    """

    lines = data_text.splitlines()
    for index, line in enumerate(lines):
        if _strip_comment(line).strip() != "START":
            continue
        for follow in lines[index + 1 :]:
            tokens = _expand_repeats(_TOKEN_RE.findall(_strip_comment(follow)))
            if not tokens:
                continue
            return parse_deck_date(DeckRecord(tuple(t for t in tokens if t != "/"), index + 1))
        break
    raise DeckParseError("deck has no usable START keyword")


def _collect_wells(blocks: Sequence[DeckBlock]) -> tuple[str, ...]:
    wells: set[str] = set()
    for block in blocks:
        if block.keyword in ("WELSPECS", "COMPDAT"):
            for record in block.records:
                name = record.text(0)
                if name:
                    wells.add(name)
    return tuple(sorted(wells, key=natural_key))


def parse_schedule_file(path: Path, *, start_date: date) -> DeckSchedule:
    """Разобрать файл расписания и разметить каждый блок его датой."""

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DeckParseError(f"schedule include is not readable: {path}") from exc

    blocks = tuple(tokenize(text))
    cursor = start_date
    block_dates: list[date] = []
    for block in blocks:
        if block.keyword == "DATES" and block.records:
            cursor = parse_deck_date(block.records[-1])
        block_dates.append(cursor)
    return DeckSchedule(
        path=path,
        text=text,
        start_date=start_date,
        blocks=blocks,
        block_dates=tuple(block_dates),
        wells=_collect_wells(blocks),
    )
