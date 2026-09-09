"""Рабочая копия колоды для одного прогона.

Физическая модель не меняется никогда. Меняется только SCHEDULE: история
переносится байт в байт, а управляемое окно подставляется сгенерированным
файлом.

Остальные файлы колоды именно копируются, а не связываются жёсткой ссылкой.
Ссылка была бы дешевле, но она делит inode с исходной колодой: любая запись
в рабочую копию -- своя, чужая или ошибочная -- испортила бы оригинал. За
эту гарантию платится копирование сетки, то есть доли секунды против
полутора минут счёта.
"""

import os
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

from aios.cases.contract import Case

MARKER_NAME = ".aios-workspace"
HISTORY_INCLUDE = "INCLUDE/AIOS_HISTORY.inc"
SCHEDULE_INCLUDE = "INCLUDE/wells_schedule.inc"
OUTPUT_DIRNAME = "OUT"


class WorkspaceError(RuntimeError):
    """Рабочую копию собрать не удалось."""


@dataclass(frozen=True, slots=True)
class Workspace:
    """Готовая к запуску копия кейса."""

    root: Path
    data_file: Path
    schedule_file: Path
    history_file: Path
    output_dir: Path

    @property
    def case_name(self) -> str:
        return self.data_file.stem


def _mirror(source: Path, target: Path) -> None:
    """Независимая копия файла колоды."""

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def _rewrite_data(text: str, schedule_include: str) -> str:
    """Подменить единственный include расписания на историю + новое окно."""

    needle = f"'{schedule_include}'"
    if text.count(needle) != 1:
        raise WorkspaceError(
            f"expected exactly one reference to {needle} in the .DATA file, "
            f"found {text.count(needle)}"
        )
    replacement = f"'{HISTORY_INCLUDE}' /\n\nINCLUDE\n'{SCHEDULE_INCLUDE}'"
    return text.replace(needle, replacement, 1)


def _make_container_writable(directory: Path) -> None:
    """Сделать каталог доступным на запись пользователю контейнера.

    OPM-образ запускает `flow` от внутреннего пользователя `opm` (uid 1001),
    который не совпадает с uid хоста. Docker монтирует рабочую копию как есть,
    поэтому каталог вывода, созданный хостом, должен быть открыт на запись
    для «остальных» -- иначе `flow` падает с `Failed opening file
    /data/OUT/...PRT for StreamLog`. Это детерминировано: права выставляются
    явно, а не наследуются от umask хоста, поэтому прогон не зависит от
    раскладки пользователей на машине организатора.
    """

    os.chmod(directory, 0o777)


_TNAV_IF_ZERO_RE = re.compile(r"(?m)^\s*#if\s+0\b.*?^\s*#endif\s*$", re.DOTALL)


def _strip_tnav_dead_code(deck_text: str) -> str:
    """Убрать блоки ``#if 0 ... #endif`` tNavigator из текста колоды.

    Model Y распространяется организаторами как экспорт tNavigator: в начале
    .DATA стоит ``#if 0 // tNavigator keyword ... #endif`` -- синтаксис
    условной компиляции tNavigator, который парсер OPM Flow не понимает даже
    при ``--parsing-strictness=low``. Семантически это мёртвый код (``#if 0``
    = «выключено»), поэтому удаление детерминировано и не меняет физику.
    Без этого симулятор падает на `String #if 0 ... not formatted as valid
    keyword`, и прогон выглядит упавшим при исправной колоде.
    """

    return _TNAV_IF_ZERO_RE.sub("", deck_text)


def prepare_workspace(
    case: Case, *, root: Path, history_text: str, schedule_text: str
) -> Workspace:
    """Собрать рабочую копию в `root`, затерев предыдущую копию того же прогона."""

    deck_root = case.history.deck_root
    if not deck_root.is_dir():
        raise WorkspaceError(f"deck root is not a directory: {deck_root}")
    data_source = case.history.data_path
    if not data_source.is_file():
        raise WorkspaceError(f"deck .DATA file is missing: {data_source}")

    if root.exists():
        if not (root / MARKER_NAME).exists():
            raise WorkspaceError(f"refusing to reuse {root}: it was not created by this pipeline")
        shutil.rmtree(root)
    root.mkdir(parents=True)
    (root / MARKER_NAME).write_text("aios track1 transient run workspace\n", encoding="utf-8")

    skip = {
        deck_root / case.history.data_file,
        case.history.schedule_path,
    }
    for source in sorted(deck_root.rglob("*")):
        if not source.is_file() or source in skip:
            continue
        _mirror(source, root / source.relative_to(deck_root))

    history_file = root / HISTORY_INCLUDE
    history_file.parent.mkdir(parents=True, exist_ok=True)
    history_file.write_text(history_text, encoding="utf-8")

    schedule_file = root / SCHEDULE_INCLUDE
    schedule_file.write_text(schedule_text, encoding="utf-8")

    data_file = root / case.history.data_file
    data_file.write_text(
        _rewrite_data(
            _strip_tnav_dead_code(data_source.read_text(encoding="utf-8")),
            case.history.schedule_include,
        ),
        encoding="utf-8",
    )

    output_dir = root / OUTPUT_DIRNAME
    output_dir.mkdir()
    _make_container_writable(output_dir)
    return Workspace(
        root=root,
        data_file=data_file,
        schedule_file=schedule_file,
        history_file=history_file,
        output_dir=output_dir,
    )
