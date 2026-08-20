"""Production restart primitives for optional OPM Flow continuation.

Restart is an execution optimization only. It may accelerate future controller
iterations, but final claimed NPV must always come from a clean full run.

Три факта об этой колоде, установленные измерением, а не по памяти.

1. **Поставленная колода не пишет состояний для перезапуска.** В начале
   SCHEDULE стоит `RPTRST 'BASIC=5' 'FREQ=3'`, но следом идёт
   `RPTSCHED 'NOTHING' 'SUMMARY=2'`, и в этой сборке (`flow 2026.04`)
   в `.UNRST` остаётся ровно одна запись -- `SEQNUM 0` на 2007-05-01,
   и та без данных по скважинам. Перезапуск с середины горизонта на
   исходной колоде невозможен: восстанавливать нечего.

2. **Вывод состояний включается повторным `RPTRST` внутри окна.** Тогда
   записи появляются на каждом отчётном шаге окна и содержат не только
   решение (`PRESSURE`, `RS`, `SGAS`, `SWAT`, `THRESHPR`), но и массивы
   скважин и соединений (`IWEL`, `SWEL`, `XWEL`, `ZWEL`, `ICON`, `SCON`,
   `XCON`) -- то есть всё, без чего продолжение не воспроизводит режим.

3. **`SEQNUM` -- не индекс отчётного шага сводки.** Измерено:
   `SEQNUM 80` = 2014-01-01 = индекс 84 сводки, `SEQNUM 82` = 2014-03-01
   = индекс 87. Смещение непостоянно, потому что в сводке есть
   промежуточные строки. Соответствие устанавливается **по дате** из
   `INTEHEAD`, и только по ней. Арифметика по индексам здесь молча даёт
   не тот шаг, а значит -- не то состояние.
"""

from __future__ import annotations

import datetime
import shutil
from dataclasses import dataclass
from pathlib import Path

from aios.cases.contract import Case
from aios.opm.summary import read_blocks
from aios.opm.workspace import Workspace, prepare_workspace

# Позиции даты в INTEHEAD: день, месяц, год.
_INTEHEAD_DAY = 64
_INTEHEAD_MONTH = 65
_INTEHEAD_YEAR = 66

RESTART_SOLUTION_INCLUDE = "INCLUDE/AIOS_RESTART_SOLUTION.inc"
RESTART_SOURCE_STEM = "AIOS_RESTART_SOURCE"

# Режим вывода состояний: запись на каждом отчётном шаге. Для замера
# эквивалентности нужны все шаги окна; в реальном контуре достаточно одного.
RESTART_OUTPUT_EVERY_REPORT_STEP = "BASIC=3' 'FREQ=1"


class RestartError(RuntimeError):
    """Перезапуск невозможен. Подстановка «похожего» состояния запрещена."""


@dataclass(frozen=True, slots=True)
class RestartRiskPolicy:
    """Configurable provenance for restart-risk findings.

    Значения MAS-SPIKE-001A/B/C -- результаты измерений на конкретной линии,
    а не универсальные физические константы. Поэтому будущие Agent/Arbiter
    будут получать этот объект как конфигурацию, а не импортировать числа из
    кода.
    """

    source: str
    calm_ranking_uncertainty: float
    transient_ranking_uncertainty: float
    hard_constraint_reconciliation_margin: float
    large_action_relative_change: float
    final_claim_requires_full_run: bool = True

    def to_json(self) -> dict[str, object]:
        return {
            "source": self.source,
            "calm_ranking_uncertainty": self.calm_ranking_uncertainty,
            "transient_ranking_uncertainty": self.transient_ranking_uncertainty,
            "hard_constraint_reconciliation_margin": (self.hard_constraint_reconciliation_margin),
            "large_action_relative_change": self.large_action_relative_change,
            "final_claim_requires_full_run": self.final_claim_requires_full_run,
        }

    @classmethod
    def model_y_spike_001abc(cls) -> RestartRiskPolicy:
        return cls(
            source="MAS-SPIKE-001A/B/C",
            calm_ranking_uncertainty=0.0,
            transient_ranking_uncertainty=0.0,
            hard_constraint_reconciliation_margin=0.0,
            large_action_relative_change=0.0,
        )


@dataclass(frozen=True, slots=True)
class RestartRecord:
    """Одно сохранённое состояние в `.UNRST`."""

    seqnum: int
    date: datetime.date
    has_well_data: bool

    def to_json(self) -> dict[str, object]:
        return {
            "seqnum": self.seqnum,
            "date": self.date.isoformat(),
            "has_well_data": self.has_well_data,
        }


@dataclass(frozen=True, slots=True)
class RestartSpec:
    """Откуда и с какого шага продолжать."""

    source_stem: str
    seqnum: int
    restart_date: datetime.date

    def to_json(self) -> dict[str, object]:
        return {
            "source_stem": self.source_stem,
            "seqnum": self.seqnum,
            "restart_date": self.restart_date.isoformat(),
        }


def read_restart_index(unrst_path: Path) -> tuple[RestartRecord, ...]:
    """Перечислить сохранённые состояния файла перезапуска.

    Запись считается пригодной, только если после её заголовка идут
    массивы скважин: состояние без `IWEL` восстанавливает пласт, но не
    режим работы фонда.
    """

    if not unrst_path.is_file():
        raise RestartError(f"restart file is missing: {unrst_path}")

    records: list[RestartRecord] = []
    pending_seq: int | None = None
    pending_date: datetime.date | None = None
    saw_wells = False

    def flush() -> None:
        nonlocal pending_seq, pending_date, saw_wells
        if pending_seq is not None and pending_date is not None:
            records.append(RestartRecord(pending_seq, pending_date, saw_wells))
        pending_seq, pending_date, saw_wells = None, None, False

    for keyword, _type, values in read_blocks(unrst_path):
        if keyword == "SEQNUM":
            flush()
            if not values:
                raise RestartError(f"{unrst_path}: SEQNUM block carries no value")
            pending_seq = int(values[0])
        elif keyword == "INTEHEAD":
            if len(values) <= _INTEHEAD_YEAR:
                raise RestartError(f"{unrst_path}: INTEHEAD is too short to carry a date")
            try:
                pending_date = datetime.date(
                    int(values[_INTEHEAD_YEAR]),
                    int(values[_INTEHEAD_MONTH]),
                    int(values[_INTEHEAD_DAY]),
                )
            except ValueError as exc:
                raise RestartError(f"{unrst_path}: INTEHEAD holds an impossible date") from exc
        elif keyword == "IWEL":
            saw_wells = True
    flush()

    if not records:
        raise RestartError(f"{unrst_path}: no restart states found")
    return tuple(records)


def find_restart_record(records: tuple[RestartRecord, ...], day: datetime.date) -> RestartRecord:
    """Найти состояние по дате. Ближайшее не подставляется."""

    matches = [record for record in records if record.date == day]
    if not matches:
        available = ", ".join(record.date.isoformat() for record in records)
        raise RestartError(
            f"no restart state at {day.isoformat()}; available dates: {available or 'none'}"
        )
    if len(matches) > 1:
        raise RestartError(f"restart file holds {len(matches)} states at {day.isoformat()}")
    record = matches[0]
    if not record.has_well_data:
        raise RestartError(
            f"restart state at {day.isoformat()} (SEQNUM {record.seqnum}) carries no well "
            f"arrays; continuing from it would restart the field with an undefined fund state"
        )
    return record


def enable_restart_output(schedule_text: str, mode: str = RESTART_OUTPUT_EVERY_REPORT_STEP) -> str:
    """Включить запись состояний с начала сгенерированного окна.

    Ключевое слово вставляется после шапки комментариев и до первого
    `DATES`, поэтому действует на всё окно и не трогает историю.
    Физику расчёта `RPTRST` не меняет -- только объём вывода.
    """

    lines = schedule_text.splitlines(keepends=True)
    try:
        split = next(index for index, line in enumerate(lines) if not line.startswith("--"))
    except StopIteration as exc:
        raise RestartError("schedule text has no keyword to precede") from exc
    return "".join(lines[:split]) + f"RPTRST\n '{mode}' /\n" + "".join(lines[split:])


def render_restart_solution(spec: RestartSpec) -> str:
    """SOLUTION-секция продолжения.

    `EQUIL`/`PBVD` заменяются на `RESTART`: инициализация равновесием и
    восстановление из файла -- взаимоисключающие способы задать начальное
    состояние. `THPRES` не переобъявляется: пороговые давления лежат в
    самом файле перезапуска (`THRESHPR`), и повторное объявление задало бы
    их дважды из двух источников.
    """

    return (
        "-- AIOS Track 1 -- restart continuation solution section.\n"
        f"-- restart date: {spec.restart_date.isoformat()}\n"
        "RESTART\n"
        f" '{spec.source_stem}' {spec.seqnum} /\n"
    )


def ensure_unified_restart_input(text: str) -> str:
    """Объявить `UNIFIN` в RUNSPEC.

    Колода объявляет `UNIFOUT`, то есть пишет единый `.UNRST`, но формат
    **чтения** перезапуска задаётся отдельным ключевым словом. Без `UNIFIN`
    эта сборка ищет пофайловый формат и отказывает буквально так:
    `The restart file /data/AIOS_RESTART_SOURCE.X0083 does not exist`.
    Одно слово вывода не подразумевает другого слова ввода.
    """

    lines = text.splitlines(keepends=True)
    if any(line.split("--")[0].strip() == "UNIFIN" for line in lines):
        return text
    for index, line in enumerate(lines):
        if line.split("--")[0].strip() == "UNIFOUT":
            lines.insert(index, "UNIFIN\n\n")
            return "".join(lines)
    raise RestartError("RUNSPEC has no UNIFOUT to anchor the UNIFIN declaration")


def rewrite_data_for_restart(text: str, solution_include: str, spec: RestartSpec) -> str:
    """Подменить include инициализации на секцию перезапуска."""

    needle = f"'{solution_include}'"
    if text.count(needle) != 1:
        raise RestartError(
            f"expected exactly one reference to {needle} in the .DATA file, "
            f"found {text.count(needle)}"
        )
    _ = spec
    return ensure_unified_restart_input(text.replace(needle, f"'{RESTART_SOLUTION_INCLUDE}'", 1))


def prepare_restart_workspace(
    case: Case,
    *,
    root: Path,
    history_text: str,
    schedule_text: str,
    solution_include: str,
    spec: RestartSpec,
    restart_source: Path,
) -> Workspace:
    """Рабочая копия для продолжения с сохранённого состояния.

    Расписание передаётся целиком -- история плюс окно, ровно то же, что
    у опорного прогона. Пропуск уже пройденной части задаётся `SKIPREST`,
    а не вырезанием строк: сравнивать нужно один и тот же файл управления,
    иначе измеряется разница расписаний, а не разница способов счёта.
    """

    if not restart_source.is_file():
        raise RestartError(f"restart source is missing: {restart_source}")

    workspace = prepare_workspace(
        case,
        root=root,
        history_text="SKIPREST\n" + history_text,
        schedule_text=schedule_text,
    )

    solution_file = workspace.root / RESTART_SOLUTION_INCLUDE
    solution_file.parent.mkdir(parents=True, exist_ok=True)
    solution_file.write_text(render_restart_solution(spec), encoding="utf-8")

    workspace.data_file.write_text(
        rewrite_data_for_restart(
            workspace.data_file.read_text(encoding="utf-8"), solution_include, spec
        ),
        encoding="utf-8",
    )

    shutil.copy2(restart_source, workspace.root / f"{spec.source_stem}.UNRST")
    return workspace


def find_solution_include(data_text: str) -> str:
    """Найти include секции SOLUTION в корневом .DATA."""

    section: str | None = None
    candidates: list[str] = []
    for raw in data_text.splitlines():
        line = raw.split("--")[0].strip()
        if line in (
            "RUNSPEC",
            "GRID",
            "EDIT",
            "PROPS",
            "REGIONS",
            "SOLUTION",
            "SUMMARY",
            "SCHEDULE",
        ):
            section = line
            continue
        if section == "SOLUTION" and line.startswith("'") and line.endswith("/"):
            candidates.append(line.strip("/").strip().strip("'"))
    if len(candidates) != 1:
        raise RestartError(
            f"expected exactly one SOLUTION include in the .DATA file, found {len(candidates)}"
        )
    return candidates[0]
